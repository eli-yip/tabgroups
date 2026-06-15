# LESSON：classify 可读性重构

- 日期：2026-06-15
- 方式：直接在 `master` 上小步提交（非 spec-first；起因是一次可读性评审，逐条落地）
- 关联提交：`a6b3a91` · `7cde01c` · `c687c92` · `d4f02f6` · `48f3c9f` · `d1ca7a3`
  （前置 `337cb56` 已先把 `config` / `models` 拆出独立模块）

## 背景

`classify.py` 一度膨胀到 737 行。先按职责拆出 `config.py`（`LLMSettings`/`load_settings`）
和 `models.py`（pydantic 模型），再做一轮纯可读性打磨。本篇记录后一轮的 7 项改动与其背后
的原则。重构后 `classify.py` 约 530 行，且 `export` 的输出逻辑一并收敛。

## 贯穿全程的原则

- **在做出判定的地方直接数你测量的量；聚合量才用派生。** 原 `duplicates = total −
  classifiable − noise` 是"用它不是什么来定义它"，且 `total += 1` 与重复判定分散在不同
  位置。改为在 `if url in seen: duplicates += 1` 处直接计数，`total` 反过来作 `@property`
  派生（三桶之和）。`total = a+b+c` 是人脑天然接受的加总；`x = total−a−b` 是别扭的反解。

- **同一份"知识"只能有一个家。** "格式 → 渲染器 → 流"的分派原本散在三处（classify 文件
  输出、classify stdout、export）。逐层收敛到 `render.py`：`render_*`（单格式渲染）→
  `render_to`（格式→流）→ `write_all_formats`（批量写盘）→ `emit`（命令级输出编排）。
  新增格式或改输出行为只动一处，两个命令永不漂移。

- **类型即文档。** document 的形状原本只活在 `render.py` 的散文 docstring 里，靠
  `d["groups"]`/`g["tabs"]`/`t["url"]` 字符串下标盲访问。提成 `Document`/`Group`/`Tab`
  三个 `TypedDict`（`token`/`version`/`browser` 用 `NotRequired`），两个生产者
  （`export.parse`、`classify.build_document`）与全部消费者都标注它，下标访问全程受 ty 检查。

- **别让读者推断不变量。** `discover_topics` 里 `key` 被设计成"仅当 cache 非空才非空"的
  Optional，于是每处都写 `cache is not None and key is not None`。`discover_key` 只是一次
  廉价哈希——无条件算出来，只把*使用*交给 `cache` 守卫，每个调用点回到单一判断。

- **用声明式表达意图，而非让读者跑一遍副作用。** `_unwrap` 原本两步 `.replace`：第一步
  故意把 `https://` 过度替换成 `https:///`，第二步再擦回来。换成一条 `re.compile(
  r"(https?):/+")` + `sub(r"\1://", ...)`，意图（"scheme 后任意斜杠归一成 `://`"）一眼可读。

- **把长函数切成"编排 + 命名单元"。** `classify_entries`（~80 行）把建键、预扫缓存、逐批
  跑模型、兜底揉在一起。抽出嵌套闭包 `classify_batch(batch, label)`（捕获共享上下文，与已有
  的 `key_for` 闭包同一风格），主体回到清晰三步：建 `key_for` → 预扫缓存 → 循环 batch。

- **顺手消除名字复用。** 同一函数里 `for e in entries`（`Entry`）与 `except ... as e`
  （异常）混用，靠作用域才不冲突、扫读晃眼。重写该函数时一并把异常名改 `exc`。

## 决策中的取舍

- **内联闭包 vs 顶层函数。** `classify_batch` 选内联：它依赖的 `settings`/`topic_block`/
  `valid`/`cache`/`key_for` 全是 `classify_entries` 的局部，做成顶层函数要把 5 个 context
  参数串来串去，反而违背"抽函数是为了消除参数线"的初衷。与文件里既有的 `key_for` 闭包风格
  一致。

- **`NamedTuple` 而非裸三元组。** `load_entries` 返回 `LoadedEntries`：具名字段自描述，且
  能挂 `total` 派生属性，把"三桶 + 其聚合"收在数据定义处。调用点 `loaded.classifiable` /
  `loaded.duplicates` 比位置解包更清楚。

- **`render_to`/`write_all_formats` 暴露还是私有。** 它们最终只被 `emit` 调用，但作为有
  意义的可测/可复用分层单元保留为公开 building blocks（评审时确认保留）。

## 验证

无测试套件，靠 `just lint`（ruff + format + ty 全绿）加针对性的端到端对比：

- **改输出路径的两项（`render_to`/`emit`）做逐字节回归。** 用 `git stash` 在改动版与
  `HEAD` 间各跑一遍 `export` 的全部 6 种输出（4 个 stdout 格式 + `--format all` 的 4 个
  文件），`cmp` 比对——全部 IDENTICAL。classify 的 `_write_outputs`/`emit` 走同一套
  render 助手，故同样安全。
- 其余项以 `tabgroups classify --help` 冒烟确认 import 链路通 + lint 把关。

## 端到端发现的两个非回归差异

- **`_unwrap` 正则化修了一个静默 bug。** 旧链式 `.replace` 对 `https:///`（3 斜杠）输入
  净结果不变（step1 推到 4 斜杠、step2 拉回 3），导致 tldextract 解析失败、`main_domain`
  回退成整条畸形 URL。新正则把它归一成 `https://` → 正确提取出域名。现实代理是"少斜杠"
  （`https:/`），不产生此输入，故现实数据逐字节一致；这是一处顺带的修正而非回归。

- **`emit` 让 export 的 stderr 文案更准。** 统一后 export 的写盘提示从
  `wrote md/json/html/csv` 变为 `wrote tabgroups.md/json/html/csv`（点明真实文件名，与
  classify 一致）。仅 stderr 状态行变化，数据输出不变。

## 坑

- 每抽走一处实现，对应的 import 就会变成未使用，`ruff F401` 逐个拦下：classify 去掉
  `render_md/html/csv`、`sys`，export 去掉 `json`、`render_*`。逐项 lint、按提示删即可——
  这也是"改动确实收敛干净了"的信号。
- `TypedDict` 的 dict 字面量无需手动 cast：`build_document`/`parse` 里直接构造的
  `{"title":..., "url":..., "window":...}` 被 ty 按 `Tab` 推断校验通过，因此 `Tab` 不必
  在 classify 里显式 import（import 了反被 F401 判未使用）。

## 流程教训

- **每项独立提交、改前先报方案、等评审再提交。** 用户全程逐项 review。小步 + 行为可验证
  让每次评审都聚焦单一意图，回滚成本也最低。
- **可读性问题值得用第一性原理追到底，而非止于"看着别扭"。** `duplicates` 和 `_unwrap`
  表面是风格问题，深挖后分别暴露出"派生方向反了"和"一个潜在 bug"——真正的可读性改进往往
  连带把隐藏的正确性问题一并解决。
