from datetime import datetime, timedelta, timezone

from job_radar.scheduler import SCHEDULE_HOURS, next_run, serve

TZ = timezone(timedelta(hours=-6))


def at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


def test_schedule_matches_launchd():
    assert SCHEDULE_HOURS == [0, 2, 4, 6, 7, 8, 10, 12, 14, 16, 18, 20, 22]
    assert next_run(at(1, 6, 30)) == at(1, 7)
    assert next_run(at(1, 7)) == at(1, 8)
    assert next_run(at(1, 23, 10)) == at(2, 0)


class FakeClock:
    def __init__(self, start):
        self.now = start
        self.slept = []

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += timedelta(seconds=seconds)


def test_runs_at_startup_and_on_schedule():
    clock = FakeClock(at(1, 9, 30))
    runs, beats = [], []
    serve(lambda: runs.append(clock.now), heartbeat=lambda: beats.append(1), now=lambda: clock.now,
          sleep=clock.sleep, max_loops=400)
    assert runs[0] == at(1, 9, 30)  # start-up run
    assert [r.strftime("%H:%M") for r in runs[1:4]] == ["10:00", "12:00", "14:00"]
    assert all(s <= 60 for s in clock.slept) and len(beats) == 400


def test_missed_runs_while_asleep_collapse_into_one():
    clock = FakeClock(at(1, 9, 59))
    runs = []

    def sleep(seconds):
        # the laptop sleeps for 7 hours right after the 9:59 check
        clock.now += timedelta(hours=7) if not runs[1:] else timedelta(seconds=seconds)

    serve(lambda: runs.append(clock.now), now=lambda: clock.now, sleep=sleep, max_loops=2)
    assert len(runs) == 2  # start-up + ONE catch-up run at 16:59 (not one per missed slot)
    assert runs[1] == at(1, 16, 59)


def test_a_failing_run_does_not_stop_the_scheduler():
    clock = FakeClock(at(1, 9, 30))
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("network down")

    serve(boom, now=lambda: clock.now, sleep=clock.sleep, max_loops=300)
    assert len(calls) >= 3


def test_job_radar_home_moves_personal_files(tmp_path, monkeypatch):
    from job_radar.config import load_settings

    (tmp_path / "config.yaml").write_text("min_score: 80\nsources: {}\ndb_path: data/x.db\n", encoding="utf-8")
    (tmp_path / "profile.md").write_text("perfil", encoding="utf-8")
    monkeypatch.setenv("JOB_RADAR_HOME", str(tmp_path))
    settings = load_settings()
    assert settings.min_score == 80
    assert settings.db_path == tmp_path / "data" / "x.db"
    assert settings.profile_text == "perfil"
