"""Load and validate sources.yaml.

A bad config should stop the run. That is different from a source being
down, which the pipeline tolerates later — here, a typo means a source you
think is running silently isn't, and a smaller digest looks exactly like a
quiet day in SF.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

PAGE = "page"
SEARCH = "search"
VALID_TYPES = (PAGE, SEARCH)


class ConfigError(Exception):
    """sources.yaml is malformed. The message names the offending entry."""


@dataclass(frozen=True)
class Source:
    name: str
    type: str
    url: str | None = None
    query: str | None = None

    @property
    def target(self) -> str:
        """What this source points at, whichever kind it is.

        Lets callers log or display a source without branching on type.
        """
        return self.url if self.type == PAGE else self.query


def load_sources(path: str | Path = "sources.yaml") -> list[Source]:
    """Read sources.yaml and return the enabled sources.

    Raises ConfigError if the file is missing, unparseable, or any entry is
    invalid.
    """
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"no config file at {path}") from None
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} is not valid YAML: {e}") from None

    if not isinstance(raw, dict) or "sources" not in raw:
        raise ConfigError(f"{path} must have a top-level 'sources:' list")

    entries = raw["sources"]
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"'sources' in {path} must be a non-empty list")

    sources = []
    seen_names = set()
    for i, entry in enumerate(entries):
        source = _parse_entry(entry, i)
        if source is None:  # disabled
            continue
        # Names identify sources in logs and in the digest, so collisions
        # would make it ambiguous which source an event came from.
        if source.name in seen_names:
            raise ConfigError(f"duplicate source name: {source.name!r}")
        seen_names.add(source.name)
        sources.append(source)

    if not sources:
        raise ConfigError(f"every source in {path} is disabled")

    return sources


def _parse_entry(entry: object, index: int) -> Source | None:
    """Validate one YAML entry. Returns None if the source is disabled."""
    where = f"sources[{index}]"

    if not isinstance(entry, dict):
        raise ConfigError(f"{where} must be a mapping, got {type(entry).__name__}")

    name = entry.get("name")
    if not name:
        raise ConfigError(f"{where} is missing a 'name'")
    if not isinstance(name, str):
        # YAML turns bare on/off/yes/no/true into booleans, so an unquoted
        # name like `name: On` arrives here as True. Say so, or it reads as
        # a missing key that is plainly right there in the file.
        raise ConfigError(
            f"{where} has a non-string 'name' ({name!r}) — quote it in the YAML"
        )
    # Past this point we can name the source instead of its position, which
    # is what you actually want to see in an error message.
    where = f"source {name!r}"

    if not entry.get("enabled", True):
        return None

    type_ = entry.get("type")
    if type_ not in VALID_TYPES:
        raise ConfigError(
            f"{where} has type {type_!r}, expected one of {list(VALID_TYPES)}"
        )

    # Check for typos before checking for missing fields: a misspelled `urls`
    # is really an unknown key, and saying "needs a url" when a url-shaped
    # line is sitting right there sends you looking in the wrong place.
    unknown = set(entry) - {"name", "type", "url", "query", "enabled"}
    if unknown:
        raise ConfigError(f"{where} has unknown keys: {sorted(unknown)}")

    required = "url" if type_ == PAGE else "query"
    value = entry.get(required)
    if not value or not isinstance(value, str):
        raise ConfigError(f"{where} is type '{type_}' so it needs a '{required}'")

    return Source(name=name, type=type_, **{required: value})
