"""SQLite storage: every job seen, its prefilter result, its score, and per-source fetch times."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    url_key TEXT NOT NULL UNIQUE,
    company_title_key TEXT NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT,
    url TEXT NOT NULL,
    description TEXT,
    posted_at TEXT,
    remote INTEGER,
    salary TEXT,
    first_seen_at TEXT NOT NULL,
    prefilter_passed INTEGER,
    prefilter_reason TEXT,
    score INTEGER,
    fits_location INTEGER,
    seniority TEXT,
    reason TEXT,
    red_flags TEXT,
    scored_at TEXT,
    score_model TEXT,
    score_attempts INTEGER NOT NULL DEFAULT 0,
    last_score_error TEXT,
    notified_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_company_title ON jobs(company_title_key);
CREATE INDEX IF NOT EXISTS idx_jobs_to_score ON jobs(prefilter_passed, score);

CREATE TABLE IF NOT EXISTS llm_spend (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    model TEXT NOT NULL,
    calls INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL,
    cache_write_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS source_runs (
    source TEXT PRIMARY KEY,
    last_fetch_at TEXT,
    last_ok_at TEXT,
    last_count INTEGER,
    last_error TEXT
);
"""


def _utc(value: datetime) -> str:
    """Timestamps are stored and compared as UTC ISO strings, so text comparison matches time order."""
    return value.astimezone(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # The Telegram bot reads while a run writes: wait for locks, and use WAL so readers never block writers.
        self.conn = sqlite3.connect(str(path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        if str(path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after the first release (existing databases keep their data)."""
        for table, new_columns in [
            ("source_runs", [("fail_streak", "INTEGER NOT NULL DEFAULT 0"), ("disabled_at", "TEXT"),
                             ("disabled_reason", "TEXT")]),
            # your feedback and application tracking
            ("jobs", [("feedback", "TEXT"), ("feedback_at", "TEXT"), ("status", "TEXT"), ("status_at", "TEXT"),
                      ("notes", "TEXT"), ("salary_usd_low", "REAL"), ("salary_usd_month", "REAL"),
                      ("closed_at", "TEXT"), ("batch_id", "TEXT")]),
        ]:
            columns = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, ddl in new_columns:
                if name not in columns:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- jobs -------------------------------------------------------------
    def insert_new_jobs(self, jobs: Iterable[Job], now: datetime) -> list[tuple[int, Job]]:
        """Insert jobs not seen before (by normalized URL or company+title). Returns the new ones."""
        new: list[tuple[int, Job]] = []
        with self.conn:
            for job in jobs:
                url_key, ct_key = job.url_key, job.company_title_key
                if not url_key or not job.title:
                    continue
                exists = self.conn.execute(
                    "SELECT 1 FROM jobs WHERE url_key = ? OR company_title_key = ? LIMIT 1", (url_key, ct_key)
                ).fetchone()
                if exists:
                    continue
                cursor = self.conn.execute(
                    """INSERT INTO jobs (url_key, company_title_key, source, external_id, title, company, location,
                       url, description, posted_at, remote, salary, first_seen_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        url_key, ct_key, job.source, job.external_id, job.title, job.company, job.location,
                        job.url, job.description, job.posted_at,
                        None if job.remote is None else int(bool(job.remote)), job.salary, now.isoformat(),
                    ),
                )
                new.append((cursor.lastrowid, job))
        return new

    def set_description(self, job_id: int, description: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE jobs SET description = ? WHERE id = ?", (description, job_id))

    def set_prefilter(self, job_id: int, passed: bool, reason: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE jobs SET prefilter_passed = ?, prefilter_reason = ? WHERE id = ?",
                (int(passed), reason, job_id),
            )

    def jobs_to_score(self, max_attempts: int, limit: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT * FROM jobs WHERE prefilter_passed = 1 AND score IS NULL AND score_attempts < ? AND batch_id IS NULL
               ORDER BY first_seen_at DESC, id DESC LIMIT ?""",
            (max_attempts, limit),
        ).fetchall()

    def count_pending_scores(self, max_attempts: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE prefilter_passed = 1 AND score IS NULL AND score_attempts < ?",
            (max_attempts,),
        ).fetchone()[0]

    def save_score(self, job_id: int, result: dict[str, Any], model: str, now: datetime) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE jobs SET score = ?, fits_location = ?, seniority = ?, reason = ?, red_flags = ?,
                   scored_at = ?, score_model = ?, score_attempts = score_attempts + 1, last_score_error = NULL
                   WHERE id = ?""",
                (
                    int(result["score"]), int(bool(result["fits_location"])), result.get("seniority"),
                    result.get("reason"), json.dumps(result.get("red_flags") or [], ensure_ascii=False),
                    now.isoformat(), model, job_id,
                ),
            )

    def fill_salary_from_llm(self, job_id: int, usd_month: float | None) -> None:
        if usd_month:
            with self.conn:
                self.conn.execute(
                    "UPDATE jobs SET salary_usd_month = ? WHERE id = ? AND salary_usd_month IS NULL", (usd_month, job_id)
                )

    def record_score_failure(self, job_id: int, error: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE jobs SET score_attempts = score_attempts + 1, last_score_error = ? WHERE id = ?",
                (error[:500], job_id),
            )

    def jobs_to_notify(
        self, min_score: int, require_location_fit: bool, min_salary_usd_month: float | None = None
    ) -> list[sqlite3.Row]:
        sql = "SELECT * FROM jobs WHERE notified_at IS NULL AND closed_at IS NULL AND score IS NOT NULL AND score >= ?"
        args: list[Any] = [min_score]
        if require_location_fit:
            sql += " AND fits_location = 1"
        if min_salary_usd_month:
            # unknown salary (NULL) always passes; only a salary clearly below the minimum is held back
            sql += " AND (salary_usd_month IS NULL OR salary_usd_month >= ?)"
            args.append(min_salary_usd_month)
        return self.conn.execute(sql + " ORDER BY score DESC, id DESC", args).fetchall()

    def mark_notified(self, job_ids: Iterable[int], now: datetime) -> None:
        with self.conn:
            self.conn.executemany(
                "UPDATE jobs SET notified_at = ? WHERE id = ?", [(now.isoformat(), i) for i in job_ids]
            )

    def top(self, days: int, limit: int, now: datetime) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM jobs WHERE score IS NOT NULL AND first_seen_at >= ? ORDER BY score DESC, id DESC LIMIT ?",
            (_utc(now - timedelta(days=days)), limit),
        ).fetchall()

    # --- your feedback and applications -------------------------------------
    FEEDBACK = ("like", "dislike")
    STATUSES = ("applied", "interview", "offer", "rejected")

    def set_feedback(self, job_id: int, feedback: str | None, now: datetime) -> bool:
        if feedback is not None and feedback not in self.FEEDBACK:
            raise ValueError(f"unknown feedback {feedback!r}")
        with self.conn:
            cursor = self.conn.execute(
                "UPDATE jobs SET feedback = ?, feedback_at = ? WHERE id = ?",
                (feedback, _utc(now) if feedback else None, job_id),
            )
        return cursor.rowcount > 0

    def set_status(self, job_id: int, status: str | None, now: datetime, notes: str | None = None) -> bool:
        if status is not None and status not in self.STATUSES:
            raise ValueError(f"unknown status {status!r}")
        with self.conn:
            cursor = self.conn.execute(
                "UPDATE jobs SET status = ?, status_at = ?, notes = COALESCE(?, notes) WHERE id = ?",
                (status, _utc(now) if status else None, notes, job_id),
            )
        return cursor.rowcount > 0

    def set_batch(self, job_ids: list[int], batch_id: str) -> None:
        with self.conn:
            self.conn.executemany("UPDATE jobs SET batch_id = ? WHERE id = ?", [(batch_id, i) for i in job_ids])

    def clear_batch(self, batch_id: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE jobs SET batch_id = NULL WHERE batch_id = ?", (batch_id,))

    def pending_batch_ids(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT DISTINCT batch_id FROM jobs WHERE batch_id IS NOT NULL")]

    def set_salary_usd(self, job_id: int, low: float | None, high: float | None) -> None:
        """Normalized salary in USD per month (high = top of the range, used by the minimum-salary rule)."""
        with self.conn:
            self.conn.execute("UPDATE jobs SET salary_usd_low = ?, salary_usd_month = ? WHERE id = ?", (low, high, job_id))

    def mark_closed(self, job_id: int, now: datetime) -> None:
        with self.conn:
            self.conn.execute("UPDATE jobs SET closed_at = ? WHERE id = ?", (_utc(now), job_id))

    def job(self, job_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    def applications(self, include_closed: bool = False) -> list[sqlite3.Row]:
        sql = "SELECT * FROM jobs WHERE status IS NOT NULL"
        if not include_closed:
            sql += " AND status IN ('applied', 'interview', 'offer')"
        return self.conn.execute(sql + " ORDER BY status_at DESC").fetchall()

    def feedback_examples(self, limit: int) -> dict[str, list[sqlite3.Row]]:
        """Recent jobs you liked (or applied to) and disliked, used as examples in the scoring prompt."""
        liked = self.conn.execute(
            """SELECT * FROM jobs WHERE feedback = 'like' OR status IS NOT NULL
               ORDER BY COALESCE(feedback_at, status_at) DESC LIMIT ?""", (limit,)).fetchall()
        disliked = self.conn.execute(
            "SELECT * FROM jobs WHERE feedback = 'dislike' ORDER BY feedback_at DESC LIMIT ?", (limit,)).fetchall()
        return {"liked": liked, "disliked": disliked}

    # --- queries for the Telegram bot ---------------------------------------
    def latest_matches(self, min_score: int, require_location_fit: bool, limit: int) -> list[sqlite3.Row]:
        sql = "SELECT * FROM jobs WHERE score IS NOT NULL AND score >= ?"
        if require_location_fit:
            sql += " AND fits_location = 1"
        return self.conn.execute(sql + " ORDER BY scored_at DESC, id DESC LIMIT ?", (min_score, limit)).fetchall()

    def latest_scored(self, limit: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM jobs WHERE score IS NOT NULL ORDER BY scored_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()

    def stats_since(self, since: datetime, min_score: int = 70) -> dict[str, int]:
        start = _utc(since)
        row = self.conn.execute(
            """SELECT
                 (SELECT COUNT(*) FROM jobs WHERE first_seen_at >= :s) AS new,
                 (SELECT COUNT(*) FROM jobs WHERE first_seen_at >= :s AND prefilter_passed = 1) AS passed,
                 (SELECT COUNT(*) FROM jobs WHERE scored_at >= :s) AS scored,
                 (SELECT COUNT(*) FROM jobs WHERE scored_at >= :s AND score >= :m) AS matches,
                 (SELECT COUNT(*) FROM jobs WHERE notified_at >= :s) AS sent""",
            {"s": start, "m": min_score},
        ).fetchone()
        return dict(row)

    def jobs_first_seen_since(self, since: datetime) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT id, source, title, company, url, location, description, score, fits_location, red_flags,
                      prefilter_passed, prefilter_reason
               FROM jobs WHERE first_seen_at >= ?""",
            (_utc(since),),
        ).fetchall()

    def source_status(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM source_runs ORDER BY source").fetchall()

    # --- source pacing ----------------------------------------------------
    def last_success(self, source: str) -> datetime | None:
        """Pacing uses the last successful fetch, so a failed source is retried on the next run."""
        row = self.conn.execute("SELECT last_ok_at FROM source_runs WHERE source = ?", (source,)).fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    def record_fetch(self, source: str, now: datetime, count: int, error: str | None) -> int:
        """Record a fetch; returns the number of consecutive failures (0 after a success)."""
        with self.conn:
            self.conn.execute(
                """INSERT INTO source_runs (source, last_fetch_at, last_ok_at, last_count, last_error, fail_streak)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source) DO UPDATE SET last_fetch_at = excluded.last_fetch_at,
                     last_ok_at = COALESCE(excluded.last_ok_at, source_runs.last_ok_at),
                     last_count = excluded.last_count, last_error = excluded.last_error,
                     fail_streak = CASE WHEN excluded.last_error IS NULL THEN 0 ELSE source_runs.fail_streak + 1 END""",
                (source, now.isoformat(), None if error else now.isoformat(), count, error, 1 if error else 0),
            )
        return int(self.conn.execute("SELECT fail_streak FROM source_runs WHERE source = ?", (source,)).fetchone()[0])

    def source_state(self, source: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM source_runs WHERE source = ?", (source,)).fetchone()

    def disable_source(self, source: str, now: datetime, reason: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE source_runs SET disabled_at = ?, disabled_reason = ? WHERE source = ?",
                (now.isoformat(), reason[:300], source),
            )

    def enable_source(self, source: str) -> bool:
        """Clear an automatic disable. Returns True if the source was disabled."""
        with self.conn:
            cursor = self.conn.execute(
                "UPDATE source_runs SET disabled_at = NULL, disabled_reason = NULL, fail_streak = 0 "
                "WHERE source = ? AND disabled_at IS NOT NULL",
                (source,),
            )
        return cursor.rowcount > 0

    # --- Claude spend (estimated from token usage) -------------------------
    def record_llm_spend(self, now: datetime, model: str, usage: Any, cost_usd: float) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO llm_spend (at, model, calls, input_tokens, output_tokens, cache_read_tokens,
                   cache_write_tokens, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _utc(now), model, usage.calls, usage.input_tokens, usage.output_tokens,
                    usage.cache_read_tokens, usage.cache_write_tokens, cost_usd,
                ),
            )

    def spend_between(self, start: datetime, end: datetime | None = None) -> float:
        sql, args = "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_spend WHERE at >= ?", [_utc(start)]
        if end is not None:
            sql += " AND at < ?"
            args.append(_utc(end))
        return float(self.conn.execute(sql, args).fetchone()[0])

    # --- small key/value state ---------------------------------------------
    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
