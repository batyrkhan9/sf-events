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
