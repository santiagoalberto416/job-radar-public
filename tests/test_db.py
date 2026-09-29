from datetime import datetime, timedelta, timezone

from job_radar.db import Database
from job_radar.models import Job

NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)


def job(title="Senior Angular Developer", company="Acme", url="https://jobs.example.com/1", **kw):
    return Job(source=kw.pop("source", "greenhouse"), title=title, company=company, url=url, **kw)


def test_dedupes_by_normalized_url_and_company_title():
    db = Database(":memory:")
    first = db.insert_new_jobs([job()], NOW)
    assert len(first) == 1
    again = db.insert_new_jobs(
        [
            job(url="https://jobs.example.com/1/?utm_source=linkedin"),  # same URL, tracking params
            job(url="https://other.example.com/99", company="ACME Inc."),  # same company + title elsewhere
            job(title="Senior React Engineer", url="https://jobs.example.com/2"),  # genuinely new
        ],
        NOW,
    )
    assert [j.title for _, j in again] == ["Senior React Engineer"]


def test_dedupes_within_one_batch():
    db = Database(":memory:")
    new = db.insert_new_jobs([job(), job(source="linkedin", url="https://linkedin.com/jobs/view/5")], NOW)
    assert len(new) == 1


def test_score_queue_notify_and_mark():
    db = Database(":memory:")
    (a_id, _), (b_id, _), (c_id, _) = db.insert_new_jobs(
        [job(url="https://x/1", title="A"), job(url="https://x/2", title="B"), job(url="https://x/3", title="C")], NOW
    )
    db.set_prefilter(a_id, True, "ok")
    db.set_prefilter(b_id, True, "ok")
    db.set_prefilter(c_id, False, "title not relevant")
    assert {r["id"] for r in db.jobs_to_score(max_attempts=3, limit=10)} == {a_id, b_id}

    result = {"score": 85, "fits_location": True, "seniority": "senior", "reason": "Buen fit", "red_flags": []}
    db.save_score(a_id, result, "claude-opus-5", NOW)
    db.save_score(b_id, {**result, "score": 60}, "claude-opus-5", NOW)
    assert db.jobs_to_score(3, 10) == []  # nothing is scored twice

    to_send = db.jobs_to_notify(min_score=70, require_location_fit=True)
    assert [r["id"] for r in to_send] == [a_id]
    db.mark_notified([a_id], NOW)
    assert db.jobs_to_notify(70, True) == []
    assert [r["title"] for r in db.top(days=7, limit=10, now=NOW)] == ["A", "B"]


def test_failed_scores_retry_until_max_attempts():
    db = Database(":memory:")
    ((job_id, _),) = db.insert_new_jobs([job()], NOW)
    db.set_prefilter(job_id, True, "ok")
    for _ in range(2):
        db.record_score_failure(job_id, "refusal")
        assert len(db.jobs_to_score(max_attempts=3, limit=10)) == 1
    db.record_score_failure(job_id, "refusal")
    assert db.jobs_to_score(max_attempts=3, limit=10) == []


def test_location_fit_required_for_notification():
    db = Database(":memory:")
    ((job_id, _),) = db.insert_new_jobs([job()], NOW)
    db.save_score(job_id, {"score": 90, "fits_location": False, "seniority": "senior", "reason": "", "red_flags": []}, "m", NOW)
    assert db.jobs_to_notify(70, require_location_fit=True) == []
    assert len(db.jobs_to_notify(70, require_location_fit=False)) == 1


def test_source_pacing_uses_last_success():
    db = Database(":memory:")
    assert db.last_success("remotive") is None
    db.record_fetch("remotive", NOW, 10, None)
    db.record_fetch("remotive", NOW + timedelta(hours=1), 0, "HTTP 500")
    assert db.last_success("remotive") == NOW
