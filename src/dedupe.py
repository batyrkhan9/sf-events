"""Collapse duplicate events, and remember what's already been sent.

Two separate jobs that both hinge on identifying "the same event":

  merge_duplicates()  the same event cross-posted to two platforms
  load_seen/save_seen  events already delivered on a previous morning

The asymmetry that drives every choice here: merging two events wrongly means
you never learn one of them existed. Splitting two wrongly means one extra
line in a message you're already reading. So everything errs toward splitting.
"""

from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
import json
import re

from src.extract import Event

SEEN_PATH = Path("seen.json")

# Titles this similar, on the same date, are one event regardless of anything
# else. High on purpose — this is the only rule that fires without
# corroborating evidence.
SIMILARITY_THRESHOLD = 0.90

# Weaker title/host agreement is enough when the start time also matches.
# A cross-posted event keeps its time; two unrelated events sharing a date,
# a start time, AND a similar host is vanishingly unlikely.
CORROBORATED_THRESHOLD = 0.60

# How long a sent event stays in seen.json after its date passes. Without
# pruning the file grows forever; without a grace period, an event that slips
# a day could be re-sent as new.
SEEN_RETENTION_DAYS = 30

# Trailing location tags that carry no information about which event it is.
CITY_SUFFIX = re.compile(
    r"[\s|,\-–—]+(in\s+)?(san\s+francisco|sf|bay\s+area|oakland|berkeley)\s*$",
    re.IGNORECASE,
)


def normalize_title(title: str) -> str:
    """Reduce a title to the part that identifies the event.

    Strips emoji, punctuation and city tags so that '👾 Hack Night — SF' and
    'Hack Night' compare as the same string.
    """
    text = title.lower()
    text = CITY_SUFFIX.sub("", text)
    # Keep letters, digits and spaces; everything else is decoration. This
    # also removes the emoji that Luma organisers put in titles.
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def event_key(event: Event) -> str:
    """A stable identity for an event across days. Used by seen.json."""
    return f"{normalize_title(event.title)}|{event.date}"


def similarity(a: str, b: str) -> float:
    """Character-level similarity. Good at typos, bad at reordered words."""
    return SequenceMatcher(None, a, b).ratio()


def token_overlap(a: str, b: str) -> float:
    """Word-level overlap, insensitive to order and to extra words.

    Needed because platforms retitle the same event freely: 'AI Engineers
    Tech Talk: August' and 'SF AI Engineers: Aug' are one event, but share
    only two words in a different order — SequenceMatcher scores that 0.67
    while the shorter-string overlap is 0.50.

    Divides by the smaller set so that one platform padding the title with
    extra words doesn't sink the score.
    """
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def same_event(a: Event, b: Event) -> bool:
    """Decide whether two records describe one event.

    Rules, in order of confidence:

    1. Different dates -> never the same event.
    2. Same source with different URLs -> never the same event. A platform
       does not list one event twice under two links, so this is a hard
       block. It's what stops Luma's 'Claude Code Workshop' and 'Claude
       Coworkshop' (same host, same day, 0.92 similar, different URLs) from
       being collapsed into one.
    3. Near-identical titles -> the same event.
    4. Same start time AND a decent title or host match -> the same event.
       This is what catches cross-platform reposts that were retitled.
    """
    if a.date != b.date:
        return False

    if a.source == b.source and a.url and b.url and a.url != b.url:
        return False

    title_a, title_b = normalize_title(a.title), normalize_title(b.title)
    title_score = max(similarity(title_a, title_b), token_overlap(title_a, title_b))

    if title_score >= SIMILARITY_THRESHOLD:
        return True

    if a.time and a.time == b.time:
        host_score = token_overlap(normalize_title(a.host), normalize_title(b.host))
        if max(title_score, host_score) >= CORROBORATED_THRESHOLD:
            return True

    return False


def _completeness(event: Event) -> int:
    """How many useful fields this record has filled in.

    When two listings are the same event, keep the richer one — a Meetup
    entry with a description beats a Luma entry without one.
    """
    return sum(
        1
        for value in (event.time, event.venue, event.host, event.url,
                      event.price, event.one_liner)
        if value
    )


def merge_duplicates(events: list[Event]) -> list[Event]:
    """Collapse the same event appearing on more than one platform.

    Pairing rules live in same_event(). Records are processed richest-first
    so the surviving copy is the one with the most fields, and the duplicate
    only fills its gaps.
    """
    kept: list[Event] = []

    for event in sorted(events, key=lambda e: -_completeness(e)):
        duplicate_of = None

        for existing in kept:
            if same_event(event, existing):
                duplicate_of = existing
                break

        if duplicate_of is None:
            kept.append(event)
        else:
            # The richer record is already in `kept` (we sorted by
            # completeness), so fill only its gaps from the duplicate.
            _fill_gaps(duplicate_of, event)

    return sorted(kept, key=lambda e: (e.date, e.time))


def _fill_gaps(primary: Event, other: Event) -> None:
    """Copy fields the primary record is missing. Never overwrites."""
    for field_name in ("time", "venue", "host", "url", "price", "one_liner"):
        if not getattr(primary, field_name) and getattr(other, field_name):
            setattr(primary, field_name, getattr(other, field_name))
    if other.source not in primary.source:
        primary.source = f"{primary.source}, {other.source}"


# --------------------------------------------------------------------------
# seen.json — what has already been delivered
# --------------------------------------------------------------------------

def load_seen(path: Path = SEEN_PATH) -> dict[str, str]:
    """Read the sent-events record. A missing or corrupt file is not fatal.

    Returns {event_key: event_date}. If the file is unreadable we start
    fresh — the cost is one duplicated digest, which beats crashing the
    morning run over a malformed state file.
    """
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def new_events(events: list[Event], seen: dict[str, str]) -> list[Event]:
    """The events not already delivered. Pure — records nothing."""
    return [e for e in events if event_key(e) not in seen]


def save_seen(events: list[Event], seen: dict[str, str],
              path: Path = SEEN_PATH) -> dict[str, str]:
    """Record events as delivered, and prune entries that have aged out.

    Deliberately NOT a side effect of new_events(): a --dry-run must be able
    to check what's new without marking it sent, or the real digest that
    morning arrives empty.
    """
    updated = dict(seen)
    for event in events:
        updated[event_key(event)] = event.date

    cutoff = (date.today() - timedelta(days=SEEN_RETENTION_DAYS)).isoformat()
    updated = {
        key: event_date
        for key, event_date in updated.items()
        # Keep undated entries: we can't prove they've passed.
        if not event_date or event_date >= cutoff
    }

    Path(path).write_text(json.dumps(updated, indent=2, sort_keys=True))
    return updated


if __name__ == "__main__":
    # python -m src.dedupe — show what merges across the real sources.
    from src.extract import dedupe_exact, in_bay_area, parse_events
    from src.fetch import fetch_page
    from src.sources import load_sources

    events = []
    for source in load_sources():
        if source.type == "page":
            events.extend(
                e for e in parse_events(fetch_page(source)) if in_bay_area(e)
            )
    events = dedupe_exact(events)
    print(f"{len(events)} events before fuzzy merge")

    merged = merge_duplicates(events)
    print(f"{len(merged)} after ({len(events) - len(merged)} merged)\n")

    # Show every close call with the reason for its verdict — that's how you
    # tell a real threshold from one that happens to fit today's data.
    print("Close calls on the same date:")
    pairs = []
    for i, a in enumerate(events):
        for b in events[i + 1:]:
            if a.date != b.date:
                continue
            ta, tb = normalize_title(a.title), normalize_title(b.title)
            score = max(similarity(ta, tb), token_overlap(ta, tb))
            host = token_overlap(normalize_title(a.host), normalize_title(b.host))
            if score > 0.45 or (a.time and a.time == b.time and host > 0.5):
                pairs.append((score, host, a, b))

    for score, host, a, b in sorted(pairs, reverse=True, key=lambda p: p[0])[:12]:
        if same_event(a, b):
            why = "MERGED"
        elif a.source == b.source and a.url and b.url and a.url != b.url:
            why = "split: same platform, different URLs"
        else:
            why = "split: below threshold"
        clock = "same time" if a.time and a.time == b.time else "diff time"
        print(f"  title={score:.2f} host={host:.2f} {clock:9}  {why}")
        print(f"     {a.title[:46]!r}  [{a.source}]")
        print(f"     {b.title[:46]!r}  [{b.source}]")
