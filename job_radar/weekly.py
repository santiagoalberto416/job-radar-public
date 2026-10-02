"""Weekly summary: one Telegram message per week (Mondays 07:00 by default) with the last 7 days.

Config (optional, in config.yaml):

    weekly_summary:
      enabled: true
      weekday: 0      # 0 = Monday ... 6 = Sunday
      hour: 7         # sent by the first run at or after this hour (local time)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping

from . import telegram
from .config import Settings
from .db import Database
from .skills import analyze

DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def weekly_cfg(settings: Settings) -> dict[str, Any]:
    return {"enabled": True, "weekday": 0, "hour": 7, **(settings.raw.get("weekly_summary") or {})}


def week_key(now_local: datetime) -> str:
    year, week, _ = now_local.isocalendar()
    return f"{year}-W{week:02d}"


def is_due(now_local: datetime, cfg: Mapping[str, Any], last_sent_week: str | None) -> bool:
    if not cfg.get("enabled", True):
        return False
    return (now_local.weekday() == int(cfg["weekday"]) and now_local.hour >= int(cfg["hour"])
            and last_sent_week != week_key(now_local))


def _day(d: datetime) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}"


def build(db: Database, settings: Settings, now_local: datetime) -> str:
    since = now_local - timedelta(days=7)
    stats = db.stats_since(since, settings.min_score)
    spend = db.spend_between(since)
    e = telegram.escape
    lines = [
        f"📅 <b>Resumen semanal</b> ({_day(since)} – {_day(now_local)})",
        f"Ofertas vistas: {stats['new']} · pasaron el filtro: {stats['passed']} · calificadas: {stats['scored']}",
        f"Coincidencias (≥ {settings.min_score}): {stats['matches']} · enviadas: {stats['sent']}",
        f"Gasto de Claude: ${spend:.2f}",
    ]
    apps = [dict(r) for r in db.applications(include_closed=True)]
    if apps:
        def recent(r):
            return r["status_at"] and datetime.fromisoformat(r["status_at"]) >= since
        active = sum(r["status"] in ("applied", "interview", "offer") for r in apps)
        counts = {s: sum(r["status"] == s and recent(r) for r in apps) for s in ("applied", "interview", "offer", "rejected")}
        lines.append(f"Postulaciones activas: {active} · esta semana: {counts['applied']} aplicadas, "
                     f"{counts['interview']} entrevistas, {counts['offer']} ofertas, {counts['rejected']} rechazos "
                     "(/postulaciones)")
    top = [dict(r) for r in db.top(7, 50, now_local)
           if r["score"] >= settings.min_score and (r["fits_location"] or not settings.require_location_fit)]
    if top:
        lines += ["", "<b>Lo mejor de la semana</b>"]
        lines += [f"{i}. <b>{r['score']}</b> · <a href=\"{e(r['url'])}\">{e(r['title'])}</a> — {e(r['company'])}"
                  for i, r in enumerate(top[:3], 1)]
    ignore = (settings.raw.get("skills_report") or {}).get("not_really_have") or []
    report = analyze(db.jobs_first_seen_since(since), settings.profile_text, settings.prefilter, 7, ignore)
    if report.market_jobs:
        demand = ", ".join(f"{e(s)} {report.pct(n, report.market_jobs)}" for s, n in report.demand[:5])
        lines += ["", f"<b>Skills más pedidas</b>: {demand}"]
        if report.near_miss_missing:
            missing = ", ".join(f"{e(s)} ({n})" for s, n in report.near_miss_missing[:3])
            lines.append(f"<b>Te faltan en casi-coincidencias</b>: {missing}")
    from .filter_report import build as build_filter_report, weekly_line

    hint = weekly_line(build_filter_report(db.jobs_first_seen_since(since), settings.prefilter, 7))
    if hint:
        lines += ["", hint]
    broken = [r for r in db.source_status() if r["disabled_at"] or r["last_error"]]
    if broken:
        names = ", ".join(e(r["source"]) + (" (desactivada)" if r["disabled_at"] else "") for r in broken)
        lines += ["", f"⚠️ Fuentes con problemas: {names}"]
    lines += ["", "Más detalle: /skills · /top 7"]
    return "\n".join(lines)
