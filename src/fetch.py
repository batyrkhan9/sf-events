"""Fetch a page and strip its HTML down to visible text.

Returns both the raw HTML and the cleaned text. The HTML is what step 4 mines
for embedded JSON-LD (free, exact); the text is the fallback for sources that
don't publish structured data.

A dead source raises FetchError rather than returning empty text — the pipeline
has to be able to tell "this site is down" from "this site had no events
today", and an empty string looks identical to both.
"""

from dataclasses import dataclass
from pathlib import Path
import re

import requests
from bs4 import BeautifulSoup

from src.sources import Source

# Plenty of sites 403 Python's default User-Agent. This is not an attempt to
# hide what we are — it's the minimum needed to get the same HTML a browser
# would see.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

TIMEOUT_SECONDS = 20

# Tags that never contain event listings. Dropping them before text extraction
# removes most of the noise that would otherwise dominate the output.
BOILERPLATE_TAGS = ("script", "style", "nav", "header", "footer", "noscript", "svg")

# Cap on the visible text we keep. Only matters for the fallback path — a page
# with JSON-LD never gets this far. Event listings sit near the top of these
# pages; the tail is navigation and footer sludge.
MAX_TEXT_CHARS = 50_000


class FetchError(Exception):
    """A page could not be retrieved. Carries the source name for logging."""


@dataclass
class FetchedPage:
    source: Source
    html: str
    text: str

    @property
    def truncated(self) -> bool:
        return len(self.text) >= MAX_TEXT_CHARS


def fetch_page(source: Source, timeout: int = TIMEOUT_SECONDS) -> FetchedPage:
    """GET a page source and return its HTML plus cleaned visible text."""
    if source.type != "page":
        raise FetchError(f"{source.name} is type {source.type!r}, not a page")

    try:
        response = requests.get(
            source.url,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.HTTPError as e:
        raise FetchError(f"{source.name}: HTTP {e.response.status_code}") from None
    except requests.Timeout:
        raise FetchError(f"{source.name}: timed out after {timeout}s") from None
    except requests.RequestException as e:
        raise FetchError(f"{source.name}: {e.__class__.__name__}") from None

    return FetchedPage(source=source, html=response.text, text=html_to_text(response.text))


def html_to_text(html: str) -> str:
    """Strip HTML to the text a reader would actually see."""
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(BOILERPLATE_TAGS):
        tag.decompose()

    # Newline separator keeps events on separate lines instead of running the
    # whole page together into one unreadable paragraph.
    text = soup.get_text(separator="\n")

    # HTML-to-text always leaves long runs of blank lines where the markup was.
    lines = (line.strip() for line in text.splitlines())
    text = "\n".join(line for line in lines if line)

    if len(text) > MAX_TEXT_CHARS:
        # Cut at a line boundary so we never hand step 4 half an event.
        text = text[:MAX_TEXT_CHARS].rsplit("\n", 1)[0]

    return text


def save_raw(page: FetchedPage, directory: str | Path = "raw") -> Path:
    """Write a fetched page to disk for offline development.

    Steps 4-6 cost money (or at least time) per run. Saving pages once means
    the parser and filter can be iterated against fixed input instead of
    re-fetching every site on every attempt.
    """
    directory = Path(directory)
    directory.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", page.source.name.lower()).strip("-")
    path = directory / f"{slug}.html"
    path.write_text(page.html)
    return path


if __name__ == "__main__":
    # python -m src.fetch — fetch every enabled page source and save it.
    from src.sources import load_sources

    for source in load_sources():
        if source.type != "page":
            continue
        try:
            page = fetch_page(source)
        except FetchError as e:
            print(f"FAILED  {e}")
            continue
        path = save_raw(page)
        note = " (truncated)" if page.truncated else ""
        print(
            f"ok      {source.name:20} {len(page.html):>8,} html "
            f"{len(page.text):>7,} text{note}  -> {path}"
        )
