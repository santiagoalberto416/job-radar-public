from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.helpers import example_settings
from job_radar.prefilter import prefilter
from job_radar.sources.computrabajo import parse_computrabajo, parse_description, parse_relative_date, search_url

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 27, 20, tzinfo=timezone.utc)


def test_parse_search_page():
    jobs = parse_computrabajo((FIXTURES / "computrabajo_search.html").read_text(encoding="utf-8"), NOW)
    assert len(jobs) == 4
    job = jobs[1]
    assert job.source == "computrabajo"
    assert job.title == "Desarrollador Java Angular"
    assert job.company == "IDS Comercial, S.A. de C.V."  # the "4.4" rating is stripped
    assert job.location == "Álvaro Obregón, Ciudad de México DF (Presencial y remoto)"
    assert job.salary == "MXN 25,000.00 (Mensual)"
    assert job.posted_at == "2026-09-25"  # "Hace 2 días"
    assert job.url.startswith("https://mx.computrabajo.com/ofertas-de-trabajo/") and "#" not in job.url
    assert job.external_id == "0845FCE031FF1AE561373E686DCF3405"
    assert job.description == ""  # filled later, only for jobs that pass the prefilter


def test_parse_description():
    text = parse_description((FIXTURES / "computrabajo_detail.html").read_text(encoding="utf-8"))
    assert "Descripción de la oferta" not in text
    assert "Angular" in text and "Spring Boot" in text


@pytest.mark.parametrize(
    "text, expected",
    [("Hace 3 horas", "2026-09-27"), ("Hace 25 minutos", "2026-09-27"), ("Ayer", "2026-09-26"),
     ("Hace 2 días", "2026-09-25"), ("Hace más de 30 días", "2026-08-27"), ("Destacada", None)],
)
def test_relative_dates(text, expected):
    assert parse_relative_date(text, NOW) == expected


def test_search_url():
    assert search_url("angular") == "https://mx.computrabajo.com/trabajo-de-angular"
    assert search_url("desarrollador front end", "Baja California") == (
        "https://mx.computrabajo.com/trabajo-de-desarrollador-front-end-en-baja-california"
    )


def test_hybrid_outside_location_local_is_filtered():
    settings = example_settings()
    jobs = parse_computrabajo((FIXTURES / "computrabajo_search.html").read_text(encoding="utf-8"), NOW)
    results = {j.title: prefilter(j, settings.prefilter, settings.exclude_companies, 30, NOW) for j in jobs}
    assert results["Desarrollador Java Angular"][0] is False  # "Presencial y remoto" in CDMX
    assert results["Desarrollador Full Stack Junior .NET / Angular"][0] is False  # junior
