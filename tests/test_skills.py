import json
from types import SimpleNamespace

import pytest

from tests.helpers import example_settings
from job_radar.skills import analyze, extract_skills, format_report, is_dev_job, recommend, split_message

RULES = example_settings().prefilter


@pytest.mark.parametrize(
    "text, present, absent",
    [
        ("Strong JavaScript and TypeScript, React.js", {"TypeScript", "React"}, {"Java/Spring"}),
        ("Java 17 + Spring Boot microservices", {"Java/Spring", "Microservicios"}, set()),
        ("We build with React Native and Flutter", {"React Native", "Flutter"}, {"React"}),
        ("Experience with Go, Python or Ruby", {"Go", "Python", "Ruby on Rails"}, set()),
        ("Ready to go to market fast; the rest of the team", set(), {"Go"}),
        ("Node.js / NestJS backend on AWS with PostgreSQL", {"Node.js", "NestJS", "AWS", "PostgreSQL"}, set()),
        ("C# and ASP.NET Core; .NET 8", {".NET/C#"}, set()),
        ("Customers include Anthropic and NVIDIA", set(), {"IA/LLMs en producto"}),
        ("Integrate LLMs via the OpenAI API and build RAG", {"IA/LLMs en producto"}, set()),
        ("Next.js app router, Tailwind, Storybook design system", {"Next.js", "Tailwind", "Design systems/Storybook"}, set()),
    ],
)
def test_extract_skills(text, present, absent):
    found = extract_skills(text)
    assert present <= found and not (absent & found)


@pytest.mark.parametrize(
    "title, expected",
    [("Senior Full Stack Engineer", True), ("Frontend Developer", True), ("Desarrollador Angular", True),
     ("Senior Backend Engineer", False), ("Site Reliability Engineer", False), ("Junior Frontend Developer", False),
     ("Engineering Manager", False), ("Account Executive", False)],
)
def test_is_dev_job(title, expected):
    assert is_dev_job(title) is expected


def row(title, description, location="Remote", score=None, fits=None, flags=()):
    return {"source": "t", "title": title, "company": "C", "url": f"https://x/{title}", "location": location,
            "description": description, "score": score, "fits_location": fits, "red_flags": json.dumps(list(flags))}


ROWS = [
    row("Senior Full Stack Engineer", "React + Node.js + AWS", score=55, fits=1, flags=["requiere Node.js"]),
    row("Full Stack Developer", "Angular and Java Spring Boot", score=45, fits=1),
    row("Senior Frontend Engineer", "React, TypeScript, Storybook", score=90, fits=1),
    row("Full Stack Engineer", "React + Python/Django", location="Bengaluru, India"),  # location excluded
    row("Senior Backend Engineer", "Go and Kubernetes"),  # not in the market sample
]


def test_analyze_counts_market_full_stack_and_near_misses():
    report = analyze(ROWS, "Angular, React, TypeScript, Android (Kotlin/Java)", RULES, 30, not_really_have=["Java/Spring"])
    assert report.market_jobs == 3 and report.full_stack_jobs == 2 and report.near_miss_jobs == 2
    assert dict(report.full_stack_backend) == {"Node.js": 1, "Java/Spring": 1}
    missing = dict(report.near_miss_missing)
    assert missing["Node.js"] == 1 and missing["AWS"] == 1 and missing["Java/Spring"] == 1  # Java not counted as had
    assert "React" not in missing  # already in the profile
    assert report.near_miss_flags == ["requiere Node.js"]
    text = format_report(report)
    assert "Full stack (2 ofertas)" in text and "Node.js 50%" in text and "pocos datos" in text


def test_recommend_uses_claude_once_and_returns_text():
    calls = []

    class Client:
        class messages:  # noqa: N801
            @staticmethod
            def create(**kwargs):
                calls.append(kwargs)
                usage = SimpleNamespace(input_tokens=2000, output_tokens=300, cache_read_input_tokens=0,
                                        cache_creation_input_tokens=0)
                return SimpleNamespace(stop_reason="end_turn", usage=usage,
                                       content=[SimpleNamespace(type="text", text="1. Full stack: Node.js…")])

    report = analyze(ROWS, "Angular, React", RULES, 30)
    text, usage, model = recommend(report, "Angular, React", {"model": "claude-sonnet-5", "effort": "low"}, Client)
    assert text.startswith("1. Full stack") and usage.calls == 1 and model == "claude-sonnet-5"
    payload = calls[0]["messages"][0]["content"]
    assert '"backend_pedido"' in payload and "<perfil>" in payload
    assert calls[0]["output_config"] == {"effort": "low"}


def test_split_message():
    parts = split_message("\n\n".join(["x" * 1500] * 5), limit=4000)
    assert len(parts) == 3 and all(len(p) <= 4000 for p in parts)
