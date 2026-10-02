"""Keeping the radar healthy: queued alerts, auto-disabling broken sources, backups and the "radar stopped" check.

Config (all optional, in config.yaml):

    health:
      disable_source_after_failures: 6   # consecutive failed fetches before a source is switched off
      retry_disabled_every_hours: 24     # a disabled source is retried this often; it comes back on success
      stale_after_hours: 6               # the bot warns if no healthy search finished in this long
      ping_url:                          # optional dead-man's switch (e.g. https://hc-ping.com/<uuid>)
    backup:
      every_days: 7
      keep: 4
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

import httpx

from . import telegram
from .config import Settings, secret
from .db import Database
from .util import USER_AGENT, short_error

log = logging.getLogger("job_radar.health")

DEFAULTS = {
    "disable_source_after_failures": 6,
    "retry_disabled_every_hours": 24,
    "stale_after_hours": 6,
    "ping_url": None,
}


def health_cfg(settings: Settings) -> dict[str, Any]:
    return {**DEFAULTS, **(settings.raw.get("health") or {})}


# --- sending -------------------------------------------------------------------------------------------------
def deliver(text: str, dry_run: bool = False, out: Callable[[str], None] = print) -> bool:
    """Send a Telegram message (print it on dry-run or without credentials). Returns True if it was sent."""
    token, chat_id = secret("TELEGRAM_BOT_TOKEN"), secret("TELEGRAM_CHAT_ID")
    if dry_run or not token or not chat_id:
        out(text)
        return False
    try:
        telegram.send_message(token, chat_id, text)
    except telegram.TelegramError as exc:
        log.error("could not send message to Telegram: %s", exc)
        return False
    return True


def queue_alert(db: Database, text: str) -> None:
    """Hold an alert until the next run inside the notify window (duplicates are dropped)."""
    pending = json.loads(db.get_meta("pending_alerts") or "[]")
    if text not in pending:
        pending.append(text)
        db.set_meta("pending_alerts", json.dumps(pending[-20:], ensure_ascii=False))


def flush_alerts(db: Database, dry_run: bool = False, out: Callable[[str], None] = print) -> int:
    pending = json.loads(db.get_meta("pending_alerts") or "[]")
    sent = 0
    for text in pending:
        if deliver(text, dry_run, out) or dry_run:
            sent += 1
        else:
            break  # keep the rest for next time (no credentials / Telegram down)
    if not dry_run:
        db.set_meta("pending_alerts", json.dumps(pending[sent:], ensure_ascii=False))
    return sent


# --- broken sources ------------------------------------------------------------------------------------------
def should_skip_disabled(db: Database, source: str, now: datetime, cfg: Mapping[str, Any]) -> bool:
    """True while an auto-disabled source waits for its next retry."""
    state = db.source_state(source)
    if not state or not state["disabled_at"]:
        return False
    last_try = datetime.fromisoformat(state["last_fetch_at"] or state["disabled_at"])
    return now - last_try < timedelta(hours=float(cfg["retry_disabled_every_hours"]))


def after_fetch(db: Database, source: str, now: datetime, streak: int, error: str | None,
                cfg: Mapping[str, Any]) -> None:
    """Disable a source after too many failures in a row, or re-enable it when a retry works."""
    state = db.source_state(source)
    disabled = bool(state and state["disabled_at"])
    if error is None and disabled:
        db.enable_source(source)
        queue_alert(db, f"✅ <b>job-radar</b>: <b>{telegram.escape(source)}</b> volvió a funcionar y la reactivé.")
        log.info("source %s works again: re-enabled", source)
    elif error and not disabled and streak >= int(cfg["disable_source_after_failures"]):
        db.disable_source(source, now, error)
        hours = cfg["retry_disabled_every_hours"]
        queue_alert(
            db,
            f"⚠️ <b>job-radar</b>: desactivé <b>{telegram.escape(source)}</b> porque falló {streak} veces seguidas "
            f"(<code>{telegram.escape(error)}</code>). La reintento cada {hours} h y la reactivo sola si vuelve a "
            f"funcionar; también puedes usar /reactivar {telegram.escape(source)}.",
        )
        log.warning("source %s disabled after %d consecutive failures", source, streak)


# --- dead-man's switch ---------------------------------------------------------------------------------------
def ping(cfg: Mapping[str, Any]) -> None:
    url = cfg.get("ping_url")
    if not url:
        return
    try:
        httpx.get(str(url), timeout=10, headers={"User-Agent": USER_AGENT})
    except httpx.HTTPError as exc:
        log.warning("health ping failed: %s", short_error(exc))


def stale_message(last_healthy_iso: str | None, now: datetime, awake_since: datetime,
                  cfg: Mapping[str, Any]) -> str | None:
    """The "radar stopped" warning, or None. Waits 30 min after the machine wakes up (the catch-up run needs time)."""
    if now - awake_since < timedelta(minutes=30):
        return None
    limit = timedelta(hours=float(cfg["stale_after_hours"]))
    if last_healthy_iso and now - datetime.fromisoformat(last_healthy_iso) < limit:
        return None
    when = "nunca" if not last_healthy_iso else f"hace {int((now - datetime.fromisoformat(last_healthy_iso)).total_seconds() // 3600)} h"
    return (f"⚠️ <b>job-radar</b>: no ha terminado una búsqueda correcta desde {when}. "
            "Revisa /estado (fuentes caídas, crédito de Claude o el servicio de búsqueda detenido).")


# --- backups -------------------------------------------------------------------------------------------------
def maybe_backup(db: Database, db_path: Path, now: datetime, cfg: Mapping[str, Any]) -> Path | None:
    """Copy the database every `every_days` (safe while other processes use it) and keep the newest `keep`."""
    every = timedelta(days=float(cfg.get("every_days", 7)))
    last = db.get_meta("last_backup_at")
    if last and now - datetime.fromisoformat(last) < every:
        return None
    folder = Path(db_path).parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"jobs-{now.strftime('%Y-%m-%d')}.db"
    dest = sqlite3.connect(target)
    try:
        db.conn.backup(dest)
    finally:
        dest.close()
    for old in sorted(folder.glob("jobs-*.db"))[: -int(cfg.get("keep", 4))]:
        old.unlink()
    db.set_meta("last_backup_at", now.isoformat())
    log.info("database backed up to %s", target)
    return target
