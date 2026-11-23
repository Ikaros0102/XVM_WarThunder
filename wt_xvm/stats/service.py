"""Coordinator connecting BattleSessionState, SQLite StatsCache, and RateLimitedStatsQueue."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from wt_xvm.config import AppConfig
from wt_xvm.core.battle_state import BattleSessionState
from wt_xvm.models import PlayerState, PlayerStatsRecord, ProviderStatus
from wt_xvm.stats.cache import StatsCache
from wt_xvm.stats.providers import ProviderResult, StatsProvider, ThunderSkillProvider
from wt_xvm.stats.rate_limiter import RateLimitedStatsQueue

if TYPE_CHECKING:
    from wt_xvm.core.debug_recorder import DebugSessionRecorder

logger = logging.getLogger("wt_xvm.stats.service")


def build_candidate_queries(player: PlayerState) -> list[str]:
    """Build ordered ThunderSkill lookup candidates (`@psn`/`@live` and unbracketed graphical tag fallback)."""
    nick = player.display_nick.strip()
    if not nick:
        return []

    if player.platform_hint == "psn":
        return [nick] if nick.lower().endswith("@psn") else [f"{nick}@psn"]
    if player.platform_hint == "live":
        return [nick] if nick.lower().endswith("@live") else [f"{nick}@live"]
    if player.platform_hint == "console":
        base = nick
        for suffix in ("@psn", "@live"):
            if base.lower().endswith(suffix):
                base = base[: -len(suffix)].strip()
                break
        return [f"{base}@psn", f"{base}@live"]

    cands = [nick]
    if not player.exact_nick_verified and " " in nick:
        prefix, rest = nick.split(" ", 1)
        prefix, rest = prefix.strip(), rest.strip()
        if 2 <= len(prefix) <= 6 and len(rest) >= 3:
            cands.append(rest)
    return cands


class StatsService:
    """Orchestrates cached lookups, session anonymizer tracking, and polite background fetching."""

    def __init__(
        self,
        config: AppConfig,
        state: BattleSessionState,
        cache: StatsCache | None = None,
        provider: StatsProvider | None = None,
        on_change: Callable[[], None] | None = None,
        debug_recorder: DebugSessionRecorder | None = None,
    ) -> None:
        self.config = config
        self.state = state
        self.cache = cache or StatsCache()
        self.provider: StatsProvider = provider or ThunderSkillProvider()
        self.on_change = on_change
        self.debug_recorder = debug_recorder

        # Keys already resolved (ok, not_found, hidden) during current battle session
        self._session_resolved: set[str] = set()
        self._session_resolved_hint: dict[str, str | None] = {}
        self._query_nicks: dict[str, str] = {}

        self.queue = RateLimitedStatsQueue(
            provider=self.provider,
            on_result=self._handle_provider_result,
            on_status=self._handle_status_change,
            min_interval_sec=config.stats_min_interval_sec,
            jitter_sec=config.stats_jitter_sec,
        )

        # Clean up expired SQLite rows on startup (TTL = 7 days)
        purged = self.cache.purge_expired(ttl_days=self.config.stats_cache_ttl_days)
        if purged:
            logger.info("Purged %d expired SQLite stats records on startup.", purged)
        self._sync_status()

    def _sync_status(self) -> None:
        status = self.queue.current_status(cache_count=self.cache.count())
        self.state.provider_status = status

    def _handle_status_change(self, status: ProviderStatus) -> None:
        status.cache_count = self.cache.count()
        self.state.provider_status = status
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                pass

    def reset_session(self) -> None:
        """Reset per-session deduplication and pending queue when leaving a battle."""
        self._session_resolved.clear()
        self._session_resolved_hint.clear()
        self._query_nicks.clear()
        self.queue.clear_queue()
        self._sync_status()

    def _record_debug_lookup(
        self,
        source: str,
        canonical_key: str,
        query_nick: str,
        outcome: str,
        record: PlayerStatsRecord | None = None,
        error_detail: str | None = None,
    ) -> None:
        if self.debug_recorder is None or not self.config.debug_mode:
            return
        self.debug_recorder.record_stats_lookup(
            {
                "source": source,
                "canonical_key": canonical_key,
                "query_nick": query_nick,
                "outcome": outcome,
                "error_detail": error_detail,
                "rb_wr": record.rb.winrate if record is not None else None,
                "rb_kd": record.rb.kd if record is not None else None,
                "rb_battles": record.rb.battles if record is not None else None,
            }
        )

    def request_player_stats(self, player: PlayerState) -> None:
        """Resolve player stats from SQLite cache or enqueue a polite external fetch."""
        key = player.canonical_key
        if not key:
            return

        cached = self.cache.get(key, ttl_days=self.config.stats_cache_ttl_days)
        prev_hint = self._session_resolved_hint.get(key)
        hint_upgraded = (
            player.platform_hint in ("console", "psn", "live")
            and prev_hint != player.platform_hint
        )

        if cached is not None:
            console_cached = cached.nick.lower().endswith(("@psn", "@live"))
            needs_console_retry = (
                player.platform_hint in ("console", "psn", "live")
                and cached.status == "not_found"
                and not console_cached
            )
            if cached.status == "ok" or (not needs_console_retry and not hint_upgraded):
                self.state.apply_stats_result(cached)
                if key not in self._session_resolved:
                    self._record_debug_lookup(
                        source="cache",
                        canonical_key=key,
                        query_nick=cached.nick or player.display_nick,
                        outcome=cached.status,
                        record=cached,
                    )
                self._session_resolved.add(key)
                self._session_resolved_hint[key] = player.platform_hint
                self._sync_status()
                return

        if key in self._session_resolved and not hint_upgraded:
            return
        if hint_upgraded:
            self._session_resolved.discard(key)

        candidates = build_candidate_queries(player)
        if not candidates:
            return

        # Prioritize team-assigned players over unassigned log players, and exact log nicks over unverified OCR
        priority = 1 if player.team in ("ally", "enemy") else 5
        if not player.exact_nick_verified:
            priority += 2

        self._session_resolved_hint[key] = player.platform_hint
        self._query_nicks[key] = " | ".join(candidates)
        self.queue.enqueue(key, candidates[0], priority=priority, fallbacks=candidates[1:])
        self._sync_status()

    async def _handle_provider_result(self, canonical_key: str, result: ProviderResult) -> None:
        query_nick = self._query_nicks.get(
            canonical_key, result.record.nick if result.record is not None else canonical_key
        )
        self._record_debug_lookup(
            source="thunderskill",
            canonical_key=canonical_key,
            query_nick=query_nick,
            outcome=result.outcome,
            record=result.record,
            error_detail=result.error_detail,
        )
        if result.record is not None:
            if result.outcome in ("ok", "not_found", "hidden"):
                self.cache.upsert(result.record)
                self._session_resolved.add(canonical_key)
                self.state.apply_stats_result(result.record)
        elif result.outcome == "error":
            # Mark error on player temporarily without polluting persistent SQLite cache
            self.state.apply_stats_result(
                PlayerStatsRecord(
                    canonical_key=canonical_key,
                    nick=canonical_key,
                    status="error",
                )
            )
            self._session_resolved.add(canonical_key)

        self._sync_status()
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                pass

    def resume(self) -> None:
        self.queue.resume()
        self._sync_status()

    def clear_cache(self) -> None:
        self.cache.clear()
        self._session_resolved.clear()
        self._session_resolved_hint.clear()
        self._sync_status()

    async def run(self) -> None:
        await self.queue.run()

    async def stop(self) -> None:
        self.queue.stop()
        await self.provider.aclose()
