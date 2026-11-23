"""Passive VK_TAB watcher with 30-second debounce and single-worker OCR executor."""

from __future__ import annotations

import asyncio
import ctypes
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from wt_xvm.config import AppConfig
from wt_xvm.core.battle_state import BattleSessionState
from wt_xvm.core.nick_cleaner import parse_player_identity
from wt_xvm.ocr.screen_capture import capture_scoreboard_columns
from wt_xvm.ocr.scoreboard_ocr import ScoreboardOCR

if TYPE_CHECKING:
    from PIL import Image
    from wt_xvm.core.debug_recorder import DebugSessionRecorder

logger = logging.getLogger("wt_xvm.ocr.tab_watcher")

_VK_TAB = 0x09
_VK_CONTROL = 0x11
_VK_MENU = 0x12
_VK_LWIN = 0x5B
_VK_RWIN = 0x5C
_MODIFIER_VKS = (_VK_CONTROL, _VK_MENU, _VK_LWIN, _VK_RWIN)


def _is_key_down_win32(vk_code: int) -> bool:
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk_code) & 0x8000)
    except Exception:
        return False


def _get_foreground_title_win32() -> str:
    if sys.platform != "win32":
        return ""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value
    except Exception:
        return ""


@dataclass(slots=True)
class _TabScanBundle:
    allies: list[str]
    enemies: list[str]
    preview_png: bytes
    left_img: Image.Image | None = None
    right_img: Image.Image | None = None
    left_diag: dict = field(default_factory=dict)
    right_diag: dict = field(default_factory=dict)


class TabOcrWatcher:
    """Monitors Tab key passively during active battles to scan and merge scoreboard rosters."""

    def __init__(
        self,
        config: AppConfig,
        state: BattleSessionState,
        ocr: ScoreboardOCR | None = None,
        on_change: Callable[[], None] | None = None,
        key_probe: Callable[[int], bool] | None = None,
        window_title_probe: Callable[[], str] | None = None,
        capture_fn: Callable | None = None,
        mono_clock: Callable[[], float] | None = None,
        debug_recorder: DebugSessionRecorder | None = None,
    ) -> None:
        self.config = config
        self.state = state
        self.ocr = ocr or ScoreboardOCR()
        self.on_change = on_change
        self._key_probe = key_probe or _is_key_down_win32
        self._window_title_probe = window_title_probe or _get_foreground_title_win32
        self._capture_fn = capture_fn or capture_scoreboard_columns
        self._mono = mono_clock or time.monotonic
        self.debug_recorder = debug_recorder

        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wt_ocr")
        self._stop_event = asyncio.Event()
        self._last_scan_mono: float | None = None
        self._was_tab_down: bool = False
        self._scan_in_progress: bool = False
        self.last_preview_png: bytes | None = None

    def reset_debounce(self) -> None:
        self._last_scan_mono = None
        self._was_tab_down = False

    def cooldown_remaining(self) -> float:
        if self._last_scan_mono is None:
            return 0.0
        rem = self.config.tab_debounce_sec - (self._mono() - self._last_scan_mono)
        return max(0.0, rem)

    def stop(self) -> None:
        self._stop_event.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _notify(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                pass

    def _any_modifier_down(self) -> bool:
        return any(self._key_probe(vk) for vk in _MODIFIER_VKS)

    def _is_wt_focused(self) -> bool:
        target_title = self.config.wt_window_title.casefold()
        if not target_title:
            return True
        title = self._window_title_probe()
        return target_title in title.casefold()

    def _sync_capture_and_ocr(self) -> _TabScanBundle:
        cols = self._capture_fn(self.config.ocr_roi)
        left_diag: dict = {}
        right_diag: dict = {}
        if hasattr(self.ocr, "recognize_column_detailed"):
            allies, left_diag = self.ocr.recognize_column_detailed(cols.left_img)
            enemies, right_diag = self.ocr.recognize_column_detailed(cols.right_img)
        else:
            allies, enemies = self.ocr.recognize_columns(cols.left_img, cols.right_img)
        return _TabScanBundle(
            allies=allies,
            enemies=enemies,
            preview_png=cols.preview_png,
            left_img=getattr(cols, "left_img", None),
            right_img=getattr(cols, "right_img", None),
            left_diag=left_diag,
            right_diag=right_diag,
        )

    def _record_debug_scan(
        self,
        bundle: _TabScanBundle,
        accepted: bool,
        reason: str,
        added_allies: int = 0,
        added_enemies: int = 0,
    ) -> None:
        if self.debug_recorder is None or not self.config.debug_mode:
            return
        parsed_allies = [parse_player_identity(x).model_dump() for x in bundle.allies]
        parsed_enemies = [parse_player_identity(x).model_dump() for x in bundle.enemies]
        roster_summary = {
            "allies": [p.display_nick for p in self.state.players.values() if p.team == "ally" and not p.is_placeholder],
            "enemies": [p.display_nick for p in self.state.players.values() if p.team == "enemy" and not p.is_placeholder],
            "placeholders": [p.display_nick for p in self.state.players.values() if p.is_placeholder],
            "unknown_count": sum(1 for p in self.state.players.values() if p.team == "unknown"),
        }
        payload = {
            "accepted": accepted,
            "reason": reason,
            "added_allies": added_allies,
            "added_enemies": added_enemies,
            "left_count": len(bundle.allies),
            "right_count": len(bundle.enemies),
            "left_diag": bundle.left_diag,
            "right_diag": bundle.right_diag,
            "parsed_allies": parsed_allies,
            "parsed_enemies": parsed_enemies,
            "roster_after": roster_summary,
        }
        self.debug_recorder.record_tab_ocr(
            payload,
            preview_png=bundle.preview_png,
            left_img=bundle.left_img,
            right_img=bundle.right_img,
        )

    async def trigger_scan_if_ready(self, require_hold: bool = True) -> bool:
        """Execute capture + OCR if debounce elapsed and (optionally) Tab remains held."""
        if self._scan_in_progress or not self.state.battle_active:
            return False

        if self.cooldown_remaining() > 0.0:
            return False

        if require_hold:
            if self._any_modifier_down() or not self._is_wt_focused():
                return False
            await asyncio.sleep(self.config.tab_hold_delay_ms / 1000.0)
            if (
                not self._key_probe(_VK_TAB)
                or self._any_modifier_down()
                or not self._is_wt_focused()
                or not self.state.battle_active
            ):
                return False

        self._scan_in_progress = True
        try:
            loop = asyncio.get_running_loop()
            bundle = await loop.run_in_executor(
                self._executor,
                self._sync_capture_and_ocr,
            )
            allies = bundle.allies
            enemies = bundle.enemies
            self.last_preview_png = bundle.preview_png
            self.state.ocr_status.last_error = None
            min_req = self.config.min_ocr_players_per_side
            if len(allies) < min_req or len(enemies) < min_req:
                logger.info(
                    "Tab OCR ignored incomplete scan (allies=%d, enemies=%d, min_required=%d).",
                    len(allies),
                    len(enemies),
                    min_req,
                )
                self._record_debug_scan(
                    bundle,
                    accepted=False,
                    reason=f"below_min_players_{min_req}",
                )
                return False

            self._last_scan_mono = self._mono()
            added_a, added_e = self.state.apply_tab_ocr(allies, enemies)
            self._record_debug_scan(
                bundle,
                accepted=True,
                reason="ok",
                added_allies=added_a,
                added_enemies=added_e,
            )
            logger.info(
                "Tab OCR scanned %d allies (%d added), %d enemies (%d added).",
                len(allies),
                added_a,
                len(enemies),
                added_e,
            )
            self._notify()
            return True
        except Exception as exc:
            logger.error("Tab OCR scan failed: %s", exc)
            self.state.ocr_status.last_error = str(exc)
            self._notify()
            return False
        finally:
            self._scan_in_progress = False

    async def run(self) -> None:
        """Poll VK_TAB passively at ~30 Hz during active matches."""
        was_battle = False
        while not self._stop_event.is_set():
            try:
                if not self.state.battle_active:
                    if was_battle:
                        self.reset_debounce()
                        was_battle = False
                    await asyncio.wait_for(self._stop_event.wait(), timeout=0.4)
                    continue

                was_battle = True
                is_wt_focused = self._is_wt_focused()

                tab_down = is_wt_focused and (not self._any_modifier_down()) and self._key_probe(_VK_TAB)
                rising_edge = tab_down and not self._was_tab_down
                self._was_tab_down = tab_down

                if rising_edge and self.cooldown_remaining() <= 0.0:
                    await self.trigger_scan_if_ready(require_hold=True)

                await asyncio.wait_for(self._stop_event.wait(), timeout=0.035)
            except asyncio.TimeoutError:
                pass
            except Exception as exc:
                logger.error("TabOcrWatcher loop error: %s", exc)
                await asyncio.sleep(0.5)
