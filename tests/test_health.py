import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from job_radar import bot, health, pipeline, weekly
from job_radar.db import Database
from job_radar.models import Job
from job_radar.scorer import ScoreOutcome, Usage
from job_radar.sources import FetchResult
from job_radar.util import utcnow
from tests.helpers import example_settings

TZ = timezone(timedelta(hours=-7))


@pytest.fixture
def env(tmp_path, monkeypatch):
    settings = example_settings()
    settings.raw = copy.deepcopy(settings.raw)
    settings.raw["db_path"] = str(tmp_path / "jobs.db")
    settings.raw["sources"] = {"linkedin": {"min_interval_hours": 4}}
    settings.raw["notify_window"] = {"start": "07:00", "end": "22:00"}
    settings.raw["credit"] = {"report_daily": False}
    settings.raw["weekly_summary"] = {"enabled": False}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    sent = []
    monkeypatch.setattr(health.telegram, "send_message", lambda token, chat, text, **kw: sent.append(text))
    clock = {"local": datetime(2026, 10, 1, 12, 0, tzinfo=TZ)}
    monkeypatch.setattr(pipeline, "local_now", lambda tz=None: clock["local"])

    class FakeScorer:
        model = "claude-sonnet-5"

        def __init__(self, **kwargs):
            self.usage = Usage()

        def score(self, job):
            return ScoreOutcome(result={"score": 90, "fits_location": True, "seniority": "senior",
                                        "reason": "ok", "red_flags": []})

    monkeypatch.setattr(pipeline, "Scorer", FakeScorer)
    return settings, sent, clock


def failing(monkeypatch, error="HTTP 403"):
    monkeypatch.setattr(pipeline, "fetch_source", lambda name, cfg: FetchResult(name, error=error))


def test_source_is_disabled_after_six_failures_and_alert_waits_for_the_window(env, monkeypatch):
    settings, sent, clock = env
    failing(monkeypatch)
    clock["local"] = datetime(2026, 10, 1, 23, 30, tzinfo=TZ)  # quiet hours
    for _ in range(6):
        pipeline.run(settings)
    db = Database(settings.db_path)
    state = db.source_state("linkedin")
    assert state["disabled_at"] and state["fail_streak"] == 6
    assert len(json.loads(db.get_meta("pending_alerts"))) == 1
    db.close()
    assert not any("desactivé" in m for m in sent)  # held during quiet hours

    clock["local"] = datetime(2026, 10, 2, 8, 0, tzinfo=TZ)
    report = pipeline.run(settings)
    assert report.disabled == ["linkedin"] and report.fetched == []  # skipped while waiting for the retry
    assert sum("desactivé <b>linkedin</b>" in m for m in sent) == 1


def test_disabled_source_is_retried_after_24h_and_comes_back(env, monkeypatch):
    settings, sent, clock = env
    db = Database(settings.db_path)
    old = utcnow() - timedelta(hours=25)
    db.record_fetch("linkedin", old, 0, "HTTP 403")
    db.disable_source("linkedin", old, "HTTP 403")
    db.close()
    job = Job(source="linkedin", title="Senior Angular Developer", company="Acme", url="https://x/1", location="Remote")
    monkeypatch.setattr(pipeline, "fetch_source", lambda name, cfg: FetchResult(name, jobs=[job]))
    report = pipeline.run(settings)
    assert [r.source for r in report.fetched] == ["linkedin"]
    db = Database(settings.db_path)
    assert db.source_state("linkedin")["disabled_at"] is None
    db.close()
    assert any("volvió a funcionar" in m for m in sent)


def test_healthy_runs_are_recorded_and_pinged(env, monkeypatch):
    settings, sent, clock = env
    pings = []
    monkeypatch.setattr(health.httpx, "get", lambda url, **kw: pings.append(url))
    settings.raw["health"] = {"ping_url": "https://hc-ping.example/abc"}
    monkeypatch.setattr(pipeline, "fetch_source", lambda name, cfg: FetchResult(name))
    pipeline.run(settings)
    failing(monkeypatch)
    pipeline.run(settings, force=True)
    assert pings == ["https://hc-ping.example/abc"]  # only the healthy run pings
    db = Database(settings.db_path)
    assert db.get_meta("last_healthy_run_at") is not None
    db.close()


def test_weekly_backup_keeps_the_newest_copies(tmp_path):
    db = Database(tmp_path / "jobs.db")
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    made = [health.maybe_backup(db, tmp_path / "jobs.db", start + timedelta(days=7 * i), {"every_days": 7, "keep": 4})
            for i in range(6)]
    assert all(made)
    assert health.maybe_backup(db, tmp_path / "jobs.db", start + timedelta(days=36), {"every_days": 7}) is None
    names = sorted(p.name for p in (tmp_path / "backups").glob("*.db"))
    assert names == ["jobs-2026-09-15.db", "jobs-2026-09-22.db", "jobs-2026-09-29.db", "jobs-2026-10-06.db"]
    db.close()


def test_stale_message():
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    cfg = health.DEFAULTS
    awake = now - timedelta(hours=2)
    assert health.stale_message((now - timedelta(hours=2)).isoformat(), now, awake, cfg) is None
    assert "hace 9 h" in health.stale_message((now - timedelta(hours=9)).isoformat(), now, awake, cfg)
    assert health.stale_message((now - timedelta(hours=9)).isoformat(), now, now - timedelta(minutes=5), cfg) is None
    assert "nunca" in health.stale_message(None, now, awake, cfg)


def test_weekly_summary_is_sent_once_on_monday(env, monkeypatch):
    settings, sent, clock = env
    settings.raw["weekly_summary"] = {"enabled": True, "weekday": 0, "hour": 7}
    job = Job(source="linkedin", title="Senior Angular Developer", company="Acme", url="https://x/1", location="Remote")
    monkeypatch.setattr(pipeline, "fetch_source", lambda name, cfg: FetchResult(name, jobs=[job]))
    clock["local"] = datetime(2026, 10, 5, 6, 0, tzinfo=TZ)   # Monday before 7:00 (quiet)
    pipeline.run(settings)
    assert not any("Resumen semanal" in m for m in sent)
    clock["local"] = datetime(2026, 10, 5, 7, 0, tzinfo=TZ)
    pipeline.run(settings, force=True)
    clock["local"] = datetime(2026, 10, 5, 8, 0, tzinfo=TZ)
    pipeline.run(settings, force=True)
    summaries = [m for m in sent if "Resumen semanal" in m]
    assert len(summaries) == 1
    assert "Lo mejor de la semana" in summaries[0] and "Senior Angular Developer" in summaries[0]


def test_is_due():
    cfg = {"enabled": True, "weekday": 0, "hour": 7}
    monday = datetime(2026, 10, 5, 7, 30, tzinfo=TZ)
    assert weekly.is_due(monday, cfg, None)
    assert not weekly.is_due(monday, cfg, weekly.week_key(monday))
    assert not weekly.is_due(monday.replace(hour=6), cfg, None)
    assert not weekly.is_due(monday + timedelta(days=1), cfg, None)
    assert not weekly.is_due(monday, {**cfg, "enabled": False}, None)


def test_bot_reactivar_estado_and_semana(env):
    settings, sent, clock = env
    db = Database(settings.db_path)
    db.record_fetch("linkedin", utcnow(), 0, "HTTP 403")
    db.disable_source("linkedin", utcnow(), "HTTP 403")
    (estado,) = bot.handle("estado", None, settings, db, clock["local"])
    assert "⏸️ linkedin: desactivada" in estado
    (listing,) = bot.handle("reactivar", None, settings, db, clock["local"])
    assert "linkedin" in listing
    (done,) = bot.handle("reactivar", None, settings, db, clock["local"], arg="linkedin")
    assert "Reactivé" in done and db.source_state("linkedin")["disabled_at"] is None
    (semana,) = bot.handle("semana", None, settings, db, clock["local"])
    assert "Resumen semanal" in semana
    db.close()
    assert bot.command_arg("/reactivar LinkedIn") == "linkedin" and bot.command_arg("/reactivar") is None


def test_bot_watchdog_warns_once_and_not_right_after_wake(env, monkeypatch):
    settings, sent, clock = env
    monkeypatch.setattr(bot, "load_settings", lambda: settings)
    replies = []
    b = bot.Bot(settings, "t", "42", client=object())
    monkeypatch.setattr(b, "reply", replies.append)
    now = utcnow()
    b.awake_since = now - timedelta(hours=3)
    b.last_loop = now - timedelta(seconds=40)
    b.last_watch = now - timedelta(minutes=11)
    b.watchdog(now)                    # never had a healthy run, awake for 3 h -> warn
    assert len(replies) == 1 and "no ha terminado una búsqueda" in replies[0]
    b.last_watch = now - timedelta(minutes=11)
    b.watchdog(now + timedelta(minutes=1))  # already warned in the last 12 h
    assert len(replies) == 1
    b2 = bot.Bot(settings, "t", "42", client=object())
    monkeypatch.setattr(b2, "reply", replies.append)
    b2.last_loop = now - timedelta(hours=8)   # the Mac just woke up
    b2.last_watch = now - timedelta(minutes=11)
    Database(settings.db_path).set_meta("watchdog_alert_at", (now - timedelta(days=1)).isoformat())
    b2.watchdog(now)
    assert len(replies) == 1
