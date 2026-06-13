# AGENTS.md

`brave-tabgroups` is a small, single-purpose CLI: it parses Brave's on-disk
SNSS session log and exports tab groups to md/json/html/csv. Keep it simple.

## Conventions

- Before writing a commit message, read the recent commit history. Use the
  Conventional Commit style and write commit messages in English.
- **Zero runtime dependencies.** The exporter must run on the Python standard
  library alone — do not add packages to `[project.dependencies]`. Tooling
  (`ruff`, `ty`) belongs in the `dev` dependency group only.
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
