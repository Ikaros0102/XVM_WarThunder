"""Pluggable statistics providers (default: polite ThunderSkill JSON API)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import quote

import httpx

from wt_xvm.models import ModeStats, PlayerStatsRecord

logger = logging.getLogger("wt_xvm.stats.providers")

FetchOutcome = Literal["ok", "not_found", "hidden", "rate_limited", "blocked", "error"]


@dataclass(slots=True)
class ProviderResult:
    """Normalized fetch response from a StatsProvider."""

    outcome: FetchOutcome
    record: PlayerStatsRecord | None = None
    error_detail: str | None = None


class StatsProvider(Protocol):
    """Interface for external War Thunder player statistics sources."""

    name: str

    async def fetch(self, canonical_key: str, query_nick: str) -> ProviderResult: ...

    async def aclose(self) -> None: ...


def _parse_mode_stats(raw_mode: dict | None) -> ModeStats:
    if not isinstance(raw_mode, dict):
        return ModeStats()
    battles_raw = raw_mode.get("mission")
    try:
        battles = int(battles_raw) if battles_raw is not None else None
    except (TypeError, ValueError):
        battles = None

    if battles is None or battles <= 0:
        return ModeStats(battles=0 if battles == 0 else None)

    def _to_float(val: object) -> float | None:
        try:
            num = float(val)  # type: ignore[arg-type]
            return round(num, 2)
        except (TypeError, ValueError):
            return None

    return ModeStats(
        winrate=_to_float(raw_mode.get("winrate")),
        kd=_to_float(raw_mode.get("kd")),
        battles=battles,
        kpd=_to_float(raw_mode.get("kpd")),
    )


class ThunderSkillProvider:
    """Polite ThunderSkill JSON export client (`https://thunderskill.com/en/stat/{nick}/export/json`)."""

    name = "ThunderSkill"

    def __init__(
        self,
        base_url: str = "https://thunderskill.com/en/stat",
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "WT-XVM-Overlay/1.0 (Personal Tactical HUD)",
                "Accept": "application/json",
            },
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch(self, canonical_key: str, query_nick: str) -> ProviderResult:
        encoded_nick = quote(query_nick, safe="")
        url = f"{self.base_url}/{encoded_nick}/export/json"
        try:
            resp = await self._client.get(url)
        except httpx.TimeoutException as exc:
            return ProviderResult(outcome="error", error_detail=f"Timeout: {exc}")
        except httpx.HTTPError as exc:
            return ProviderResult(outcome="error", error_detail=f"Network error: {exc}")
        except Exception as exc:
            return ProviderResult(outcome="error", error_detail=f"Unexpected error: {exc}")

        if resp.status_code == 404:
            return ProviderResult(
                outcome="not_found",
                record=PlayerStatsRecord(canonical_key=canonical_key, nick=query_nick, status="not_found"),
            )

        if resp.status_code == 403:
            body_lower = resp.text.lower()
            if "hidden" in body_lower:
                return ProviderResult(
                    outcome="hidden",
                    record=PlayerStatsRecord(canonical_key=canonical_key, nick=query_nick, status="hidden"),
                )
            return ProviderResult(
                outcome="blocked",
                error_detail="HTTP 403 (Challenge / WAF)",
            )

        if resp.status_code == 429:
            return ProviderResult(outcome="rate_limited", error_detail="HTTP 429 Too Many Requests")

        if resp.status_code >= 500:
            return ProviderResult(outcome="error", error_detail=f"HTTP {resp.status_code}")

        if resp.status_code != 200:
            return ProviderResult(outcome="error", error_detail=f"Unexpected HTTP {resp.status_code}")

        try:
            payload = resp.json()
            stats = payload.get("stats")
            if not isinstance(stats, dict):
                return ProviderResult(outcome="error", error_detail="Malformed JSON: missing 'stats'")
        except ValueError as exc:
            return ProviderResult(outcome="error", error_detail=f"Invalid JSON: {exc}")

        record = PlayerStatsRecord(
            canonical_key=canonical_key,
            nick=str(stats.get("nick") or query_nick),
            status="ok",
            ab=_parse_mode_stats(stats.get("a")),
            rb=_parse_mode_stats(stats.get("r")),
            sb=_parse_mode_stats(stats.get("s")),
        )
        return ProviderResult(outcome="ok", record=record)
