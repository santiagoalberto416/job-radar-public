import copy

import pytest

from job_radar import pipeline
from job_radar.db import Database
from job_radar.models import Job
from job_radar.prefilter import prefilter
from job_radar.salary import DEFAULT_FX, format_usd_month, parse_salary, usd_month
from job_radar.scorer import ScoreOutcome, Usage
from job_radar.sources import FetchResult
from tests.helpers import example_settings


@pytest.mark.parametrize(
    "text, expected",
    [
        ("USD 1900-2500/month", (1900, 2500)),
        ("MXN 57,000.00 (Mensual)", (3167, 3167)),
        ("USD 120,000-140,000/annual", (10000, 11667)),
        ("$90k - $105k", (7500, 8750)),                 # no period, clearly yearly
        ("USD 18/hourly", (2880, 2880)),
        ("BRL 249,000-409,000/yearly", (3773, 6197)),
        ("$35,3k- $52k", (2942, 4333)),
    ],
)
def test_usd_month(text, expected):
    low, high = usd_month(text, DEFAULT_FX)
    assert (round(low), round(high)) == expected


@pytest.mark.parametrize("text", ["$10K-$20K", "181,220-217,464/yearly", "USD 1,100-1,500/annual",
                                  "USD 30-36/year", "", None, "Competitivo", "ARS 2.000.000/month"])
def test_ambiguous_salaries_are_unknown(text):
    assert usd_month(text, DEFAULT_FX) is None


def test_format():
    assert format_usd_month(1900, 2500) == "~US$1.9k–2.5k/mes"
    assert format_usd_month(3167, 3167) == "~US$3.2k/mes"
    assert parse_salary("US$4,000 monthly").currency == "USD"


def test_prefilter_drops_only_clearly_low_salaries():
    s = example_settings()
    check = lambda salary: prefilter(  # noqa: E731
        Job(source="t", title="Senior React Engineer", company="C", url="https://x", location="Remote", salary=salary),
        s.prefilter, [], None, None, 3000, DEFAULT_FX)
    assert check("USD 1900-2500/month") == (False, "salary below minimum (~US$1.9k–2.5k/mes)")
    assert check("USD 2500-3500/month")[0] is True     # the top of the range reaches the minimum
    assert check("$10K-$20K")[0] is True               # ambiguous: never filtered
    assert check(None)[0] is True                      # no salary: always passes


def test_llm_salary_holds_back_low_paid_matches(tmp_path, monkeypatch):
    settings = example_settings()
    settings.raw = copy.deepcopy(settings.raw)
    settings.raw.update({"db_path": str(tmp_path / "jobs.db"), "sources": {"linkedin": {}}, "notify_window": None,
                         "credit": {"report_daily": False}, "weekly_summary": {"enabled": False},
                         "salary": {"min_usd_month": 3000}})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    sent = []
    monkeypatch.setattr(pipeline.telegram, "send_message", lambda *a, **kw: sent.append(a[2]))
    salaries = {"Senior Angular Developer": 2200, "Senior React Engineer": 4500, "Senior Frontend Engineer": 0}

    class FakeScorer:
        model = "m"

        def __init__(self, **kw):
            self.usage = Usage()

        def score(self, job):
            return ScoreOutcome(result={"score": 90, "fits_location": True, "seniority": "senior", "reason": "ok",
                                        "red_flags": [], "salary_usd_month": salaries[job["title"]]})

    monkeypatch.setattr(pipeline, "Scorer", FakeScorer)
    jobs = [Job(source="linkedin", title=t, company=t, url=f"https://x/{i}", location="Remote")
            for i, t in enumerate(salaries)]
    monkeypatch.setattr(pipeline, "fetch_source", lambda name, cfg: FetchResult(name, jobs=jobs))
    pipeline.run(settings)
    assert "Senior React Engineer" in sent[0] and "💰 ~US$4.5k/mes" in sent[0]
    assert "Senior Frontend Engineer" in sent[0]          # no salary stated: still sent
    assert "Senior Angular Developer" not in sent[0]      # description said ~US$2.2k/month
    db = Database(settings.db_path)
    assert db.conn.execute("SELECT salary_usd_month FROM jobs WHERE title = 'Senior Angular Developer'").fetchone()[0] == 2200
    db.close()
