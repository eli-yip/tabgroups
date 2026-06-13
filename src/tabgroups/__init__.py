"""`tabgroups` — read a Chromium browser's tab groups and export or classify them.

A single CLI entry point with subcommands:
- `tabgroups export` — parse the on-disk session file → tree / md / json / html / csv.
- `tabgroups classify` — re-group an export by topic with an LLM (discover + run).
"""

from .cli import app, main

__all__ = ["app", "main"]
