"""The run: fetch -> dedupe -> prefilter -> score -> notify."""

from __future__ import annotations

import fcntl
import logging
import time as time_module
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any, Callable, Iterator
from zoneinfo import ZoneInfo

from . import credit, telegram
from .config import Settings, secret
from .db import Database
from .prefilter import prefilter
from .scorer import CreditExhaustedError, Scorer, ScorerFatalError
from .sources import DESCRIPTION_FETCHERS, FetchResult, enabled_sources, fetch_source
from .util import http_client, short_error, utcnow

log = logging.getLogger("job_radar")

# launchd fires on the hour; allow some slack so a 2h source isn't skipped for being 5 seconds early.
PACING_SLACK = timedelta(minutes=10)


def local_now(timezone: str | None = None) -> datetime:
    return datetime.now(ZoneInfo(timezone)) if timezone else datetime.now().astimezone()


def in_notify_window(window: dict[str, Any] | None, now: datetime) -> bool:
    """True if `now` (local time) is inside [start, end], to the minute, so a run that starts at 22:00:05 is
    still inside a window ending at "22:00". Windows may cross midnight. No window = always."""
    if not window:
        return True
    start = time.fromisoformat(str(window.get("start", "00:00")))
    end = time.fromisoformat(str(window.get("end", "23:59")))
    current = now.time().replace(tzinfo=None, second=0, microsecond=0)
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


@contextmanager
def run_lock(path) -> Iterator[bool]:
    """Exclusive lock so a scheduled run and a /buscar run from Telegram never overlap. Yields False if busy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@dataclass
class RunReport:
    fetched: list[FetchResult] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    new_jobs: int = 0
    prefilter_passed: int = 0
    scored: int = 0
    score_failures: int = 0
    notified: int = 0
    llm_cost_usd: float = 0.0
    credit_exhausted: bool = False
    busy: bool = False
    notes: list[str] = field(default_factory=list)


def run(
    settings: Settings,
    dry_run: bool = False,
    no_llm: bool = False,
    force: bool = False,
    out: Callable[[str], None] = print,
) -> RunReport:
    with run_lock(settings.db_path.with_suffix(".lock")) as acquired:
        if not acquired:
            log.warning("another run is already in progress; skipping this one")
            return RunReport(busy=True)
        return _run(settings, dry_run, no_llm, force, out)


def _run(
    settings: Settings, dry_run: bool, no_llm: bool, force: bool, out: Callable[[str], None]
) -> RunReport:
    now = utcnow()
    report = RunReport()
    window = settings.notify_window
    now_local = local_now((window or {}).get("timezone"))
    quiet = not dry_run and not in_notify_window(window, now_local)
    db = Database(settings.db_path)
    try:
        # 1. Fetch every source that is due. One failing source never breaks the run.
        for name in enabled_sources(settings.sources):
            cfg = settings.sources.get(name) or {}
            last_ok = db.last_success(name)
            interval = timedelta(hours=float(cfg.get("min_interval_hours", 2)))
            if not force and last_ok and now - last_ok < interval - PACING_SLACK:
                report.skipped.append(name)
                continue
            result = fetch_source(name, cfg)
            report.fetched.append(result)
            db.record_fetch(name, now, len(result.jobs), result.error)
            if result.error:
                log.error("source %s FAILED in %.1fs: %s", name, result.seconds, result.error)
            else:
                log.info("source %s: %d jobs in %.1fs", name, len(result.jobs), result.seconds)
            for warning in result.warnings:
                log.warning("source %s: %s", name, warning)
        if report.skipped:
            log.info("not due yet (min_interval_hours): %s", ", ".join(report.skipped))

        # 2. Dedupe against the DB, then 3. prefilter every new job.
        new = db.insert_new_jobs((job for r in report.fetched for job in r.jobs), now)
        report.new_jobs = len(new)
        passed_jobs = []
        for job_id, job in new:
            passed, reason = prefilter(
                job, settings.prefilter, settings.exclude_companies, settings.max_job_age_days, now
            )
            db.set_prefilter(job_id, passed, reason)
            report.prefilter_passed += passed
            if passed:
                passed_jobs.append((job_id, job))
        log.info("new jobs: %d, passed prefilter: %d", report.new_jobs, report.prefilter_passed)
        _fetch_missing_descriptions(db, settings, passed_jobs)

        # 4. Score with Claude (only jobs never scored; failures are retried next run).
        if no_llm:
            log.info("--no-llm: skipping scoring")
            if dry_run:
                _print_unscored(db, settings, out)
        else:
            _score_pending(db, settings, report)

        # 5. Notify (outside the notify window, matches wait in the DB for the next run inside it).
        if quiet:
            pending = len(db.jobs_to_notify(settings.min_score, settings.require_location_fit))
            log.info("quiet hours (window %s-%s): %d matches held until the window opens",
                     window.get("start"), window.get("end"), pending)
        else:
            _notify(db, settings, report, dry_run, out)

        # All attempted sources failed -> warn.
        if report.fetched and all(not r.ok for r in report.fetched):
            if quiet:
                log.error("all sources failed (no Telegram warning during quiet hours)")
            else:
                lines = "\n".join(f"• {telegram.escape(r.source)}: {telegram.escape(r.error)}" for r in report.fetched)
                _send_or_print(settings, f"⚠️ <b>job-radar</b>: todas las fuentes fallaron en esta corrida.\n{lines}", dry_run, out)

        # Claude credit: an alert when the API says it ran out, and one short estimate per day.
        if not quiet:
            _credit_messages(db, settings, report, now_local, dry_run, out)
        if not dry_run:
            db.set_meta("last_run_at", utcnow().isoformat())
    finally:
        db.close()
    log.info(
        "run done: fetched=%d sources (%d failed), new=%d, prefilter=%d, scored=%d, score_failures=%d, "
        "notified=%d, llm_cost≈$%.4f",
        len(report.fetched), sum(not r.ok for r in report.fetched), report.new_jobs, report.prefilter_passed,
        report.scored, report.score_failures, report.notified, report.llm_cost_usd,
    )
    return report


def _fetch_missing_descriptions(db: Database, settings: Settings, jobs: list) -> None:
    """Some sources list jobs without a description; fetch it only for new jobs that passed the prefilter."""
    todo = [(job_id, job) for job_id, job in jobs if job.source in DESCRIPTION_FETCHERS and not job.description]
    if not todo:
        return
    fetched = 0
    with http_client() as client:
        for source in sorted({job.source for _, job in todo}):
            cfg = settings.sources.get(source) or {}
            limit, delay = int(cfg.get("max_details_per_run", 25)), float(cfg.get("delay_seconds", 5))
            for i, (job_id, job) in enumerate([t for t in todo if t[1].source == source][:limit]):
                if i:
                    time_module.sleep(delay)
                try:
                    description = DESCRIPTION_FETCHERS[source](job.url, client)
                except Exception as exc:  # scoring still works from title/company/location
                    log.warning("could not fetch description for %s (%s): %s", job.title, source, short_error(exc))
                    continue
                if description:
                    db.set_description(job_id, description)
                    fetched += 1
    log.info("fetched %d missing descriptions", fetched)


def _score_pending(db: Database, settings: Settings, report: RunReport) -> None:
    llm = settings.llm
    max_attempts = int(llm.get("max_attempts", 3))
    rows = db.jobs_to_score(max_attempts, int(llm.get("max_jobs_per_run", 40)))
    if not rows:
        return
    if not secret("ANTHROPIC_API_KEY"):
        log.error("ANTHROPIC_API_KEY is not set in .env; %d jobs left unscored", len(rows))
        report.notes.append("missing ANTHROPIC_API_KEY")
        return
    scorer = Scorer(llm_cfg=llm, profile=settings.profile_text, location=settings.location)
    for row in rows:
        job = dict(row)
        try:
            outcome = scorer.score(job)
        except CreditExhaustedError as exc:
            log.error("scoring stopped for this run: %s", exc)
            report.credit_exhausted = True
            break
        except ScorerFatalError as exc:
            log.error("scoring stopped for this run: %s", exc)
            report.notes.append(str(exc))
            break
        if outcome.result:
            db.save_score(job["id"], outcome.result, scorer.model, utcnow())
            report.scored += 1
            log.info("scored %3d %s | %s @ %s", outcome.result["score"],
                     "✓" if outcome.result["fits_location"] else "✗", job["title"], job["company"])
        else:
            db.record_score_failure(job["id"], outcome.error or "unknown error")
            report.score_failures += 1
            log.warning("could not score job %d (%s @ %s): %s", job["id"], job["title"], job["company"], outcome.error)
    report.llm_cost_usd = scorer.usage.cost_usd(scorer.model)
    if scorer.usage.calls:
        db.record_llm_spend(utcnow(), scorer.model, scorer.usage, report.llm_cost_usd)
    remaining = db.count_pending_scores(max_attempts)
    log.info(
        "LLM usage: %d calls, in=%d out=%d cache_read=%d cache_write=%d tokens, ≈$%.4f (%s); %d jobs still pending",
        scorer.usage.calls, scorer.usage.input_tokens, scorer.usage.output_tokens, scorer.usage.cache_read_tokens,
        scorer.usage.cache_write_tokens, report.llm_cost_usd, scorer.model, remaining,
    )


def _notify(db: Database, settings: Settings, report: RunReport, dry_run: bool, out: Callable[[str], None]) -> None:
    rows = [dict(r) for r in db.jobs_to_notify(settings.min_score, settings.require_location_fit)]
    if not rows:
        log.info("nothing new to notify (min_score=%d)", settings.min_score)
        return
    messages = telegram.build_digest(rows)
    if dry_run:
        out(f"--- DRY RUN: would send {len(rows)} jobs in {len(messages)} Telegram message(s) ---")
        for text, _ in messages:
            out(text + "\n---")
        return
    token, chat_id = secret("TELEGRAM_BOT_TOKEN"), secret("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing in .env; %d jobs not sent", len(rows))
        return
    for text, ids in messages:
        try:
            telegram.send_message(token, chat_id, text)
        except telegram.TelegramError as exc:
            log.error("Telegram send failed, will retry next run: %s", exc)
            return
        db.mark_notified(ids, utcnow())
        report.notified += len(ids)
    log.info("sent %d jobs to Telegram in %d message(s)", report.notified, len(messages))


def _credit_messages(
    db: Database, settings: Settings, report: RunReport, now_local, dry_run: bool, out: Callable[[str], None]
) -> None:
    today = now_local.date().isoformat()
    if report.credit_exhausted and (dry_run or db.get_meta("credit_alert_date") != today):
        if _send_or_print(settings, credit.CREDIT_EXHAUSTED_MESSAGE, dry_run, out) and not dry_run:
            db.set_meta("credit_alert_date", today)
    cfg = settings.credit
    if not cfg.get("report_daily", True) or db.get_meta("credit_report_date") == today:
        return
    status = credit.credit_status(db, cfg, now_local)
    warn = cfg.get("warn_below_usd")
    text = credit.format_credit_message(status, float(warn) if warn is not None else None)
    log.info("credit: yesterday $%.4f, 30d $%.4f, estimated remaining %s", status.yesterday, status.last_30_days,
             "n/a" if status.remaining is None else f"${status.remaining:.2f}")
    if _send_or_print(settings, text, dry_run, out) and not dry_run:
        db.set_meta("credit_report_date", today)


def _send_or_print(settings: Settings, text: str, dry_run: bool, out: Callable[[str], None]) -> bool:
    """Send a Telegram message (print it on dry-run). Returns True if it was sent."""
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


def _print_unscored(db: Database, settings: Settings, out: Callable[[str], None]) -> None:
    rows = db.jobs_to_score(int(settings.llm.get("max_attempts", 3)), 50)
    out(f"--- DRY RUN (--no-llm): {len(rows)} jobs waiting to be scored ---")
    for row in rows:
        out(f"• {row['title']} | {row['company']} | {row['location']} | {row['source']} | {row['url']}")


def check_sources(settings: Settings, names: list[str] | None = None, out: Callable[[str], None] = print) -> bool:
    """Fetch each source live and print counts. No DB writes. Returns True if every source worked."""
    now = utcnow()
    all_ok = True
    for name in names or enabled_sources(settings.sources):
        cfg = settings.sources.get(name) or {}
        result = fetch_source(name, cfg)
        passed = [
            job for job in result.jobs
            if prefilter(job, settings.prefilter, settings.exclude_companies, settings.max_job_age_days, now)[0]
        ]
        status = "OK  " if result.ok else "FAIL"
        all_ok &= result.ok
        out(f"{status} {name:<11} {len(result.jobs):>5} jobs  {len(passed):>4} pass prefilter  {result.seconds:5.1f}s"
            + (f"  error: {result.error}" if result.error else ""))
        for warning in result.warnings:
            out(f"       warning: {warning}")
        for job in passed[:3]:
            out(f"       e.g. {job.title} | {job.company} | {job.location}")
    return all_ok
