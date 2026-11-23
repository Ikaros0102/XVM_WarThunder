"""Battle session state machine: teams (OCR-only), K/D, FPE, Nuke Threat, Enemy Air, Air Alert."""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from collections import deque
from typing import Callable

from rapidfuzz import fuzz

from wt_xvm.config import AppConfig
from wt_xvm.core.log_parser import (
    AirAlertChatEvent,
    DamageEvent,
    DisconnectEvent,
    FireEvent,
    KillEvent,
    ParsedChatEvent,
    ParsedHudEvent,
    SelfDeathEvent,
    SenderSightingChatEvent,
    VehicleSightingEvent,
)
from wt_xvm.core.nick_cleaner import is_clan_only_fragment, parse_player_identity
from wt_xvm.core.vehicle_db import VehicleDB
from wt_xvm.models import (
    AirAlertState,
    EnemyAirSummary,
    OcrStatus,
    PlayerIdentity,
    PlayerState,
    PlayerStatsRecord,
    ProviderStatus,
    SessionSnapshot,
    TeamSide,
)

logger = logging.getLogger("wt_xvm.core.battle_state")

MAX_TEAM_PLAYERS = 16

# Homoglyph normalization map for OCR -> exact log nickname matching
_HOMOGLYPH_MAP = str.maketrans(
    {
        "а": "a",
        "в": "b",
        "е": "e",
        "к": "k",
        "м": "m",
        "н": "h",
        "о": "o",
        "р": "p",
        "с": "c",
        "т": "t",
        "у": "y",
        "х": "x",
        "0": "o",
        "1": "l",
        "і": "i",
        "ё": "e",
    }
)


def _homoglyph_key(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.casefold().translate(_HOMOGLYPH_MAP)


def _has_cjk(text: str) -> bool:
    return any(ord(ch) > 0x2E80 for ch in text)


def _ascii_skeleton(text: str) -> str:
    """Keep only ASCII letters/digits/underscore after homoglyph normalization (strips CJK & punctuation)."""
    norm = _homoglyph_key(text)
    return "".join(ch for ch in norm if ch.isascii() and (ch.isalnum() or ch == "_"))


def _suffix_match_score(a: str, b: str) -> float:
    """Match an unstripped OCR clan-tag+nick string against a clean nick."""
    if not a or not b:
        return 0.0
    longer, shorter = (a, b) if len(a) >= len(b) else (b, a)
    diff = len(longer) - len(shorter)
    if len(shorter) < 4 or diff < 1 or diff > 10:
        return 0.0

    best = 0.0
    boundary = longer[-len(shorter) - 1]
    if not boundary.isalnum():
        r = fuzz.ratio(longer[-len(shorter) :], shorter)
        if r >= 90.0:
            best = max(best, min(96.0, r))
    if " " in longer:
        prefix, rest = longer.split(" ", 1)
        if 1 <= len(prefix) <= 8 and len(rest) >= 4:
            r2 = fuzz.ratio(rest, shorter)
            if r2 >= 90.0:
                best = max(best, min(96.0, r2))
    return best


def _clan_core(text: str | None) -> str:
    """Extract lowercase alphanumeric core of an ASCII/Cyrillic squadron tag."""
    return re.sub(r"[\W_]+", "", text or "").casefold()


_CLAN_FRAGMENT_CORE_RE = re.compile(r"^(?:=([^\s=]{1,7})=|\[([^\s\]]{1,7})\]|\^([^\s\^]{1,7})\^)")


def _extract_clan_fragment_text(text: str) -> str:
    m = _CLAN_FRAGMENT_CORE_RE.match(text)
    if m:
        return m.group(1) or m.group(2) or m.group(3) or text
    return text


def _clan_core_matches(ocr_text: str, clan_tag: str | None) -> bool:
    """Match clan-only Tab OCR placeholder (e.g. '=VAKey=', 'RAG1-', '=Be=', '=NQF') against clan_tag."""
    if not ocr_text or not clan_tag:
        return False
    trimmed = ocr_text.strip()
    if len(trimmed) > 12 or " " in trimmed:
        return False
    c_ocr = _clan_core(_extract_clan_fragment_text(trimmed))
    c_tag = _clan_core(clan_tag)
    if len(c_ocr) < 2 or len(c_tag) < 2:
        return False
    if c_ocr == c_tag:
        return True
    is_delimited = trimmed[0] in "=[^-" or trimmed[-1] in "=]^-"
    if is_delimited and (c_tag.startswith(c_ocr) or c_tag.endswith(c_ocr)):
        return True
    if len(c_ocr) >= 3 and len(c_tag) >= 3 and (c_tag.endswith(c_ocr) or fuzz.ratio(c_ocr, c_tag) >= 85.0):
        return True
    return False


def _matches_merged_or_truncated(ocr_clean: str, ocr_clan: str | None, hud_identity: PlayerIdentity) -> bool:
    """Match OCR 'BofSsCoBeT WnNoH' -> HUD '═BofSs║ ⋇CoBeT WnNoH', truncation, or CJK ASCII skeletons."""
    if hud_identity.clan_tag:
        merged = _homoglyph_key(_clan_core(hud_identity.clan_tag) + hud_identity.canonical_key).replace(" ", "")
        ocr_comp = _homoglyph_key(ocr_clean).replace(" ", "")
        if len(merged) >= 8 and len(ocr_comp) >= 8 and fuzz.ratio(merged, ocr_comp) >= 92.0:
            return True
    if len(ocr_clean) >= 4 and hud_identity.clean_nick.casefold().startswith(ocr_clean.casefold()):
        suffix = hud_identity.clean_nick[len(ocr_clean) :]
        if suffix and (
            (ocr_clan and hud_identity.clan_tag and _clan_core(ocr_clan) == _clan_core(hud_identity.clan_tag))
            or _has_cjk(suffix)
        ):
            return True
    if _has_cjk(hud_identity.clean_nick):
        if ocr_clan and hud_identity.clan_tag and _clan_core_matches(ocr_clan, hud_identity.clan_tag):
            if len(re.sub(r"[\W_]+", "", ocr_clean)) <= 3:
                return True
        clans_conflict = (
            bool(ocr_clan)
            and bool(hud_identity.clan_tag)
            and len(_clan_core(ocr_clan)) >= 3
            and len(_clan_core(hud_identity.clan_tag)) >= 3
            and fuzz.ratio(
                _homoglyph_key(_clan_core(ocr_clan)),
                _homoglyph_key(_clan_core(hud_identity.clan_tag)),
            )
            < 60.0
        )
        if not clans_conflict:
            hud_skel = _ascii_skeleton((hud_identity.clan_tag or "") + hud_identity.clean_nick)
            ocr_skel = _ascii_skeleton((ocr_clan or "") + ocr_clean)
            if len(hud_skel) >= 5 and len(ocr_skel) >= 5:
                shorter, longer = (ocr_skel, hud_skel) if len(ocr_skel) <= len(hud_skel) else (hud_skel, ocr_skel)
                if (
                    fuzz.ratio(hud_skel, ocr_skel) >= 80.0
                    or (len(shorter) >= 0.65 * len(longer) and longer.endswith(shorter))
                ):
                    return True
    return False


def compute_nuke_threat(kills: int, deaths: int) -> bool:
    """Return True when player is ~3 kills away from a nuclear strike."""
    if deaths <= 0:
        return kills >= 8
    if deaths == 1:
        return kills >= 9
    if deaths == 2:
        return kills >= 12
    return kills >= 15


class BattleSessionState:
    """Thread-safe single source of truth for the active War Thunder battle."""

    def __init__(
        self,
        config: AppConfig,
        vehicle_db: VehicleDB,
        stats_enqueue_cb: Callable[[PlayerState], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self.vehicle_db = vehicle_db
        self._stats_enqueue_cb = stats_enqueue_cb
        self._clock = clock or time.time

        self.battle_active: bool = False
        self.wt_connected: bool = False
        self.mock_mode: bool = False

        self.players: dict[str, PlayerState] = {}
        self.air_alert: AirAlertState | None = None
        self.provider_status = ProviderStatus()
        self.ocr_status = OcrStatus()
        self._last_killer_by_victim: dict[str, str] = {}
        self._combat_opponents: dict[str, set[str]] = {}
        self._inferred_team_keys: set[str] = set()

    def set_stats_enqueue_callback(self, cb: Callable[[PlayerState], None] | None) -> None:
        self._stats_enqueue_cb = cb

    def reset_session(self, keep_connection_flags: bool = True) -> None:
        """Clear active match state when returning to hangar or changing maps."""
        self.players.clear()
        self.air_alert = None
        self.ocr_status = OcrStatus()
        self._last_killer_by_victim.clear()
        self._combat_opponents.clear()
        self._inferred_team_keys.clear()
        if not keep_connection_flags:
            self.battle_active = False
            self.mock_mode = False
        logger.info("Battle session state cleared.")

    def clear_stale_hud_data(self) -> None:
        """Remove unassigned previous-battle HUD players and reset combat counters while preserving Tab OCR teams."""
        for key in [k for k, p in self.players.items() if p.team == "unknown"]:
            self.players.pop(key, None)
        for player in self.players.values():
            player.current_vehicle = None
            player.current_vehicle_kind = "unknown"
            player.is_alive = True
            player.kills = 0
            player.deaths = 0
            player.fpe_count = 2
            player.last_fire_time = None
            player.last_death_time = None
            player.last_dead_vehicle = None
            player.disconnected = False
            player.nuke_threat = False
        self.air_alert = None
        self._last_killer_by_victim.clear()
        self._combat_opponents.clear()
        self._inferred_team_keys.clear()
        logger.info("Cleared stale previous-battle HUD data while preserving Tab OCR roster.")

    def _enqueue_stats(self, player: PlayerState) -> None:
        if self._stats_enqueue_cb is not None:
            try:
                self._stats_enqueue_cb(player)
            except Exception as exc:
                logger.warning("Stats enqueue callback failed for %s: %s", player.display_nick, exc)

    def _find_fuzzy_match(
        self,
        candidate_key: str,
        pool: list[PlayerState],
        threshold: float = 85.0,
    ) -> PlayerState | None:
        if not candidate_key or not pool:
            return None
        norm_cand = _homoglyph_key(candidate_key)
        cand_stripped = norm_cand.strip("_ ")
        best_player: PlayerState | None = None
        best_score = 0.0
        for item in pool:
            item_norm = _homoglyph_key(item.canonical_key)
            item_stripped = item_norm.strip("_ ")
            stripped_score = (
                fuzz.ratio(cand_stripped, item_stripped)
                if len(cand_stripped) >= 4 and len(item_stripped) >= 4
                else 0.0
            )
            score = max(
                fuzz.ratio(norm_cand, item_norm),
                fuzz.ratio(norm_cand.replace("_", " "), item_norm.replace("_", " ")),
                fuzz.ratio(norm_cand.replace(" ", ""), item_norm.replace(" ", "")),
                stripped_score,
                _suffix_match_score(norm_cand, item_norm),
            )
            if score >= threshold and score > best_score:
                best_score = score
                best_player = item
        return best_player

    def _prune_matching_placeholders(self, team: TeamSide, clan_tag: str | None) -> None:
        if team == "unknown" or not clan_tag:
            return
        for k in [
            k
            for k, p in self.players.items()
            if p.team == team and p.is_placeholder and _clan_core_matches(p.display_nick, clan_tag)
        ]:
            self.players.pop(k, None)

    def _move_combat_key(self, old_key: str, new_key: str) -> None:
        if old_key == new_key:
            return
        old_neighbors = self._combat_opponents.pop(old_key, None)
        if old_neighbors is not None:
            dest = self._combat_opponents.setdefault(new_key, set())
            for nb in old_neighbors:
                if nb == old_key or nb == new_key:
                    continue
                dest.add(nb)
                nb_set = self._combat_opponents.get(nb)
                if nb_set is not None:
                    nb_set.discard(old_key)
                    nb_set.add(new_key)
        if old_key in self._inferred_team_keys:
            self._inferred_team_keys.discard(old_key)
            self._inferred_team_keys.add(new_key)

    def _assign_inferred_team(self, player: PlayerState, inferred_team: TeamSide) -> bool:
        if player.team != "unknown" or inferred_team not in ("ally", "enemy"):
            return False

        visible = [p for p in self.players.values() if p.team == inferred_team and not p.is_placeholder]
        placeholders = [p for p in self.players.values() if p.team == inferred_team and p.is_placeholder]

        claimed_ph: PlayerState | None = None
        if player.clan_tag:
            c_player = _clan_core(player.clan_tag)
            for ph in placeholders:
                if _clan_core_matches(ph.display_nick, player.clan_tag):
                    claimed_ph = ph
                    break
            if claimed_ph is None and c_player:
                p_delim = player.clan_tag[0] if player.clan_tag[0] in "=[^{-" else ""
                for ph in placeholders:
                    c_ph = _clan_core(_extract_clan_fragment_text(ph.display_nick))
                    ph_delim = ph.display_nick[0] if ph.display_nick and ph.display_nick[0] in "=[^{-" else ""
                    if (
                        c_ph
                        and len(c_ph) <= 2
                        and c_player[0] == c_ph[0]
                        and (not ph_delim or not p_delim or ph_delim == p_delim)
                    ):
                        claimed_ph = ph
                        break

        if claimed_ph is not None:
            self.players.pop(claimed_ph.canonical_key, None)
        elif len(visible) >= MAX_TEAM_PLAYERS:
            ghosts = [
                p
                for p in visible
                if not p.exact_nick_verified
                and p.kills == 0
                and p.deaths == 0
                and p.current_vehicle is None
            ]
            if not ghosts:
                return False
            chosen = next(
                (
                    g
                    for g in ghosts
                    if player.clan_tag and g.clan_tag and _clan_core_matches(g.clan_tag, player.clan_tag)
                ),
                next(
                    (g for g in ghosts if len(_clan_core(g.display_nick)) <= 3),
                    None,
                ),
            )
            if chosen is None:
                return False
            self.players.pop(chosen.canonical_key, None)
            self._combat_opponents.pop(chosen.canonical_key, None)

        player.team = inferred_team
        self._inferred_team_keys.add(player.canonical_key)
        self._prune_matching_placeholders(inferred_team, player.clan_tag)
        return True

    def _propagate_combat_teams(self) -> None:
        queue: deque[str] = deque(
            k
            for k in self._combat_opponents
            if (p := self.players.get(k)) is not None and p.team in ("ally", "enemy") and not p.is_placeholder
        )
        while queue:
            curr_key = queue.popleft()
            curr = self.players.get(curr_key)
            if curr is None or curr.team not in ("ally", "enemy"):
                continue
            opp_team: TeamSide = "enemy" if curr.team == "ally" else "ally"
            for nb_key in list(self._combat_opponents.get(curr_key, ())):
                nb = self.players.get(nb_key)
                if nb is not None and nb.team == "unknown":
                    if self._assign_inferred_team(nb, opp_team):
                        queue.append(nb_key)

    def _record_combat_edge(self, key_a: str, key_b: str) -> None:
        if not key_a or not key_b or key_a == key_b:
            return
        self._combat_opponents.setdefault(key_a, set()).add(key_b)
        self._combat_opponents.setdefault(key_b, set()).add(key_a)
        self._propagate_combat_teams()

    def _rekey_player(self, old_key: str, new_key: str, identity: PlayerIdentity) -> PlayerState:
        player = self.players.pop(old_key)
        player.canonical_key = new_key
        player.display_nick = identity.clean_nick
        if identity.clan_tag is not None:
            player.clan_tag = identity.clan_tag
        if identity.console_prefix is not None:
            player.console_prefix = identity.console_prefix
        if identity.platform_hint is not None and (
            player.platform_hint is None or identity.platform_hint in ("psn", "live")
        ):
            player.platform_hint = identity.platform_hint
        player.exact_nick_verified = True
        player.is_placeholder = False
        self.players[new_key] = player
        self._move_combat_key(old_key, new_key)
        self._prune_matching_placeholders(player.team, player.clan_tag)
        return player

    def _resolve_log_player(self, identity: PlayerIdentity) -> PlayerState:
        """Resolve an identity coming from reliable UTF-8 game logs or chat."""
        key = identity.canonical_key
        existing = self.players.get(key)
        if existing is not None:
            prev_hint = existing.platform_hint
            was_verified = existing.exact_nick_verified
            existing.display_nick = identity.clean_nick
            if identity.clan_tag is not None:
                existing.clan_tag = identity.clan_tag
            if identity.console_prefix is not None:
                existing.console_prefix = identity.console_prefix
            if identity.platform_hint is not None and (
                existing.platform_hint is None or identity.platform_hint in ("psn", "live")
            ):
                existing.platform_hint = identity.platform_hint
            existing.exact_nick_verified = True
            existing.is_placeholder = False
            self._prune_matching_placeholders(existing.team, existing.clan_tag)
            hint_upgraded = (
                existing.platform_hint != prev_hint
                and existing.platform_hint in ("console", "psn", "live")
                and (existing.stats is None or existing.stats.status == "not_found")
            )
            if (not was_verified and existing.stats is None) or hint_upgraded:
                self._enqueue_stats(existing)
            return existing

        # Attempt fuzzy or structural upgrade of an unverified Tab-OCR player
        ocr_candidates = [p for p in self.players.values() if not p.exact_nick_verified]
        regular_ocr = [p for p in ocr_candidates if not p.is_placeholder]
        fuzzy_hit = self._find_fuzzy_match(key, regular_ocr, threshold=85.0)
        if fuzzy_hit is None:
            for p in regular_ocr:
                if _matches_merged_or_truncated(p.display_nick, p.clan_tag, identity):
                    fuzzy_hit = p
                    break
        if fuzzy_hit is None and identity.clan_tag:
            placeholder_matches = [
                p
                for p in ocr_candidates
                if p.is_placeholder and _clan_core_matches(p.display_nick, identity.clan_tag)
            ]
            if placeholder_matches:
                fuzzy_hit = placeholder_matches[0]
            else:
                clan_matches = [
                    p
                    for p in regular_ocr
                    if p.clan_tag is None and _clan_core_matches(p.display_nick, identity.clan_tag)
                ]
                if len(clan_matches) == 1:
                    fuzzy_hit = clan_matches[0]
        if fuzzy_hit is not None:
            upgraded = self._rekey_player(fuzzy_hit.canonical_key, key, identity)
            upgraded.stats = None
            self._enqueue_stats(upgraded)
            return upgraded

        # New player observed in log before Tab scan or combat-graph inference -> starts as "unknown"
        created = PlayerState(
            canonical_key=key,
            display_nick=identity.clean_nick,
            clan_tag=identity.clan_tag,
            console_prefix=identity.console_prefix,
            platform_hint=identity.platform_hint,
            team="unknown",
            exact_nick_verified=True,
        )
        self.players[key] = created
        self._enqueue_stats(created)
        return created

    def apply_tab_ocr(self, ally_raw_nicks: list[str], enemy_raw_nicks: list[str]) -> tuple[int, int]:
        """Merge Tab scoreboard columns into session state without overwriting collected match data.

        Tab OCR is the primary source of truth for team assignment (`ally` / `enemy`).
        """
        added_allies = 0
        added_enemies = 0

        for team, raw_list in (("ally", ally_raw_nicks), ("enemy", enemy_raw_nicks)):
            for raw in raw_list:
                identity = parse_player_identity(raw)
                key = identity.canonical_key
                if not key:
                    continue

                placeholder = (identity.clan_tag is None) and is_clan_only_fragment(raw)
                existing = self.players.get(key)
                if existing is not None:
                    if existing.team == "unknown" or (
                        existing.canonical_key in self._inferred_team_keys and existing.team != team
                    ):
                        existing.team = team  # type: ignore[assignment]
                        self._inferred_team_keys.discard(existing.canonical_key)
                        if not existing.is_placeholder:
                            self._prune_matching_placeholders(team, existing.clan_tag)  # type: ignore[arg-type]
                            if team == "ally":
                                added_allies += 1
                            else:
                                added_enemies += 1
                    continue

                same_team_pool = [p for p in self.players.values() if p.team == team]
                unknown_pool = [p for p in self.players.values() if p.team == "unknown"]

                if placeholder:
                    # First try assigning any waiting "unknown" log player matching this clan tag
                    clan_unknown = [
                        p
                        for p in unknown_pool
                        if p.clan_tag and _clan_core_matches(raw, p.clan_tag)
                    ]
                    if len(clan_unknown) == 1:
                        clan_unknown[0].team = team  # type: ignore[assignment]
                        self._inferred_team_keys.discard(clan_unknown[0].canonical_key)
                        if team == "ally":
                            added_allies += 1
                        else:
                            added_enemies += 1
                        continue
                    c_raw_ph = _clan_core(_extract_clan_fragment_text(raw))
                    # Avoid creating duplicate hidden placeholders for variations like '=NQF' vs '=CNQF=' or '=S=' vs '=SI'
                    if any(
                        (p.clan_tag and _clan_core_matches(raw, p.clan_tag))
                        or (
                            p.is_placeholder
                            and (
                                _clan_core_matches(raw, p.display_nick)
                                or (
                                    c_raw_ph
                                    and (c_existing_ph := _clan_core(_extract_clan_fragment_text(p.display_nick)))
                                    and (c_raw_ph.startswith(c_existing_ph) or c_existing_ph.startswith(c_raw_ph))
                                )
                            )
                        )
                        for p in same_team_pool
                    ):
                        continue
                    created_ph = PlayerState(
                        canonical_key=key,
                        display_nick=identity.clean_nick,
                        clan_tag=identity.clan_tag,
                        console_prefix=identity.console_prefix,
                        platform_hint=identity.platform_hint,
                        team=team,  # type: ignore[arg-type]
                        exact_nick_verified=False,
                        is_placeholder=True,
                    )
                    self.players[key] = created_ph
                    continue

                # Avoid creating duplicate rows on repeated Tab scans when OCR varies slightly
                regular_same_team = [p for p in same_team_pool if not p.is_placeholder]
                if self._find_fuzzy_match(key, regular_same_team, threshold=85.0) is not None:
                    continue
                if any(
                    _matches_merged_or_truncated(
                        identity.clean_nick,
                        identity.clan_tag,
                        PlayerIdentity(
                            raw=p.display_nick,
                            clan_tag=p.clan_tag,
                            clean_nick=p.display_nick,
                            canonical_key=p.canonical_key,
                        ),
                    )
                    for p in regular_same_team
                ):
                    continue
                if identity.clan_tag is None and any(
                    _clan_core_matches(identity.clean_nick, p.clan_tag or (p.display_nick if p.is_placeholder else None))
                    for p in same_team_pool
                ):
                    continue
                if len(regular_same_team) >= MAX_TEAM_PLAYERS and all(
                    p.exact_nick_verified for p in regular_same_team
                ):
                    continue

                # Check if this OCR nick matches an already-seen "unknown" exact log player
                fuzzy_hit = self._find_fuzzy_match(key, unknown_pool, threshold=85.0)
                if fuzzy_hit is None:
                    for p in unknown_pool:
                        log_ident = PlayerIdentity(
                            raw=p.display_nick,
                            clan_tag=p.clan_tag,
                            clean_nick=p.display_nick,
                            canonical_key=p.canonical_key,
                        )
                        if _matches_merged_or_truncated(identity.clean_nick, identity.clan_tag, log_ident):
                            fuzzy_hit = p
                            break
                if fuzzy_hit is None and identity.clan_tag is None:
                    clan_unknown = [
                        p
                        for p in unknown_pool
                        if p.clan_tag and _clan_core_matches(identity.clean_nick, p.clan_tag)
                    ]
                    if len(clan_unknown) == 1:
                        fuzzy_hit = clan_unknown[0]
                if fuzzy_hit is not None:
                    fuzzy_hit.team = team  # type: ignore[assignment]
                    self._inferred_team_keys.discard(fuzzy_hit.canonical_key)
                    self._prune_matching_placeholders(team, fuzzy_hit.clan_tag)  # type: ignore[arg-type]
                    if team == "ally":
                        added_allies += 1
                    else:
                        added_enemies += 1
                    continue

                # Brand-new player discovered via Tab OCR
                created = PlayerState(
                    canonical_key=key,
                    display_nick=identity.clean_nick,
                    clan_tag=identity.clan_tag,
                    console_prefix=identity.console_prefix,
                    platform_hint=identity.platform_hint,
                    team=team,  # type: ignore[arg-type]
                    exact_nick_verified=False,
                    is_placeholder=False,
                )
                self.players[key] = created
                self._enqueue_stats(created)
                if team == "ally":
                    added_allies += 1
                else:
                    added_enemies += 1

        self._propagate_combat_teams()
        self.ocr_status.last_scan_at = self._clock()
        self.ocr_status.last_allies_count = sum(
            1 for p in self.players.values() if p.team == "ally" and not p.is_placeholder
        )
        self.ocr_status.last_enemies_count = sum(
            1 for p in self.players.values() if p.team == "enemy" and not p.is_placeholder
        )
        return added_allies, added_enemies

    def _set_vehicle(self, player: PlayerState, vehicle: str) -> None:
        kind = self.vehicle_db.classify(vehicle)
        if kind == "recon_drone":
            return
        player.current_vehicle = vehicle
        player.current_vehicle_kind = kind

    def apply_hud_event(self, event: ParsedHudEvent) -> None:
        """Apply a parsed /hudmsg combat event to session state."""
        if isinstance(event, KillEvent):
            attacker = self._resolve_log_player(event.attacker)
            attacker.disconnected = False
            attacker.is_alive = True
            self._set_vehicle(attacker, event.attacker_vehicle)

            victim_kind = self.vehicle_db.classify(event.victim_vehicle)
            if victim_kind == "recon_drone":
                # Destroying a scout UAV does NOT count as a player kill/death or erase real vehicle
                return

            victim = self._resolve_log_player(event.victim)
            self._record_combat_edge(attacker.canonical_key, victim.canonical_key)
            if (
                not victim.is_alive
                and victim.last_death_time is not None
                and abs(event.timestamp - victim.last_death_time) <= 2.0
                and victim.last_dead_vehicle == event.victim_vehicle
                and self._last_killer_by_victim.get(victim.canonical_key) == attacker.canonical_key
            ):
                return

            attacker.kills += 1
            attacker.nuke_threat = compute_nuke_threat(attacker.kills, attacker.deaths)

            victim.deaths += 1
            victim.is_alive = False
            victim.last_death_time = event.timestamp
            victim.last_dead_vehicle = event.victim_vehicle
            victim.current_vehicle = None
            victim.current_vehicle_kind = "unknown"
            victim.fpe_count = 2
            victim.last_fire_time = None
            victim.nuke_threat = compute_nuke_threat(victim.kills, victim.deaths)
            self._last_killer_by_victim[victim.canonical_key] = attacker.canonical_key
            return

        if isinstance(event, FireEvent):
            attacker = self._resolve_log_player(event.attacker)
            attacker.disconnected = False
            attacker.is_alive = True
            self._set_vehicle(attacker, event.attacker_vehicle)

            victim_kind = self.vehicle_db.classify(event.victim_vehicle)
            if victim_kind == "recon_drone":
                return

            victim = self._resolve_log_player(event.victim)
            self._record_combat_edge(attacker.canonical_key, victim.canonical_key)
            # Post-mortem fire guard (e.g., shot down + set afire in same second)
            if (
                not victim.is_alive
                and victim.last_death_time is not None
                and (event.timestamp - victim.last_death_time) <= 3.0
                and victim.last_dead_vehicle == event.victim_vehicle
            ):
                return

            victim.disconnected = False
            victim.is_alive = True
            self._set_vehicle(victim, event.victim_vehicle)

            if victim.last_fire_time is None or (event.timestamp - victim.last_fire_time) >= 6.0:
                victim.fpe_count = max(0, victim.fpe_count - 1)
                victim.last_fire_time = event.timestamp
            return

        if isinstance(event, DamageEvent):
            attacker = self._resolve_log_player(event.attacker)
            attacker.disconnected = False
            attacker.is_alive = True
            self._set_vehicle(attacker, event.attacker_vehicle)

            victim_kind = self.vehicle_db.classify(event.victim_vehicle)
            if victim_kind != "recon_drone":
                victim = self._resolve_log_player(event.victim)
                self._record_combat_edge(attacker.canonical_key, victim.canonical_key)
                victim.disconnected = False
                victim.is_alive = True
                self._set_vehicle(victim, event.victim_vehicle)
            return

        if isinstance(event, VehicleSightingEvent):
            actor = self._resolve_log_player(event.actor)
            actor.disconnected = False
            actor.is_alive = True
            self._set_vehicle(actor, event.vehicle)
            return

        if isinstance(event, SelfDeathEvent):
            actor_kind = self.vehicle_db.classify(event.vehicle)
            if actor_kind == "recon_drone":
                return
            actor = self._resolve_log_player(event.actor)
            if (
                not actor.is_alive
                and actor.last_death_time is not None
                and abs(event.timestamp - actor.last_death_time) <= 5.0
                and actor.last_dead_vehicle == event.vehicle
            ):
                return
            actor.deaths += 1
            actor.is_alive = False
            actor.last_death_time = event.timestamp
            actor.last_dead_vehicle = event.vehicle
            actor.current_vehicle = None
            actor.current_vehicle_kind = "unknown"
            actor.fpe_count = 2
            actor.last_fire_time = None
            actor.nuke_threat = compute_nuke_threat(actor.kills, actor.deaths)
            return

        if isinstance(event, DisconnectEvent):
            actor = self._resolve_log_player(event.actor)
            actor.disconnected = True
            actor.is_alive = False
            actor.current_vehicle = None
            actor.current_vehicle_kind = "unknown"

    def apply_chat_event(self, event: ParsedChatEvent) -> None:
        """Apply parsed /gamechat event (platform hint enrichment & Air Alert timer)."""
        if isinstance(event, SenderSightingChatEvent):
            # Enrich existing player platform hint if already present; don't inject noise players
            existing = self.players.get(event.sender.canonical_key)
            if existing is not None and event.sender.platform_hint in ("psn", "live"):
                if existing.platform_hint != event.sender.platform_hint:
                    existing.platform_hint = event.sender.platform_hint
                    if existing.stats is None or existing.stats.status == "not_found":
                        self._enqueue_stats(existing)
            return

        if isinstance(event, AirAlertChatEvent):
            existing = self.players.get(event.sender.canonical_key)
            if existing is not None and event.sender.platform_hint in ("psn", "live"):
                existing.platform_hint = event.sender.platform_hint

            if not self.config.air_alert_enabled:
                return
            now = self._clock()
            duration = max(1, min(15, int(self.config.air_alert_duration_sec)))
            self.air_alert = AirAlertState(
                azimuth=event.azimuth,
                altitude=event.altitude,
                sender=event.sender.clean_nick,
                triggered_at=now,
                expires_at=now + duration,
            )

    def apply_stats_result(self, record: PlayerStatsRecord) -> None:
        """Attach resolved statistics record to the matching player."""
        player = self.players.get(record.canonical_key)
        if player is None:
            return
        player.stats = record
        if (
            record.status == "ok"
            and not player.exact_nick_verified
            and " " in player.display_nick
        ):
            prefix, rest = player.display_nick.split(" ", 1)
            resolved_base = re.sub(r"@(psn|live)$", "", record.nick.strip(), flags=re.IGNORECASE)
            if rest.strip() and rest.strip().lower() == resolved_base.lower():
                if not player.clan_tag:
                    player.clan_tag = prefix.strip()
                player.display_nick = rest.strip()

    def purge_expired_air_alert(self) -> bool:
        """Clear air_alert once expired. Returns True if cleared."""
        if self.air_alert is not None and self._clock() >= self.air_alert.expires_at:
            self.air_alert = None
            return True
        return False

    def compute_enemy_air(self) -> EnemyAirSummary:
        """Derive active enemy planes and helicopters from current living enemy vehicles."""
        planes: list[str] = []
        helicopters: list[str] = []
        for p in self.players.values():
            if p.team != "enemy" or not p.is_alive or p.disconnected or not p.current_vehicle:
                continue
            if p.current_vehicle_kind == "plane":
                planes.append(p.current_vehicle)
            elif p.current_vehicle_kind == "helicopter":
                helicopters.append(p.current_vehicle)
        return EnemyAirSummary(
            planes=len(planes),
            helicopters=len(helicopters),
            plane_list=planes,
            helicopter_list=helicopters,
        )

    def snapshot(self, ocr_cooldown_remaining: float = 0.0) -> SessionSnapshot:
        """Build an immutable JSON-ready snapshot for WebSocket and REST consumers."""
        self.purge_expired_air_alert()
        self.ocr_status.cooldown_remaining = max(0.0, round(ocr_cooldown_remaining, 1))

        allies = [p for p in self.players.values() if p.team == "ally" and not p.is_placeholder]
        enemies = [p for p in self.players.values() if p.team == "enemy" and not p.is_placeholder]
        unassigned_count = sum(1 for p in self.players.values() if p.team == "unknown")

        # Keep deterministic sort: active first, then by kills desc, then nick
        sort_key = lambda p: (p.disconnected, -p.kills, p.display_nick.casefold())
        allies.sort(key=sort_key)
        enemies.sort(key=sort_key)

        return SessionSnapshot(
            battle_active=self.battle_active,
            wt_connected=self.wt_connected,
            mock_mode=self.mock_mode,
            battle_mode=self.config.battle_mode,
            allies=allies,
            enemies=enemies,
            unassigned_count=unassigned_count,
            air_alert=self.air_alert if self.config.air_alert_enabled else None,
            enemy_air=self.compute_enemy_air(),
            provider_status=self.provider_status.model_copy(),
            ocr_status=self.ocr_status.model_copy(),
        )
