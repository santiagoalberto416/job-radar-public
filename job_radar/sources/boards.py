"""Remote job boards: Remotive, Remote OK, Get on Board."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from ..models import Job
from ..util import fix_mojibake, html_to_text, short_error, to_iso


# --- Remotive (https://remotive.com/api-documentation) ---
def parse_remotive(data: dict[str, Any]) -> list[Job]:
    jobs = []
    for item in data.get("jobs", []):
        jobs.append(
            Job(
                source="remotive",
                external_id=str(item.get("id")),
                title=(item.get("title") or "").strip(),
                company=item.get("company_name") or "",
                url=item.get("url", ""),
                location=f"Remote ({item.get('candidate_required_location') or 'anywhere'})",
                description=html_to_text(item.get("description")),
                posted_at=to_iso(item.get("publication_date")),
                remote=True,
                salary=item.get("salary") or None,
            )
        )
    return jobs


def fetch_remotive(cfg: dict[str, Any], client: httpx.Client) -> tuple[list[Job], list[str]]:
    params = {"category": cfg.get("category", "software-dev")}
    response = client.get("https://remotive.com/api/remote-jobs", params=params)
    response.raise_for_status()
    return parse_remotive(response.json()), []


# --- Remote OK (https://remoteok.com/api). The first element is a legal notice, not a job.
# Its JSON double-encodes UTF-8, hence fix_mojibake. ---
def parse_remoteok(data: list[dict[str, Any]]) -> list[Job]:
    jobs = []
    for item in data[1:]:
        if not item.get("position"):
            continue
        low, high = item.get("salary_min"), item.get("salary_max")
        salary = f"USD {low}-{high}/year" if low and str(low) != "0" else None
        jobs.append(
            Job(
                source="remoteok",
                external_id=str(item.get("id")),
                title=fix_mojibake(item["position"]).strip(),
                company=fix_mojibake(item.get("company")).strip(),
                url=item.get("url") or item.get("apply_url", ""),
                location=f"Remote ({fix_mojibake(item.get('location')) or 'anywhere'})",
                description=html_to_text(fix_mojibake(item.get("description"))),
                posted_at=to_iso(item.get("date") or item.get("epoch")),
                remote=True,
                salary=salary,
            )
        )
    return jobs


def fetch_remoteok(cfg: dict[str, Any], client: httpx.Client) -> tuple[list[Job], list[str]]:
    response = client.get("https://remoteok.com/api")
    response.raise_for_status()
    return parse_remoteok(response.json()), []


# --- Get on Board (https://www.getonbrd.com/api-doc) ---
_GOB_MODALITY = {
    "fully_remote": "Remote",
    "remote_local": "Remote (local residents)",
    "temporarily_remote": "Temporarily remote",
    "hybrid": "Hybrid",
    "no_remote": "On-site",
}


def parse_getonboard(data: dict[str, Any]) -> list[Job]:
    jobs = []
    for item in data.get("data", []):
        attrs = item.get("attributes") or {}
        company = (((attrs.get("company") or {}).get("data") or {}).get("attributes") or {}).get("name", "")
        modality = _GOB_MODALITY.get(attrs.get("remote_modality") or "", "")
        countries = ", ".join(c for c in attrs.get("countries") or [] if c.lower() != "remote")
        location = " · ".join(part for part in (modality, countries) if part)
        sections = [attrs.get(key) for key in ("description", "functions", "desirable", "benefits")]
        low, high = attrs.get("min_salary"), attrs.get("max_salary")
        jobs.append(
            Job(
                source="getonboard",
                external_id=item.get("id"),
                title=(attrs.get("title") or "").strip(),
                company=company,
                url=(item.get("links") or {}).get("public_url", ""),
                location=location,
                description="\n\n".join(html_to_text(s) for s in sections if s),
                posted_at=to_iso(attrs.get("published_at")),
                remote=bool(attrs.get("remote")),
                salary=f"USD {low}-{high}/month" if low or high else None,
            )
        )
    return jobs


def fetch_getonboard(cfg: dict[str, Any], client: httpx.Client) -> tuple[list[Job], list[str]]:
    queries = cfg.get("queries") or ["frontend"]
    jobs: list[Job] = []
    warnings: list[str] = []
    for i, query in enumerate(queries):
        if i:
            time.sleep(float(cfg.get("delay_seconds", 1)))
        try:
            response = client.get(
                "https://www.getonbrd.com/api/v0/search/jobs",
                params={"query": query, "per_page": cfg.get("per_page", 50), "expand": json.dumps(["company"])},
            )
            response.raise_for_status()
            jobs.extend(parse_getonboard(response.json()))
        except (httpx.HTTPError, ValueError) as exc:
            warnings.append(f"getonboard:{query}: {short_error(exc)}")
    if len(warnings) == len(queries):
        raise RuntimeError(f"all getonboard queries failed: {'; '.join(warnings[:3])}")
    return jobs, warnings


# --- Himalayas (https://himalayas.app/api). Free; data refreshes daily, so poll once a day. ---
def _salary_text(low: Any, high: Any, currency: Any, period: Any) -> str | None:
    if not low and not high:
        return None
    amounts = "-".join(f"{int(v):,}" for v in (low, high) if v)
    return f"{currency or ''} {amounts}/{period or 'year'}".strip()


def parse_himalayas(data: dict[str, Any]) -> list[Job]:
    jobs = []
    for item in data.get("jobs", []):
        countries = item.get("locationRestrictions") or []
        jobs.append(
            Job(
                source="himalayas",
                external_id=item.get("guid"),
                title=(item.get("title") or "").strip(),
                company=item.get("companyName") or "",
                url=item.get("applicationLink") or item.get("guid") or "",
                location=f"Remote ({', '.join(countries) if countries else 'worldwide'})",
                description=html_to_text(item.get("description")) or item.get("excerpt") or "",
                posted_at=to_iso(item.get("pubDate")),
                remote=True,
                salary=_salary_text(item.get("minSalary"), item.get("maxSalary"), item.get("currency"),
                                    item.get("salaryPeriod")),
            )
        )
    return jobs


def fetch_himalayas(cfg: dict[str, Any], client: httpx.Client) -> tuple[list[Job], list[str]]:
    queries = cfg.get("queries") or ["frontend"]
    pages = int(cfg.get("pages", 2))
    jobs: list[Job] = []
    warnings: list[str] = []
    requests = 0
    for query in queries:
        for page in range(1, pages + 1):
            if requests:
                time.sleep(float(cfg.get("delay_seconds", 1)))
            requests += 1
            params = {"q": query, "country": cfg.get("country", "MX"), "sort": "recent", "page": page}
            try:
                response = client.get("https://himalayas.app/jobs/api/search", params=params)
                response.raise_for_status()
                batch = parse_himalayas(response.json())
            except (httpx.HTTPError, ValueError) as exc:
                warnings.append(f"himalayas:{query} p{page}: {short_error(exc)}")
                break
            jobs.extend(batch)
            if len(batch) < 20:  # last page
                break
    if queries and len(warnings) == len(queries) and not jobs:
        raise RuntimeError(f"all himalayas queries failed: {'; '.join(warnings[:3])}")
    return jobs, warnings


# --- Jobicy (https://jobi.cy/apidocs). Must credit Jobicy and link to its listing; poll at most hourly. ---
def parse_jobicy(data: dict[str, Any]) -> list[Job]:
    jobs = []
    for item in data.get("jobs", []):
        geo = " ".join((item.get("jobGeo") or "").split())
        jobs.append(
            Job(
                source="jobicy",
                external_id=str(item.get("id")),
                title=html_to_text(item.get("jobTitle")).strip(),
                company=html_to_text(item.get("companyName")),
                url=item.get("url", ""),
                location=f"Remote ({geo or 'anywhere'})",
                description=html_to_text(item.get("jobDescription")) or item.get("jobExcerpt") or "",
                posted_at=to_iso(item.get("pubDate")),
                remote=True,
                salary=_salary_text(item.get("salaryMin"), item.get("salaryMax"), item.get("salaryCurrency"),
                                    item.get("salaryPeriod")),
            )
        )
    return jobs


def fetch_jobicy(cfg: dict[str, Any], client: httpx.Client) -> tuple[list[Job], list[str]]:
    geos = cfg.get("geos") or ["mexico", "latam"]
    jobs: list[Job] = []
    warnings: list[str] = []
    for i, geo in enumerate(geos):
        if i:
            time.sleep(float(cfg.get("delay_seconds", 1)))
        params = {"count": cfg.get("count", 50), "geo": geo, "industry": cfg.get("industry", "engineering")}
        try:
            response = client.get("https://jobicy.com/api/v2/remote-jobs", params=params)
            response.raise_for_status()
            jobs.extend(parse_jobicy(response.json()))
        except (httpx.HTTPError, ValueError) as exc:
            warnings.append(f"jobicy:{geo}: {short_error(exc)}")
    if geos and len(warnings) == len(geos):
        raise RuntimeError(f"all jobicy queries failed: {'; '.join(warnings[:3])}")
    return jobs, warnings
