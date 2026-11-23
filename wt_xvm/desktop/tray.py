"""Windows system tray icon and menu for WT-XVM."""

from __future__ import annotations

import logging
import os
import sys
import webbrowser
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

from wt_xvm.models import BattleMode
from wt_xvm.paths import get_log_path

logger = logging.getLogger("wt_xvm.desktop.tray")


def create_tray_icon_image() -> Image.Image:
    """Render a crisp 64x64 tactical radar tray icon without external icon assets."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill=(15, 23, 42, 240), outline=(56, 189, 248, 255), width=3)
    draw.ellipse((18, 18, 46, 46), outline=(56, 189, 248, 170), width=2)
    draw.line((32, 8, 32, 56), fill=(56, 189, 248, 130), width=2)
    draw.line((8, 32, 56, 32), fill=(56, 189, 248, 130), width=2)
    draw.ellipse((28, 28, 36, 36), fill=(250, 204, 21, 255))
    return img


class TrayController:
    """Controls the pystray icon in a background thread."""

    def __init__(
        self,
        board_url: str,
        on_toggle_edit_layout: Callable[[], None],
        on_toggle_visible: Callable[[], None],
        on_set_mode: Callable[[BattleMode], None],
        on_exit: Callable[[], None],
        on_refresh_transparency: Callable[[], None] | None = None,
        on_toggle_debug_mode: Callable[[], None] | None = None,
    ) -> None:
        self.board_url = board_url
        self.on_toggle_edit_layout = on_toggle_edit_layout
        self.on_toggle_visible = on_toggle_visible
        self.on_set_mode = on_set_mode
        self.on_exit = on_exit
        self.on_refresh_transparency = on_refresh_transparency
        self.on_toggle_debug_mode = on_toggle_debug_mode
        self._icon = None

    def _open_second_screen(self, *_args) -> None:
        try:
            webbrowser.open(self.board_url)
        except Exception as exc:
            logger.warning("Failed to open browser: %s", exc)

    def _open_logs(self, *_args) -> None:
        log_dir: Path = get_log_path().parent
        log_dir.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(log_dir))  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning("Failed to open logs dir: %s", exc)

    def start_detached(self) -> None:
        import pystray

        menu = pystray.Menu(
            pystray.MenuItem("Открыть второй экран (Браузер)", self._open_second_screen, default=True),
            pystray.MenuItem("Режим редактирования лейаута (F9)", lambda *_: self.on_toggle_edit_layout()),
            pystray.MenuItem("Показать / Скрыть оверлей", lambda *_: self.on_toggle_visible()),
            pystray.MenuItem(
                "Восстановить прозрачность",
                lambda *_: self.on_refresh_transparency() if self.on_refresh_transparency is not None else None,
            ),
            pystray.MenuItem(
                "Режим отладки (Вкл/Выкл)",
                lambda *_: self.on_toggle_debug_mode() if self.on_toggle_debug_mode is not None else None,
            ),
            pystray.MenuItem(
                "Режим статистики",
                pystray.Menu(
                    pystray.MenuItem("Realistic Battles (RB)", lambda *_: self.on_set_mode("rb")),
                    pystray.MenuItem("Arcade Battles (AB)", lambda *_: self.on_set_mode("ab")),
                    pystray.MenuItem("Simulation Battles (SB)", lambda *_: self.on_set_mode("sb")),
                ),
            ),
            pystray.MenuItem("Открыть папку логов", self._open_logs),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", lambda *_: self._handle_exit()),
        )

        self._icon = pystray.Icon("WT-XVM", create_tray_icon_image(), "WT-XVM Tactical Overlay", menu)
        self._icon.run_detached()

    def _handle_exit(self) -> None:
        self.stop()
        self.on_exit()

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
