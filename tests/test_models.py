from job_radar.models import Job, normalize_url


def test_normalize_url_drops_tracking_and_unifies_hosts():
    assert normalize_url("https://mx.indeed.com/viewjob?jk=ABC&from=serp&utm_source=x") == "indeed.com/viewjob?jk=ABC"
    assert normalize_url("https://www.indeed.com/viewjob?jk=ABC") == "indeed.com/viewjob?jk=ABC"
    assert normalize_url("https://jobs.lever.co/kavak/123/?lever-source=LinkedIn#apply") == "jobs.lever.co/kavak/123"


def test_normalize_linkedin_urls():
    assert normalize_url("https://mx.linkedin.com/jobs/view/senior-angular-dev-at-acme-4472030348?refId=x&trk=y") == (
        "linkedin.com/jobs/view/4472030348"
    )
    assert normalize_url("https://www.linkedin.com/jobs/view/4472030348/") == "linkedin.com/jobs/view/4472030348"


def test_company_title_key_ignores_case_accents_and_suffixes():
    a = Job(source="a", title="Desarrollador Front-End Sénior", company="Acme, Inc.", url="https://a")
    b = Job(source="b", title="desarrollador front end senior", company="ACME", url="https://b")
    assert a.company_title_key == b.company_title_key
