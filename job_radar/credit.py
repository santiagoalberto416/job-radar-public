"""Estimated Claude API credit: the balance you last read in the Console minus what job-radar spent since.

Anthropic has no API that returns the prepaid credit balance (the Admin API cost report needs an org admin
key and isn't available to individual accounts), so this is an estimate based on job-radar's own token usage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import Any, Mapping

from .db import Database

BILLING_URL = "https://console.anthropic.com/settings/billing"


@dataclass
class CreditStatus:
    yesterday: float
    last_30_days: float
    balance_start: float | None
    as_of: datetime | None
    spent_since: float | None

    @property
    def remaining(self) -> float | None:
        if self.balance_start is None or self.spent_since is None:
            return None
        return self.balance_start - self.spent_since


def _usd(value: float) -> str:
    return f"${value:.2f}" if abs(value) >= 0.1 or value == 0 else f"${value:.3f}"


def _midnight(day: date, tz) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=tz)


def parse_as_of(value: Any, tz: tzinfo | None) -> datetime | None:
    """Accepts "2026-09-27" (midnight) or "2026-09-27 20:35" (local time)."""
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).strip())
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)


def _format_as_of(value: datetime) -> str:
    return value.strftime("%Y-%m-%d" if (value.hour, value.minute) == (0, 0) else "%Y-%m-%d %H:%M")


def credit_status(db: Database, cfg: Mapping[str, Any], now_local: datetime) -> CreditStatus:
    tz = now_local.tzinfo
    today = _midnight(now_local.date(), tz)
    as_of = parse_as_of(cfg.get("as_of"), tz)
    balance = float(cfg["balance_usd"]) if cfg.get("balance_usd") is not None else None
    return CreditStatus(
        yesterday=db.spend_between(today - timedelta(days=1), today),
        last_30_days=db.spend_between(today - timedelta(days=30)),
        balance_start=balance,
        as_of=as_of,
        spent_since=db.spend_between(as_of) if as_of and balance is not None else None,
    )


def format_credit_message(status: CreditStatus, warn_below: float | None) -> str:
    lines = [f"Ayer: {_usd(status.yesterday)} · Últimos 30 días: {_usd(status.last_30_days)}"]
    low = False
    if status.remaining is not None:
        low = warn_below is not None and status.remaining < warn_below
        lines.append(
            f"Saldo estimado: <b>{_usd(status.remaining)}</b> "
            f"(de {_usd(status.balance_start)} el {_format_as_of(status.as_of)})"
        )
    else:
        lines.append("Saldo: configura credit.balance_usd y credit.as_of en config.yaml")
    title = "⚠️ <b>Crédito Claude bajo</b>" if low else "💳 <b>Crédito Claude</b>"
    footer = f'<a href="{BILLING_URL}">Recargar</a>' if low else "Solo cuenta lo que gasta job-radar."
    return "\n".join([f"{title} (job-radar)", *lines, footer])


CREDIT_EXHAUSTED_MESSAGE = (
    "⛔ <b>job-radar</b>: la API de Claude dice que no queda crédito, así que no se están calificando ofertas "
    f'(se seguirán buscando y se calificarán al recargar). <a href="{BILLING_URL}">Recargar crédito</a>'
)
