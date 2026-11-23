"""FastAPI application factory exposing REST controls, WebSocket feed, and SPA static assets."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from wt_xvm.api.ws_hub import WebSocketHub
from wt_xvm.config import AppConfig, save_config
from wt_xvm.core.battle_state import BattleSessionState
from wt_xvm.core.log_parser import LogParser
from wt_xvm.core.mock_replay import MockReplayController
from wt_xvm.core.poller import WarThunderPoller
from wt_xvm.ocr.tab_watcher import TabOcrWatcher
from wt_xvm.paths import get_frontend_dist_dir, get_log_path
from wt_xvm.stats.service import StatsService

if TYPE_CHECKING:
    from wt_xvm.core.debug_recorder import DebugSessionRecorder

logger = logging.getLogger("wt_xvm.api.server")


class ConfigPatchRequest(BaseModel):
    """Partial configuration update payload."""

    battle_mode: Literal["rb", "ab", "sb"] | None = None
    log_language: Literal["auto", "ru", "en"] | None = None
    air_alert_enabled: bool | None = None
    air_alert_duration_sec: int | None = None
    air_alert_style: Literal["rect", "circle"] | None = None
    edit_layout: bool | None = None
    overlay_visible: bool | None = None
    toggle_overlay_hotkey: str | None = None
    debug_mode: bool | None = None
    widgets: dict[str, Any] | None = None
    ocr_roi: dict[str, Any] | None = None


class RuntimeContext:
    """Holds shared backend services for FastAPI routes and desktop shell."""

    def __init__(
        self,
        config: AppConfig,
        state: BattleSessionState,
        parser: LogParser,
        stats_service: StatsService,
        tab_watcher: TabOcrWatcher,
        poller: WarThunderPoller,
        mock_replay: MockReplayController,
        ws_hub: WebSocketHub,
        config_path: Path | None = None,
        on_edit_layout_changed: Callable[[bool], None] | None = None,
        on_overlay_visible_changed: Callable[[bool], None] | None = None,
        on_toggle_hotkey_changed: Callable[[str], None] | None = None,
        on_quit: Callable[[], None] | None = None,
        debug_recorder: DebugSessionRecorder | None = None,
    ) -> None:
        self.config = config
        self.state = state
        self.parser = parser
        self.stats_service = stats_service
        self.tab_watcher = tab_watcher
        self.poller = poller
        self.mock_replay = mock_replay
        self.ws_hub = ws_hub
        self.config_path = config_path
        self.on_edit_layout_changed = on_edit_layout_changed
        self.on_overlay_visible_changed = on_overlay_visible_changed
        self.on_toggle_hotkey_changed = on_toggle_hotkey_changed
        self.on_quit = on_quit
        self.debug_recorder = debug_recorder or getattr(poller, "debug_recorder", None)

    def snapshot_dict(self) -> dict[str, Any]:
        snap = self.state.snapshot(ocr_cooldown_remaining=self.tab_watcher.cooldown_remaining())
        return snap.model_dump(mode="json")

    def config_dict(self) -> dict[str, Any]:
        return self.config.model_dump(mode="json")

    async def apply_config_patch(self, patch: dict[str, Any]) -> AppConfig:
        merged = self.config.model_dump()
        for key, value in patch.items():
            if value is None:
                continue
            if key == "widgets" and isinstance(value, dict):
                w = merged.get("widgets", {})
                for wk, wv in value.items():
                    if isinstance(wv, dict) and isinstance(w.get(wk), dict):
                        w[wk].update(wv)
                    else:
                        w[wk] = wv
                merged["widgets"] = w
            elif key == "ocr_roi" and isinstance(value, dict):
                r = merged.get("ocr_roi", {})
                for rk, rv in value.items():
                    if isinstance(rv, dict) and isinstance(r.get(rk), dict):
                        r[rk].update(rv)
                    else:
                        r[rk] = rv
                merged["ocr_roi"] = r
            else:
                merged[key] = value

        updated = AppConfig.model_validate(merged)
        prev_edit = self.config.edit_layout
        prev_visible = self.config.overlay_visible
        prev_hotkey = self.config.toggle_overlay_hotkey

        # Mutate fields in-place so references held by Poller/State/Watcher stay up-to-date
        for field_name in AppConfig.model_fields:
            setattr(self.config, field_name, getattr(updated, field_name))

        save_config(self.config, self.config_path)

        if self.config.edit_layout != prev_edit and self.on_edit_layout_changed is not None:
            try:
                self.on_edit_layout_changed(self.config.edit_layout)
            except Exception as exc:
                logger.warning("Overlay edit_layout hook failed: %s", exc)

        if self.config.overlay_visible != prev_visible and self.on_overlay_visible_changed is not None:
            try:
                self.on_overlay_visible_changed(self.config.overlay_visible)
            except Exception as exc:
                logger.warning("Overlay visibility hook failed: %s", exc)

        if self.config.toggle_overlay_hotkey != prev_hotkey and self.on_toggle_hotkey_changed is not None:
            try:
                self.on_toggle_hotkey_changed(self.config.toggle_overlay_hotkey)
            except Exception as exc:
                logger.warning("Overlay toggle hotkey hook failed: %s", exc)

        await self.ws_hub.broadcast_json({"type": "config", "config": self.config_dict()})
        self.ws_hub.schedule_state_broadcast()
        return self.config


def create_app(
    ctx: RuntimeContext,
    start_background_tasks: bool = True,
    frontend_dir: Path | None = None,
    on_startup_async: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    """Create configured FastAPI app with REST/WS API and optional SPA static serving."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        loop = asyncio.get_running_loop()
        ctx.ws_hub.bind_loop(loop)
        tasks: list[asyncio.Task] = []
        if start_background_tasks:
            tasks.append(asyncio.create_task(ctx.poller.run(), name="wt_poller"))
            tasks.append(asyncio.create_task(ctx.stats_service.run(), name="stats_queue"))
            tasks.append(asyncio.create_task(ctx.tab_watcher.run(), name="tab_ocr"))

            async def _air_alert_ticker() -> None:
                while True:
                    await asyncio.sleep(0.5)
                    if ctx.state.purge_expired_air_alert():
                        ctx.ws_hub.schedule_state_broadcast()

            tasks.append(asyncio.create_task(_air_alert_ticker(), name="air_alert_ticker"))

        if on_startup_async is not None:
            await on_startup_async()

        try:
            yield
        finally:
            await ctx.mock_replay.stop()
            ctx.poller.stop()
            ctx.tab_watcher.stop()
            await ctx.stats_service.stop()
            for t in tasks:
                t.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="WT-XVM Tactical Overlay API", lifespan=lifespan)

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "wt_connected": ctx.state.wt_connected,
            "battle_active": ctx.state.battle_active,
            "mock_mode": ctx.state.mock_mode,
        }

    @app.get("/api/state")
    async def get_state() -> dict[str, Any]:
        return ctx.snapshot_dict()

    @app.get("/api/config")
    async def get_config() -> dict[str, Any]:
        return ctx.config_dict()

    @app.patch("/api/config")
    async def patch_config(payload: ConfigPatchRequest) -> dict[str, Any]:
        updated = await ctx.apply_config_patch(payload.model_dump(exclude_none=True))
        return updated.model_dump(mode="json")

    @app.post("/api/config/reset-layout")
    async def reset_layout() -> dict[str, Any]:
        defaults = AppConfig().widgets.model_dump(mode="json")
        updated = await ctx.apply_config_patch({"widgets": defaults})
        return updated.model_dump(mode="json")

    @app.post("/api/config/reset-ocr-roi")
    async def reset_ocr_roi() -> dict[str, Any]:
        defaults = AppConfig().ocr_roi.model_dump(mode="json")
        updated = await ctx.apply_config_patch({"ocr_roi": defaults})
        return updated.model_dump(mode="json")

    @app.post("/api/mode/{mode}")
    async def set_mode(mode: Literal["rb", "ab", "sb"]) -> dict[str, Any]:
        updated = await ctx.apply_config_patch({"battle_mode": mode})
        return {"battle_mode": updated.battle_mode}

    @app.post("/api/stats/resume")
    async def resume_stats() -> dict[str, Any]:
        ctx.stats_service.resume()
        ctx.ws_hub.schedule_state_broadcast()
        return {"ok": True}

    @app.delete("/api/stats/cache")
    async def clear_cache() -> dict[str, Any]:
        ctx.stats_service.clear_cache()
        ctx.ws_hub.schedule_state_broadcast()
        return {"ok": True}

    @app.post("/api/mock/start")
    async def start_mock(lang: Literal["ru", "en"] = "ru", speed: float = 5.0) -> dict[str, Any]:
        await ctx.mock_replay.start(lang=lang, speed=speed)
        return {"ok": True, "lang": lang, "speed": speed}

    @app.post("/api/mock/stop")
    async def stop_mock() -> dict[str, Any]:
        await ctx.mock_replay.stop()
        return {"ok": True}

    @app.get("/api/ocr/preview")
    async def ocr_preview() -> Response:
        png = ctx.tab_watcher.last_preview_png
        if not png:
            return Response(status_code=204)
        return Response(content=png, media_type="image/png")

    @app.get("/api/debug/status")
    async def debug_status() -> dict[str, Any]:
        base_dir = (
            ctx.debug_recorder.get_base_dir()
            if ctx.debug_recorder is not None
            else (get_log_path().parent / "debug_sessions")
        )
        session_dir = ctx.debug_recorder.get_session_dir() if ctx.debug_recorder is not None else None
        return {
            "enabled": ctx.config.debug_mode,
            "base_dir": str(base_dir),
            "session_dir": str(session_dir) if session_dir is not None else None,
        }

    @app.post("/api/debug/open-folder")
    async def open_debug_folder() -> dict[str, Any]:
        target = (
            ctx.debug_recorder.get_base_dir()
            if ctx.debug_recorder is not None
            else (get_log_path().parent / "debug_sessions")
        )
        target.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            try:
                os.startfile(str(target))  # type: ignore[attr-defined]
            except Exception as exc:
                logger.warning("Failed to open debug folder %s: %s", target, exc)
        return {"ok": True, "path": str(target)}

    @app.post("/api/app/quit")
    async def quit_app() -> dict[str, Any]:
        """Gracefully shut down the application from UI or overlay."""
        if ctx.on_quit is not None:
            threading.Timer(0.15, ctx.on_quit).start()
        return {"ok": True}

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        initial = {
            "type": "init",
            "state": ctx.snapshot_dict(),
            "config": ctx.config_dict(),
        }
        await ctx.ws_hub.connect(ws, initial)
        try:
            while True:
                msg = await ws.receive_text()
                if msg == "ping":
                    await ws.send_json({"type": "pong"})
        except WebSocketDisconnect:
            ctx.ws_hub.disconnect(ws)
        except Exception:
            ctx.ws_hub.disconnect(ws)

    # Mount compiled React SPA if present
    dist_dir = frontend_dir or get_frontend_dist_dir()
    assets_dir = dist_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    index_html = dist_dir / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        if full_path.startswith("api/") or full_path == "ws":
            raise HTTPException(status_code=404, detail="Not found")
        if index_html.exists():
            return FileResponse(str(index_html))
        return JSONResponse(
            status_code=503,
            content={"detail": "Frontend bundle not built yet. Run `npm run build` in frontend/."},
        )

    return app
