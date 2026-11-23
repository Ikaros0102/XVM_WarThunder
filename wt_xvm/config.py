"""Application configuration schema and atomic persistence."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from wt_xvm.paths import get_config_path

logger = logging.getLogger("wt_xvm.config")


class WidgetLayout(BaseModel):
    """Layout configuration for an individual overlay widget."""

    enabled: bool = True
    x: float = 20.0
    y: float = 20.0
    scale: float = Field(default=1.0, ge=0.5, le=2.0)
    opacity: float = Field(default=1.0, ge=0.1, le=1.0)
    anchor_x: Literal["left", "right"] = "left"

    @field_validator("scale", mode="before")
    @classmethod
    def clamp_scale(cls, value: float) -> float:
        try:
            num = float(value)
        except (TypeError, ValueError):
            return 1.0
        return max(0.5, min(2.0, num))

    @field_validator("opacity", mode="before")
    @classmethod
    def clamp_opacity(cls, value: float) -> float:
        try:
            num = float(value)
        except (TypeError, ValueError):
            return 1.0
        return max(0.1, min(1.0, num))


class WidgetsConfig(BaseModel):
    """Per-widget positions, scales, and sub-feature toggles."""

    ally_panel: WidgetLayout = Field(default_factory=lambda: WidgetLayout(enabled=True, x=16.0, y=140.0, scale=1.0))
    enemy_panel: WidgetLayout = Field(
        default_factory=lambda: WidgetLayout(enabled=True, x=16.0, y=140.0, scale=1.0, anchor_x="right")
    )
    air_alert: WidgetLayout = Field(default_factory=lambda: WidgetLayout(enabled=True, x=835.0, y=640.0, scale=1.0))
    enemy_air_counter: WidgetLayout = Field(default_factory=lambda: WidgetLayout(enabled=True, x=845.0, y=24.0, scale=1.0))

    show_fpe: bool = True
    show_nuke_threat: bool = True
    show_disconnect: bool = True
    show_vehicle: bool = True
    show_match_kd: bool = True
    show_wr: bool = True
    show_kd: bool = True
    show_battles: bool = True


class NormalizedRoi(BaseModel):
    """Normalized [0..1] ROI inside a 16:9 safe-frame."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(ge=0.01, le=1.0)
    h: float = Field(ge=0.01, le=1.0)


class OcrRoiConfig(BaseModel):
    """Normalized scoreboard column ROIs for Allies (left) and Enemies (right)."""

    left_nicks: NormalizedRoi = Field(default_factory=lambda: NormalizedRoi(x=0.342, y=0.312, w=0.146, h=0.470))
    right_nicks: NormalizedRoi = Field(default_factory=lambda: NormalizedRoi(x=0.512, y=0.312, w=0.158, h=0.470))


class AppConfig(BaseModel):
    """Root application settings stored in config.json."""

    host: str = "127.0.0.1"
    port: int = 8123
    wt_api_url: str = "http://localhost:8111"
    wt_window_title: str = "War Thunder"

    battle_mode: Literal["rb", "ab", "sb"] = "rb"
    log_language: Literal["auto", "ru", "en"] = "auto"

    air_alert_enabled: bool = True
    air_alert_duration_sec: int = Field(default=10, ge=1, le=15)
    air_alert_style: Literal["rect", "circle"] = "rect"

    tab_debounce_sec: float = Field(default=30.0, ge=1.0, le=120.0)
    tab_hold_delay_ms: int = Field(default=220, ge=50, le=1500)
    min_ocr_players_per_side: int = Field(default=3, ge=1, le=16)

    stats_min_interval_sec: float = Field(default=1.5, ge=0.5, le=10.0)
    stats_jitter_sec: float = Field(default=0.4, ge=0.0, le=2.0)
    stats_cache_ttl_days: int = Field(default=7, ge=1, le=30)

    edit_layout: bool = False
    overlay_visible: bool = True
    toggle_overlay_hotkey: str = "F8"
    debug_mode: bool = False

    widgets: WidgetsConfig = Field(default_factory=WidgetsConfig)
    ocr_roi: OcrRoiConfig = Field(default_factory=OcrRoiConfig)

    @field_validator("toggle_overlay_hotkey", mode="before")
    @classmethod
    def normalize_toggle_hotkey(cls, value: object) -> str:
        raw = str(value or "").strip().upper()
        return raw if raw else "F8"

    @field_validator("air_alert_duration_sec", mode="before")
    @classmethod
    def clamp_air_duration(cls, value: int) -> int:
        try:
            num = int(value)
        except (TypeError, ValueError):
            return 10
        return max(1, min(15, num))


def _is_v1_default_ocr_roi(roi: OcrRoiConfig) -> bool:
    """Return True if roi matches the legacy v1 uncalibrated default ROIs."""
    left, right = roi.left_nicks, roi.right_nicks
    return (
        abs(left.x - 0.145) < 1e-4
        and abs(left.y - 0.255) < 1e-4
        and abs(left.w - 0.195) < 1e-4
        and abs(left.h - 0.495) < 1e-4
        and abs(right.x - 0.530) < 1e-4
        and abs(right.y - 0.255) < 1e-4
        and abs(right.w - 0.195) < 1e-4
        and abs(right.h - 0.495) < 1e-4
    )


def load_config(path: Path | None = None) -> AppConfig:
    """Load config.json resiliently; fall back to defaults on missing/corrupt file."""
    target = path or get_config_path()
    if not target.exists():
        cfg = AppConfig()
        save_config(cfg, target)
        return cfg

    try:
        raw = target.read_text(encoding="utf-8")
        raw_dict = json.loads(raw)
        cfg = AppConfig.model_validate(raw_dict)
        changed = False
        if _is_v1_default_ocr_roi(cfg.ocr_roi):
            cfg.ocr_roi = OcrRoiConfig()
            changed = True
            logger.info("Migrated legacy v1 ocr_roi in %s to calibrated coordinates.", target)
        raw_enemy = (
            raw_dict.get("widgets", {}).get("enemy_panel", {})
            if isinstance(raw_dict, dict)
            else {}
        )
        if isinstance(raw_enemy, dict) and "anchor_x" not in raw_enemy:
            cfg.widgets.enemy_panel.anchor_x = "right"
            if cfg.widgets.enemy_panel.x > 400.0:
                cfg.widgets.enemy_panel.x = 16.0
            changed = True
            logger.info("Migrated enemy_panel in %s to right-edge anchoring (x=%s).", target, cfg.widgets.enemy_panel.x)
        if changed:
            save_config(cfg, target)
        return cfg
    except Exception as exc:
        logger.warning("Failed to parse config %s (%s); using defaults.", target, exc)
        return AppConfig()


def save_config(config: AppConfig, path: Path | None = None) -> None:
    """Atomically write configuration to config.json."""
    target = path or get_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = config.model_dump_json(indent=2)
    fd, tmp_name = tempfile.mkstemp(prefix=".config_", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_name, target)
    except Exception as exc:
        logger.error("Failed to save config to %s: %s", target, exc)
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass
