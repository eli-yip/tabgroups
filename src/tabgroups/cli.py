"""Top-level `tabgroups` command line: wires the feature subcommands into one
entry point. Business logic lives in the feature modules (`export`, `classify`);
this module only assembles them.
"""

import typer

from .classify import app as classify_app
from .export import export

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Read a Chromium browser's tab groups and export or classify them.",
)
app.command()(export)
app.add_typer(classify_app, name="classify")


def main() -> None:
    app()
