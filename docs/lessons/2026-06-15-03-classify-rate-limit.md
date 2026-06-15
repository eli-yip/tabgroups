# LESSON：classify 并发 + RPM 限流

- 日期：2026-06-15
- 分支：`feat-classify-rate-limit`
- 对应 SPEC/PLAN：[spec](../specs/2026-06-15-03-classify-rate-limit.md) ·
  [plan](../plans/2026-06-15-03-classify-rate-limit.md)

## 核心结论

**RPM 限流和并发是一对，单独加限流是空操作。** 原 `classify_entries` 串行逐批
`await`，任意时刻仅一个在途请求；1500 RPM = 25 req/s，而单批要数秒，永远碰不到上限。
只有先并发（`asyncio.gather`）把吞吐拉上去，令牌桶才有约束对象。

## 关键决定

- **令牌在请求粒度取，不在批粒度。** `_acomplete` 内 `_raw` 每次真正发请求前
  `await limiter.acquire()`，于是首次尝试、schema→json_object fallback、每次 retry
  都各扣一令牌，RPM 计数精确；若在批粒度只扣一次，重试放大会突破上限。
- **令牌桶 + 信号量，职责分离。** `AsyncLimiter(_RPM, 60)` 限长期速率；但它初始**满
  桶**，t=0 可瞬放上千请求 → 惊群。故再加 `asyncio.Semaphore(_MAX_INFLIGHT)` 限瞬时
  并发批数。一个管速率、一个管并发，缺一不可。
- **1500 写死常量，不进 config。** 遵 CLAUDE.md「config 只放三项凭证」，`_RPM` /
  `_MAX_INFLIGHT` 与 `_BATCH_SIZE` 同级。代价：换服务商档位要改源码——可接受。
- **`discover` 不动。** 单次调用，无并发、无需限流，`_acomplete` 的 `limiter` 默认
  `None` 直发。

## 并发安全（已确认无虞）

asyncio 单线程：`CallStats.good += 1` 间无 await，原子；`cache.put`（diskcache）并发
安全；`gather` 保序返回，`assigned.update` 顺序无关，`build_document` 仍按原始 entry
顺序装桶，输出可复现。URL 完整性硬保证不受影响——并发只改调用顺序，不碰 URL。

## 验证

`apply --no-cache`（383 tab / 13 批）：13 条 "classifying batch" 几乎同时打印，确认
并发；`13/13 ok`、`384 URLs preserved, 0 fabricated` 通过。`just lint` 干净。
