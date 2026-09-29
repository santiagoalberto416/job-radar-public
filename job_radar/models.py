"""The normalized Job record and the keys used to dedupe it."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query params that identify a job; everything else (utm_*, refId, trk, ...) is dropped.
_KEEP_QUERY_PARAMS = {"jk", "gh_jid", "id", "jobid", "job_id"}
_COMPANY_SUFFIXES = re.compile(
    r"\b(inc|llc|ltd|limited|corp|corporation|co|gmbh|s ?a ?de ?c ?v|s ?a|sas|srl|bv|plc)\b"
)


@dataclass
class Job:
    source: str
    title: str
    company: str
    url: str
    location: str = ""
    description: str = ""
    posted_at: str | None = None  # ISO-8601 date or datetime, if the source gives one
    remote: bool | None = None
    salary: str | None = None
    external_id: str | None = None

    @property
    def url_key(self) -> str:
        return normalize_url(self.url)

    @property
    def company_title_key(self) -> str:
        return f"{normalize_company(self.company)}|{normalize_text(self.title)}"


def normalize_text(value: str | None) -> str:
    """Lowercase, strip accents and punctuation, collapse spaces."""
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def normalize_company(value: str | None) -> str:
    text = _COMPANY_SUFFIXES.sub(" ", normalize_text(value))
    return " ".join(text.split())


def normalize_url(url: str) -> str:
    """Canonical form of a job URL so the same posting from two links dedupes."""
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.endswith("indeed.com"):  # mx.indeed.com / www.indeed.com / indeed.com
        host = "indeed.com"
    path = parts.path.rstrip("/")
    if host.endswith("linkedin.com"):
        match = re.search(r"/jobs/view/(?:[^/]*?-)?(\d+)", path)
        if match:
            return f"linkedin.com/jobs/view/{match.group(1)}"
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if k.lower() in _KEEP_QUERY_PARAMS))
    return urlunsplit(("", host, path, query, "")).lstrip("/")
