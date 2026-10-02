import json
from types import SimpleNamespace

import pytest

from job_radar.batch_scoring import score_in_batch
from job_radar.db import Database
from job_radar.models import Job
from job_radar.pipeline import RunReport
from job_radar.scorer import Scorer
from job_radar.util import utcnow

GOOD = {"score": 88, "fits_location": True, "seniority": "senior", "reason": "ok", "red_flags": [], "salary_usd_month": 0}


def message(payload=GOOD, stop="end_turn"):
    usage = SimpleNamespace(input_tokens=1000, output_tokens=100, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(payload))], stop_reason=stop,
                           stop_details=None, usage=usage)


class FakeBatches:
    """Finishes each batch after `polls_needed` retrieve() calls; results can include errors."""

    def __init__(self, polls_needed=2, outcomes=None):
        self.polls_needed, self.outcomes = polls_needed, outcomes or {}
        self.created, self.polls = [], {}

    def create(self, requests):
        batch_id = f"msgbatch_{len(self.created) + 1}"
        self.created.append((batch_id, requests))
        self.polls[batch_id] = 0
        return SimpleNamespace(id=batch_id, processing_status="in_progress")

    def retrieve(self, batch_id):
        self.polls[batch_id] += 1
        done = self.polls[batch_id] >= self.polls_needed
        return SimpleNamespace(id=batch_id, processing_status="ended" if done else "in_progress")

    def results(self, batch_id):
        requests = dict(self.created)[batch_id]
        for req in reversed(requests):  # results come back in any order
            kind = self.outcomes.get(req["custom_id"], "succeeded")
            result = SimpleNamespace(type=kind, message=message() if kind == "succeeded" else None,
                                     error="overloaded" if kind == "errored" else None)
            yield SimpleNamespace(custom_id=req["custom_id"], result=result)


def setup(tmp_path, batches):
    db = Database(tmp_path / "jobs.db")
    jobs = [Job(source="t", title=f"Senior Angular Developer {i}", company="A", url=f"https://x/{i}") for i in range(3)]
    ids = [job_id for job_id, _ in db.insert_new_jobs(jobs, utcnow())]
    for job_id in ids:
        db.set_prefilter(job_id, True, "ok")
    client = SimpleNamespace(messages=SimpleNamespace(batches=batches))
    scorer = Scorer(llm_cfg={"model": "claude-sonnet-5", "effort": "low"}, profile="P", client=client)
    return db, scorer, ids


def test_batch_finishes_within_the_wait(tmp_path):
    batches = FakeBatches(polls_needed=3)
    db, scorer, ids = setup(tmp_path, batches)
    report = RunReport()
    usage = score_in_batch(db, scorer, db.jobs_to_score(3, 40), report, wait_minutes=10, sleep=lambda s: None)
    assert report.scored == 3 and usage.calls == 3
    assert [r["custom_id"] for r in batches.created[0][1]] == [f"job-{i}" for i in sorted(ids, reverse=True)]
    assert batches.created[0][1][0]["params"]["output_config"]["format"]["type"] == "json_schema"
    assert all(db.job(i)["score"] == 88 and db.job(i)["batch_id"] is None for i in ids)
    spent = db.conn.execute("SELECT cost_usd FROM llm_spend").fetchone()[0]
    assert spent == pytest.approx(usage.cost_usd("claude-sonnet-5") * 0.5)  # batch = 50% off


def test_slow_batch_is_collected_by_the_next_run(tmp_path):
    batches = FakeBatches(polls_needed=100)
    db, scorer, ids = setup(tmp_path, batches)
    clock = {"t": 0.0}

    def sleep(seconds):
        clock["t"] += seconds

    report = RunReport()
    score_in_batch(db, scorer, db.jobs_to_score(3, 40), report, wait_minutes=10, sleep=sleep, monotonic=lambda: clock["t"])
    assert report.scored == 0 and clock["t"] >= 600  # waited 10 minutes, then gave up for this run
    assert db.jobs_to_score(3, 40) == []  # in the batch: nobody submits them twice
    assert db.pending_batch_ids() == ["msgbatch_1"]
    batches.polls_needed = 0  # meanwhile the batch finished
    report2 = RunReport()
    score_in_batch(db, scorer, db.jobs_to_score(3, 40), report2, wait_minutes=10, sleep=sleep)
    assert report2.scored == 3 and len(batches.created) == 1 and db.pending_batch_ids() == []


def test_errored_and_expired_results(tmp_path):
    db, scorer, ids = setup(tmp_path, None)
    a, b, c = ids
    batches = FakeBatches(polls_needed=1, outcomes={f"job-{a}": "errored", f"job-{b}": "expired"})
    scorer.client = SimpleNamespace(messages=SimpleNamespace(batches=batches))
    report = RunReport()
    score_in_batch(db, scorer, db.jobs_to_score(3, 40), report, wait_minutes=1, sleep=lambda s: None)
    assert report.scored == 1 and report.score_failures == 1
    assert db.job(a)["score_attempts"] == 1 and "batch error" in db.job(a)["last_score_error"]
    assert db.job(b)["score"] is None and db.job(b)["batch_id"] is None  # expired: retried next run
    assert {r["id"] for r in db.jobs_to_score(3, 40)} == {a, b}
