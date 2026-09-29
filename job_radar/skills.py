"""Skills market report: which skills the jobs around your profile ask for, and which ones would unlock more.

Everything is computed from the jobs already in the DB (no extra fetching). Skill detection is a keyword
dictionary, so it's free; one optional Claude call turns the numbers into a short recommendation in Spanish.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Iterable, Mapping

from .models import Job, normalize_text
from .prefilter import location_ok

log = logging.getLogger(__name__)

# (name, category, regex on lowercased text). Categories: frontend, backend, data, cloud, ai, mobile, other.
SKILLS: list[tuple[str, str, str]] = [
    ("TypeScript", "frontend", r"\btypescript\b"),
    ("Angular", "frontend", r"\bangular(?:js)?\b"),
    ("React", "frontend", r"\breact(?:\.?js)?\b(?!\s*native)"),
    ("Next.js", "frontend", r"\bnext\.?js\b"),
    ("Vue", "frontend", r"\bvue(?:\.?js)?\b|\bnuxt"),
    ("Svelte", "frontend", r"\bsvelte"),
    ("RxJS/NgRx", "frontend", r"\brxjs\b|\bngrx\b"),
    ("Redux", "frontend", r"\bredux\b"),
    ("Tailwind", "frontend", r"\btailwind"),
    ("Design systems/Storybook", "frontend", r"design systems?|sistemas? de dise[ñn]o|\bstorybook\b|component librar"),
    ("Accesibilidad (WCAG)", "frontend", r"\bwcag\b|\ba11y\b|accessibility|accesibilidad"),
    ("Testing (Jest/Cypress/Playwright)", "frontend", r"\bjest\b|\bcypress\b|\bplaywright\b|testing library|\bvitest\b"),
    ("Micro-frontends", "frontend", r"micro[- ]?frontends?|module federation"),
    ("React Native", "mobile", r"\breact native\b"),
    ("Flutter", "mobile", r"\bflutter\b"),
    ("Swift/iOS", "mobile", r"\bswift(?:ui)?\b|\bios\b"),
    ("Node.js", "backend", r"\bnode(?:\.?js)?\b"),
    ("NestJS", "backend", r"\bnest\.?js\b"),
    ("Java/Spring", "backend", r"\bjava\b(?!\s*script)|\bspring(?:[ -]boot| framework)\b"),
    ("Kotlin", "backend", r"\bkotlin\b"),
    (".NET/C#", "backend", r"(?<![a-z])\.net\b|\bc#|\basp\.net\b|\bdotnet\b"),
    ("Python", "backend", r"\bpython\b"),
    ("Django/FastAPI/Flask", "backend", r"\bdjango\b|\bfastapi\b|\bflask\b"),
    ("Go", "backend", r"\bgolang\b|\bgo\b(?=\s*(?:/|,|\)|and\b|or\b|y\b|developer|engineer|services|microservices))"),
    ("Ruby on Rails", "backend", r"\bruby\b|\brails\b"),
    ("PHP/Laravel", "backend", r"\bphp\b|\blaravel\b"),
    ("Rust", "backend", r"\brust\b"),
    ("GraphQL", "backend", r"\bgraphql\b"),
    ("Microservicios", "backend", r"micro-?servic"),
    ("PostgreSQL", "data", r"\bpostgres(?:ql)?\b"),
    ("MySQL", "data", r"\bmysql\b"),
    ("MongoDB", "data", r"\bmongo(?:db)?\b"),
    ("Redis", "data", r"\bredis\b"),
    ("AWS", "cloud", r"\baws\b|amazon web services"),
    ("GCP", "cloud", r"\bgcp\b|google cloud"),
    ("Azure", "cloud", r"\bazure\b"),
    ("Docker", "cloud", r"\bdocker\b"),
    ("Kubernetes", "cloud", r"\bkubernetes\b|\bk8s\b"),
    ("Terraform/IaC", "cloud", r"\bterraform\b|infrastructure as code|\bpulumi\b"),
    ("CI/CD", "cloud", r"\bci ?/ ?cd\b|github actions|gitlab ci|\bjenkins\b|circleci"),
    ("IA/LLMs en producto", "ai",
     r"\bllms?\b|(?:openai|anthropic|claude|gemini) (?:api|sdk)s?\b|langchain|\brag\b|generative ai|\bgen ?ai\b|"
     r"ia generativa|ai agents?|agentic|agentes? de ia"),
    ("Herramientas IA (Copilot/Cursor/Claude Code)", "ai", r"copilot|\bcursor\b|claude code|ai[- ]assisted|asistid[ao]s? por ia"),
    ("Shopify", "other", r"\bshopify\b"),
    ("WordPress", "other", r"\bwordpress\b"),
    ("Figma", "other", r"\bfigma\b"),
]
_COMPILED = [(name, category, re.compile(pattern)) for name, category, pattern in SKILLS]
BACKEND_FOR_FULL_STACK = [n for n, c, _ in SKILLS if c == "backend" and n not in ("GraphQL", "Microservicios")]

_DEV_TITLE = re.compile(
    r"\b(developer|engineer|desarrollador|programador|ingeniero|software|frontend|front end|full stack|fullstack)\b"
)
# The "market" is roles near the profile: frontend, full stack and generic software engineering.
_NOT_DEV = re.compile(
    r"\b(junior|jr|intern|internship|trainee|becario|practicante|manager|director|recruiter|sales|designer|"
    r"qa|tester|analyst|support|data|devops|sre|site reliability|infrastructure|platform|security|solutions|"
    r"machine learning|ml|backend|back end|embedded|firmware|android|ios|mobile|database|databases|network)\b"
)
_FULL_STACK = re.compile(r"\bfull ?stack\b")


def extract_skills(text: str) -> set[str]:
    lowered = (text or "").lower()
    return {name for name, _, pattern in _COMPILED if pattern.search(lowered)}


def is_dev_job(title: str) -> bool:
    text = normalize_text(title)
    return bool(_DEV_TITLE.search(text)) and not _NOT_DEV.search(text)


@dataclass
class SkillsReport:
    days: int
    market_jobs: int
    full_stack_jobs: int
    near_miss_jobs: int
    have: set[str]
    demand: list[tuple[str, int]]  # (skill, jobs mentioning it) in the market sample
    full_stack_backend: list[tuple[str, int]]
    full_stack_no_backend: int
    near_miss_missing: list[tuple[str, int]]  # skills you don't list, counted in near misses
    near_miss_flags: list[str] = field(default_factory=list)
    recommendation: str | None = None

    def pct(self, count: int, total: int) -> str:
        return f"{round(100 * count / total)}%" if total else "0%"


def analyze(
    rows: Iterable[Mapping[str, Any]],
    profile_text: str,
    rules: Mapping[str, Any],
    days: int,
    not_really_have: Iterable[str] = (),
) -> SkillsReport:
    have = extract_skills(profile_text) - set(not_really_have)
    demand: Counter[str] = Counter()
    fs_backend: Counter[str] = Counter()
    near_missing: Counter[str] = Counter()
    flags: list[str] = []
    market = full_stack = full_stack_no_backend = near = 0
    for row in rows:
        title = row["title"] or ""
        job = Job(source=row["source"], title=title, company=row["company"], url=row["url"],
                  location=row["location"] or "")
        if not is_dev_job(title) or not location_ok(job, rules)[0]:
            continue
        skills = extract_skills(f"{title}\n{row['description'] or ''}")
        market += 1
        demand.update(skills)
        if _FULL_STACK.search(normalize_text(title)):
            full_stack += 1
            backends = [s for s in skills if s in BACKEND_FOR_FULL_STACK]
            fs_backend.update(backends)
            full_stack_no_backend += not backends
        score, fits = row["score"], row["fits_location"]
        if score is not None and 40 <= score < 70 and fits:
            near += 1
            near_missing.update(skills - have)
            flags.extend(json.loads(row["red_flags"] or "[]"))
    return SkillsReport(
        days=days,
        market_jobs=market,
        full_stack_jobs=full_stack,
        near_miss_jobs=near,
        have=have,
        demand=demand.most_common(),
        full_stack_backend=fs_backend.most_common(),
        full_stack_no_backend=full_stack_no_backend,
        near_miss_missing=near_missing.most_common(),
        near_miss_flags=flags[:80],
    )


RECOMMENDATION_PROMPT = """Eres un asesor de carrera técnico. Con el perfil del candidato y las estadísticas de ofertas
reales que te paso (datos de su propio buscador de empleo), recomienda qué skills le conviene sumar para tener más
ofertas que encajen con él. Básate SOLO en los datos; cita los números. Responde en español, en texto plano sin
markdown (usa guiones y saltos de línea), máximo 1100 caracteres, con estas tres partes:
1. Full stack: en qué backend especializarse y por qué (según los datos).
2. Top 3 skills a sumar, en orden, con la evidencia.
3. Ganancias rápidas: skills que ya tiene o casi tiene pero que debería destacar en su CV/LinkedIn.
Si hay pocos datos (menos de ~100 ofertas), dilo en una línea."""


def recommend(report: SkillsReport, profile_text: str, llm_cfg: Mapping[str, Any], client: Any = None):
    """One Claude call. Returns (text, usage, model) or raises anthropic errors to the caller."""
    import anthropic

    from .scorer import Usage

    client = client or anthropic.Anthropic(timeout=120.0, max_retries=2)
    model = llm_cfg.get("model", "claude-sonnet-5")
    data = {
        "dias_analizados": report.days,
        "ofertas_de_desarrollo_con_ubicacion_compatible": report.market_jobs,
        "skills_mas_pedidas": [
            {"skill": s, "ofertas": n, "en_su_perfil": s in report.have} for s, n in report.demand[:25]
        ],
        "full_stack": {
            "ofertas": report.full_stack_jobs,
            "backend_pedido": dict(report.full_stack_backend),
            "sin_backend_claro": report.full_stack_no_backend,
        },
        "casi_coincidencias_40_69_puntos": {
            "ofertas": report.near_miss_jobs,
            "skills_que_no_tiene_mas_frecuentes": dict(report.near_miss_missing[:15]),
            "motivos_de_claude": report.near_miss_flags,
        },
    }
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": 4000,
        "system": RECOMMENDATION_PROMPT,
        "messages": [{
            "role": "user",
            "content": f"<perfil>\n{profile_text.strip()}\n</perfil>\n\n<datos>\n"
                       f"{json.dumps(data, ensure_ascii=False, indent=1)}\n</datos>",
        }],
    }
    if llm_cfg.get("effort") and not model.startswith("claude-haiku"):
        params["output_config"] = {"effort": llm_cfg["effort"]}
    response = client.messages.create(**params)
    usage = Usage()
    usage.add(response.usage)
    if response.stop_reason == "refusal":
        return None, usage, model
    text = next((b.text for b in response.content if b.type == "text"), "").strip()
    return text or None, usage, model


def format_report(report: SkillsReport) -> str:
    from .telegram import escape

    def mark(skill: str) -> str:
        return "✓" if skill in report.have else "✗"

    lines = [f"📈 <b>Skills en demanda</b> (últimos {report.days} días)"]
    lines.append(f"Base: {report.market_jobs} ofertas de desarrollo con ubicación compatible")
    if report.market_jobs < 100:
        lines.append("⚠️ Todavía hay pocos datos; el análisis mejora con los días.")
    top = " · ".join(f"{mark(s)} {escape(s)} {report.pct(n, report.market_jobs)}" for s, n in report.demand[:14])
    lines += ["", "<b>Más pedidas</b> (✓ = ya en tu perfil)", top or "(sin datos)"]
    if report.full_stack_jobs:
        backend = " · ".join(
            f"{escape(s)} {report.pct(n, report.full_stack_jobs)}" for s, n in report.full_stack_backend[:8]
        )
        lines += ["", f"<b>Full stack ({report.full_stack_jobs} ofertas): backend que piden</b>", backend or "(ninguno)"]
        if report.full_stack_no_backend:
            lines.append(f"Sin backend específico: {report.pct(report.full_stack_no_backend, report.full_stack_jobs)}")
    if report.near_miss_jobs:
        missing = " · ".join(f"{escape(s)} {n}" for s, n in report.near_miss_missing[:8])
        lines += ["", f"<b>Lo que te falta en tus casi-coincidencias</b> ({report.near_miss_jobs} ofertas de 40–69 pts)",
                  missing or "(nada claro)"]
    if report.recommendation:
        lines += ["", "🤖 <b>Recomendación</b>", escape(report.recommendation)]
    return "\n".join(lines)


def build_report(db, settings, days: int, use_llm: bool = True, client: Any = None) -> SkillsReport:
    from .util import utcnow

    rows = db.jobs_first_seen_since(utcnow() - timedelta(days=days))
    ignore = (settings.raw.get("skills_report") or {}).get("not_really_have") or []
    report = analyze(rows, settings.profile_text, settings.prefilter, days, ignore)
    if use_llm and report.market_jobs:
        try:
            text, usage, model = recommend(report, settings.profile_text, settings.llm, client)
        except Exception as exc:  # the numbers are still useful without the recommendation
            log.error("skills recommendation failed: %s", type(exc).__name__)
            return report
        cost = usage.cost_usd(model)
        db.record_llm_spend(utcnow(), model, usage, cost)
        log.info("skills recommendation: %d in / %d out tokens, ≈$%.4f", usage.input_tokens, usage.output_tokens, cost)
        report.recommendation = text
    return report


def split_message(text: str, limit: int = 4000) -> list[str]:
    """Split on blank lines so each Telegram message stays under the limit."""
    parts, current = [], ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > limit and current:
            parts.append(current)
            candidate = block
        current = candidate[:limit]
    if current:
        parts.append(current)
    return parts

