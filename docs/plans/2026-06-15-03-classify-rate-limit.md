# PLAN：classify 并发 + RPM 限流

- 日期：2026-06-15
- 分支：`feat-classify-rate-limit`
- 对应 SPEC：[2026-06-15-03-classify-rate-limit](../specs/2026-06-15-03-classify-rate-limit.md)

## 步骤

1. **依赖**：`uv add aiolimiter`，进 `[project.dependencies]`。（已完成）

2. **常量**：在 classify.py 的「固定旋钮」区加
   - `_RPM = 1500`（每分钟最大 LLM 请求数）
   - `_MAX_INFLIGHT = 16`（最大并发在途请求数）

3. **`_acomplete` 接入令牌桶**：
   - 形参增加 `limiter: AsyncLimiter | None = None`。
   - 在内部 `_raw` 真正发请求处 `async with limiter`（None 时直发）。

4. **`classify_batch` 透传**：形参增加 `limiter`，调用 `_acomplete(..., limiter=limiter)`。

5. **`classify_entries` 改并发**：
   - 建 `limiter = AsyncLimiter(_RPM, 60)` 与 `sem = asyncio.Semaphore(_MAX_INFLIGHT)`。
   - 串行 `for` 改为信号量包裹的 `run(n, batch)` + `asyncio.gather`，结果按序
     `assigned.update`。

6. **验证**：`just lint` 干净；`just run classify apply` 跑真实数据，确认并发可见、
   URL 完整性通过、统计正确。

7. **文档**：写 LESSON，更新 `docs/PROGRESS.md`。

8. **评审**：请作者评审，批准后 squash-merge 进 master 并删分支。
