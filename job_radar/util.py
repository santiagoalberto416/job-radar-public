"""Small shared helpers: HTML to text, HTTP client, date parsing."""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

import httpx

USER_AGENT = "job-radar/0.1 (personal job alerts; non-commercial)"
_BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag):
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(value: str | None) -> str:
    """Convert (possibly entity-escaped) HTML to readable plain text."""
    if not value:
        return ""
    if "&lt;" in value and "<" not in value:  # Greenhouse returns escaped HTML
        value = html.unescape(value)
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    text = "".join(parser.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()


def fix_mojibake(value: str | None) -> str:
    """Repair UTF-8 text that was decoded as Latin-1/CP1252 upstream (e.g. 'MecÃ¡nico' -> 'Mecánico')."""
    if not value or not any(marker in value for marker in ("Ã", "Â", "â€")):
        return value or ""
    for codec in ("cp1252", "latin-1"):
        try:
            return value.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return value


def http_client(timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def to_iso(value) -> str | None:
    """Accepts ISO strings, epoch seconds or epoch milliseconds; returns ISO-8601 UTC or None."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            number = float(value)
            if number > 1e12:  # milliseconds
                number /= 1000
            return datetime.fromtimestamp(number, tz=timezone.utc).isoformat()
        text = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def short_error(exc: BaseException) -> str:
    """One-line error description that never includes request headers or secrets."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return f"{type(exc).__name__}: {str(exc)[:160]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
