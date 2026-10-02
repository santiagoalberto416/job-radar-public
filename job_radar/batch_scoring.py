"""Score jobs with the Message Batches API: 50% cheaper, results usually within minutes (up to 24 h).

Enabled with `llm.batch: true`. Each run:
1. collects the results of batches submitted by earlier runs;
2. submits the new jobs as one batch (they're marked so no other run scores them again);
3. waits up to `llm.batch_wait_minutes` (10 by default); if the batch isn't done by then, the next run collects it.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

import anthropic

from .db import Database
from .scorer import CreditExhaustedError, Scorer, ScorerFatalError, Usage, _is_credit_error, parse_response
from .util import utcnow

log = logging.getLogger("job_radar.batch")

BATCH_DISCOUNT = 0.5
POLL_SECONDS = 15


def _api_call(fn: Callable[[], Any]) -> Any:
    """Run one Batches API call, translating errors like Scorer.score() does."""
    try:
        return fn()
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        raise ScorerFatalError(f"Anthropic auth error ({exc.status_code}); check ANTHROPIC_API_KEY") from exc
    except anthropic.RateLimitError as exc:
        raise ScorerFatalError("rate limited by the Anthropic API; will retry next run") from exc
    except anthropic.APIStatusError as exc:
        if _is_credit_error(exc):
            raise CreditExhaustedError("Anthropic credit balance is too low") from exc
        raise ScorerFatalError(f"Batches API error {exc.status_code}: {str(exc.message)[:200]}") from exc
    except anthropic.APIConnectionError as exc:
        raise ScorerFatalError(f"cannot reach the Anthropic API: {type(exc).__name__}") from exc


def _collect(db: Database, client: Any, batch_id: str, model: str, report: Any, usage: Usage) -> bool:
    """Save the results of a finished batch. Returns False if it's still running."""
    try:
        batch = _api_call(lambda: client.messages.batches.retrieve(batch_id))
    except ScorerFatalError as exc:
        if "404" in str(exc):  # the batch no longer exists: release its jobs so they're scored again
            db.clear_batch(batch_id)
            return True
        raise
    if batch.processing_status != "ended":
        return False
    for result in _api_call(lambda: client.messages.batches.results(batch_id)):
        job_id = int(str(result.custom_id).removeprefix("job-"))
        kind = result.result.type
        if kind == "succeeded":
            message = result.result.message
            usage.add(message.usage)
            outcome = parse_response(message)
            if outcome.result:
                db.save_score(job_id, outcome.result, model, utcnow())
                db.fill_salary_from_llm(job_id, outcome.result.get("salary_usd_month"))
                report.scored += 1
                job = db.job(job_id)
                log.info("scored %3d %s | %s @ %s (batch)", outcome.result["score"],
                         "✓" if outcome.result["fits_location"] else "✗", job["title"], job["company"])
            else:
                db.record_score_failure(job_id, outcome.error or "unknown error")
                report.score_failures += 1
        elif kind == "errored":
            db.record_score_failure(job_id, f"batch error: {str(getattr(result.result, 'error', ''))[:200]}")
            report.score_failures += 1
        # canceled / expired: nothing saved; the job is released below and retried next run
    db.clear_batch(batch_id)
    return True


def score_in_batch(
    db: Database,
    scorer: Scorer,
    rows: list,
    report: Any,
    wait_minutes: float = 10,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> Usage:
    client = scorer.client
    usage = Usage()
    try:
        for batch_id in db.pending_batch_ids():
            if _collect(db, client, batch_id, scorer.model, report, usage):
                log.info("collected batch %s from an earlier run", batch_id)
        if rows:
            requests = [{"custom_id": f"job-{row['id']}", "params": scorer.build_request(dict(row))} for row in rows]
            batch = _api_call(lambda: client.messages.batches.create(requests=requests))
            db.set_batch([row["id"] for row in rows], batch.id)
            log.info("submitted %d jobs for scoring in batch %s (50%% cheaper)", len(rows), batch.id)
            deadline = monotonic() + wait_minutes * 60
            while not _collect(db, client, batch.id, scorer.model, report, usage):
                if monotonic() >= deadline:
                    log.info("batch %s still running after %.0f min; the next run collects it", batch.id, wait_minutes)
                    break
                sleep(POLL_SECONDS)
    finally:
        cost = usage.cost_usd(scorer.model) * BATCH_DISCOUNT
        if usage.calls:
            db.record_llm_spend(utcnow(), scorer.model, usage, cost)
        report.llm_cost_usd += cost
    return usage
