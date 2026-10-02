"""Cheap keyword prefilter so only plausible jobs are sent to Claude."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Iterable

from .models import Job, normalize_text


@lru_cache(maxsize=None)
def _pattern(keyword: str) -> re.Pattern[str]:
    # Whole-word match on accent-free lowercase text; "sr" matches "Sr." but not "srv".
    return re.compile(rf"(?<![a-z0-9]){re.escape(normalize_text(keyword))}(?![a-z0-9])")


def _matches(text: str, keywords: Iterable[str]) -> str | None:
    """Return the first keyword found in text (text must already be normalize_text'ed)."""
    for keyword in keywords:
        if normalize_text(keyword) and _pattern(keyword).search(text):
            return keyword
    return None


def is_excluded_company(job: Job, excluded: Iterable[str]) -> bool:
    company = normalize_text(job.company)
    url = job.url.lower().replace("-", "").replace("_", "")
    for name in excluded:
        needle = normalize_text(name)
        if not needle:
            continue
        if _pattern(name).search(company):
            return True
        # Also catch the company's own careers URLs; skip short names to avoid false hits in URLs.
        compact = needle.replace(" ", "")
        if len(compact) >= 8 and compact in url:
            return True
    return False


def location_ok(job: Job, rules: dict[str, Any]) -> tuple[bool, str]:
    """On-site/hybrid only in `location_local`; excluded regions unless a remote-allowed region is also named."""
    location = normalize_text(job.location)
    if not location:
        return True, "unknown location"
    local = _matches(location, rules.get("location_local", []))
    if not local and (hit := _matches(location, rules.get("location_onsite_markers", []))):
        return False, f"{hit} outside location_local"
    allowed = local or _matches(location, rules.get("location_remote_allow", []))
    if not allowed and (hit := _matches(location, rules.get("location_exclude", []))):
        return False, f"location excludes '{hit}'"
    return True, "location ok"


def prefilter(
    job: Job,
    rules: dict[str, Any],
    excluded_companies: Iterable[str] = (),
    max_age_days: int | None = None,
    now: datetime | None = None,
    min_salary_usd_month: float | None = None,
    fx: dict[str, float] | None = None,
) -> tuple[bool, str]:
    """Return (passed, reason). The reason explains a rejection (or what matched)."""
    if is_excluded_company(job, excluded_companies):
        return False, "excluded company"

    title = normalize_text(job.title)
    if hit := _matches(title, rules.get("title_exclude", [])):
        return False, f"title excludes '{hit}'"

    include_hit = _matches(title, rules.get("title_include", []))
    if not include_hit:
        generic = _matches(title, rules.get("title_include_if_senior", []))
        senior = _matches(title, rules.get("senior_keywords", []))
        if not (generic and senior):
            return False, "title not relevant"
        include_hit = f"{senior} {generic}"

    ok, reason = location_ok(job, rules)
    if not ok:
        return False, reason

    if max_age_days and job.posted_at and now:
        try:
            posted = datetime.fromisoformat(job.posted_at)
            if now - posted > timedelta(days=max_age_days):
                return False, f"older than {max_age_days} days"
        except (ValueError, TypeError):
            pass

    if min_salary_usd_month and job.salary:
        from .salary import DEFAULT_FX, format_usd_month, usd_month

        usd = usd_month(job.salary, fx or DEFAULT_FX)
        if usd and usd[1] < min_salary_usd_month:
            return False, f"salary below minimum ({format_usd_month(*usd)})"

    return True, f"matched '{include_hit}'"
