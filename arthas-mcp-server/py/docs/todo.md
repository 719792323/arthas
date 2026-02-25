[任务规划完成]
好的，先删除废弃的文件，然后我来分析当前设计的不足。
[执行命令]
现在让我全面审视当前设计，结合你的工作目标（WORK_SUMMARY.md 中描述的智能诊断平台 + LLM 驱动诊断）来分析：
[读取文件]
已全面审阅完当前代码。下面是我结合你工作目标的详细分析：

---

## 当前设计评价

### ✅ 做得好的部分

1. **事件驱动状态机设计合理** — 无 `processing` 中间状态、靠锁防并发、靠幂等防重复，重启后自动恢复 pending stage，这个思路非常扎实
2. **ReAct 循环建模清晰** — `USER_QUERY → LLM_THINKING → TOOL_CALL → TOOL_RESULT → LLM_THINKING → ... → LLM_CONCLUSION` 链路完整
3. **审核拒绝后能回到 LLM 重新决策** — 这个闭环设计得好，拒绝不是终态而是让 LLM 换条路
4. **仓储层事务原子性** — `complete_and_next` 在同一事务中完成当前 + 创建下一个，不会出现半状态

---

### ⚠️ 需要改进的不足

结合你的工作目标（WORK_SUMMARY 中描述的智能诊断平台 + Token 消耗控制），我看到以下问题：

#### 1. **缺少 ReAct 循环次数上限保护**

当前没有限制一个 task 能跑多少轮 `LLM_THINKING → TOOL_CALL` 循环。如果 LLM 陷入无限循环或者持续找不到答案，会无限消耗 Token 且永远不结束。

> **建议**：在 `DiagnosisTask` 上加 `max_iterations` 字段（默认如10），`LlmThinkingHandler` 中检查当前 `stage_seq` 是否超限，超限则强制进入 `LLM_CONCLUSION`。

#### 2. **ToolCallHandler 中 `complete_stage` 被双重调用**

在 `ToolCallHandler.handle()` 中，成功路径先调了 `repo.complete_and_next()`（内部已将 stage 标记为 completed），紧接着又调了 `repo.complete_stage(stage.id, tool_result=...)`。第二次调用打开了新的事务去 UPDATE 一个已经在前一个事务中被 committed 的 stage，但此时 `stage.id` 的 status 已经是 completed 了，再次 update 虽然不报错但语义上是冗余的，并且 `tool_result` 应该直接在 `complete_and_next` 的 `output_data` 中就携带了。

> **建议**：把 `tool_result` 写入逻辑合并到 `complete_and_next` 调用中，去掉多余的 `complete_stage` 调用。

#### 3. **上下文窗口管理不只是"预留"，需要尽早设计骨架**

你在 WORK_SUMMARY 中提到 **"调用大量指令来确定问题在哪，这消耗了大量 token"**。当前 `ContextBuilder` 是把所有历史 stage 全量拼成 messages，没有任何截断或摘要策略。当 ReAct 循环走到 7-8 轮时，context 会爆。

> **建议**：至少在 `ContextBuilder` 中实现一个"滑动窗口"骨架——永远保留第一条 USER_QUERY + 最近 N 轮的 TOOL_CALL/TOOL_RESULT，中间部分用摘要占位。这样在接 LLM 时不会一开始就踩坑。

#### 4. **缺少对 tools/list 的主动拉取**

`ContextBuilder` 有 `available_tools` 字段，但当前没有任何地方从 Arthas 客户端主动拉取 `tools/list`。LLM 做决策需要知道有哪些工具可用。

> **建议**：在 `ToolCallHandler` 或 `LlmThinkingHandler` 首次执行时（或客户端初始化完成后），主动调用 `tools/list` 拉取工具列表缓存到 `ContextBuilder`。

#### 5. **缺少 WebSocket 推送诊断进度的能力**

当前诊断进度只能通过 REST API `GET /api/diagnosis/{task_id}/progress` 轮询获取。但你已经有 WebSocket 基础设施了，诊断过程中每个 stage 状态变更时应该主动推送给前端，实现实时的"类 MCP 工具调用过程渲染"。

> **建议**：在 `StageHandler` 的 `handle()` 完成后，通过 WebSocket 或 SSE 向关联 session_id 推送 stage 变更事件。

#### 6. **`ToolResultHandler` 的 ImmediatePool 投递是空操作**

`ToolResultHandler` 持有 `immediate_pool` 引用，但只是打了个日志，没有实际 submit。TOOL_RESULT → LLM_THINKING 这步最应该走 ImmediatePool 加速（用户等工具结果回来后不想再等 5s 轮询），当前等于白白浪费了设计好的双池架构。

> **建议**：在 `ToolResultHandler` 中实际调用 ImmediatePool 或者让 scheduler 提供一个 `dispatch_now()` 方法来立即触发新创建的 stage 处理。

#### 7. **Repository 每个方法都开独立 Session，缺少复合事务能力**

比如 `mark_failed` + `fail_task` 是两个独立事务。如果 `mark_failed` 成功但 `fail_task` 失败，会出现 stage=failed 但 task 仍然 running 的不一致状态（虽然 `check_and_fail_stale_tasks` 能兜底，但延迟不可控）。

> **建议**：为 Repository 方法增加可选的 `session` 参数传入，允许在同一事务中完成多步操作。或者提供一个 `fail_stage_and_task` 的原子方法。

#### 8. **缺少取消任务的 API 和机制**

`TaskStatus` 有 `CANCELLED` 状态，但没有对应的 REST API 和处理逻辑。用户创建任务后无法主动取消。

> **建议**：加 `POST /api/diagnosis/{task_id}/cancel` API。

---

### 📋 优先级建议

| 优先级 | 问题 | 理由 |
|--------|------|------|
| **P0** | #1 循环次数上限 | 接 LLM 前必须有，否则可能无限消耗 |
| **P0** | #3 上下文窗口骨架 | Token 消耗是你核心痛点 |
| **P1** | #4 tools/list 拉取 | LLM 决策必须知道可用工具 |
| **P1** | #2 双重 complete_stage | 代码 bug，应立即修复 |
| **P1** | #6 ImmediatePool 空操作 | 影响诊断响应速度 |
| **P2** | #7 复合事务 | 兜底机制在，但生产环境不够健壮 |
| **P2** | #5 WebSocket 进度推送 | 用户体验优化 |
| **P2** | #8 取消任务 API | 功能完整性 |

你想先处理哪些？我可以逐个实施。


[任务规划完成]
让我先全面了解当前项目的最新状态，包括代码结构、模型设计和 todo 文件，然后从顶层使用视角进行分析。
[读取文件]
[查找目录]
[读取文件]
[读取文件]
好，我已经完整阅读了所有核心代码。现在从**顶层使用视角**（而不是编码实现细节）来分析存在的不足。

---

## 顶层使用视角的设计不足分析

### 1. 🔴 **一个诊断任务只绑定单个 session_id，不支持跨实例/跨应用诊断**

当前 `CreateDiagnosisRequest` 要求用户指定**一个** `session_id`，意味着一次诊断只能诊断一个 Java 实例。但你在 WORK_SUMMARY 中明确说了：

> *"在分布式链路中很难定位哪个服务出问题"*

实际场景：用户说"订单支付链路延迟高"，LLM 可能需要先查 ServiceA 的 trace，再查 ServiceB 的线程状态，再查 ServiceC 的 GC。**这三个服务对应不同的 session_id**。

当前设计下 LLM 必须在一个固定的 session 上执行所有工具调用，**无法跨实例跳转**。这意味着：
- 要么用户提前就得知道问题在哪个服务上（那还需要什么智能诊断？）
- 要么每个实例开一个独立诊断任务，但上下文完全隔离，LLM 无法关联分析

> **建议**：task 不应该绑死单个 session_id，而是应该绑定一个 **session 组 / 应用拓扑**。TOOL_CALL 阶段应该允许 LLM 指定 `target_session_id`，从 `available_sessions` 中选择目标。

---

### 2. 🔴 **缺少"谁来提问"的入口设计 — 用户侧交互模型不清晰**

当前的使用流程是：

```
用户 → POST /api/diagnosis {session_id, user_query} → 创建任务 → 轮询 progress
```

问题在于：**这只是一个 API 接口，没有设计用户交互通道**。在实际使用中：

- **用户需要和诊断过程交互**：比如 LLM 判断不确定时需要向用户追问（"你说的延迟是在哪个接口？"），或者用户看到中间结果想补充信息
- **多轮对话**：用户的第一个问题可能不够精确，需要在诊断过程中追加上下文
- **审核拒绝时的沟通**：当前审核拒绝只是让 LLM 换条路，但实际场景中管理员可能想附带拒绝理由让 LLM 参考

当前设计中，诊断一旦创建就完全是**自动机模式**，用户只能作为旁观者轮询进度，无法参与中间过程。这和你想做的"类 MCP 渲染工具调用过程"的体验目标是矛盾的。

> **建议**：增加 `HUMAN_INPUT` 类型的 stage——当 LLM 认为需要用户补充信息时，可以创建这种 stage 暂停诊断，等用户通过 API 提交补充信息后继续。同时提供 `POST /api/diagnosis/{task_id}/message` 让用户在任务运行中追加上下文。

---

### 3. 🟡 **审核机制过于粗粒度 — 只按命令名分级，不考虑参数和上下文**

当前 `commands_requiring_approval` 是一个简单的命令名列表：
```python
["heapdump", "redefine", "retransform", "reset", "stop", "shutdown"]
```

但在实际生产环境中：
- 同一个命令不同参数风险差异巨大：`watch com.foo.Bar * '{params}'` 是低风险观察，但 `watch com.foo.Bar * '{params}' -b -n 999999` 可能搞崩应用
- **context matters**：对于测试环境可能全部自动执行，对于生产环境可能全部需要审核
- 缺少**执行频率/资源限制**：LLM 可能在短时间内发起大量工具调用，对目标应用造成性能冲击

> **建议**：审核策略应该升级为策略引擎模式 — 不只看命令名，还要看参数、目标环境标签、当前 task 已执行的命令数量等。至少应该能按 session 打环境标签（prod/staging/test），不同环境走不同审核策略。

---

### 4. 🟡 **缺少诊断结果的可追溯性和可解释性设计**

当前 timeline 渲染只是把每个 stage 平铺展示出来，但没有考虑：

- **诊断链路的因果关系不明确**：用户看到一堆 stage 列表，但不知道"为什么 LLM 选择执行这个命令而不是那个"
- **缺少 LLM 推理过程的结构化输出**：`thinking` 字段是纯文本，没有结构化的"观察→推理→行动"格式
- **缺少诊断知识沉淀**：一次成功的诊断过程（问题类型 + 工具调用序列 + 结论）应该能沉淀为知识，供 RAG 使用或作为 few-shot 示例

> **建议**：
> - thinking 应该是结构化的 JSON（observation / reasoning / action_plan），而不只是纯文本
> - 诊断结束后应有一个"诊断报告"的概念，总结问题、诊断路径、关键发现、结论
> - 考虑引入诊断模板/诊断 SOP，让 LLM 的行为可预测且可复现

---

### 5. 🟡 **工具抽象层缺失 — Arthas 命令和 LLM Tool 之间缺少映射层**

当前 LLM 决策的 `tool_name` 直接就是 Arthas 原生命令名，但你在 WORK_SUMMARY 中说了：

> *"精简指令返回内容、组合封装高阶指令，降低调用复杂度和 LLM Token 消耗"*

这意味着 LLM 看到的工具不应该是 Arthas 原始的 100+ 条命令，而应该是**经过封装的高阶诊断工具**，例如：
- `diagnose_memory_leak` = jvm + heapdump + ognl + ...
- `analyze_slow_method` = trace + watch + ...
- `check_thread_deadlock` = thread + stack + ...

但当前设计中，从 `ContextBuilder` 到 `ToolCallHandler`，都没有这样的工具抽象层。`available_tools` 是直接透传客户端的 `tools/list`，LLM 需要在几十个原始命令中选择，这会：
- 消耗大量 Token 来理解每个命令的含义
- 增加 LLM 选错工具的概率
- 无法利用"组合封装高阶指令"的优化

> **建议**：引入 ToolRegistry 层，定义高阶诊断工具和原始 Arthas 命令之间的映射关系。LLM 面向高阶工具决策，系统内部拆解为具体命令序列。

---

### 6. 🟡 **没有考虑诊断安全边界和资源保护**

生产环境最怕的是诊断反而搞出故障。当前设计缺少：

- **并发诊断冲突检测**：两个诊断任务同时对同一个 session_id 执行工具调用，可能互相干扰
- **单 session 诊断上限**：一个 Java 实例上不应该同时跑多个诊断任务
- **工具调用频率限制**：LLM 可能在短时间内密集调用 trace/watch 等性能影响大的命令
- **诊断超时总控**：一个诊断任务不应该无限期运行（只有 stage 级别的重试，没有 task 级别的超时）

> **建议**：在 task 创建时检查目标 session 是否有正在运行的任务；加入 task 级别的 TTL；对单位时间内的工具调用频率做限流。

---

### 📋 总结对比

| 维度 | 当前设计 | 实际需要 |
|------|---------|---------|
| **诊断范围** | 单实例单 session | 跨实例、跨应用 |
| **用户交互** | 创建→旁观→看结果 | 可追问、可补充、可中间干预 |
| **审核策略** | 命令名黑名单 | 参数+环境+频率多维策略 |
| **工具抽象** | 原始 Arthas 命令直通 | 高阶诊断工具 → 原始命令拆解 |
| **安全边界** | stage 级重试 | task 超时+并发隔离+频率限流 |
| **可解释性** | 纯文本 thinking | 结构化推理链+诊断报告 |

这些是从**实际使用场景**倒推出的顶层设计缺陷，不是代码bug而是架构上的gap。你觉得哪些是当前阶段最需要优先补的？