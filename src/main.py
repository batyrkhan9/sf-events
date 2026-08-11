"""The pipeline: fetch -> extract -> dedupe -> digest.

Run daily from cron. One broken source must not kill the run — a site
redesign or a timeout should cost you that source's events, not the digest.
"""

import argparse
import sys

from src.dedupe import load_seen, merge_duplicates, new_events, save_seen
from src.digest import format_digest, send_digest
from src.extract import (
    Event,
    build_filter_prompt,
    dedupe_exact,
    estimate_tokens,
    filter_professional,
    in_bay_area,
    parse_events,
)
from src.fetch import FetchError, fetch_page
from src.sources import ConfigError, Source, load_sources

# Sonnet 5 introductory input price, for the per-run cost line. Output is a
# few hundred tokens on top; this is the order of magnitude, not an invoice.
DOLLARS_PER_INPUT_TOKEN = 2 / 1_000_000


def collect(sources: list[Source]) -> tuple[list[Event], int]:
    """Fetch and parse every page source. Returns (events, failure count).

    Failures are logged and skipped. Losing one platform is a bad morning;
    losing the whole digest because one site changed its markup is worse.
    """
    events: list[Event] = []
    failures = 0

    for source in sources:
        if source.type != "page":
            # Search sources arrive in step 5. Skipping quietly here means
            # enabling one later doesn't require touching this file.
            continue
        try:
            page = fetch_page(source)
            found = parse_events(page)
        except FetchError as e:
            print(f"[warn] {e}", file=sys.stderr)
            failures += 1
            continue
        except Exception as e:  # noqa: BLE001 — one bad page can't stop the run
            print(f"[warn] {source.name}: {e.__class__.__name__}: {e}", file=sys.stderr)
            failures += 1
            continue

        local = [e for e in found if in_bay_area(e)]
        print(f"  {source.name:20} {len(found):>3} parsed, {len(local):>3} local")
        events.extend(local)

    return events, failures


def run(dry_run: bool = False, use_filter: bool = True) -> int:
    """Run the pipeline. Returns a process exit code."""
    try:
        sources = load_sources()
    except ConfigError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 2

    page_sources = [s for s in sources if s.type == "page"]
    print(f"Checking {len(page_sources)} sources...")
    events, failures = collect(sources)

    if failures and failures == len(page_sources):
        # Every source failing is a network or code problem, not a quiet
        # news day. Exit non-zero so cron surfaces it.
        print("[error] every source failed", file=sys.stderr)
        return 1

    counts = {"parsed": len(events)}
    events = merge_duplicates(dedupe_exact(events))
    counts["deduped"] = len(events)

    if use_filter:
        prompt = build_filter_prompt(events)
        tokens = estimate_tokens(prompt)
        print(f"\nFiltering {len(events)} events (~{tokens:,} tokens, "
              f"~${tokens * DOLLARS_PER_INPUT_TOKEN:.4f})...")
        try:
            events = filter_professional(events)
        except Exception as e:  # noqa: BLE001
            # An unfiltered digest is still worth sending — 49 events beats
            # nothing — but say clearly that it wasn't filtered.
            print(f"[warn] filter failed ({e}); sending unfiltered", file=sys.stderr)
    else:
        print("\nSkipping filter (--no-filter).")
    counts["professional"] = len(events)

    seen = load_seen()
    events = new_events(events, seen)
    counts["new"] = len(events)

    print("\n" + " -> ".join(f"{k}: {v}" for k, v in counts.items()))

    text = format_digest(events)

    if dry_run:
        print("\n--- dry run, nothing sent or recorded ---\n")
        print(text)
        return 0

    if send_digest(text):
        print(f"\nSent {len(events)} events to Telegram.")
    # Only record after delivery succeeded. A crash mid-send must not mark
    # events as seen, or they're lost forever.
    save_seen(events, seen)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send a daily digest of professional tech events in SF."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the digest instead of sending it, and don't record "
             "anything as seen",
    )
    parser.add_argument(
        "--no-filter",
        action="store_true",
        help="skip the Claude call. The run costs nothing and the digest "
             "is unfiltered",
    )
    args = parser.parse_args()
    return run(dry_run=args.dry_run, use_filter=not args.no_filter)


if __name__ == "__main__":
    sys.exit(main())
