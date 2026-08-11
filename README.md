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

✅ means it exists and runs today.

| Piece | Role |
| --- | --- |
| ✅ `sources.yaml` + `src/sources.py` | The source list and its loader. Two kinds: `page` (a URL to fetch and parse) and `search` (a query run through the Claude API's web search tool) |
| ✅ `src/fetch.py` | HTTP GET a page, keeping both the HTML and its visible text |
| ✅ `src/extract.py` | Reads event data embedded in the page, then one Claude call to filter down to professional events |
| ✅ `src/dedupe.py` | Cross-platform duplicate matching, plus `seen.json` so each digest only carries new events |
| ✅ `src/digest.py` | Format the digest and send it via Telegram bot |
| `src/main.py` | The pipeline — fetch → extract → dedupe → digest — with a `--dry-run` flag. One broken source must not kill the run |

Web search sources are written but muted, pending a decision about API spend.

**The design decision worth naming: reading events is free, judging them is
not.** These sites embed their event data as `schema.org` JSON right in the
HTML, so title, date, venue, host, price and URL can be parsed exactly, at no
cost, without a model ever seeing the page. That leaves one genuinely
judgment-shaped question — is this worth an evening? — which needs a title,
not ten thousand tokens of rendered page. All the candidates go up in a
single call. A run costs about half a cent.

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
| `ANTHROPIC_API_KEY` | no | The professional-events filter. Without it, everything still runs — the digest is just unfiltered |
| `TELEGRAM_BOT_TOKEN` | no | Digest delivery. Without it, the digest prints to the console |
| `TELEGRAM_CHAT_ID` | no | The chat the digest is sent to |

`.env` and `seen.json` are gitignored — keys and local state stay out of the
repo.

### Setting up the Telegram bot

Free, and no billing attached to any of it.

1. Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`,
   and follow the prompts. It replies with a token — that's
   `TELEGRAM_BOT_TOKEN`.
2. Send any message to your new bot (it can't message you first).
3. Open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser
   and find `"chat":{"id":...}` — that number is `TELEGRAM_CHAT_ID`.

Put both in `.env`. Skip this entirely and the digest prints to your
terminal, which is the easiest way to see the output before committing to
anything.

### Running it

The full pipeline arrives in step 8. Until then each stage runs on its own,
and none of these spend money unless you add `--filter`:

```bash
python -m src.fetch              # fetch every source, cache pages in raw/
python -m src.extract            # parse events out of the cached pages
python -m src.extract --estimate # show the filter prompt and its cost, send nothing
python -m src.extract --filter   # the only command that calls the API
python -m src.dedupe             # show what merges across platforms, and why
python -m src.digest             # build today's digest and print it
```

## Devlog

This is a learning project as much as a tool, so the process is part of the
output. [devlog.md](devlog.md) records what got built at each step, what was
decided, and what turned out to be harder than expected.
