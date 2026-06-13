#!/usr/bin/env python3
"""
Export all Brave (Chromium) tab groups from the on-disk session file.

Reads Brave's SNSS session log directly — no need for Brave to expose anything,
but Brave should ideally be CLOSED (or at least idle) so the latest session file
is fully flushed. The script copies the session file before parsing, so it is
safe to run while Brave is open; it just reflects the last flushed state.

Usage:
    uv run brave-tabgroups                      # md + json + html + csv into ./brave_tabgroups/
    uv run brave-tabgroups --format md          # only markdown to stdout
    uv run brave-tabgroups --profile "Profile 1"
    uv run brave-tabgroups --session /path/to/Session_xxx
    uv run brave-tabgroups --out-dir ~/Desktop/export --format all

Formats: md, json, html, csv, all. A rich summary table is always printed to
stderr; the chosen format goes to stdout (or files for --format all).
"""

import csv
import glob
import html
import json
import os
import shutil
import struct
import sys
import tempfile
from collections import defaultdict
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

# Chromium tab_groups::TabGroupColorId order
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

# Session command ids we care about (sessions/session_service_commands.cc)
CMD_SET_TAB_WINDOW = 0
CMD_SET_TAB_INDEX = 2
CMD_UPDATE_TAB_NAV = 6
CMD_SET_SELECTED_NAV_INDEX = 7
CMD_TAB_CLOSED = 16
CMD_SET_TAB_GROUP = 25
CMD_SET_TAB_GROUP_METADATA2 = 27


def default_sessions_dir(profile):
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(
            home,
            "Library/Application Support/BraveSoftware/Brave-Browser",
            profile,
            "Sessions",
        ),  # macOS
        os.path.join(
            home, ".config/BraveSoftware/Brave-Browser", profile, "Sessions"
        ),  # Linux
        os.path.join(
            home,
            "AppData/Local/BraveSoftware/Brave-Browser/User Data",
            profile,
            "Sessions",
        ),  # Windows
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


def newest_session_file(sessions_dir):
    files = glob.glob(os.path.join(sessions_dir, "Session_*"))
    if not files:
        return None
    return max(files, key=os.path.getmtime)


class Pickle:
    """Reader for a Chromium base::Pickle (optionally with its 4-byte size header)."""

    def __init__(self, b):
        if len(b) >= 4 and struct.unpack("<I", b[:4])[0] == len(b) - 4:
            self.d = b[4:]
        else:
            self.d = b
        self.o = 0

    def remaining(self):
        return len(self.d) - self.o

    def _align(self):
        self.o = (self.o + 3) & ~3

    def i32(self):
        v = struct.unpack("<i", self.d[self.o : self.o + 4])[0]
        self.o += 4
        return v

    def u32(self):
        v = struct.unpack("<I", self.d[self.o : self.o + 4])[0]
        self.o += 4
        return v

    def u64(self):
        v = struct.unpack("<Q", self.d[self.o : self.o + 8])[0]
        self.o += 8
        return v

    def str8(self):
        n = self.u32()
        if n < 0 or self.o + n > len(self.d):
            raise ValueError("str8 overflow")
        v = self.d[self.o : self.o + n].decode("utf-8", "replace")
        self.o += n
        self._align()
        return v

    def str16(self):
        n = self.u32()
        b = 2 * n
        if b < 0 or self.o + b > len(self.d):
            raise ValueError("str16 overflow")
        v = self.d[self.o : self.o + b].decode("utf-16-le", "replace")
        self.o += b
        self._align()
        return v


def read_commands(data):
    if data[:4] != b"SNSS":
        raise ValueError("not an SNSS file")
    version = struct.unpack("<i", data[4:8])[0]
    pos = 8
    cmds = []
    while pos + 2 <= len(data):
        size = struct.unpack("<H", data[pos : pos + 2])[0]
        pos += 2
        if size == 0 or pos + size > len(data):
            break
        cid = data[pos]
        content = data[pos + 1 : pos + size]
        pos += size
        cmds.append((cid, content))
    return version, cmds


def parse(data):
    version, cmds = read_commands(data)
    gmeta = {}  # token -> (title, color)
    tab_group = {}  # tab_id -> token
    navs = defaultdict(dict)  # tab_id -> {index: (url, title)}
    selected = {}  # tab_id -> selected nav index
    tab_index = {}  # tab_id -> index within window
    tab_window = {}  # tab_id -> window id
    closed = set()

    for cid, c in cmds:
        try:
            p = Pickle(c)
            if cid == CMD_SET_TAB_GROUP_METADATA2:
                hi = p.u64()
                lo = p.u64()
                title = p.str16()
                color = p.u32()
                gmeta[(hi, lo)] = (title, color)
            elif cid == CMD_SET_TAB_GROUP:
                tid = p.i32()
                p.i32()  # tid, reserved
                hi = p.u64()
                lo = p.u64()
                tab_group[tid] = (hi, lo)
            elif cid == CMD_UPDATE_TAB_NAV:
                tid = p.i32()
                idx = p.i32()
                url = p.str8()
                title = p.str16()
                navs[tid][idx] = (url, title)
            elif cid == CMD_SET_SELECTED_NAV_INDEX:
                tid = p.i32()
                selected[tid] = p.i32()
            elif cid == CMD_SET_TAB_WINDOW:
                w = p.i32()
                t = p.i32()
                tab_window[t] = w
            elif cid == CMD_SET_TAB_INDEX:
                t = p.i32()
                tab_index[t] = p.i32()
            elif cid == CMD_TAB_CLOSED:
                closed.add(p.i32())
        except Exception:
            pass  # tolerate malformed/foreign commands

    def current_nav(tid):
        n = navs.get(tid, {})
        if not n:
            return None
        si = selected.get(tid)
        return n.get(si, n[max(n)])

    groups_by_token = defaultdict(list)
    for tid, tok in tab_group.items():
        if tid in closed:
            continue
        groups_by_token[tok].append(tid)

    result = []
    for tok, tids in groups_by_token.items():
        title, color = gmeta.get(tok, ("", 0))
        tids_sorted = sorted(tids, key=lambda t: tab_index.get(t, 1 << 30))
        tabs = []
        for t in tids_sorted:
            cn = current_nav(t)
            if cn is None:
                continue
            url, ttl = cn
            tabs.append({"title": ttl.strip(), "url": url, "window": tab_window.get(t)})
        result.append(
            {
                "name": title.strip(),
                "color": COLORS.get(color, str(color)),
                "token": f"{tok[0]:016x}{tok[1]:016x}",
                "tabs": tabs,
            }
        )
    # order groups by first tab's position
    result.sort(
        key=lambda g: min((t.get("window") or 0) for t in g["tabs"]) if g["tabs"] else 0
    )
    return {
        "version": version,
        "group_count": len(result),
        "tab_count": sum(len(g["tabs"]) for g in result),
        "groups": result,
    }


# ---------- renderers ----------


def render_md(d):
    out = [f"# Brave Tab Groups — {d['group_count']} groups, {d['tab_count']} tabs\n"]
    for i, g in enumerate(d["groups"], 1):
        name = g["name"] or "(untitled)"
        out.append(f"## {i}. {name}  `[{g['color']}]`  · {len(g['tabs'])} tabs\n")
        for t in g["tabs"]:
            title = t["title"] or "(no title)"
            out.append(f"- [{title}]({t['url']})")
        out.append("")
    return "\n".join(out)


def render_html(d):
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        "<title>Brave Tab Groups</title><style>",
        "body{font:14px/1.5 -apple-system,system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem}",
        "h1{font-size:1.4rem}h2{margin-top:1.6rem;font-size:1.05rem;border-bottom:1px solid #ddd;padding-bottom:.3rem}",
        ".chip{display:inline-block;width:.7em;height:.7em;border-radius:50%;margin-right:.4em;vertical-align:middle}",
        "ul{padding-left:1.2rem}a{text-decoration:none;color:#1a56db}a:hover{text-decoration:underline}",
        ".count{color:#888;font-weight:normal;font-size:.85em}</style></head><body>",
        f"<h1>Brave Tab Groups — {d['group_count']} groups, {d['tab_count']} tabs</h1>",
    ]
    css_color = {
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
    for i, g in enumerate(d["groups"], 1):
        name = html.escape(g["name"] or "(untitled)")
        col = css_color.get(g["color"], "#9aa0a6")
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


def render_csv(d, fh):
    w = csv.writer(fh)
    w.writerow(["group", "color", "title", "url"])
    for g in d["groups"]:
        for t in g["tabs"]:
            w.writerow([g["name"], g["color"], t["title"], t["url"]])


def render_tree(d, console):
    root = Tree(
        Text("Brave Tab Groups", style="bold")
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


# ---------- CLI ----------

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


class Format(StrEnum):
    tree = "tree"
    md = "md"
    json = "json"
    html = "html"
    csv = "csv"
    all = "all"


# rich Console for status/summary; goes to stderr so stdout stays pipeable.
err = Console(stderr=True)


def load_session(profile: str, session: Path | None) -> tuple[Path, bytes]:
    """Resolve the session file and return (path, raw bytes), copying it first
    so a running Brave can't truncate the file mid-read."""
    if session is None:
        sessions_dir = default_sessions_dir(profile)
        if not sessions_dir:
            err.print(
                f"[red]error:[/] could not find Brave Sessions dir for profile "
                f"[bold]{profile!r}[/]"
            )
            raise typer.Exit(1)
        newest = newest_session_file(sessions_dir)
        if not newest:
            err.print(f"[red]error:[/] no Session_* files in {sessions_dir}")
            raise typer.Exit(1)
        session = Path(newest)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".snss") as tmp:
        shutil.copyfile(session, tmp.name)
        tmp_path = tmp.name
    try:
        return session, Path(tmp_path).read_bytes()
    finally:
        os.unlink(tmp_path)


def print_summary(d: dict, session_path: Path) -> None:
    table = Table(
        title=f"Brave Tab Groups — {d['group_count']} groups, {d['tab_count']} tabs",
        title_style="bold",
        header_style="bold",
        border_style="grey39",
    )
    table.add_column("#", justify="right", style="grey50")
    table.add_column("group")
    table.add_column("color")
    table.add_column("tabs", justify="right")
    for i, g in enumerate(d["groups"], 1):
        style = RICH_STYLE.get(g["color"], "white")
        name = Text(g["name"] or "(untitled)", style=style)
        chip = Text("● ", style=style) + Text(g["color"], style="grey50")
        table.add_row(str(i), name, chip, str(len(g["tabs"])))
    err.print(table)
    err.print(f"[grey50]source:[/] {session_path}")


def write_all(d: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "tabgroups.md").write_text(render_md(d), encoding="utf-8")
    (out_dir / "tabgroups.json").write_text(
        json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "tabgroups.html").write_text(render_html(d), encoding="utf-8")
    with (out_dir / "tabgroups.csv").open("w", encoding="utf-8", newline="") as f:
        render_csv(d, f)
    err.print(f"[green]wrote[/] md/json/html/csv into [bold]{out_dir}/[/]")


def export(
    profile: Annotated[str, typer.Option(help="Profile directory name.")] = "Default",
    session: Annotated[
        Path | None,
        typer.Option(
            exists=True,
            dir_okay=False,
            help="Explicit path to a Session_* file (default: newest in the profile).",
        ),
    ] = None,
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
    ] = Path("brave_tabgroups"),
) -> None:
    """Export Brave tab groups from the on-disk session file."""
    session_path, data = load_session(profile, session)
    try:
        d = parse(data)
    except ValueError as e:
        err.print(f"[red]error:[/] {e}")
        raise typer.Exit(1) from e

    # the tree already shows per-group counts, so skip the summary table for it
    if fmt is not Format.tree:
        print_summary(d, session_path)

    if fmt is Format.tree:
        render_tree(d, Console())
    elif fmt is Format.all:
        write_all(d, out_dir)
    elif fmt is Format.json:
        json.dump(d, sys.stdout, ensure_ascii=False, indent=2)
    elif fmt is Format.csv:
        render_csv(d, sys.stdout)
    elif fmt is Format.md:
        sys.stdout.write(render_md(d))
    elif fmt is Format.html:
        sys.stdout.write(render_html(d))


def main() -> None:
    typer.run(export)


if __name__ == "__main__":
    main()
