"""Tests for externalized RU/EN log & chat parser and balanced-parenthesis actor extraction."""

from wt_xvm.core.log_parser import (
    AirAlertChatEvent,
    DamageEvent,
    DisconnectEvent,
    FireEvent,
    KillEvent,
    LogParser,
    SelfDeathEvent,
    SenderSightingChatEvent,
    VehicleSightingEvent,
    extract_actor,
)


def test_extract_actor_nested_parentheses():
    parsed = extract_actor("SHADOW- (Type 16 (P))")
    assert parsed is not None
    ident, veh = parsed
    assert ident.clean_nick == "SHADOW-"
    assert veh == "Type 16 (P)"

    parsed2 = extract_actor("-COGAS- xXBlutrabeXx#1 (XM1 (Chrysler))")
    assert parsed2 is not None
    assert parsed2[0].clan_tag == "-COGAS-"
    assert parsed2[0].clean_nick == "xXBlutrabeXx#1"
    assert parsed2[1] == "XM1 (Chrysler)"

    parsed3 = extract_actor("[1HACK] vovka716 (AMV (BMP-3))")
    assert parsed3 is not None
    assert parsed3[0].clean_nick == "vovka716"
    assert parsed3[1] == "AMV (BMP-3)"


def test_hud_events_en_and_ru():
    parser = LogParser()

    ev_kill = parser.parse_hud_message("=PCIA= mehdiji (BTR-82AT) destroyed SHADOW- (Type 16 (P))", 86.0)
    assert isinstance(ev_kill, KillEvent)
    assert ev_kill.attacker.clean_nick == "mehdiji"
    assert ev_kill.attacker_vehicle == "BTR-82AT"
    assert ev_kill.victim.clean_nick == "SHADOW-"
    assert ev_kill.victim_vehicle == "Type 16 (P)"

    ev_fire = parser.parse_hud_message("[DHrs] ⋇kkomakbr (TAM) set afire karuoce (Chieftain Mk 5)", 152.0)
    assert isinstance(ev_fire, FireEvent)
    assert ev_fire.victim.clean_nick == "karuoce"
    assert ev_fire.victim_vehicle == "Chieftain Mk 5"

    ev_dmg = parser.parse_hud_message(
        "[HQ22] Duce67 (PGZ88) нанёс фатальное повреждение Renamed#109 (Recon Micro)", 351.0
    )
    assert isinstance(ev_dmg, DamageEvent)
    assert ev_dmg.victim_vehicle == "Recon Micro"

    ev_ai = parser.parse_hud_message("[HQ22] Duce67 (PGZ88) уничтожил [ии] Recon Micro", 366.0)
    assert isinstance(ev_ai, VehicleSightingEvent)
    assert ev_ai.actor.clean_nick == "Duce67"
    assert ev_ai.vehicle == "PGZ88"

    ev_wpn = parser.parse_hud_message("[HQ22] Duce67 (PGZ88) уничтожил weapons/us_750lb_m117_cone_45/short", 448.0)
    assert isinstance(ev_wpn, VehicleSightingEvent)

    ev_self = parser.parse_hud_message("Renamed#109 (AMV (BMP-3)) выведен из строя", 449.0)
    assert isinstance(ev_self, SelfDeathEvent)
    assert ev_self.actor.clean_nick == "Renamed#109"
    assert ev_self.vehicle == "AMV (BMP-3)"

    assert parser.parse_hud_message('Ikaros_02 (SAAB-105G) получил "Тройной удар!"', 234.0) is None
    assert parser.parse_hud_message("=PCIA= mehdiji (BTR-82AT) has delivered the first strike!", 87.0) is None

    ev_dc1 = parser.parse_hud_message("-KBRN- Вливаю потерял связь", 271.0)
    ev_dc2 = parser.parse_hud_message("Вливаюtd! kd?NET_PLAYER_DISCONNECT_FROM_GAME", 271.0)
    assert isinstance(ev_dc1, DisconnectEvent)
    assert isinstance(ev_dc2, DisconnectEvent)
    assert ev_dc1.actor.canonical_key == ev_dc2.actor.canonical_key == "вливаю"


def test_air_alert_requires_azimuth_and_altitude_and_friendly_only():
    parser = LogParser()

    # Bare "Воздух!" MUST be ignored per user specification
    bare_ru = parser.parse_chat_message("Воздух!", sender="Ikaros_02", mode="Команда", enemy=False, timestamp=269.0)
    assert isinstance(bare_ru, SenderSightingChatEvent)

    bare_en = parser.parse_chat_message("Air Alert!", sender="Ikaros_02", mode="Team", enemy=False, timestamp=200.0)
    assert isinstance(bare_en, SenderSightingChatEvent)

    # Full RU & EN alerts MUST trigger
    full_ru = parser.parse_chat_message(
        "Воздух! Азимут 330, высота около 200", sender="roma_chumeyko", mode="Команда", enemy=False, timestamp=288.0
    )
    assert isinstance(full_ru, AirAlertChatEvent)
    assert full_ru.azimuth == 330
    assert full_ru.altitude == 200

    full_en = parser.parse_chat_message(
        "Air Alert! Azimuth 160, the altitude is about 300",
        sender="Ikaros_02",
        mode="Team",
        enemy=False,
        timestamp=216.0,
    )
    assert isinstance(full_en, AirAlertChatEvent)
    assert full_en.azimuth == 160
    assert full_en.altitude == 300

    # Enemy or All-chat alerts MUST be ignored
    enemy_alert = parser.parse_chat_message(
        "Air Alert! Azimuth 160, the altitude is about 300", sender="Enemy", mode="All", enemy=True, timestamp=216.0
    )
    assert isinstance(enemy_alert, SenderSightingChatEvent)


def test_sam_subunit_victims_filtered_while_player_sam_preserved():
    parser = LogParser()

    # Destroyed autonomous SAM launcher sub-units must NOT create fake player victims
    for msg in (
        "-BotD- Zxznb114514 (ZTZ96B) уничтожил IRIS-T SLM (СОУ)",
        "^BH3r^ _736624389 (Leopard 2A7V) уничтожил FSAF SAMP/T (Mamba) (СОУ)",
        '=6dog9= KiKU_CHiiYO (M1A1 Click-Bait) уничтожил "БУК-М3" (СОУ)',
    ):
        ev = parser.parse_hud_message(msg, 300.0, language="ru")
        assert isinstance(ev, VehicleSightingEvent)

    # Human player operating a SAM vehicle MUST still parse cleanly as a KillEvent
    ev_human = parser.parse_hud_message(
        "-June- _8snrbnjgz (IRIS-T SLM (СОУ)) сбил =CTMDx= 奖励你多打两个小时俄罗斯原神 (Ми-28НМ)",
        349.0,
        language="ru",
    )
    assert isinstance(ev_human, KillEvent)
    assert ev_human.attacker.clean_nick == "_8snrbnjgz"
    assert ev_human.attacker_vehicle == "IRIS-T SLM (СОУ)"
    assert ev_human.victim.clean_nick == "奖励你多打两个小时俄罗斯原神"
    assert ev_human.victim_vehicle == "Ми-28НМ"
