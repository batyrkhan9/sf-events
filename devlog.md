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
