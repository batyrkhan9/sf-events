"""Format the digest and deliver it.

Formatting and sending are separate so the formatter can be tested with no
token and no network — which is most of what can go wrong here.

With no TELEGRAM_BOT_TOKEN set, the digest prints to the console. That's the
documented fallback, not a degraded mode: it's how you check the output
before trusting it to arrive on your phone every morning.
"""

from datetime import date, datetime, timedelta
from html import escape
import os

import requests

from src.extract import Event

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# Telegram rejects anything longer. We split well below it so a long event
# never lands us one character over.
MAX_MESSAGE_CHARS = 3800

SEND_TIMEOUT_SECONDS = 15


def day_header(iso_date: str, today: date | None = None) -> str:
    """'2026-08-10' -> 'Tonight' / 'Tomorrow' / 'Wednesday 12 Aug'.

    Reading a digest at 8am, "2026-08-12" makes you do arithmetic. Relative
    names for the next two days, weekday names after that, since anything
    further out is a plan rather than tonight.
    """
    today = today or date.today()
    try:
        when = datetime.strptime(iso_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return iso_date or "Date unknown"

    if when == today:
        return "Tonight"
    if when == today + timedelta(days=1):
        return "Tomorrow"
    if when <= today + timedelta(days=6):
        return when.strftime("%A")
    return when.strftime("%A %-d %b")


def format_event(event: Event) -> str:
    """One event as a Telegram HTML block."""
    title = escape(event.title)
    # Link the title when we have a URL; a bare title is still readable.
    heading = f'<a href="{escape(event.url)}">{title}</a>' if event.url else f"<b>{title}</b>"

    facts = []
    if event.time:
        facts.append(event.time)
    if event.venue:
        facts.append(escape(event.venue))
    if event.price:
        facts.append(escape(event.price))

    lines = [f"• {heading}"]
    if facts:
        lines.append(f"  {' · '.join(facts)}")
    if event.one_liner:
        lines.append(f"  <i>{escape(event.one_liner)}</i>")
    return "\n".join(lines)


def format_digest(events: list[Event], today: date | None = None) -> str:
    """The whole digest as one HTML string, grouped by day."""
    if not events:
        # Saying so beats silence — silence is indistinguishable from the
        # cron job having failed.
        return "<b>SF events</b>\n\nNothing new today."

    by_date: dict[str, list[Event]] = {}
    for event in sorted(events, key=lambda e: (e.date, e.time)):
        by_date.setdefault(event.date, []).append(event)

    count = len(events)
    parts = [f"<b>SF events</b> — {count} new event{'s' if count != 1 else ''}"]

    for iso_date, day_events in by_date.items():
        parts.append(f"\n<b>{day_header(iso_date, today)}</b>")
        parts.extend(format_event(e) for e in day_events)

    return "\n".join(parts)


def split_message(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Split an over-long digest on day boundaries.

    Breaking mid-event would strand a title in one message and its time in
    the next, so chunks are assembled from whole blocks.
    """
    if len(text) <= limit:
        return [text]

    chunks, current = [], ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > limit and current:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def send_digest(text: str, token: str | None = None,
                chat_id: str | None = None) -> bool:
    """Deliver the digest. Returns True if it went to Telegram.

    Falls back to printing when the bot isn't configured — that's the
    documented behaviour, so an unconfigured run is a successful run.
    """
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print(text)
        return False

    for chunk in split_message(text):
        response = requests.post(
            TELEGRAM_API.format(token=token),
            json={
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                # Event links would otherwise generate a large preview card
                # per event and bury the digest.
                "disable_web_page_preview": True,
            },
            timeout=SEND_TIMEOUT_SECONDS,
        )
        if not response.ok:
            # Surface Telegram's own message — it names the actual problem
            # (bad chat_id, unescaped HTML) far better than a status code.
            raise RuntimeError(f"Telegram rejected the message: {response.text}")

    return True


if __name__ == "__main__":
    # python -m src.digest — build a real digest from the live sources.
    # Prints unless the Telegram env vars are set. No Anthropic API call.
    from src.dedupe import merge_duplicates
    from src.extract import dedupe_exact, in_bay_area, parse_events
    from src.fetch import FetchError, fetch_page
    from src.sources import load_sources

    events = []
    for source in load_sources():
        if source.type != "page":
            continue
        try:
            events.extend(
                e for e in parse_events(fetch_page(source)) if in_bay_area(e)
            )
        except FetchError as e:
            print(f"[warn] {e}")

    events = merge_duplicates(dedupe_exact(events))
    text = format_digest(events)

    if send_digest(text):
        print(f"Sent {len(events)} events to Telegram.")
