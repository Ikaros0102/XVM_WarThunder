"""SQLite statistics cache with 7-day TTL and full AB/RB/SB mode storage."""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from wt_xvm.models import ModeStats, PlayerStatsRecord
from wt_xvm.paths import get_db_path

logger = logging.getLogger("wt_xvm.stats.cache")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS player_stats (
    canonical_key TEXT PRIMARY KEY,
    nick TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    ab_wr REAL,
    ab_kd REAL,
    ab_battles INTEGER,
    ab_kpd REAL,
    rb_wr REAL,
    rb_kd REAL,
    rb_battles INTEGER,
    rb_kpd REAL,
    sb_wr REAL,
    sb_kd REAL,
    sb_battles INTEGER,
    sb_kpd REAL
);
CREATE INDEX IF NOT EXISTS idx_player_stats_updated_at ON player_stats(updated_at);
"""


def _utcnow_iso(dt: datetime | None = None) -> str:
    val = dt or datetime.now(timezone.utc)
    return val.astimezone(timezone.utc).isoformat()


def _parse_iso(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


class StatsCache:
    """Thread-safe SQLite store for player statistics across AB/RB/SB."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or get_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute("PRAGMA journal_mode=WAL;")
                    conn.executescript(_SCHEMA_SQL)
            except Exception as exc:
                logger.error("Failed to initialize SQLite cache %s: %s", self.db_path, exc)

    def purge_expired(
        self,
        ttl_days: int = 7,
        negative_ttl_hours: int = 24,
        now: datetime | None = None,
    ) -> int:
        """Delete stale records older than ttl_days (or negative_ttl_hours for 404/hidden)."""
        ref_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        ok_cutoff = (ref_now - timedelta(days=ttl_days)).isoformat()
        neg_cutoff = (ref_now - timedelta(hours=negative_ttl_hours)).isoformat()

        deleted = 0
        with self._lock:
            try:
                with self._connect() as conn:
                    cur1 = conn.execute(
                        "DELETE FROM player_stats WHERE updated_at < ?",
                        (ok_cutoff,),
                    )
                    cur2 = conn.execute(
                        "DELETE FROM player_stats WHERE status IN ('not_found', 'hidden') AND updated_at < ?",
                        (neg_cutoff,),
                    )
                    deleted = (cur1.rowcount or 0) + (cur2.rowcount or 0)
            except Exception as exc:
                logger.error("Failed to purge expired SQLite cache entries: %s", exc)
        return deleted

    def get(
        self,
        canonical_key: str,
        ttl_days: int = 7,
        negative_ttl_hours: int = 24,
        now: datetime | None = None,
    ) -> PlayerStatsRecord | None:
        """Fetch fresh cached record or None if missing/expired."""
        if not canonical_key:
            return None
        ref_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

        with self._lock:
            try:
                with self._connect() as conn:
                    row = conn.execute(
                        "SELECT * FROM player_stats WHERE canonical_key = ?",
                        (canonical_key,),
                    ).fetchone()
            except Exception as exc:
                logger.error("SQLite read error for %s: %s", canonical_key, exc)
                return None

        if row is None:
            return None

        updated_dt = _parse_iso(row["updated_at"])
        if updated_dt is None:
            return None

        status = str(row["status"])
        max_age = (
            timedelta(hours=negative_ttl_hours)
            if status in ("not_found", "hidden")
            else timedelta(days=ttl_days)
        )
        if (ref_now - updated_dt) > max_age:
            return None

        return PlayerStatsRecord(
            canonical_key=row["canonical_key"],
            nick=row["nick"],
            status=status,  # type: ignore[arg-type]
            updated_at=row["updated_at"],
            ab=ModeStats(
                winrate=row["ab_wr"],
                kd=row["ab_kd"],
                battles=row["ab_battles"],
                kpd=row["ab_kpd"],
            ),
            rb=ModeStats(
                winrate=row["rb_wr"],
                kd=row["rb_kd"],
                battles=row["rb_battles"],
                kpd=row["rb_kpd"],
            ),
            sb=ModeStats(
                winrate=row["sb_wr"],
                kd=row["sb_kd"],
                battles=row["sb_battles"],
                kpd=row["sb_kpd"],
            ),
        )

    def upsert(self, record: PlayerStatsRecord, updated_at: datetime | None = None) -> None:
        """Insert or update a player statistics record."""
        ts = record.updated_at or _utcnow_iso(updated_at)
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO player_stats (
                            canonical_key, nick, status, updated_at,
                            ab_wr, ab_kd, ab_battles, ab_kpd,
                            rb_wr, rb_kd, rb_battles, rb_kpd,
                            sb_wr, sb_kd, sb_battles, sb_kpd
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(canonical_key) DO UPDATE SET
                            nick = excluded.nick,
                            status = excluded.status,
                            updated_at = excluded.updated_at,
                            ab_wr = excluded.ab_wr,
                            ab_kd = excluded.ab_kd,
                            ab_battles = excluded.ab_battles,
                            ab_kpd = excluded.ab_kpd,
                            rb_wr = excluded.rb_wr,
                            rb_kd = excluded.rb_kd,
                            rb_battles = excluded.rb_battles,
                            rb_kpd = excluded.rb_kpd,
                            sb_wr = excluded.sb_wr,
                            sb_kd = excluded.sb_kd,
                            sb_battles = excluded.sb_battles,
                            sb_kpd = excluded.sb_kpd
                        """,
                        (
                            record.canonical_key,
                            record.nick,
                            record.status,
                            ts,
                            record.ab.winrate,
                            record.ab.kd,
                            record.ab.battles,
                            record.ab.kpd,
                            record.rb.winrate,
                            record.rb.kd,
                            record.rb.battles,
                            record.rb.kpd,
                            record.sb.winrate,
                            record.sb.kd,
                            record.sb.battles,
                            record.sb.kpd,
                        ),
                    )
            except Exception as exc:
                logger.error("SQLite upsert error for %s: %s", record.canonical_key, exc)

    def count(self) -> int:
        """Return total cached rows."""
        with self._lock:
            try:
                with self._connect() as conn:
                    row = conn.execute("SELECT COUNT(*) AS cnt FROM player_stats").fetchone()
                    return int(row["cnt"]) if row else 0
            except Exception:
                return 0

    def clear(self) -> None:
        """Remove all cached rows on explicit user request."""
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute("DELETE FROM player_stats")
            except Exception as exc:
                logger.error("Failed to clear SQLite cache: %s", exc)
