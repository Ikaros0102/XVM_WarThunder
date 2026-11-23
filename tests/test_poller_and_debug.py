"""Tests for WarThunderPoller (/hudmsg damage-only, map_generation resilience) and DebugSessionRecorder."""

import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from wt_xvm.config import AppConfig
from wt_xvm.core.battle_state import BattleSessionState
from wt_xvm.core.debug_recorder import DebugSessionRecorder
from wt_xvm.core.log_parser import LogParser
from wt_xvm.core.poller import WarThunderPoller
from wt_xvm.core.vehicle_db import VehicleDB
from wt_xvm.ocr.tab_watcher import TabOcrWatcher


@pytest.mark.asyncio
async def test_poller_avoids_local_kd_doubling_and_ignores_map_gen_switch(tmp_path):
    fake_mono = [10.0]
    cfg = AppConfig(debug_mode=True, log_language="ru")
    recorder = DebugSessionRecorder(cfg, base_dir=tmp_path / "debug_sessions")
    state = BattleSessionState(cfg, VehicleDB())
    parser = LogParser()
    poller = WarThunderPoller(
        config=cfg,
        state=state,
        parser=parser,
        debug_recorder=recorder,
        invalid_reset_grace_sec=3.0,
        mono_clock=lambda: fake_mono[0],
    )

    scenario = {"map": {"valid": True, "map_generation": 1}, "hud": {"events": [], "damage": []}, "chat": []}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/map_info.json":
            return httpx.Response(200, json=scenario["map"])
        if path == "/hudmsg":
            return httpx.Response(200, json=scenario["hud"])
        if path == "/gamechat":
            return httpx.Response(200, json=scenario["chat"])
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        # Step 1: Enter battle and receive same local kill in BOTH `events` and `damage`
        scenario["hud"] = {
            "events": [
                {"id": 1, "msg": "Ikaros_02 (Leopard 1A5) уничтожил Enemy1 (T-72A)", "time": 25},
                {"id": 2, "msg": "Enemy1 (T-72A) уничтожил Ikaros_02 (Leopard 1A5)", "time": 40},
            ],
            "damage": [
                {"id": 10, "msg": "Ikaros_02 (Leopard 1A5) уничтожил Enemy1 (T-72A)", "time": 25},
                {"id": 11, "msg": "Enemy1 (T-72A) уничтожил Ikaros_02 (Leopard 1A5)", "time": 40},
            ],
        }
        assert await poller.poll_once(client) is True
        assert state.battle_active is True
        assert state.players["ikaros_02"].kills == 1
        assert state.players["ikaros_02"].deaths == 1

        # Step 2: Switch tank -> plane swaps map_generation (1 -> 2) with valid=True -> session MUST survive!
        scenario["map"] = {"valid": True, "map_generation": 2}
        scenario["hud"] = {
            "events": [],
            "damage": [
                {"id": 12, "msg": "Ikaros_02 (SAAB-105G) сбил Enemy1 (Su-25)", "time": 80},
            ],
        }
        assert await poller.poll_once(client) is True
        assert state.battle_active is True
        assert state.players["ikaros_02"].kills == 2
        assert state.players["ikaros_02"].deaths == 1
        assert state.players["ikaros_02"].current_vehicle == "SAAB-105G"

        # Step 3: Brief valid=False flicker (< 3.0s) preserves state
        scenario["map"] = {"valid": False, "map_generation": 2}
        scenario["hud"] = {"events": [], "damage": []}
        fake_mono[0] = 20.0
        assert await poller.poll_once(client) is True
        assert state.battle_active is True
        assert "ikaros_02" in state.players

        fake_mono[0] = 22.0
        assert await poller.poll_once(client) is True
        assert state.battle_active is True

        # Step 4: Sustained valid=False (>= 3.0s) resets session to hangar while preserving monotonic cursors
        fake_mono[0] = 23.5
        assert await poller.poll_once(client) is True
        assert state.battle_active is False
        assert state.players == {}
        assert poller.last_evt == 2 and poller.last_dmg == 12

    # Step 5: Game disconnect (wt_connected -> False) resets cursors back to 0
    def offline_handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("game closed")

    async with httpx.AsyncClient(transport=httpx.MockTransport(offline_handler)) as offline_client:
        assert await poller.poll_once(offline_client) is False
        assert state.wt_connected is False
        assert poller.last_evt == 0 and poller.last_dmg == 0

    session_dir = recorder.get_session_dir()
    assert session_dir is not None
    hud_lines = (session_dir / "hudmsg_raw.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(hud_lines) == 5
    events_logged = [json.loads(line) for line in hud_lines if json.loads(line)["source"] == "events"]
    damage_logged = [json.loads(line) for line in hud_lines if json.loads(line)["source"] == "damage"]
    assert all(item["applied"] is False for item in events_logged)
    assert all(item["applied"] is True for item in damage_logged)

    map_lines = [
        json.loads(line)
        for line in (session_dir / "map_info.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(entry["action"] == "map_generation_changed_kept_session" for entry in map_lines)
    assert any(entry["action"] == "reset_hangar" for entry in map_lines)


@pytest.mark.asyncio
async def test_debug_recorder_respects_toggle_and_writes_tab_ocr(tmp_path):
    cfg = AppConfig(debug_mode=False, min_ocr_players_per_side=1)
    recorder = DebugSessionRecorder(cfg, base_dir=tmp_path / "dbg")
    state = BattleSessionState(cfg, VehicleDB())
    state.battle_active = True

    recorder.record_stats_lookup({"canonical_key": "test", "outcome": "ok"})
    assert recorder.get_session_dir() is None
    assert not (tmp_path / "dbg").exists()

    cfg.debug_mode = True

    class FakeOcr:
        def recognize_column_detailed(self, _img):
            return ["Ikaros_02"], {"raw_boxes": [{"text": "Ikaros_02", "conf": 0.99, "y_center": 10.0}], "grouped_rows": ["Ikaros_02"], "filtered": ["Ikaros_02"]}

    def fake_capture(_roi):
        img = Image.new("RGB", (20, 20), (10, 20, 30))
        return SimpleNamespace(left_img=img, right_img=img, preview_png=b"\x89PNG\r\n\x1a\n")

    watcher = TabOcrWatcher(
        config=cfg,
        state=state,
        ocr=FakeOcr(),  # type: ignore[arg-type]
        key_probe=lambda _vk: True,
        window_title_probe=lambda: "War Thunder",
        capture_fn=fake_capture,
        debug_recorder=recorder,
    )
    assert await watcher.trigger_scan_if_ready(require_hold=False) is True
    session_dir = recorder.get_session_dir()
    assert session_dir is not None
    assert (session_dir / "tab_ocr.jsonl").exists()
    assert (session_dir / "tab_scan_001_preview.png").exists()
    assert (session_dir / "tab_scan_001_left.png").exists()
    assert (session_dir / "tab_scan_001_right.png").exists()
    watcher.stop()


@pytest.mark.asyncio
async def test_poller_skips_stale_previous_battle_hudmsg_buffer(tmp_path):
    cfg = AppConfig(debug_mode=True, log_language="ru")
    recorder = DebugSessionRecorder(cfg, base_dir=tmp_path / "dbg_stale")
    state = BattleSessionState(cfg, VehicleDB())
    parser = LogParser()
    poller = WarThunderPoller(config=cfg, state=state, parser=parser, debug_recorder=recorder)

    hud_payload = {
        "events": [],
        "damage": [
            {"id": 255, "msg": "Ikaros_02 (SAAB-105G) уничтожил ^MAOWO^ Tom Sawyer#2 (Т-62М-1)", "time": 610},
            {"id": 270, "msg": "gabrilck (XM246) сбил Ikaros_02 (SAAB-105G)", "time": 650},
            {"id": 378, "msg": "=FBC01= vggu874 потерял связь", "time": 29},
            {"id": 397, "msg": "⋇Tear7605 (XM800T) уничтожил Ikaros_02 (ItPsV Leopard)", "time": 118},
            {"id": 423, "msg": "Ikaros_02 (ItPsV Leopard) уничтожил =BOBAN= Fifa200 (MBT-70)", "time": 196},
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/map_info.json":
            return httpx.Response(200, json={"valid": True, "map_generation": 6})
        if request.url.path == "/hudmsg":
            return httpx.Response(200, json=hud_payload)
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await poller.poll_once(client) is True

    assert "tom sawyer#2" not in state.players
    assert "gabrilck" not in state.players
    assert state.players["ikaros_02"].kills == 1
    assert state.players["ikaros_02"].deaths == 1
    assert state.players["vggu874"].disconnected is True
    assert state.players["tear7605"].platform_hint == "console"
    assert poller.last_dmg == 423

    session_dir = recorder.get_session_dir()
    assert session_dir is not None
    entries = [json.loads(l) for l in (session_dir / "hudmsg_raw.jsonl").read_text(encoding="utf-8").splitlines()]
    skipped = [e for e in entries if e.get("note") == "skipped_stale_previous_battle"]
    applied = [e for e in entries if e["applied"] is True]
    assert len(skipped) == 2
    assert len(applied) == 3


@pytest.mark.asyncio
async def test_hangar_cursor_drain_and_crossed_boundary_preserves_tab_roster():
    fake_mono = [100.0]
    cfg = AppConfig(log_language="ru")
    state = BattleSessionState(cfg, VehicleDB())
    parser = LogParser()
    poller = WarThunderPoller(
        config=cfg,
        state=state,
        parser=parser,
        invalid_reset_grace_sec=3.0,
        mono_clock=lambda: fake_mono[0],
    )

    scenario = {
        "map": {"valid": False},
        "hud": {
            "events": [{"id": 500, "msg": "OldPlayer (T-72A) уничтожил OtherOld (XM803)", "time": 807}],
            "damage": [{"id": 1295, "msg": "OldPlayer (T-72A) уничтожил OtherOld (XM803)", "time": 807}],
        },
        "chat": [{"id": 42, "msg": "hi", "sender": "OldPlayer", "mode": "All", "enemy": False}],
        "timeout": False,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if scenario["timeout"]:
            raise httpx.ReadTimeout("transient stall")
        if request.url.path == "/map_info.json":
            return httpx.Response(200, json=scenario["map"])
        if request.url.path == "/hudmsg":
            return httpx.Response(200, json=scenario["hud"])
        if request.url.path == "/gamechat":
            return httpx.Response(200, json=scenario["chat"])
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        # Step 1: Hangar idle drains old /hudmsg & /gamechat cursors without adding state players
        assert await poller.poll_once(client) is True
        assert state.battle_active is False
        assert state.players == {}
        assert poller.last_dmg == 1295
        assert poller.last_evt == 500
        assert poller.last_chat_id == 42

        # Step 2: Enter new battle before first kill, then Tab OCR scans teams
        scenario["map"] = {"valid": True, "map_generation": 15}
        scenario["hud"] = {"events": [], "damage": []}
        scenario["chat"] = []
        assert await poller.poll_once(client) is True
        assert state.battle_active is True

        state.apply_tab_ocr(["Ikaros_02", "Boman14"], ["Bowman"])
        assert len([p for p in state.players.values() if p.team == "ally"]) == 2
        assert len([p for p in state.players.values() if p.team == "enemy"]) == 1

        # Step 3: Brief HTTP ReadTimeout (< 3.0s) preserves battle_active and cursors
        scenario["timeout"] = True
        fake_mono[0] = 101.0
        assert await poller.poll_once(client) is False
        assert state.battle_active is True
        assert poller.last_dmg == 1295

        # Step 4: First kill of new match arrives with time=79 (< prev 807 - 10 => crossed_boundary)
        # Tab OCR roster MUST NOT be wiped!
        scenario["timeout"] = False
        scenario["hud"] = {
            "events": [],
            "damage": [
                {"id": 1296, "msg": "-ZAY- Bowman (Olifant Mk.2) поджёг [TD42] Boman14 (AMV (BMP-3))", "time": 79},
                {"id": 1297, "msg": "Ikaros_02 (ItPsV Leopard) уничтожил -ZAY- Bowman (Olifant Mk.2)", "time": 82},
            ],
        }
        assert await poller.poll_once(client) is True
        assert state.players["ikaros_02"].team == "ally"
        assert state.players["boman14"].team == "ally"
        assert state.players["bowman"].team == "enemy"
        assert state.players["ikaros_02"].kills == 1
        assert state.players["bowman"].deaths == 1
