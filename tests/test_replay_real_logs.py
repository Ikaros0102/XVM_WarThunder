"""End-to-end replay test against the 4 real battle log and chat files in the workspace."""

from pathlib import Path

from wt_xvm.config import AppConfig
from wt_xvm.core.battle_state import BattleSessionState
from wt_xvm.core.log_parser import LogParser, parse_md_chat_lines, parse_md_log_lines
from wt_xvm.core.vehicle_db import VehicleDB

ROOT = Path(__file__).resolve().parent.parent


def test_replay_en_log_and_chat():
    parser = LogParser()
    state = BattleSessionState(AppConfig(log_language="en"), VehicleDB())

    # Simulate Tab OCR team split for players in EN_Log
    allies = [
        "Ikaros_02",
        "[AuPth] daniel_mosko1",
        "┾SYRAX┿ babou95",
        "[iCAT] TuGuZZZ",
        "[_1ST_] Mon1k3",
        "=T4T= ⋇ChaMaGo-",
        "QuantomKnight",
        "Fenixon777",
        "karuoce",
    ]
    enemies = [
        "=PCIA= mehdiji",
        "SHADOW-",
        "=LIM5= ⋇Zbuntowany_ironm",
        "=KyMAR= -Н_П_Т-",
        "⋇RUS_Moscow_Bomj",
        "[QGS] worm96930",
        "[bXd] BaTHbIu_ZoV_OpKa",
        "=EXPLN= SirFynnster",
        "┺xALLx┻ Тaker",
        "[NKVDz] YourBro_10",
        "-ALSN- 弦_",
        "PereLucky",
        "[12RCC] P4SKUD4 блин",
        "-e6a- Дедушка Вова",
        "[BRCM] ⋇Dragon9993517",
        "-77MR- BELAZ Z Z",
        "[DHrs] ⋇kkomakbr",
        "_97e917jr3",
        "-VdeK- ⋇AzZuZoW-_-",
        "-COGAS- xXBlutrabeXx#1",
        "⋇davlet_092",
    ]
    state.apply_tab_ocr(allies, enemies)

    events = parse_md_log_lines((ROOT / "EN_Log.md").read_text(encoding="utf-8"))
    for ts, msg in events:
        ev = parser.parse_hud_message(msg, timestamp=ts, language="en")
        if ev is not None:
            state.apply_hud_event(ev)

    # Check karuoce: 2 fires (22s apart) -> FPE 2->1->0, then died at 2:59 -> reset to 2
    karuoce = state.players["karuoce"]
    assert karuoce.deaths == 1
    assert karuoce.fpe_count == 2
    assert karuoce.current_vehicle is None

    # Check nested parenthesis vehicles parsed properly
    assert state.players["shadow-"].deaths == 1
    assert state.players["azZuzow-_-".casefold()].deaths == 2

    # Check =KyMAR= -Н_П_Т- preserved inner dashes of nickname
    npt = state.players["-н_п_т-"]
    assert npt.clan_tag == "=KyMAR="
    assert npt.display_nick == "-Н_П_Т-"
    assert npt.current_vehicle == "BMP-2"

    # Check Enemy Air Counter at end of EN_Log:
    # Yak-130 (Тaker) was shot down & disconnected; Su-22M3 (BaTHbIu_ZoV_OpKa) is alive!
    air = state.compute_enemy_air()
    assert air.planes == 1
    assert air.plane_list == ["Su-22M3"]
    assert air.helicopters == 0

    # Check EN_Chat Air Alerts (3 alerts with azimuth & altitude)
    fake_now = [100.0]
    chat_state = BattleSessionState(AppConfig(log_language="en"), VehicleDB(), clock=lambda: fake_now[0])
    chat_lines = parse_md_chat_lines((ROOT / "EN_Chat.md").read_text(encoding="utf-8"))
    alerts = []
    for ts, mode, sender, msg in chat_lines:
        fake_now[0] = ts
        ev = parser.parse_chat_message(msg, sender=sender, mode=mode, enemy=False, timestamp=ts, language="en")
        if ev is not None:
            chat_state.apply_chat_event(ev)
            snap = chat_state.snapshot()
            if snap.air_alert is not None and snap.air_alert.triggered_at == ts:
                alerts.append((snap.air_alert.azimuth, snap.air_alert.altitude))
    assert alerts == [(160, 300), (330, 1000), (100, 1000)]


def test_replay_ru_log_and_chat():
    parser = LogParser()
    fake_now = [0.0]
    state = BattleSessionState(AppConfig(log_language="ru"), VehicleDB(), clock=lambda: fake_now[0])

    allies = [
        "Ikaros_02",
        "[BALTS] ⋇rSemjonovs",
        "[1HACK] vovka716",
        "[1HACK] roma_chumeyko",
        "DenebHyde",
        "evilllllllllllll",
        "=REKET= ⋇WHITE_DEATHwtsn0",
        "sergboombomm",
        "tankwidow",
    ]
    enemies = [
        "[OGAST] SimonSimon1",
        "=CNWTP= Tinker_qoi",
        "⋇funny_bee265",
        "⋇Pluto027oceanic",
        "[HQ22] Duce67",
        "-HGFS- KernelPanic_0x0",
        "[GSRpl] TomixPl",
        "^OM2GD^ FARTYK#2",
        "-KBRN- Вливаю",
        "=OFN= 宵崎kanade",
        "=N3RFD= ⋇osaramp06",
        "[B1D] 卡特罗",
        "-EMCIS- Urıël",
        "=HeTu= Kangmiao",
        "Renamed#109",
    ]
    state.apply_tab_ocr(allies, enemies)

    for ts, msg in parse_md_log_lines((ROOT / "RU_Log.md").read_text(encoding="utf-8")):
        fake_now[0] = ts
        ev = parser.parse_hud_message(msg, timestamp=ts, language="ru")
        if ev is not None:
            state.apply_hud_event(ev)

    # Check Ikaros_02: 2 kills, 1 death (shot down at 7:28 followed by post-mortem fire at 7:28 -> FPE stays 2)
    ikaros = state.players["ikaros_02"]
    assert ikaros.kills == 2
    assert ikaros.deaths == 1
    assert ikaros.fpe_count == 2
    assert ikaros.current_vehicle is None

    # Check Duce67: died once to Ikaros_02, damaged Renamed#109 (Recon Micro) and killed [ии] Recon Micro + weapons/... -> 0 player kills!
    duce = state.players["duce67"]
    assert duce.kills == 1  # Only Ikaros_02 (SAAB-105G) counts as a kill
    assert duce.current_vehicle == "PGZ88"

    # Check Renamed#109: self-death on AMV (BMP-3) + disconnect
    renamed = state.players["renamed#109"]
    assert renamed.deaths == 1
    assert renamed.disconnected is True
    assert renamed.current_vehicle is None

    # Check FARTYK#2 (Як-130) & 卡特罗 (Z-11WA) were both shot down -> 0 enemy air at end
    air = state.compute_enemy_air()
    assert air.planes == 0
    assert air.helicopters == 0

    # Check rSemjonovs kills/deaths
    rsem = state.players["rsemjonovs"]
    assert rsem.kills == 5
    assert rsem.deaths == 1

    # Replay RU_Chat.md onto the same state
    alerts = []
    for ts, mode, sender, msg in parse_md_chat_lines((ROOT / "RU_Chat.md").read_text(encoding="utf-8")):
        fake_now[0] = ts
        ev = parser.parse_chat_message(msg, sender=sender, mode=mode, enemy=False, timestamp=ts, language="ru")
        if ev is not None:
            state.apply_chat_event(ev)
            snap = state.snapshot()
            if snap.air_alert is not None and snap.air_alert.triggered_at == ts:
                alerts.append((snap.air_alert.azimuth, snap.air_alert.altitude))

    # Bare "Воздух!" at 4:29 (269s) MUST be ignored; only the 2 alerts with azimuth & altitude fire
    assert alerts == [(330, 200), (330, 500)]

    # Console player WHITE_DEATHwtsn0@psn enriched platform hint from chat
    wd = state.players["white_deathwtsn0"]
    assert wd.platform_hint == "psn"
