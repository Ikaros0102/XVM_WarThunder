"""Tests for SQLite 7-day TTL cache, polite rate limiter, circuit breaker, and anonymizer handling."""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from wt_xvm.config import AppConfig
from wt_xvm.core.battle_state import BattleSessionState
from wt_xvm.core.vehicle_db import VehicleDB
from wt_xvm.models import ModeStats, PlayerStatsRecord
from wt_xvm.stats.cache import StatsCache
from wt_xvm.stats.providers import ThunderSkillProvider
from wt_xvm.stats.service import StatsService

SAMPLE_TS_JSON = {
    "stats": {
        "nick": "Ikaros_02",
        "a": {"winrate": 52.31, "kd": 1.6, "mission": 843, "kpd": 65.43},
        "r": {"winrate": 59.16, "kd": 2.4, "mission": 18260, "kpd": 74.95},
        "s": {"winrate": 0, "kd": 0, "mission": 1, "kpd": 0},
    }
}


def test_sqlite_cache_ttl_and_mode_storage(tmp_path):
    cache = StatsCache(tmp_path / "cache.db")
    now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)

    fresh = PlayerStatsRecord(
        canonical_key="ikaros_02",
        nick="Ikaros_02",
        status="ok",
        ab=ModeStats(winrate=52.31, kd=1.6, battles=843, kpd=65.43),
        rb=ModeStats(winrate=59.16, kd=2.4, battles=18260, kpd=74.95),
        sb=ModeStats(winrate=0.0, kd=0.0, battles=1, kpd=0.0),
        updated_at=(now - timedelta(days=3)).isoformat(),
    )
    stale = PlayerStatsRecord(
        canonical_key="old_player",
        nick="Old_Player",
        status="ok",
        updated_at=(now - timedelta(days=8)).isoformat(),
    )

    cache.upsert(fresh)
    cache.upsert(stale)

    assert cache.get("ikaros_02", ttl_days=7, now=now) is not None
    assert cache.get("ikaros_02", ttl_days=7, now=now).rb.winrate == 59.16
    assert cache.get("ikaros_02", ttl_days=7, now=now).ab.battles == 843
    assert cache.get("old_player", ttl_days=7, now=now) is None

    purged = cache.purge_expired(ttl_days=7, now=now)
    assert purged == 1
    assert cache.count() == 1


@pytest.mark.asyncio
async def test_stats_service_polite_queue_anonymizer_and_circuit_breaker(tmp_path):
    calls: list[str] = []
    mode_flag = {"behaviour": "normal"}

    def handler(request: httpx.Request) -> httpx.Response:
        nick = request.url.path.split("/stat/")[1].split("/export")[0]
        calls.append(nick)
        if mode_flag["behaviour"] == "429":
            return httpx.Response(429, text="rate limited")
        if nick == "Ikaros_02":
            return httpx.Response(200, json=SAMPLE_TS_JSON)
        if nick == "HiddenGuy":
            return httpx.Response(403, text="player profile is hidden")
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    provider = ThunderSkillProvider(client=client)

    cfg = AppConfig(stats_min_interval_sec=0.5, stats_jitter_sec=0.0)
    state = BattleSessionState(cfg, VehicleDB())
    service = StatsService(cfg, state, cache=StatsCache(tmp_path / "c.db"), provider=provider)
    service.queue.min_interval_sec = 0.01
    state.set_stats_enqueue_callback(service.request_player_stats)

    # Enqueue players via Tab OCR
    state.apply_tab_ocr(["Ikaros_02", "HiddenGuy"], ["AnonPlayer"])
    # Duplicate enqueue in same session must NOT trigger second request
    service.request_player_stats(state.players["anonplayer"])

    for _ in range(3):
        await service.queue.process_one()

    assert calls == ["Ikaros_02", "HiddenGuy", "AnonPlayer"]
    assert state.players["ikaros_02"].stats.status == "ok"
    assert state.players["ikaros_02"].stats.rb.winrate == 59.16
    assert state.players["ikaros_02"].stats.ab.winrate == 52.31
    assert state.players["hiddenguy"].stats.status == "hidden"
    assert state.players["anonplayer"].stats.status == "not_found"

    # AnonPlayer must NOT be retried in the same session
    service.request_player_stats(state.players["anonplayer"])
    assert service.queue.current_status().queue_size == 0

    # Test circuit breaker on HTTP 429
    mode_flag["behaviour"] = "429"
    state.apply_tab_ocr(["PlayerFour"], [])
    await service.queue.process_one()
    status = service.queue.current_status()
    assert status.state == "paused"
    assert status.paused_until is not None
    assert "429" in (status.last_error or "")

    await service.stop()


@pytest.mark.asyncio
async def test_stats_service_multi_candidate_console_and_graphical_tag_fallbacks(tmp_path):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nick = httpx.URL(str(request.url)).path.split("/stat/")[1].split("/export")[0]
        from urllib.parse import unquote

        decoded = unquote(nick)
        calls.append(decoded)
        if decoded in ("NickConsole@live", "CoolestTable@psn", "Crashhardgamer"):
            return httpx.Response(200, json=SAMPLE_TS_JSON)
        return httpx.Response(404, text="not found")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = ThunderSkillProvider(client=client)
    cfg = AppConfig(stats_min_interval_sec=0.5, stats_jitter_sec=0.0)
    state = BattleSessionState(cfg, VehicleDB())
    service = StatsService(cfg, state, cache=StatsCache(tmp_path / "fb.db"), provider=provider)
    service.queue.min_interval_sec = 0.01
    state.set_stats_enqueue_callback(service.request_player_stats)

    # 1. Tab OCR unbracketed graphical clan tag ("C439 Crashhardgamer") -> tries full (404), then "Crashhardgamer" (200)
    state.apply_tab_ocr(["C439 Crashhardgamer", "CoolestTable"], [])
    for _ in range(3):
        await service.queue.process_one()
    assert "C439 Crashhardgamer" in calls and "Crashhardgamer" in calls
    assert state.players["c439 crashhardgamer"].stats.status == "ok"
    # Bare CoolestTable without console hint returned 404
    assert state.players["coolesttable"].stats.status == "not_found"

    # 2. HUD event reveals ⋇CoolestTable (console) -> automatically re-enqueues and resolves CoolestTable@psn!
    from wt_xvm.core.log_parser import KillEvent
    from wt_xvm.core.nick_cleaner import parse_player_identity

    state.apply_hud_event(
        KillEvent(
            15.0,
            parse_player_identity("⋇CoolestTable"),
            "Turm III",
            parse_player_identity("⋇NickConsole"),
            "M3 Bradley",
        )
    )
    for _ in range(4):
        await service.queue.process_one()

    assert "CoolestTable@psn" in calls
    assert state.players["coolesttable"].stats.status == "ok"
    # Direct console log player NickConsole tries @psn (404) -> falls back to @live (200)
    assert "NickConsole@psn" in calls and "NickConsole@live" in calls
    assert state.players["nickconsole"].stats.status == "ok"

    await service.stop()
