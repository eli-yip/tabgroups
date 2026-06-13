# tabgroups-export

> [English](README.md)

把浏览器的**标签页分组**——每个分组及其中的所有标签——导出成终端树状图、
Markdown、HTML、JSON 或 CSV。

无需插件、无需登录、完全离线。直接读取浏览器本地的会话文件。支持
**Brave、Chrome、Chromium、Edge、Vivaldi**。

## 快速开始

需要 [uv](https://docs.astral.sh/uv/) 和 Python ≥ 3.14。

```bash
git clone <repo-url> && cd tabgroups-export

# 在终端里展开成树状图（标题可点击）
uv run tabgroups-export --format tree

# 导出全部格式到 ./tabgroups/
uv run tabgroups-export
```

> **提示：** 想要完整、最新的导出，先退出浏览器。浏览器开着时运行也安全，
> 只是反映的是最近一次保存的状态。

## 用法

```bash
# 指定浏览器 / 配置文件（profile）
uv run tabgroups-export --browser chrome
uv run tabgroups-export --profile "Profile 1"

# 单一格式输出到 stdout（可任意管道处理）
uv run tabgroups-export --format md > tabs.md
uv run tabgroups-export --format html > tabs.html

# 指定具体的会话文件，或更改输出目录
uv run tabgroups-export --session /path/to/Session_123456
uv run tabgroups-export --out-dir ~/Desktop/export
```

如果找不到指定的 profile，报错会列出你实际拥有的 profile。

## 选项

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--browser`、`-b` | `brave` | `brave` · `chrome` · `chromium` · `edge` · `vivaldi` |
| `--profile` | `Default` | profile 目录名（如 `"Profile 1"`） |
| `--format`、`-f` | `all` | `tree` · `md` · `json` · `html` · `csv` · `all` |
| `--session` | 最新 | 指定某个 `Session_*` 文件的路径 |
| `--out-dir` | `tabgroups` | `--format all` 时的输出目录 |

`all` 会向输出目录写入四个文件；任何单一格式则打印到 stdout。摘要彩色表格始终
显示（在 stderr 上），因此用管道导出单一格式时输出依然干净。

## 平台

跨平台：**macOS、Linux、Windows**。各浏览器的 profile 位置会自动探测。
（在 macOS 上开发与测试。）

完全离线运行；导出的文件包含你真实的浏览历史（输出目录已被 git 忽略，不会误提交）。

## 许可证

[MIT](LICENSE)
