"""Gentle rate-limited queue with deduplication and exponential circuit breaker."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from wt_xvm.models import ProviderStatus
from wt_xvm.stats.providers import ProviderResult, StatsProvider

logger = logging.getLogger("wt_xvm.stats.rate_limiter")

_BACKOFF_STEPS_SEC = (60.0, 180.0, 600.0)


@dataclass(order=True)
class _QueuedLookup:
    priority: int
    seq: int
    canonical_key: str = field(compare=False)
    query_nick: str = field(compare=False)
    fallbacks: tuple[str, ...] = field(default=(), compare=False)


class RateLimitedStatsQueue:
    """Single-worker polite queue enforcing inter-request intervals and circuit breaker."""

    def __init__(
        self,
        provider: StatsProvider,
        on_result: Callable[[str, ProviderResult], Awaitable[None] | None],
        on_status: Callable[[ProviderStatus], None] | None = None,
        min_interval_sec: float = 1.5,
        jitter_sec: float = 0.4,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.provider = provider
        self.on_result = on_result
        self.on_status = on_status
        self.min_interval_sec = min_interval_sec
        self.jitter_sec = jitter_sec
        self._clock = clock or time.time
        self._mono = time.monotonic

        self._queue: asyncio.PriorityQueue[_QueuedLookup] = asyncio.PriorityQueue()
        self._pending_keys: set[str] = set()
        self._seq = 0
        self._last_request_mono: float = 0.0
        self._consecutive_errors: int = 0
        self._backoff_level: int = 0
        self._paused_until_wall: float | None = None
        self._paused_until_mono: float | None = None
        self._last_error: str | None = None
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._in_flight: bool = False

    def current_status(self, cache_count: int = 0) -> ProviderStatus:
        now_mono = self._mono()
        is_paused = self._paused_until_mono is not None and now_mono < self._paused_until_mono
        if is_paused:
            state = "paused"
        elif self._in_flight or not self._queue.empty():
            state = "active"
        elif self._last_error:
            state = "error"
        else:
            state = "idle"

        return ProviderStatus(
            name=self.provider.name,
            state=state,
            paused_until=self._paused_until_wall if is_paused else None,
            last_error=self._last_error,
            queue_size=self._queue.qsize(),
            cache_count=cache_count,
        )

    def _emit_status(self) -> None:
        if self.on_status is not None:
            try:
                self.on_status(self.current_status())
            except Exception:
                pass

    def enqueue(
        self,
        canonical_key: str,
        query_nick: str,
        priority: int = 10,
        fallbacks: tuple[str, ...] | list[str] = (),
    ) -> bool:
        """Enqueue a lookup if not already queued/in-flight."""
        if not canonical_key or not query_nick or canonical_key in self._pending_keys:
            return False
        self._pending_keys.add(canonical_key)
        self._seq += 1
        self._queue.put_nowait(
            _QueuedLookup(priority, self._seq, canonical_key, query_nick, tuple(fallbacks))
        )
        self._wake_event.set()
        self._emit_status()
        return True

    def clear_queue(self) -> None:
        """Drop pending lookups (e.g., on match reset)."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break
        self._pending_keys.clear()
        self._emit_status()

    def resume(self) -> None:
        """Manually unpause circuit breaker."""
        self._paused_until_mono = None
        self._paused_until_wall = None
        self._consecutive_errors = 0
        self._backoff_level = 0
        self._last_error = None
        self._wake_event.set()
        self._emit_status()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()

    def _trip_circuit_breaker(self, reason: str) -> None:
        idx = min(self._backoff_level, len(_BACKOFF_STEPS_SEC) - 1)
        delay = _BACKOFF_STEPS_SEC[idx]
        self._backoff_level += 1
        self._paused_until_mono = self._mono() + delay
        self._paused_until_wall = self._clock() + delay
        self._last_error = reason
        logger.warning("Stats provider paused for %.0fs: %s", delay, reason)
        self._emit_status()

    async def process_one(self) -> bool:
        """Process a single queued lookup, obeying circuit breaker and polite rate limit."""
        if self._paused_until_mono is not None:
            remaining = self._paused_until_mono - self._mono()
            if remaining > 0:
                self._emit_status()
                return False
            self._paused_until_mono = None
            self._paused_until_wall = None

        try:
            item = self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return False

        try:
            # Enforce gentle rate limit
            now_mono = self._mono()
            target_gap = self.min_interval_sec + (
                random.uniform(0.0, self.jitter_sec) if self.jitter_sec > 0 else 0.0
            )
            elapsed = now_mono - self._last_request_mono
            if self._last_request_mono > 0 and elapsed < target_gap:
                await asyncio.sleep(target_gap - elapsed)

            if self._stop_event.is_set():
                return False

            self._in_flight = True
            self._emit_status()
            self._last_request_mono = self._mono()
            result = await self.provider.fetch(item.canonical_key, item.query_nick)

            if result.outcome in ("rate_limited", "blocked"):
                self._trip_circuit_breaker(result.error_detail or result.outcome)
                # Re-queue current item at lower priority for retry after cooldown
                self._seq += 1
                self._queue.put_nowait(
                    _QueuedLookup(
                        item.priority + 5,
                        self._seq,
                        item.canonical_key,
                        item.query_nick,
                        item.fallbacks,
                    )
                )
                return True

            if result.outcome == "not_found" and item.fallbacks:
                self._consecutive_errors = 0
                self._backoff_level = 0
                self._last_error = None
                self._seq += 1
                self._queue.put_nowait(
                    _QueuedLookup(
                        item.priority,
                        self._seq,
                        item.canonical_key,
                        item.fallbacks[0],
                        item.fallbacks[1:],
                    )
                )
                return True

            if result.outcome == "error":
                self._consecutive_errors += 1
                self._last_error = result.error_detail
                if self._consecutive_errors >= 3:
                    self._trip_circuit_breaker(result.error_detail or "Repeated provider errors")
                self._pending_keys.discard(item.canonical_key)
            else:
                # ok, not_found, hidden all mean request succeeded cleanly
                self._consecutive_errors = 0
                self._backoff_level = 0
                self._last_error = None
                self._pending_keys.discard(item.canonical_key)

            maybe_coro = self.on_result(item.canonical_key, result)
            if asyncio.iscoroutine(maybe_coro):
                await maybe_coro
            return True
        except Exception as exc:
            logger.error("Unexpected error processing stats lookup for %s: %s", item.query_nick, exc)
            self._pending_keys.discard(item.canonical_key)
            return False
        finally:
            self._in_flight = False
            self._queue.task_done()
            self._emit_status()

    async def run(self) -> None:
        """Main background worker loop."""
        while not self._stop_event.is_set():
            try:
                if self._paused_until_mono is not None:
                    remaining = self._paused_until_mono - self._mono()
                    if remaining > 0:
                        self._wake_event.clear()
                        try:
                            await asyncio.wait_for(self._wake_event.wait(), timeout=min(remaining, 2.0))
                        except asyncio.TimeoutError:
                            pass
                        continue

                processed = await self.process_one()
                if not processed and self._queue.empty():
                    self._wake_event.clear()
                    try:
                        await asyncio.wait_for(self._wake_event.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        pass
            except Exception as exc:
                logger.error("RateLimitedStatsQueue loop error: %s", exc)
                await asyncio.sleep(1.0)
