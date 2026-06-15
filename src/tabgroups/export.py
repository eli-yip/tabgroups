#!/usr/bin/env python3
"""
Export tab groups from a Chromium-based browser's on-disk session file.

Reads the browser's SNSS session log directly — no extension or API needed.
Works with Brave, Chrome, Chromium, Edge, and Vivaldi (they share the format).
The browser should ideally be CLOSED (or idle) so the latest session file is
fully flushed; the file is copied before parsing, so it is safe to run while the
browser is open — it just reflects the last flushed state.

Usage:
    uv run tabgroups export                      # files for Brave's Default profile
    uv run tabgroups export --browser chrome     # Chrome instead of Brave
    uv run tabgroups export --format tree        # expand groups in the terminal
    uv run tabgroups export --format md          # only markdown to stdout
    uv run tabgroups export --profile "Profile 1"
    uv run tabgroups export --session /path/to/Session_xxx
    uv run tabgroups export --out-dir ~/Desktop/export --format all

Formats: tree, md, json, html, csv, all. A rich summary table is always printed
to stderr; the chosen format goes to stdout (or files for --format all).
"""

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

from .render import (
    COLORS,
    RICH_STYLE,
    Document,
    Format,
    Group,
    Tab,
    _title,
    emit,
)

# Session command ids we care about (sessions/session_service_commands.cc)
CMD_SET_TAB_WINDOW = 0
CMD_SET_TAB_INDEX = 2
CMD_UPDATE_TAB_NAV = 6
CMD_SET_SELECTED_NAV_INDEX = 7
CMD_TAB_CLOSED = 16
CMD_SET_TAB_GROUP = 25
CMD_SET_TAB_GROUP_METADATA2 = 27


if sys.platform == "darwin":
    _PLAT = "mac"
elif os.name == "nt":
    _PLAT = "win"
else:
    _PLAT = "linux"

# User-data directory for each Chromium-based browser, relative to $HOME.
# They all share the SNSS session format, so the same parser works for all.
BROWSERS = {
    "brave": {
        "mac": "Library/Application Support/BraveSoftware/Brave-Browser",
        "linux": ".config/BraveSoftware/Brave-Browser",
        "win": "AppData/Local/BraveSoftware/Brave-Browser/User Data",
    },
    "chrome": {
        "mac": "Library/Application Support/Google/Chrome",
        "linux": ".config/google-chrome",
        "win": "AppData/Local/Google/Chrome/User Data",
    },
    "chromium": {
        "mac": "Library/Application Support/Chromium",
        "linux": ".config/chromium",
        "win": "AppData/Local/Chromium/User Data",
    },
    "edge": {
        "mac": "Library/Application Support/Microsoft Edge",
        "linux": ".config/microsoft-edge",
        "win": "AppData/Local/Microsoft/Edge/User Data",
    },
    "vivaldi": {
        "mac": "Library/Application Support/Vivaldi",
        "linux": ".config/vivaldi",
        "win": "AppData/Local/Vivaldi/User Data",
    },
}


def user_data_dir(browser: str) -> Path:
    return Path.home() / BROWSERS[browser][_PLAT]


def list_profiles(browser: str) -> list[str]:
    """Profile directory names that actually contain a Sessions/ folder."""
    base = user_data_dir(browser)
    if not base.is_dir():
        return []
    return [e.name for e in sorted(base.iterdir()) if (e / "Sessions").is_dir()]


def default_sessions_dir(browser: str, profile: str) -> Path | None:
    d = user_data_dir(browser) / profile / "Sessions"
    return d if d.is_dir() else None


def newest_session_file(sessions_dir: Path) -> Path | None:
    files = list(sessions_dir.glob("Session_*"))
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


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


def parse(data) -> Document:
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

    result: list[Group] = []
    for tok, tids in groups_by_token.items():
        title, color = gmeta.get(tok, ("", 0))
        tids_sorted = sorted(tids, key=lambda t: tab_index.get(t, 1 << 30))
        tabs: list[Tab] = []
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


# ---------- CLI ----------


class Browser(StrEnum):
    brave = "brave"
    chrome = "chrome"
    chromium = "chromium"
    edge = "edge"
    vivaldi = "vivaldi"


# rich Console for status/summary; goes to stderr so stdout stays pipeable.
err = Console(stderr=True)


def load_session(
    browser: str, profile: str, session: Path | None
) -> tuple[Path, bytes]:
    """Resolve the session file and return (path, raw bytes), copying it first
    so a running browser can't truncate the file mid-read."""
    if session is None:
        sessions_dir = default_sessions_dir(browser, profile)
        if not sessions_dir:
            err.print(
                f"[red]error:[/] no Sessions dir for {browser} profile "
                f"[bold]{profile!r}[/]"
            )
            found = list_profiles(browser)
            if found:
                err.print(f"available profiles: {', '.join(found)}")
            else:
                err.print(
                    f"is {browser} installed? looked under {user_data_dir(browser)}"
                )
            raise typer.Exit(1)
        newest = newest_session_file(sessions_dir)
        if not newest:
            err.print(f"[red]error:[/] no Session_* files in {sessions_dir}")
            raise typer.Exit(1)
        session = newest

    with tempfile.NamedTemporaryFile(delete=False, suffix=".snss") as tmp:
        shutil.copyfile(session, tmp.name)
        tmp_path = tmp.name
    try:
        return session, Path(tmp_path).read_bytes()
    finally:
        Path(tmp_path).unlink()


def print_summary(d: Document, session_path: Path) -> None:
    table = Table(
        title=f"{_title(d)} — {d['group_count']} groups, {d['tab_count']} tabs",
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


def export(
    browser: Annotated[
        Browser, typer.Option("--browser", "-b", help="Chromium-based browser.")
    ] = Browser.brave,
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
    ] = Path("tabgroups"),
) -> None:
    """Export tab groups from a Chromium browser's on-disk session file."""
    session_path, data = load_session(browser, profile, session)
    try:
        d = parse(data)
    except ValueError as e:
        err.print(f"[red]error:[/] {e}")
        raise typer.Exit(1) from e
    d["browser"] = browser.value

    # the tree already shows per-group counts, so skip the summary table for it
    if fmt is not Format.tree:
        print_summary(d, session_path)

    emit(d, fmt, out_dir, "tabgroups", err)
