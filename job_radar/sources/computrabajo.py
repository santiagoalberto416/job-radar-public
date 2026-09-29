"""Computrabajo Mexico (mx.computrabajo.com) public search pages.

Computrabajo has no API, its legal notice forbids automated access, and it answers 403 to clients that identify
as bots. The user chose to use it anyway (2026-09-27), so this source sends browser-like headers and is kept
deliberately slow: a few searches every `min_interval_hours`, a pause between requests, and job detail pages are
fetched only for new jobs that already passed the prefilter (see `fetch_description`).
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup

from ..models import Job, normalize_text
from ..util import html_to_text, short_error

BASE_URL = "https://mx.computrabajo.com"
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
}
_RATING = re.compile(r"^\d+(?:[.,]\d+)?\s+")
_MODALITIES = ("presencial y remoto", "remoto", "presencial", "hibrido")


def search_url(term: str, location: str | None = None) -> str:
    slug = normalize_text(term).replace(" ", "-")
    if location:
        slug += "-en-" + normalize_text(location).replace(" ", "-")
    return f"{BASE_URL}/trabajo-de-{slug}"


def parse_relative_date(text: str, now: datetime) -> str | None:
    """'Hace 3 horas' / 'Ayer' / 'Hace 2 días' / 'Hace más de 30 días' -> ISO date."""
    value = normalize_text(text)
    if not value:
        return None
    if value.startswith("hoy") or re.search(r"\b(minuto|minutos|hora|horas)\b", value):
        days = 0
    elif value.startswith("ayer"):
        days = 1
    elif match := re.search(r"(\d+)\s+dias?\b", value):
        days = int(match.group(1)) + (1 if "mas de" in value else 0)
    else:
        return None
    return (now - timedelta(days=days)).date().isoformat()


def _text(node) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def parse_computrabajo(html: str, now: datetime | None = None) -> list[Job]:
    now = now or datetime.now(timezone.utc)
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for article in soup.select("article.box_offer"):
        link = article.select_one("h2 a.js-o-link")
        if not link or not link.get("href"):
            continue
        paragraphs = [_text(p) for p in article.find_all("p", recursive=False)]
        company = _RATING.sub("", paragraphs[0]) if paragraphs else ""
        city = paragraphs[1] if len(paragraphs) > 1 else ""
        posted = parse_relative_date(paragraphs[-1], now) if len(paragraphs) > 2 else None
        extras = [_text(span) for div in article.select("div.fs13") for span in div.find_all("span", recursive=False)]
        salary = next((e for e in extras if "$" in e), None)
        modality = next((e for e in extras if normalize_text(e) in _MODALITIES), None)
        location = f"{city} ({modality})" if modality and city else (city or modality or "")
        jobs.append(
            Job(
                source="computrabajo",
                external_id=article.get("data-id"),
                title=_text(link),
                company=company,
                url=BASE_URL + link["href"].split("#", 1)[0],
                location=location,
                posted_at=posted,
                remote=True if modality and normalize_text(modality) == "remoto" else None,
                salary=f"MXN {salary.replace('$', '').strip()}" if salary else None,
            )
        )
    return jobs


def parse_description(html: str) -> str:
    """The 'Descripción de la oferta' block of a job detail page, as text."""
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find(lambda tag: tag.name in ("h2", "h3") and "Descripci" in tag.get_text())
    block = heading.parent if heading else None
    if block is None:
        return ""
    heading.extract()
    return html_to_text(str(block))


def fetch_computrabajo(cfg: dict[str, Any], client: httpx.Client) -> tuple[list[Job], list[str]]:
    searches = cfg.get("searches") or []
    delay = float(cfg.get("delay_seconds", 5))
    jobs: list[Job] = []
    warnings: list[str] = []
    for i, search in enumerate(searches):
        if i:
            time.sleep(delay)
        try:
            response = client.get(search_url(search["term"], search.get("location")), headers=BROWSER_HEADERS)
            response.raise_for_status()
            jobs.extend(parse_computrabajo(response.text))
        except httpx.HTTPError as exc:
            warnings.append(f"computrabajo:{search['term']!r}: {short_error(exc)}")
    if searches and len(warnings) == len(searches):
        raise RuntimeError(f"all computrabajo searches failed: {'; '.join(warnings[:3])}")
    return jobs, warnings


def fetch_description(url: str, client: httpx.Client) -> str:
    response = client.get(url, headers=BROWSER_HEADERS)
    response.raise_for_status()
    return parse_description(response.text)
