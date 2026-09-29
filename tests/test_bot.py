import copy
from datetime import datetime, timedelta, timezone

import pytest

from job_radar import bot
from tests.helpers import example_settings
from job_radar.db import Database
from job_radar.models import Job
from job_radar.scorer import Usage
from job_radar.util import utcnow

TZ = timezone(timedelta(hours=-7))


@pytest.fixture
def settings(tmp_path):
    s = example_settings()
    s.raw = copy.deepcopy(s.raw)
    s.raw["db_path"] = str(tmp_path / "jobs.db")
    s.raw["credit"] = {"balance_usd": 4.04, "as_of": "2026-09-01", "warn_below_usd": 2}
    return s


@pytest.fixture
def db(settings):
    db = Database(settings.db_path)
    now = utcnow()
    jobs = [
        Job(source="greenhouse", title="Senior Angular Developer", company="Acme", url="https://a/1", location="Remote"),
        Job(source="linkedin", title="Senior React Engineer", company="Beta", url="https://a/2", location="Mexico"),
        Job(source="remotive", title="Frontend Engineer", company="Gamma", url="https://a/3", location="US only"),
    ]
    ids = [job_id for job_id, _ in db.insert_new_jobs(jobs, now)]
    for job_id, (score, fits) in zip(ids, [(90, True), (75, True), (85, False)]):
        db.set_prefilter(job_id, True, "ok")
        db.save_score(job_id, {"score": score, "fits_location": fits, "seniority": "senior",
                               "reason": f"razón {score}", "red_flags": []}, "claude-sonnet-5", now)
    db.mark_notified([ids[0]], now)
    db.record_fetch("linkedin", now, 43, None)
    db.record_fetch("computrabajo", now, 0, "HTTP 403")
    db.record_llm_spend(now, "claude-sonnet-5", Usage(calls=3), 0.05)
    db.set_meta("last_run_at", now.isoformat())
    yield db
    db.close()


@pytest.mark.parametrize(
    "text, expected",
    [("/ultimos", ("ultimos", None)), ("/ultimos@MiJobRadarBot 10", ("ultimos", 10)), ("últimos 3", ("ultimos", 3)),
     ("Top 14", ("top", 14)), ("/start", ("ayuda", None)), ("saldo", ("credito", None)), ("hola", (None, None)),
     ("", (None, None))],
)
def test_parse_command(text, expected):
    assert bot.parse_command(text) == expected


def run(command, number, settings, db):
    return bot.handle(command, number, settings, db, datetime.now(TZ))


def test_ultimos_lists_only_matches_newest_first(settings, db):
    (text,) = run("ultimos", None, settings, db)
    assert "Últimas 2 ofertas" in text
    assert text.index("Senior React Engineer") < text.index("Senior Angular Developer")
    assert "Frontend Engineer" not in text  # 85 but location doesn't fit


def test_recientes_shows_every_scored_job_with_fit_mark(settings, db):
    (text,) = run("recientes", 5, settings, db)
    assert "Frontend Engineer" in text and "85 ✗" in text and "90 ✓" in text


def test_hoy_estado_credito(settings, db):
    (hoy,) = run("hoy", None, settings, db)
    assert "Ofertas nuevas vistas: 3" in hoy and "Enviadas: 1" in hoy and "$0.050" in hoy
    (estado,) = run("estado", None, settings, db)
    assert "✅ linkedin: 43 ofertas" in estado and "❌ computrabajo: HTTP 403" in estado
    assert "Próxima búsqueda:" in estado and "Esperando envío: 1" in estado
    (credito,) = run("credito", None, settings, db)
    assert "Saldo estimado" in credito and "$3.99" in credito


def test_unknown_or_help(settings, db):
    (text,) = run(None, None, settings, db)
    assert "/ultimos" in text and "/buscar" in text


def test_empty_database(settings):
    empty = Database(settings.db_path)
    assert run("ultimos", None, settings, empty) == ["Todavía no hay ofertas que pasen el corte."]
    empty.close()


def test_next_run_follows_schedule():
    assert bot.next_run(datetime(2026, 9, 28, 6, 30, tzinfo=TZ)).hour == 7
    assert bot.next_run(datetime(2026, 9, 28, 7, 0, tzinfo=TZ)).hour == 8
    late = bot.next_run(datetime(2026, 9, 28, 23, 10, tzinfo=TZ))
    assert (late.day, late.hour) == (29, 0)


class FakeTelegram:
    def __init__(self, batches):
        self.batches = list(batches)
        self.sent = []

    def get_updates(self, token, client, offset=None, timeout=0):
        if not self.batches:
            raise KeyboardInterrupt  # stop the serve loop
        return self.batches.pop(0)


def msg(update_id, chat_id, text):
    return {"update_id": update_id, "message": {"chat": {"id": chat_id}, "text": text}}


def test_serve_answers_only_owner_and_remembers_offset(settings, db, monkeypatch):
    fake = FakeTelegram([[msg(10, 999, "/ultimos"), msg(11, 42, "/credito")], [msg(12, 42, "/ayuda")]])
    monkeypatch.setattr(bot.telegram, "get_updates", fake.get_updates)
    monkeypatch.setattr(bot.telegram, "send_message", lambda token, chat, text, client=None: fake.sent.append((chat, text)))
    monkeypatch.setattr(bot.telegram, "set_my_commands", lambda *a, **k: None)
    monkeypatch.setattr(bot, "load_settings", lambda: settings)
    with pytest.raises(KeyboardInterrupt):
        bot.Bot(settings, "token", "42", client=object()).serve(sleep=lambda s: None)
    assert [chat for chat, _ in fake.sent] == ["42", "42"]  # the stranger (999) got nothing
    assert "Crédito Claude" in fake.sent[0][1] and "comandos" in fake.sent[1][1]
    assert db.get_meta("telegram_offset") == "13"


def test_buscar_runs_pipeline_in_background(settings, monkeypatch):
    sent = []
    monkeypatch.setattr(bot.telegram, "send_message", lambda token, chat, text, client=None: sent.append(text))
    monkeypatch.setattr(bot.pipeline, "run", lambda s, out=None: bot.pipeline.RunReport(new_jobs=5, scored=2, notified=1))
    b = bot.Bot(settings, "token", "42", client=object())
    b.start_search(settings)
    b.search_thread.join(timeout=5)
    assert sent[0].startswith("🔎 Buscando") and "Búsqueda terminada" in sent[1] and "enviadas: 1" in sent[1]


def test_run_lock_prevents_overlapping_runs(tmp_path):
    from job_radar.pipeline import run_lock

    path = tmp_path / "jobs.lock"
    with run_lock(path) as first:
        with run_lock(path) as second:
            assert first is True and second is False
    with run_lock(path) as again:
        assert again is True
