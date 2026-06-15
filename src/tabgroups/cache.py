"""On-disk cache for `classify`'s LLM outputs.

The model's answer for one tab is a pure function of what it sees — the topic
list plus that tab's title and domain — so we cache it keyed by exactly that.
The key deliberately excludes the tab's positional `id` and its `url`: `id` is
reassigned per export (so including it would miss across exports), and `url`
never reaches the model. This is what lets a tab shared between two different
exports hit the cache, as long as the same topics are applied.

Storage is a `diskcache.Cache` (SQLite-backed). The directory depends on how the
tool is run — see `resolve_cache_dir`.

Integrity is unaffected: cached values are only `tab -> topic name`. URLs are
restored from the original export by id and re-checked by `_assert_urls_preserved`
after the cache is consulted, so a cache can never corrupt or drop a link.
"""

import hashlib
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import diskcache
import platformdirs

from .stats import Rate

_APP = "tabgroups"


# ---------- cache directory ----------


def resolve_cache_dir() -> Path:
    """Where the cache lives, by run mode:

    1. `TABGROUPS_CACHE_DIR` if set (highest priority — override / tests).
    2. Source-tree run (package not under site-packages) → `<repo>/.cache/tabgroups`.
    3. Installed CLI → `platformdirs.user_cache_dir` (Linux honours `XDG_CACHE_HOME`,
       macOS uses `~/Library/Caches`).
    """
    env = os.environ.get("TABGROUPS_CACHE_DIR")
    if env:
        return Path(env).expanduser()

    pkg = Path(__file__).resolve()
    installed = any(p in ("site-packages", "dist-packages") for p in pkg.parts)
    if not installed:
        for parent in pkg.parents:
            if (parent / "pyproject.toml").is_file():
                return parent / ".cache" / _APP
    return Path(platformdirs.user_cache_dir(_APP))


class ModelCache:
    """Typed str-in/str-out wrapper over a `diskcache.Cache`.

    diskcache stores arbitrary objects, so its `__getitem__` is typed as a broad
    union. We only ever store `str`, so this wrapper narrows the boundary to
    `str` in one place (a single `isinstance` check) and keeps diskcache from
    leaking into callers.
    """

    def __init__(self, inner: diskcache.Cache) -> None:
        self._cache = inner

    def get(self, key: str) -> str | None:
        value = self._cache.get(key)
        return value if isinstance(value, str) else None

    def put(self, key: str, value: str) -> None:
        self._cache[key] = value

    def __len__(self) -> int:
        # diskcache ships no type stubs, so a strict checker can't see Cache's
        # __len__; it exists at runtime. Ignore just this, as the codebase already
        # does for other stub gaps.
        return len(self._cache)  # pyright: ignore[reportArgumentType]

    def clear(self) -> int:
        """Empty the cache; returns how many entries were removed."""
        n = len(self)
        self._cache.clear()
        return n

    def close(self) -> None:
        self._cache.close()


def open_cache(cache_dir: Path) -> ModelCache:
    cache_dir.mkdir(parents=True, exist_ok=True)
    return ModelCache(diskcache.Cache(str(cache_dir)))


# ---------- key derivation ----------


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def classify_key(
    *,
    prompt: str,
    model: str,
    temperature: float,
    topic_block: str,
    title: str,
    domain: str,
) -> str:
    """Key for one tab's topic assignment. `prompt` is the classify system prompt;
    its hash stands in for a manual prompt-version — changing it invalidates."""
    return _sha256(
        _canonical(
            {
                "kind": "classify",
                "v": _sha256(prompt),
                "model": model,
                "temperature": temperature,
                "topics": topic_block,
                "title": title,
                "domain": domain,
            }
        )
    )


def discover_key(
    *,
    prompt: str,
    model: str,
    temperature: float,
    entries: Iterable[tuple[str, str]],
) -> str:
    """Key for a whole discover call, fingerprinting every entry's (title, domain)
    in order. A different export is a different set, so discover simply re-runs."""
    return _sha256(
        _canonical(
            {
                "kind": "discover",
                "v": _sha256(prompt),
                "model": model,
                "temperature": temperature,
                "entries": [[t, d] for t, d in entries],
            }
        )
    )


# ---------- rate accounting ----------


@dataclass
class CacheStats(Rate):
    """`good` = cache hits, `bad` = misses."""
