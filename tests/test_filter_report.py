from job_radar.filter_report import build, format_report, weekly_line
from tests.helpers import example_settings

RULES = example_settings().prefilter


def row(title, reason, passed=0, location="Remote"):
    return {"title": title, "prefilter_passed": passed, "prefilter_reason": reason, "location": location}


ROWS = [
    row("Full Stack Engineer", "title not relevant"),
    row("Full Stack Developer (remote)", "title not relevant"),
    row("Desarrollador Full Stack", "title not relevant"),
    row("Software Engineer II", "title not relevant"),
    row("Software Engineer", "title not relevant"),
    row("Account Executive", "title not relevant"),                          # not a dev role
    row("Full Stack Engineer", "title not relevant", location="Bengaluru, India"),  # location excluded
    row("Full Stack Developer (.NET / Angular)", "title excludes '.net'"),
    row("Senior .NET + React Developer", "title excludes '.net'"),
    row("Senior Backend Engineer", "title excludes 'backend'"),             # no include word: not a conflict
    row("Senior Angular Developer", "matched 'angular'", passed=1),
]


def test_finds_role_phrases_and_conflicts():
    report = build(ROWS, RULES, 30)
    phrases = {p: n for p, n, _ in report.phrases}
    assert phrases["full stack"] == 3 and phrases["software engineer"] == 2
    assert report.rejected_dev_titles == 5
    assert [(w, n) for w, n, _ in report.conflicts] == [(".net", 2)]
    text = format_report(report)
    assert "«full stack» — 3" in text and "«.net» descartó 2" in text
    assert "full stack" in weekly_line(report)


def test_nothing_to_review():
    report = build([row("Senior Angular Developer", "matched 'angular'", passed=1)], RULES, 30)
    assert "Nada que revisar" in format_report(report) and weekly_line(report) is None
