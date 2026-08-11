"""Turn fetched pages into filtered events.

Two halves, deliberately separate:

  parse_events()        free. Reads schema.org JSON-LD embedded in the page.
  filter_professional() paid. One Claude call for the whole run.

Splitting them is what makes this cheap. The structured data already holds
title, date, venue, host, url and price exactly — asking a model to read them
out of rendered text would cost ~10,000 tokens per page and be less accurate.
The only thing left that needs judgment is "would this be worth my evening",
which needs a title, not a whole page.
"""

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import os
import re

from bs4 import BeautifulSoup

from src.fetch import FetchedPage

MODEL = "claude-sonnet-5"

# The digest is read in San Francisco, so every time is shown in SF time.
PACIFIC = ZoneInfo("America/Los_Angeles")

# Everything above this many events gets dropped before the API call. A
# runaway page can't turn into a runaway bill.
MAX_EVENTS_PER_CALL = 200

# Rough token estimate for --estimate. Real counting needs an API call, and
# the whole point of --estimate is to not make one.
CHARS_PER_TOKEN = 4

BAY_AREA_TERMS = (
    "san francisco", "sf", "oakland", "berkeley", "palo alto", "mountain view",
    "menlo park", "sunnyvale", "santa clara", "san jose", "san mateo",
    "redwood city", "cupertino", "emeryville", "bay area", "silicon valley",
)

FILTER_SYSTEM = """\
You decide which events belong in a daily digest of professional tech events \
in San Francisco.

KEEP: tech talks, company events, AI/ML meetups, hackathons, demo nights, \
founder and VC events, conferences, workshops, technical trainings.

DROP: dating events, language exchanges, party and club nights, fitness and \
run clubs, generic social mixers with no professional angle, MLM and \
"business opportunity" pitches, wellness and spirituality, art and craft \
classes, food events.

The test is whether someone would go for their career or their technical \
interests, not to socialise. A "Founders & Funders Mixer" is professional; a \
"Singles Mixer" is not. When a title is genuinely ambiguous, drop it — a \
short digest the person trusts beats a long one they stop reading.

For each event also return a one_liner: at most 12 words, plain and factual, \
saying what actually happens. If the event already has a description, \
compress it. Never invent details that aren't in the input.\
"""

FILTER_SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "professional": {"type": "boolean"},
                    "one_liner": {"type": "string"},
                },
                "required": ["index", "professional", "one_liner"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["decisions"],
    "additionalProperties": False,
}


@dataclass
class Event:
    title: str
    date: str = ""
    time: str = ""
    venue: str = ""
    host: str = ""
    url: str = ""
    price: str = ""
    one_liner: str = ""
    source: str = ""
    # Kept for the location check and for debugging a wrong drop.
    location_blob: str = field(default="", repr=False)

    def __str__(self) -> str:
        when = f"{self.date} {self.time}".strip()
        return f"{self.title[:60]:60} {when:16} @ {self.venue[:24]}"


# --------------------------------------------------------------------------
# Free half: parse embedded structured data
# --------------------------------------------------------------------------

def parse_events(page: FetchedPage) -> list[Event]:
    """Pull events out of a page's schema.org JSON-LD.

    Handles both shapes we've seen: bare `@type: Event` objects (Meetup) and
    events nested inside an ItemList (Luma, Eventbrite).
    """
    soup = BeautifulSoup(page.html, "html.parser")
    events = []

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "{}")
        except (json.JSONDecodeError, TypeError):
            # A malformed block on one site must not lose the others.
            continue
        for raw in _walk_for_events(data):
            event = _to_event(raw, source=page.source.name)
            if event is not None:
                events.append(event)

    return events


def _walk_for_events(node: object) -> list[dict]:
    """Recursively collect Event-shaped dicts from a JSON-LD document."""
    found = []
    if isinstance(node, list):
        for item in node:
            found.extend(_walk_for_events(item))
    elif isinstance(node, dict):
        type_ = node.get("@type")
        if type_ == "Event" or (type_ is None and "startDate" in node and "name" in node):
            found.append(node)
        else:
            # ItemList wraps each event in {"item": {...}}; @graph is the
            # other common container.
            for key in ("itemListElement", "@graph", "item", "events"):
                if key in node:
                    found.extend(_walk_for_events(node[key]))
    return found


def _to_event(raw: dict, source: str) -> Event | None:
    """Normalise one JSON-LD Event into our own shape."""
    title = (raw.get("name") or "").strip()
    if not title:
        return None

    date, time = _split_start(raw.get("startDate", ""))
    venue, blob = _location(raw.get("location"))

    return Event(
        title=title,
        date=date,
        time=time,
        venue=venue,
        host=_organizer(raw.get("organizer")),
        url=(raw.get("url") or raw.get("@id") or "").strip(),
        price=_price(raw.get("offers")),
        one_liner=_clean_description(raw.get("description", "")),
        source=source,
        location_blob=blob,
    )


def _split_start(start: str) -> tuple[str, str]:
    """'2026-08-09T18:00:00-07:00' -> ('2026-08-09', '18:00'), in SF time.

    Sources disagree about timezone: Luma sends local offsets (-07:00),
    Meetup sends UTC ('...Z'). Slicing the string naively made a 6pm Tuesday
    Meetup event read as 1am Wednesday — wrong day *and* wrong time, on every
    Meetup event. Everything is converted to Pacific, since that's where the
    person reading the digest is standing.
    """
    if not isinstance(start, str) or not start:
        return "", ""
    if "T" not in start:
        return start[:10], ""

    try:
        parsed = datetime.fromisoformat(start.replace("Z", "+00:00"))
    except ValueError:
        # Unparseable: fall back to the raw slice rather than losing the event.
        date, _, rest = start.partition("T")
        return date, rest[:5]

    if parsed.tzinfo is None:
        # No offset at all — assume it's already local rather than shifting it.
        return parsed.strftime("%Y-%m-%d"), parsed.strftime("%H:%M")

    local = parsed.astimezone(PACIFIC)
    return local.strftime("%Y-%m-%d"), local.strftime("%H:%M")


def _location(loc: object) -> tuple[str, str]:
    """Return (venue name, searchable blob of everything location-ish)."""
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if isinstance(loc, str):
        return loc.strip(), loc.lower()
    if not isinstance(loc, dict):
        return "", ""

    name = (loc.get("name") or "").strip()
    address = loc.get("address")
    parts = [name]
    if isinstance(address, str):
        parts.append(address)
    elif isinstance(address, dict):
        parts += [
            str(address.get(k, ""))
            for k in ("streetAddress", "addressLocality", "addressRegion", "addressCountry")
        ]
    return name, " ".join(p for p in parts if p).lower()


def _organizer(org: object) -> str:
    if isinstance(org, list):
        org = org[0] if org else None
    if isinstance(org, dict):
        return (org.get("name") or "").strip()
    return str(org).strip() if isinstance(org, str) else ""


def _price(offers: object) -> str:
    """Best-effort price string. Absent offers means unknown, not free."""
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    if not isinstance(offers, dict):
        return ""
    price = offers.get("price")
    if price in (None, ""):
        return ""
    if str(price) in ("0", "0.0", "0.00"):
        return "Free"
    # Luma sends "usd" lowercase, so a case-sensitive check rendered "$20"
    # as "usd 20".
    currency = str(offers.get("priceCurrency", "")).upper()
    symbol = "$" if currency in ("USD", "") else f"{currency} "
    return f"{symbol}{price}"


def _clean_description(text: object) -> str:
    """Collapse a description into one readable line.

    Meetup and Luma descriptions are markdown, so they arrive full of
    '**bold**', '\\#' headings and '[RSVP here](url)' — which render as
    literal noise in a Telegram message. The filter rewrites these into a
    proper one_liner anyway; this is what an unfiltered run shows.
    """
    if not isinstance(text, str):
        return ""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # links -> label
    text = re.sub(r"[*_#`~\\]+", "", text)                # emphasis markers
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 160:
        # Cut at a word boundary; a mid-word ellipsis reads as corruption.
        text = text[:160].rsplit(" ", 1)[0] + "…"
    return text


def in_bay_area(event: Event) -> bool:
    """Keep Bay Area events, drop clearly-elsewhere ones, keep unknowns.

    This is what stops Eventbrite's Hong Kong conferences reaching the paid
    call. Unknown locations are kept rather than dropped: Luma often gives a
    venue name with no city ("Frontier Tower"), and silently discarding real
    SF events would be a worse failure than paying for a few extra titles.
    """
    blob = event.location_blob
    if not blob:
        return True
    if any(term in blob for term in BAY_AREA_TERMS):
        return True
    # An explicit non-US country or non-CA state is a confident drop.
    if re.search(r"\b(hong kong|singapore|london|dubai|india|toronto|berlin|tokyo)\b", blob):
        return False
    if re.search(r"\b(new york|austin|seattle|boston|chicago|los angeles|miami)\b", blob):
        return False
    return True


def dedupe_exact(events: list[Event]) -> list[Event]:
    """Drop byte-identical repeats before paying to classify them.

    Eventbrite lists the same event three times. This is not the fuzzy
    cross-platform matching of step 6 — just the cheap obvious pass.
    """
    seen, out = set(), []
    for e in events:
        key = (e.title.lower().strip(), e.date)
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


# --------------------------------------------------------------------------
# Paid half: one call, the whole run
# --------------------------------------------------------------------------

def build_filter_prompt(events: list[Event]) -> str:
    """The user turn: a numbered list of candidates, nothing else."""
    lines = []
    for i, e in enumerate(events):
        bits = [f"{i}. {e.title}"]
        if e.venue:
            bits.append(f"venue: {e.venue}")
        if e.one_liner:
            bits.append(f"about: {e.one_liner[:200]}")
        lines.append(" | ".join(bits))
    return (
        "Classify each event below. Return one decision per index.\n\n"
        + "\n".join(lines)
    )


def estimate_tokens(prompt: str) -> int:
    """Rough input-token estimate without calling the API."""
    return (len(FILTER_SYSTEM) + len(prompt)) // CHARS_PER_TOKEN


def filter_professional(events: list[Event], model: str = MODEL) -> list[Event]:
    """One API call. Returns only the professional events, one_liners filled.

    Requires ANTHROPIC_API_KEY. Raises rather than silently returning
    everything — an unfiltered digest is the failure this project exists to
    prevent.
    """
    import anthropic  # imported here so the free path needs no API package

    if not events:
        return []
    if len(events) > MAX_EVENTS_PER_CALL:
        # Bound the spend. Sorted by date first so we keep the soonest.
        events = sorted(events, key=lambda e: e.date)[:MAX_EVENTS_PER_CALL]

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Run:  set -a; source .env; set +a"
        )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=8000,
        system=FILTER_SYSTEM,
        # A classification needs no reasoning tokens; low effort keeps the
        # cost at roughly the size of the list itself.
        thinking={"type": "disabled"},
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": FILTER_SCHEMA},
        },
        messages=[{"role": "user", "content": build_filter_prompt(events)}],
    )

    text = next(b.text for b in response.content if b.type == "text")
    decisions = json.loads(text)["decisions"]

    kept = []
    for d in decisions:
        i = d["index"]
        if not d["professional"] or not 0 <= i < len(events):
            continue
        event = events[i]
        if d.get("one_liner"):
            event.one_liner = d["one_liner"]
        kept.append(event)

    usage = response.usage
    print(
        f"[filter] {len(kept)}/{len(events)} kept · "
        f"{usage.input_tokens} in / {usage.output_tokens} out"
    )
    return kept


if __name__ == "__main__":
    import sys

    from src.fetch import fetch_page
    from src.sources import load_sources

    do_filter = "--filter" in sys.argv
    estimate = "--estimate" in sys.argv

    all_events = []
    for source in load_sources():
        if source.type != "page":
            continue
        page = fetch_page(source)
        found = parse_events(page)
        local = [e for e in found if in_bay_area(e)]
        print(f"\n### {source.name}: {len(found)} parsed, {len(local)} in the Bay Area")
        for e in local:
            print(f"  {e}")
        all_events.extend(local)

    all_events = dedupe_exact(all_events)
    print(f"\n{len(all_events)} events after exact dedupe.")

    if estimate or do_filter:
        prompt = build_filter_prompt(all_events)
        print(f"\n--- filter prompt ({len(prompt):,} chars, "
              f"~{estimate_tokens(prompt):,} input tokens) ---")
    if estimate:
        print(prompt)
        cost = estimate_tokens(prompt) / 1_000_000 * 2  # $2/MTok Sonnet 5 intro
        print(f"\nEstimated input cost of one call: ${cost:.4f}. Nothing was sent.")
    if do_filter:
        for e in filter_professional(all_events):
            print(f"  KEEP  {e}\n        {e.one_liner}")
