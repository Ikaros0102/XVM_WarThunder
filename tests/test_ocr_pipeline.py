"""Tests for Tab OCR debounce, non-destructive merge, synthetic Cyrillic/Latin OCR, and error resilience."""

from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw, ImageFont

from wt_xvm.config import AppConfig, OcrRoiConfig, load_config
from wt_xvm.core.battle_state import BattleSessionState
from wt_xvm.core.log_parser import KillEvent
from wt_xvm.core.nick_cleaner import parse_player_identity
from wt_xvm.core.vehicle_db import VehicleDB
from wt_xvm.ocr.scoreboard_ocr import (
    ScoreboardOCR,
    filter_scoreboard_nicks,
    group_boxes_into_rows,
    normalize_ocr_mixed_script,
)
from wt_xvm.ocr.screen_capture import compute_safe_16_9_frame, crop_scoreboard_columns
from wt_xvm.ocr.tab_watcher import _VK_CONTROL, _VK_LWIN, _VK_MENU, _VK_TAB, TabOcrWatcher


def test_filter_and_row_grouping():
    raw_boxes = [
        [[[10, 10], [60, 10], [60, 28], [10, 28]], "Игрок", 0.99],
        [[[10, 45], [95, 45], [95, 65], [10, 65]], "-е6а- Дедушка", 0.97],
        [[[100, 46], [140, 46], [140, 64], [100, 64]], "Вова", 0.98],
        [[[10, 85], [110, 85], [110, 105], [10, 105]], "1 Ikaros_02", 0.96],
        [[[10, 125], [50, 125], [50, 145], [10, 145]], "1250", 0.99],
    ]
    rows = group_boxes_into_rows(raw_boxes)
    assert rows == ["Игрок", "-е6а- Дедушка Вова", "1 Ikaros_02", "1250"]
    nicks = filter_scoreboard_nicks(rows)
    assert nicks == ["-е6а- Дедушка Вова", "Ikaros_02"]


def test_safe_16_9_frame_and_crop():
    # 21:9 ultrawide 3440x1440 -> centered 2560x1440 safe frame
    assert compute_safe_16_9_frame(3440, 1440) == (440, 0, 2560, 1440)
    # Standard 1920x1080
    assert compute_safe_16_9_frame(1920, 1080) == (0, 0, 1920, 1080)

    img = Image.new("RGB", (1920, 1080), (15, 18, 22))
    cols = crop_scoreboard_columns(img, OcrRoiConfig())
    assert cols.left_img.width > 250
    assert cols.right_img.width > 250
    assert len(cols.preview_png) > 100


def test_rapidocr_synthetic_scoreboard_cyrillic_and_latin():
    left_img = Image.new("RGB", (460, 220), (18, 22, 28))
    right_img = Image.new("RGB", (460, 220), (18, 22, 28))
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 22)

    dl = ImageDraw.Draw(left_img)
    dl.text((20, 20), "Игрок", fill=(180, 180, 180), font=font)
    dl.text((20, 65), "Ikaros_02", fill=(240, 210, 80), font=font)
    dl.text((20, 110), "[12RCC] P4SKUD4 блин", fill=(140, 200, 240), font=font)

    dr = ImageDraw.Draw(right_img)
    dr.text((20, 20), "Player", fill=(180, 180, 180), font=font)
    dr.text((20, 65), "-e6a- Дедушка Вова", fill=(235, 105, 105), font=font)
    dr.text((20, 110), "=Audra= Karsten5244", fill=(235, 105, 105), font=font)

    ocr = ScoreboardOCR()
    allies, enemies = ocr.recognize_columns(left_img, right_img)

    assert any("ikaros" in a.casefold() for a in allies)
    assert any("p4skud4" in a.casefold() for a in allies)
    assert any("karsten5244" in e.casefold() for e in enemies)
    assert any("дедушка" in e.casefold() for e in enemies)


@pytest.mark.asyncio
async def test_tab_watcher_debounce_and_non_destructive_merge():
    fake_mono = [100.0]
    cfg = AppConfig(tab_debounce_sec=30.0, tab_hold_delay_ms=50, min_ocr_players_per_side=1)
    state = BattleSessionState(cfg, VehicleDB())
    state.battle_active = True

    scan_outputs = [
        (["Ikaros_02"], ["EnemyOne"]),
        (["Ikaros_02", "LateJoinerAlly"], ["EnemyOne", "LateJoinerEnemy"]),
    ]
    idx = [0]

    class FakeOcr:
        def recognize_columns(self, _l, _r):
            res = scan_outputs[idx[0]]
            idx[0] += 1
            return res

    def fake_capture(_roi):
        dummy = Image.new("RGB", (10, 10), (0, 0, 0))
        return SimpleNamespace(left_img=dummy, right_img=dummy, preview_png=b"png")

    watcher = TabOcrWatcher(
        config=cfg,
        state=state,
        ocr=FakeOcr(),  # type: ignore[arg-type]
        key_probe=lambda _vk: True,
        window_title_probe=lambda: "War Thunder",
        capture_fn=fake_capture,
        mono_clock=lambda: fake_mono[0],
    )

    # First scan succeeds
    assert await watcher.trigger_scan_if_ready(require_hold=False) is True
    assert "ikaros_02" in state.players
    assert "enemyone" in state.players

    # Record a kill before 2nd scan to prove non-destructive merge preserves match data
    state.apply_hud_event(
        KillEvent(
            10.0,
            parse_player_identity("Ikaros_02"),
            "Leopard 2A4",
            parse_player_identity("EnemyOne"),
            "T-72B",
        )
    )
    assert state.players["ikaros_02"].kills == 1

    # Attempt 2nd scan at +15s -> MUST be blocked by 30s debounce
    fake_mono[0] = 115.0
    assert await watcher.trigger_scan_if_ready(require_hold=False) is False
    assert "latejoinerally" not in state.players

    # Attempt 2nd scan at +31s -> MUST add newly joined players without erasing kills
    fake_mono[0] = 131.0
    assert await watcher.trigger_scan_if_ready(require_hold=False) is True
    assert state.players["ikaros_02"].kills == 1
    assert state.players["latejoinerally"].team == "ally"
    assert state.players["latejoinerenemy"].team == "enemy"

    # Exception resilience test
    def broken_capture(_roi):
        raise RuntimeError("GDI capture unavailable")

    watcher._capture_fn = broken_capture
    fake_mono[0] = 200.0
    assert await watcher.trigger_scan_if_ready(require_hold=False) is False
    assert state.ocr_status.last_error == "GDI capture unavailable"

    watcher.stop()


def test_filter_noise_substrings():
    raw_rows = [
        "Очки: 14156, Зоны: 2",
        "Реалистичный бой, [Превосходство #2]",
        "Время миссии осталось: 17:00",
        "Score: 2210, Zones: 1",
        "BattlePlayer1",
        "1 -ADSM= Galahad4657",
        "Ikaros_02",
    ]
    assert filter_scoreboard_nicks(raw_rows) == ["BattlePlayer1", "-ADSM= Galahad4657", "Ikaros_02"]


@pytest.mark.asyncio
async def test_tab_watcher_blocks_modifiers_and_incomplete_scans():
    fake_mono = [100.0]
    cfg = AppConfig(tab_debounce_sec=30.0, tab_hold_delay_ms=50, min_ocr_players_per_side=1)
    state = BattleSessionState(cfg, VehicleDB())
    state.battle_active = True

    held_keys: set[int] = {_VK_TAB}
    scan_result = (["OnlyAlly"], [])
    ocr_calls = [0]

    class FakeOcr:
        def recognize_columns(self, _l, _r):
            ocr_calls[0] += 1
            return scan_result

    def fake_capture(_roi):
        dummy = Image.new("RGB", (10, 10), (0, 0, 0))
        return SimpleNamespace(left_img=dummy, right_img=dummy, preview_png=b"png")

    watcher = TabOcrWatcher(
        config=cfg,
        state=state,
        ocr=FakeOcr(),  # type: ignore[arg-type]
        key_probe=lambda vk: vk in held_keys,
        window_title_probe=lambda: "War Thunder",
        capture_fn=fake_capture,
        mono_clock=lambda: fake_mono[0],
    )

    for mod in (_VK_MENU, _VK_CONTROL, _VK_LWIN):
        held_keys.add(mod)
        assert await watcher.trigger_scan_if_ready(require_hold=True) is False
        held_keys.remove(mod)
    assert ocr_calls[0] == 0

    # Partial single-side empty scan must be rejected without setting debounce
    assert await watcher.trigger_scan_if_ready(require_hold=True) is False
    assert ocr_calls[0] == 1
    assert "onlyally" not in state.players
    assert watcher.cooldown_remaining() == 0.0

    # Complete both-sides scan immediately succeeds without being blocked by partial attempt
    scan_result = (["AllyOne"], ["EnemyOne"])
    assert await watcher.trigger_scan_if_ready(require_hold=True) is True
    assert state.players["allyone"].team == "ally"
    assert state.players["enemyone"].team == "enemy"
    assert watcher.cooldown_remaining() > 0.0

    watcher.stop()


def test_load_config_migrates_v1_ocr_roi_and_keeps_custom(tmp_path):
    legacy_cfg = tmp_path / "legacy.json"
    legacy_cfg.write_text(
        '{"ocr_roi": {"left_nicks": {"x": 0.145, "y": 0.255, "w": 0.195, "h": 0.495}, '
        '"right_nicks": {"x": 0.53, "y": 0.255, "w": 0.195, "h": 0.495}}}',
        encoding="utf-8",
    )
    migrated = load_config(legacy_cfg)
    assert migrated.ocr_roi.left_nicks.x == pytest.approx(0.342)
    assert migrated.ocr_roi.right_nicks.x == pytest.approx(0.512)
    # Re-reading persisted migrated config keeps v2 defaults
    reloaded = load_config(legacy_cfg)
    assert reloaded.ocr_roi.left_nicks.x == pytest.approx(0.342)

    custom_cfg = tmp_path / "custom.json"
    custom_cfg.write_text(
        '{"ocr_roi": {"left_nicks": {"x": 0.25, "y": 0.30, "w": 0.16, "h": 0.45}, '
        '"right_nicks": {"x": 0.55, "y": 0.30, "w": 0.16, "h": 0.45}}}',
        encoding="utf-8",
    )
    preserved = load_config(custom_cfg)
    assert preserved.ocr_roi.left_nicks.x == pytest.approx(0.25)
    assert preserved.ocr_roi.right_nicks.x == pytest.approx(0.55)


def test_normalize_ocr_mixed_script_and_short_debris_filter():
    # Cyrillic 'у' inside Latin nick -> 'y'
    assert normalize_ocr_mixed_script("Hуpe_Maxi-L") == "Hype_Maxi-L"
    # Genuine Cyrillic and mixed-word entries preserved
    assert normalize_ocr_mixed_script("Дедушка Вова") == "Дедушка Вова"
    assert normalize_ocr_mixed_script("P4SKUD4 блин") == "P4SKUD4 блин"

    # Drop 2-char non-CJK debris ("hl", "ul", "аж") while keeping CJK ("弦_") and 3-char ("Zkz")
    filtered = filter_scoreboard_nicks(["hl", "ul", "аж", "弦_", "Zkz", "Hуpe_Maxi-L"])
    assert filtered == ["弦_", "Zkz", "Hype_Maxi-L"]


@pytest.mark.asyncio
async def test_min_ocr_players_guard_and_repeat_scan_deduplication():
    fake_mono = [100.0]
    cfg = AppConfig(tab_debounce_sec=30.0, tab_hold_delay_ms=50, min_ocr_players_per_side=3)
    state = BattleSessionState(cfg, VehicleDB())
    state.battle_active = True

    class FakeOcr:
        def __init__(self, res):
            self.res = res

        def recognize_columns(self, _l, _r):
            return self.res

    def fake_capture(_roi):
        dummy = Image.new("RGB", (10, 10), (0, 0, 0))
        return SimpleNamespace(left_img=dummy, right_img=dummy, preview_png=b"png")

    # 2 allies / 1 enemy (garbage menu scan like ul/hl/аж) rejected without debounce
    fake_ocr = FakeOcr((["AllyA", "AllyB"], ["EnemyA"]))
    watcher = TabOcrWatcher(
        config=cfg,
        state=state,
        ocr=fake_ocr,  # type: ignore[arg-type]
        key_probe=lambda _vk: True,
        window_title_probe=lambda: "War Thunder",
        capture_fn=fake_capture,
        mono_clock=lambda: fake_mono[0],
    )
    assert await watcher.trigger_scan_if_ready(require_hold=False) is False
    assert len(state.players) == 0
    assert watcher.cooldown_remaining() == 0.0

    # 1st full scan + exact log upgrades
    fake_ocr.res = (
        ["Hype_Maxi-L", "Bobb13352", "awa486 desuwa"],
        ["=MAOWO= Tom Sawyer#2", "*MNx* CroûtonSuprême", "lastikt2341"],
    )
    assert await watcher.trigger_scan_if_ready(require_hold=False) is True
    for p in state.players.values():
        p.exact_nick_verified = True
    initial_count = len(state.players)
    assert initial_count == 6

    # 2nd Tab scan with OCR drift / unstripped tags / missing spaces / Cyrillic homoglyph -> 0 duplicates!
    fake_mono[0] = 135.0
    fake_ocr.res = (
        ["Hуpe_Maxi-L", "-RF2-Bobb13352", "Rad7 awa486 desuwa"],
        ["TomSawyer#2", "MNx*CroütonSuprëme", ".XDGKD. lastikt2341"],
    )
    # Use direct filter pass on 2nd scan too (Simulating ScoreboardOCR normalization)
    fake_ocr.res = (
        filter_scoreboard_nicks(fake_ocr.res[0]),
        filter_scoreboard_nicks(fake_ocr.res[1]),
    )
    assert await watcher.trigger_scan_if_ready(require_hold=False) is True
    assert len(state.players) == initial_count
    watcher.stop()
