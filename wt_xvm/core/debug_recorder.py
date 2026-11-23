"""Opt-in debug capture recorder for live /map_info, /hudmsg, /gamechat, Tab OCR, and stats lookups."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from wt_xvm.config import AppConfig
from wt_xvm.paths import get_log_path

logger = logging.getLogger("wt_xvm.core.debug_recorder")


class DebugSessionRecorder:
    """Thread-safe JSONL + PNG recorder gated behind config.debug_mode."""

    def __init__(self, config: AppConfig, base_dir: Path | None = None) -> None:
        self.config = config
        self._base_dir = base_dir or (get_log_path().parent / "debug_sessions")
        self._lock = threading.Lock()
        self._session_dir: Path | None = None
        self._scan_counter: int = 0

    def get_base_dir(self) -> Path:
        return self._base_dir

    def get_session_dir(self) -> Path | None:
        with self._lock:
            return self._session_dir

    def rotate_session(self) -> None:
        """Reset current session directory so the next battle starts a fresh debug folder."""
        with self._lock:
            self._session_dir = None
            self._scan_counter = 0

    def _ensure_session_dir(self) -> Path:
        if self._session_dir is not None:
            return self._session_dir
        self._base_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        candidate = self._base_dir / f"session_{stamp}"
        idx = 1
        while candidate.exists():
            candidate = self._base_dir / f"session_{stamp}_{idx}"
            idx += 1
        candidate.mkdir(parents=True, exist_ok=True)
        self._session_dir = candidate
        self._scan_counter = 0
        logger.info("Debug session directory created: %s", candidate)
        return candidate

    def _append_jsonl(self, filename: str, payload: dict[str, Any]) -> None:
        if not self.config.debug_mode:
            return
        with self._lock:
            try:
                session_dir = self._ensure_session_dir()
                entry = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    **payload,
                }
                line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
                path = session_dir / filename
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(line)
                    fh.flush()
            except Exception as exc:
                logger.debug("Debug recorder failed writing %s: %s", filename, exc)

    def record_map_info(
        self,
        raw: dict[str, Any],
        valid: bool,
        map_generation: int | None,
        action: str,
    ) -> None:
        if not self.config.debug_mode:
            return
        self._append_jsonl(
            "map_info.jsonl",
            {
                "valid": valid,
                "map_generation": map_generation,
                "action": action,
                "raw": raw,
            },
        )

    def record_hud_item(
        self,
        source: str,
        item: dict[str, Any],
        parsed_type: str | None,
        parsed_summary: dict[str, Any] | None,
        applied: bool,
        note: str | None = None,
    ) -> None:
        if not self.config.debug_mode:
            return
        self._append_jsonl(
            "hudmsg_raw.jsonl",
            {
                "source": source,
                "applied": applied,
                "parsed_type": parsed_type,
                "parsed_summary": parsed_summary,
                "note": note,
                "raw": item,
            },
        )

    def record_chat_item(
        self,
        item: dict[str, Any],
        parsed_type: str | None,
        parsed_summary: dict[str, Any] | None,
    ) -> None:
        if not self.config.debug_mode:
            return
        self._append_jsonl(
            "gamechat_raw.jsonl",
            {
                "parsed_type": parsed_type,
                "parsed_summary": parsed_summary,
                "raw": item,
            },
        )

    def record_tab_ocr(
        self,
        payload: dict[str, Any],
        preview_png: bytes | None = None,
        left_img: Image.Image | None = None,
        right_img: Image.Image | None = None,
    ) -> None:
        if not self.config.debug_mode:
            return
        with self._lock:
            try:
                session_dir = self._ensure_session_dir()
                self._scan_counter += 1
                scan_idx = self._scan_counter
                files: dict[str, str] = {}

                if preview_png:
                    preview_name = f"tab_scan_{scan_idx:03d}_preview.png"
                    (session_dir / preview_name).write_bytes(preview_png)
                    files["preview_file"] = preview_name
                if left_img is not None:
                    left_name = f"tab_scan_{scan_idx:03d}_left.png"
                    left_img.save(session_dir / left_name, format="PNG")
                    files["left_crop_file"] = left_name
                if right_img is not None:
                    right_name = f"tab_scan_{scan_idx:03d}_right.png"
                    right_img.save(session_dir / right_name, format="PNG")
                    files["right_crop_file"] = right_name

                entry = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "scan_index": scan_idx,
                    **files,
                    **payload,
                }
                line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
                path = session_dir / "tab_ocr.jsonl"
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(line)
                    fh.flush()
            except Exception as exc:
                logger.debug("Debug recorder failed writing tab_ocr: %s", exc)

    def record_stats_lookup(self, payload: dict[str, Any]) -> None:
        if not self.config.debug_mode:
            return
        self._append_jsonl("stats_lookups.jsonl", payload)
