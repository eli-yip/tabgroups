# AGENTS.md

`tabgroups-export` is a small, single-purpose CLI: it parses a Chromium
browser's on-disk SNSS session log and exports tab groups to a terminal tree /
md / json / html / csv. Keep it simple.

## Conventions

- Before writing a commit message, read the recent commit history. Use the
  Conventional Commit style and write commit messages in English.
- **Prefer mature, modern libraries** over hand-rolled code — e.g. `typer` for
  the CLI, `rich` for terminal output. Don't reinvent what a well-maintained
  package does well; add real dependencies to `[project.dependencies]` and keep
  tooling (`ruff`, `ty`) in the `dev` group.
- Target the Python version pinned in `.python-version` / `requires-python`.

## Before committing

Run the lint pipeline and make sure it is clean:

```sh
just lint        # autocorrect + ruff check + ruff format --check + ty check
just fix-lint    # auto-fix formatting and lint issues
```

There is no test suite; verify changes by running the CLI against a real
session file (`just run --format md`).

## Notes

- The SNSS format is an append-only binary log; parsing is best-effort and
  tolerant of unknown commands. When touching `cli.py`, preserve that
  defensive behavior (skip commands that fail to decode rather than aborting).
