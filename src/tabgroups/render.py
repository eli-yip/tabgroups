"""Renderers for a tab-groups *document* — terminal tree, Markdown, HTML, CSV.

A document is the structure the export parser produces and `classify` reuses;
its shape is the `Document` / `Group` / `Tab` TypedDicts below. These renderers
are shared by both the `export` and `classify` subcommands.
"""

import csv
import html
from enum import StrEnum
from typing import NotRequired, TextIO, TypedDict

from rich.console import Console
from rich.text import Text
from rich.tree import Tree


class Tab(TypedDict):
    """One saved tab: its title, URL, and originating window (None when unknown)."""

    title: str
    url: str
    window: int | None


class Group(TypedDict):
    """A named, colored group of tabs. `token` is the SNSS group id, present only
    on exported documents (classify regroups by topic and omits it)."""

    name: str
    color: str
    tabs: list[Tab]
    token: NotRequired[str]


class Document(TypedDict):
    """A whole export/classification: groups plus their roll-up counts. `version`
    (SNSS file version) and `browser` are set by the exporter only."""

    group_count: int
    tab_count: int
    groups: list[Group]
    version: NotRequired[int]
    browser: NotRequired[str]


class Format(StrEnum):
    """Output format shared by the `export` and `classify` subcommands."""

    tree = "tree"
    md = "md"
    json = "json"
    html = "html"
    csv = "csv"
    all = "all"


# Chromium tab_groups::TabGroupColorId order — the canonical color-name space
# shared by the SNSS parser (which decodes the ids), these renderers, and the
# classify palette.
COLORS = {
    0: "grey",
    1: "blue",
    2: "red",
    3: "yellow",
    4: "green",
    5: "pink",
    6: "purple",
    7: "cyan",
    8: "orange",
}

# rich styles for each Chromium group color
RICH_STYLE = {
    "grey": "grey50",
    "blue": "blue",
    "red": "red",
    "yellow": "yellow",
    "green": "green",
    "pink": "magenta",
    "purple": "medium_purple",
    "cyan": "cyan",
    "orange": "dark_orange",
}

# CSS colors for the HTML renderer's chips
_CSS_COLOR = {
    "grey": "#9aa0a6",
    "blue": "#1a73e8",
    "red": "#d93025",
    "yellow": "#f9ab00",
    "green": "#188038",
    "pink": "#d01884",
    "purple": "#9334e6",
    "cyan": "#007b83",
    "orange": "#e8710a",
}


def _title(d: Document) -> str:
    b = d.get("browser")
    return f"{b.capitalize()} Tab Groups" if b else "Tab Groups"


def render_md(d: Document) -> str:
    out = [f"# {_title(d)} — {d['group_count']} groups, {d['tab_count']} tabs\n"]
    for i, g in enumerate(d["groups"], 1):
        name = g["name"] or "(untitled)"
        out.append(f"## {i}. {name}  `[{g['color']}]`  · {len(g['tabs'])} tabs\n")
        for t in g["tabs"]:
            title = t["title"] or "(no title)"
            out.append(f"- [{title}]({t['url']})")
        out.append("")
    return "\n".join(out)


def render_html(d: Document) -> str:
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<title>{_title(d)}</title><style>",
        "body{font:14px/1.5 -apple-system,system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem}",
        "h1{font-size:1.4rem}h2{margin-top:1.6rem;font-size:1.05rem;border-bottom:1px solid #ddd;padding-bottom:.3rem}",
        ".chip{display:inline-block;width:.7em;height:.7em;border-radius:50%;margin-right:.4em;vertical-align:middle}",
        "ul{padding-left:1.2rem}a{text-decoration:none;color:#1a56db}a:hover{text-decoration:underline}",
        ".count{color:#888;font-weight:normal;font-size:.85em}</style></head><body>",
        f"<h1>{_title(d)} — {d['group_count']} groups, {d['tab_count']} tabs</h1>",
    ]
    for i, g in enumerate(d["groups"], 1):
        name = html.escape(g["name"] or "(untitled)")
        col = _CSS_COLOR.get(g["color"], "#9aa0a6")
        parts.append(
            f"<h2><span class='chip' style='background:{col}'></span>{i}. {name} "
            f"<span class='count'>[{g['color']}] · {len(g['tabs'])}</span></h2><ul>"
        )
        for t in g["tabs"]:
            title = html.escape(t["title"] or t["url"])
            url = html.escape(t["url"], quote=True)
            parts.append(f"<li><a href='{url}'>{title}</a></li>")
        parts.append("</ul>")
    parts.append("</body></html>")
    return "".join(parts)


def render_csv(d: Document, fh: TextIO) -> None:
    w = csv.writer(fh)
    w.writerow(["group", "color", "title", "url"])
    for g in d["groups"]:
        for t in g["tabs"]:
            w.writerow([g["name"], g["color"], t["title"], t["url"]])


def render_tree(d: Document, console: Console) -> None:
    root = Tree(
        Text(_title(d), style="bold")
        + Text(f" — {d['group_count']} groups, {d['tab_count']} tabs", style="grey50")
    )
    for i, g in enumerate(d["groups"], 1):
        style = RICH_STYLE.get(g["color"], "white")
        label = (
            Text(f"{i}. ", style="grey50")
            + Text("● ", style=style)
            + Text(g["name"] or "(untitled)", style=style)
            + Text(f"  [{g['color']}] · {len(g['tabs'])}", style="grey50")
        )
        branch = root.add(label)
        for t in g["tabs"]:
            title = t["title"] or "(no title)"
            # clickable title (OSC 8 hyperlink) + dimmed url
            leaf = Text(title, style=f"link {t['url']}") + Text(
                f"  {t['url']}", style="grey39"
            )
            branch.add(leaf)
    console.print(root)
