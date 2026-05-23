# Claude Code vs PioneClaw 对比分析报告

> 生成时间: 2026-05-23
> 分析范围: Agent Loop 架构、System Prompt/Context 管理、工具系统与执行机制、测试策略与工程实践
> Claude Code 源码: `claude-code/` (~1900 TypeScript 文件)
> PioneClaw 源码: `pione/pioneclaw/`

---

## 执行摘要：Top 10 可借鉴点

| 优先级 | 借鉴点 | 对应 Phase | 实施难度 |
|--------|--------|-----------|---------|
| P0 | 将 **TokenBudget** 接入 loop 主循环（模块已实现但从未使用） | Phase 2 | 低 |
| P0 | **System Prompt 模块化重构** — 拆分为独立 Section，添加缓存边界 | Phase 3 | 低 |
| P0 | 在 Prompt 中明确指导**并行工具调用** | Phase 3 | 低 |
| P0 | **scheduler.py 完整接入 AgentLoop**，实现工具分区执行 | Phase 4 | 中 |
| P0 | **错误级联机制** — Bash 失败时取消同批次兄弟进程 | Phase 4 | 中 |
| P1 | **max_output_tokens 自动恢复** — 输出截断时自动续传（最多3次） | Phase 2 | 中 |
| P1 | **KeyRotator + LLMCallRetrier 完整接入**，对标 `withRetry.ts` | Phase 2 | 中 |
| P1 | **Deferred Tools** — MCP 工具延迟加载，通过 ToolSearchTool 按需获取 | Phase 3-4 | 中 |
| P1 | **FileEditTool 内容匹配模型** — old_string/new_string 替换 | Phase 4 | 中 |
| P2 | **Stop Hooks 接入主循环** — 每轮后异步执行记忆提取 | Phase 5 | 低 |

---

## 一、Agent Loop 架构（对比 loop.py）

### Claude Code 的核心设计

Claude Code 的 `query.ts` (~1730行) 采用 **纯函数循环 + QueryEngine 状态管理类** 的架构：

```
QueryEngine.submitMessage()
    |
    v
queryLoop() while 循环:
    1. Snip -> Microcompact -> ContextCollapse -> Autocompact
    2. 检查 blocking limit
    3. callModel() with streaming（支持 fallback 降级）
    4. 收集 assistant message + tool_use
    5. StreamingToolExecutor 执行工具（读并发/写串行）
    6. 收集 results -> 注入 attachments -> 递归下一轮
    7. 检查 maxTurns / maxBudget / tokenBudget
```

### 关键差距

| 维度 | Claude Code | PioneClaw 现状 |
|------|-------------|----------------|
| **流式工具执行** | 工具随 assistant message 流式到达即时入队，无需等待完整响应 | 先收集完整响应再处理 tool_calls |
| **上下文压缩** | 5 层管道（Snip->Microcompact->ContextCollapse->Autocompact->Reactive Compact） | 3 层（Snip->Microcompact->Compactor） |
| **压缩触发** | context_window - 13K buffer，动态计算 | 固定 30 条/5000 tokens |
| **Token 预算** | 每轮检查 90% 阈值，自动续传 | 模块已创建，**从未接入 loop** |
| **重试体系** | 10 次重试、指数退避、模型降级、持久模式 | 3 次固定延迟，Key 轮换未接入 |
| **错误恢复** | max_output_tokens 截断自动恢复（3次）、prompt_too_long 多级恢复 | emergency_compact 单次尝试 |
| **Stop Hooks** | 每轮后异步执行 memory extract / prompt suggestion | `post_turn_services` 已设计但未接入 |

### 立即行动建议

1. **接入 TokenBudget** (`app/modules/agent/token_budget.py`)
   在 `loop.py` 每轮调用 LLM 前检查预算，超 90% 时注入续传提示。

2. **接入 KeyRotator + LLMCallRetrier** (`app/modules/providers/runtime.py`、`app/modules/llm/retry.py`)
   重构 `_call_llm_stream()` 的错误处理，覆盖 401/429/502/529/prompt_too_long 等错误类型。

3. **降低 Compactor 触发阈值**
   改为基于 `context_window` 动态计算（如 `context_window * 0.7`），而非固定值。

---

## 二、System Prompt / Context 管理（对比 context.py）

### Claude Code 的 Prompt 架构

Claude Code 将 System Prompt 分为 **10+ 独立 Section**，并区分**静态内容（可缓存）**与**动态内容（每轮重算）**：

```
Static (cacheable)
|-- Simple Intro (身份声明 + 网络安全警告)
|-- Simple System (工具执行模式)
|-- Simple Doing Tasks (任务执行准则)
|-- Actions (风险操作确认)
|-- Using Your Tools (工具使用指导)
|-- Simple Tone and Style
|-- Output Efficiency
|-- SYSTEM_PROMPT_DYNAMIC_BOUNDARY  <-- 缓存边界
Dynamic (per-turn)
|-- Session-specific Guidance
|-- Memory (CLAUDE.md)
|-- Environment Info
|-- MCP Instructions
|-- Scratchpad Instructions
|-- Function Result Clearing
|-- Summarize Tool Results
```

### PioneClaw 当前问题

- 7 个 Section 混合在一起，缺少缓存边界标记
- 工具使用策略分散在"执行铁律"中，不如 Claude Code 的 "Using Your Tools Section" 清晰
- **缺少并行工具调用的明确指导**
- **缺少风险操作确认的独立 Section**
- **反虚假声明已有但可更具体**

### 立即行动建议

1. **重构 context.py 为模块化 Section**
   将 `_build_tools_section()` 拆分为：
   - `UsingYourToolsSection`（含并行调用指导）
   - `ActionsSection`（风险操作确认）
   - `OutputEfficiencySection`（输出效率）

2. **增强反虚假声明表述**（直接借鉴）：
   > "Report outcomes faithfully: if tests fail, say so with the relevant output; if you did not run a verification step, say that rather than implying it succeeded. Never claim 'all tests pass' when output shows failures."

3. **添加并行工具调用指导**：
   > "You can call multiple tools in a single response. If you intend to call multiple tools and there are no dependencies between them, make all independent tool calls in parallel."

4. **添加 Prompt Caching 边界标记**（为 Claude 模型优化）：
   在静态内容后插入 `SYSTEM_PROMPT_DYNAMIC_BOUNDARY`，静态部分使用 `cache_control: { type: "ephemeral" }`。

### Context 管理的进阶差距

| Claude Code 功能 | PioneClaw 状态 | 建议 |
|-----------------|---------------|------|
| **Deferred Tools** | 无 | MCP 工具默认延迟加载，通过 ToolSearchTool 按需获取 schema |
| **MicroCompact 时间触发** | 无 | 距上次 assistant 消息超 5 分钟时自动清除旧工具结果 |
| **Session Memory Compact** | 无 | 用结构化 session-memory 文件替代部分对话历史 |
| **Post-Compact 恢复** | 只有文件恢复 | 恢复 skills、plan file、async agent 状态 |
| **上下文可视化** | JSON 数据 | `/context` 命令展示 token 分布 |

---

## 三、工具系统与执行机制（对比 tools/）

### Claude Code 的核心亮点

#### 1. 工具分区执行（`toolOrchestration.ts`）

```typescript
// 策略：连续多个 isConcurrencySafe=true -> 并发批次
//       单个 isConcurrencySafe=false -> 独占串行批次
```

- `isConcurrencySafe` 默认 `false`（fail-closed）
- Read-only 工具（Read/Glob/Grep）标记为 `true`
- Bash 的并发安全动态判断：`isReadOnly(input)` — 只读命令可并发

#### 2. 错误级联（`StreamingToolExecutor.ts`）

- **Bash 错误 -> 取消同批次其他 Bash 工具**（避免无意义的后续命令）
- **非 Bash 错误 -> 隔离**（Read/WebFetch 失败不影响其他工具）
- 被取消的工具收到合成错误消息 `"Cancelled: parallel tool call Bash(xxx) errored"`

#### 3. FileEditTool 设计

- **old_string / new_string 内容匹配**（非行号定位）
- `findActualString()` 处理引号规范化
- 文件新鲜度检查（读取时间戳 vs 修改时间戳）
- LSP 集成（编辑后自动 `didChange` + `didSave`）

#### 4. BashTool 设计

- AsyncGenerator 流式输出
- 后台任务（15s 后自动转后台）
- 命令语义分类（`isSearchOrReadBashCommand()`）
- 大输出持久化（超阈值写入 `tool-results/` 目录）

### PioneClaw 当前差距

| 维度 | Claude Code | PioneClaw |
|------|-------------|-----------|
| **并发调度** | StreamingToolExecutor 流式入队+并发上限 10 | scheduler.py 有分区逻辑但接入不完全 |
| **错误级联** | Bash 错误取消 siblings | **无** |
| **流式进度** | 实时 yield progress | SSE 标记 `<!--TOOL_START-->` / `<!--TOOL_RESULT-->` |
| **后台任务** | `run_in_background` 参数 | **无** |
| **文件编辑** | old_string/new_string 匹配 | **无专门 FileEditTool** |
| **大结果处理** | 持久化到磁盘 + 预览 | `max_result_size` 截断 |
| **读取去重** | 同文件未变更返回 stub | **无** |

### 立即行动建议

1. **将 scheduler.py 完整接入 loop.py**
   替换当前串行执行逻辑，使用 `partition_tool_calls()` + `run_concurrent_batch()`。

2. **实现错误级联**
   Bash/Terminal 工具失败时，取消同批次其他 Bash 工具（参考 `siblingAbortController`）。

3. **实现 FileEditTool**
   引入 `old_string` / `new_string` 替换模型，替代行号定位编辑。

4. **Bash 工具增强**
   - 流式输出（AsyncGenerator）
   - 大结果持久化（超阈值写入临时文件）
   - 命令语义分类（UI 可折叠显示）

---

## 四、测试策略与工程实践

### 关键发现

Claude Code 源码中**不包含测试文件**（这是构建产物快照），但从源码引用可推断：
- 测试与源码分离（`test/` 独立）
- Feature Flag 隔离（200+ 处 `feature('X')`）
- Mock 限流系统（`mockRateLimits.ts`）
- VCR 录制回放（`withTokenCountVCR`）

### PioneClaw 的优势

- **MockLLMProvider 非常完善**（脚本/规则/延迟/错误/追踪），可直接扩展使用
- **pytest 基础设施成熟**（独立 SQLite、fixtures、依赖注入替换）

### 建议

1. **扩展 MockLLMProvider 使用**
   所有 Agent Loop 测试统一使用 `MockLLMProvider`，替代内联的 `FakeChatStreamProvider`。

2. **增加 Agent Loop 集成测试**
   - 多轮对话测试（LLM -> 工具 -> 结果 -> LLM）
   - 压缩触发测试
   - 错误恢复测试

3. **引入 Feature Flag 机制**
   ```python
   def feature_enabled(name: str) -> bool:
       return name in settings.FEATURE_FLAGS
   ```
   用于 Phase 2-5 的逐步 rollout。

4. **统一配置管理**
   借鉴 `envUtils.ts`，区分环境变量（敏感）vs 数据库配置（可热更新）vs 代码常量。

---

## 五、按 Phase 映射的实施路线图

### Phase 2: Key 轮换 + 重试接入（立即）

| 任务 | 文件 | 复杂度 |
|------|------|--------|
| 接入 TokenBudget 到 loop | `app/modules/agent/loop.py` | 低 |
| 接入 KeyRotator + LLMCallRetrier | `app/modules/agent/loop.py`、`app/modules/llm/retry.py` | 中 |
| 实现 max_output_tokens 自动恢复 | `app/modules/agent/loop.py` | 中 |
| 错误分类体系（401/429/502/529/prompt_too_long） | 新建 `retry_engine.py` | 中 |

### Phase 3: System Prompt 增强（立即-短期）

| 任务 | 文件 | 复杂度 |
|------|------|--------|
| System Prompt 模块化重构 | `app/modules/agent/context.py` | 低 |
| 增强反虚假声明 + 完成前验证 | `app/modules/agent/context.py` | 低 |
| 添加并行工具调用指导 | `app/modules/agent/context.py` | 低 |
| Prompt Caching 边界标记 | `app/modules/agent/context.py` + provider | 低 |
| 引入 Deferred Tools 机制 | 新建 `tool_search.py` | 中 |
| MicroCompact 时间触发 | `app/modules/agent/context_pruner.py` | 低 |

### Phase 4: 工具并行执行（短期）

| 任务 | 文件 | 复杂度 |
|------|------|--------|
| scheduler.py 完整接入 loop | `app/modules/agent/loop.py`、`app/modules/tools/scheduler.py` | 中 |
| 错误级联机制 | `app/modules/tools/scheduler.py` | 中 |
| 实现 FileEditTool | 新建 `file_edit.py` | 中 |
| Bash 流式输出 + 大结果持久化 | `app/modules/tools/base.py` | 中 |
| 读取去重机制 | 新增 read state 追踪 | 低 |

### Phase 5: Memory 系统简化（中期）

| 任务 | 文件 | 复杂度 |
|------|------|--------|
| Stop Hooks 接入主循环 | `app/modules/agent/loop.py` | 低 |
| Session Memory 机制 | 新建 `session_memory.py` | 中 |
| extract + consolidate 双轨制 | `app/modules/agent/compactor.py` | 中 |
| Post-Compact 恢复增强 | `app/modules/agent/compression_service.py` | 中 |

---

## 关键文件参考

### Claude Code 核心参考

| 文件 | 说明 |
|------|------|
| `claude-code/src/query.ts` | Agent Loop 主引擎 (~1730行) |
| `claude-code/src/QueryEngine.ts` | 会话生命周期管理 |
| `claude-code/src/services/tools/StreamingToolExecutor.ts` | 流式并发工具执行 |
| `claude-code/src/services/tools/toolOrchestration.ts` | 工具分区调度 |
| `claude-code/src/services/tools/toolExecution.ts` | 单工具完整生命周期 |
| `claude-code/src/services/compact/microCompact.ts` | Microcompact 实现 |
| `claude-code/src/services/compact/autoCompact.ts` | 自动压缩触发逻辑 |
| `claude-code/src/services/compact/compact.ts` | Compactor 实现 |
| `claude-code/src/services/api/withRetry.ts` | 重试与降级 |
| `claude-code/src/constants/prompts.ts` | System Prompt Section 定义 |
| `claude-code/src/utils/systemPrompt.ts` | SystemPrompt 组装逻辑 |
| `claude-code/src/tools/FileEditTool/FileEditTool.ts` | 文件编辑工具 |
| `claude-code/src/tools/FileReadTool/FileReadTool.ts` | 文件读取工具 |
| `claude-code/src/tools/BashTool/BashTool.tsx` | Bash 终端工具 |
| `claude-code/src/tools/GrepTool/GrepTool.ts` | 代码搜索工具 |
| `claude-code/src/query/tokenBudget.ts` | Token 预算解析 |
| `claude-code/src/query/stopHooks.ts` | Stop Hooks 机制 |
| `claude-code/src/services/SessionMemory/prompts.ts` | Session Memory 模板 |
| `claude-code/src/utils/toolSearch.ts` | 工具延迟加载 |
| `claude-code/src/utils/envUtils.ts` | 环境工具 |

### PioneClaw 当前对应

| 文件 | 说明 |
|------|------|
| `pione/pioneclaw/backend/app/modules/agent/loop.py` | AgentLoop (~1871行) |
| `pione/pioneclaw/backend/app/modules/agent/context.py` | ContextBuilder |
| `pione/pioneclaw/backend/app/modules/tools/scheduler.py` | 批处理调度器 |
| `pione/pioneclaw/backend/app/modules/tools/base.py` | 工具基类 |
| `pione/pioneclaw/backend/app/modules/agent/token_budget.py` | TokenBudget（未接入） |
| `pione/pioneclaw/backend/app/modules/agent/compression_service.py` | 压缩服务 |
| `pione/pioneclaw/backend/app/modules/agent/context_pruner.py` | MicroCompacter + Snip |
| `pione/pioneclaw/backend/app/modules/agent/compactor.py` | Compactor |
| `pione/pioneclaw/backend/app/modules/llm/retry.py` | LLMCallRetrier（未接入） |
| `pione/pioneclaw/backend/app/modules/providers/runtime.py` | KeyRotator（未接入） |
| `pione/pioneclaw/backend/app/modules/llm/mock_provider.py` | MockLLMProvider |
| `pione/pioneclaw/backend/tests/conftest.py` | 测试 fixtures |
| `pione/pioneclaw/backend/tests/mock_helpers.py` | Mock 辅助 |

---

## 总结

Claude Code 的核心优势在于**极致的模块化设计**（Prompt Section、工具生命周期、压缩管道）和**精细的边界控制**（并发安全默认关闭、错误级联隔离、Token 预算续传）。PioneClaw Phase 1 已打下压缩基础，Phase 2-5 应优先补齐 **TokenBudget 接入、System Prompt 模块化、工具分区执行、错误级联** 四个核心短板，这些都是模块已存在、只需接入或小幅扩展的"低垂果实"。
