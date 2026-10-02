"""Telegram Bot API: HTML digest formatting, message splitting, sending, and chat_id discovery."""

from __future__ import annotations

import html
from typing import Any, Mapping, Sequence

import httpx

from .util import USER_AGENT

API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4096
SAFE_LEN = 4000  # headroom under Telegram's 4096-character limit


class TelegramError(Exception):
    pass


def escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def format_job(job: Mapping[str, Any], number: int | None = None) -> str:
    url = html.escape(job["url"], quote=True)
    prefix = f"{number}. " if number is not None else ""
    lines = [
        f"{prefix}<b>{job['score']}</b> · <a href=\"{url}\">{escape(job['title'])}</a>",
        f"🏢 {escape(job['company'])}  📍 {escape(job.get('location') or 'n/d')}  🔎 {escape(job['source'])}",
    ]
    if job.get("salary_usd_month"):
        from .salary import format_usd_month

        lines.append(f"💰 {format_usd_month(job.get('salary_usd_low'), job['salary_usd_month'])}")
    if job.get("reason"):
        lines.append(f"💬 {escape(job['reason'])}")
    return "\n".join(lines)


def build_digest(
    jobs: Sequence[Mapping[str, Any]], limit: int = SAFE_LEN, header: str | None = None, numbered: bool = False
) -> list[tuple[str, list[int]]]:
    """Split the digest into messages under `limit` chars. Returns (html_text, job_ids) per message."""
    if not jobs:
        return []
    if header is None:
        header = f"🛰️ <b>job-radar</b>: {len(jobs)} {'oferta nueva' if len(jobs) == 1 else 'ofertas nuevas'}"
    messages: list[tuple[str, list[int]]] = []
    current, ids = header, []
    for number, job in enumerate(jobs, 1):
        block = format_job(job, number if numbered else None)
        if len(block) + 2 > limit - len(header):  # a single huge entry: drop the reason line
            block = block.split("\n💬", 1)[0][: limit - len(header) - 2]
        if len(current) + 2 + len(block) > limit:
            messages.append((current, ids))
            current, ids = "", []
        current = f"{current}\n\n{block}" if current else block
        ids.append(job["id"])
    messages.append((current, ids))
    return messages


def send_message(
    token: str, chat_id: str, text: str, client: httpx.Client | None = None, reply_markup: dict | None = None
) -> None:
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    _call(token, "sendMessage", payload, client)


def answer_callback(token: str, callback_id: str, text: str, client: httpx.Client | None = None) -> None:
    _call(token, "answerCallbackQuery", {"callback_query_id": callback_id, "text": text}, client)


def edit_markup(token: str, chat_id: str, message_id: int, markup: dict, client: httpx.Client | None = None) -> None:
    _call(token, "editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id, "reply_markup": markup}, client)


# --- feedback buttons ---------------------------------------------------------------------------------------
# callback_data: "fb:<job_id>:like|dislike|applied" on digests, "st:<job_id>:interview|offer|rejected" on /postulaciones.
FEEDBACK_BUTTONS = [("like", "👍"), ("dislike", "👎"), ("applied", "📨")]
STATUS_BUTTONS = [("interview", "🗣"), ("offer", "🎉"), ("rejected", "✖")]


def feedback_keyboard(items: Sequence[tuple[int, int, str | None, str | None]]) -> dict:
    """items: (number, job_id, feedback, status). One row per job: [1 👍] [1 👎] [1 📨]; ✅ marks what's set."""
    rows = []
    for number, job_id, feedback, status in items:
        row = []
        for action, emoji in FEEDBACK_BUTTONS:
            active = feedback == action or (action == "applied" and status is not None)
            row.append({"text": f"{'✅' if active else ''}{number} {emoji}", "callback_data": f"fb:{job_id}:{action}"})
        rows.append(row)
    return {"inline_keyboard": rows}


def status_keyboard(items: Sequence[tuple[int, int, str | None]]) -> dict:
    """items: (number, job_id, status). One row per application: [1 🗣] [1 🎉] [1 ✖]."""
    rows = []
    for number, job_id, status in items:
        rows.append([{"text": f"{'✅' if status == action else ''}{number} {emoji}", "callback_data": f"st:{job_id}:{action}"}
                     for action, emoji in STATUS_BUTTONS])
    return {"inline_keyboard": rows}


def keyboard_items(markup: Mapping[str, Any] | None) -> list[tuple[str, int, int]]:
    """(kind, number, job_id) for each row of a keyboard we sent, so it can be redrawn after a tap."""
    items = []
    for row in (markup or {}).get("inline_keyboard", []):
        data = row[0].get("callback_data", "") if row else ""
        parts = data.split(":")
        if len(parts) == 3 and parts[1].isdigit():
            number = "".join(ch for ch in row[0].get("text", "").lstrip("✅").split(" ")[0] if ch.isdigit())
            items.append((parts[0], int(number or 0), int(parts[1])))
    return items


def get_me(token: str, client: httpx.Client | None = None) -> dict[str, Any]:
    return _call(token, "getMe", {}, client) or {}


def get_updates(
    token: str, client: httpx.Client | None = None, offset: int | None = None, timeout: int = 0
) -> list[dict[str, Any]]:
    """Long-polls for up to `timeout` seconds. Pass a client whose timeout is longer than that."""
    payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
    if offset is not None:
        payload["offset"] = offset
    return _call(token, "getUpdates", payload, client)


def set_my_commands(token: str, commands: Sequence[tuple[str, str]], client: httpx.Client | None = None) -> None:
    _call(token, "setMyCommands", {"commands": [{"command": c, "description": d} for c, d in commands]}, client)


def get_webhook_url(token: str, client: httpx.Client | None = None) -> str:
    return (_call(token, "getWebhookInfo", {}, client) or {}).get("url", "")


def chats_from_updates(updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chats: dict[int, dict[str, Any]] = {}
    for update in updates:
        message = update.get("message") or update.get("edited_message") or update.get("channel_post") or {}
        chat = message.get("chat")
        if chat:
            chats[chat["id"]] = chat
    return list(chats.values())


def _call(token: str, method: str, payload: dict[str, Any], client: httpx.Client | None) -> Any:
    own_client = client is None
    client = client or httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT})
    try:
        response = client.post(API.format(token=token, method=method), json=payload)
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Never include the exception text: httpx errors can contain the URL, which holds the bot token.
        raise TelegramError(f"{method} failed: {type(exc).__name__}") from None
    finally:
        if own_client:
            client.close()
    if not data.get("ok"):
        raise TelegramError(f"{method} failed: {data.get('error_code')} {data.get('description')}")
    return data.get("result")
