# SPEC：新增 `close` 子命令——关闭浏览器中所有「属于某个标签组」的标签页

- 日期：2026-06-18
- 分支：`feat-close-grouped-tabs`
- 状态：**已否决 / 不在本仓实现**——改做独立浏览器扩展

## 0. 结论（2026-06-18 更新）

本 SPEC 提出的「CLI `close` 子命令 + AppleScript」方案**最终未采用**。调研关闭运行中
浏览器标签页的可行路径后发现：

- **AppleScript**：Chromium 的脚本字典只有 `window/tab`，**没有「标签组」概念**，
  无法直接判断某标签页是否分组——只能解析 SNSS 再按 URL 近似匹配，不精确。
- **CDP（remote debugging）**：Chrome 136（2025-04）起对默认 profile 失效，连不到
  用户的日常浏览器。出局。
- **浏览器扩展（`chrome.tabGroups` + `chrome.tabs`）**：能**直接、精确**读取标签组
  归属，无 URL 匹配、无启发式。

因此该需求改为独立项目 **[tabgroup-sweeper](https://github.com/eli-yip/tabgroup-sweeper)**
（Chromium MV3 扩展，bun + TypeScript）实现，与本仓的 export / classify 正交。其
SPEC 见该仓库 `docs/specs/2026-06-18-01-close-grouped-tabs.md`。

以下为当时基于 CLI 方案的原始 SPEC，保留作为决策记录。

## 1. 背景与动机

`tabgroups` 现有 `export` / `classify` 都是**纯只读**：解析磁盘上的 SNSS 会话文件，
从不接触正在运行的浏览器。

用户需要一个**清理**动作：把「已经归入某个标签组（tab group）的标签页」从正在运行
的浏览器里关掉，**保留未分组的散落标签页**。

> 例：Tab1∈Tg1、Tab2∈Tg2、Tab3 不属于任何组 → 关闭 Tab1、Tab2，保留 Tab3。

这与「关闭导出的标签页」一致——导出的恰是分组标签页。功能上它是**独立子命令**，
与 export / classify 的产物无关。

## 2. 目标与非目标

### 目标

- 新增子命令 `tabgroups close`，关闭运行中浏览器里**所有属于某个标签组**的标签页。
- 未分组标签页一律保留。
- 默认**直接关闭、无需确认**（用户明确要求）。
- 仅支持 macOS（当前唯一目标平台），通过 AppleScript 控制浏览器。

### 非目标

- 不做交互确认 / 二次确认（用户已确认直接关闭）。
- 不依赖 `export` / `classify` 的输出文件；`close` 自带 SNSS 解析。
- 不支持非 macOS 平台（Linux/Windows 无 AppleScript；本次不实现 CDP 路径）。
- 不按组名筛选关闭（"只关某几个组"）——本次范围是「全部分组标签页」。

## 3. 关键技术约束

**AppleScript（以及 Chromium 的任何外部接口）不暴露「某标签页属于哪个标签组」。**
因此「哪些打开的标签页是分组的」只能来自 SNSS：

- 复用 `export.py` 的 `load_session` + `parse`，得到所有分组标签页 →
  **分组 URL 集合 `grouped_urls`**。
- AppleScript 只负责枚举 / 关闭浏览器**当前打开**的标签页。

两边的唯一可靠关联键是 **URL**（SNSS 的 tab_id 与 AppleScript 的 tab id / index
是不同命名空间，且 index 会随开关标签页漂移）。

### 边界情况（写明、不做特殊处理）

- **URL 同时存在于分组与未分组**：按 URL 匹配会把未分组的那个副本也关掉。少见，
  接受。
- **SNSS 滞后**：会话文件是追加日志，可能略滞后于浏览器真实状态；刚分组/刚改 URL
  的标签页可能匹配不到。属固有限制，`close` 会报告实际关闭数供用户核对。

## 4. 设计

### 4.1 子命令与选项

`tabgroups close`，选项与 `export` 对齐以复用会话解析：

- `--browser` / `-b`（默认 brave）：复用 `export.Browser` 枚举。
- `--profile`（默认 "Default"）。
- `--session`（可选，显式 Session_* 路径）。
- `--dry-run`：只打印「将要关闭哪些标签页」，不实际关闭（便于核对；默认 **关**，
  即默认真的关）。

CLI 装配：`export.py` 暴露 `close` 函数，`cli.py` 中 `app.command()(close)`，与
`export` 同级。

### 4.2 流程

1. `load_session` + `parse` → `grouped_urls: set[str]`（遍历 `d["groups"]` 的所有
   tab 的 `url`）。
2. AppleScript **读阶段**：返回浏览器全部标签页的 `(tab id, URL)`（每行
   `id\tURL`）。用 AppleScript 自带的 `id`（会话内稳定、关标签页不漂移），避免靠
   index。
3. Python 计算待关集合：`URL ∈ grouped_urls` 的那些 tab id。
4. AppleScript **关阶段**：按 tab id 关闭这些标签页。
5. 打印摘要：分组标签页数 / 浏览器打开标签页数 / 实际关闭数。

### 4.3 AppleScript 机制

- 通过 `subprocess` 调 `osascript` 执行脚本（AppleScript 无足够成熟的 Python 库值得
  引入；`osascript` 是 macOS 上的惯用做法）。
- 应用名映射：brave→"Brave Browser"、chrome→"Google Chrome"、chromium→"Chromium"、
  edge→"Microsoft Edge"、vivaldi→"Vivaldi"。
- **关阶段按 tab id 定位**，例如对每个 id：
  `close (every tab of every window whose id is <ID>)`，规避 index 漂移。
- 浏览器未运行 / 自动化权限被拒：捕获 `osascript` 非零退出，给出可读报错
  （首次运行会触发 macOS「自动化」授权弹窗，需用户允许 终端/Claude 控制浏览器）。

## 5. 正确性与风险

- **只读解析不变**：SNSS 仍走 `load_session` 的 temp-copy 路径，不动原文件。
- **误关风险**：仅限 §3 的 URL 同名边界；`--dry-run` 提供事前核对手段。
- **平台**：非 macOS 直接报错退出（明确不支持），不静默失败。
- **权限**：osascript 失败时区分「浏览器没开」与「自动化未授权」并给出指引。

## 6. 验收标准

- 浏览器里有分组标签页 + 未分组标签页时，`tabgroups close` 关闭全部分组标签页，
  未分组标签页保持打开。
- `--dry-run` 列出待关标签页且不关闭任何标签页。
- 浏览器未运行 / 权限被拒时给出清晰报错，非堆栈崩溃。
- `just lint` 干净。

## 7. 待 PLAN 细化的实现点

- `export.py`（或新 `close.py`）新增 `close` 函数 + AppleScript 读/关脚本生成。
- 应用名映射表；`osascript` 调用与退出码处理。
- tab id 解析与按 id 关闭脚本。
- 摘要输出（复用 `err` Console 风格）。
- PLAN 阶段先用**只读** AppleScript（数标签页）在用户真实浏览器上验证可行性与授权
  流程，再写关闭逻辑。
