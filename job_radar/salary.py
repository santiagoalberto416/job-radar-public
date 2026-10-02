"""Salary normalization to USD per month, and the minimum-salary rule.

Conservative on purpose: a job is only dropped when its salary is CLEARLY below the minimum. Anything ambiguous
(no period, unknown currency, implausible numbers) counts as "unknown" and is never filtered.

Config (config.yaml):

    salary:
      min_usd_month: 3000        # drop jobs whose top salary is clearly below this (empty = only show salaries)
      fx_per_usd:                # units of each currency per 1 USD; update them now and then
        MXN: 18.0
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

DEFAULT_FX = {"USD": 1.0, "MXN": 18.0, "EUR": 0.92, "GBP": 0.79, "CAD": 1.37, "BRL": 5.5, "COP": 4000.0,
              "CLP": 930.0, "PEN": 3.75}
PERIOD_TO_MONTH = {"year": 1 / 12, "month": 1.0, "week": 4.33, "day": 21.0, "hour": 160.0}
_CURRENCY = re.compile(r"\b(USD|MXN|EUR|GBP|CAD|BRL|COP|CLP|PEN|ARS)\b|US\$", re.I)
_PERIODS = [
    ("year", r"year|yearly|annual|annually|anual|/yr|p\.?a\.?"),
    ("month", r"month|monthly|mensual|/mo\b|al mes|por mes"),
    ("hour", r"hour|hourly|/hr|/h\b|hora"),
    ("day", r"daily|/day|por día|diario"),
    ("week", r"week|weekly|semanal"),
]
_NUMBER = re.compile(r"(\d[\d.,]*)\s*([kK])?")


@dataclass
class Salary:
    low: float
    high: float
    currency: str
    period: str

    def usd_month(self, fx: Mapping[str, float]) -> tuple[float, float] | None:
        rate = fx.get(self.currency)
        if not rate:
            return None
        factor = PERIOD_TO_MONTH[self.period] / rate
        low, high = self.low * factor, self.high * factor
        if high < 200 or high > 100_000:  # implausible: bad data at the source
            return None
        return low, high


def _to_number(raw: str, k: bool) -> float | None:
    raw = raw.strip(".,")
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", raw):      # 1,234,567.89
        value = float(raw.replace(",", ""))
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", raw):    # 1.234.567,89
        value = float(raw.replace(".", "").replace(",", "."))
    elif re.fullmatch(r"\d+([.,]\d+)?", raw):
        value = float(raw.replace(",", "."))
    else:
        return None
    return value * 1000 if k else value


def parse_salary(text: str | None) -> Salary | None:
    if not text or not text.strip():
        return None
    match = _CURRENCY.search(text)
    if match:
        currency = "USD" if match.group(0).upper() == "US$" else match.group(1).upper()
    elif "$" in text:
        currency = "USD"  # e.g. Remotive's "$90k - $105k"
    else:
        return None
    values = [v for v in (_to_number(n, bool(k)) for n, k in _NUMBER.findall(text)) if v]
    if not values:
        return None
    period = next((name for name, pattern in _PERIODS if re.search(pattern, text, re.I)), None)
    if period is None:
        # No period: only a clearly yearly figure in a strong currency is safe to assume ("$90k - $105k").
        if max(values) >= 30_000 and currency in ("USD", "EUR", "GBP", "CAD"):
            period = "year"
        else:
            return None
    return Salary(min(values), max(values), currency, period)


def salary_cfg(raw: Mapping[str, Any]) -> tuple[float | None, dict[str, float]]:
    cfg = raw.get("salary") or {}
    minimum = cfg.get("min_usd_month")
    fx = {**DEFAULT_FX, **{k.upper(): float(v) for k, v in (cfg.get("fx_per_usd") or {}).items()}}
    return (float(minimum) if minimum else None), fx


def usd_month(text: str | None, fx: Mapping[str, float]) -> tuple[float, float] | None:
    parsed = parse_salary(text)
    return parsed.usd_month(fx) if parsed else None


def format_usd_month(low: float | None, high: float | None) -> str:
    if not high:
        return ""

    def k(v: float) -> str:
        return f"{v / 1000:.1f}k".replace(".0k", "k") if v >= 1000 else f"{v:.0f}"

    return f"~US${k(high)}/mes" if not low or abs(high - low) < 50 else f"~US${k(low)}–{k(high)}/mes"
