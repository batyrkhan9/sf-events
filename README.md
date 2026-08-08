# sf-events

A personal daily digest of professional tech events in San Francisco.

## The problem

The good events in SF are never in one place. Some live on Luma, some on
Partiful, some on Eventbrite, and the best ones — the Google, Nvidia and
OpenAI evenings — are buried on company event pages that nobody thinks to
check. There's no feed. There's no calendar. There's just a dozen tabs I'd
have to open every morning and never do.

So I find out the way everyone else does: someone reshares it on LinkedIn,
three days after it happened. I miss the events I'd most want to be at, not
because they were hard to get into, but because I never heard about them.

## What this does

Every morning, sf-events checks all of those sources for me, pulls out the
events, throws away everything that isn't professional, and sends me one
Telegram message with what's worth going to.

- **Checks every source daily** — event platforms and company pages, plus
  web searches for companies that don't keep a scrapable event page.
- **Extracts events with the Claude API** — a page of messy HTML becomes
  structured events: title, date, time, venue, host, url, price, one-liner.
- **Filters to professional events only** — tech talks, company events,
  AI/ML meetups, hackathons, demo nights, founder/VC events, conferences,
  workshops. Dating, language exchange, party nights, fitness, generic
  mixers and MLM pitches get dropped.
- **Only shows me what's new** — fuzzy title matching catches the same event
  cross-posted to two platforms, and already-sent events don't come back.
- **One message a morning** — delivered to Telegram, or printed to the
  console if no bot token is configured.

## Planned architecture

This repo is at step 2 of 9. Each piece lands in its own commit, in this
order — ✅ means it exists today:

| Piece | Role |
| --- | --- |
| ✅ `sources.yaml` + `src/sources.py` | The source list and its loader. Two kinds: `page` (a URL to fetch and parse) and `search` (a query run through the Claude API's web search tool) |
| `src/fetch.py` | HTTP GET a page, strip the HTML down to visible text |
| `src/extract.py` | One Claude API call per source that extracts events as JSON *and* applies the professional-only filter in the same pass |
| `src/dedupe.py` | Fuzzy title matching across platforms, plus `seen.json` so each digest only carries new events |
| `src/digest.py` | Format the digest and send it via Telegram bot |
| `src/main.py` | The pipeline — fetch → extract → dedupe → digest — with a `--dry-run` flag. One broken source must not kill the run |

The design principle worth naming: extraction and filtering happen in a
single model call per source. Parsing an event page and deciding whether the
event is worth my evening are both judgment calls, and splitting them into
two passes would mean paying twice to read the same text.

## Setup

Requires Python 3.11 or newer.

```bash
git clone https://github.com/batyrkhan9/sf-events.git
cd sf-events

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# then open .env and fill in your keys
```

### Configuration

| Variable | Required | What it's for |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | yes | Event extraction and filtering |
| `TELEGRAM_BOT_TOKEN` | no | Digest delivery. Without it, the digest prints to the console |
| `TELEGRAM_CHAT_ID` | no | The chat the digest is sent to |

`.env` and `seen.json` are gitignored — keys and local state stay out of the
repo.

### Running it

Once the pipeline exists (step 8), a run will look like:

```bash
python -m src.main --dry-run   # fetch and print, send nothing
python -m src.main             # the real thing
```

## Devlog

This is a learning project as much as a tool, so the process is part of the
output. [devlog.md](devlog.md) records what got built at each step, what was
decided, and what turned out to be harder than expected.
