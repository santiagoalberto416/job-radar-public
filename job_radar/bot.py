"""Telegram bot: answers your commands (/ultimos, /top, /hoy, /estado, /credito, /buscar) from the job-radar DB.

Runs as its own long-lived process (`python -m job_radar bot`, a second launchd agent). It long-polls Telegram's
getUpdates, so it answers within a second or two while the Mac is awake and uses no CPU while waiting.
Only messages from TELEGRAM_CHAT_ID are answered; everything else is ignored.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable

import httpx

from . import credit, pipeline, telegram
from .config import Settings, load_settings, secret
from .db import Database
from .models import normalize_text
from .util import USER_AGENT, utcnow

log = logging.getLogger("job_radar.bot")

POLL_SECONDS = 50
# Keep in sync with StartCalendarInterval in scripts/search.plist.template.
SCHEDULE_HOURS = sorted(set(range(0, 24, 2)) | {7})

COMMANDS = [
    ("ultimos", "Últimas ofertas que pasaron el corte (ej. /ultimos 10)"),
    ("recientes", "Últimas ofertas evaluadas, cualquier puntaje"),
    ("top", "Mejores de los últimos N días (ej. /top 7)"),
    ("hoy", "Resumen de hoy"),
    ("estado", "Estado de las fuentes y próxima búsqueda"),
    ("credito", "Crédito estimado de Claude"),
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


def help_text() -> str:
    lines = ["🛰️ <b>job-radar</b>: comandos"] + [f"/{name}: {telegram.escape(desc)}" for name, desc in COMMANDS]
    lines.append("\nTambién puedes escribirlos sin la barra, por ejemplo: <i>ultimos 10</i>.")
    return "\n".join(lines)


def next_run(now_local: datetime) -> datetime:
    for day in (0, 1):
        for hour in SCHEDULE_HOURS:
            candidate = (now_local + timedelta(days=day)).replace(hour=hour, minute=0, second=0, microsecond=0)
            if candidate > now_local:
                return candidate
    raise AssertionError("unreachable")


def handle(command: str | None, number: int | None, settings: Settings, db: Database, now_local: datetime) -> list[str]:
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
            if row["last_error"]:
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

    def reply(self, text: str) -> None:
        try:
            telegram.send_message(self.token, self.chat_id, text, self.client)
        except telegram.TelegramError as exc:
            log.error("could not reply: %s", exc)

    def on_update(self, update: dict[str, Any]) -> None:
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
        db = Database(settings.db_path)
        try:
            window = settings.notify_window or {}
            for text in handle(command, number, settings, db, pipeline.local_now(window.get("timezone"))):
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
            self.reply("⚠️ La búsqueda falló; revisa ~/Library/Logs/job-radar-bot.log")
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
