"""Core Pydantic domain models for WT-XVM."""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field

TeamSide = Literal["ally", "enemy", "unknown"]
BattleMode = Literal["rb", "ab", "sb"]
VehicleKind = Literal["plane", "helicopter", "recon_drone", "ground", "unknown"]
StatsStatus = Literal["pending", "ok", "not_found", "hidden", "error"]
PlatformHint = Literal["psn", "live", "console", None]


class ModeStats(BaseModel):
    """Aggregated player stats for one battle mode (AB/RB/SB)."""

    winrate: float | None = None
    kd: float | None = None
    battles: int | None = None
    kpd: float | None = None


class PlayerStatsRecord(BaseModel):
    """Full cached statistics record across AB, RB, and SB modes."""

    canonical_key: str
    nick: str
    status: StatsStatus = "pending"
    ab: ModeStats = Field(default_factory=ModeStats)
    rb: ModeStats = Field(default_factory=ModeStats)
    sb: ModeStats = Field(default_factory=ModeStats)
    updated_at: str | None = None


class PlayerIdentity(BaseModel):
    """Parsed player nickname and clan/console metadata."""

    raw: str
    clan_tag: str | None = None
    clean_nick: str
    console_prefix: str | None = None
    platform_hint: PlatformHint = None
    canonical_key: str


class PlayerState(BaseModel):
    """Mutable per-player tactical state for the active match."""

    canonical_key: str
    display_nick: str
    clan_tag: str | None = None
    console_prefix: str | None = None
    platform_hint: PlatformHint = None
    team: TeamSide = "unknown"
    exact_nick_verified: bool = False
    is_placeholder: bool = False
    current_vehicle: str | None = None
    current_vehicle_kind: VehicleKind = "unknown"
    is_alive: bool = True
    kills: int = 0
    deaths: int = 0
    fpe_count: int = 2
    last_fire_time: float | None = None
    last_death_time: float | None = None
    last_dead_vehicle: str | None = None
    disconnected: bool = False
    nuke_threat: bool = False
    stats: PlayerStatsRecord | None = None


class AirAlertState(BaseModel):
    """Active Air Alert widget payload."""

    azimuth: int
    altitude: int
    sender: str
    triggered_at: float
    expires_at: float


class EnemyAirSummary(BaseModel):
    """Derived count and model lists of active enemy aircraft in the air."""

    planes: int = 0
    helicopters: int = 0
    plane_list: list[str] = Field(default_factory=list)
    helicopter_list: list[str] = Field(default_factory=list)


class ProviderStatus(BaseModel):
    """Health & rate-limiter state of the external stats provider."""

    name: str = "ThunderSkill"
    state: Literal["idle", "active", "paused", "error"] = "idle"
    paused_until: float | None = None
    last_error: str | None = None
    queue_size: int = 0
    cache_count: int = 0


class OcrStatus(BaseModel):
    """Telemetry from the Tab OCR scanner."""

    last_scan_at: float | None = None
    cooldown_remaining: float = 0.0
    last_allies_count: int = 0
    last_enemies_count: int = 0
    last_error: str | None = None


class SessionSnapshot(BaseModel):
    """Full WebSocket / REST snapshot pushed to Overlay & Second Screen UIs."""

    battle_active: bool = False
    wt_connected: bool = False
    mock_mode: bool = False
    battle_mode: BattleMode = "rb"
    allies: list[PlayerState] = Field(default_factory=list)
    enemies: list[PlayerState] = Field(default_factory=list)
    unassigned_count: int = 0
    air_alert: AirAlertState | None = None
    enemy_air: EnemyAirSummary = Field(default_factory=EnemyAirSummary)
    provider_status: ProviderStatus = Field(default_factory=ProviderStatus)
    ocr_status: OcrStatus = Field(default_factory=OcrStatus)
