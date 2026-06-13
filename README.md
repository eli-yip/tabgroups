# brave-tabgroups

Export all your **Chromium tab groups** straight from the browser's on-disk
session file — no extension, no browser API, works offline. Defaults to Brave;
also supports **Chrome, Chromium, Edge, and Vivaldi** (`--browser`).

It parses the `SNSS` session log directly and emits each tab group (name, color)
with all its tabs (title + URL) as a terminal tree, Markdown, HTML, JSON, or CSV.

## How it works

Chromium browsers store the live window/tab state — including tab groups — as an
append-only binary log under the profile's `Sessions/` directory
(`Session_*` files, the SNSS format). This tool reads the newest session file,
decodes the relevant commands (tab-group metadata, tab→group assignment, tab
navigations) and renders a snapshot.

The session file is **copied before parsing**, so it is safe to run while the
browser is open. For the most complete and clean state, **fully quit the browser
first** so it flushes a final snapshot.

## Usage

```bash
# expand every group as a tree in the terminal (clickable titles)
uv run brave-tabgroups --format tree

# all file formats into ./tabgroups/
uv run brave-tabgroups

# a different browser
uv run brave-tabgroups --browser chrome
uv run brave-tabgroups -b edge -f tree

# a single format to stdout
uv run brave-tabgroups --format md
uv run brave-tabgroups --format html > tabs.html

# a different profile, or an explicit session file
uv run brave-tabgroups --profile "Profile 1"
uv run brave-tabgroups --session ~/path/to/Session_123456

# choose output dir for --format all
uv run brave-tabgroups --out-dir ~/Desktop/export
```

If a profile isn't found, the error lists the profiles that actually exist for
that browser.

### Options

| flag | default | meaning |
|------|---------|---------|
| `--browser` / `-b` | `brave` | `brave` \| `chrome` \| `chromium` \| `edge` \| `vivaldi` |
| `--profile` | `Default` | profile directory name |
| `--session` | (auto) | explicit path to a `Session_*` file |
| `--format` / `-f` | `all` | `tree` \| `md` \| `json` \| `html` \| `csv` \| `all` |
| `--out-dir` | `tabgroups` | output directory when `--format all` |

`--format all` writes files; any single format prints to stdout. A colored
summary table (group name, color, tab count) is always printed to stderr, so it
stays out of the way when you pipe a single format.

## Supported platforms

Auto-detects browser profile paths on macOS, Linux, and Windows. Built with
[`typer`](https://typer.tiangolo.com/) and [`rich`](https://rich.readthedocs.io/),
Python ≥ 3.14.

## Caveats

- The SNSS log is append-only; this reflects the **last flushed** state. Quit
  the browser for a guaranteed-complete export.
- Reads only the browser's own storage on your machine; nothing is sent anywhere.
- Exports contain your real browsing history — `tabgroups/` and `tabgroups.*`
  are git-ignored so you don't commit them by accident.

## License

[MIT](LICENSE)
