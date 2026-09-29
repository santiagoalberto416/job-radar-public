"""JobSpy scrapers (Indeed, LinkedIn guest search, Google Jobs). Never logs in to anything."""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from ..models import Job
from ..util import short_error, to_iso

# Source name -> JobSpy site name. indeed_mx / indeed_us differ only by `country` in config.
SITE_FOR_SOURCE = {"indeed_mx": "indeed", "indeed_us": "indeed", "linkedin": "linkedin", "google": "google"}


def _clean(value: Any) -> Any:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return value


def _salary(record: dict[str, Any]) -> str | None:
    low, high = _clean(record.get("min_amount")), _clean(record.get("max_amount"))
    if low is None and high is None:
        return None
    currency = _clean(record.get("currency")) or ""
    interval = _clean(record.get("interval")) or ""
    amounts = "-".join(f"{int(v):,}" for v in (low, high) if v is not None)
    return f"{currency} {amounts} {interval}".strip()


def parse_jobspy(records: list[dict[str, Any]], source: str) -> list[Job]:
    jobs = []
    for record in records:
        title = _clean(record.get("title"))
        url = _clean(record.get("job_url")) or _clean(record.get("job_url_direct"))
        if not title or not url:
            continue
        posted = _clean(record.get("date_posted"))
        jobs.append(
            Job(
                source=source,
                external_id=_clean(record.get("id")),
                title=str(title).strip(),
                company=str(_clean(record.get("company")) or "").strip(),
                url=str(url),
                location=str(_clean(record.get("location")) or ""),
                description=str(_clean(record.get("description")) or ""),
                posted_at=to_iso(str(posted)) if posted is not None else None,
                remote=_clean(record.get("is_remote")),
                salary=_salary(record),
            )
        )
    return jobs


def _quiet_jobspy_loggers() -> None:
    """JobSpy logs every scrape at INFO on "JobSpy:<Site>" loggers, some created lazily mid-run.
    Its create_logger() leaves loggers that already have a handler alone, so pre-create them at WARNING."""
    names = {n for n in logging.Logger.manager.loggerDict if n.startswith("JobSpy")}
    names |= {f"JobSpy:{site}" for site in ("Linkedin", "LinkedIn", "Indeed", "Google", "Glassdoor", "ZipRecruiter")}
    for name in names:
        logger = logging.getLogger(name)
        if not logger.handlers:
            logger.addHandler(logging.StreamHandler())
            logger.propagate = False
        logger.setLevel(logging.WARNING)


def fetch_jobspy(source: str, cfg: dict[str, Any]) -> tuple[list[Job], list[str]]:
    from jobspy import scrape_jobs  # heavy import (pandas); only load when used

    _quiet_jobspy_loggers()
    site = cfg.get("site") or SITE_FOR_SOURCE[source]
    searches = cfg.get("searches") or []
    jobs: list[Job] = []
    warnings: list[str] = []
    for i, search in enumerate(searches):
        if i:
            time.sleep(float(cfg.get("delay_seconds", 3)))
        kwargs: dict[str, Any] = {
            "site_name": [site],
            "results_wanted": int(cfg.get("results_wanted", 20)),
            "description_format": "markdown",
            "verbose": 0,
        }
        if site == "google":
            kwargs["google_search_term"] = search["term"]
        else:
            kwargs["search_term"] = search["term"]
            if search.get("location"):
                kwargs["location"] = search["location"]
            if search.get("remote"):
                kwargs["is_remote"] = True
            if cfg.get("hours_old"):
                kwargs["hours_old"] = int(cfg["hours_old"])
        if site == "indeed":
            kwargs["country_indeed"] = cfg.get("country", "usa")
        if site == "linkedin":
            kwargs["linkedin_fetch_description"] = bool(cfg.get("fetch_description", True))
        try:
            frame = scrape_jobs(**kwargs)
            jobs.extend(parse_jobspy(frame.to_dict("records"), source))
        except Exception as exc:  # JobSpy raises many different exception types
            warnings.append(f"{source}:{search['term']!r}: {short_error(exc)}")
    if searches and len(warnings) == len(searches):
        raise RuntimeError(f"all {source} searches failed: {'; '.join(warnings[:3])}")
    return jobs, warnings
