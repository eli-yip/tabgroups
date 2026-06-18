# AGENTS.md

`tabgroups` is a small CLI toolkit built around one job: read a Chromium
browser's on-disk SNSS session log and do useful things with the tab groups it
finds. Capabilities are exposed as **subcommands of a single `tabgroups` entry
point**:

- `export` — parse the session file and write the groups as a terminal tree /
  md / json / html / csv.
- `classify` — re-group an export by topic with an LLM (OpenAI-compatible
  endpoint), in a `discover` + `apply` two-step flow.

Keep it simple. Add new capabilities as subcommands of `tabgroups`, not as
separate binaries.

## Related project

There is a sibling project, **[tabgroup-sweeper](https://github.com/eli-yip/tabgroup-sweeper)**
(a Chromium MV3 extension, bun + TypeScript), typically checked out next to this
repo (`../tabgroup-sweeper`). The two are **orthogonal**, and the split is
deliberate:

- **This repo** is read-only: it parses the on-disk SNSS file and never touches
  the running browser.
- **tabgroup-sweeper** acts on the *live* browser — it closes every tab in a tab
  group, reading group membership directly via `chrome.tabGroups` / `chrome.tabs`.

So **do not add tab-closing (or any live-browser control) to this CLI.** Closing
grouped tabs was considered as a `close` subcommand here and rejected — only an
extension can read tab-group membership exactly (AppleScript can't see groups;
CDP can't attach to the default profile since Chrome 136). See
[`docs/specs/2026-06-18-01-close-grouped-tabs.md`](docs/specs/2026-06-18-01-close-grouped-tabs.md)
for that decision.

## Conventions

- Before writing a commit message, read the recent commit history. Use the
  Conventional Commit style and write commit messages in English.
- **Prefer mature, modern libraries** over hand-rolled code — `typer` for the
  CLI, `rich` for terminal output, `pydantic` / `pydantic-settings` for models
  and config, `any-llm` for LLM calls. Don't reinvent what a well-maintained
  package does well; add real dependencies to `[project.dependencies]` and keep
  tooling (`ruff`, `ty`) in the `dev` group.
- Target the Python version pinned in `.python-version` / `requires-python`.
- **When an LLM touches data, never let it emit values that must stay byte-exact**
  (URLs, ids, code). Hand it opaque ids, restore the real payload from the source
  by id, and end with an assertion that the output set equals the input set. A
  hallucination must not be able to silently corrupt or drop data. (See
  `classify`.)

## Development workflow

For any non-trivial change, follow a spec-first process:

1. **SPEC → PLAN → implement + LESSON.** Agree on the SPEC (what to build and
   why) before planning; break it into a PLAN (concrete, small steps) before
   writing code; capture experience in a LESSON while implementing. Keep
   `docs/PROGRESS.md` current throughout.
2. **Work on a dedicated branch with small commits.** Branch off `master` named
   `feat-<topic>` (short kebab-case); commit in small, focused steps rather than
   one large commit.
3. **Request review before merging.** When the work is complete, ask the author
   to review it. After approval, squash-merge into `master` and delete the
   branch.

Trivial one-liners (a typo, a doc tweak) don't need a SPEC — use judgment.

### Documentation layout

Docs live under `docs/`. `NO` is a zero-padded same-day sequence number (`01`,
`02`, …) disambiguating documents created on one date; `<topic>` is a short
kebab-case slug.

- **SPECs** — `docs/specs/YYYY-MM-DD-NO-<topic>.md`: what to build and why,
  agreed before implementation.
- **PLANs** — `docs/plans/YYYY-MM-DD-NO-<topic>.md`: a SPEC broken into concrete
  implementation steps.
- **LESSONs** — `docs/lessons/YYYY-MM-DD-NO-<topic>.md`: experience appended
  while executing a PLAN; reorganized into a coherent summary once the PLAN is
  done.
- **`docs/PROGRESS.md`** — a table tracking SPEC / PLAN / status across all work;
  keep it up to date as work advances.

## Before committing

Run the lint pipeline and make sure it is clean:

```sh
just lint        # autocorrect + ruff check + ruff format --check + ty check
just fix-lint    # auto-fix formatting and lint issues
```

There is no test suite yet; verify changes by running the relevant subcommand
against real data (`just run export --format md`). Add **targeted** tests when a change
is hard to verify by hand (e.g. the SNSS parser, the classify integrity guard);
don't add a heavyweight suite this tool doesn't need.

## Notes

- The SNSS format is an append-only binary log; parsing is best-effort and
  tolerant of unknown commands. When touching the parser, preserve that
  defensive behavior (skip commands that fail to decode rather than aborting).
- `classify` is non-destructive to links by construction: the URL-set integrity
  assertion is a hard guarantee, not a nicety — keep it.
