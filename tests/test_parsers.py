from job_radar.sources.ats import parse_ashby, parse_greenhouse, parse_lever
from job_radar.sources.boards import parse_getonboard, parse_remoteok, parse_remotive
from job_radar.sources.jobspy_source import parse_jobspy


def test_greenhouse(load_fixture):
    jobs = parse_greenhouse(load_fixture("greenhouse"), "wizeline")
    assert len(jobs) == 3
    job = jobs[1]
    assert job.source == "greenhouse"
    assert job.title == "Android Engineer"
    assert job.company == "WIZELINE"
    assert job.location == "Mexico City- On-site"
    assert job.url.startswith("https://www.wizeline.ai/careers/job?gh_jid=")
    assert job.posted_at.startswith("2021-02-08")  # first_published, not updated_at
    # content is entity-escaped HTML; it must come out as plain text
    assert "<p>" not in job.description and "&lt;" not in job.description
    assert job.description.startswith("We are:")


def test_lever(load_fixture):
    jobs = parse_lever(load_fixture("lever"), "kavak")
    assert [j.title for j in jobs][2] == "Senior AI Engineer - Agentic Systems"
    job = jobs[0]
    assert job.company == "Kavak"
    assert job.location == "Mexico City (hybrid)"
    assert job.url.startswith("https://jobs.lever.co/kavak/")
    assert job.posted_at.startswith("2026-08-28")  # createdAt is epoch milliseconds
    assert "Responsabilidades" in job.description


def test_ashby(load_fixture):
    jobs = parse_ashby(load_fixture("ashby"), "andela")
    assert len(jobs) == 3
    job = jobs[0]
    assert job.title == "Staff Fullstack Engineer"
    assert job.company == "Andela"
    assert job.location == "North America (Remote)"
    assert job.remote is True
    assert job.url.startswith("https://jobs.ashbyhq.com/andela/")
    assert "United Kingdom" in jobs[1].location  # secondary locations are included


def test_remotive(load_fixture):
    jobs = parse_remotive(load_fixture("remotive"))
    job = jobs[1]
    assert job.title == "Frontend Web Application Developer"
    assert job.company == "KoboToolbox"
    assert job.location == "Remote (USA, Canada, Argentina, Mexico, Peru)"
    assert job.salary == "$90k - $105k"
    assert job.remote is True


def test_remoteok_skips_legal_notice_and_fixes_encoding(load_fixture):
    data = load_fixture("remoteok")
    assert "legal" in data[0]
    jobs = parse_remoteok(data)
    assert len(jobs) == len(data) - 1
    assert jobs[2].title == "Mecánico Automotriz Diagnóstico y Presupuestos"
    assert jobs[0].url.startswith("https://remoteOK.com/remote-jobs/")


def test_getonboard(load_fixture):
    jobs = parse_getonboard(load_fixture("getonboard"))
    job = jobs[0]
    assert job.title == "Senior Front-end Developer"
    assert job.company == "TCIT"
    assert job.location == "Hybrid · Chile"
    assert job.salary == "USD 1900-2500/month"
    assert job.url == "https://www.getonbrd.com/jobs/senior-front-end-developer-tcit-santiago-be67"
    assert jobs[1].location == "Remote (local residents)"  # the bogus "Remote" country is dropped


def test_jobspy(load_fixture):
    jobs = parse_jobspy(load_fixture("jobspy"), "linkedin")
    assert len(jobs) == 3
    job = jobs[2]
    assert job.title == "Sr Front End React.js Engineer - Remote in México"
    assert job.company == "Capgemini Engineering"
    assert job.posted_at.startswith("2026-09-25")
    assert job.remote is True
    # NaN values from pandas become None / empty
    assert job.description == "" and job.salary is None


def test_jobspy_skips_rows_without_title_or_url():
    assert parse_jobspy([{"title": None, "job_url": "https://x"}, {"title": "A", "job_url": float("nan")}], "indeed_us") == []


def test_himalayas(load_fixture):
    from job_radar.sources.boards import parse_himalayas

    jobs = parse_himalayas(load_fixture("himalayas"))
    assert len(jobs) == 3
    job = jobs[0]
    assert job.source == "himalayas"
    assert job.title == "Lead Software Engineer (Golang/TypeScript/React) - Independent Contractor"
    assert job.company == "FXC Intelligence"
    assert job.location == "Remote (Mexico)"
    assert job.url.startswith("https://himalayas.app/companies/fxcintel/jobs/")
    assert job.posted_at.startswith("2026-09-28")  # pubDate is epoch seconds
    assert "<p>" not in job.description and job.description
    assert jobs[1].location == "Remote (worldwide)"  # no restrictions = worldwide


def test_jobicy(load_fixture):
    from job_radar.sources.boards import parse_jobicy

    jobs = parse_jobicy(load_fixture("jobicy"))
    assert len(jobs) == 4
    job = jobs[0]
    assert job.source == "jobicy"
    assert job.title == "Senior Android SDK Engineer"
    assert job.company == "RevenueCat"
    assert job.location == "Remote (EMEA, LATAM, Canada, USA)"  # extra spaces collapsed
    assert job.salary == "USD 227,000/yearly"
    assert job.url == "https://jobicy.com/jobs/144154-senior-android-sdk-engineer"
    assert job.description and "<a" not in job.description


def test_jobicy_regions_go_through_location_prefilter(load_fixture):
    from dataclasses import replace
    from datetime import datetime, timezone

    from tests.helpers import example_settings
    from job_radar.prefilter import prefilter
    from job_radar.sources.boards import parse_jobicy

    settings = example_settings()
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    # Same relevant title everywhere, so only the location decides.
    jobs = {j.location: replace(j, title="Senior Frontend Engineer") for j in parse_jobicy(load_fixture("jobicy"))}
    check = lambda loc: prefilter(jobs[loc], settings.prefilter, settings.exclude_companies, 30, now)  # noqa: E731
    assert check("Remote (Brazil)") == (False, "location excludes 'brazil'")
    assert check("Remote (Argentina)") == (False, "location excludes 'argentina'")
    assert check("Remote (Mexico)")[0] is True
    assert check("Remote (EMEA, LATAM, Canada, USA)")[0] is True  # LATAM allowed wins over Canada


def test_workingnomads_keeps_development_only(load_fixture):
    from job_radar.sources.boards import parse_workingnomads

    data = load_fixture("workingnomads")
    jobs = parse_workingnomads(data, ["Development"])
    assert len(jobs) == 3 and len(data) == 4  # the non-Development item is skipped
    job = jobs[0]
    assert job.source == "workingnomads" and job.remote is True
    assert job.title == data[0]["title"].strip() and job.company == data[0]["company_name"].strip()
    assert job.location.startswith("Remote (") and job.url.startswith("https://www.workingnomads.com/job/")
    assert job.external_id and job.external_id.isdigit()
    assert job.posted_at and "<p>" not in job.description
    assert len(parse_workingnomads(data, [])) == 4  # no category filter: everything
