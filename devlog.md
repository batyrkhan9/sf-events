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

## Step 4 — extraction, and the cheapest thing that works

The parser reads the schema.org JSON-LD embedded in each page and normalises
it into one `Event` shape. Two site conventions to handle: Meetup emits bare
`@type: Event` objects, Luma and Eventbrite nest them inside an `ItemList`.
82 events parsed across four sources, 52 after the location check and an
exact-duplicate pass. All of it free.

The location check keeps events whose location it can't identify, and only
drops on a confident signal. Luma routinely gives a venue name with no city —
"Frontier Tower", "Convex" — so a strict allowlist would throw away real SF
events. Being wrong in the keep direction costs a fraction of a cent; being
wrong in the drop direction means missing the thing this tool exists to find.
It correctly caught an Austin event whose title read "TechCrunch Disrupt SF".

Testing surfaced a bug that would have been invisible in production: Meetup
sends `startDate` in UTC, Luma sends local offsets. The first implementation
just sliced the first five characters of the time, so a 6pm Tuesday Meetup
event was being recorded as 1am Wednesday — wrong time and wrong day, on
every Meetup event. Everything now converts to Pacific. The giveaway was an
event whose own title contained the time: "SF Edition - Saturday, 10:00 AM"
was displaying as 17:00.

The filter is one API call for the whole run rather than one per event, with
structured outputs so the response is a validated schema instead of prose to
parse. Roughly half a cent per run, about fifteen cents a month. It is
written but has never been executed — `--estimate` prints the prompt and the
cost without sending anything, and `--filter` is the only path that spends
money. Deliberate: the code is ready whenever the API key is, and not before.

## Step 6 — dedup, where the obvious approach was wrong twice

Step 5 got postponed. The company searches need the API and there's no way to
build them and leave them unrun the way the filter was, so the plan is to
finish the free pipeline first and evaluate that step against a tool that
already works rather than in the abstract.

The plan for dedup was fuzzy title matching above a threshold, and the plan
was wrong in both directions — which only showed up by running it against the
real 50 events. It merged Luma's "Claude Code Workshop" and "Claude
Coworkshop": same day, same host, 0.92 similar, and two genuinely different
events with different Luma URLs. One of them was being silently deleted. At
the same time it missed the actual cross-post — "AI Engineers Tech Talk:
August" on Luma and "SF AI Engineers: Aug" on Meetup, the same event on two
platforms, scoring only 0.67.

The fix was to stop treating title similarity as the signal and treat it as
one signal among three. A platform never lists one event under two URLs, so
same-source-different-URL is now a hard block regardless of how alike the
titles are. And weaker title agreement is accepted when corroborated by a
matching start time plus host overlap, which is what catches the retitled
repost — its hosts, "AI Engineers - SF" and "San Francisco AI Engineers",
overlap completely once compared as word sets rather than character
sequences. Hence `token_overlap` next to `SequenceMatcher`: platforms reorder
and pad titles freely, and character-level matching is bad at exactly that.

The count didn't change (50 to 49 either way) but the merge it makes is now
the right one. A metric that looks identical while the behaviour underneath
is wrong is a good argument for reading the actual pairs rather than the
summary line.

seen.json was uneventful by comparison. The one design point worth recording:
recording is separate from checking, so a dry run can ask what's new without
marking it sent. Wiring those together would mean a test run silently
emptying that morning's real digest.

## Step 7 — the digest, and seeing the thing for the first time

Formatting and sending are separate functions, which meant the whole
formatter could be tested with no token and no network — worth doing, since
that's where most of what can go wrong lives. Telegram gets HTML rather than
MarkdownV2: MarkdownV2 needs about eighteen characters escaped and these
event titles are full of them ("Founders & Funders", "Kong x AWS Meetup:"),
where HTML needs three and the standard library does it. Verified with a
deliberately hostile title containing a `<script>` tag.

Day headers read "Tonight" and "Tomorrow" before falling back to weekday
names. Small thing, but an ISO date makes you do arithmetic at 8am, which is
exactly when this gets read.

The useful part was seeing real output for the first time. Two things landed
that no amount of planning would have surfaced. Eventbrite publishes dates
with no time at all, so those entries show a venue and no clock — not
fixable from this end, the data isn't in the page. And the unfiltered digest
runs to 49 events, including a poetry night, a 5k sunset run and a chess
club. That's the strongest argument yet for the filter: this is the version
you'd stop opening by Thursday.
