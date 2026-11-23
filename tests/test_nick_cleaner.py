"""Tests for clan-tag stripping, whitespace preservation, and console markers."""

from wt_xvm.core.nick_cleaner import is_clan_only_fragment, parse_player_identity


def test_clan_tags_and_spaces_preserved():
    cases = [
        ("=Audra= Karsten5244", "=Audra=", "Karsten5244", None, None),
        ("[AuPth] daniel_mosko1", "[AuPth]", "daniel_mosko1", None, None),
        ("[_1ST_] Mon1k3", "[_1ST_]", "Mon1k3", None, None),
        ("-ALSN- 弦_", "-ALSN-", "弦_", None, None),
        ("^OM2GD^ FARTYK#2", "^OM2GD^", "FARTYK#2", None, None),
        ("╖TAG╖ Brown rat", "╖TAG╖", "Brown rat", None, None),
        ("┾SYRAX┿ babou95", "┾SYRAX┿", "babou95", None, None),
        ("┺xALLx┻ Тaker", "┺xALLx┻", "Тaker", None, None),
        ("[12RCC] P4SKUD4 блин", "[12RCC]", "P4SKUD4 блин", None, None),
        ("-e6a- Дедушка Вова", "-e6a-", "Дедушка Вова", None, None),
        ("-77MR- BELAZ Z Z", "-77MR-", "BELAZ Z Z", None, None),
        ("ГЕНЕРАЛ ЙУХОП", None, "ГЕНЕРАЛ ЙУХОП", None, None),
        ("=KyMAR= -Н_П_Т-", "=KyMAR=", "-Н_П_Т-", None, None),
    ]
    for raw, exp_clan, exp_nick, exp_pref, exp_plat in cases:
        res = parse_player_identity(raw)
        assert res.clan_tag == exp_clan, f"Failed clan_tag for {raw}"
        assert res.clean_nick == exp_nick, f"Failed clean_nick for {raw}"
        assert res.console_prefix == exp_pref
        assert res.platform_hint == exp_plat


def test_console_markers_and_canonical_key_unification():
    from_log = parse_player_identity("=REKET= ⋇WHITE_DEATHwtsn0")
    from_chat = parse_player_identity("WHITE_DEATHwtsn0@psn")

    assert from_log.clan_tag == "=REKET="
    assert from_log.console_prefix == "⋇"
    assert from_log.clean_nick == "WHITE_DEATHwtsn0"
    assert from_log.platform_hint == "console"

    assert from_chat.clan_tag is None
    assert from_chat.clean_nick == "WHITE_DEATHwtsn0"
    assert from_chat.platform_hint == "psn"
    assert from_log.canonical_key == from_chat.canonical_key == "white_deathwtsn0"

    star_player = parse_player_identity("*shino_rs")
    assert star_player.console_prefix == "*"
    assert star_player.clean_nick == "shino_rs"
    assert star_player.canonical_key == "shino_rs"


def test_ocr_mangled_asymmetric_clan_tags_and_preserved_tokens():
    cases = [
        ("-ADSM= Galahad4657", "-ADSM=", "Galahad4657"),
        ("|KNA- PlayerOne", "|KNA-", "PlayerOne"),
        ("[TAG| Brown rat", "[TAG|", "Brown rat"),
        ("*MILKS^ Charizard X", "*MILKS^", "Charizard X"),
        ('"RZMT" NeedForTanks', '"RZMT"', "NeedForTanks"),
        ("=KyMAR= -Н_П_Т-", "=KyMAR=", "-Н_П_Т-"),
        (".XDGKD. lastikt2341", ".XDGKD.", "lastikt2341"),
        ("┼NERFD┽ d1ddedbc_", "┼NERFD┽", "d1ddedbc_"),
        ("—Rad7- awa486 desuwa", "—Rad7-", "awa486 desuwa"),
        ("_RAT_^ SKORDAZZ", "_RAT_^", "SKORDAZZ"),
        ("-HWH-Roselia", "-HWH-", "Roselia"),
        ("[TD42]Boman14", "[TD42]", "Boman14"),
        ("_Srbosjek_", None, "_Srbosjek_"),
        ("... Player", None, "... Player"),
        ("Mr. Smith", None, "Mr. Smith"),
        ("Hans-Werner", None, "Hans-Werner"),
    ]
    for raw, exp_clan, exp_nick in cases:
        res = parse_player_identity(raw)
        assert res.clan_tag == exp_clan, f"Failed clan_tag for {raw}"
        assert res.clean_nick == exp_nick, f"Failed clean_nick for {raw}"


def test_clan_only_fragment_detection():
    positives = [
        "=Be=",
        "=CNQF=",
        "=NQF",
        "=CPNB4=",
        "=peony=",
        "[LTXB]",
        "^LLLCM^",
        "[Regen]1",
        "=144PL=5",
        "[GRSVs]#5",
        "CTMDX=",
        "SKN=",
        "=S=",
        "=SI",
    ]
    negatives = ["Ikaros_02", "_Srbosjek_", "-Saul Goodman-", "-Н_П_Т-", "SpartanWay-", "-HWH-Roselia", "[TD42]Boman14", "we-jiayou"]
    for frag in positives:
        assert is_clan_only_fragment(frag) is True, f"Expected fragment True for {frag}"
    for nick in negatives:
        assert is_clan_only_fragment(nick) is False, f"Expected fragment False for {nick}"

    # Leading underscore / short CJK-artifact compact clan separation
    p_underscored = parse_player_identity("^BH3r^_736624389")
    assert p_underscored.clan_tag == "^BH3r^"
    assert p_underscored.clean_nick == "_736624389"

    p_cjk_art = parse_player_identity("[CRSI]_X")
    assert p_cjk_art.clan_tag == "[CRSI]"
    assert p_cjk_art.clean_nick == "_X"
