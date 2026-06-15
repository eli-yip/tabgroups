# SPEC：为 classify 的 LLM 请求加并发 + RPM 限流

- 日期：2026-06-15
- 分支：`feat-classify-rate-limit`
- 状态：草拟 / 待评审

## 1. 背景与动机

`tabgroups classify apply` 把待分类的 tab 按 `_BATCH_SIZE`（30）一批，逐批调用
LLM。当前 `classify_entries` 是**串行**的：

```python
for n, batch in enumerate(batches, 1):
    assigned.update(await classify_batch(batch, f"{n}/{len(batches)}"))
```

任意时刻只有一个在途请求，几百个 tab 要等几十秒。这是这个工具最慢的环节。

串行结构下加 RPM 限流是空操作：1500 RPM = 25 req/s，而单个 LLM 批次要几秒，永远
碰不到上限。**只有同时引入并发，限流才有意义**：并发把吞吐拉上去，再用令牌桶把它
压回服务商档位以内，避免 429。

本 SPEC 目标：**并行跑各批，并以默认 1500 RPM 的令牌桶 + 并发上限约束总速率。**

## 2. 目标与非目标

### 目标

- `classify` 的多个批次并发执行，显著缩短墙钟时间。
- 全部 LLM 请求（含 `_acomplete` 内的重试 / fallback）走同一个 RPM 令牌桶，默认
  1500 次/分钟，不超服务商限速。
- 加一个并发上限，避免令牌桶初始满桶造成的瞬时 thundering herd。

### 非目标

- 不把 RPM 做成 config.toml 配置项：按 CLAUDE.md「config 只放 base_url/api_key/
  model 三项凭证，其余写死常量」，1500 与并发上限作为 `_RPM` / `_MAX_INFLIGHT`
  常量，与 `_BATCH_SIZE` / `_TEMPERATURE` 同级。
- 不改变缓存语义、URL 完整性硬保证、CLI 选项、输出格式。
- 不并行 `discover`（只有 1 次调用，无需限流 / 并发）。

## 3. 设计

### 3.1 限流：令牌桶

用成熟库 **`aiolimiter`**（`AsyncLimiter`），契合「优先成熟库」约定，而非手搓。

```python
_RPM = 1500          # 每分钟最大 LLM 请求数（令牌桶）
_MAX_INFLIGHT = 16   # 最大并发在途请求数
```

`AsyncLimiter(_RPM, 60)` 表示「每 60 秒补满 1500 个令牌」。

**令牌在哪里取？** 在 `_acomplete` 内、真正发出每个 HTTP 请求处（`_raw`）`async
with limiter`。这样**每个真实请求**（首次 + schema fallback + 重试）都各扣一个令牌，
RPM 计数精确，而不是按批次粗略计。

`_acomplete` 接受可选 `limiter: AsyncLimiter | None`；`None` 时不限流（`discover`
的单次调用走这条路）。

### 3.2 并发：信号量 + gather

`AsyncLimiter(1500, 60)` 初始是**满桶**，t=0 可瞬间放行上千请求 → 惊群。故再加一个
`asyncio.Semaphore(_MAX_INFLIGHT)` 限制同时在途的批数。两者职责分明：

- 令牌桶：限制**长期速率**（≤1500/min）。
- 信号量：限制**瞬时并发**（≤16 个批同时在飞）。

`classify_entries` 内把串行循环改为：

```python
limiter = AsyncLimiter(_RPM, 60)
sem = asyncio.Semaphore(_MAX_INFLIGHT)

async def run(n, batch):
    async with sem:
        return await classify_batch(batch, f"{n}/{len(batches)}", limiter)

for r in await asyncio.gather(*(run(n, b) for n, b in enumerate(batches, 1))):
    assigned.update(r)
```

信号量持有覆盖整批（含其重试），约束并发批数；令牌桶在更细的请求粒度计速率。

### 3.3 并发安全

- `CallStats.good/bad += 1`：asyncio 单线程，`+=` 间无 await，原子，安全。
- `cache.put`：diskcache 进程/线程安全（同步调用），并发 coroutine 调用安全。
- `err.print` 进度行会交错输出，可接受（仅日志）。
- `assigned.update`：gather 保序返回，按批顺序合并；`build_document` 本就按原始
  entry 顺序装桶，输出可复现，不受合并顺序影响。

## 4. 正确性与风险

- **URL 完整性**：并发只改变调用顺序，不触碰 URL；`_assert_urls_preserved` 照常在
  其后执行，硬保证不变。
- **重试放大**：单批最坏 ~6 请求（3 retry × (schema + json fallback)）。令牌桶按真实
  请求计，重试也受限，不会突破 RPM。
- **满桶惊群**：由 `_MAX_INFLIGHT` 信号量兜底。

## 5. 验收标准

- `apply` 对多批输入并发执行，墙钟时间显著低于串行（日志中多个 "classifying batch"
  near-simultaneously 出现）。
- 人为压低 `_RPM`（如 60）可观察到请求被节流（速率受限）。
- URL 完整性检查通过；命中率 / 成功率统计仍正确。
- `--no-cache` 与缓存路径行为不变；`just lint` 干净。

## 6. 待 PLAN 细化的实现点

- `_acomplete` 增加 `limiter` 形参并在 `_raw` 内 `async with`。
- `classify_batch` 增加 `limiter` 形参并透传。
- `classify_entries` 串行循环改 gather + semaphore + limiter。
- 新增 `_RPM` / `_MAX_INFLIGHT` 常量；`aiolimiter` 入依赖。
