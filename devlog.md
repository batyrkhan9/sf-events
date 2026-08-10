# Devlog

## Step 1 — project structure and README

Set up the skeleton: `src/` as a package, `requirements.txt`, an
`.env.example` template for the three env vars, and a gitignore. The README
does most of the work here — it states the problem (SF tech events scattered
across Luma, Partiful, Eventbrite and company pages, only found out about
after the fact via LinkedIn) and lays out the six modules to come, marked
clearly as not yet built.

The decision worth recording: extraction and filtering will share a single
Claude API call per source rather than running as two passes. Both are
judgment calls over the same page of text, so two passes would mean paying to
read it twice.

One correction along the way — `CLAUDE.md` is gitignored rather than
committed. It's the working spec for how the project gets built, not part of
the project itself, and this repo should read as its author's own work.

## Step 2 — sources.yaml and its loader

`sources.yaml` defines seven sources: Luma SF, Luma AI, Partiful and
Eventbrite as `page` type, plus `search` entries for Google, Nvidia and
OpenAI. The companies get searches rather than URLs because none of them
keeps a single scrapable events page, and a guessed URL would rot silently.

The loader validates hard and fails loudly — unknown type, missing
`url`/`query`, misspelled keys, duplicate names. That's a deliberate split
from the "one broken source must not kill the run" rule: a source being down
is a runtime problem worth surviving, but a typo in the config means a source
you believe is running silently isn't, and a short digest looks exactly like
a quiet week in SF.

Two things came out of actually running it. Checking for unknown keys had to
move *before* the missing-field check — a typo'd `urls:` was reporting "needs
a url" while a url-shaped line sat right there in the file. And YAML's
boolean coercion bit: a test source named `On` was parsed as the boolean
`True`, so a real source named `No` or `Off` would fail with a confusing
"missing name" error. The loader now names that trap explicitly.

Also worth noting: the system `python3` here is 3.9, below the 3.11 the
project needs, so the venv is built from the Anaconda 3.11 interpreter.

## Step 3 — the page fetcher, and a change of plan

The fetcher itself is small: GET with a browser User-Agent (Python's default
gets 403'd plenty of places), strip the tags that never hold event listings,
collapse the blank-line sludge. It returns both the raw HTML and the cleaned
text, and a dead source raises `FetchError` rather than returning an empty
string — the pipeline has to be able to tell "this site is down" from "no
events today," and empty text looks identical to both.

The bigger decision came first. Asking what the API would cost led to
realising these sites embed their event data as JSON right in the HTML —
`schema.org` blocks and Next.js `__NEXT_DATA__` blobs. Parsing that is free,
exact, and sidesteps the JavaScript-rendering problem entirely. So extraction
splits from filtering: structured data gives us the fields, and Claude only
judges professional-vs-not on a title and a line of description. That takes
the per-page cost from ~10,000 tokens to ~40, or roughly a cent a month
instead of ten dollars. It contradicts CLAUDE.md's "same call" design, which
was right when both halves needed a model to read a page and isn't now that
one half is a JSON parse. Web search sources are muted until step 5 for the
same reason — they're the one part that can't be free.

Fetching the four real URLs was the most useful thing in this step, because
two of the four sources turned out to be worthless. Luma SF is excellent: 20
events with title, start, end, venue, url, price and host. Eventbrite works
but returns Hong Kong conferences from a San Francisco URL, plus the same
event three times — dedup in step 6 just justified itself. But `lu.ma/ai`
returns zero events (it lists calendars, not events — wrong URL), and
Partiful's five SF entries were a candle class, a watercolour picnic, a photo
walk, a pastry pop-up and "Berkeley Nights." Not one professional event.
Guessing at source URLs and confirming they work are very different things,
and it was worth finding that out at step 3 rather than step 8.

That left the project looking thin — one good source and one dirty one is not
worth building a tool for. The fix was to go looking for other sites that
publish structured data, which turned up Meetup (a third platform, with
descriptions Luma doesn't provide) and the correct Luma category URL. Devpost,
GDG, Cerebral Valley and SF Tech Week publish none and were left out. Four
working sources across three platforms now, and the lesson is the same one as
above: a source list written from memory is a hypothesis, not a configuration.
