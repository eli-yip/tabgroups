#!/usr/bin/env python3
"""
LLM-based topical classification for an exported tab-groups JSON file.

Two phases:

    tabgroups classify discover tabgroups/tabgroups.json -o topics.toml
    # ...edit topics.toml by hand (rename/merge topics, refine descriptions)...
    tabgroups classify apply tabgroups/tabgroups.json --topics topics.toml -f md

`discover` asks the model to propose a set of English topics from every tab's
title + main domain. `apply` assigns each tab to exactly one of those topics (or
to an `unclassified` bucket) and re-renders the result with the same md/json/
html/csv/tree renderers used by the exporter.

Hallucination guard: the model only ever sees `id + title + domain`. URLs are
restored from the original export by id, and `apply` asserts that the set of URLs
it emits is exactly the set it read — no fabricated or dropped links.

Config (any-llm, OpenAI-compatible endpoint) comes from a TOML file and/or
`TABGROUPS_*` environment variables, env taking precedence:

    # config.toml
    base_url = "https://api.example.com/v1"
    api_key  = "sk-..."          # may also be set via TABGROUPS_API_KEY
    model    = "gpt-4o-mini"
"""

import asyncio
import json
import re
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Any, NamedTuple, cast
from urllib.parse import unquote

import any_llm
import tldextract
import typer
from any_llm.types.completion import ChatCompletion
from pydantic import BaseModel
from rich.console import Console

from .cache import (
    CacheStats,
    ModelCache,
    classify_key,
    discover_key,
    open_cache,
    resolve_cache_dir,
)
from .config import LLMSettings, load_settings
from .models import AssignmentList, Entry, Topic, TopicList
from .render import (
    COLORS,
    Document,
    Format,
    Group,
    emit,
)

err = Console(stderr=True)

UNCLASSIFIED = "unclassified"
NOISE = "noise"

# Colors cycled through for topics in the rendered output (skip grey, which is
# reserved for the unclassified/noise buckets).
_PALETTE = [c for c in COLORS.values() if c != "grey"]

# Offline TLD extractor: use the bundled public-suffix snapshot, never the
# network, so domain extraction is deterministic and works without internet.
_extract = tldextract.TLDExtract(suffix_list_urls=())

# Proxy/archive URL shapes that wrap a real target URL after a marker; we unwrap
# them so e.g. rss-zero.darkeli.com/api/v1/archive/https://zhihu.com/... is
# classified by its true source (zhihu.com), not the proxy host.
_ARCHIVE_MARKERS = ("/api/v1/archive/",)

# Normalize a scheme separator to exactly "://": some exports mangle it to a
# single slash ("https:/host"), others leave the normal "https://".
_SCHEME_SEP = re.compile(r"(https?):/+")

# Fixed knobs — deliberately NOT user config. config.toml only carries the three
# credentials (base_url / api_key / model); these are sensible constants.
_PROVIDER = "openai"  # any-llm provider for the OpenAI-compatible endpoint
_TEMPERATURE = 0.0  # deterministic classification
_BATCH_SIZE = 30  # tabs per classification request
_MAX_RETRIES = 3  # per-call retries before falling back / giving up


# ---------- entries / preprocessing ----------


def _unwrap(url: str) -> str:
    """Unwrap an archive/proxy URL to the real target it wraps, if any."""
    for marker in _ARCHIVE_MARKERS:
        i = url.find(marker)
        if i != -1:
            inner = unquote(url[i + len(marker) :])
            return _SCHEME_SEP.sub(r"\1://", inner) or url
    return url


def main_domain(url: str, *, unwrap: bool = True) -> str:
    """Registrable domain (eTLD+1) of `url`, optionally unwrapping proxies."""
    target = _unwrap(url) if unwrap else url
    ext = _extract(target)
    return ".".join(p for p in (ext.domain, ext.suffix) if p) or target


def _is_noise(title: str, url: str) -> bool:
    if url.startswith(("chrome://", "edge://", "about:", "brave://")):
        return True
    if not url.strip():
        return True
    return False


class LoadedEntries(NamedTuple):
    """A read export, with every tab sorted into exactly one of three buckets."""

    classifiable: list[Entry]  # first-seen real tabs, to be classified
    noise: list[Entry]  # first-seen but junk (chrome://-style or empty URL)
    duplicates: int  # tabs dropped because their URL was already seen

    @property
    def total(self) -> int:
        """Tabs seen before dedup — the three buckets sum back to this."""
        return len(self.classifiable) + len(self.noise) + self.duplicates


def load_entries(export_json: Path, *, unwrap: bool = True) -> LoadedEntries:
    """Read the export and sort each tab into one of three buckets: a real tab to
    classify, noise, or a duplicate of an already-seen URL (dropped).

    Ids are assigned in first-seen order over the kept tabs, so the same input
    yields the same ids every run.
    """
    try:
        d = json.loads(export_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        err.print(f"[red]error:[/] cannot read export JSON {export_json}: {e}")
        raise typer.Exit(1) from e

    seen: set[str] = set()
    classifiable: list[Entry] = []
    noise: list[Entry] = []
    duplicates = 0
    for group in d.get("groups", []):
        for tab in group.get("tabs", []):
            url = tab.get("url", "")
            if url in seen:
                duplicates += 1  # same URL as an earlier tab — drop it
                continue
            seen.add(url)
            title = (tab.get("title") or "").strip()
            entry = Entry(
                id=len(classifiable) + len(noise),
                title=title,
                url=url,
                domain=main_domain(url, unwrap=unwrap),
            )
            (noise if _is_noise(title, url) else classifiable).append(entry)
    return LoadedEntries(classifiable, noise, duplicates)


# ---------- LLM plumbing ----------


def _strip_json(text: str) -> str:
    """Best-effort extraction of a JSON object from a model reply."""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if "```" in s[3:] else s[3:]
        s = s.removeprefix("json").strip().removesuffix("```").strip()
    start, end = s.find("{"), s.rfind("}")
    return s[start : end + 1] if start != -1 and end != -1 else s


async def _acomplete[T: BaseModel](
    settings: LLMSettings, messages: list[dict[str, Any]], schema: type[T]
) -> T:
    """Call the model and return a validated `schema` instance.

    Tries native structured output (response_format=pydantic), then falls back
    to json_object mode + manual validation for endpoints that lack strict
    json_schema support. Retries up to `_MAX_RETRIES`; raises on final failure.

    Async so a whole run shares one event loop: each command drives its calls
    under a single `asyncio.run(...)`, so the underlying HTTP client is created
    and torn down once instead of per call — which is what produced spurious
    "Event loop is closed" noise across sequential synchronous calls.
    """

    async def _raw(response_format: Any) -> ChatCompletion:
        # cast(Any, messages): any-llm types messages invariantly, so a plain
        # list[dict] needs a boundary cast. We never stream, so the result is a
        # ChatCompletion, not the streaming-iterator half of the return union.
        return cast(
            ChatCompletion,
            await any_llm.acompletion(
                model=settings.model,
                provider=_PROVIDER,
                api_base=settings.base_url,
                api_key=settings.api_key,
                temperature=_TEMPERATURE,
                messages=cast(Any, messages),
                response_format=response_format,
            ),
        )

    last: Exception | None = None
    for _ in range(_MAX_RETRIES):
        try:
            resp = await _raw(schema)
            parsed = getattr(resp.choices[0].message, "parsed", None)
            if parsed is not None:
                return parsed
        except Exception as e:  # endpoint may reject pydantic response_format
            last = e
        try:
            resp = await _raw({"type": "json_object"})
            content = resp.choices[0].message.content or ""
            return schema.model_validate_json(_strip_json(content))
        except Exception as e:
            last = e
    raise last if last else RuntimeError("LLM call failed")


# ---------- phase 1: discover ----------


_DISCOVER_SYS = (
    "You are a meticulous librarian organizing a person's saved browser tabs. "
    "Propose a concise set of topical categories that together cover all the "
    "tabs. Aim for 8-16 topics: specific enough to be useful, broad enough that "
    "most tabs fit one. Every topic name and description MUST be in English. "
    'Return JSON: {"topics": [{"name": str, "description": str}]}, where '
    "description is a one-line rule for what belongs in the topic."
)


async def discover_topics(
    settings: LLMSettings, entries: Iterable[Entry], cache: ModelCache | None = None
) -> tuple[list[Topic], bool]:
    """Propose topics; returns (topics, cache_hit). The whole call is cached by a
    fingerprint of every entry's (title, domain)."""
    entries = list(entries)
    # Always derivable (a cheap hash of every entry's title+domain); only its
    # use is gated on the cache, so each call site needs a single guard.
    key = discover_key(
        prompt=_DISCOVER_SYS,
        model=settings.model,
        temperature=_TEMPERATURE,
        entries=[(e.title, e.domain) for e in entries],
    )
    if cache is not None and (cached := cache.get(key)) is not None:
        return TopicList.model_validate_json(cached).topics, True

    lines = [f"- {e.title or '(no title)'}  [{e.domain}]" for e in entries]
    user = "Here are the tabs (title [domain]):\n\n" + "\n".join(lines)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _DISCOVER_SYS},
        {"role": "user", "content": user},
    ]
    result = await _acomplete(settings, messages, TopicList)
    if cache is not None:
        cache.put(key, result.model_dump_json())
    return result.topics, False


def _toml_str(s: str) -> str:
    body = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{body}"'


def write_topics_toml(topics: list[Topic], path: Path) -> None:
    out = [
        "# Topics proposed by the model. Edit freely before `classify apply`:",
        "#   rename / merge / delete topics, and refine each description (the",
        "#   description is the rule the model uses to assign tabs).",
        "",
    ]
    for t in topics:
        out.append("[[topic]]")
        out.append(f"name = {_toml_str(t.name)}")
        out.append(f"description = {_toml_str(t.description)}")
        out.append("")
    path.write_text("\n".join(out), encoding="utf-8")


def load_topics_toml(path: Path) -> list[Topic]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        err.print(f"[red]error:[/] cannot read topics file {path}: {e}")
        raise typer.Exit(1) from e
    topics = [Topic(**t) for t in data.get("topic", [])]
    if not topics:
        err.print(f"[red]error:[/] no [[topic]] entries in {path}")
        raise typer.Exit(1)
    return topics


# ---------- phase 2: classify ----------


_CLASSIFY_SYS = (
    "You assign saved browser tabs to topics. You are given a numbered list of "
    "topics and a batch of tabs (each with an id, title and domain). For EVERY "
    "tab, choose the single best-fitting topic by its exact name. If no topic "
    'fits well, use the topic name "unclassified". Do not invent topic names. '
    'Return JSON: {"assignments": [{"id": int, "topic": str}]}, one entry '
    "per tab id given."
)


def _batches[T](items: list[T], size: int) -> Iterable[list[T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


async def classify_entries(
    settings: LLMSettings,
    entries: list[Entry],
    topics: list[Topic],
    cache: ModelCache | None = None,
) -> tuple[dict[int, str], CacheStats]:
    """Map entry id -> topic name (or UNCLASSIFIED), reusing cached per-tab
    assignments. Returns (assignments, stats). Defensive: a failed batch or any
    unrecognized/missing id falls back to UNCLASSIFIED, never aborts.

    Every explicit per-tab decision from the model is cached — including a
    deliberate UNCLASSIFIED. Only the structural fallback for ids the model
    skipped or whose batch failed is left uncached, so a transient failure can't
    poison a tab's cached result."""
    valid = {t.name for t in topics}
    topic_block = "\n".join(
        f"{i}. {t.name} — {t.description}" for i, t in enumerate(topics, 1)
    )

    def key_for(e: Entry) -> str:
        return classify_key(
            prompt=_CLASSIFY_SYS,
            model=settings.model,
            temperature=_TEMPERATURE,
            topic_block=topic_block,
            title=e.title,
            domain=e.domain,
        )

    async def classify_batch(batch: list[Entry], label: str) -> dict[int, str]:
        """Classify one batch; return {id -> topic} for every id in it — the
        model's choice (normalized to a known topic or UNCLASSIFIED) where it
        answered, else the UNCLASSIFIED fallback. Genuine answers are cached; the
        fallback never is, so a transient batch failure can't freeze a tab."""
        err.print(f"[grey50]classifying batch {label} ({len(batch)} tabs)...[/]")
        tab_block = "\n".join(
            f"[{e.id}] {e.title or '(no title)'}  ({e.domain})" for e in batch
        )
        user = (
            f"Topics:\n{topic_block}\n\n"
            f"Tabs to classify:\n{tab_block}\n\n"
            "Return JSON assignments for every id above."
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _CLASSIFY_SYS},
            {"role": "user", "content": user},
        ]
        by_id = {e.id: e for e in batch}
        out: dict[int, str] = {}
        try:
            result = await _acomplete(settings, messages, AssignmentList)
            for a in result.assignments:
                if a.id not in by_id:
                    continue  # ignore ids we didn't ask about in this batch
                # an unknown topic name normalizes to unclassified; a deliberate
                # "unclassified" is a real model decision, not a failure.
                topic = a.topic if a.topic in valid else UNCLASSIFIED
                out[a.id] = topic
                if cache is not None:  # cache every explicit per-tab decision
                    cache.put(key_for(by_id[a.id]), topic)
        except Exception as exc:
            err.print(
                f"[yellow]warn:[/] batch {label} failed ({exc}); "
                "marking its tabs unclassified"
            )
        # any id the model skipped or whose batch failed -> unclassified, and
        # NOT cached, so a transient miss can never be frozen onto a tab.
        for e in batch:
            out.setdefault(e.id, UNCLASSIFIED)
        return out

    assigned: dict[int, str] = {}
    stats = CacheStats()

    # Pre-scan: serve cache hits directly, leave only misses for the model.
    pending: list[Entry] = []
    for e in entries:
        cached = cache.get(key_for(e)) if cache is not None else None
        if cached is not None:
            assigned[e.id] = cached
            stats.hits += 1
        else:
            pending.append(e)
            stats.misses += 1

    batches = list(_batches(pending, _BATCH_SIZE))
    for n, batch in enumerate(batches, 1):
        assigned.update(await classify_batch(batch, f"{n}/{len(batches)}"))
    return assigned, stats


# ---------- assemble + render ----------


def build_document(
    entries: list[Entry],
    noise: list[Entry],
    topics: list[Topic],
    assignments: dict[int, str],
) -> Document:
    """Shape results into the exporter's {groups:[{name,color,tabs}]} document,
    grouped by topic, so the existing renderers can be reused as-is.

    Tabs are bucketed in original entry order (not assignments' dict order), so
    the rendered output is identical regardless of how many assignments came from
    the cache versus a fresh model call."""
    buckets: dict[str, list[Entry]] = {t.name: [] for t in topics}
    buckets[UNCLASSIFIED] = []
    for e in entries:
        topic = assignments.get(e.id, UNCLASSIFIED)
        buckets.setdefault(topic, []).append(e)

    ordered = [t.name for t in topics] + [UNCLASSIFIED]
    groups: list[Group] = []
    color_i = 0
    for name in ordered:
        tabs = buckets.get(name, [])
        if not tabs and name == UNCLASSIFIED:
            continue
        if name in (UNCLASSIFIED, NOISE):
            color = "grey"
        else:
            color = _PALETTE[color_i % len(_PALETTE)]
            color_i += 1
        groups.append(
            {
                "name": name,
                "color": color,
                "tabs": [
                    {"title": e.title, "url": e.url, "window": None} for e in tabs
                ],
            }
        )
    if noise:
        groups.append(
            {
                "name": NOISE,
                "color": "grey",
                "tabs": [
                    {"title": e.title, "url": e.url, "window": None} for e in noise
                ],
            }
        )
    return {
        "group_count": len(groups),
        "tab_count": sum(len(g["tabs"]) for g in groups),
        "groups": groups,
    }


def _assert_urls_preserved(
    document: Document, entries: list[Entry], noise: list[Entry]
) -> None:
    """Hard guarantee: every input URL appears exactly once in the output, and
    no URL was fabricated. Aborts loudly if violated."""
    expected = {e.url for e in entries} | {e.url for e in noise}
    out = [t["url"] for g in document["groups"] for t in g["tabs"]]
    out_set = set(out)
    if out_set != expected or len(out) != len(expected):
        missing = expected - out_set
        extra = out_set - expected
        err.print(
            f"[red]integrity check FAILED[/] — "
            f"expected {len(expected)} unique URLs, emitted {len(out)} "
            f"({len(out_set)} unique); missing={len(missing)} extra={len(extra)}"
        )
        for u in list(extra)[:5]:
            err.print(f"  [red]fabricated:[/] {u}")
        raise typer.Exit(2)


# ---------- CLI ----------

app = typer.Typer(
    no_args_is_help=True,
    help="LLM topical classification of an exported tab-groups JSON file.",
    add_completion=False,
)

_ConfigOpt = Annotated[
    Path,
    typer.Option(
        "--config", "-c", help="TOML config file (TABGROUPS_* env overrides)."
    ),
]
_UnwrapOpt = Annotated[
    bool,
    typer.Option(help="Unwrap archive/proxy URLs to their real source domain."),
]
_NoCacheOpt = Annotated[
    bool,
    typer.Option("--no-cache", help="Bypass the model-output cache for this run."),
]


def _get_cache(no_cache: bool) -> ModelCache | None:
    """Open the cache unless disabled. Returns None when --no-cache is set."""
    return None if no_cache else open_cache(resolve_cache_dir())


@app.command()
def discover(
    export_json: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, help="Exported tabgroups.json."),
    ],
    out: Annotated[
        Path,
        typer.Option("--out", "-o", help="Where to write the editable topics TOML."),
    ] = Path("topics.toml"),
    config: _ConfigOpt = Path("config.toml"),
    unwrap: _UnwrapOpt = True,
    no_cache: _NoCacheOpt = False,
) -> None:
    """Propose an editable topic list from the exported tabs."""
    settings = load_settings(config)
    cache = _get_cache(no_cache)
    loaded = load_entries(export_json, unwrap=unwrap)
    entries = loaded.classifiable
    err.print(
        f"[grey50]{loaded.total} tabs, {len(entries)} unique to classify "
        f"({len(loaded.noise)} noise, {loaded.duplicates} duplicates)[/]"
    )
    topics, hit = asyncio.run(discover_topics(settings, entries, cache))
    if cache is None:
        err.print("[grey50]cache: disabled[/]")
    else:
        status = "hit" if hit else "miss"
        err.print(f"[grey50]cache: {status} · {resolve_cache_dir()}[/]")
    write_topics_toml(topics, out)
    err.print(
        f"[green]wrote[/] {len(topics)} topics to [bold]{out}[/] — edit, then run:"
    )
    err.print(f"  tabgroups classify apply {export_json} --topics {out}")


@app.command()
def apply(
    export_json: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, help="Exported tabgroups.json."),
    ],
    topics_file: Annotated[
        Path,
        typer.Option(
            "--topics", "-t", exists=True, dir_okay=False, help="topics.toml."
        ),
    ] = Path("topics.toml"),
    fmt: Annotated[
        Format,
        typer.Option(
            "--format",
            "-f",
            help="Output format; 'tree' draws a terminal tree, 'all' writes files.",
        ),
    ] = Format.all,
    out_dir: Annotated[
        Path, typer.Option(help="Output directory when --format all.")
    ] = Path("classified"),
    config: _ConfigOpt = Path("config.toml"),
    unwrap: _UnwrapOpt = True,
    no_cache: _NoCacheOpt = False,
) -> None:
    """Classify tabs against an (edited) topic list and render the result."""
    settings = load_settings(config)
    cache = _get_cache(no_cache)
    topics = load_topics_toml(topics_file)
    loaded = load_entries(export_json, unwrap=unwrap)
    entries, noise = loaded.classifiable, loaded.noise
    err.print(
        f"[grey50]{loaded.total} tabs → {len(entries)} unique, {len(topics)} topics "
        f"({len(noise)} noise, {loaded.duplicates} duplicates)[/]"
    )
    assignments, stats = asyncio.run(classify_entries(settings, entries, topics, cache))
    document = build_document(entries, noise, topics, assignments)
    _assert_urls_preserved(document, entries, noise)

    counts = {g["name"]: len(g["tabs"]) for g in document["groups"]}
    for name, c in counts.items():
        err.print(f"  [bold]{c:>4}[/]  {name}")
    err.print(f"[green]✓ {len(entries) + len(noise)} URLs preserved, 0 fabricated[/]")
    if cache is None:
        err.print("[grey50]cache: disabled[/]")
    else:
        err.print(
            f"[grey50]cache: {stats.hits}/{stats.total} hit "
            f"({stats.rate():.0%}) · {resolve_cache_dir()}[/]"
        )
    emit(document, fmt, out_dir, "classified", err)


cache_app = typer.Typer(
    no_args_is_help=True, help="Manage the model-output cache.", add_completion=False
)
app.add_typer(cache_app, name="cache")


@cache_app.command("clear")
def cache_clear() -> None:
    """Empty the model-output cache."""
    cache_dir = resolve_cache_dir()
    cache = open_cache(cache_dir)
    n = cache.clear()
    cache.close()
    err.print(f"[green]cleared[/] {n} cached entries from [bold]{cache_dir}[/]")
