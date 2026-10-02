import copy
from datetime import datetime, timedelta, timezone

import pytest

from job_radar import bot, pipeline, telegram
from job_radar.db import Database
from job_radar.models import Job
from job_radar.scorer import ScoreOutcome, Scorer, Usage, format_feedback
from job_radar.sources import FetchResult
from job_radar.util import utcnow
from tests.helpers import example_settings

NOW = datetime(2026, 10, 1, 18, tzinfo=timezone.utc)


def seed(db, n=3):
    jobs = [Job(source="linkedin", title=f"Senior Angular Developer {i}", company=f"Acme {i}", url=f"https://x/{i}",
                location="Remote") for i in range(n)]
    return [job_id for job_id, _ in db.insert_new_jobs(jobs, NOW)]


def test_feedback_and_status_in_db(tmp_path):
    db = Database(tmp_path / "jobs.db")
    a, b, c = seed(db)
    db.set_feedback(a, "like", NOW)
    db.set_feedback(b, "dislike", NOW)
    db.set_status(c, "applied", NOW, notes="Recruiter: Ana")
    db.set_status(a, "rejected", NOW + timedelta(hours=1))
    assert db.job(c)["notes"] == "Recruiter: Ana"
    assert [r["id"] for r in db.applications()] == [c]  # rejected is closed
    assert {r["id"] for r in db.applications(include_closed=True)} == {a, c}
    examples = db.feedback_examples(5)
    assert {r["id"] for r in examples["liked"]} == {a, c} and [r["id"] for r in examples["disliked"]] == [b]
    with pytest.raises(ValueError):
        db.set_status(a, "hired", NOW)


def test_scoring_prompt_includes_feedback(tmp_path):
    db = Database(tmp_path / "jobs.db")
    a, b, _ = seed(db)
    db.set_feedback(a, "like", NOW)
    db.set_feedback(b, "dislike", NOW)
    block = format_feedback(db.feedback_examples(8))
    assert "Liked or applied to:\n- Senior Angular Developer 0 — Acme 0 (Remote)" in block
    assert "Not interested:\n- Senior Angular Developer 1" in block
    s = Scorer(llm_cfg={"model": "claude-sonnet-5"}, profile="P", client=object(), feedback=block)
    assert s.system.endswith("</feedback>") and "<profile>" in s.system
    assert format_feedback({"liked": [], "disliked": []}) == ""


def test_callbacks_toggle_feedback_and_advance_status(tmp_path):
    db = Database(tmp_path / "jobs.db")
    a, _, _ = seed(db)
    assert bot.apply_callback(db, f"fb:{a}:like", NOW).startswith("👍")
    assert db.job(a)["feedback"] == "like"
    assert bot.apply_callback(db, f"fb:{a}:like", NOW) == "Quitado" and db.job(a)["feedback"] is None
    assert bot.apply_callback(db, f"fb:{a}:applied", NOW).startswith("📨") and db.job(a)["status"] == "applied"
    assert bot.apply_callback(db, f"st:{a}:interview", NOW).startswith("🗣") and db.job(a)["status"] == "interview"
    assert bot.apply_callback(db, f"fb:{a}:applied", NOW) and db.job(a)["status"] == "interview"  # not downgraded
    assert bot.apply_callback(db, "fb:999:like", NOW) is None
    assert bot.apply_callback(db, "garbage", NOW) is None


def test_keyboard_redraw_marks_choices(tmp_path):
    db = Database(tmp_path / "jobs.db")
    a, b, _ = seed(db)
    markup = telegram.feedback_keyboard([(1, a, None, None), (2, b, None, None)])
    db.set_feedback(b, "dislike", NOW)
    redrawn = bot.redraw_keyboard(db, markup)
    assert [x["text"] for x in redrawn["inline_keyboard"][1]] == ["2 👍", "✅2 👎", "2 📨"]
    status = bot.redraw_keyboard(db, telegram.status_keyboard([(1, a, "applied")]))
    assert [x["callback_data"] for x in status["inline_keyboard"][0]] == [f"st:{a}:interview", f"st:{a}:offer", f"st:{a}:rejected"]


@pytest.fixture
def settings(tmp_path, monkeypatch):
    s = example_settings()
    s.raw = copy.deepcopy(s.raw)
    s.raw["db_path"] = str(tmp_path / "jobs.db")
    s.raw["sources"] = {"linkedin": {}}
    s.raw["notify_window"] = None
    s.raw["credit"] = {"report_daily": False}
    s.raw["weekly_summary"] = {"enabled": False}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    return s


def test_digest_is_numbered_with_feedback_buttons(settings, monkeypatch):
    sent = []
    monkeypatch.setattr(pipeline.telegram, "send_message",
                        lambda token, chat, text, client=None, reply_markup=None: sent.append((text, reply_markup)))

    class FakeScorer:
        model = "m"

        def __init__(self, **kw):
            self.usage = Usage()

        def score(self, job):
            return ScoreOutcome(result={"score": 90, "fits_location": True, "seniority": "senior", "reason": "ok",
                                        "red_flags": []})

    monkeypatch.setattr(pipeline, "Scorer", FakeScorer)
    jobs = [Job(source="linkedin", title=f"Senior Angular Developer {i}", company=f"C{i}", url=f"https://x/{i}",
                location="Remote") for i in range(2)]
    monkeypatch.setattr(pipeline, "fetch_source", lambda name, cfg: FetchResult(name, jobs=jobs))
    pipeline.run(settings)
    text, markup = sent[0]
    assert "1. <b>90</b>" in text and "2. <b>90</b>" in text
    assert [b["text"] for b in markup["inline_keyboard"][0]] == ["1 👍", "1 👎", "1 📨"]


def test_bot_handles_taps_only_from_owner_and_lists_applications(settings, monkeypatch):
    db = Database(settings.db_path)
    a, _, _ = seed(db)
    db.close()
    monkeypatch.setattr(bot, "load_settings", lambda: settings)
    answers, edits, messages = [], [], []
    monkeypatch.setattr(bot.telegram, "answer_callback", lambda token, cid, text, client=None: answers.append(text))
    monkeypatch.setattr(bot.telegram, "edit_markup", lambda token, chat, mid, markup, client=None: edits.append(markup))
    monkeypatch.setattr(bot.telegram, "send_message",
                        lambda token, chat, text, client=None, reply_markup=None: messages.append((text, reply_markup)))
    b = bot.Bot(settings, "t", "42", client=object())
    markup = telegram.feedback_keyboard([(1, a, None, None)])
    tap = lambda chat: {"callback_query": {"id": "c1", "data": f"fb:{a}:applied",  # noqa: E731
                                           "message": {"chat": {"id": chat}, "message_id": 7, "reply_markup": markup}}}
    b.on_update(tap(999))
    assert answers == [] and edits == []  # a stranger's tap is ignored
    b.on_update(tap(42))
    assert answers == ["📨 Marcada como aplicada"]
    assert edits[0]["inline_keyboard"][0][2]["text"] == "✅1 📨"
    b.on_update({"message": {"chat": {"id": 42}, "text": "/postulaciones"}})
    text, keyboard = messages[-1]
    assert "Postulaciones activas" in text and "Senior Angular Developer 0" in text
    assert keyboard["inline_keyboard"][0][0]["callback_data"] == f"st:{a}:interview"
