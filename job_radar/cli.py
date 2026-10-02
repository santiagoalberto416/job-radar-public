"""Command-line interface: python -m job_radar <command>."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

from . import telegram
from .config import load_settings, secret
from .db import Database
from .pipeline import check_sources, run
from .util import utcnow

MAX_LOG_BYTES = 5 * 1024 * 1024


LOG_FILES = {"run": "job-radar.log", "schedule": "job-radar.log", "bot": "job-radar-bot.log"}


def _setup_logging(verbose: bool, command: str | None = None) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Docker: also write to a rotating file in JOB_RADAR_LOG_DIR (on macOS launchd redirects stdout instead).
    log_dir = os.environ.get("JOB_RADAR_LOG_DIR")
    if log_dir and command in LOG_FILES:
        from logging.handlers import RotatingFileHandler

        Path(log_dir).mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(Path(log_dir) / LOG_FILES[command], maxBytes=MAX_LOG_BYTES, backupCount=1,
                                      encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"))
        logging.getLogger().addHandler(handler)
    # httpx logs full request URLs at INFO, and Telegram URLs contain the bot token: keep them quiet.
    for noisy in ("httpx", "httpx2", "httpcore", "anthropic", "urllib3", "JobSpy"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _rotate_log() -> None:
    """launchd appends forever to the log file; keep one 5 MB backup."""
    path = os.environ.get("JOB_RADAR_LOG_FILE")
    if not path:
        return
    log_file = Path(path).expanduser()
    if log_file.exists() and log_file.stat().st_size > MAX_LOG_BYTES:
        log_file.replace(log_file.with_suffix(log_file.suffix + ".1"))


def cmd_run(args: argparse.Namespace) -> int:
    _rotate_log()
    settings = load_settings()
    if args.max_llm is not None:
        settings.raw.setdefault("llm", {})["max_jobs_per_run"] = args.max_llm
    report = run(settings, dry_run=args.dry_run, no_llm=args.no_llm, force=args.force)
    return 1 if report.fetched and all(not r.ok for r in report.fetched) else 0


def cmd_check_sources(args: argparse.Namespace) -> int:
    settings = load_settings()
    names = args.sources or None
    return 0 if check_sources(settings, names) else 1


def _require_token() -> str | None:
    token = secret("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN is not set. Add it to .env (see .env.example) and try again.")
    return token


def cmd_telegram_setup(args: argparse.Namespace) -> int:
    load_settings()
    token = _require_token()
    if not token:
        return 1
    try:
        me = telegram.get_me(token)
        if telegram.get_webhook_url(token):
            print("This bot has a webhook set, so getUpdates can't be used. Remove it with deleteWebhook first.")
            return 1
        chats = telegram.chats_from_updates(telegram.get_updates(token))
    except telegram.TelegramError as exc:
        if " 409 " in f" {exc} ":
            print("The job-radar bot daemon is already reading this bot's messages (only one reader is allowed).\n"
                  "Your chat is set up if the bot answers /ayuda. To run this anyway, stop the bot first:\n"
                  f"  launchctl bootout gui/$(id -u)/{os.environ.get('JOB_RADAR_LABEL_PREFIX', 'com.jobradar')}.bot")
            return 1
        print(f"Telegram error: {exc}")
        return 1
    print(f"Bot: @{me.get('username')} ({me.get('first_name')})")
    if not chats:
        print(
            f"No messages found yet.\n"
            f"1. Open Telegram and search for @{me.get('username')}\n"
            f"2. Tap Start (or send it any message, e.g. 'hola')\n"
            f"3. Run this command again within 24 hours."
        )
        return 1
    for chat in chats:
        name = chat.get("title") or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
        username = f" @{chat['username']}" if chat.get("username") else ""
        print(f"chat_id={chat['id']}  type={chat.get('type')}  name={name}{username}")
    print("\nPut your chat_id in .env as:  TELEGRAM_CHAT_ID=<chat_id>\nThen run:  python -m job_radar test-telegram")
    return 0


def cmd_test_telegram(args: argparse.Namespace) -> int:
    load_settings()
    token, chat_id = _require_token(), secret("TELEGRAM_CHAT_ID")
    if not token:
        return 1
    if not chat_id:
        print("TELEGRAM_CHAT_ID is not set. Run: python -m job_radar telegram-setup")
        return 1
    try:
        telegram.send_message(token, chat_id, "✅ <b>job-radar</b>: mensaje de prueba. Telegram está configurado.")
    except telegram.TelegramError as exc:
        print(f"Telegram error: {exc}")
        return 1
    print("Test message sent. Check Telegram.")
    return 0


def cmd_top(args: argparse.Namespace) -> int:
    settings = load_settings()
    db = Database(settings.db_path)
    rows = db.top(args.days, args.limit, utcnow())
    db.close()
    if not rows:
        print(f"No scored jobs in the last {args.days} days.")
        return 0
    for row in rows:
        loc = "✓" if row["fits_location"] else "✗"
        sent = "sent" if row["notified_at"] else ""
        print(f"{row['score']:>3} {loc} {row['title']} | {row['company']} | {row['location']} | {row['source']} {sent}")
        print(f"      {row['reason']}")
        flags = json.loads(row["red_flags"] or "[]")
        if flags:
            print(f"      ⚑ {'; '.join(flags)}")
        print(f"      {row['url']}")
    return 0


def cmd_credit(args: argparse.Namespace) -> int:
    import re

    from .credit import credit_status, format_credit_message
    from .pipeline import local_now

    settings = load_settings()
    db = Database(settings.db_path)
    tz = (settings.notify_window or {}).get("timezone")
    status = credit_status(db, settings.credit, local_now(tz))
    db.close()
    warn = settings.credit.get("warn_below_usd")
    text = format_credit_message(status, float(warn) if warn is not None else None)
    print(re.sub(r"<[^>]+>", "", text))
    if not args.send:
        return 0
    token, chat_id = _require_token(), secret("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("TELEGRAM_CHAT_ID is not set. Run: python -m job_radar telegram-setup")
        return 1
    try:
        telegram.send_message(token, chat_id, text)
    except telegram.TelegramError as exc:
        print(f"Telegram error: {exc}")
        return 1
    db = Database(settings.db_path)
    db.set_meta("credit_report_date", local_now(tz).date().isoformat())  # counts as today's daily message
    db.close()
    print("Sent to Telegram.")
    return 0


def cmd_skills(args: argparse.Namespace) -> int:
    import re

    from .skills import build_report, format_report, split_message

    settings = load_settings()
    use_llm = not args.no_llm and bool(secret("ANTHROPIC_API_KEY"))
    db = Database(settings.db_path)
    try:
        report = build_report(db, settings, args.days, use_llm=use_llm)
    finally:
        db.close()
    text = format_report(report)
    print(re.sub(r"<[^>]+>", "", text))
    if args.send:
        token, chat_id = _require_token(), secret("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            return 1
        try:
            for part in split_message(text):
                telegram.send_message(token, chat_id, part)
        except telegram.TelegramError as exc:
            print(f"Telegram error: {exc}")
            return 1
        print("Sent to Telegram.")
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    from .db import Database
    from .scheduler import serve
    from .util import utcnow

    def run_once() -> None:
        run(load_settings())

    def heartbeat_forever() -> None:
        # In its own thread so the portal keeps seeing the service alive during long runs (the batch wait).
        while True:
            try:
                db = Database(load_settings().db_path)
                db.set_meta("heartbeat_search", utcnow().isoformat())
                db.close()
            except Exception:
                logging.getLogger("job_radar.scheduler").exception("heartbeat failed")
            time.sleep(60)

    threading.Thread(target=heartbeat_forever, name="heartbeat", daemon=True).start()
    serve(run_once)
    return 0


def cmd_bot(args: argparse.Namespace) -> int:
    from .bot import main as bot_main

    _rotate_log()
    return bot_main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="job_radar", description="Find, score and send matching job openings.")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="fetch, score and notify")
    p.add_argument("--dry-run", action="store_true", help="print the digest instead of sending it to Telegram")
    p.add_argument("--no-llm", action="store_true", help="skip Claude scoring")
    p.add_argument("--force", action="store_true", help="ignore each source's min_interval_hours")
    p.add_argument("--max-llm", type=int, metavar="N", help="score at most N jobs this run (overrides config)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("check-sources", help="fetch each source live and print counts (no DB writes)")
    p.add_argument("sources", nargs="*", help="only these sources (default: all enabled)")
    p.set_defaults(func=cmd_check_sources)

    sub.add_parser("telegram-setup", help="print your chat_id after you message the bot").set_defaults(
        func=cmd_telegram_setup
    )
    sub.add_parser("test-telegram", help="send a test message").set_defaults(func=cmd_test_telegram)

    p = sub.add_parser("credit", help="estimated Claude credit and job-radar's spend")
    p.add_argument("--send", action="store_true", help="also send it to Telegram now (ignores notify_window)")
    p.set_defaults(func=cmd_credit)

    p = sub.add_parser("skills", help="which skills the jobs around your profile ask for (and what to learn next)")
    p.add_argument("--days", type=int, default=30, help="analyze jobs first seen in the last N days (default 30)")
    p.add_argument("--send", action="store_true", help="also send the report to Telegram")
    p.add_argument("--no-llm", action="store_true", help="numbers only, skip the Claude recommendation")
    p.set_defaults(func=cmd_skills)

    sub.add_parser("schedule", help="run searches on the built-in schedule (Docker/Linux; macOS uses launchd)").set_defaults(
        func=cmd_schedule
    )

    sub.add_parser("bot", help="answer Telegram commands (/ultimos, /top, /hoy...); runs until stopped").set_defaults(
        func=cmd_bot
    )

    p = sub.add_parser("top", help="best scored jobs from the DB")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_top)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose, args.command)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
