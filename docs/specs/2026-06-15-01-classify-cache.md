# SPEC：为 classify 的模型输出加缓存

- 日期：2026-06-15
- 分支：`feat-classify-cache`
- 状态：草拟 / 待评审

## 1. 背景与动机

`tabgroups classify` 的两个阶段都要调用 LLM：

- `discover` —— 1 次调用，从全部 tab 的 `title [domain]` 提出一组 topic。
- `apply` → `classify_entries` —— 按 30 个 tab 一批，发起 N/30 次调用，把每个 tab
  分到某个 topic。每个 classify 请求都内嵌**完整的 topic 列表**。

`classify` 的批量调用是这个工具里最慢、最花钱的部分。而它的典型迭代循环恰恰需要
反复重跑：

1. 原样重跑 —— 上次某批失败重试、崩溃恢复，或只是换个 `--format` 重新渲染。
2. 编辑 `topics.toml`（改描述 / 合并 / 改名）后重跑 `apply`。
3. export 增删了几个 tab 后重跑。
4. 换一组 tabgroup（tg2）导出，其中部分 tab 与上一组（tg1）重合。

目前每次重跑都把所有 tab 重新发给模型，上述场景的重复计算完全没有被复用。

本 SPEC 的目标：**缓存模型对单个 tab 的分类结果，让上述重跑尽可能命中缓存，且在
构造上不可能损坏数据。**

## 2. 目标与非目标

### 目标

- 缓存 `classify` 阶段的逐 tab 分类结果，命中时不再发起模型调用。
- 缓存 `discover` 阶段的整次结果，原样重跑时直接复用。
- 缓存键设计成：在"语义相同"的重跑中命中，在"语义改变"时正确失效。
- 跨导出复用：tg1 与 tg2 中**内容相同的 tab**，在套用同一组 topics 时命中。
- 保持 `classify` 对链接的硬保证：缓存只触碰 `tab → topic 名`，URL 仍由 id 从原始
  export 还原，`_assert_urls_preserved` 照常在缓存之后执行。

### 非目标

- 不做跨机器 / 远程共享缓存，只做本地磁盘缓存。
- 不设 TTL（基于时间的过期）。模型分类不会"过时"，失效靠键的内容指纹完成。
- 不缓存渲染输出（md/json/html/csv）—— 渲染本身很快，不值得缓存。
- 不改变 `discover` / `apply` 的命令语义和现有 CLI 选项的默认行为。

## 3. 设计

### 3.1 缓存粒度：单 tab 级

模型对一个 tab 的判断是一个纯函数：

```
topic = f(topic_block, title, domain)
```

模型**只看到 title 和 domain**，看不到 `id`、也看不到 URL。因此缓存以"模型实际看到
的内容"为键，值为模型给出的 topic 名。

### 3.2 缓存键（classify，逐 tab）

```
key = sha256(canonical_json({
    "kind": "classify",
    "v":     sha256(_CLASSIFY_SYS),  # system prompt 的指纹，prompt 一改自动失效
    "model": settings.model,
    "temperature": _TEMPERATURE,
    "topics": topic_block,     # 模型实际看到的那段编号 topic 文本，逐字
    "title": entry.title,
    "domain": entry.domain,
}))
value = topic_name: str
```

**关键决定：键不含 `id`、也不含 `url`。**

- `id` 是 `load_entries` 按去重后首见顺序生成的**位置序号**，换一组导出就会重排。
  若把 `id` 放进键，tg2 中逐字相同的 tab 只要位置不同就 miss —— 缓存形同虚设。去掉
  `id` 后，命中只取决于内容。
- `url` 不参与模型判断，且包含它会破坏"内容相同则命中"。URL 不入键不影响完整性：
  输出里的 URL 始终由 `id` 从原始 export 还原，与缓存无关。

**用 `topic_block` 原文当 topic 指纹**：缓存有效性精确绑定"模型当时看到了什么"。改
任何描述、改名、调整顺序都让 `topic_block` 变化，从而自然失效，无需另设失效规则。

### 3.3 缓存键（discover，请求级）

`discover` 只有 1 次调用，按整次请求缓存：

```
key = sha256(canonical_json({
    "kind": "discover",
    "v":     sha256(_DISCOVER_SYS),
    "model": settings.model,
    "temperature": _TEMPERATURE,
    "entries": [ (title, domain) for e in entries ],  # 全体 entry 指纹，按出现顺序
}))
value = TopicList（序列化为 JSON 存储）
```

换一组 tabgroup 时，entry 集合不同 → discover 必然 miss、重新提一组 topic，这是预期
行为。

### 3.4 命中场景对照

| 重跑场景 | classify 缓存 | 说明 |
|---|---|---|
| 原样重跑 / 换 `--format` | 全命中 | 内容与 topics 都未变 |
| 编辑 topics.toml | 全失效 | `topic_block` 变，语义变了，应当重算 |
| export 增删 tab | 只新增的 miss | 已有 tab 内容不变则命中 |
| tg2 与 tg1 部分重合（同 topics） | 重合 tab 命中 | 键基于 (title, domain)，与位置无关 |
| tg2 套用不同 topics.toml | 全 miss | `topic_block` 不同 |

### 3.5 写入纪律（重要）

- **缓存模型在本批中显式返回的每个分配**（归一化后的值：合法 topic 名，或一个明确的
  `UNCLASSIFIED`）。模型主动把某 tab 判为 unclassified 是一个确定的决定，不是失败，应当
  缓存 —— 否则这些 tab 每次都被重发、且 temp=0 也并非逐字稳定，破坏可复现性。
- **绝不缓存兜底值**：失败批次、模型漏掉的 id 由 `assigned.setdefault(UNCLASSIFIED)`
  补上，这类**不写入缓存** —— 否则一次抽风/超时会把 `unclassified` 永久固化到该 tab。
- 关键区分：**"模型显式返回的（含显式 unclassified）"可缓存**，**"事后 setdefault
  兜底的"不可缓存**。

### 3.5.1 渲染顺序与可复现

- `build_document` 按**原始 entry 顺序**把 tab 装桶，而非按 `assignments` 字典顺序。
  否则同一份分配在"部分命中缓存"与"全部命中"两种运行下，组内 tab 顺序会不同，输出不可
  复现（URL 集合相同，仅顺序不同）。

### 3.6 集成点

在 `classify_entries` 内，分批**之前**先查缓存：

1. 对每个 entry 计算 classify 缓存键，命中的直接取结果填入 `assigned`，不进批。
2. 只把未命中的 entry 组成批次发给模型。
3. 模型返回后，对每个"合法分配"写回缓存，再填入 `assigned`。
4. 兜底 `setdefault(UNCLASSIFIED)` 逻辑保持不变，但不写缓存。

效果："增 5 个 tab 重跑"只会发 1 个小批；"换格式重渲染"零调用。

`discover_topics` 内：先查请求级缓存，命中直接返回 `TopicList`，否则调用模型并写回。

`_assert_urls_preserved` 位置与逻辑不变，跑在缓存之后。缓存碰不到 URL，硬保证不受
影响。

### 3.7 存储

- 使用 **`diskcache`** 库（SQLite 后端 KV 缓存），加入 `[project.dependencies]`。
  契合 CLAUDE.md「优先成熟、现代的库」：自带原子写、并发安全、过期支持（此处不用）。
- 缓存目录按运行形态区分（在 `cache.py` 内 `resolve_cache_dir()` 解析）：
  1. 若设了 `TABGROUPS_CACHE_DIR` 环境变量 → 直接用它（最高优先级，便于覆盖/测试）。
  2. 否则若**从源码树运行**（包文件不在 `site-packages`/`dist-packages` 下）→ 仓库根
     的 `.cache/tabgroups/`。便于开发时就地查看/清理。
  3. 否则（**已安装为 CLI 工具**）→ `platformdirs.user_cache_dir("tabgroups")`：Linux
     尊重 `XDG_CACHE_HOME`，macOS 用 `~/Library/Caches/tabgroups`。
- 判定"源码树运行"：解析 `tabgroups.__file__`，若路径段含 `site-packages` /
  `dist-packages` 视为已安装，否则视为源码/editable 安装；源码态再向上找含
  `pyproject.toml` 的仓库根放 `.cache/`。
- 新增依赖 `platformdirs`（成熟、跨平台、被广泛使用）。
- 在 `.gitignore` 增加 `/.cache/`。

### 3.8 CLI 与输出

- 新增子命令选项 `--no-cache`：本次运行完全绕过缓存读写（`discover` 与 `apply` 都
  支持）。默认启用缓存。
- 新增子命令 `tabgroups classify cache clear`：清空缓存目录内容并打印清掉的条目数。
- **输出命中率**：`apply` 结束时在 stderr 摘要里打印 classify 命中情况，例如
  `cache: 42/57 hit (74%), 缓存目录 <path>`；`discover` 打印是否命中。`--no-cache`
  时不打印命中率（或标注 disabled）。

### 3.9 prompt 指纹与失效

- 不再维护手写的版本常量；键中的 `v` 取相关 system prompt 的 sha256
  （`classify` → `_CLASSIFY_SYS`，`discover` → `_DISCOVER_SYS`）。改 prompt 自动失效，
  零维护。
- `model`、`temperature` 已进键，切换模型或温度自动失效。
- 说明：user-message 模板（包裹 topic_block / tab 列表的 f-string 结构）当前稳定；
  其拼装片段（`topic_block`、title、domain）本就在键内。若将来大改 user-message 的结构
  且影响模型输出，应把该模板也纳入 `v` 的指纹来源。

## 4. 正确性与风险

- **数据完整性**：缓存只产出 `tab → topic 名`，URL 由 id 还原，
  `_assert_urls_preserved` 照常执行 —— 缓存在构造上无法损坏或丢失链接。符合 CLAUDE.md
  对 `classify` 的硬保证。
- **批上下文耦合**：单 tab 缓存把分类当作 `(topics, title, domain)` 的纯函数；当前
  实现中一个 tab 的结果理论上可能受同批其他 tab 影响。但 `_TEMPERATURE = 0.0` 且
  prompt 要求逐 id 独立判断，该耦合可忽略。本 SPEC 接受这一近似。
- **title+domain 相同、URL 不同的 tab**：会共用缓存键、得到同一 topic。不损失精度 ——
  模型本来也只看到 title+domain，对它们给的答案必然相同。
- **缓存使结果更确定**：provider 在 temp=0 下也未必逐字稳定，缓存反而让重跑可复现，
  是附带收益。
- **缓存投毒风险**：已通过「不缓存兜底值」消除；只有模型成功且合法的分配才落盘。

## 5. 验收标准

- `apply` 连续两次原样运行：第二次零模型调用（日志可见无批次发出 / 全部命中）。
- `apply` 后给 export 追加若干新 tab 再跑：只对新 tab 发起调用。
- 编辑 `topics.toml` 的任一描述后跑 `apply`：全部重新分类（无命中）。
- 用 tg2（与 tg1 部分重合、套用同一 topics）跑 `apply`：重合 tab 命中、新 tab 调用。
- `--no-cache` 下行为与加缓存前完全一致（每次全量调用）。
- `classify cache clear` 后下一次运行全量重算。
- 所有路径下 `_assert_urls_preserved` 通过；`just lint` 干净。

## 6. 待 PLAN 细化的实现点

- 新建 `src/tabgroups/cache.py`：封装 `diskcache`、`resolve_cache_dir()`、键计算、
  读写 helper、命中计数。
- canonical JSON 的实现（`json.dumps(..., sort_keys=True, ensure_ascii=False)`）。
- `classify_entries` 重构以分离"可缓存的合法分配"与"兜底"，并统计命中率。
- `--no-cache` 与 `cache clear` 的 typer 接线、命中率输出接线。
- 验收用的 targeted 小测试（键稳定性 / 不缓存兜底 / 跨导出命中）。
