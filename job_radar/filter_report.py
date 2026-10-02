"""Which job titles the keyword prefilter may be throwing away by mistake.

Two signals from the titles it rejected (developer roles with a compatible location only):
- "role phrases": the most common two-word phrases in titles rejected as "not relevant" (e.g. "full stack" without a
  seniority word), which may deserve a place in prefilter.title_include;
- "conflicts": exclude words that knocked out titles that also contain one of your include words
  (e.g. ".net" dropping "Full Stack Developer (.NET / Angular)").
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .models import Job, normalize_text
from .prefilter import _matches, location_ok
from .skills import is_dev_job

STOP = set("""
senior sr ssr jr junior mid staff lead principal ii iii iv i remote remoto remota latam mexico colombia argentina chile
peru brazil us usa only english spanish required contract contractor time part fulltime 100 de del la el en y a
the and of for with to at in on or el los las para con por teletrabajo hybrid hibrido anywhere worldwide new grad
""".split())


@dataclass
class FilterReport:
    days: int
    rejected_dev_titles: int
    phrases: list[tuple[str, int, list[str]]] = field(default_factory=list)   # (phrase, count, examples)
    conflicts: list[tuple[str, int, list[str]]] = field(default_factory=list)  # (exclude word, count, examples)


def _bigrams(title: str) -> set[str]:
    words = normalize_text(title).split()
    return {f"{a} {b}" for a, b in zip(words, words[1:]) if not (a in STOP or b in STOP) and not (a.isdigit() or b.isdigit())}


def build(rows: Iterable[Mapping[str, Any]], rules: Mapping[str, Any], days: int, top: int = 6) -> FilterReport:
    phrases: Counter[str] = Counter()
    phrase_examples: dict[str, list[str]] = defaultdict(list)
    conflicts: Counter[str] = Counter()
    conflict_examples: dict[str, list[str]] = defaultdict(list)
    rejected = 0
    for row in rows:
        if row["prefilter_passed"] != 0:
            continue
        title = row["title"] or ""
        reason = row["prefilter_reason"] or ""
        job = Job(source="", title=title, company="", url="", location=row["location"] or "")
        if not is_dev_job(title) or not location_ok(job, rules)[0]:
            continue
        if reason == "title not relevant":
            rejected += 1
            for phrase in _bigrams(title):
                phrases[phrase] += 1
                if len(phrase_examples[phrase]) < 2:
                    phrase_examples[phrase].append(title)
        elif reason.startswith("title excludes") and _matches(normalize_text(title), rules.get("title_include", [])):
            word = reason.split("'")[1] if "'" in reason else reason
            conflicts[word] += 1
            if len(conflict_examples[word]) < 2:
                conflict_examples[word].append(title)
    return FilterReport(
        days=days,
        rejected_dev_titles=rejected,
        phrases=[(p, n, phrase_examples[p]) for p, n in phrases.most_common(top) if n >= 2],
        conflicts=[(w, n, conflict_examples[w]) for w, n in conflicts.most_common(top) if n >= 2],
    )


def format_report(report: FilterReport) -> str:
    from .telegram import escape

    lines = [f"🔎 <b>Lo que el filtro de palabras podría estar dejando fuera</b> (últimos {report.days} días)"]
    if report.phrases:
        lines += ["", f"<b>Frases comunes en títulos descartados</b> ({report.rejected_dev_titles} títulos de desarrollo):"]
        lines += [f"• «{escape(p)}» — {n} (ej. {escape(ex[0])})" for p, n, ex in report.phrases]
        lines.append("Si alguna te interesa, agrégala a <code>prefilter.title_include</code>.")
    if report.conflicts:
        lines += ["", "<b>Exclusiones que chocan con tus palabras</b>:"]
        lines += [f"• «{escape(w)}» descartó {n} títulos que también tenían una palabra tuya (ej. {escape(ex[0])})"
                  for w, n, ex in report.conflicts]
        lines.append("Si no quieres perderlas, quita esa palabra de <code>prefilter.title_exclude</code>.")
    if not report.phrases and not report.conflicts:
        lines.append("Nada que revisar: el filtro no parece estar descartando títulos parecidos a lo que buscas.")
    return "\n".join(lines)


def weekly_line(report: FilterReport) -> str | None:
    """A one-line hint for the weekly summary, or None."""
    from .telegram import escape

    parts = [f"«{escape(p)}» ({n})" for p, n, _ in report.phrases[:2]]
    parts += [f"excluir «{escape(w)}» ({n})" for w, n, _ in report.conflicts[:1]]
    return f"🔎 El filtro podría estar dejando fuera: {', '.join(parts)} — /filtro" if parts else None
