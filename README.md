# tabgroups

> [中文文档](README.zh.md)

Export your browser's **tab groups** — every group and the tabs inside it — to a
terminal tree, Markdown, HTML, JSON, or CSV, and optionally re-group them by topic
with an LLM.

No extension, no sign-in; export runs fully offline. Works with **Brave, Chrome,
Chromium, Edge, and Vivaldi**.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Python ≥ 3.14.

```bash
git clone https://github.com/eli-yip/tabgroups && cd tabgroups

# pretty tree in your terminal (clickable titles)
uv run tabgroups export --format tree

# export every format into ./tabgroups/
uv run tabgroups export
```

> **Tip:** quit the browser first for a complete, up-to-date export. Running
> while it's open is safe, but only shows the last saved state.

## Usage

```bash
# a different browser / profile
uv run tabgroups export --browser chrome
uv run tabgroups export --profile "Profile 1"

# a single format to stdout (pipe it anywhere)
uv run tabgroups export --format md > tabs.md
uv run tabgroups export --format html > tabs.html

# point at a specific session file, or change the output folder
uv run tabgroups export --session /path/to/Session_123456
uv run tabgroups export --out-dir ~/Desktop/export
```

## Options

| flag | default | description |
|------|---------|-------------|
| `--browser`, `-b` | `brave` | `brave` · `chrome` · `chromium` · `edge` · `vivaldi` |
| `--profile` | `Default` | profile directory name (e.g. `"Profile 1"`) |
| `--format`, `-f` | `all` | `tree` · `md` · `json` · `html` · `csv` · `all` |
| `--session` | newest | path to a specific `Session_*` file |
| `--out-dir` | `tabgroups` | output folder for `--format all` |

`all` writes four files to the output folder; any single format prints to stdout.

## Classify by topic (LLM)

"Read later" groups are often just time-ordered dumps. `tabgroups classify`
re-groups an export by **topic** with an LLM, in two steps so you stay in control
of the taxonomy:

```bash
# 1. propose a topic list from your tabs → editable topics.toml
uv run tabgroups classify discover tabgroups/tabgroups.json -o topics.toml

# 2. ...edit topics.toml (rename / merge topics, refine each description)...

# 3. classify every tab into those topics (+ an "unclassified" bucket)
uv run tabgroups classify apply tabgroups/tabgroups.json -t topics.toml -f md
```

Output uses the same `tree · md · json · html · csv · all` formats as the export.

Model results are cached, so re-running after editing `topics.toml` or adding a
few tabs only calls the LLM for what actually changed (`apply` prints the cache
hit rate). Pass `--no-cache` to bypass it, or `tabgroups classify cache clear`
to wipe it.

Point it at any OpenAI-compatible endpoint via `config.toml` (see
[`config.example.toml`](config.example.toml)) or `TABGROUPS_*` environment variables
(`TABGROUPS_BASE_URL` / `TABGROUPS_API_KEY` / `TABGROUPS_MODEL`), which take precedence:

```toml
base_url = "https://api.openai.com/v1"
api_key  = "sk-..."        # or set TABGROUPS_API_KEY in your shell
model    = "gpt-4o-mini"
```

## Platforms

Works on **macOS, Linux, and Windows**; primarily tested on macOS.

Export runs locally and uploads nothing; `classify` sends each tab's title and
domain to the LLM endpoint you configure. Exported files contain your real
browsing history.

## Related

Done exporting and want to **close** those grouped tabs?
[tabgroup-sweeper](https://github.com/eli-yip/tabgroup-sweeper) is a companion
Chromium extension that closes every tab in a tab group, leaving ungrouped tabs
open.

## License

[MIT](LICENSE)
