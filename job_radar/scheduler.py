"""Built-in scheduler for Docker/Linux: runs a search at the same times as the macOS launchd agent.

macOS uses launchd (scripts/search.plist.template). In Docker there is no launchd, so `python -m job_radar schedule`
runs one search at start-up, then at every even hour plus 07:00 (local time, from TZ). If the machine was asleep or
the container was stopped, the missed runs are coalesced into a single run when it comes back, like launchd does.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Callable

log = logging.getLogger("job_radar.scheduler")

# Keep in sync with StartCalendarInterval in scripts/search.plist.template.
SCHEDULE_HOURS = sorted(set(range(0, 24, 2)) | {7})


def next_run(now_local: datetime) -> datetime:
    """The first scheduled time strictly after now_local."""
    for day in (0, 1):
        for hour in SCHEDULE_HOURS:
            candidate = (now_local + timedelta(days=day)).replace(hour=hour, minute=0, second=0, microsecond=0)
            if candidate > now_local:
                return candidate
    raise AssertionError("unreachable")


def _safe(fn: Callable[[], object], what: str) -> None:
    try:
        fn()
    except Exception:  # a failing run must never stop the scheduler
        log.exception("%s failed", what)


def serve(
    run_once: Callable[[], object],
    heartbeat: Callable[[], object] | None = None,
    now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    sleep: Callable[[float], None] = time.sleep,
    max_loops: int | None = None,
) -> None:
    log.info("scheduler started; searches at %s:00 (local time)", ", ".join(f"{h:02d}" for h in SCHEDULE_HOURS))
    _safe(run_once, "start-up run")
    due = next_run(now())
    log.info("next search at %s", due.strftime("%Y-%m-%d %H:%M"))
    loops = 0
    while max_loops is None or loops < max_loops:
        loops += 1
        if heartbeat:
            _safe(heartbeat, "heartbeat")
        if now() >= due:
            _safe(run_once, "scheduled run")
            due = next_run(now())  # missed slots (sleep, downtime) collapse into this one run
            log.info("next search at %s", due.strftime("%Y-%m-%d %H:%M"))
        sleep(max(1.0, min(60.0, (due - now()).total_seconds())))
