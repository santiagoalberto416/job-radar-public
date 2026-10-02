import copy
from datetime import datetime

import pytest

from job_radar import pipeline
from tests.helpers import example_settings
from job_radar.db import Database
from job_radar.models import Job
from job_radar.scorer import ScoreOutcome, Usage
from job_radar.sources import FetchResult


@pytest.fixture
def env(tmp_path, monkeypatch):
    settings = example_settings()
    settings.raw = copy.deepcopy(settings.raw)
    settings.raw["db_path"] = str(tmp_path / "jobs.db")
    settings.raw["sources"] = {"greenhouse": {"min_interval_hours": 6}, "remotive": {"min_interval_hours": 6}}
    settings.raw["notify_window"] = {"start": "07:00", "end": "22:00"}
    settings.raw["credit"] = {"report_daily": False}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    sent = []
    monkeypatch.setattr(pipeline.telegram, "send_message", lambda token, chat, text, **kw: sent.append(text))
    monkeypatch.setattr(pipeline, "local_now", lambda tz=None: datetime(2026, 9, 27, 12, 0))  # inside 07-21

    class FakeScorer:
        model = "claude-opus-5"

        def __init__(self, **kwargs):
            self.usage = Usage()

        def score(self, job):
            score = 90 if "Angular" in job["title"] else 50
            return ScoreOutcome(result={"score": score, "fits_location": True, "seniority": "senior",
                                        "reason": "ok", "red_flags": []})

    monkeypatch.setattr(pipeline, "Scorer", FakeScorer)
    return settings, sent


def fake_fetch(results):
    def _fetch(name, cfg):
        return results[name]

    return _fetch


GOOD_JOBS = [
    Job(source="greenhouse", title="Senior Angular Developer", company="Acme", url="https://a/1", location="Remote"),
    Job(source="greenhouse", title="Senior React Engineer", company="Beta", url="https://a/2", location="Remote"),
    Job(source="greenhouse", title="Senior Backend Engineer", company="Beta", url="https://a/3", location="Remote"),
]


def test_one_failing_source_does_not_break_the_run(env, monkeypatch):
    settings, sent = env
    monkeypatch.setattr(pipeline, "fetch_source", fake_fetch({
        "greenhouse": FetchResult("greenhouse", jobs=GOOD_JOBS),
        "remotive": FetchResult("remotive", error="HTTP 503"),
    }))
    report = pipeline.run(settings)
    assert report.new_jobs == 3 and report.prefilter_passed == 2 and report.scored == 2
    assert report.notified == 1 and len(sent) == 1 and "Senior Angular Developer" in sent[0]

    # Second run right away: sources not due, nothing re-scored or re-sent.
    report = pipeline.run(settings)
    assert report.skipped == ["greenhouse"] and [r.source for r in report.fetched] == ["remotive"]
    assert report.scored == 0 and report.notified == 0 and len(sent) == 2  # 2nd message = all-failed warning


def test_all_sources_failing_sends_a_warning(env, monkeypatch):
    settings, sent = env
    monkeypatch.setattr(pipeline, "fetch_source", fake_fetch({
        "greenhouse": FetchResult("greenhouse", error="HTTP 500"),
        "remotive": FetchResult("remotive", error="ConnectError"),
    }))
    pipeline.run(settings)
    assert len(sent) == 1 and "todas las fuentes fallaron" in sent[0]


def test_dry_run_prints_and_does_not_mark_notified(env, monkeypatch):
    settings, sent = env
    monkeypatch.setattr(pipeline, "fetch_source", fake_fetch({
        "greenhouse": FetchResult("greenhouse", jobs=GOOD_JOBS),
        "remotive": FetchResult("remotive"),
    }))
    printed = []
    pipeline.run(settings, dry_run=True, out=printed.append)
    assert sent == [] and any("DRY RUN" in line for line in printed)
    db = Database(settings.db_path)
    assert len(db.jobs_to_notify(70, True)) == 1
    db.close()


def test_no_llm_skips_scoring(env, monkeypatch):
    settings, sent = env
    monkeypatch.setattr(pipeline, "fetch_source", fake_fetch({
        "greenhouse": FetchResult("greenhouse", jobs=GOOD_JOBS),
        "remotive": FetchResult("remotive"),
    }))
    report = pipeline.run(settings, no_llm=True)
    assert report.scored == 0 and sent == []


@pytest.mark.parametrize(
    "hhmm, expected",
    [("06:59", False), ("07:00", True), ("12:00", True), ("22:00", True), ("22:00:45", True), ("22:01", False),
     ("23:30", False)],
)
def test_notify_window_end_is_inclusive_to_the_minute(hhmm, expected):
    now = datetime.fromisoformat(f"2026-09-27T{hhmm}")
    assert pipeline.in_notify_window({"start": "07:00", "end": "22:00"}, now) is expected


def test_notify_window_across_midnight_and_disabled():
    night = {"start": "22:00", "end": "06:00"}
    assert pipeline.in_notify_window(night, datetime(2026, 9, 27, 23, 0))
    assert not pipeline.in_notify_window(night, datetime(2026, 9, 27, 12, 0))
    assert pipeline.in_notify_window(None, datetime(2026, 9, 27, 3, 0))


def test_quiet_hours_hold_matches_until_morning(env, monkeypatch):
    settings, sent = env
    fetches = {
        "greenhouse": FetchResult("greenhouse", jobs=GOOD_JOBS),
        "remotive": FetchResult("remotive", error="HTTP 503"),
    }
    monkeypatch.setattr(pipeline, "fetch_source", fake_fetch(fetches))
    monkeypatch.setattr(pipeline, "local_now", lambda tz=None: datetime(2026, 9, 27, 23, 0))
    report = pipeline.run(settings)
    assert report.scored == 2 and report.notified == 0 and sent == []  # scored at night, not sent

    fetches["greenhouse"] = FetchResult("greenhouse", error="HTTP 500")  # all failing at night: still silent
    pipeline.run(settings, force=True)
    assert sent == []

    monkeypatch.setattr(pipeline, "local_now", lambda tz=None: datetime(2026, 9, 28, 7, 0))
    report = pipeline.run(settings)
    assert report.notified == 1 and "Senior Angular Developer" in sent[0]


def test_daily_credit_message_once_per_day_with_spend(env, monkeypatch):
    settings, sent = env
    settings.raw["credit"] = {"balance_usd": 10.0, "as_of": "2026-09-27", "report_daily": True, "warn_below_usd": 2}
    monkeypatch.setattr(pipeline, "fetch_source", fake_fetch({
        "greenhouse": FetchResult("greenhouse", jobs=GOOD_JOBS),
        "remotive": FetchResult("remotive"),
    }))
    monkeypatch.setattr(pipeline, "local_now", lambda tz=None: datetime.now().astimezone())
    pipeline.run(settings)
    credit_msgs = [m for m in sent if "Crédito Claude" in m]
    assert len(credit_msgs) == 1 and "Saldo estimado" in credit_msgs[0]
    pipeline.run(settings)
    assert len([m for m in sent if "Crédito Claude" in m]) == 1  # not repeated the same day


def test_credit_exhausted_sends_one_alert_per_day(env, monkeypatch):
    settings, sent = env
    from job_radar.scorer import CreditExhaustedError

    class BrokeScorer:
        model = "claude-sonnet-5"

        def __init__(self, **kwargs):
            from job_radar.scorer import Usage
            self.usage = Usage()

        def score(self, job):
            raise CreditExhaustedError("Anthropic credit balance is too low")

    monkeypatch.setattr(pipeline, "Scorer", BrokeScorer)
    monkeypatch.setattr(pipeline, "fetch_source", fake_fetch({
        "greenhouse": FetchResult("greenhouse", jobs=GOOD_JOBS),
        "remotive": FetchResult("remotive"),
    }))
    report = pipeline.run(settings)
    assert report.credit_exhausted and report.scored == 0
    assert len([m for m in sent if "no queda crédito" in m]) == 1
    pipeline.run(settings, force=True)
    assert len([m for m in sent if "no queda crédito" in m]) == 1


def test_descriptions_fetched_only_for_new_jobs_that_pass_prefilter(env, monkeypatch):
    settings, sent = env
    settings.raw["sources"] = {"computrabajo": {"delay_seconds": 0}}
    ct_jobs = [
        Job(source="computrabajo", title="Desarrollador Angular Senior", company="A", url="https://ct/1", location="Remoto"),
        Job(source="computrabajo", title="Desarrollador Java Junior", company="B", url="https://ct/2", location="Remoto"),
    ]
    monkeypatch.setattr(pipeline, "fetch_source", fake_fetch({"computrabajo": FetchResult("computrabajo", jobs=ct_jobs)}))
    fetched_urls = []
    monkeypatch.setitem(pipeline.DESCRIPTION_FETCHERS, "computrabajo",
                        lambda url, client: fetched_urls.append(url) or "Angular 18, RxJS, remoto")
    pipeline.run(settings)
    assert fetched_urls == ["https://ct/1"]
    db = Database(settings.db_path)
    rows = {r["title"]: r["description"] for r in db.conn.execute("SELECT title, description FROM jobs")}
    db.close()
    assert rows["Desarrollador Angular Senior"] == "Angular 18, RxJS, remoto" and not rows["Desarrollador Java Junior"]
