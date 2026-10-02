"""Source registry. Each source returns (jobs, warnings) or raises if it failed completely."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..models import Job
from ..util import http_client, short_error
from .ats import fetch_ats
from .boards import (
    fetch_getonboard, fetch_himalayas, fetch_jobicy, fetch_remoteok, fetch_remotive, fetch_workingnomads,
)
from .computrabajo import fetch_computrabajo, fetch_description as computrabajo_description
from .jobspy_source import SITE_FOR_SOURCE, fetch_jobspy

HTTP_SOURCES = {
    "greenhouse": lambda cfg, client: fetch_ats("greenhouse", cfg, client),
    "lever": lambda cfg, client: fetch_ats("lever", cfg, client),
    "ashby": lambda cfg, client: fetch_ats("ashby", cfg, client),
    "remotive": fetch_remotive,
    "remoteok": fetch_remoteok,
    "getonboard": fetch_getonboard,
    "himalayas": fetch_himalayas,
    "jobicy": fetch_jobicy,
    "workingnomads": fetch_workingnomads,
    "computrabajo": fetch_computrabajo,
}
# Sources whose listings have no description: fetch it per job, only for new jobs that passed the prefilter.
DESCRIPTION_FETCHERS = {"computrabajo": computrabajo_description}
KNOWN_SOURCES = set(HTTP_SOURCES) | set(SITE_FOR_SOURCE)


@dataclass
class FetchResult:
    source: str
    jobs: list[Job] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None


def enabled_sources(sources_cfg: dict[str, dict[str, Any]]) -> list[str]:
    return [name for name, cfg in sources_cfg.items() if (cfg or {}).get("enabled", True)]


def fetch_source(name: str, cfg: dict[str, Any]) -> FetchResult:
    """Fetch one source; never raises (errors are captured in the result)."""
    start = time.monotonic()
    result = FetchResult(source=name)
    try:
        if name in HTTP_SOURCES:
            with http_client() as client:
                result.jobs, result.warnings = HTTP_SOURCES[name](cfg, client)
        elif name in SITE_FOR_SOURCE or cfg.get("site"):
            result.jobs, result.warnings = fetch_jobspy(name, cfg)
        else:
            result.error = f"unknown source '{name}'"
    except Exception as exc:  # one failing source must never break the run
        result.error = short_error(exc)
    result.seconds = time.monotonic() - start
    return result
