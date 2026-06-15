# tabgroups

> [English](README.md)

把浏览器的**标签页分组**——每个分组及其中的所有标签——导出成终端树状图、
Markdown、HTML、JSON 或 CSV，还能用 LLM 按主题重新归类。

无需插件、无需登录；导出完全离线。支持
**Brave、Chrome、Chromium、Edge、Vivaldi**。

## 快速开始

需要 [uv](https://docs.astral.sh/uv/) 和 Python ≥ 3.14。

```bash
git clone https://github.com/eli-yip/tabgroups && cd tabgroups

# 在终端里展开成树状图（标题可点击）
uv run tabgroups export --format tree

# 导出全部格式到 ./tabgroups/
uv run tabgroups export
```

> **提示：** 想要完整、最新的导出，先退出浏览器。浏览器开着时运行也安全，
> 只是反映的是最近一次保存的状态。

## 用法

```bash
# 指定浏览器 / 配置文件（profile）
uv run tabgroups export --browser chrome
uv run tabgroups export --profile "Profile 1"

# 单一格式输出到 stdout（可任意管道处理）
uv run tabgroups export --format md > tabs.md
uv run tabgroups export --format html > tabs.html

# 指定具体的会话文件，或更改输出目录
uv run tabgroups export --session /path/to/Session_123456
uv run tabgroups export --out-dir ~/Desktop/export
```

## 选项

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--browser`、`-b` | `brave` | `brave` · `chrome` · `chromium` · `edge` · `vivaldi` |
| `--profile` | `Default` | profile 目录名（如 `"Profile 1"`） |
| `--format`、`-f` | `all` | `tree` · `md` · `json` · `html` · `csv` · `all` |
| `--session` | 最新 | 指定某个 `Session_*` 文件的路径 |
| `--out-dir` | `tabgroups` | `--format all` 时的输出目录 |

`all` 会向输出目录写入四个文件；单一格式则打印到标准输出。

## 按主题分类（LLM）

「稍后读」分组往往只是按时间堆叠的标签。`tabgroups classify` 用 LLM 把导出
按**主题**重新归类，分两步进行，分类标准始终由你掌控：

```bash
# 1. 从你的标签里提出一份主题列表 → 可编辑的 topics.toml
uv run tabgroups classify discover tabgroups/tabgroups.json -o topics.toml

# 2. ……手动修改 topics.toml（增删/合并主题、细化每条描述）……

# 3. 按这份主题分类每个标签（外加一个 “unclassified” 未分类栏）
uv run tabgroups classify apply tabgroups/tabgroups.json -t topics.toml -f md
```

输出格式与导出一致：`tree · md · json · html · csv · all`。

模型结果会被缓存，所以改完 `topics.toml` 或新增几个标签后再跑，只会对真正变化的部分
调用 LLM（`apply` 会打印缓存命中率）。加 `--no-cache` 可绕过缓存，
`tabgroups classify cache clear` 可清空缓存。

通过 `config.toml`（见 [`config.example.toml`](config.example.toml)）或 `TABGROUPS_*`
环境变量（`TABGROUPS_BASE_URL` / `TABGROUPS_API_KEY` / `TABGROUPS_MODEL`）指向任一
OpenAI 兼容接口，环境变量优先：

```toml
base_url = "https://api.openai.com/v1"
api_key  = "sk-..."        # 也可在 shell 里设 TABGROUPS_API_KEY
model    = "gpt-4o-mini"
```

## 平台

支持 **macOS、Linux、Windows**，主要在 macOS 上测试。

导出完全在本地完成，不上传任何数据；`classify` 会把标签的标题和域名发送到你
配置的 LLM 接口。导出的文件包含你真实的浏览历史。

## 许可证

[MIT](LICENSE)
