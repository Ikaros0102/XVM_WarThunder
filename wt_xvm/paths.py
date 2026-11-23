"""Path resolution helpers supporting both development and PyInstaller frozen builds."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def is_frozen() -> bool:
    """Return True when running inside a PyInstaller executable."""
    return bool(getattr(sys, "frozen", False))


def get_bundle_dir() -> Path:
    """Return directory containing bundled read-only assets."""
    if is_frozen() and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS")).resolve()
    return Path(__file__).resolve().parent.parent


def get_app_dir() -> Path:
    """Return writable portable directory next to executable or repo root."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def ensure_runtime_file(rel_path: str) -> Path:
    """Ensure an editable asset exists in app_dir (copying from bundle_dir if needed)."""
    app_target = get_app_dir() / rel_path
    if app_target.exists():
        return app_target

    bundle_source = get_bundle_dir() / rel_path
    if bundle_source.exists():
        app_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundle_source, app_target)
        return app_target

    return app_target


def get_config_path() -> Path:
    """Return path to user configuration JSON."""
    return get_app_dir() / "config.json"


def get_db_path() -> Path:
    """Return path to SQLite statistics cache database."""
    return get_app_dir() / "wt_xvm_cache.db"


def get_log_path() -> Path:
    """Return path to rotating log file."""
    return get_app_dir() / "logs" / "wt_xvm.log"


def get_frontend_dist_dir() -> Path:
    """Return path to built React SPA static files."""
    return get_bundle_dir() / "frontend" / "dist"


def get_ocr_models_dir() -> Path:
    """Return path to bundled or runtime OCR ONNX models."""
    app_models = get_app_dir() / "data" / "ocr_models"
    if app_models.exists():
        return app_models
    return get_bundle_dir() / "data" / "ocr_models"
