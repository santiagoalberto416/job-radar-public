"""Public ATS job-board APIs: Greenhouse, Lever, Ashby. One request per company."""

from __future__ import annotations

import time
from typing import Any

import httpx

from ..models import Job
from ..util import html_to_text, short_error, to_iso


def _company_name(token: str) -> str:
    return token.replace("-", " ").title() if token.islower() else token


def parse_greenhouse(data: dict[str, Any], token: str) -> list[Job]:
    jobs = []
    for item in data.get("jobs", []):
        location = (item.get("location") or {}).get("name") or ""
        jobs.append(
            Job(
                source="greenhouse",
                external_id=str(item.get("id")),
                title=item.get("title", "").strip(),
                company=item.get("company_name") or _company_name(token),
                url=item.get("absolute_url", ""),
                location=location,
                description=html_to_text(item.get("content")),
                posted_at=to_iso(item.get("first_published") or item.get("updated_at")),
                remote="remote" in location.lower() or None,
            )
        )
    return jobs


def parse_lever(data: list[dict[str, Any]], token: str) -> list[Job]:
    jobs = []
    for item in data:
        categories = item.get("categories") or {}
        locations = categories.get("allLocations") or [categories.get("location")]
        location = ", ".join(loc for loc in locations if loc)
        workplace = item.get("workplaceType") or ""
        if workplace and workplace != "unspecified":
            location = f"{location} ({workplace})" if location else workplace
        sections = [item.get("descriptionPlain") or ""]
        for block in item.get("lists") or []:
            sections.append(f"{block.get('text', '')}\n{html_to_text(block.get('content'))}")
        sections.append(item.get("additionalPlain") or "")
        salary = item.get("salaryRange") or {}
        jobs.append(
            Job(
                source="lever",
                external_id=item.get("id"),
                title=(item.get("text") or "").strip(),
                company=_company_name(token),
                url=item.get("hostedUrl", ""),
                location=location,
                description="\n\n".join(s.strip() for s in sections if s and s.strip()),
                posted_at=to_iso(item.get("createdAt")),
                remote=workplace == "remote" or None,
                salary=(
                    f"{salary.get('min')}-{salary.get('max')} {salary.get('currency', '')} {salary.get('interval', '')}".strip()
                    if salary.get("min")
                    else None
                ),
            )
        )
    return jobs


def parse_ashby(data: dict[str, Any], token: str) -> list[Job]:
    jobs = []
    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue
        locations = [item.get("location") or ""]
        locations += [loc.get("location", "") for loc in item.get("secondaryLocations") or []]
        location = ", ".join(loc for loc in locations if loc)
        workplace = item.get("workplaceType") or ""
        if workplace:
            location = f"{location} ({workplace})" if location else workplace
        jobs.append(
            Job(
                source="ashby",
                external_id=item.get("id"),
                title=(item.get("title") or "").strip(),
                company=_company_name(token),
                url=item.get("jobUrl", ""),
                location=location,
                description=item.get("descriptionPlain") or html_to_text(item.get("descriptionHtml")),
                posted_at=to_iso(item.get("publishedAt")),
                remote=bool(item.get("isRemote")) or None,
            )
        )
    return jobs


ATS = {
    "greenhouse": (
        lambda token: f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
        parse_greenhouse,
    ),
    "lever": (lambda token: f"https://api.lever.co/v0/postings/{token}?mode=json", parse_lever),
    "ashby": (lambda token: f"https://api.ashbyhq.com/posting-api/job-board/{token}", parse_ashby),
}


def fetch_ats(kind: str, cfg: dict[str, Any], client: httpx.Client) -> tuple[list[Job], list[str]]:
    """Fetch every configured company. A failing company is a warning; all failing is an error."""
    url_for, parse = ATS[kind]
    companies: list[str] = cfg.get("companies") or []
    delay = float(cfg.get("delay_seconds", 0.5))
    jobs: list[Job] = []
    warnings: list[str] = []
    for i, token in enumerate(companies):
        if i:
            time.sleep(delay)
        try:
            response = client.get(url_for(token))
            response.raise_for_status()
            jobs.extend(parse(response.json(), token))
        except (httpx.HTTPError, ValueError) as exc:
            warnings.append(f"{kind}:{token}: {short_error(exc)}")
    if companies and len(warnings) == len(companies):
        raise RuntimeError(f"all {kind} companies failed: {'; '.join(warnings[:3])}")
    return jobs, warnings

