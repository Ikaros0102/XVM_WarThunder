"""Coalesced WebSocket broadcast hub synchronizing Overlay and Second Screen clients."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from fastapi import WebSocket

logger = logging.getLogger("wt_xvm.api.ws_hub")


class WebSocketHub:
    """Broadcasts serialized state and config snapshots to connected WebSocket clients."""

    def __init__(self, snapshot_fn: Callable[[], dict[str, Any]]) -> None:
        self._snapshot_fn = snapshot_fn
        self._clients: set[WebSocket] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending_broadcast: bool = False
        self._timer_handle: asyncio.TimerHandle | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, ws: WebSocket, initial_payload: dict[str, Any]) -> None:
        await ws.accept()
        self._clients.add(ws)
        try:
            await ws.send_json(initial_payload)
        except Exception:
            self._clients.discard(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    def schedule_state_broadcast(self) -> None:
        """Schedule a coalesced (~80ms) state push from any thread or coroutine."""
        if self._loop is None or self._loop.is_closed():
            return

        def _schedule_on_loop() -> None:
            if self._pending_broadcast:
                return
            self._pending_broadcast = True
            self._timer_handle = self._loop.call_later(  # type: ignore[union-attr]
                0.08,
                lambda: asyncio.create_task(self._flush_state()),
            )

        try:
            running = asyncio.get_running_loop()
            if running is self._loop:
                _schedule_on_loop()
            else:
                self._loop.call_soon_threadsafe(_schedule_on_loop)
        except RuntimeError:
            self._loop.call_soon_threadsafe(_schedule_on_loop)

    async def _flush_state(self) -> None:
        self._pending_broadcast = False
        self._timer_handle = None
        if not self._clients:
            return
        payload = {"type": "state", "state": self._snapshot_fn()}
        await self.broadcast_json(payload)

    async def broadcast_json(self, payload: dict[str, Any]) -> None:
        """Send JSON payload to all live WebSocket clients, pruning disconnected ones."""
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)
