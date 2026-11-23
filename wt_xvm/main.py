"""Main application entrypoint supporting Desktop Overlay Mode and Headless/Second-Screen Mode."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import threading
import time
from typing import Literal

import uvicorn

from wt_xvm.api.server import RuntimeContext, create_app
from wt_xvm.api.ws_hub import WebSocketHub
from wt_xvm.config import load_config
from wt_xvm.core.battle_state import BattleSessionState
from wt_xvm.core.debug_recorder import DebugSessionRecorder
from wt_xvm.core.log_parser import LogParser
from wt_xvm.core.mock_replay import MockReplayController
from wt_xvm.core.poller import WarThunderPoller
from wt_xvm.core.vehicle_db import VehicleDB
from wt_xvm.desktop.overlay_window import OverlayWindowController, enable_dpi_awareness
from wt_xvm.desktop.tray import TrayController
from wt_xvm.logging_setup import setup_logging
from wt_xvm.models import BattleMode
from wt_xvm.ocr.tab_watcher import TabOcrWatcher
from wt_xvm.stats.cache import StatsCache
from wt_xvm.stats.providers import ThunderSkillProvider
from wt_xvm.stats.service import StatsService

logger = logging.getLogger("wt_xvm.main")


def build_runtime(
    host: str | None = None,
    port: int | None = None,
) -> tuple[RuntimeContext, list[asyncio.Future]]:
    """Construct all application services and return shared RuntimeContext."""
    cfg = load_config()
    if host:
        cfg.host = host
    if port:
        cfg.port = port

    vehicle_db = VehicleDB()
    parser = LogParser()
    state = BattleSessionState(cfg, vehicle_db)
    debug_recorder = DebugSessionRecorder(cfg)

    # Late-bound WS hub so callbacks can schedule broadcasts before loop starts
    ctx_holder: dict[str, RuntimeContext] = {}
    ws_hub = WebSocketHub(lambda: ctx_holder["ctx"].snapshot_dict())

    stats_cache = StatsCache()
    provider = ThunderSkillProvider()
    stats_service = StatsService(
        config=cfg,
        state=state,
        cache=stats_cache,
        provider=provider,
        on_change=ws_hub.schedule_state_broadcast,
        debug_recorder=debug_recorder,
    )
    state.set_stats_enqueue_callback(stats_service.request_player_stats)

    tab_watcher = TabOcrWatcher(
        config=cfg,
        state=state,
        on_change=ws_hub.schedule_state_broadcast,
        debug_recorder=debug_recorder,
    )

    def _on_session_reset() -> None:
        stats_service.reset_session()
        tab_watcher.reset_debounce()
        debug_recorder.rotate_session()

    poller = WarThunderPoller(
        config=cfg,
        state=state,
        parser=parser,
        on_change=ws_hub.schedule_state_broadcast,
        on_session_reset=_on_session_reset,
        debug_recorder=debug_recorder,
    )
    mock_replay = MockReplayController(
        state=state,
        parser=parser,
        on_change=ws_hub.schedule_state_broadcast,
    )

    ctx = RuntimeContext(
        config=cfg,
        state=state,
        parser=parser,
        stats_service=stats_service,
        tab_watcher=tab_watcher,
        poller=poller,
        mock_replay=mock_replay,
        ws_hub=ws_hub,
        debug_recorder=debug_recorder,
    )
    ctx_holder["ctx"] = ctx
    return ctx, []


def _schedule_coro(ctx: RuntimeContext, coro_factory) -> None:
    loop = ctx.ws_hub._loop
    if loop is None or loop.is_closed():
        return
    asyncio.run_coroutine_threadsafe(coro_factory(), loop)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="War Thunder Tactical Overlay (WT-XVM)")
    parser.add_argument("--headless", action="store_true", help="Run API/Web UI without overlay window or tray")
    parser.add_argument("--mock", action="store_true", help="Start built-in battle replay simulator automatically")
    parser.add_argument("--mock-lang", choices=["ru", "en"], default="ru", help="Mock replay language")
    parser.add_argument("--host", type=str, default=None, help="Bind host (default from config.json)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default from config.json)")
    args = parser.parse_args(argv)

    setup_logging()
    # PyInstaller console=False sets sys.stdout/sys.stderr to None; prevent third-party .isatty() crashes
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    if not args.headless:
        enable_dpi_awareness()

    ctx, _ = build_runtime(host=args.host, port=args.port)

    async def _auto_start_mock() -> None:
        lang: Literal["ru", "en"] = args.mock_lang
        await ctx.mock_replay.start(lang=lang, speed=5.0)

    app = create_app(
        ctx,
        start_background_tasks=True,
        on_startup_async=_auto_start_mock if args.mock else None,
    )

    if args.headless:
        logger.info("Starting WT-XVM in headless mode at http://%s:%d", ctx.config.host, ctx.config.port)
        uvicorn.run(app, host=ctx.config.host, port=ctx.config.port, log_level="warning", log_config=None)
        return 0

    # Desktop mode: FastAPI runs in daemon thread, Tray runs detached, pywebview owns main STA thread
    overlay_url = f"http://{ctx.config.host}:{ctx.config.port}/?mode=overlay"
    board_url = f"http://{ctx.config.host}:{ctx.config.port}/"

    def _toggle_edit() -> None:
        _schedule_coro(ctx, lambda: ctx.apply_config_patch({"edit_layout": not ctx.config.edit_layout}))

    def _toggle_vis() -> None:
        def _do_toggle() -> None:
            next_visible = not ctx.config.overlay_visible
            patch: dict[str, object] = {"overlay_visible": next_visible}
            if not next_visible and ctx.config.edit_layout:
                patch["edit_layout"] = False
            return ctx.apply_config_patch(patch)  # type: ignore[return-value]

        _schedule_coro(ctx, _do_toggle)

    overlay = OverlayWindowController(
        url=overlay_url,
        initial_edit_layout=ctx.config.edit_layout,
        initial_visible=ctx.config.overlay_visible,
        on_toggle_edit_layout=_toggle_edit,
        initial_toggle_visible_hotkey=ctx.config.toggle_overlay_hotkey,
        on_toggle_visible=_toggle_vis,
    )
    ctx.on_edit_layout_changed = lambda edit: overlay.set_click_through(not edit)
    ctx.on_overlay_visible_changed = overlay.set_visible
    ctx.on_toggle_hotkey_changed = overlay.set_toggle_visible_hotkey

    uv_config = uvicorn.Config(
        app,
        host=ctx.config.host,
        port=ctx.config.port,
        log_level="warning",
        log_config=None,
    )
    server = uvicorn.Server(uv_config)

    server_thread = threading.Thread(target=server.run, name="wt_uvicorn", daemon=True)
    server_thread.start()

    # Wait briefly for FastAPI to bind before WebView navigates
    deadline = time.monotonic() + 5.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)

    def _set_mode(mode: BattleMode) -> None:
        _schedule_coro(ctx, lambda: ctx.apply_config_patch({"battle_mode": mode}))

    def _toggle_debug() -> None:
        _schedule_coro(ctx, lambda: ctx.apply_config_patch({"debug_mode": not ctx.config.debug_mode}))

    tray_ref: list[TrayController] = []

    def _shutdown() -> None:
        server.should_exit = True
        for t in tray_ref:
            try:
                t.stop()
            except Exception:
                pass
        overlay.stop()
        force_exit_timer = threading.Timer(1.2, lambda: os._exit(0))
        force_exit_timer.daemon = True
        force_exit_timer.start()

    ctx.on_quit = _shutdown

    tray = TrayController(
        board_url=board_url,
        on_toggle_edit_layout=_toggle_edit,
        on_toggle_visible=_toggle_vis,
        on_set_mode=_set_mode,
        on_exit=_shutdown,
        on_refresh_transparency=overlay.refresh_transparency,
        on_toggle_debug_mode=_toggle_debug,
    )
    tray_ref.append(tray)
    tray.start_detached()

    logger.info("Starting WT-XVM overlay (%s) and second-screen UI (%s)", overlay_url, board_url)
    try:
        overlay.start_blocking()
    finally:
        tray.stop()
        server.should_exit = True
        server_thread.join(timeout=3.0)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
