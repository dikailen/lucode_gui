# Lucode Context Selection And SQLite Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 Lucode 的上下文取舍核心机制，并在结构稳定后接入 SQLite 主存储，让模型输入可控、历史可完整查看、审计证据不丢失、后续 RAG 有稳定的数据基础。

**Architecture:** 先实现纯 Python 的 Context Selection Core，明确模型最终吃什么；再做 SQLite Context Store，承载会话、摘要、工具脱水结果、证据引用和上下文账本。JSONL 在过渡期继续保留为审计和兼容层，RAG / 向量库放到 SQLite + FTS5 稳定之后再接入。

**Tech Stack:** Python, SQLite, sqlite3, JSONL compatibility, FTS5, existing `runtime/context/*`, `runtime/history/*`, `runtime/server/*`, `runtime/execution/*`, pytest.

---

## 1. 核心结论

不要先上数据库。先做上下文取舍方法，再接 SQLite。

原因：

- 数据库解决“存在哪里”，上下文取舍解决“什么值得存、什么进 prompt、什么只进审计”。
- 如果先建库，容易把当前 JSONL 结构照搬到 SQLite，后续 Context Ledger 字段稳定后又要改 schema。
- 当前最大风险不是历史查不到，而是长对话、工具说明、工具结果、证据摘要互相挤占 prompt。
- SQLite 应该承载已经确定的数据模型，而不是替代上下文策略本身。

推荐顺序：

```text
Context Selection Core v1
  -> ContextCompressionMiddleware
  -> Electron Runtime 接入
  -> SQLite Context Store v1
  -> SQLite FTS5 搜索
  -> RAG / 向量索引
```

---

## 2. 当前项目基础

已有能力：

- `runtime/context/compaction.py`
  - 已有规则压缩：保留最近消息，旧消息折成摘要。
  - 已有敏感信息 redaction。
- `runtime/context/semantic_compaction.py`
  - 已有语义压缩入口。
  - 默认长内容达到约 `16000` 字符才尝试。
- `runtime/common/conversation.py`
  - 已有 `compose_recent_context()`，能拼最近轮次和 session summary。
- `runtime/history/store.py`
  - 已有完整历史 JSONL、`load_context_summary()`、`load_recent_turns()`。
- `runtime/sessions/store.py`
  - 每条消息最多保存约 `20000` 字符，适合用户可见历史。
- `runtime/execution/run_context.py`
  - 本轮运行黑板、文件快照、工具输出已有数量和长度限制。
- `runtime/server/execution_bridge.py`
  - Electron Runtime 现在直接把 `request.user_input` 交给 `KernelFacade.run_once()`。

主要缺口：

- 没有统一 token 预算。
- 没有按工具 schema / system prompt / evidence / user history 分配预算。
- 没有动态滑动窗口。
- 没有统一 Context Ledger 结果对象。
- 没有工具输出脱水标准。
- 没有 SQLite 主存储。
- Electron、CLI、旧 PySide 还没有统一上下文入口。

---

## 3. 上下文取舍方法设计

### 3.1 动态滑动窗口

目标：保留最近 N 轮原始对话，但 N 不是固定值，而是由剩余 token 预算决定。

预算优先级：

```text
system / developer instructions
  > safety / approval / evidence rules
  > tool schemas / MCP capability descriptions
  > current user input
  > current run state / accepted evidence
  > recent raw turns
  > session summary
  > project memory
  > inline file snippets
  > cold RAG snippets
```

默认策略：

```text
普通直接回答:
  最近 6 轮原文

工具较少:
  最近 4-6 轮原文

工具很多或 MCP schema 很大:
  最近 2-4 轮原文

高风险任务:
  优先保留审批规则、accepted evidence、当前输入，压缩历史原文

接近硬限制:
  最近 2 轮原文 + 最短任务状态摘要
```

必须满足：

- 不能让历史对话把系统指令和工具说明顶掉。
- 不能让工具 schema 把当前用户输入顶掉。
- 当前用户输入永远完整保留。

### 3.2 层级化总结

历史不直接塞完整原文，而是分层：

```text
Recent Raw Turns
  最近原文，保证自然连贯。

Session Summary
  会话滚动摘要，保留旧历史主线。

Task State Summary
  当前任务状态：目标、决策、未完成项、阻塞原因、下一步。

Failure Lessons / Project Experience
  项目经验和失败教训，由 MemoryResolver 控制注入。
```

关键设计：

- 会话摘要不能替代当前任务状态摘要。
- 当前任务状态不能只放向量库，必须进入 Context Ledger。
- 摘要本身也要受预算控制，过长后递归压缩。

### 3.3 RAG 外部存储回旋

RAG 不作为第一阶段主链路。

适合进入冷存储 / RAG 的内容：

- 历史项目经验。
- 长文档片段。
- Skill 说明和用法。
- 插件说明。
- ComfyUI 工作流说明。
- 过往失败教训。

不适合只靠 RAG 的内容：

- 当前用户输入。
- 当前 run 的任务目标。
- 审批状态。
- 高风险操作证据。
- accepted evidence。
- 当前任务未完成项。

冷热分层：

```text
Hot:
  current_input
  recent_turns
  task_state_summary
  approval/evidence state

Warm:
  session_summary
  run_blackboard
  accepted_evidence
  selected project memories

Cold:
  archived history
  long docs
  skill library
  plugin docs
  old failure lessons
```

### 3.4 Token 剪枝与工具结果脱水

工具输出不能原样长期塞回 prompt。

脱水原则：

```text
原始工具输出:
  进入审计、artifact、evidence ref，不直接长期进入 prompt。

模型上下文:
  只保留 core result、关键字段、错误原因、路径、状态、evidence id。

长期记忆:
  只保存可复用结论，不保存冗余 JSON、DOM、日志噪音。
```

典型处理：

- 搜索结果：保留标题、URL、摘要、来源，不保留完整 JSON。
- 浏览器摘要：保留 URL、title、关键文本、可操作元素摘要，不保留完整 DOM。
- 终端输出：保留命令、returncode、关键 stdout/stderr 尾部，不保留全量日志。
- MCP 返回：保留业务结果字段、状态、错误，不保留协议包装字段。
- 文件读取：保留路径、sha256、相关片段，不保留整文件，除非任务明确需要。

重要边界：

- 给模型看的内容可以脱水。
- 给证据门和审计看的原始证据引用不能丢。
- P4 Evidence Gate 不能因为上下文压缩而失去证据来源。

---

## 4. SQLite 存储策略

### 4.1 为什么选择 SQLite

SQLite 适合当前 Lucode：

- 本地桌面 App，不需要额外服务。
- 易打包。
- 可在 `.lucode/lucode.db` 中按 workspace 存储。
- 可配合 WAL 模式支持稳定读写。
- 可用 FTS5 做全文搜索。
- 迁移成本比 Postgres / Qdrant / Mongo 低。

### 4.2 JSONL 的角色

不要立刻删除 JSONL。

过渡期：

```text
JSONL:
  append-only 原始历史
  审计备份
  导出格式
  兼容旧会话

SQLite:
  查询主存储
  Context Ledger
  session summary
  run snapshots
  tool dehydrated results
  evidence refs
```

### 4.3 SQLite 最小表

第一版只建必须表，不做插件市场大库。

```sql
sessions(
  session_id text primary key,
  title text not null,
  created_at text not null,
  updated_at text not null,
  source text not null default 'sqlite'
)

messages(
  message_id text primary key,
  session_id text not null,
  role text not null,
  content text not null,
  created_at text not null,
  metadata_json text not null default '{}'
)

session_summaries(
  summary_id text primary key,
  session_id text not null,
  summary text not null,
  mode text not null,
  source_message_start text,
  source_message_end text,
  created_at text not null,
  metadata_json text not null default '{}'
)

context_ledger_runs(
  ledger_id text primary key,
  run_id text not null,
  session_id text not null,
  mode text not null,
  estimated_input_tokens integer not null,
  context_window_tokens integer not null,
  triggered integer not null,
  run_input_preview text not null,
  metadata_json text not null default '{}',
  created_at text not null
)

tool_result_summaries(
  result_id text primary key,
  run_id text not null,
  task_id text not null,
  tool text not null,
  action text not null,
  summary text not null,
  evidence_ref text,
  raw_artifact_ref text,
  created_at text not null,
  metadata_json text not null default '{}'
)

evidence_refs(
  evidence_id text primary key,
  run_id text not null,
  task_id text,
  kind text not null,
  source text not null,
  event_seq integer,
  sha256 text,
  excerpt text,
  metadata_json text not null default '{}',
  created_at text not null
)
```

FTS5 后续再加：

```sql
messages_fts(content, title, session_id UNINDEXED)
summaries_fts(summary, session_id UNINDEXED)
```

---

## 5. 修改点与链路影响预估

| 修改点 | 影响链路 | 可能问题 | 风险等级 | 控制方式 |
|---|---|---:|---:|---|
| 新增 token budget | Planner / Worker / Final 输入 | 估算不准导致过早压缩或过晚压缩 | 中 | 阈值保守；测试覆盖 60/72/88/95 |
| 动态滑窗替代固定最近轮次 | CLI / PySide / Electron 连续对话 | 用户感觉上下文断裂 | 中 | 当前输入永远保留；最近 2 轮最低保留 |
| 引入 session summary | Planner 输入 | 摘要污染当前任务 | 高 | 摘要明确标注“背景不是本轮任务”；当前输入放最后 |
| 工具结果脱水 | Evidence Gate / LeadReview / Final | 原始证据丢失，审计无法复核 | 高 | prompt 用脱水结果，evidence refs 保留原始 artifact 引用 |
| Context Middleware 接入 Electron | Runtime Server / WebSocket / history | run input 变长、前端结果不一致 | 中 | routing_input 保持原始用户问题；metadata 记录 ledger |
| SQLite 双写 | History / Sessions / Runtime Server | SQLite 写失败导致 run 失败 | 高 | 第一阶段 SQLite 写失败降级 JSONL，不阻断 run |
| SQLite 读路径切换 | 历史列表 / 会话恢复 | 旧 JSONL 会话读不到 | 高 | 先 SQLite + JSONL fallback，迁移完成前不删 JSONL |
| FTS5 搜索 | 历史搜索 / skill 搜索 | 中文分词效果一般 | 中 | 第一版用 simple token + like fallback，后续再优化 |
| RAG 冷存储 | Memory / Skill resolver / Context Ledger | 召回不准导致上下文缺失 | 高 | RAG 不承载当前任务状态，只做背景补充 |
| 数据库锁 | Electron 多请求 / terminal / run events | UI 卡顿或写入冲突 | 中 | WAL、短事务、单写入口、失败重试 |
| 隐私与脱敏 | Provider key / browser / terminal | 敏感内容写入摘要或向量库 | 高 | redaction 在写摘要、脱水、embedding 前执行 |

---

## 6. 风险分级与回滚策略

### 6.1 高风险点

#### 风险 A：摘要污染当前任务

表现：

- 用户本轮要求 A，模型被旧摘要带去做 B。
- Planner 把旧任务当成本轮任务继续执行。

控制：

- `current_input` 单独放在 run input 最后。
- 摘要段落必须写明：“以下是历史背景，不是本轮新任务。”
- Planner prompt 保持“当前用户问题优先”。
- 测试覆盖：旧摘要包含旧任务，新输入要求直接回答，不能触发旧任务工具。

#### 风险 B：工具脱水削弱 Evidence Gate

表现：

- Final 只能看到脱水摘要，但没有 evidence ref。
- LeadReview 无法复查原始工具输出。

控制：

- `ToolDehydratedResult` 必须包含 `evidence_ref` 或 `raw_artifact_ref`。
- `accepted_evidence_packet()` 继续只消费 accepted evidence。
- 原始工具输出可以不进 prompt，但必须可由 evidence ref 追踪。

#### 风险 C：数据库写入破坏现有历史

表现：

- 新消息写 SQLite 成功但 JSONL 失败，或反过来。
- 历史列表显示重复或缺失。

控制：

- 第一阶段 JSONL 仍是事实源。
- SQLite 作为索引和缓存，写失败不阻断 run。
- 每条 SQLite 写入带 source 和 schema_version。
- 提供重建索引命令，从 JSONL 重新生成 SQLite。

#### 风险 D：RAG 过早接入导致不确定性

表现：

- 该召回的当前任务状态没召回。
- 召回旧经验误导 planner。

控制：

- RAG 只做 cold context。
- 当前任务状态、审批、证据永远不只放向量库。
- RAG 结果必须带 source、score、reason。

### 6.2 回滚开关

建议新增：

```text
LUCODE_CONTEXT_LEDGER=off|observe|enforce
LUCODE_CONTEXT_SQLITE=off|dual_write|read_through|primary
LUCODE_TOOL_DEHYDRATION=off|observe|enforce
LUCODE_CONTEXT_FTS=off|on
LUCODE_CONTEXT_RAG=off|observe|on
```

默认建议：

```text
LUCODE_CONTEXT_LEDGER=observe
LUCODE_CONTEXT_SQLITE=off
LUCODE_TOOL_DEHYDRATION=observe
LUCODE_CONTEXT_FTS=off
LUCODE_CONTEXT_RAG=off
```

---

## 7. 实施阶段

### Phase 1: Context Core v1

目标：先确定模型输入取舍，不碰数据库。

**Files:**

- Create: `runtime/context/token_counter.py`
- Create: `runtime/context/budget.py`
- Create: `runtime/context/ledger.py`
- Create: `runtime/context/tool_dehydration.py`
- Create: `runtime/context/middleware.py`
- Modify: `runtime/context/__init__.py`
- Test: `tests/test_context_token_counter.py`
- Test: `tests/test_context_budget.py`
- Test: `tests/test_context_ledger.py`
- Test: `tests/test_tool_dehydration.py`
- Test: `tests/test_context_middleware.py`

- [ ] **Step 1: 写 token 估算测试**

Run:

```powershell
python -m pytest tests/test_context_token_counter.py -q
```

Expected before implementation:

```text
ModuleNotFoundError: No module named 'runtime.context.token_counter'
```

- [ ] **Step 2: 实现 token 估算**

要求：

```python
estimate_tokens(text: str) -> int
context_window_for_model(model_info: dict | None) -> int
```

默认 `context_window_tokens=32768`，优先读取：

```text
context_window_tokens
context_length
max_context_tokens
max_input_tokens
```

- [ ] **Step 3: 写预算决策测试**

覆盖：

```text
normal < 60%
warning >= 60%
compress >= 72%
emergency >= 88%
hard_limit >= 95%
```

- [ ] **Step 4: 实现 ContextBudgetDecision**

输出字段：

```python
mode: str
triggered: bool
keep_messages: int
max_summary_chars: int
max_inline_file_chars: int
max_memory_entries: int
reserved_tokens: dict[str, int]
reasons: list[str]
```

- [ ] **Step 5: 写 ContextLedger 测试**

必须验证：

- 当前用户输入完整保留。
- 最近轮次按预算缩减。
- session summary 被截断但不消失。
- inline files 在紧急模式被缩短。
- run input 明确区分历史背景和本轮任务。

- [ ] **Step 6: 实现 ContextLedger**

输入：

```python
ContextLedgerInput(
    session_id,
    current_input,
    messages,
    existing_summary,
    project_experience,
    run_blackboard,
    inline_files,
    tool_schema_tokens,
    evidence_tokens,
    model_id,
    context_window_tokens,
)
```

输出：

```python
ContextLedgerResult(
    run_input,
    mode,
    triggered,
    recent_turns,
    session_summary,
    estimated_input_tokens,
    context_window_tokens,
    compression_reasons,
    dropped_sections,
)
```

- [ ] **Step 7: 写工具脱水测试**

覆盖：

- browser summary。
- terminal output。
- command output。
- MCP JSON。
- search result。
- file snapshot。

断言：

```text
原始冗余 JSON 不进入 summary
核心 result 保留
evidence_ref 保留
raw_artifact_ref 保留
敏感 token 被 redacted
```

- [ ] **Step 8: 实现 ToolDehydrator**

输出：

```python
ToolDehydratedResult(
    summary,
    key_fields,
    evidence_ref,
    raw_artifact_ref,
    omitted_fields,
    redacted,
)
```

- [ ] **Step 9: 跑 Phase 1 测试**

Run:

```powershell
python -m pytest tests/test_context_token_counter.py tests/test_context_budget.py tests/test_context_ledger.py tests/test_tool_dehydration.py tests/test_context_middleware.py -q
```

Expected:

```text
all passed
```

### Phase 2: Electron Runtime 接入

目标：让真实桌面 App 连续问答使用 Context Middleware，但不改变路由判断。

**Files:**

- Modify: `runtime/server/execution_bridge.py`
- Modify: `runtime/server/run_manager.py`
- Modify: `runtime/server/schemas.py`
- Test: `tests/test_runtime_server_context_ledger.py`
- Test: `tests/test_runtime_browser_route_trace.py`

- [ ] **Step 1: 扩展 `RunExecutionRequest`**

新增可选字段：

```python
history_facade: Any | None = None
model_info: dict[str, Any] | None = None
routing_input: str = ""
```

- [ ] **Step 2: RuntimeRunManager 传入 history**

构造 run request 时传：

```python
history_facade=self._history_facade
routing_input=user_input
```

- [ ] **Step 3: KernelAgentLoopExecutor 生成 ledger**

调用：

```python
ledger = ContextCompressionMiddleware(history=request.history_facade).prepare_run_input(
    session_id=request.session_id,
    user_input=request.user_input,
    model_info=request.model_info or {},
)
```

然后：

```python
KernelFacade(context).run_once(
    ledger.run_input,
    routing_input=request.routing_input or request.user_input,
    ...
)
```

关键约束：

- `run_input` 给模型。
- `routing_input` 保持原始用户问题。
- 不能因为历史摘要里有“浏览器”就误触发 desktop_browser。

- [ ] **Step 4: 写 Electron 连续问答测试**

测试：

```text
第一轮：问 “你好”
第二轮：问 “继续解释”
断言第二轮 run_input 包含最近对话或 summary
断言 routing_input 仍然是第二轮原始问题
```

- [ ] **Step 5: 写路由污染测试**

测试：

```text
历史摘要包含 “用内置浏览器打开 https://example.com”
本轮用户输入是 “你好”
断言不绑定 desktop_browser
```

- [ ] **Step 6: 跑 Phase 2 测试**

Run:

```powershell
python -m pytest tests/test_runtime_server_context_ledger.py tests/test_runtime_browser_route_trace.py tests/test_runtime_server.py -q
```

Expected:

```text
all passed
```

### Phase 3: SQLite Context Store v1

目标：引入 SQLite，但先不让它成为唯一事实源。

**Files:**

- Create: `runtime/storage/sqlite_store.py`
- Create: `runtime/storage/schema.sql`
- Create: `runtime/storage/context_store.py`
- Modify: `runtime/history/store.py`
- Modify: `runtime/server/run_manager.py`
- Test: `tests/test_sqlite_context_store.py`
- Test: `tests/test_history_jsonl_sqlite_compat.py`

- [ ] **Step 1: 写 SQLite 初始化测试**

断言：

- `.lucode/lucode.db` 可创建。
- schema_version 可读取。
- WAL 可启用。
- 重复初始化幂等。

- [ ] **Step 2: 实现 SQLiteConnectionFactory**

要求：

```python
connect(workspace_root) -> sqlite3.Connection
PRAGMA journal_mode=WAL
PRAGMA foreign_keys=ON
PRAGMA busy_timeout=3000
```

- [ ] **Step 3: 写 context store 测试**

覆盖：

- insert session。
- insert message。
- insert summary。
- insert context ledger result。
- insert tool dehydrated result。
- insert evidence ref。
- list recent sessions。
- load messages with JSONL fallback。

- [ ] **Step 4: 实现 ContextSQLiteStore**

第一版只提供窄接口：

```python
save_session(session)
save_message(message)
save_context_summary(summary)
save_context_ledger_result(result)
save_tool_result_summary(result)
save_evidence_ref(ref)
load_messages(session_id, limit=None)
load_latest_summary(session_id)
```

- [ ] **Step 5: 接入 dual-write**

规则：

```text
JSONL 写入仍然是主路径。
SQLite 写入失败只记录 warning，不阻断 run。
SQLite 不参与关键执行判断。
```

- [ ] **Step 6: 跑 Phase 3 测试**

Run:

```powershell
python -m pytest tests/test_sqlite_context_store.py tests/test_history_jsonl_sqlite_compat.py tests/test_runtime_server.py -q
```

Expected:

```text
all passed
```

### Phase 4: SQLite Read-Through 与索引重建

目标：SQLite 可以作为查询加速层，但 JSONL 仍可兜底。

**Files:**

- Modify: `runtime/history/store.py`
- Create: `runtime/storage/rebuild_index.py`
- Test: `tests/test_sqlite_rebuild_index.py`

- [ ] **Step 1: 写索引重建测试**

给定旧 JSONL 会话，运行 rebuild 后：

- sessions 表有记录。
- messages 表有记录。
- summaries 表有记录。
- 历史列表与 JSONL 一致。

- [ ] **Step 2: 实现 rebuild_index**

命令入口暂时只做函数：

```python
rebuild_sqlite_from_jsonl(workspace_root: Path) -> RebuildResult
```

- [ ] **Step 3: HistoryFacade read-through**

读取顺序：

```text
SQLite 可用且记录存在 -> SQLite
否则 -> JSONL
```

- [ ] **Step 4: 跑 Phase 4 测试**

Run:

```powershell
python -m pytest tests/test_sqlite_rebuild_index.py tests/test_history_jsonl_sqlite_compat.py -q
```

Expected:

```text
all passed
```

### Phase 5: FTS5 搜索

目标：先用全文搜索解决大部分历史和 skill 检索，不急着上向量库。

**Files:**

- Modify: `runtime/storage/schema.sql`
- Create: `runtime/storage/search.py`
- Test: `tests/test_sqlite_fts_search.py`

- [ ] **Step 1: 写 FTS 搜索测试**

覆盖：

- 搜会话标题。
- 搜用户问题。
- 搜 assistant 回答。
- 搜 summary。
- 删除会话后搜索结果消失。

- [ ] **Step 2: 实现 FTS 表和触发器**

优先简单可靠：

```sql
CREATE VIRTUAL TABLE messages_fts USING fts5(session_id UNINDEXED, role UNINDEXED, content);
CREATE VIRTUAL TABLE summaries_fts USING fts5(session_id UNINDEXED, summary);
```

- [ ] **Step 3: 实现 search API**

```python
search_history(query: str, limit: int = 20) -> list[SearchResult]
```

- [ ] **Step 4: 跑 Phase 5 测试**

Run:

```powershell
python -m pytest tests/test_sqlite_fts_search.py -q
```

Expected:

```text
all passed
```

### Phase 6: RAG 冷存储准备

目标：只定义接口和数据边界，不立即引入向量库。

**Files:**

- Create: `runtime/context/cold_store.py`
- Create: `runtime/context/retrieval_policy.py`
- Test: `tests/test_context_cold_store_policy.py`

- [ ] **Step 1: 写 cold store policy 测试**

断言：

- 当前任务状态不允许进入 cold-only。
- accepted evidence 不允许只放 RAG。
- skill docs、plugin docs、old failure lessons 可以进入 cold store。
- secret / browser_dom / terminal_output 默认不进入 embedding。

- [ ] **Step 2: 实现 RetrievalPolicy**

字段：

```python
source_type
sensitivity
allowed_for_embedding
allowed_for_prompt
requires_redaction
reason
```

- [ ] **Step 3: 跑 Phase 6 测试**

Run:

```powershell
python -m pytest tests/test_context_cold_store_policy.py tests/test_context_source_labels.py -q
```

Expected:

```text
all passed
```

---

## 8. 验收标准

### 8.1 上下文取舍

- 当前用户输入完整保留。
- 工具多时最近对话窗口自动缩小。
- 系统指令、工具说明、审批规则不会被历史顶掉。
- 历史摘要明确标注为背景。
- 摘要过长会递归压缩。
- 工具结果进入 prompt 前会脱水。

### 8.2 Electron 连续问答

- 同一会话第二轮能看到第一轮的最近原文或摘要。
- 历史摘要不会污染路由。
- “你好” 不会因为历史提到浏览器而触发 desktop_browser。
- 显式内置浏览器请求仍然能触发 desktop_browser。

### 8.3 SQLite

- JSONL 旧历史仍可读。
- SQLite 写失败不阻断 run。
- 可从 JSONL 重建 SQLite 索引。
- FTS5 搜索能查 messages 和 summaries。

### 8.4 Evidence 与审计

- 脱水结果保留 evidence_ref。
- raw_artifact_ref 可追踪。
- Final 只消费 accepted evidence。
- Evidence Gate 测试继续通过。

### 8.5 回归测试

每阶段至少跑：

```powershell
python -m pytest tests/test_context_*.py -q
python -m pytest tests/test_runtime_server.py tests/test_runtime_browser_route_trace.py -q
python -m pytest tests/test_evidence_gate.py tests/test_lead_review_evidence_gate.py -q
```

关键阶段跑全量：

```powershell
python -m pytest -q
```

---

## 9. 不建议做的事

1. 不建议先做数据库再设计 Context Ledger。
2. 不建议一开始就接 Qdrant。
3. 不建议让 RAG 承载当前任务状态。
4. 不建议把工具原始 JSON 长期塞回 prompt。
5. 不建议删除现有 JSONL 历史。
6. 不建议把 SQLite 写失败作为 run 失败。
7. 不建议把摘要当成事实源；摘要只是模型输入优化层。

---

## 10. 自审结果

- 已覆盖用户提出的四个上下文取舍方法：
  - 动态滑动窗口。
  - 层级化总结。
  - RAG 外部存储回旋。
  - Token 剪枝与工具结果脱水。
- 已明确数据库推进顺序：先 Context Core，再 SQLite。
- 已标记高风险点和链路影响。
- 已写出回滚开关。
- 已区分 prompt 上下文、审计证据、历史事实源。
- 已避免把向量库作为当前任务状态的唯一来源。
