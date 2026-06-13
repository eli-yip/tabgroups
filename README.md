# tabgroups-export

> [中文文档](README.zh.md)

Export your browser's **tab groups** — every group and the tabs inside it — to a
terminal tree, Markdown, HTML, JSON, or CSV.

No extension, no sign-in, fully offline. It reads the browser's own session file
on disk. Works with **Brave, Chrome, Chromium, Edge, and Vivaldi**.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Python ≥ 3.14.

```bash
git clone <repo-url> && cd tabgroups-export

# pretty tree in your terminal (clickable titles)
uv run tabgroups-export --format tree

# export every format into ./tabgroups/
uv run tabgroups-export
```

> **Tip:** quit the browser first for a complete, up-to-date export. Running
> while it's open is safe, but only shows the last saved state.

## Usage

```bash
# a different browser / profile
uv run tabgroups-export --browser chrome
uv run tabgroups-export --profile "Profile 1"

# a single format to stdout (pipe it anywhere)
uv run tabgroups-export --format md > tabs.md
uv run tabgroups-export --format html > tabs.html

# point at a specific session file, or change the output folder
uv run tabgroups-export --session /path/to/Session_123456
uv run tabgroups-export --out-dir ~/Desktop/export
```

If a profile isn't found, the error lists the profiles you actually have.

## Options

| flag | default | description |
|------|---------|-------------|
| `--browser`, `-b` | `brave` | `brave` · `chrome` · `chromium` · `edge` · `vivaldi` |
| `--profile` | `Default` | profile directory name (e.g. `"Profile 1"`) |
| `--format`, `-f` | `all` | `tree` · `md` · `json` · `html` · `csv` · `all` |
| `--session` | newest | path to a specific `Session_*` file |
| `--out-dir` | `tabgroups` | output folder for `--format all` |

`all` writes four files to the output folder; any single format prints to
stdout. A colored summary table is always shown (on stderr), so piping a single
format stays clean.

## Platforms

Cross-platform: **macOS, Linux, and Windows**. Profile locations for each
browser are detected automatically. (Developed and tested on macOS.)

## Privacy

Everything runs locally — nothing is ever uploaded. Note that exported files
contain your **real browsing history**, so don't share them carelessly. The
output folder and `tabgroups.*` files are git-ignored to prevent accidental
commits.

## License

[MIT](LICENSE)
