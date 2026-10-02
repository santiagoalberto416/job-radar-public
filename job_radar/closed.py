"""Check that a posting is still open before sending it (a closed vacancy is a waste of your time).

Conservative: only a clear signal counts as closed (HTTP 404/410, or a "no longer accepting applications" page).
Anything else, including blocked or failed requests, counts as open, so nothing is lost.
"""

from __future__ import annotations

import logging

import httpx

from .sources.computrabajo import BROWSER_HEADERS
from .util import short_error

log = logging.getLogger("job_radar.closed")

CLOSED_MARKERS = [
    "no longer accepting applications",       # LinkedIn
    "this job has expired",                   # Indeed
    "this job is no longer available",
    "job is no longer available",
    "position has been filled",
    "this position is no longer open",
    "this job posting is no longer",
    "the job you are looking for is no longer",
    "posting has been closed",
    "la oferta ya no está disponible",
    "esta oferta ya no está disponible",
    "esta vacante ya no está disponible",
    "la vacante ha sido cerrada",
    "oferta finalizada",
]


def is_closed(url: str, client: httpx.Client) -> bool:
    try:
        response = client.get(url, headers=BROWSER_HEADERS, follow_redirects=True)
    except httpx.HTTPError as exc:
        log.info("could not check %s (%s); treating it as open", url, short_error(exc))
        return False
    if response.status_code in (404, 410):
        return True
    if response.status_code != 200:
        return False  # blocked (403/429/999) or a server error: unknown, keep it
    page = response.text[:400_000].lower()
    return any(marker in page for marker in CLOSED_MARKERS)
