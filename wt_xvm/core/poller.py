"""Asynchronous poller for War Thunder's official local JSON API (http://localhost:8111)."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import fields, is_dataclass
from typing import TYPE_CHECKING, Any, Callable

import httpx

from wt_xvm.config import AppConfig
from wt_xvm.core.battle_state import BattleSessionState
from wt_xvm.core.log_parser import LogParser

if TYPE_CHECKING:
    from wt_xvm.core.debug_recorder import DebugSessionRecorder

logger = logging.getLogger("wt_xvm.core.poller")


def _summarize_event(ev: Any) -> dict[str, Any] | None:
    if ev is None:
        return None
    if is_dataclass(ev):
        raw = {f.name: getattr(ev, f.name) for f in fields(ev)}
        for k, v in list(raw.items()):
            if hasattr(v, "model_dump"):
                d = v.model_dump()
                raw[k] = {
                    "clean_nick": d.get("clean_nick"),
                    "clan_tag": d.get("clan_tag"),
                    "canonical_key": d.get("canonical_key"),
                }
        return raw
    return None


def _split_current_battle_items(
    items: list[dict[str, Any]],
    prev_max_time: float | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Split /hudmsg items at backward match-timer jumps (previous match vs current match)."""
    running_time = prev_max_time
    cut_idx = 0
    crossed_battle_boundary = False
    for i, item in enumerate(items):
        raw_time = item.get("time")
        if raw_time is None:
            continue
        t = float(raw_time)
        if running_time is not None and t < running_time - 10.0:
            cut_idx = i
            if prev_max_time is not None:
                crossed_battle_boundary = True
        running_time = t
    return items[:cut_idx], items[cut_idx:], crossed_battle_boundary


class WarThunderPoller:
    """Polls /map_info.json, /hudmsg, and /gamechat incrementally without crashing when offline."""

    def __init__(
        self,
        config: AppConfig,
        state: BattleSessionState,
        parser: LogParser,
        on_change: Callable[[], None] | None = None,
        on_session_reset: Callable[[], None] | None = None,
        debug_recorder: DebugSessionRecorder | None = None,
        invalid_reset_grace_sec: float = 3.0,
        mono_clock: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self.state = state
        self.parser = parser
        self.on_change = on_change
        self.on_session_reset = on_session_reset
        self.debug_recorder = debug_recorder
        self.invalid_reset_grace_sec = invalid_reset_grace_sec
        self._mono = mono_clock or time.monotonic

        self.last_evt: int = 0
        self.last_dmg: int = 0
        self.last_chat_id: int = 0
        self._last_map_generation: int | None = None
        self._last_map_valid: bool | None = None
        self._invalid_since_mono: float | None = None
        self._max_seen_dmg_time: float | None = None
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def reset_cursors(self) -> None:
        self.last_evt = 0
        self.last_dmg = 0
        self.last_chat_id = 0

    def _reset_match_session(self, reset_cursors: bool = False) -> None:
        self.state.reset_session()
        if reset_cursors:
            self._max_seen_dmg_time = None
            self.reset_cursors()
        if self.on_session_reset is not None:
            try:
                self.on_session_reset()
            except Exception as exc:
                logger.debug("Poller on_session_reset callback error: %s", exc)

    async def _drain_cursors_in_hangar(self, client: httpx.AsyncClient, base_url: str) -> None:
        """Advance /hudmsg and /gamechat cursors while idle in hangar so finished-match ring buffers do not bleed into the next battle."""
        try:
            hud_resp = await client.get(
                f"{base_url}/hudmsg",
                params={"lastEvt": self.last_evt, "lastDmg": self.last_dmg},
            )
            if hud_resp.status_code == 200:
                hud_data = hud_resp.json()
                evt_items = hud_data.get("events", [])
                if evt_items:
                    max_evt_id = max(int(it.get("id", 0)) for it in evt_items)
                    if max_evt_id < self.last_evt:
                        self.last_evt = 0
                    for item in evt_items:
                        item_id = int(item.get("id", 0))
                        if item_id > self.last_evt:
                            self.last_evt = item_id
                dmg_items = hud_data.get("damage", [])
                if dmg_items:
                    max_dmg_id = max(int(it.get("id", 0)) for it in dmg_items)
                    if max_dmg_id < self.last_dmg:
                        self.last_dmg = 0
                    for item in dmg_items:
                        item_id = int(item.get("id", 0))
                        if item_id > self.last_dmg:
                            self.last_dmg = item_id
                        if item.get("time") is not None:
                            self._max_seen_dmg_time = float(item["time"])
        except Exception as exc:
            logger.debug("Error draining /hudmsg in hangar: %s", exc)

        try:
            chat_resp = await client.get(
                f"{base_url}/gamechat",
                params={"lastId": self.last_chat_id},
            )
            if chat_resp.status_code == 200:
                chat_items = chat_resp.json()
                if isinstance(chat_items, list) and chat_items:
                    max_chat_id = max(int(it.get("id", 0)) for it in chat_items)
                    if max_chat_id < self.last_chat_id:
                        self.last_chat_id = 0
                    for item in chat_items:
                        item_id = int(item.get("id", 0))
                        if item_id > self.last_chat_id:
                            self.last_chat_id = item_id
        except Exception as exc:
            logger.debug("Error draining /gamechat in hangar: %s", exc)

    def _notify(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception as exc:
                logger.debug("Poller on_change callback error: %s", exc)

    async def poll_once(self, client: httpx.AsyncClient) -> bool:
        """Run a single poll cycle. Returns True if game API responded."""
        if self.state.mock_mode:
            return False

        base_url = self.config.wt_api_url.rstrip("/")
        try:
            map_resp = await client.get(f"{base_url}/map_info.json")
            map_resp.raise_for_status()
            map_data = map_resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            if self.state.mock_mode:
                return False
            was_connected = self.state.wt_connected
            was_active = self.state.battle_active
            self.state.wt_connected = False
            is_connect_error = isinstance(exc, httpx.ConnectError)
            if was_active:
                now_mono = self._mono()
                if self._invalid_since_mono is None:
                    self._invalid_since_mono = now_mono
                if (now_mono - self._invalid_since_mono) >= self.invalid_reset_grace_sec:
                    self.state.battle_active = False
                    self._last_map_generation = None
                    self._last_map_valid = None
                    self._invalid_since_mono = None
                    self._reset_match_session(reset_cursors=is_connect_error)
            else:
                self._invalid_since_mono = None
                if was_connected and is_connect_error:
                    self._last_map_generation = None
                    self._last_map_valid = None
                    self._max_seen_dmg_time = None
                    self.reset_cursors()
            if was_connected or was_active:
                self._notify()
            return False

        if self.state.mock_mode:
            return False

        was_connected = self.state.wt_connected
        self.state.wt_connected = True
        valid = bool(map_data.get("valid", False))
        map_gen = map_data.get("map_generation")

        # Handle sustained hangar transition with grace window (prevents vehicle-swap flicker resets)
        if not valid:
            action = "hangar_idle"
            if self.state.battle_active:
                now_mono = self._mono()
                if self._invalid_since_mono is None:
                    self._invalid_since_mono = now_mono
                if (now_mono - self._invalid_since_mono) >= self.invalid_reset_grace_sec:
                    self.state.battle_active = False
                    self._reset_match_session(reset_cursors=False)
                    self._last_map_generation = None
                    self._invalid_since_mono = None
                    action = "reset_hangar"
                    self._notify()
                else:
                    action = "invalid_grace_wait"
            elif not was_connected:
                self._notify()

            if (
                self.debug_recorder is not None
                and self.config.debug_mode
                and (valid != self._last_map_valid or map_gen != self._last_map_generation or action == "reset_hangar")
            ):
                self.debug_recorder.record_map_info(map_data, valid, map_gen, action)
            self._last_map_valid = valid
            if not self.state.battle_active:
                await self._drain_cursors_in_hangar(client, base_url)
                self._last_map_generation = None
            return True

        self._invalid_since_mono = None
        changed = False
        action = "steady"
        if not self.state.battle_active:
            self._reset_match_session(reset_cursors=False)
            self.state.battle_active = True
            changed = True
            action = "enter_battle"
        elif (
            self._last_map_generation is not None
            and map_gen is not None
            and map_gen != self._last_map_generation
        ):
            # Tank -> plane switches swap tactical map and increment map_generation mid-battle; keep session!
            action = "map_generation_changed_kept_session"

        if (
            self.debug_recorder is not None
            and self.config.debug_mode
            and (valid != self._last_map_valid or map_gen != self._last_map_generation or action != "steady")
        ):
            self.debug_recorder.record_map_info(map_data, valid, map_gen, action)

        self._last_map_valid = valid
        self._last_map_generation = map_gen

        # Incremental /hudmsg polling
        # Apply `damage` only (full match feed). `events` duplicates local player kills/deaths and would double K/D.
        try:
            hud_resp = await client.get(
                f"{base_url}/hudmsg",
                params={"lastEvt": self.last_evt, "lastDmg": self.last_dmg},
            )
            if hud_resp.status_code == 200:
                hud_data = hud_resp.json()
                events_items = hud_data.get("events", [])
                if events_items:
                    max_evt_id = max(int(it.get("id", 0)) for it in events_items)
                    if max_evt_id < self.last_evt:
                        self.last_evt = 0
                for item in events_items:
                    item_id = int(item.get("id", 0))
                    if item_id > self.last_evt:
                        self.last_evt = item_id
                    msg = str(item.get("msg", ""))
                    if msg and self.debug_recorder is not None and self.config.debug_mode:
                        ev_ts = float(item["time"]) if item.get("time") is not None else time.time()
                        ev = self.parser.parse_hud_message(
                            msg, timestamp=ev_ts, language=self.config.log_language
                        )
                        self.debug_recorder.record_hud_item(
                            source="events",
                            item=item,
                            parsed_type=type(ev).__name__ if ev is not None else None,
                            parsed_summary=_summarize_event(ev),
                            applied=False,
                            note="events_not_applied_to_avoid_local_kd_doubling",
                        )

                damage_items = hud_data.get("damage", [])
                if damage_items:
                    max_dmg_id = max(int(it.get("id", 0)) for it in damage_items)
                    if max_dmg_id < self.last_dmg:
                        self.last_dmg = 0
                stale_items, active_items, crossed_boundary = _split_current_battle_items(
                    damage_items,
                    self._max_seen_dmg_time,
                )
                if crossed_boundary:
                    has_ocr_teams = any(p.team in ("ally", "enemy") for p in self.state.players.values())
                    if has_ocr_teams:
                        self.state.clear_stale_hud_data()
                    else:
                        self._reset_match_session(reset_cursors=False)
                        self.state.battle_active = True
                    changed = True

                for item in stale_items:
                    item_id = int(item.get("id", 0))
                    if item_id > self.last_dmg:
                        self.last_dmg = item_id
                    if self.debug_recorder is not None and self.config.debug_mode:
                        self.debug_recorder.record_hud_item(
                            source="damage",
                            item=item,
                            parsed_type=None,
                            parsed_summary=None,
                            applied=False,
                            note="skipped_stale_previous_battle",
                        )

                for item in active_items:
                    item_id = int(item.get("id", 0))
                    if item_id > self.last_dmg:
                        self.last_dmg = item_id
                    if item.get("time") is not None:
                        self._max_seen_dmg_time = float(item["time"])
                    msg = str(item.get("msg", ""))
                    if msg:
                        ev_ts = float(item["time"]) if item.get("time") is not None else time.time()
                        ev = self.parser.parse_hud_message(
                            msg, timestamp=ev_ts, language=self.config.log_language
                        )
                        if ev is not None:
                            self.state.apply_hud_event(ev)
                            changed = True
                        if self.debug_recorder is not None and self.config.debug_mode:
                            self.debug_recorder.record_hud_item(
                                source="damage",
                                item=item,
                                parsed_type=type(ev).__name__ if ev is not None else None,
                                parsed_summary=_summarize_event(ev),
                                applied=ev is not None,
                            )
        except Exception as exc:
            logger.debug("Error polling /hudmsg: %s", exc)

        # Incremental /gamechat polling
        try:
            chat_resp = await client.get(
                f"{base_url}/gamechat",
                params={"lastId": self.last_chat_id},
            )
            if chat_resp.status_code == 200:
                chat_items = chat_resp.json()
                if isinstance(chat_items, list):
                    for item in chat_items:
                        item_id = int(item.get("id", 0))
                        if item_id > self.last_chat_id:
                            self.last_chat_id = item_id
                        ev = self.parser.parse_chat_message(
                            msg=str(item.get("msg", "")),
                            sender=str(item.get("sender", "")),
                            mode=str(item.get("mode", "")),
                            enemy=bool(item.get("enemy", False)),
                            timestamp=time.time(),
                            language=self.config.log_language,
                        )
                        if ev is not None:
                            self.state.apply_chat_event(ev)
                            changed = True
                        if self.debug_recorder is not None and self.config.debug_mode:
                            self.debug_recorder.record_chat_item(
                                item=item,
                                parsed_type=type(ev).__name__ if ev is not None else None,
                                parsed_summary=_summarize_event(ev),
                            )
        except Exception as exc:
            logger.debug("Error polling /gamechat: %s", exc)

        if self.state.purge_expired_air_alert():
            changed = True

        if changed:
            self._notify()
        return True

    async def run(self) -> None:
        """Main background polling loop."""
        async with httpx.AsyncClient(timeout=1.5) as client:
            while not self._stop_event.is_set():
                try:
                    connected = await self.poll_once(client)
                    sleep_sec = 0.5 if (connected and self.state.battle_active) else 1.5
                    if connected and not self.state.battle_active:
                        sleep_sec = 1.0
                except Exception as exc:
                    logger.error("Unexpected poller loop error: %s", exc)
                    sleep_sec = 2.0

                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_sec)
                except asyncio.TimeoutError:
                    pass
