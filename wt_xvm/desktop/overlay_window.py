"""Transparent click-through Edge WebView2 overlay window with Win32 WS_EX_TRANSPARENT control."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import os
import sys
import threading
import time
from typing import Callable

logger = logging.getLogger("wt_xvm.desktop.overlay")

_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_LAYERED = 0x00080000
_WS_EX_TOPMOST = 0x00000008
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_TOOLWINDOW = 0x00000080

_HWND_TOPMOST = -1
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020

_SW_HIDE = 0
_SW_SHOWNA = 8
_VK_F9 = 0x78

_SPECIAL_VK_MAP: dict[str, int] = {
    "INSERT": 0x2D,
    "INS": 0x2D,
    "DELETE": 0x2E,
    "DEL": 0x2E,
    "HOME": 0x24,
    "END": 0x23,
    "PAGEUP": 0x21,
    "PGUP": 0x21,
    "PRIOR": 0x21,
    "PAGEDOWN": 0x22,
    "PGDN": 0x22,
    "NEXT": 0x22,
    "PAUSE": 0x13,
    "SCROLLLOCK": 0x91,
    "SCROLL": 0x91,
    "BACKQUOTE": 0xC0,
    "`": 0xC0,
    "~": 0xC0,
    "MINUS": 0xBD,
    "-": 0xBD,
    "EQUAL": 0xBB,
    "=": 0xBB,
    "BACKSLASH": 0xDC,
    "\\": 0xDC,
    "BRACKETLEFT": 0xDB,
    "[": 0xDB,
    "BRACKETRIGHT": 0xDD,
    "]": 0xDD,
    "MULTIPLY": 0x6A,
    "NUMPADMULTIPLY": 0x6A,
    "ADD": 0x6B,
    "NUMPADADD": 0x6B,
    "SUBTRACT": 0x6D,
    "NUMPADSUBTRACT": 0x6D,
    "DECIMAL": 0x6E,
    "NUMPADDECIMAL": 0x6E,
    "DIVIDE": 0x6F,
    "NUMPADDIVIDE": 0x6F,
}


def resolve_vk_code(key_name: str) -> int | None:
    """Resolve a configurable hotkey name (e.g. 'F8', 'INSERT', 'O') to a Win32 VK code."""
    token = str(key_name or "").strip().upper()
    if not token or token == "NONE":
        return None
    if token in _SPECIAL_VK_MAP:
        return _SPECIAL_VK_MAP[token]
    if token.startswith("F") and token[1:].isdigit():
        num = int(token[1:])
        if 1 <= num <= 12:
            return 0x70 + (num - 1)
    if token.startswith("NUMPAD") and len(token) == 7 and token[6].isdigit():
        return 0x60 + int(token[6])
    if token.startswith("KEY") and len(token) == 4 and "A" <= token[3] <= "Z":
        return ord(token[3])
    if token.startswith("DIGIT") and len(token) == 6 and "0" <= token[5] <= "9":
        return ord(token[5])
    if len(token) == 1 and (("A" <= token <= "Z") or ("0" <= token <= "9")):
        return ord(token)
    return None

_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_DONOTROUND = 1
_DWMWA_SYSTEMBACKDROP_TYPE = 38
_DWMSBT_NONE = 1


class _DwmMargins(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


def enable_dpi_awareness() -> None:
    """Enable per-monitor DPI awareness on Windows before creating windows or capturing screen."""
    if sys.platform != "win32":
        return
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def get_primary_screen_size() -> tuple[int, int]:
    """Return primary display width and height in physical pixels."""
    if sys.platform != "win32":
        return (1920, 1080)
    try:
        user32 = ctypes.windll.user32
        return (int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1)))
    except Exception:
        return (1920, 1080)


class OverlayWindowController:
    """Manages the transparent pywebview overlay window, click-through styles, and F9 hotkey."""

    def __init__(
        self,
        url: str,
        initial_edit_layout: bool = False,
        initial_visible: bool = True,
        on_toggle_edit_layout: Callable[[], None] | None = None,
        initial_toggle_visible_hotkey: str = "F8",
        on_toggle_visible: Callable[[], None] | None = None,
    ) -> None:
        self.url = url
        self._edit_layout = initial_edit_layout
        self._visible = initial_visible
        self._on_toggle_edit_layout = on_toggle_edit_layout
        self._toggle_visible_vk: int | None = resolve_vk_code(initial_toggle_visible_hotkey)
        self._on_toggle_visible = on_toggle_visible

        self.window = None
        self._hwnd: int | None = None
        self._stop_hotkey = threading.Event()
        self._hotkey_thread: threading.Thread | None = None
        self._style_lock = threading.Lock()
        self._theme_hooks_disabled = False
        self._last_foreground_hwnd: int = 0
        self._pending_heal_at: float | None = None
        self._last_drift_check_at: float = 0.0

    def set_toggle_visible_hotkey(self, key_name: str) -> None:
        """Update the active Win32 virtual-key used to toggle overlay visibility."""
        self._toggle_visible_vk = resolve_vk_code(key_name)
        logger.info("Overlay hide/show hotkey updated to %r (vk=%s).", key_name, self._toggle_visible_vk)

    def _resolve_hwnd(self) -> int | None:
        if self._hwnd:
            return self._hwnd
        if sys.platform != "win32" or self.window is None:
            return None
        try:
            native = getattr(self.window, "native", None)
            if native is not None and hasattr(native, "Handle"):
                self._hwnd = int(native.Handle.ToInt64())
                return self._hwnd
        except Exception:
            pass
        try:
            hwnd = ctypes.windll.user32.FindWindowW(None, "WT-XVM Overlay")
            if hwnd:
                self._hwnd = int(hwnd)
                return self._hwnd
        except Exception:
            pass
        return None

    def _disable_pywebview_theme_hooks(self, form) -> None:
        """Detach pywebview's Win11 UserPreferenceChanged Mica hook that breaks transparency."""
        if self._theme_hooks_disabled:
            return
        try:
            from Microsoft.Win32 import SystemEvents

            handler = getattr(form, "on_system_theme_changed", None)
            if handler is not None:
                SystemEvents.UserPreferenceChanged -= handler
        except Exception as exc:
            logger.debug("UserPreferenceChanged unsubscribe warning: %s", exc)
        try:
            form.update_title_bar_theme = lambda: None
            self._theme_hooks_disabled = True
        except Exception as exc:
            logger.debug("update_title_bar_theme override warning: %s", exc)

    def _apply_dwm_transparency(self, hwnd: int) -> None:
        """Disable Windows 11 Mica backdrop and extend DWM glass across the client area for per-pixel alpha."""
        try:
            dwmapi = ctypes.windll.dwmapi
            dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                wintypes.DWORD(_DWMWA_SYSTEMBACKDROP_TYPE),
                ctypes.byref(ctypes.c_int(_DWMSBT_NONE)),
                wintypes.DWORD(4),
            )
            dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                wintypes.DWORD(_DWMWA_WINDOW_CORNER_PREFERENCE),
                ctypes.byref(ctypes.c_int(_DWMWCP_DONOTROUND)),
                wintypes.DWORD(4),
            )
        except Exception as exc:
            logger.debug("DwmSetWindowAttribute warning: %s", exc)

        # Ensure WinForms Form & WebView2 control use exact fullscreen bounds + Black/Transparent alpha backing
        try:
            form = getattr(self.window, "native", None)
            if form is not None and not getattr(form, "IsDisposed", False):
                from System import Action
                from System.Drawing import Color

                screen_w, screen_h = get_primary_screen_size()

                def _set_colors() -> None:
                    if getattr(form, "IsDisposed", False):
                        return
                    self._disable_pywebview_theme_hooks(form)
                    if (
                        getattr(form, "Left", None) != 0
                        or getattr(form, "Top", None) != 0
                        or getattr(form, "Width", None) != screen_w
                        or getattr(form, "Height", None) != screen_h
                    ):
                        form.SetBounds(0, 0, screen_w, screen_h)
                    if form.BackColor.ToArgb() != Color.Black.ToArgb():
                        form.BackColor = Color.Black
                    browser = getattr(form, "browser", None)
                    wv = getattr(browser, "webview", None) if browser is not None else None
                    if wv is not None and wv.DefaultBackgroundColor.ToArgb() != Color.Transparent.ToArgb():
                        wv.DefaultBackgroundColor = Color.Transparent

                if getattr(form, "InvokeRequired", False):
                    form.Invoke(Action(_set_colors))
                else:
                    _set_colors()
        except Exception as exc:
            logger.debug("WinForms transparent color setup warning: %s", exc)

        try:
            margins = _DwmMargins(-1, -1, -1, -1)
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd), ctypes.byref(margins))
        except Exception as exc:
            logger.warning("DwmExtendFrameIntoClientArea failed: %s", exc)

    def _apply_ex_style(self, hwnd: int, click_through: bool, log_change: bool = True) -> None:
        user32 = ctypes.windll.user32
        ex_style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        base_style = ex_style | _WS_EX_LAYERED | _WS_EX_TOPMOST | _WS_EX_TOOLWINDOW
        if click_through:
            new_style = base_style | _WS_EX_TRANSPARENT | _WS_EX_NOACTIVATE
        else:
            new_style = base_style & ~(_WS_EX_TRANSPARENT | _WS_EX_NOACTIVATE)

        user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, new_style)
        screen_w, screen_h = get_primary_screen_size()
        user32.SetWindowPos(
            hwnd,
            _HWND_TOPMOST,
            0,
            0,
            screen_w,
            screen_h,
            _SWP_FRAMECHANGED | _SWP_NOACTIVATE,
        )
        if log_change:
            logger.info("Overlay click-through set to %s (hwnd=%s, %dx%d).", click_through, hwnd, screen_w, screen_h)

    def set_click_through(self, enabled: bool) -> None:
        """Enable or disable mouse click-through via Win32 WS_EX_TRANSPARENT and enforce DWM transparency."""
        self._edit_layout = not enabled
        if sys.platform != "win32":
            return
        hwnd = self._resolve_hwnd()
        if not hwnd:
            return
        try:
            with self._style_lock:
                self._apply_dwm_transparency(hwnd)
                self._apply_ex_style(hwnd, click_through=enabled, log_change=True)
        except Exception as exc:
            logger.warning("Failed to update Win32 click-through style: %s", exc)

    def _check_and_heal_transparency(self, force: bool = False) -> None:
        """Verify DWM backdrop/ExStyles and heal if Alt+Tab or Windows events reset them."""
        if sys.platform != "win32" or not self._visible:
            return
        hwnd = self._resolve_hwnd()
        if not hwnd:
            return
        with self._style_lock:
            if not self._visible:
                return
            click_through = not self._edit_layout
            drifted = False
            if not force:
                try:
                    backdrop = ctypes.c_int(0)
                    hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
                        wintypes.HWND(hwnd),
                        wintypes.DWORD(_DWMWA_SYSTEMBACKDROP_TYPE),
                        ctypes.byref(backdrop),
                        wintypes.DWORD(4),
                    )
                    if hr == 0 and backdrop.value != _DWMSBT_NONE:
                        drifted = True
                    ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
                    required = _WS_EX_LAYERED | _WS_EX_TOPMOST | _WS_EX_TOOLWINDOW
                    if (ex_style & required) != required:
                        drifted = True
                    elif click_through and (
                        not (ex_style & _WS_EX_TRANSPARENT) or not (ex_style & _WS_EX_NOACTIVATE)
                    ):
                        drifted = True
                    elif not click_through and (
                        (ex_style & _WS_EX_TRANSPARENT) or (ex_style & _WS_EX_NOACTIVATE)
                    ):
                        drifted = True
                except Exception as exc:
                    logger.debug("DWM/ExStyle probe warning: %s", exc)
                    return

            if not force and not drifted:
                return

            try:
                self._apply_dwm_transparency(hwnd)
                self._apply_ex_style(hwnd, click_through=click_through, log_change=False)
                if drifted:
                    logger.info("Healed drifted overlay DWM/ExStyle transparency (hwnd=%s).", hwnd)
            except Exception as exc:
                logger.debug("Transparency heal error: %s", exc)

    def refresh_transparency(self) -> None:
        """Manually force-reapply DWM glass margins, WinForms transparency, and click-through flags."""
        if sys.platform != "win32":
            return
        hwnd = self._resolve_hwnd()
        if not hwnd:
            return
        logger.info("Manual overlay transparency refresh requested.")
        with self._style_lock:
            self._apply_dwm_transparency(hwnd)
            self._apply_ex_style(hwnd, click_through=not self._edit_layout, log_change=False)

    def set_visible(self, visible: bool) -> None:
        """Show or hide overlay window without stealing focus."""
        self._visible = visible
        if sys.platform == "win32":
            hwnd = self._resolve_hwnd()
            if hwnd:
                try:
                    ctypes.windll.user32.ShowWindow(hwnd, _SW_SHOWNA if visible else _SW_HIDE)
                    if visible:
                        self.set_click_through(not self._edit_layout)
                    return
                except Exception as exc:
                    logger.debug("ShowWindow fallback error: %s", exc)
        if self.window is not None:
            try:
                if visible:
                    self.window.show()
                else:
                    self.window.hide()
            except Exception:
                pass

    def _hotkey_loop(self) -> None:
        """Passive edge-triggered GetAsyncKeyState hotkeys & DWM transparency watchdog (no keyboard hooks)."""
        was_f9_down = False
        was_vis_down = False
        while not self._stop_hotkey.is_set():
            try:
                if sys.platform == "win32":
                    user32 = ctypes.windll.user32
                    is_f9_down = bool(user32.GetAsyncKeyState(_VK_F9) & 0x8000)
                    if is_f9_down and not was_f9_down and self._on_toggle_edit_layout is not None:
                        self._on_toggle_edit_layout()
                    was_f9_down = is_f9_down

                    vk_vis = self._toggle_visible_vk
                    if vk_vis is not None and vk_vis != _VK_F9:
                        is_vis_down = bool(user32.GetAsyncKeyState(vk_vis) & 0x8000)
                        if is_vis_down and not was_vis_down and self._on_toggle_visible is not None:
                            self._on_toggle_visible()
                        was_vis_down = is_vis_down
                    else:
                        was_vis_down = False

                    if self._visible and self._resolve_hwnd():
                        now = time.monotonic()
                        fg = int(user32.GetForegroundWindow() or 0)
                        if fg and fg != self._last_foreground_hwnd:
                            if self._last_foreground_hwnd != 0:
                                self._check_and_heal_transparency(force=True)
                                self._pending_heal_at = now + 0.35
                            self._last_foreground_hwnd = fg
                        if self._pending_heal_at is not None and now >= self._pending_heal_at:
                            self._pending_heal_at = None
                            self._check_and_heal_transparency(force=True)
                        if now - self._last_drift_check_at >= 2.0:
                            self._last_drift_check_at = now
                            self._check_and_heal_transparency(force=False)
            except Exception as exc:
                logger.debug("Overlay watcher loop error: %s", exc)
            time.sleep(0.05)

    def start_blocking(self) -> None:
        """Create transparent Edge WebView2 window and run pywebview GUI loop on main thread."""
        os.environ["WEBVIEW2_DEFAULT_BACKGROUND_COLOR"] = "00000000"
        import webview

        width, height = get_primary_screen_size()
        self.window = webview.create_window(
            title="WT-XVM Overlay",
            url=self.url,
            x=0,
            y=0,
            width=width,
            height=height,
            frameless=True,
            transparent=True,
            background_color="#000000",
            on_top=True,
            easy_drag=False,
            shadow=False,
            focus=False,
            resizable=False,
            hidden=not self._visible,
        )

        def _apply_initial_styles() -> None:
            self.set_click_through(not self._edit_layout)
            if not self._visible:
                self.set_visible(False)

        def _on_shown() -> None:
            _apply_initial_styles()
            # Re-apply after 300ms to override any post-navigation WinForms/DWM theme redraw
            threading.Timer(0.3, _apply_initial_styles).start()

        if self.window is not None:
            self.window.events.shown += _on_shown
            self.window.events.loaded += _apply_initial_styles

        self._hotkey_thread = threading.Thread(target=self._hotkey_loop, name="wt_f9_hotkey", daemon=True)
        self._hotkey_thread.start()

        try:
            webview.start(gui="edgechromium")
        finally:
            self.stop()

    def stop(self) -> None:
        self._stop_hotkey.set()
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass
            self.window = None
