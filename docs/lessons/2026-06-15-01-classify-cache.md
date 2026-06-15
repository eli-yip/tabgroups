# LESSON：为 classify 的模型输出加缓存

- 日期：2026-06-15
- 分支：`feat-classify-cache`
- 关联：[SPEC](../specs/2026-06-15-01-classify-cache.md) · [PLAN](../plans/2026-06-15-01-classify-cache.md)

## 关键决策与缘由

- **缓存键去掉 `id` 与 `url`，只用 `(prompt 指纹，model, temperature, topic_block,
  title, domain)`。** 起初草案含 `id`，但 `id` 是 `load_entries` 按去重首见顺序生成的
  位置序号，换一组导出就重排 —— 含 `id` 会让 tg1/tg2 中逐字相同的 tab 全部 miss，缓存
  形同虚设。模型对一个 tab 的判断只依赖它看到的 `title`+`domain`+topic 列表，键就该且
  只该含这些。这同时让"跨导出复用"自然成立。

- **用 system prompt 的 sha256 当版本指纹，而非手维护的 `PROMPT_VERSION`。** 改 prompt
  自动失效，少一个要记得 bump 的变量。

- **只缓存模型真实返回的合法分配，绝不缓存 `UNCLASSIFIED` 兜底。** 否则一次超时/抽风会
  把 `unclassified` 永久固化到某个 tab 上。实现上把"批内合法分配"与"事后 setdefault
  兜底"分开，只有前者写缓存。

- **缓存目录按运行形态区分。** `TABGROUPS_CACHE_DIR` 覆盖 > 源码树用仓库 `.cache/` >
  安装后用 `platformdirs`（尊重 `XDG_CACHE_HOME`）。判定方式：包路径是否在
  `site-packages`/`dist-packages` 下。

## 实现要点

- 缓存逻辑独立到 `src/tabgroups/cache.py`：`resolve_cache_dir` / `open_cache` /
  `classify_key` / `discover_key` / `CacheStats`。
- `classify_entries` 改为预扫命中、只把 miss 组批发模型，返回 `(assigned, stats)`。
- `apply` 摘要打印 `cache: H/T hit (R%) · <dir>`；`discover` 打印 hit/miss；
  `--no-cache` 时打印 `cache: disabled`。
- 新增 `classify cache clear` 子命令。

## 验证

不花钱的确定性验证（临时脚本，未提交）覆盖三条核心行为，均通过：

1. 预置缓存后，全命中且 monkeypatch 的 `_acomplete` 一旦被调用即报错 —— 证明命中不触发
   模型调用，命中率 100%。
2. 跨导出命中：同 `title`+`domain`、不同 `id`+`url` 的 tab 命中同一键。
3. 不投毒：模型抛错的批次过后缓存条目数为 0。

`just lint` 全程干净。

## 端到端跑出来的两个修正

真实跑 411 tab（14 批）暴露了两处，初版验证（确定性脚本）没覆盖到：

1. **显式 unclassified 也要缓存。** 初版只缓存"落在合法 topic 内"的分配，于是模型主动判
   为 unclassified 的 tab 每次都重发：热跑只有 97% 命中，且因 temp=0 仍非逐字稳定，那批
   重算结果会变、输出不可复现。改为缓存"模型在本批显式返回的每个 id（归一化后，含显式
   unclassified）"，只把 `setdefault` 兜底排除在外。修正后热跑 100% 命中。

2. **渲染顺序依赖缓存命中比例。** `build_document` 原按 `assignments` 字典插入顺序装桶，
   而插入顺序在"部分命中"与"全命中"下不同 → 组内 tab 顺序变、输出 diff（内容一致，仅顺
   序）。改为按原始 entry 顺序装桶，输出与缓存状态无关、完全可复现。

教训：纯逻辑的确定性验证能证明命中/不投毒，但**命中率上限**和**输出可复现性**只有真实
端到端跑（含模型实际判 unclassified、跨运行非逐字稳定）才暴露得出来。

## 坑

- `ruff` isort 要求 `from diskcache import Cache` 按字母序排在 `pydantic` 之前，首次
  提交被 lint 拦下，`just fix-lint` 自动修正。
