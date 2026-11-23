"""Externalized RU/EN log and chat parser using locales.json and balanced-parenthesis matching."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from wt_xvm.core.nick_cleaner import parse_player_identity
from wt_xvm.models import PlayerIdentity
from wt_xvm.paths import ensure_runtime_file

logger = logging.getLogger("wt_xvm.core.log_parser")


@dataclass(slots=True)
class KillEvent:
    timestamp: float
    attacker: PlayerIdentity
    attacker_vehicle: str
    victim: PlayerIdentity
    victim_vehicle: str


@dataclass(slots=True)
class FireEvent:
    timestamp: float
    attacker: PlayerIdentity
    attacker_vehicle: str
    victim: PlayerIdentity
    victim_vehicle: str


@dataclass(slots=True)
class DamageEvent:
    timestamp: float
    attacker: PlayerIdentity
    attacker_vehicle: str
    victim: PlayerIdentity
    victim_vehicle: str


@dataclass(slots=True)
class VehicleSightingEvent:
    timestamp: float
    actor: PlayerIdentity
    vehicle: str


@dataclass(slots=True)
class SelfDeathEvent:
    timestamp: float
    actor: PlayerIdentity
    vehicle: str


@dataclass(slots=True)
class DisconnectEvent:
    timestamp: float
    actor: PlayerIdentity


@dataclass(slots=True)
class AirAlertChatEvent:
    timestamp: float
    sender: PlayerIdentity
    azimuth: int
    altitude: int


@dataclass(slots=True)
class SenderSightingChatEvent:
    timestamp: float
    sender: PlayerIdentity


ParsedHudEvent = (
    KillEvent
    | FireEvent
    | DamageEvent
    | VehicleSightingEvent
    | SelfDeathEvent
    | DisconnectEvent
)

ParsedChatEvent = AirAlertChatEvent | SenderSightingChatEvent

_MD_LOG_LINE_RE = re.compile(r"^(\d+):(\d{2})(.+)$")
_MD_CHAT_LINE_RE = re.compile(r"^(\d+):(\d{2})\[([^\]]+)\]\s*([^:]+):\s*(.+)$")
_NON_PLAYER_SUBUNIT_VEHICLES = {"соу", "соц", "пу", "рлс", "tel", "tar", "tads"}


def extract_actor(segment: str) -> tuple[PlayerIdentity, str] | None:
    """Extract (PlayerIdentity, vehicle_name) using right-to-left balanced parenthesis scanning.

    Handles nested parentheses in vehicle names like `SHADOW- (Type 16 (P))` or `[1HACK] vovka716 (AMV (BMP-3))`.
    """
    text = segment.strip()
    if not text.endswith(")"):
        return None

    depth = 0
    open_idx = -1
    close_idx = len(text) - 1
    for i in range(close_idx, -1, -1):
        ch = text[i]
        if ch == ")":
            depth += 1
        elif ch == "(":
            depth -= 1
            if depth == 0:
                open_idx = i
                break

    if open_idx <= 0 or depth != 0:
        return None

    player_raw = text[:open_idx].strip()
    vehicle_raw = text[open_idx + 1 : close_idx].strip()
    if not player_raw or not vehicle_raw:
        return None

    if vehicle_raw.casefold() in _NON_PLAYER_SUBUNIT_VEHICLES:
        return None

    identity = parse_player_identity(player_raw)
    if not identity.canonical_key:
        return None

    return identity, vehicle_raw


class LogParser:
    """Config-driven parser for War Thunder /hudmsg and /gamechat messages."""

    def __init__(self, locales_path: Path | None = None) -> None:
        self.locales_path = locales_path or ensure_runtime_file("data/locales.json")
        self.locales: dict[str, dict] = {}
        self._compiled_air_patterns: dict[str, list[re.Pattern[str]]] = {}
        self.reload()

    def reload(self) -> None:
        """Load locales.json from disk."""
        data = json.loads(self.locales_path.read_text(encoding="utf-8"))
        self.locales = data
        self._compiled_air_patterns = {}
        for lang in ("ru", "en"):
            lang_cfg = data.get(lang, {})
            patterns = []
            for raw_pat in lang_cfg.get("air_alert_patterns", []):
                try:
                    patterns.append(re.compile(raw_pat, re.IGNORECASE))
                except re.error as exc:
                    logger.error("Invalid air alert regex '%s' in locale %s: %s", raw_pat, lang, exc)
            self._compiled_air_patterns[lang] = patterns

    def _active_langs(self, language: Literal["auto", "ru", "en"]) -> tuple[str, ...]:
        if language in ("ru", "en"):
            return (language,)
        return ("ru", "en")

    def parse_hud_message(
        self,
        msg: str,
        timestamp: float = 0.0,
        language: Literal["auto", "ru", "en"] = "auto",
    ) -> ParsedHudEvent | None:
        """Parse a single /hudmsg message string into a structured combat event."""
        text = (msg or "").strip()
        if not text:
            return None

        universal = self.locales.get("universal", {})
        disconnect_token = universal.get("disconnect_token", "")
        if disconnect_token and disconnect_token in text:
            raw_nick = text.split(disconnect_token, 1)[0].strip()
            if raw_nick:
                identity = parse_player_identity(raw_nick)
                if identity.canonical_key:
                    return DisconnectEvent(timestamp=timestamp, actor=identity)
            return None

        langs = self._active_langs(language)

        # Ignore achievements, medals, first strike, etc.
        for lang in langs:
            for phrase in self.locales.get(lang, {}).get("ignore_phrases", []):
                if phrase in text:
                    return None

        # Localized disconnect messages
        for lang in langs:
            for phrase in self.locales.get(lang, {}).get("disconnect_phrases", []):
                marker = f" {phrase}"
                if text.endswith(marker):
                    raw_nick = text[: -len(marker)].strip()
                    identity = parse_player_identity(raw_nick)
                    if identity.canonical_key:
                        return DisconnectEvent(timestamp=timestamp, actor=identity)
                    return None

        # Binary combat events: kill, fire, damage
        weapon_prefixes = tuple(universal.get("weapon_prefixes", []))
        ai_prefixes = tuple(
            pref
            for lang in langs
            for pref in self.locales.get(lang, {}).get("ai_prefixes", [])
        )

        for lang in langs:
            lang_cfg = self.locales.get(lang, {})
            verb_groups = (
                ("kill", lang_cfg.get("kill_verbs", [])),
                ("fire", lang_cfg.get("fire_verbs", [])),
                ("damage", lang_cfg.get("damage_verbs", [])),
            )
            for kind, verbs in verb_groups:
                for verb in verbs:
                    sep = f") {verb} "
                    start_search = 0
                    while True:
                        idx = text.find(sep, start_search)
                        if idx == -1:
                            break
                        lhs = text[: idx + 1].strip()
                        rhs = text[idx + len(sep) :].strip()
                        attacker_parsed = extract_actor(lhs)
                        if attacker_parsed is not None:
                            attacker, attacker_veh = attacker_parsed
                            if rhs.startswith(ai_prefixes) or rhs.startswith(weapon_prefixes):
                                return VehicleSightingEvent(
                                    timestamp=timestamp,
                                    actor=attacker,
                                    vehicle=attacker_veh,
                                )
                            victim_parsed = extract_actor(rhs)
                            if victim_parsed is None:
                                return VehicleSightingEvent(
                                    timestamp=timestamp,
                                    actor=attacker,
                                    vehicle=attacker_veh,
                                )
                            victim, victim_veh = victim_parsed
                            if kind == "kill":
                                return KillEvent(
                                    timestamp=timestamp,
                                    attacker=attacker,
                                    attacker_vehicle=attacker_veh,
                                    victim=victim,
                                    victim_vehicle=victim_veh,
                                )
                            if kind == "fire":
                                return FireEvent(
                                    timestamp=timestamp,
                                    attacker=attacker,
                                    attacker_vehicle=attacker_veh,
                                    victim=victim,
                                    victim_vehicle=victim_veh,
                                )
                            return DamageEvent(
                                timestamp=timestamp,
                                attacker=attacker,
                                attacker_vehicle=attacker_veh,
                                victim=victim,
                                victim_vehicle=victim_veh,
                            )
                        start_search = idx + 1

        # Unary self-death events (e.g., выведен из строя, has crashed.)
        stripped_dot = text.rstrip(".")
        for lang in langs:
            for verb in self.locales.get(lang, {}).get("self_death_verbs", []):
                suffix = f") {verb}"
                if stripped_dot.endswith(suffix):
                    lhs = stripped_dot[: -len(verb)].strip()
                    actor_parsed = extract_actor(lhs)
                    if actor_parsed is not None:
                        actor, veh = actor_parsed
                        return SelfDeathEvent(timestamp=timestamp, actor=actor, vehicle=veh)

        return None

    def parse_chat_message(
        self,
        msg: str,
        sender: str = "",
        mode: str = "",
        enemy: bool = False,
        timestamp: float = 0.0,
        language: Literal["auto", "ru", "en"] = "auto",
    ) -> ParsedChatEvent | None:
        """Parse a /gamechat entry for Air Alert (requiring BOTH azimuth & altitude) and sender hints."""
        sender_id = parse_player_identity(sender) if sender.strip() else None
        text = (msg or "").strip()
        if not text:
            return None

        if enemy:
            return SenderSightingChatEvent(timestamp=timestamp, sender=sender_id) if sender_id else None

        langs = self._active_langs(language)
        if mode.strip():
            allowed_modes = {
                m.casefold()
                for lang in langs
                for m in self.locales.get(lang, {}).get("friendly_chat_modes", [])
            }
            if mode.strip().casefold() not in allowed_modes:
                return SenderSightingChatEvent(timestamp=timestamp, sender=sender_id) if sender_id else None

        for lang in langs:
            for pat in self._compiled_air_patterns.get(lang, []):
                m = pat.search(text)
                if m:
                    azimuth = int(m.group(1)) % 360
                    altitude = int(m.group(2))
                    fallback_sender = sender_id or PlayerIdentity(raw="Ally", clean_nick="Ally", canonical_key="ally")
                    return AirAlertChatEvent(
                        timestamp=timestamp,
                        sender=fallback_sender,
                        azimuth=azimuth,
                        altitude=altitude,
                    )

        if sender_id and sender_id.canonical_key:
            return SenderSightingChatEvent(timestamp=timestamp, sender=sender_id)
        return None


def parse_md_log_lines(content: str) -> list[tuple[float, str]]:
    """Parse exported M:SS<message> lines from sample log markdown files."""
    entries: list[tuple[float, str]] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _MD_LOG_LINE_RE.match(line)
        if m:
            minutes, seconds, msg = m.groups()
            ts = int(minutes) * 60 + int(seconds)
            entries.append((float(ts), msg.strip()))
    return entries


def parse_md_chat_lines(content: str) -> list[tuple[float, str, str, str]]:
    """Parse exported M:SS[Mode] Sender: Msg lines from sample chat markdown files."""
    entries: list[tuple[float, str, str, str]] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _MD_CHAT_LINE_RE.match(line)
        if m:
            minutes, seconds, mode, sender, msg = m.groups()
            ts = int(minutes) * 60 + int(seconds)
            entries.append((float(ts), mode.strip(), sender.strip(), msg.strip()))
    return entries
