"""Telegram bot: answers your commands (/ultimos, /top, /hoy, /estado, /credito, /buscar) from the job-radar DB.

Runs as its own long-lived process (`python -m job_radar bot`, a second launchd agent). It long-polls Telegram's
getUpdates, so it answers within a second or two while the Mac is awake and uses no CPU while waiting.
Only messages from TELEGRAM_CHAT_ID are answered; everything else is ignored.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable

import httpx

from . import credit, health, pipeline, telegram, weekly
from .config import Settings, load_settings, secret
from .db import Database
from .models import normalize_text
from .scheduler import SCHEDULE_HOURS, next_run  # noqa: F401  (re-exported for callers/tests)
from .util import USER_AGENT, utcnow

log = logging.getLogger("job_radar.bot")

POLL_SECONDS = 50
# The search schedule lives in scheduler.py (shared with the Docker scheduler).

COMMANDS = [
    ("ultimos", "Últimas ofertas que pasaron el corte (ej. /ultimos 10)"),
    ("recientes", "Últimas ofertas evaluadas, cualquier puntaje"),
    ("top", "Mejores de los últimos N días (ej. /top 7)"),
    ("hoy", "Resumen de hoy"),
    ("estado", "Estado de las fuentes y próxima búsqueda"),
    ("credito", "Crédito estimado de Claude"),
    ("postulaciones", "Ofertas a las que aplicaste y su etapa"),
    ("semana", "Resumen de los últimos 7 días"),
    ("filtro", "Títulos que el filtro de palabras podría estar descartando"),
    ("reactivar", "Reactivar una fuente desactivada (ej. /reactivar linkedin)"),
    ("buscar", "Buscar ahora"),
    ("skills", "Skills que más piden y cuáles te abrirían más ofertas"),
    ("ayuda", "Ver los comandos"),
]
ALIASES = {
    "ultimos": "ultimos", "ultimo": "ultimos", "last": "ultimos", "latest": "ultimos",
    "recientes": "recientes", "recent": "recientes",
    "top": "top", "mejores": "top", "best": "top",
    "hoy": "hoy", "today": "hoy", "resumen": "hoy",
    "estado": "estado", "status": "estado",
    "credito": "credito", "credit": "credito", "saldo": "credito", "balance": "credito",
    "buscar": "buscar", "search": "buscar", "run": "buscar",
    "semana": "semana", "semanal": "semana", "week": "semana",
    "filtro": "filtro", "filter": "filtro",
    "postulaciones": "postulaciones", "aplicaciones": "postulaciones", "applications": "postulaciones",
    "reactivar": "reactivar", "enable": "reactivar",
    "skills": "skills", "habilidades": "skills", "tecnologias": "skills",
    "ayuda": "ayuda", "help": "ayuda", "start": "ayuda",
}


def parse_command(text: str) -> tuple[str | None, int | None]:
    """'/ultimos@MiJobRadarBot 10' -> ('ultimos', 10). Also accepts plain words like 'últimos 5'."""
    parts = (text or "").strip().split()
    if not parts:
        return None, None
    word = normalize_text(parts[0].lstrip("/").split("@", 1)[0])
    number = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    return ALIASES.get(word), number


def command_arg(text: str) -> str | None:
    """The first word after the command, e.g. 'linkedin' in '/reactivar linkedin'."""
    parts = (text or "").strip().split()
    return parts[1].lower() if len(parts) > 1 else None


def _ago(iso: str | None, now: datetime) -> str:
    if not iso:
        return "nunca"
    minutes = int((now - datetime.fromisoformat(iso)).total_seconds() // 60)
    if minutes < 60:
        return f"hace {max(minutes, 0)} min"
    if minutes < 48 * 60:
        return f"hace {minutes // 60} h"
    return f"hace {minutes // 1440} días"


def _jobs_messages(rows: list[dict[str, Any]], header: str, empty: str, show_fit: bool = False) -> list[str]:
    if not rows:
        return [empty]
    if show_fit:
        rows = [{**r, "score": f"{r['score']} {'✓' if r['fits_location'] else '✗'}"} for r in rows]
    return [text for text, _ in telegram.build_digest(rows, header=header)]


STATUS_LABELS = {"applied": "📨 Aplicaste", "interview": "🗣 Entrevista", "offer": "🎉 Oferta", "rejected": "✖ Rechazo"}
CALLBACK_REPLIES = {
    "like": "👍 Guardado: te interesa", "dislike": "👎 Guardado: no te interesa", "applied": "📨 Marcada como aplicada",
    "interview": "🗣 Entrevista registrada", "offer": "🎉 ¡Felicidades por la oferta!", "rejected": "✖ Marcada como rechazada",
}


def applications_message(db: Database, now: datetime) -> tuple[str, dict | None]:
    rows = [dict(r) for r in db.applications()]
    if not rows:
        return ("🗂️ Todavía no marcas ninguna postulación. Toca 📨 en una oferta del resumen cuando apliques.", None)
    lines = [f"🗂️ <b>Postulaciones activas</b> ({len(rows)})"]
    for n, r in enumerate(rows, 1):
        url = telegram.escape(r["url"])
        lines.append(f"{n}. {STATUS_LABELS[r['status']]} {_ago(r['status_at'], now)} · "
                     f"<a href=\"{url}\">{telegram.escape(r['title'])}</a> — {telegram.escape(r['company'])}")
    lines.append("\nToca 🗣 entrevista, 🎉 oferta o ✖ rechazo para actualizar cada una.")
    return "\n".join(lines)[:telegram.SAFE_LEN], telegram.status_keyboard([(n, r["id"], r["status"]) for n, r in enumerate(rows, 1)][:30])


def apply_callback(db: Database, data: str, now: datetime) -> str | None:
    """Apply a button tap ('fb:<id>:like', 'st:<id>:offer'...). Returns the confirmation text, or None if invalid."""
    parts = (data or "").split(":")
    if len(parts) != 3 or not parts[1].isdigit():
        return None
    kind, job_id, action = parts[0], int(parts[1]), parts[2]
    job = db.job(job_id)
    if job is None:
        return None
    if kind == "fb" and action in ("like", "dislike"):
        # tapping the active choice again clears it
        db.set_feedback(job_id, None if job["feedback"] == action else action, now)
        return CALLBACK_REPLIES[action] if job["feedback"] != action else "Quitado"
    if kind == "fb" and action == "applied":
        if job["status"] is None:
            db.set_status(job_id, "applied", now)
        return CALLBACK_REPLIES["applied"]
    if kind == "st" and action in ("interview", "offer", "rejected"):
        db.set_status(job_id, action, now)
        return CALLBACK_REPLIES[action]
    return None


def redraw_keyboard(db: Database, markup: dict | None) -> dict | None:
    items = telegram.keyboard_items(markup)
    if not items:
        return None
    rows = []
    for kind, number, job_id in items:
        job = db.job(job_id)
        rows.append((kind, number, job_id, job["feedback"] if job else None, job["status"] if job else None))
    if rows[0][0] == "st":
        return telegram.status_keyboard([(n, i, s) for _, n, i, _, s in rows])
    return telegram.feedback_keyboard([(n, i, f, s) for _, n, i, f, s in rows])


def help_text() -> str:
    lines = ["🛰️ <b>job-radar</b>: comandos"] + [f"/{name}: {telegram.escape(desc)}" for name, desc in COMMANDS]
    lines.append("\nTambién puedes escribirlos sin la barra, por ejemplo: <i>ultimos 10</i>.")
    return "\n".join(lines)


def handle(
    command: str | None, number: int | None, settings: Settings, db: Database, now_local: datetime,
    arg: str | None = None,
) -> list[str]:
    """Build the HTML replies for one command (except /buscar, which the bot loop runs in the background)."""
    now = utcnow()
    if command == "ultimos":
        limit = min(number or 5, 30)
        rows = [dict(r) for r in db.latest_matches(settings.min_score, settings.require_location_fit, limit)]
        return _jobs_messages(rows, f"🛰️ <b>Últimas {len(rows)} ofertas</b> (≥ {settings.min_score})",
                              "Todavía no hay ofertas que pasen el corte.")
    if command == "recientes":
        rows = [dict(r) for r in db.latest_scored(min(number or 10, 30))]
        return _jobs_messages(rows, f"🔎 <b>Últimas {len(rows)} evaluadas</b> (✓ = ubicación OK)",
                              "Todavía no hay ofertas evaluadas.", show_fit=True)
    if command == "top":
        days = min(number or 7, 90)
        rows = [dict(r) for r in db.top(days, 10, now)]
        return _jobs_messages(rows, f"🏆 <b>Top de los últimos {days} días</b>", f"Nada evaluado en {days} días.",
                              show_fit=True)
    if command == "hoy":
        midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        stats = db.stats_since(midnight, settings.min_score)
        spent = db.spend_between(midnight)
        return [
            "📊 <b>Hoy</b>\n"
            f"Ofertas nuevas vistas: {stats['new']}\n"
            f"Pasaron el filtro: {stats['passed']}\n"
            f"Evaluadas con Claude: {stats['scored']} (≥ {settings.min_score}: {stats['matches']})\n"
            f"Enviadas: {stats['sent']}\n"
            f"Gasto de Claude: ${spent:.3f}\n"
            f"Última búsqueda: {_ago(db.get_meta('last_run_at'), now)}"
        ]
    if command == "estado":
        lines = ["⚙️ <b>Estado de las fuentes</b>"]
        for row in db.source_status():
            if row["disabled_at"]:
                lines.append(f"⏸️ {row['source']}: desactivada {_ago(row['disabled_at'], now)} "
                             f"({telegram.escape(row['disabled_reason'])}). /reactivar {row['source']}")
            elif row["last_error"]:
                lines.append(f"❌ {row['source']}: {telegram.escape(row['last_error'])} "
                             f"(último OK {_ago(row['last_ok_at'], now)})")
            else:
                lines.append(f"✅ {row['source']}: {row['last_count']} ofertas, {_ago(row['last_ok_at'], now)}")
        pending = len(db.jobs_to_notify(settings.min_score, settings.require_location_fit))
        window = settings.notify_window or {}
        lines.append(f"\nPróxima búsqueda: {next_run(now_local):%H:%M}")
        if window:
            lines.append(f"Envío de mensajes: {window.get('start')}–{window.get('end')}")
        if pending:
            lines.append(f"Esperando envío: {pending}")
        return ["\n".join(lines)]
    if command == "semana":
        return [weekly.build(db, settings, now_local)]
    if command == "filtro":
        from datetime import timedelta as _td

        from . import filter_report

        days = min(number or 30, 365)
        report = filter_report.build(db.jobs_first_seen_since(now - _td(days=days)), settings.prefilter, days)
        return [filter_report.format_report(report)]
    if command == "reactivar":
        disabled = [r["source"] for r in db.source_status() if r["disabled_at"]]
        if not arg:
            return ["No hay fuentes desactivadas." if not disabled else
                    "Fuentes desactivadas: " + ", ".join(disabled) + f"\nUsa: /reactivar {disabled[0]}"]
        if db.enable_source(arg):
            return [f"✅ Reactivé <b>{telegram.escape(arg)}</b>; se consultará en la próxima búsqueda."]
        return [f"«{telegram.escape(arg)}» no está desactivada." + (f" Desactivadas: {', '.join(disabled)}" if disabled else "")]
    if command == "credito":
        warn = settings.credit.get("warn_below_usd")
        status = credit.credit_status(db, settings.credit, now_local)
        return [credit.format_credit_message(status, float(warn) if warn is not None else None)]
    return [help_text()]


class Bot:
    def __init__(self, settings: Settings, token: str, chat_id: str, client: httpx.Client | None = None):
        self.settings = settings
        self.token = token
        self.chat_id = str(chat_id)
        self.client = client or httpx.Client(timeout=POLL_SECONDS + 15, headers={"User-Agent": USER_AGENT})
        self.search_thread: threading.Thread | None = None
        self.awake_since = utcnow()
        self.last_loop = utcnow()
        self.last_watch = utcnow() - timedelta(minutes=10)

    def reply(self, text: str) -> None:
        try:
            telegram.send_message(self.token, self.chat_id, text, self.client)
        except telegram.TelegramError as exc:
            log.error("could not reply: %s", exc)

    def on_callback(self, callback: dict[str, Any]) -> None:
        message = callback.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id", ""))
        if chat_id != self.chat_id:
            log.warning("ignored a button tap from an unknown chat (%s)", chat_id or "none")
            return
        settings = load_settings()
        db = Database(settings.db_path)
        try:
            text = apply_callback(db, callback.get("data", ""), utcnow()) or "No encontré esa oferta."
            markup = redraw_keyboard(db, message.get("reply_markup"))
        finally:
            db.close()
        try:
            telegram.answer_callback(self.token, callback["id"], text, self.client)
            if markup and message.get("message_id"):
                telegram.edit_markup(self.token, self.chat_id, message["message_id"], markup, self.client)
        except telegram.TelegramError as exc:
            log.warning("could not update the buttons: %s", exc)

    def on_update(self, update: dict[str, Any]) -> None:
        if update.get("callback_query"):
            self.on_callback(update["callback_query"])
            return
        message = update.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id", ""))
        if chat_id != self.chat_id:
            log.warning("ignored a message from an unknown chat (%s)", chat_id or "none")
            return
        command, number = parse_command(message.get("text") or "")
        log.info("command: %s %s", command or "?", number or "")
        settings = load_settings()  # pick up config.yaml edits without restarting
        if command == "buscar":
            self.start_search(settings)
            return
        if command == "skills":
            self.send_skills(settings, number or 30)
            return
        if command == "postulaciones":
            db = Database(settings.db_path)
            try:
                text, markup = applications_message(db, utcnow())
            finally:
                db.close()
            try:
                telegram.send_message(self.token, self.chat_id, text, self.client, reply_markup=markup)
            except telegram.TelegramError as exc:
                log.error("could not reply: %s", exc)
            return
        db = Database(settings.db_path)
        try:
            window = settings.notify_window or {}
            now_local = pipeline.local_now(window.get("timezone"))
            for text in handle(command, number, settings, db, now_local, command_arg(message.get("text") or "")):
                self.reply(text)
        finally:
            db.close()

    def send_skills(self, settings: Settings, days: int) -> None:
        from .skills import build_report, format_report, split_message

        self.reply(f"📈 Analizando las ofertas de los últimos {days} días…")
        db = Database(settings.db_path)
        try:
            report = build_report(db, settings, min(days, 365), use_llm=bool(secret("ANTHROPIC_API_KEY")))
        finally:
            db.close()
        for part in split_message(format_report(report)):
            self.reply(part)

    def watchdog(self, now: datetime | None = None) -> None:
        """Warn (at most every 12 h, inside the notify window) if no healthy search finished recently."""
        now = now or utcnow()
        if now - self.last_loop > timedelta(minutes=5):  # the machine was asleep: give the catch-up run time
            self.awake_since = now
        self.last_loop = now
        if now - self.last_watch < timedelta(minutes=10):
            return
        self.last_watch = now
        try:
            settings = load_settings()
            window = settings.notify_window or {}
            if not pipeline.in_notify_window(window, pipeline.local_now(window.get("timezone"))):
                return
            db = Database(settings.db_path)
            try:
                last_alert = db.get_meta("watchdog_alert_at")
                if last_alert and now - datetime.fromisoformat(last_alert) < timedelta(hours=12):
                    return
                text = health.stale_message(db.get_meta("last_healthy_run_at"), now, self.awake_since,
                                            health.health_cfg(settings))
                if text:
                    self.reply(text)
                    db.set_meta("watchdog_alert_at", now.isoformat())
            finally:
                db.close()
        except Exception:
            log.exception("watchdog check failed")

    def start_search(self, settings: Settings) -> None:
        if self.search_thread and self.search_thread.is_alive():
            self.reply("⏳ Ya hay una búsqueda en curso.")
            return
        self.reply("🔎 Buscando… Las fuentes consultadas hace poco se saltan para no saturarlas. Te aviso al terminar.")
        self.search_thread = threading.Thread(target=self._search, args=(settings,), daemon=True)
        self.search_thread.start()

    def _search(self, settings: Settings) -> None:
        try:
            report = pipeline.run(settings, out=lambda _: None)
        except Exception:  # the bot must keep running whatever happens in a run
            log.exception("on-demand run failed")
            where = ("docker compose logs bot" if os.environ.get("JOB_RADAR_RUNTIME") == "docker"
                     else "~/Library/Logs/job-radar-bot.log")
            self.reply(f"⚠️ La búsqueda falló; revisa {where}")
            return
        self.reply(summary_text(report, settings))

    def serve(self, sleep: Callable[[float], None] = time.sleep) -> None:
        try:
            telegram.set_my_commands(self.token, COMMANDS, self.client)
        except telegram.TelegramError as exc:
            log.warning("could not register the command menu: %s", exc)
        db = Database(self.settings.db_path)
        saved = db.get_meta("telegram_offset")
        db.close()
        offset = int(saved) if saved else None
        backoff = 5.0
        log.info("bot started; answering chat %s", self.chat_id)
        while True:
            try:
                updates = telegram.get_updates(self.token, self.client, offset=offset, timeout=POLL_SECONDS)
            except telegram.TelegramError as exc:  # offline, Mac just woke up, Telegram hiccup...
                log.warning("getUpdates failed (%s); retrying in %.0fs", exc, backoff)
                sleep(backoff)
                backoff = min(backoff * 2, 300)
                continue
            backoff = 5.0
            db = Database(self.settings.db_path)
            db.set_meta("heartbeat_bot", utcnow().isoformat())  # lets the portal show the bot as alive in Docker
            db.close()
            self.watchdog()
            for update in updates:
                offset = update["update_id"] + 1
                db = Database(self.settings.db_path)
                db.set_meta("telegram_offset", str(offset))  # never answer the same message twice
                db.close()
                try:
                    self.on_update(update)
                except Exception:
                    log.exception("error handling a message")
                    self.reply("⚠️ Algo falló al responder; revisa el log del bot.")


def summary_text(report: pipeline.RunReport, settings: Settings) -> str:
    if report.busy:
        return "⏳ Ya había una búsqueda programada en curso; sus resultados llegarán en cuanto termine."
    failed = [r.source for r in report.fetched if not r.ok]
    lines = [
        "✅ <b>Búsqueda terminada</b>",
        f"Fuentes consultadas: {len(report.fetched)}" + (f" (fallaron: {', '.join(failed)})" if failed else ""),
        f"Ofertas nuevas: {report.new_jobs} · pasaron el filtro: {report.prefilter_passed}",
        f"Evaluadas: {report.scored} · enviadas: {report.notified}",
    ]
    if report.skipped:
        lines.append(f"Saltadas (consultadas hace poco): {len(report.skipped)}")
    window = settings.notify_window
    if window and not pipeline.in_notify_window(window, pipeline.local_now(window.get("timezone"))):
        lines.append(f"🌙 Horario silencioso: las coincidencias se envían a las {window.get('start')}.")
    return "\n".join(lines)


def main() -> int:
    settings = load_settings()
    token, chat_id = secret("TELEGRAM_BOT_TOKEN"), secret("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env")
        return 1
    Bot(settings, token, chat_id).serve()
    return 0
