# PLAN：为 classify 的模型输出加缓存

- 日期：2026-06-15
- 分支：`feat-classify-cache`
- 关联 SPEC：[specs/2026-06-15-01-classify-cache.md](../specs/2026-06-15-01-classify-cache.md)
- 状态：待执行

把 SPEC 拆成小步、可独立提交。每步后跑 `just lint`，并尽量用 `just run` 对真实数据
验证。

## 步骤 0：依赖

- `uv add diskcache platformdirs`（不手改 pyproject.toml）。
- `.gitignore` 增加 `/.cache/`。
- commit：`build: add diskcache + platformdirs for classify cache`

## 步骤 1：`cache.py` 骨架与缓存目录解析

新建 `src/tabgroups/cache.py`：

- `resolve_cache_dir() -> Path`：
  1. 读 `TABGROUPS_CACHE_DIR` 环境变量，有则用。
  2. 否则判定是否源码树运行：解析 `Path(__file__).resolve()`（即 `tabgroups` 包路径），
     若路径段含 `site-packages` / `dist-packages` → 已安装；否则源码态。
  3. 源码态：从包路径向上找含 `pyproject.toml` 的目录作仓库根，返回 `<root>/.cache/tabgroups`。
  4. 已安装：`Path(platformdirs.user_cache_dir("tabgroups"))`。
- `open_cache(cache_dir: Path) -> diskcache.Cache`：`mkdir(parents=True, exist_ok=True)`
  后打开。
- `_canonical(obj) -> str`：`json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`。
- `_sha256(s: str) -> str`。
- commit：`feat(cache): add cache module with XDG/dev dir resolution`

## 步骤 2：键计算与读写 helper

仍在 `cache.py`：

- `classify_key(*, prompt: str, model: str, temperature: float, topic_block: str,
  title: str, domain: str) -> str` —— 按 SPEC 3.2 组装并 sha256。
- `discover_key(*, prompt: str, model: str, temperature: float,
  entries: list[tuple[str, str]]) -> str` —— 按 SPEC 3.3。
- 一个轻量计数器，便于上层算命中率，例如：

  ```python
  @dataclass
  class CacheStats:
      hits: int = 0
      misses: int = 0
      @property
      def total(self) -> int: ...
      def rate(self) -> float: ...
  ```

- 读写就直接用 `cache[key]` / `cache.get` / `key in cache`，不再包一层。
- commit：`feat(cache): key derivation for classify/discover + hit stats`

## 步骤 3：接入 `discover_topics`

- `discover_topics` 增参 `cache`（`diskcache.Cache | None`）。
- 命中：反序列化存的 JSON → `TopicList` 直接返回，记一次 hit。
- 未命中：调用模型，成功后把 `TopicList.model_dump_json()` 写入缓存，记一次 miss。
- `discover` 命令：`--no-cache` 为真则传 `None`；结束时 stderr 打印命中与否、缓存目录。
- 验证：`just run classify discover ...` 连跑两次，第二次命中、零调用。
- commit：`feat(classify): cache discover topic proposals`

## 步骤 4：接入 `classify_entries`（核心）

重构 `classify_entries(settings, entries, topics, cache)`：

1. 算一次 `topic_block` 与 `classify` 的 prompt 指纹。
2. 预扫所有 entry：对每个算 `classify_key`，命中则直接填 `assigned[e.id]`、记 hit；
   未命中放入 `pending` 列表、记 miss。
3. 只对 `pending` 分批发模型（保留现有 `_BATCH_SIZE`、批失败告警、defensive fallback）。
4. 模型返回后，对**落在合法 topic 集合内**的分配：填 `assigned` 并**写缓存**。
5. 兜底 `setdefault(UNCLASSIFIED)` 保持原样，且**不写缓存**（仅对 pending 中仍缺的 id）。
6. 返回 `(assigned, stats)`。
- `--no-cache` 时 `cache=None`：跳过预扫与写入，行为与现状完全一致。
- 批次日志改成反映"只发 pending 批"。
- 验证：原样重跑全命中；追加新 tab 只发新批；改 topics.toml 全 miss。
- commit：`feat(classify): per-tab cache for topic assignment`

## 步骤 5：命中率输出

- `apply` 命令在现有计数摘要后，打印
  `cache: {hits}/{total} hit ({rate:.0%}) · {cache_dir}`；`--no-cache` 时打印
  `cache: disabled`。
- 顺带在 `discover` 也打印一行缓存状态（步骤 3 已含，统一措辞）。
- commit：`feat(classify): report cache hit rate in apply summary`

## 步骤 6：CLI —— `--no-cache` 与 `cache clear`

- `discover` / `apply` 各加 `--no-cache` 选项（`bool = False`）。命令体里据此
  `cache = None if no_cache else open_cache(resolve_cache_dir())`。
- 新建 `cache_app = typer.Typer(...)`，加 `clear` 命令：打开缓存、记录条目数、
  `cache.clear()`、打印清掉多少条及目录。`classify_app.add_typer(cache_app, name="cache")`。
- 验证：`tabgroups classify cache clear` 后下次全量重算。
- commit：`feat(classify): add --no-cache flag and `cache clear` subcommand`

## 步骤 7：文档与收尾

- 更新 `classify.py` 模块 docstring / README，简述缓存与 `--no-cache`、`cache clear`
  （遵守 user-facing-writing：只讲怎么用，不堆内部保证细节）。
- 把 `docs/lessons/2026-06-15-01-classify-cache.md` 整理成连贯小结。
- 更新 `docs/PROGRESS.md` 状态。
- `just lint` 干净。
- 请作者评审 → 批准后 squash-merge 进 `master`、删分支。

## 验收（对齐 SPEC 第 5 节）

- 原样重跑第二次零调用；追加 tab 只发新批；改描述全 miss；tg2 重合 tab 命中；
  `--no-cache` 等价旧行为；`cache clear` 后全量；所有路径 `_assert_urls_preserved` 通过；
  `just lint` 干净。
