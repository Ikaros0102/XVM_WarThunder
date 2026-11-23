"""Tests for battle state mechanics: FPE 6s cooldown, Nuke Threat, Enemy Air, OCR team lock."""

import pytest

from wt_xvm.config import AppConfig
from wt_xvm.core.battle_state import BattleSessionState, compute_nuke_threat
from wt_xvm.core.log_parser import (
    AirAlertChatEvent,
    DisconnectEvent,
    FireEvent,
    KillEvent,
    SelfDeathEvent,
)
from wt_xvm.core.nick_cleaner import parse_player_identity
from wt_xvm.core.vehicle_db import VehicleDB
from wt_xvm.models import ModeStats, PlayerStatsRecord


def test_nuke_threat_thresholds():
    assert compute_nuke_threat(7, 0) is False
    assert compute_nuke_threat(8, 0) is True
    assert compute_nuke_threat(8, 1) is False
    assert compute_nuke_threat(9, 1) is True
    assert compute_nuke_threat(11, 2) is False
    assert compute_nuke_threat(12, 2) is True
    assert compute_nuke_threat(14, 3) is False
    assert compute_nuke_threat(15, 3) is True
    assert compute_nuke_threat(15, 5) is True


def test_fpe_cooldown_and_postmortem_guard():
    now = 100.0
    state = BattleSessionState(AppConfig(), VehicleDB(), clock=lambda: now)
    state.apply_tab_ocr(["Attacker"], ["Victim"])

    att = parse_player_identity("Attacker")
    vic = parse_player_identity("Victim")

    # First fire at t=10 -> FPE 2 -> 1
    state.apply_hud_event(FireEvent(10.0, att, "TAM", vic, "Chieftain Mk 5"))
    assert state.players[vic.canonical_key].fpe_count == 1

    # Second fire 3s later -> ignored due to 6s cooldown -> still 1
    state.apply_hud_event(FireEvent(13.0, att, "TAM", vic, "Chieftain Mk 5"))
    assert state.players[vic.canonical_key].fpe_count == 1

    # Third fire 7s after first -> FPE 1 -> 0
    state.apply_hud_event(FireEvent(17.0, att, "TAM", vic, "Chieftain Mk 5"))
    assert state.players[vic.canonical_key].fpe_count == 0

    # Victim dies at t=20 -> FPE resets to 2
    state.apply_hud_event(KillEvent(20.0, att, "TAM", vic, "Chieftain Mk 5"))
    assert state.players[vic.canonical_key].fpe_count == 2
    assert state.players[vic.canonical_key].current_vehicle is None

    # Post-mortem fire at t=20.0 on same destroyed vehicle MUST NOT decrement FPE
    state.apply_hud_event(FireEvent(20.0, att, "TAM", vic, "Chieftain Mk 5"))
    assert state.players[vic.canonical_key].fpe_count == 2


def test_enemy_air_counter_and_recon_drone_exclusion():
    state = BattleSessionState(AppConfig(), VehicleDB())
    state.apply_tab_ocr(["Ally1"], ["Pilot1", "Heli1", "Tank1"])

    ally = parse_player_identity("Ally1")
    pilot = parse_player_identity("Pilot1")
    heli = parse_player_identity("Heli1")
    tank = parse_player_identity("Tank1")

    state.apply_hud_event(KillEvent(10.0, pilot, "Yak-130", ally, "Leopard I"))
    state.apply_hud_event(FireEvent(12.0, heli, "Z-11WA", ally, "Leopard I"))
    # Recon micro by Tank1 should NOT classify Tank1 as plane/helicopter
    state.apply_hud_event(KillEvent(14.0, ally, "Gepard", tank, "Recon Micro"))
    assert state.players[ally.canonical_key].kills == 0
    assert state.players[tank.canonical_key].deaths == 0

    air = state.compute_enemy_air()
    assert air.planes == 1
    assert air.helicopters == 1

    # Shot down removes plane, disconnect removes heli
    state.apply_hud_event(SelfDeathEvent(20.0, pilot, "Yak-130"))
    state.apply_hud_event(DisconnectEvent(22.0, heli))
    air_after = state.compute_enemy_air()
    assert air_after.planes == 0
    assert air_after.helicopters == 0


def test_air_alert_timer_expiration():
    fake_now = [1000.0]
    cfg = AppConfig(air_alert_duration_sec=10)
    state = BattleSessionState(cfg, VehicleDB(), clock=lambda: fake_now[0])
    sender = parse_player_identity("roma_chumeyko")

    state.apply_chat_event(AirAlertChatEvent(1000.0, sender, azimuth=330, altitude=200))
    snap = state.snapshot()
    assert snap.air_alert is not None
    assert snap.air_alert.azimuth == 330
    assert snap.air_alert.altitude == 200

    # Reset timer on new alert at t=1006
    fake_now[0] = 1006.0
    state.apply_chat_event(AirAlertChatEvent(1006.0, sender, azimuth=100, altitude=500))
    assert state.snapshot().air_alert is not None
    assert state.snapshot().air_alert.azimuth == 100

    # Still active at t=1015
    fake_now[0] = 1015.0
    assert state.snapshot().air_alert is not None

    # Expired at t=1016
    fake_now[0] = 1016.0
    assert state.snapshot().air_alert is None


def test_duplicate_kill_and_postmortem_self_death_guards():
    state = BattleSessionState(AppConfig(), VehicleDB())
    state.apply_tab_ocr(["Attacker"], ["Victim"])

    att = parse_player_identity("Attacker")
    vic = parse_player_identity("Victim")

    # Initial kill + duplicate echo within 2s -> counts 1 kill / 1 death
    state.apply_hud_event(KillEvent(100.0, att, "MBT-70", vic, "Leopard 1A5"))
    state.apply_hud_event(KillEvent(100.5, att, "MBT-70", vic, "Leopard 1A5"))
    assert state.players[att.canonical_key].kills == 1
    assert state.players[vic.canonical_key].deaths == 1

    # Post-mortem self-death within 5s on same vehicle -> MUST NOT increment deaths to 2
    state.apply_hud_event(SelfDeathEvent(101.2, vic, "Leopard 1A5"))
    assert state.players[vic.canonical_key].deaths == 1

    # Legitimate later self-death after respawning (>5s or new sighting/vehicle) DOES increment
    state.apply_hud_event(SelfDeathEvent(130.0, vic, "Leopard 1A5"))
    assert state.players[vic.canonical_key].deaths == 2


def test_cjk_clan_tag_placeholder_matching_both_orders():
    # Order A: HUD log sees full CJK nicks first (`team="unknown"`), then Tab OCR only reads Latin clan tags
    state_a = BattleSessionState(AppConfig(), VehicleDB())
    state_a.apply_hud_event(
        KillEvent(
            10.0,
            parse_player_identity("=VAKey= 雾雨摸鱼莎"),
            "STB-2",
            parse_player_identity("-URAG1- 小栗帽今天吃饱了吗"),
            "Штурм-С",
        )
    )
    assert state_a.players["雾雨摸鱼莎"].team == "unknown"
    assert state_a.players["小栗帽今天吃饱了吗"].team == "unknown"

    added_a, added_e = state_a.apply_tab_ocr(["AllyOne"], ["=VAKey=", "URAG1-"])
    assert (added_a, added_e) == (1, 2)
    assert state_a.players["雾雨摸鱼莎"].team == "enemy"
    assert state_a.players["小栗帽今天吃饱了吗"].team == "enemy"
    assert "=vakey=" not in state_a.players
    assert "urag1-" not in state_a.players

    # 2nd Tab scan with degraded OCR ("VAKey=", "RAG1-") must NOT add duplicates
    added_a2, added_e2 = state_a.apply_tab_ocr(["AllyOne"], ["VAKey=", "RAG1-"])
    assert (added_a2, added_e2) == (0, 0)
    assert len([p for p in state_a.players.values() if p.team == "enemy"]) == 2

    # Order B: Tab OCR sees "=VAKey=" first, then HUD log reveals "=VAKey= 雾雨摸鱼莎" -> rekeys preserving team
    state_b = BattleSessionState(AppConfig(), VehicleDB())
    state_b.apply_tab_ocr(["AllyOne"], ["=VAKey="])
    assert state_b.players["=vakey="].team == "enemy"
    state_b.apply_hud_event(
        KillEvent(
            20.0,
            parse_player_identity("=VAKey= 雾雨摸鱼莎"),
            "STB-2",
            parse_player_identity("AllyOne"),
            "Leopard I",
        )
    )
    assert "=vakey=" not in state_b.players
    assert state_b.players["雾雨摸鱼莎"].team == "enemy"
    assert state_b.players["雾雨摸鱼莎"].kills == 1


def test_apply_stats_result_strips_unbracketed_graphical_clan_tag():
    state = BattleSessionState(AppConfig(), VehicleDB())
    state.apply_tab_ocr(["GhoW Сын шайлушая"], ["EnemyOne"])
    key = parse_player_identity("GhoW Сын шайлушая").canonical_key
    assert state.players[key].display_nick == "GhoW Сын шайлушая"

    state.apply_stats_result(
        PlayerStatsRecord(
            canonical_key=key,
            nick="Сын шайлушая",
            status="ok",
            rb=ModeStats(winrate=52.0, kd=1.4, battles=5000),
        )
    )
    assert state.players[key].display_nick == "Сын шайлушая"
    assert state.players[key].clan_tag == "GhoW"


def test_clan_only_placeholders_hidden_and_rekeyed_with_cjk_logs():
    enqueued = []
    state = BattleSessionState(AppConfig(), VehicleDB(), stats_enqueue_cb=lambda p: enqueued.append(p.canonical_key))

    # Tab OCR sees bare clan fragments + merged/truncated CJK names
    added_a, added_e = state.apply_tab_ocr(
        ["=Be=", "-HWH-Roselia", "AllyOne"],
        ["=NQF", "=CPNB4=", "BofSsCoBeT WnNoH"],
    )
    assert added_a == 2  # AllyOne + Roselia (not =Be=)
    assert added_e == 1  # BofSsCoBeT WnNoH (not =NQF or =CPNB4=)

    # Placeholders must NEVER be enqueued for ThunderSkill or exposed in snapshot()
    assert "=be=" not in enqueued
    assert "=nqf" not in enqueued
    assert "=cpnb4=" not in enqueued
    snap1 = state.snapshot()
    assert [p.display_nick for p in snap1.allies] == ["AllyOne", "Roselia"]
    assert [p.display_nick for p in snap1.enemies] == ["BofSsCoBeT WnNoH"]

    # Repeated Tab scan with '=CNQF=' must deduplicate against existing '=NQF' placeholder
    added_a2, added_e2 = state.apply_tab_ocr([], ["=CNQF="])
    assert (added_a2, added_e2) == (0, 0)

    # HUD logs arrive with real UTF-8 CJK/merged identities -> upgrade placeholders and partial names preserving teams!
    state.apply_hud_event(
        KillEvent(
            10.0,
            parse_player_identity("=CPNB4= 洞穴ゴブリン不会梦到JK"),
            "M1296",
            parse_player_identity("=BeiY= 打过长江去解放全中国"),
            "Объект 685",
        )
    )
    state.apply_hud_event(
        KillEvent(
            12.0,
            parse_player_identity("═BofSs║ ⋇CoBeT WnNoH"),
            "MBT-70",
            parse_player_identity("-HWH- Roselia最強の壓-湊友希那"),
            "Т-55АМД-1",
        )
    )
    state.apply_hud_event(
        KillEvent(
            14.0,
            parse_player_identity("=CNQF= 原来甜妹是咸的"),
            "TAM",
            parse_player_identity("AllyOne"),
            "T-72M1",
        )
    )

    snap2 = state.snapshot()
    assert {p.display_nick for p in snap2.allies} == {"AllyOne", "打过长江去解放全中国", "Roselia最強の壓-湊友希那"}
    assert {p.display_nick for p in snap2.enemies} == {"洞穴ゴブリン不会梦到JK", "CoBeT WnNoH", "原来甜妹是咸的"}
    assert snap2.unassigned_count == 0


def test_cyrillic_and_modern_vehicle_classification():
    vdb = VehicleDB()
    for heli in ("Ми-28НМ", "Ка-52", "Ми-8АМТШ", "Z-10ME", "◥AH-64E"):
        assert vdb.classify(heli) == "helicopter", f"Expected helicopter for {heli}"
    for plane in ("Су-30СМ2", "МиГ-29СМТ", "Су-34", "Як-141", "◌F/A-18C", "F/A-18E", "◄EF-2000", "◢F-16AM"):
        assert vdb.classify(plane) == "plane", f"Expected plane for {plane}"
    for ground in ("СУ-100", "СУ-85М", "СУ-152", "СУ-76М", "СУ-122-54", "◍Т-80У-Е1", "FK-2000", '"БУК-М3" (СОУ)'):
        assert vdb.classify(ground) == "ground", f"Expected ground for {ground}"


def test_cjk_ascii_skeleton_combat_inference_and_16_player_cap():
    enqueued: list[str] = []
    state = BattleSessionState(
        AppConfig(),
        VehicleDB(),
        stats_enqueue_cb=lambda p: enqueued.append(p.canonical_key),
    )

    # Simulate realistic Tab scan with mangled CJK ASCII skeletons, underscored variant, and 1-char placeholder
    distinct_allies = [
        "FalconNine",
        "IronBadger",
        "ViperZero",
        "MantisRay",
        "CobraVex",
        "ShadowKite",
        "TitanRook",
        "FrostLynx",
        "BlazeWasp",
        "StormOwl",
    ]  # 10 distinct players (+ 5 visible + 1 placeholder = 16 allies total)
    distinct_enemies = [
        "RedFoxZero",
        "GrimWolf88",
        "VoltTiger",
        "RustJackal",
        "DarkRaven",
        "AshPanther",
        "BoltVulture",
        "DustMink",
        "PyroGecko",
        "ZincMoose",
        "OnyxOtter",
    ]  # 11 distinct players
    allies_tab = [
        "=GPUBZ= Luigi6481",
        "SHUWO^ _9",
        "[CRSI]_X",
        "RST.Mbwd",
        "=S=",
        "=XRyCT= HeHaBucTb",
    ] + distinct_allies  # 5 visible + 1 placeholder + 11 => 16 allies total after =S= resolves

    enemies_tab = [
        "=CNZX= 安凌君少",
        "[To0z]-34",
        "WGSR-Sakura",
        "CTMDX=",
    ] + distinct_enemies  # 14 visible + 1 placeholder (CTMDX=) = 15 (slot 16 is clanless CJK player)

    state.apply_tab_ocr(allies_tab, enemies_tab)

    # 1. ASCII-skeleton CJK upgrades + underscore-stripped fuzzy match + re-query stats
    state.apply_hud_event(
        KillEvent(10.0, parse_player_identity("^SHUWO^ 司长_向9"), "◄EMBT", parse_player_identity("RedFoxZero"), "2С38")
    )
    state.apply_hud_event(
        KillEvent(12.0, parse_player_identity("[CRSI] 玖_风"), "Leopard 2A7V", parse_player_identity("GrimWolf88"), "TKX")
    )
    state.apply_hud_event(
        KillEvent(14.0, parse_player_identity(".1RST. 一车两发穿三被四Mbwd五马分尸"), "T 80 U", parse_player_identity("VoltTiger"), "Type 10")
    )
    state.apply_hud_event(
        KillEvent(16.0, parse_player_identity("[TooEz] T-34学弟不会遇见虎王大小姐"), "◍Т-80У-Е1", parse_player_identity("FalconNine"), "TAM")
    )
    state.apply_hud_event(
        KillEvent(18.0, parse_player_identity("-WGSR- Sakura糖尿病害我蛀牙"), "Т-80БВМ", parse_player_identity("=XRyCT= __HeHaBucTb__"), "Leopard 2A7V")
    )
    # Claim CTMDX= placeholder so all 15 initial enemies are accounted for before slot 16
    state.apply_hud_event(
        KillEvent(19.0, parse_player_identity("=CTMDx= 奖励你多打两个小时俄罗斯原神"), "БМПТ", parse_player_identity("IronBadger"), "TAM")
    )

    assert "shuwo^ _9" not in state.players
    assert "司长_向9" in state.players and state.players["司长_向9"].team == "ally"
    assert "玖_风" in state.players and state.players["玖_风"].team == "ally"
    assert "一车两发穿三被四mbwd五马分尸" in state.players and state.players["一车两发穿三被四mbwd五马分尸"].team == "ally"
    assert "t-34学弟不会遇见虎王大小姐" in state.players and state.players["t-34学弟不会遇见虎王大小姐"].team == "enemy"
    assert "sakura糖尿病害我蛀牙" in state.players and state.players["sakura糖尿病害我蛀牙"].team == "enemy"
    assert "奖励你多打两个小时俄罗斯原神" in state.players and state.players["奖励你多打两个小时俄罗斯原神"].team == "enemy"
    assert "__hehabuctb__" in state.players and state.players["__hehabuctb__"].team == "ally"

    # Upgraded/re-keyed canonical keys must trigger fresh ThunderSkill lookups
    for upgraded_key in ("司长_向9", "玖_风", "一车两发穿三被四mbwd五马分尸", "sakura糖尿病害我蛀牙", "__hehabuctb__"):
        assert upgraded_key in enqueued

    # 2. Combat-graph team inference: =CNZX= (enemy) kills =SDXII= (missing from Tab except =S= placeholder)
    state.apply_hud_event(
        KillEvent(
            20.0,
            parse_player_identity("=CNZX= 安凌君少"),
            "Freccia",
            parse_player_identity("=SDXII= 假笑男孩格拉西莫夫大将"),
            "Puma VJTF",
        )
    )
    assert "=s=" not in state.players
    assert state.players["假笑男孩格拉西莫夫大将"].team == "ally"

    # 3. Clanless pure-CJK enemy flying Су-30СМ2 inferred onto enemy team when killing Luigi6481 (ally)
    state.apply_hud_event(
        KillEvent(
            25.0,
            parse_player_identity("潮汕人民注意米和锅 碗碎"),
            "Су-30СМ2",
            parse_player_identity("=GPUBZ= Luigi6481"),
            "Leopard 2A7V",
        )
    )
    cjk_pilot = state.players["潮汕人民注意米和锅 碗碎"]
    assert cjk_pilot.team == "enemy"
    assert cjk_pilot.current_vehicle_kind == "plane"
    assert state.compute_enemy_air().planes == 1

    # 4. 16-player cap: an extra combat event from an unknown outsider vs full 16-player team must NOT push allies > 16
    snap = state.snapshot()
    assert len(snap.allies) == 16
    assert len(snap.enemies) == 16
    assert snap.unassigned_count == 0

    state.apply_hud_event(
        KillEvent(30.0, parse_player_identity("=CNZX= 安凌君少"), "Freccia", parse_player_identity("ExtraGhost17"), "TAM")
    )
    assert len(state.snapshot().allies) == 16
    assert state.players["extraghost17"].team == "unknown"
