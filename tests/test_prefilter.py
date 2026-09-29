from datetime import datetime, timezone

import pytest

from tests.helpers import example_settings
from job_radar.models import Job
from job_radar.prefilter import prefilter

SETTINGS = example_settings()
NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


def check(title, location="Remote", company="Acme", posted_at=None, url="https://x/1"):
    job = Job(source="t", title=title, company=company, url=url, location=location, posted_at=posted_at)
    return prefilter(job, SETTINGS.prefilter, SETTINGS.exclude_companies, SETTINGS.max_job_age_days, NOW)


@pytest.mark.parametrize(
    "title",
    [
        "Senior Angular Developer",
        "Sr. Front-End Engineer (React)",
        "Frontend Engineer",
        "Senior Software Engineer",
        "Staff Full Stack Engineer",
        "Desarrollador Frontend Sr",
        "Senior Next.js Developer",
    ],
)
def test_relevant_titles_pass(title):
    passed, reason = check(title)
    assert passed, reason


@pytest.mark.parametrize(
    "title, why",
    [
        ("Junior Frontend Developer", "junior"),
        ("Jr. React Developer", "jr"),
        ("Senior Backend Engineer", "backend"),
        ("QA Automation Engineer", "qa"),
        ("Senior DevOps Engineer", "devops"),
        ("Senior Data Engineer", "data engineer"),
        ("Software Engineer", "not relevant"),  # generic title without seniority
        ("Account Executive", "not relevant"),
        ("Senior Java Developer", "java developer"),
    ],
)
def test_irrelevant_titles_fail(title, why):
    passed, reason = check(title)
    assert not passed
    assert why in reason


def test_excludes_your_current_employer():
    # config.example.yaml excludes "Mi Empresa Actual" (whole words, case/accent-insensitive).
    assert check("Senior Angular Developer", company="Mi Empresa Actual")[1] == "excluded company"
    assert check("Senior Angular Developer", company="MI EMPRESA ACTUAL S.A. de C.V.")[1] == "excluded company"
    assert check("Senior Angular Developer", company="Staffing Co", url="https://careers.miempresaactual.com/job/1")[0] is False
    assert check("Senior Angular Developer", company="Mi Empresa Actualizada")[0] is True


def test_location_rules():
    ok = lambda loc: check("Senior React Engineer", location=loc)[0]  # noqa: E731
    assert not ok("Bengaluru, India")
    assert not ok("New York, NY")
    assert not ok("Remote (Singapore)")
    assert not ok("Canada (Remote)")
    assert ok("Remote - India or LatAm")  # remote allow wins over exclude
    assert ok("Remote (USA, Canada, Argentina, Mexico, Peru)")
    assert ok("Guadalajara, Jalisco")
    assert ok("Desde casa, MX")
    assert ok("Remote")
    assert ok("")  # unknown location: let Claude decide


def test_onsite_and_hybrid_only_in_location_local():
    ok = lambda loc: check("Senior React Engineer", location=loc)[0]  # noqa: E731
    assert not ok("Mexico City (hybrid)")
    assert not ok("Mexico City- On-site")
    assert not ok("Foster City, CA (Hybrid)")
    assert not ok("Hybrid · Chile")
    assert ok("Guadalajara, Jalisco (Hybrid)")
    assert ok("Zapopan, Jalisco (presencial)")


def test_old_postings_are_dropped():
    assert not check("Senior React Engineer", posted_at="2026-06-01T00:00:00+00:00")[0]
    assert check("Senior React Engineer", posted_at="2026-09-20T00:00:00+00:00")[0]
