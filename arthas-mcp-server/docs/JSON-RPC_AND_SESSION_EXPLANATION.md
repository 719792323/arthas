# JSON-RPC 2.0 与会话管理详解

## 📋 文档信息

- **创建日期**: 2026-01-23
- **版本**: v1.0
- **作者**: Arthas Team
- **目的**: 解释 JSON-RPC 2.0 协议中的 id 字段，以及 Arthas MCP 中的会话管理机制

---

## 1. 什么是 JSON-RPC 2.0？

### 1.1 定义

**JSON-RPC 2.0** 是一个**无状态的、轻量级的远程过程调用（RPC）协议**，使用 JSON 作为数据格式。

### 1.2 核心特点

| 特性 | 说明 |
|-----|------|
| **无状态** | 每个请求都是独立的，不依赖之前的请求 |
| **轻量级** | 协议简单，易于实现 |
| **传输无关** | 可以通过 HTTP、WebSocket、TCP 等传输 |
| **语言无关** | 任何支持 JSON 的语言都可以实现 |

### 1.3 协议规范

JSON-RPC 2.0 定义了三种消息类型：

```
┌─────────────────────────────────────────────────────────────────┐
│  JSON-RPC 2.0 消息类型                                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. 请求 (Request)                                              │
│     {                                                           │
│       "jsonrpc": "2.0",                                         │
│       "id": 1,              ← 必须有 id                         │
│       "method": "tools/call",                                   │
│       "params": {...}                                           │
│     }                                                           │
│                                                                 │
│  2. 响应 (Response)                                             │
│     {                                                           │
│       "jsonrpc": "2.0",                                         │
│       "id": 1,              ← 必须与请求的 id 相同              │
│       "result": {...}       ← 成功时有 result                   │
│     }                                                           │
│     或                                                          │
│     {                                                           │
│       "jsonrpc": "2.0",                                         │
│       "id": 1,                                                  │
│       "error": {...}        ← 失败时有 error                    │
│     }                                                           │
│                                                                 │
│  3. 通知 (Notification)                                         │
│     {                                                           │
│       "jsonrpc": "2.0",                                         │
│       "method": "notifications/initialized",                    │
│       "params": {...}                                           │
│       // 没有 id 字段！                                         │
│     }                                                           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 为什么 JSONRPCRequest 需要 id 字段？

### 2.1 核心作用：请求-响应关联

**问题场景**：

```
客户端同时发送多个请求：
├─ 请求 1: 调用 jvm 命令
├─ 请求 2: 调用 thread 命令
└─ 请求 3: 调用 memory 命令

服务端可能乱序返回响应：
├─ 响应 2: thread 命令的结果（先完成）
├─ 响应 1: jvm 命令的结果
└─ 响应 3: memory 命令的结果

问题：客户端如何知道哪个响应对应哪个请求？
答案：通过 id 字段！
```

### 2.2 代码示例

#### 请求示例

```json
// 请求 1
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "jvm",
    "arguments": {}
  }
}

// 请求 2
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "thread",
    "arguments": {"topN": 5}
  }
}
```

#### 响应示例

```json
// 响应 2（先返回）
{
  "jsonrpc": "2.0",
  "id": 2,  // ← 对应请求 2
  "result": {
    "content": [{"type": "text", "text": "线程信息..."}]
  }
}

// 响应 1（后返回）
{
  "jsonrpc": "2.0",
  "id": 1,  // ← 对应请求 1
  "result": {
    "content": [{"type": "text", "text": "JVM 信息..."}]
  }
}
```

### 2.3 id 字段的特性

| 特性 | 说明 |
|-----|------|
| **类型** | 可以是字符串、数字或 null |
| **唯一性** | 在同一连接中，每个请求的 id 应该唯一 |
| **必须性** | 请求必须有 id，响应必须返回相同的 id |
| **通知例外** | 通知（Notification）没有 id，也不需要响应 |

### 2.4 实际代码实现

```java
// McpSchema.java
public static class JSONRPCRequest implements JSONRPCMessage {
    private final String jsonrpc;  // 固定为 "2.0"
    private final String method;   // 方法名，如 "tools/call"
    private final Object id;       // ← 请求 ID，用于关联响应
    private final Object params;   // 参数
    
    // ...
}

public static class JSONRPCResponse implements JSONRPCMessage {
    private final String jsonrpc;  // 固定为 "2.0"
    private final Object id;       // ← 必须与请求的 id 相同
    private final Object result;   // 成功时的结果
    private final JSONRPCError error;  // 失败时的错误
    
    // ...
}
```

### 2.5 为什么不用 HTTP 的请求-响应机制？

**答案**：JSON-RPC 2.0 是**传输无关**的协议，可以在多种传输层上使用：

| 传输方式 | 是否支持多路复用 | 是否需要 id 字段 |
|---------|----------------|----------------|
| **HTTP 短连接** | ❌ 每个请求一个连接 | ✅ 仍需要（协议规范） |
| **HTTP 长连接** | ✅ 多个请求共享连接 | ✅ 必须（区分响应） |
| **WebSocket** | ✅ 双向通信 | ✅ 必须（区分响应） |
| **SSE (Server-Sent Events)** | ✅ 服务端推送 | ✅ 必须（区分响应） |

**Arthas MCP 使用的传输方式**：

```
┌─────────────────────────────────────────────────────────────────┐
│  Arthas MCP 的传输架构                                           │
│                                                                 │
│  1. SSE 长连接（用于接收服务端推送）                             │
│     GET /mcp                                                    │
│     Accept: text/event-stream                                   │
│     mcp-session-id: xxx                                         │
│     ↓                                                           │
│     保持连接，接收 SSE 事件                                      │
│                                                                 │
│  2. HTTP POST 短连接（用于发送请求）                             │
│     POST /mcp                                                   │
│     Content-Type: application/json                              │
│     mcp-session-id: xxx                                         │
│     Body: {"jsonrpc":"2.0","id":1,"method":"tools/call",...}    │
│     ↓                                                           │
│     立即关闭连接                                                 │
│                                                                 │
│  3. 响应通过 SSE 推送                                            │
│     event: message                                              │
│     data: {"jsonrpc":"2.0","id":1,"result":{...}}               │
│                                                                 │
│  问题：请求和响应在不同的连接上！                                 │
│  解决：通过 id 字段关联请求和响应                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. 三种 Session ID 的作用

在 Arthas MCP 中，有三种不同的 ID，它们各自有不同的作用：

```
┌─────────────────────────────────────────────────────────────────┐
│  三种 ID 的层次关系                                              │
│                                                                 │
│  mcpSessionId (MCP 层)                                          │
│  └─▶ 标识一个 MCP 客户端连接                                     │
│      └─▶ arthasSessionId (Arthas 层)                           │
│          └─▶ 标识一个 Arthas 命令执行会话                        │
│              └─▶ consumerId (结果消费层)                        │
│                  └─▶ 标识一个结果消费者（用于拉取结果）          │
└─────────────────────────────────────────────────────────────────┘
```

### 3.1 mcpSessionId - MCP 会话 ID

#### 定义

```java
// ArthasCommandSessionManager.CommandSessionBinding
private final String mcpSessionId;  // MCP 会话 ID
```

#### 作用

| 作用 | 说明 |
|-----|------|
| **标识 MCP 客户端** | 每个连接到 MCP 服务的客户端都有唯一的 mcpSessionId |
| **关联 HTTP 请求** | 通过 HTTP 头 `mcp-session-id` 传递，关联 SSE 连接和 POST 请求 |
| **会话隔离** | 不同的 MCP 客户端使用不同的 mcpSessionId，互不干扰 |

#### 生命周期

```
1. 客户端发送 initialize 请求
   └─▶ 服务端生成 mcpSessionId
   └─▶ 返回给客户端（在 HTTP 响应头中）

2. 客户端建立 SSE 连接
   └─▶ 在 HTTP 头中携带 mcp-session-id

3. 客户端发送后续请求
   └─▶ 在 HTTP 头中携带 mcp-session-id

4. 客户端断开连接
   └─▶ 服务端清理 mcpSessionId 相关资源
```

#### 代码示例

```java
// ArthasCommandSessionManager.java
public CommandSessionBinding getCommandSession(String mcpSessionId, Object authSubject) {
    CommandSessionBinding binding = sessionBindings.get(mcpSessionId);
    
    if (binding == null) {
        // 为这个 MCP 会话创建新的 Arthas 会话
        binding = createCommandSession(mcpSessionId);
        sessionBindings.put(mcpSessionId, binding);
        logger.debug("Created new command session: MCP={}, Arthas={}", 
                    mcpSessionId, binding.getArthasSessionId());
    }
    
    return binding;
}
```

### 3.2 arthasSessionId - Arthas 会话 ID

#### 定义

```java
// ArthasCommandSessionManager.CommandSessionBinding
private final String arthasSessionId;  // Arthas 会话 ID
```

#### 作用

| 作用 | 说明 |
|-----|------|
| **标识 Arthas 命令会话** | 每个 Arthas 会话可以执行多个命令 |
| **隔离命令执行** | 不同会话的命令互不干扰（如 trace、watch 等异步命令） |
| **会话状态管理** | Arthas 内部维护会话状态（如当前任务、结果队列等） |

#### 为什么需要 arthasSessionId？

**场景 1：多个客户端同时使用**

```
┌─────────────────────────────────────────────────────────────────┐
│  没有会话隔离的问题                                              │
│                                                                 │
│  Client A: 启动 trace 命令                                       │
│  Client B: 启动 watch 命令 → 失败！（只能有一个前台任务）        │
│                                                                 │
│  有会话隔离的解决方案                                            │
│                                                                 │
│  Client A (Session 1): 启动 trace 命令 → 成功                   │
│  Client B (Session 2): 启动 watch 命令 → 成功                   │
│                                                                 │
│  每个会话有独立的任务队列和结果队列                               │
└─────────────────────────────────────────────────────────────────┘
```

**场景 2：异步命令的持续运行**

```
┌─────────────────────────────────────────────────────────────────┐
│  异步命令的执行流程                                              │
│                                                                 │
│  1. 启动命令                                                    │
│     executeAsync("trace com.example.MyClass myMethod")          │
│     └─▶ 返回 jobId                                             │
│     └─▶ 命令在后台持续运行（在 arthasSessionId 对应的会话中）   │
│                                                                 │
│  2. 拉取结果（轮询）                                            │
│     pullResults(arthasSessionId, consumerId)                    │
│     └─▶ 返回自上次拉取以来的所有结果                            │
│     └─▶ 可能返回 0 个、1 个或多个结果                           │
│                                                                 │
│  3. 停止命令                                                    │
│     interruptJob(arthasSessionId)                               │
│     └─▶ 停止后台任务                                            │
│                                                                 │
│  关键：arthasSessionId 标识了命令运行的上下文                    │
└─────────────────────────────────────────────────────────────────┘
```

#### 代码示例

```java
// ArthasCommandSessionManager.java
public CommandSessionBinding createCommandSession(String mcpSessionId) {
    // 调用 Arthas 创建新会话
    Map<String, Object> result = commandExecutor.createSession();
    
    CommandSessionBinding binding = new CommandSessionBinding(
        mcpSessionId,
        (String) result.get("sessionId"),  // ← Arthas 返回的 sessionId
        (String) result.get("consumerId")  // ← Arthas 返回的 consumerId
    );
    
    return binding;
}
```

#### Arthas 内部的会话管理

```
┌─────────────────────────────────────────────────────────────────┐
│  Arthas 内部的会话结构                                           │
│                                                                 │
│  Session 1 (arthasSessionId = "abc123")                        │
│  ├─ 当前任务: trace com.example.MyClass myMethod                │
│  ├─ 任务状态: RUNNING                                           │
│  ├─ 结果队列: [result1, result2, result3, ...]                 │
│  └─ 消费者列表:                                                 │
│     └─ Consumer 1 (consumerId = "consumer-1")                  │
│        └─ 已消费位置: 2 (下次从 result3 开始)                   │
│                                                                 │
│  Session 2 (arthasSessionId = "def456")                        │
│  ├─ 当前任务: watch com.example.MyClass myMethod                │
│  ├─ 任务状态: RUNNING                                           │
│  ├─ 结果队列: [result1, result2]                               │
│  └─ 消费者列表:                                                 │
│     └─ Consumer 1 (consumerId = "consumer-2")                  │
│        └─ 已消费位置: 1 (下次从 result2 开始)                   │
└─────────────────────────────────────────────────────────────────┘
```

### 3.3 consumerId - 结果消费者 ID

#### 定义

```java
// ArthasCommandSessionManager.CommandSessionBinding
private final String consumerId;  // 结果消费者 ID
```

#### 作用

| 作用 | 说明 |
|-----|------|
| **标识结果消费者** | 用于拉取异步命令的结果 |
| **记录消费位置** | Arthas 记录每个 consumer 已经消费到哪个位置 |
| **支持多消费者** | 同一个会话可以有多个消费者（虽然 MCP 中通常只有一个） |

#### 为什么需要 consumerId？

**问题场景**：

```
1. 启动异步命令
   trace com.example.MyClass myMethod
   └─▶ 命令持续运行，产生结果不断积累到队列中

2. 第一次拉取结果（轮询调用）
   pullResults(sessionId, consumerId)
   └─▶ 从队列中取出一批结果：[result1, result2]（最多20个）
   └─▶ 这些结果从队列中移除（poll 语义）

3. 第二次拉取结果（继续轮询）
   pullResults(sessionId, consumerId)
   └─▶ 从队列中取出新的一批结果：[result3, result4]
   └─▶ 如果队列暂时为空，会等待最多 2 秒（长轮询）

4. 持续轮询...
   └─▶ 直到命令结束或超时

关键点：每次 pullResults 返回的是队列中当前积累的一批结果，
       而不是一次性返回所有结果！
```

#### 代码示例

```java
// CommandExecutor.java
public interface CommandExecutor {
    /**
     * 拉取异步命令的结果
     * 
     * @param sessionId Arthas 会话 ID
     * @param consumerId 消费者 ID
     * @return 自上次拉取以来的所有结果
     */
    Map<String, Object> pullResults(String sessionId, String consumerId);
}
```

#### pullResults/pollResults 的实现细节

```
┌─────────────────────────────────────────────────────────────────┐
│  ResultConsumerImpl.pollResults() 的行为                        │
│                                                                 │
│  每个 Consumer 有独立的结果队列（BlockingQueue）：               │
│                                                                 │
│  Consumer (consumerId = "consumer-xyz")                        │
│  └─ resultQueue: [result1, result2, result3, ...]              │
│  └─ resultBatchSizeLimit: 20  （每次最多返回20个结果）          │
│  └─ pollTimeLimit: 2000ms     （长轮询最大等待时间）            │
│                                                                 │
│  pollResults() 行为：                                           │
│  1. 从队列中 poll 取出结果（取出后从队列移除）                   │
│  2. 最多取 20 个结果，或等待 2 秒                               │
│  3. 如果队列为空，会阻塞等待新结果（长轮询）                     │
│  4. 返回这一批结果给调用方                                       │
│                                                                 │
│  注意：是 poll 语义（取出即删除），而不是 peek 语义！            │
└─────────────────────────────────────────────────────────────────┘
```

#### 关键理解：pullResults 是轮询调用的

```java
// ResultConsumerImpl.java 核心代码
public List<ResultModel> pollResults() {
    // 每次调用最多返回 resultBatchSizeLimit(20) 个结果
    List<ResultModel> sendingResults = new ArrayList<>(resultBatchSizeLimit);
    
    while (sendingResults.size() < resultBatchSizeLimit
            && sendingDelay < 100       // 发送延迟
            && waitingTime < 2000) {    // 最多等待2秒
        
        // 从队列中取出一个结果（阻塞最多100ms）
        ResultModel aResult = resultQueue.poll(100, TimeUnit.MILLISECONDS);
        
        if (aResult != null) {
            sendingResults.add(aResult);  // 加入本批次
        }
    }
    
    return sendingResults;  // 返回这一批结果
}
```

**关键点**：
1. **不是一次性返回所有结果**：每次调用最多返回 20 个结果
2. **需要多次轮询**：调用方需要循环调用 `pullResults()` 直到命令结束
3. **长轮询机制**：如果队列为空，会等待最多 2 秒再返回
4. **poll 语义**：结果取出后从队列中删除，不会重复返回

---

## 4. 三种 ID 的协同工作

### 4.1 完整的调用流程

```
┌─────────────────────────────────────────────────────────────────┐
│  1. MCP 客户端连接                                               │
│     └─▶ 生成 mcpSessionId = "mcp-session-123"                  │
│                                                                 │
│  2. 创建 Arthas 会话                                             │
│     └─▶ commandExecutor.createSession()                        │
│         └─▶ 返回 arthasSessionId = "arthas-abc"                │
│         └─▶ 返回 consumerId = "consumer-xyz"                   │
│                                                                 │
│  3. 绑定关系                                                    │
│     CommandSessionBinding {                                     │
│         mcpSessionId: "mcp-session-123",                        │
│         arthasSessionId: "arthas-abc",                          │
│         consumerId: "consumer-xyz"                              │
│     }                                                           │
│                                                                 │
│  4. 执行异步命令                                                │
│     executeAsync("trace MyClass myMethod", "arthas-abc")        │
│     └─▶ 命令在 arthas-abc 会话中运行                            │
│                                                                 │
│  5. 拉取结果                                                    │
│     pullResults("arthas-abc", "consumer-xyz")                   │
│     └─▶ 返回 consumer-xyz 未消费的结果                          │
│                                                                 │
│  6. 客户端断开                                                  │
│     └─▶ 根据 mcpSessionId 找到 binding                         │
│         └─▶ 关闭 arthasSessionId 对应的会话                     │
│             └─▶ 清理 consumerId 的消费记录                      │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 代码实现

```java
// ArthasCommandSessionManager.java
public static class CommandSessionBinding {
    private final String mcpSessionId;      // MCP 层的会话 ID
    private final String arthasSessionId;   // Arthas 层的会话 ID
    private final String consumerId;        // 结果消费者 ID
    private final long createdTime;         // 创建时间
    private volatile long lastAccessTime;   // 最后访问时间
    
    public CommandSessionBinding(String mcpSessionId, 
                                String arthasSessionId, 
                                String consumerId) {
        this.mcpSessionId = mcpSessionId;
        this.arthasSessionId = arthasSessionId;
        this.consumerId = consumerId;
        this.createdTime = System.currentTimeMillis();
        this.lastAccessTime = this.createdTime;
    }
    
    // 更新访问时间（用于会话过期检测）
    public void updateAccessTime() {
        this.lastAccessTime = System.currentTimeMillis();
    }
}
```

### 4.3 会话管理器的作用

```java
// ArthasCommandSessionManager.java
public class ArthasCommandSessionManager {
    // MCP Session ID → CommandSessionBinding
    private final ConcurrentHashMap<String, CommandSessionBinding> sessionBindings;
    
    /**
     * 获取命令执行会话
     * 
     * @param mcpSessionId MCP 会话 ID
     * @param authSubject 认证主体
     * @return 会话绑定对象
     */
    public CommandSessionBinding getCommandSession(String mcpSessionId, 
                                                   Object authSubject) {
        CommandSessionBinding binding = sessionBindings.get(mcpSessionId);
        
        if (binding == null) {
            // 创建新的 Arthas 会话
            binding = createCommandSession(mcpSessionId);
            sessionBindings.put(mcpSessionId, binding);
        } else if (!isSessionValid(binding)) {
            // 会话过期，重新创建
            closeSession(binding.getArthasSessionId());
            binding = createCommandSession(mcpSessionId);
            sessionBindings.put(mcpSessionId, binding);
        }
        
        binding.updateAccessTime();
        return binding;
    }
    
    /**
     * 关闭命令会话
     * 
     * @param mcpSessionId MCP 会话 ID
     */
    public void closeCommandSession(String mcpSessionId) {
        CommandSessionBinding binding = sessionBindings.remove(mcpSessionId);
        if (binding != null) {
            // 关闭 Arthas 会话（会自动清理 consumerId）
            commandExecutor.closeSession(binding.getArthasSessionId());
        }
    }
}
```

---

## 5. 实际应用场景

### 5.1 场景 1：执行同步命令（jvm）

```
1. 客户端发送请求
   POST /mcp
   mcp-session-id: mcp-session-123
   {
     "jsonrpc": "2.0",
     "id": 1,  ← JSON-RPC 请求 ID
     "method": "tools/call",
     "params": {"name": "jvm", "arguments": {}}
   }

2. 服务端处理
   ├─ 根据 mcp-session-id 找到 binding
   │  └─▶ arthasSessionId = "arthas-abc"
   │  └─▶ consumerId = "consumer-xyz"
   │
   ├─ 执行同步命令
   │  └─▶ executeSync("jvm", "arthas-abc")
   │  └─▶ 立即返回结果
   │
   └─ 通过 SSE 推送响应
      └─▶ event: message
          data: {
            "jsonrpc": "2.0",
            "id": 1,  ← 与请求的 id 相同
            "result": {...}
          }

3. 客户端接收响应
   └─ 根据 id=1 找到对应的请求
   └─ 处理结果
```

### 5.2 场景 2：执行异步命令（trace）

```
1. 客户端发送请求
   POST /mcp
   mcp-session-id: mcp-session-123
   {
     "jsonrpc": "2.0",
     "id": 2,  ← JSON-RPC 请求 ID
     "method": "tools/call",
     "params": {
       "name": "trace",
       "arguments": {"class": "MyClass", "method": "myMethod"}
     }
   }

2. 服务端处理（StreamableToolUtils.executeAndCollectResults）
   ├─ 根据 mcp-session-id 找到 binding
   │  └─▶ arthasSessionId = "arthas-abc"
   │  └─▶ consumerId = "consumer-xyz"
   │
   ├─ 启动异步命令
   │  └─▶ executeAsync("trace MyClass myMethod", "arthas-abc")
   │  └─▶ 返回 jobId
   │  └─▶ 命令在后台持续运行，结果不断产生
   │
   ├─ 轮询拉取结果（核心循环）
   │  ┌─────────────────────────────────────────────────────┐
   │  │  while (未超时 && 未达到预期结果数) {              │
   │  │      results = pullResults(sessionId, consumerId)  │
   │  │      // ↑ 每次返回一批结果（最多20个）             │
   │  │      // 如果队列为空，会等待最多2秒（长轮询）      │
   │  │                                                    │
   │  │      if (results != null && !results.isEmpty()) {  │
   │  │          allResults.addAll(results)  // 累积结果   │
   │  │          发送进度通知（如果有progressToken）       │
   │  │      }                                             │
   │  │                                                    │
   │  │      if (jobStatus == TERMINATED) break            │
   │  │      Thread.sleep(100ms)  // 轮询间隔              │
   │  │  }                                                 │
   │  └─────────────────────────────────────────────────────┘
   │
   └─ 收集完成后，一次性返回所有结果
      └─▶ event: message
          data: {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
              "content": [... 所有累积的结果 ...],
              "resultCount": 15,
              "status": "completed"
            }
          }

3. 关键区别
   ├─ pullResults() 是**内部轮询机制**，每次返回一批结果
   ├─ 对 MCP 客户端来说，收到的是**最终聚合后的结果**
   └─ 除非命令执行时间很长，才会通过 progressNotification 报告进度
```

### 5.3 场景 3：多个客户端同时使用

```
┌─────────────────────────────────────────────────────────────────┐
│  Client A                                                       │
│  ├─ mcpSessionId: "mcp-session-A"                              │
│  ├─ arthasSessionId: "arthas-session-A"                        │
│  ├─ consumerId: "consumer-A"                                   │
│  └─ 执行: trace MyClass method1                                │
│                                                                 │
│  Client B                                                       │
│  ├─ mcpSessionId: "mcp-session-B"                              │
│  ├─ arthasSessionId: "arthas-session-B"                        │
│  ├─ consumerId: "consumer-B"                                   │
│  └─ 执行: watch MyClass method2                                │
│                                                                 │
│  两个客户端完全隔离，互不干扰！                                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 6. 总结

### 6.1 核心要点

| ID 类型 | 层次 | 作用 | 生命周期 |
|--------|-----|------|---------|
| **JSON-RPC id** | 协议层 | 关联请求和响应 | 单次请求-响应 |
| **mcpSessionId** | MCP 层 | 标识 MCP 客户端连接 | 客户端连接期间 |
| **arthasSessionId** | Arthas 层 | 标识 Arthas 命令会话 | 会话创建到关闭 |
| **consumerId** | 结果消费层 | 标识结果消费者，记录消费位置 | 会话创建到关闭 |

### 6.2 设计原则

```
1. 分层设计
   ├─ JSON-RPC 层：处理请求-响应关联
   ├─ MCP 层：处理客户端会话管理
   └─ Arthas 层：处理命令执行和结果管理

2. 职责分离
   ├─ JSON-RPC id：只负责关联请求和响应
   ├─ mcpSessionId：只负责标识 MCP 客户端
   ├─ arthasSessionId：只负责标识 Arthas 会话
   └─ consumerId：只负责记录消费位置

3. 会话隔离
   ├─ 不同的 MCP 客户端使用不同的 mcpSessionId
   ├─ 不同的 mcpSessionId 对应不同的 arthasSessionId
   └─ 不同的会话互不干扰
```

### 6.3 为什么需要这么多 ID？

**答案**：每个 ID 都有其特定的职责，缺一不可：

```
┌─────────────────────────────────────────────────────────────────┐
│  如果没有 JSON-RPC id                                            │
│  └─▶ 无法关联请求和响应（特别是在异步场景下）                     │
│                                                                 │
│  如果没有 mcpSessionId                                           │
│  └─▶ 无法区分不同的 MCP 客户端                                   │
│  └─▶ 无法管理客户端的连接状态                                    │
│                                                                 │
│  如果没有 arthasSessionId                                        │
│  └─▶ 无法隔离不同客户端的命令执行                                 │
│  └─▶ 无法支持异步命令的持续运行                                  │
│                                                                 │
│  如果没有 consumerId                                             │
│  └─▶ 无法记录结果的消费位置                                      │
│  └─▶ 每次拉取都会返回所有结果（重复消费）                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## 7. 参考资料

- [JSON-RPC 2.0 规范](https://www.jsonrpc.org/specification)
- [MCP 协议规范](https://spec.modelcontextprotocol.io/)
- [Arthas 官方文档](https://arthas.aliyun.com/)

---

**文档版本：** 1.0  
**最后更新：** 2026-01-23  
**作者：** Arthas Team