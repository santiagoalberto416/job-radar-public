import copy

import httpx
import pytest

from job_radar import pipeline
from job_radar.closed import is_closed
from job_radar.db import Database
from job_radar.models import Job
from job_radar.scorer import ScoreOutcome, Usage
from job_radar.sources import FetchResult
from tests.helpers import example_settings

PAGES = {
    "/gone": (404, ""),
    "/removed": (410, ""),
    "/linkedin-closed": (200, "<html>Senior Dev · No longer accepting applications</html>"),
    "/indeed-expired": (200, "<h1>This job has expired on Indeed</h1>"),
    "/open": (200, "<html>Apply now</html>"),
    "/blocked": (999, ""),
}


def transport():
    def handler(request):
        if request.url.path == "/error":
            raise httpx.ConnectError("boom")
        status, body = PAGES[request.url.path]
        return httpx.Response(status, text=body)

    return httpx.MockTransport(handler)


@pytest.mark.parametrize(
    "path, closed",
    [("/gone", True), ("/removed", True), ("/linkedin-closed", True), ("/indeed-expired", True),
     ("/open", False), ("/blocked", False), ("/error", False)],
)
def test_is_closed(path, closed):
    with httpx.Client(transport=transport()) as client:
        assert is_closed(f"https://jobs.example{path}", client) is closed


def test_closed_postings_are_not_sent(tmp_path, monkeypatch):
    settings = example_settings()
    settings.raw = copy.deepcopy(settings.raw)
    settings.raw.update({"db_path": str(tmp_path / "jobs.db"), "sources": {"linkedin": {}}, "notify_window": None,
                         "credit": {"report_daily": False}, "weekly_summary": {"enabled": False},
                         "closed_check": {"enabled": True, "delay_seconds": 0}})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    sent = []
    monkeypatch.setattr(pipeline.telegram, "send_message", lambda *a, **kw: sent.append(a[2]))
    monkeypatch.setattr(pipeline, "http_client", lambda timeout=30: httpx.Client(transport=transport()))

    class FakeScorer:
        model = "m"

        def __init__(self, **kw):
            self.usage = Usage()

        def score(self, job):
            return ScoreOutcome(result={"score": 90, "fits_location": True, "seniority": "senior", "reason": "ok",
                                        "red_flags": [], "salary_usd_month": 0})

    monkeypatch.setattr(pipeline, "Scorer", FakeScorer)
    jobs = [Job(source="linkedin", title="Senior Angular Developer", company="A", url="https://jobs.example/open"),
            Job(source="linkedin", title="Senior React Engineer", company="B", url="https://jobs.example/linkedin-closed")]
    monkeypatch.setattr(pipeline, "fetch_source", lambda name, cfg: FetchResult(name, jobs=jobs))
    pipeline.run(settings)
    assert "Senior Angular Developer" in sent[0] and "Senior React Engineer" not in sent[0]
    db = Database(settings.db_path)
    closed = db.conn.execute("SELECT title FROM jobs WHERE closed_at IS NOT NULL").fetchall()
    assert [r[0] for r in closed] == ["Senior React Engineer"]
    assert db.jobs_to_notify(70, True) == []  # the closed one is never offered again
    db.close()
