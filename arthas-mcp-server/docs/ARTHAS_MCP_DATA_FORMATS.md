# Arthas MCP Server 数据格式详解

## 📋 目录

- [概述](#概述)
- [两种传输协议](#两种传输协议)
- [为什么需要两种格式](#为什么需要两种格式)
- [Streamable 模式（SSE + JSON）](#streamable-模式sse--json)
- [Stateless 模式（纯 JSON）](#stateless-模式纯-json)
- [协议对比](#协议对比)
- [客户端实现示例](#客户端实现示例)
- [常见问题](#常见问题)

---

## 概述

Arthas MCP Server 支持 **两种传输协议**，以适应不同的使用场景：

1. **Streamable 模式**（默认）：使用 **SSE (Server-Sent Events) + JSON-RPC**
2. **Stateless 模式**：使用 **纯 JSON-RPC over HTTP**

这两种模式在数据格式、会话管理、响应方式上有显著差异。

---

## 两种传输协议

### 协议配置

在 Arthas 启动时，可以通过配置选择协议：

```java
// 默认使用 Streamable 模式
ArthasMcpServer server = new ArthasMcpServer("/mcp", commandExecutor, "STREAMABLE");

// 或使用 Stateless 模式
ArthasMcpServer server = new ArthasMcpServer("/mcp", commandExecutor, "STATELESS");
```

### 协议枚举定义

```java
public enum ServerProtocol {
    STREAMABLE,  // SSE 流式传输（默认）
    STATELESS    // 无状态 HTTP 传输
}
```

---

## 为什么需要两种格式

### 设计动机

Arthas 的诊断命令可以分为两大类：

#### 1️⃣ **事件驱动型命令**（必须使用 SSE）

这类命令通过 **字节码增强（Bytecode Instrumentation）** 在目标方法执行时触发回调，每次方法调用都会产生一个新的结果。

**典型命令：**
- `watch` - 监控方法调用，每次方法执行产生一个结果
- `trace` - 追踪方法调用链路，每次调用产生一个调用树
- `monitor` - 定期统计方法调用次数、耗时等，每个周期产生一个统计结果
- `stack` - 输出方法调用堆栈，每次调用产生一个堆栈信息
- `tt` (TimeTunnel) - 记录方法调用现场，每次调用产生一个记录

**为什么必须用 SSE？**

```java
// watch 命令的工作原理
public class WatchCommand {
    // 1. 增强目标类的字节码，注入监控代码
    public void enhance(Class<?> targetClass) {
        // 在方法执行前后插入回调
    }
    
    // 2. 每次目标方法被调用时，触发回调
    public void onMethodInvoke(Object[] params, Object returnValue) {
        // 生成一个 WatchModel 结果
        WatchModel result = new WatchModel(params, returnValue, cost);
        
        // 通过 ResultDistributor 推送到客户端
        resultDistributor.appendResult(result);  // ← 实时推送！
    }
}
```

**关键特征：**
- ⏱️ **结果数量不确定**：取决于目标方法被调用多少次
- 🔄 **持续产生结果**：只要目标方法被调用，就会产生新结果
- ⚡ **实时性要求高**：需要立即看到每次方法调用的情况
- 🎯 **用户可中断**：用户可以随时停止监控（Ctrl+C）

**如果用 Stateless 模式会怎样？**
```
❌ 问题 1：无法知道何时结束
   - 目标方法可能被调用 1 次、100 次、或永远不被调用
   - HTTP 请求必须等到"结束"才能返回，但何时结束？

❌ 问题 2：结果可能非常大
   - 如果目标方法被调用 10000 次，一次性返回 10000 个结果
   - 内存占用巨大，响应时间极长

❌ 问题 3：无法实时查看
   - 用户必须等待命令完全结束才能看到结果
   - 失去了"实时诊断"的意义
```

#### 2️⃣ **查询型命令**（可以使用 Stateless）

这类命令执行一次查询，返回固定的结果集。

**典型命令：**
- `jvm` - 查询 JVM 信息（一次性返回）
- `sysprop` - 查询系统属性（一次性返回）
- `sysenv` - 查询环境变量（一次性返回）
- `sc` - 搜索类信息（一次性返回）
- `sm` - 搜索方法信息（一次性返回）
- `thread` - 查询线程信息（一次性返回）

**为什么可以用 Stateless？**

```java
// jvm 命令的工作原理
public class JvmCommand {
    public void execute() {
        // 1. 收集 JVM 信息
        JvmModel result = new JvmModel();
        result.setJavaVersion(System.getProperty("java.version"));
        result.setMemoryInfo(getMemoryInfo());
        // ...
        
        // 2. 一次性返回完整结果
        return result;  // ← 一次性返回！
    }
}
```

**关键特征：**
- ✅ **结果数量固定**：执行一次，返回一个结果
- ✅ **执行时间短**：通常在几毫秒到几秒内完成
- ✅ **无需实时性**：等待完整结果即可
- ✅ **无需中断**：执行完就结束

### 设计对比表

| 需求场景 | 适用协议 | 原因 |
|---------|---------|------|
| **事件驱动型命令** | Streamable (SSE) | 结果数量不确定，需要实时推送每个事件 |
| **查询型命令** | Stateless (JSON) | 结果数量固定，一次请求-响应即可完成 |
| **需要会话保持** | Streamable (SSE) | 支持多次交互、状态保持（如 `tt` 的回放） |
| **无状态集成** | Stateless (JSON) | 简单的 REST API 调用，无需管理会话 |
| **实时通知** | Streamable (SSE) | 服务端主动推送事件（如工具列表变更） |
| **防火墙友好** | Stateless (JSON) | 标准 HTTP POST，更容易通过企业防火墙 |

### 核心差异

```
┌─────────────────────────────────────────────────────────────┐
│                    Streamable 模式                           │
│  ┌──────────┐         SSE Stream          ┌──────────┐     │
│  │  Client  │ ◄─────────────────────────  │  Server  │     │
│  │          │                              │          │     │
│  │          │  POST (JSON-RPC Request)     │          │     │
│  │          │ ─────────────────────────►  │          │     │
│  └──────────┘                              └──────────┘     │
│     需要 Session ID                         有状态          │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                    Stateless 模式                            │
│  ┌──────────┐                             ┌──────────┐     │
│  │  Client  │  POST (JSON-RPC Request)    │  Server  │     │
│  │          │ ─────────────────────────►  │          │     │
│  │          │                              │          │     │
│  │          │  JSON-RPC Response           │          │     │
│  │          │ ◄─────────────────────────  │          │     │
│  └──────────┘                              └──────────┘     │
│     无需 Session                            无状态          │
└─────────────────────────────────────────────────────────────┘
```

### 技术实现：ResultDistributor 机制

Arthas 使用 **ResultDistributor（结果分发器）** 来实现实时结果推送。这是理解为什么需要 SSE 的关键。

#### 工作原理

```
┌─────────────────────────────────────────────────────────────┐
│                   事件驱动型命令的执行流程                    │
└─────────────────────────────────────────────────────────────┘

1. 客户端发送 watch 命令
   │
   ├─► 2. Arthas 增强目标类的字节码
   │      (在方法前后插入监控代码)
   │
   ├─► 3. 目标方法被调用（第 1 次）
   │      │
   │      ├─► 触发 AdviceListener 回调
   │      │
   │      ├─► 生成 WatchModel 结果
   │      │
   │      └─► ResultDistributor.appendResult(result)
   │             │
   │             └─► 通过 SSE 推送给客户端 ✅
   │
   ├─► 4. 目标方法被调用（第 2 次）
   │      │
   │      └─► 再次推送结果 ✅
   │
   ├─► 5. 目标方法被调用（第 3 次）
   │      │
   │      └─► 再次推送结果 ✅
   │
   └─► 6. 用户按 Ctrl+C 或达到限制条件
          │
          └─► 停止监控，关闭 SSE 连接
```

#### 代码示例

**服务端：ResultDistributor 推送结果**

```java
// 1. 命令执行时创建 ResultDistributor
public class WatchCommand {
    public void execute(CommandProcess process) {
        // 获取结果分发器
        ResultDistributor distributor = process.getSession().getResultDistributor();
        
        // 2. 增强目标类，注入监控代码
        AdviceListener listener = new AdviceListener() {
            @Override
            public void afterReturning(Advice advice) {
                // 3. 每次方法调用时，生成结果
                WatchModel result = new WatchModel();
                result.setParams(advice.getParams());
                result.setReturnObj(advice.getReturnObj());
                result.setCost(advice.getCost());
                
                // 4. 立即推送结果（不等待命令结束）
                distributor.appendResult(result);  // ← 关键！
            }
        };
        
        // 5. 注册监听器
        AdviceWeaver.reg(targetClass, listener);
    }
}

// ResultDistributor 的实现
public class SharingResultDistributorImpl implements ResultDistributor {
    private BlockingQueue<ResultModel> resultQueue = new LinkedBlockingQueue<>();
    
    @Override
    public void appendResult(ResultModel result) {
        // 将结果放入队列
        resultQueue.offer(result);
        
        // 通知所有订阅者（SSE 连接）
        notifySubscribers(result);
    }
    
    private void notifySubscribers(ResultModel result) {
        // 通过 SSE 推送给所有连接的客户端
        for (SseEmitter emitter : sseEmitters) {
            emitter.send(result);  // ← 实时推送！
        }
    }
}
```

**客户端：接收流式结果**

```python
# 客户端通过 SSE 接收实时结果
response = requests.post(url, json=request, headers=headers, stream=True)

# 持续读取 SSE 事件
for event in parse_sse_stream(response):
    if event.type == 'message':
        result = json.loads(event.data)
        print(f"收到结果: {result}")  # ← 实时显示！
```

#### 为什么查询型命令不需要 SSE？

```java
// 查询型命令的执行流程
public class JvmCommand {
    public void execute(CommandProcess process) {
        // 1. 收集 JVM 信息（一次性）
        JvmModel result = new JvmModel();
        result.setJavaVersion(System.getProperty("java.version"));
        result.setMemoryInfo(getMemoryInfo());
        // ...
        
        // 2. 一次性返回完整结果
        process.write(result);  // ← 一次性返回！
        
        // 3. 命令结束
        process.end();
    }
}
```

**关键区别：**

| 特性 | 事件驱动型（watch） | 查询型（jvm） |
|-----|-------------------|--------------|
| **结果产生方式** | 被动触发（方法被调用时） | 主动查询（执行时） |
| **结果数量** | 不确定（0 ~ ∞） | 固定（1 个） |
| **执行时间** | 不确定（持续监控） | 固定（几毫秒） |
| **是否需要 ResultDistributor** | ✅ 需要 | ❌ 不需要 |
| **是否需要 SSE** | ✅ 必须 | ❌ 可选 |

---

## Streamable 模式（SSE + JSON）

### 特点

- ✅ **有状态会话**：需要先初始化获取 Session ID
- ✅ **双向通信**：客户端发送请求，服务端可主动推送
- ✅ **流式响应**：支持长时间运行的命令，实时返回结果
- ✅ **Keep-Alive**：支持心跳保持连接（默认 15 秒）
- ✅ **事件通知**：服务端可主动通知客户端（如工具列表变更）

### 工作流程

```
1. Initialize (POST)
   ├─ 客户端发送 initialize 请求
   ├─ 服务端返回 Session ID（在 HTTP Header 中）
   └─ 响应格式：纯 JSON

2. 建立 SSE 连接 (GET)
   ├─ 客户端带上 Session ID
   ├─ 服务端返回 SSE 流
   └─ 响应格式：text/event-stream

3. 发送命令 (POST)
   ├─ 客户端带上 Session ID
   ├─ 服务端通过 SSE 推送结果
   └─ 响应格式：text/event-stream

4. 删除会话 (DELETE)
   └─ 清理服务端会话资源
```

### 请求示例

#### 1. 初始化会话

**请求：**
```http
POST /mcp HTTP/1.1
Host: localhost:8563
Content-Type: application/json
Accept: text/event-stream, application/json

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-03-26",
    "capabilities": {},
    "clientInfo": {
      "name": "arthas-client",
      "version": "1.0.0"
    }
  }
}
```

**响应：**
```http
HTTP/1.1 200 OK
Content-Type: application/json
mcp-session-id: 22c1c4e5-f722-4ea7-889e-427f67c4b590

{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2025-03-26",
    "capabilities": {
      "tools": { "listChanged": true },
      "resources": { "subscribe": false, "listChanged": true },
      "prompts": { "listChanged": true }
    },
    "serverInfo": {
      "name": "arthas-mcp-server",
      "version": "4.1.5"
    }
  }
}
```

#### 2. 建立 SSE 连接

**请求：**
```http
GET /mcp HTTP/1.1
Host: localhost:8563
Accept: text/event-stream
mcp-session-id: 22c1c4e5-f722-4ea7-889e-427f67c4b590
```

**响应：**
```http
HTTP/1.1 200 OK
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive
Transfer-Encoding: chunked

(保持连接，等待服务端推送事件)
```

#### 3. 调用工具（通过 POST）

**请求：**
```http
POST /mcp HTTP/1.1
Host: localhost:8563
Content-Type: application/json
Accept: text/event-stream, application/json
mcp-session-id: 22c1c4e5-f722-4ea7-889e-427f67c4b590

{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "thread",
    "arguments": {
      "n": 5
    }
  }
}
```

**响应（通过 SSE 推送）：**
```http
HTTP/1.1 200 OK
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive

id: 22c1c4e5-f722-4ea7-889e-427f67c4b590
event: message
data: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"线程信息..."}]}}

```

### SSE 事件格式

SSE 使用纯文本格式，每个事件包含：

```
id: <事件ID>
event: <事件类型>
data: <JSON 数据>

```

**关键点：**
- 每个字段一行，格式为 `字段名: 值`
- 事件之间用**空行**（`\n\n`）分隔
- `data` 字段包含 JSON-RPC 消息

### 实现代码

**服务端发送 SSE 事件：**
```java
private void sendSseEvent(String eventType, String data, String id) {
    StringBuilder sseData = new StringBuilder();
    if (id != null) {
        sseData.append("id: ").append(id).append("\n");
    }
    sseData.append("event: ").append(eventType).append("\n");
    sseData.append("data: ").append(data).append("\n\n");

    ByteBuf buffer = Unpooled.copiedBuffer(sseData.toString(), CharsetUtil.UTF_8);
    ctx.writeAndFlush(new DefaultHttpContent(buffer));
}
```

**客户端解析 SSE：**
```python
# 按空行分割事件
events = response.content.decode('utf-8').split('\n\n')

for event_block in events:
    current_event = None
    current_data = None
    
    for line in event_block.split('\n'):
        if line.startswith('event:'):
            current_event = line[6:].strip()
        elif line.startswith('data:'):
            current_data = line[5:].strip()
    
    if current_data and current_event == 'message':
        data = json.loads(current_data)
        # 处理 JSON-RPC 响应
```

---

## Stateless 模式（纯 JSON）

### 特点

- ✅ **无状态**：每次请求独立，无需会话管理
- ✅ **简单**：标准的 HTTP POST + JSON 响应
- ✅ **防火墙友好**：标准 HTTP 协议
- ❌ **不支持流式响应**：一次性返回完整结果
- ❌ **不支持服务端推送**：无法主动通知客户端

### 工作流程

```
1. 发送请求 (POST)
   ├─ 客户端发送 JSON-RPC 请求
   ├─ 服务端处理并返回完整结果
   └─ 响应格式：application/json

无需初始化，无需会话管理
```

### 请求示例

#### 1. 初始化（可选）

**请求：**
```http
POST /mcp HTTP/1.1
Host: localhost:8563
Content-Type: application/json
Accept: text/event-stream, application/json

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-03-26",
    "capabilities": {},
    "clientInfo": {
      "name": "arthas-client",
      "version": "1.0.0"
    }
  }
}
```

**响应：**
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2025-03-26",
    "capabilities": {
      "tools": { "listChanged": true },
      "resources": { "subscribe": false, "listChanged": true },
      "prompts": { "listChanged": true }
    },
    "serverInfo": {
      "name": "arthas-mcp-server",
      "version": "4.1.5"
    }
  }
}
```

#### 2. 调用工具

**请求：**
```http
POST /mcp HTTP/1.1
Host: localhost:8563
Content-Type: application/json
Accept: text/event-stream, application/json

{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "jvm",
    "arguments": {}
  }
}
```

**响应：**
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"command\":\"jvm\",\"results\":[...]}"
      }
    ]
  }
}
```

### 实现代码

**服务端处理：**
```java
private void handlePostRequest(ChannelHandlerContext ctx, FullHttpRequest request) {
    // 解析 JSON-RPC 请求
    McpSchema.JSONRPCMessage message = McpSchema.deserializeJsonRpcMessage(objectMapper, body);
    
    if (message instanceof McpSchema.JSONRPCRequest) {
        McpSchema.JSONRPCRequest jsonrpcRequest = (McpSchema.JSONRPCRequest) message;
        
        // 处理请求并返回 JSON 响应
        this.mcpHandler.handleRequest(transportContext, jsonrpcRequest)
            .thenAccept(jsonrpcResponse -> {
                FullHttpResponse response = new DefaultFullHttpResponse(
                    HttpVersion.HTTP_1_1,
                    HttpResponseStatus.OK,
                    Unpooled.copiedBuffer(objectMapper.writeValueAsString(jsonrpcResponse), CharsetUtil.UTF_8)
                );
                
                response.headers().set(HttpHeaderNames.CONTENT_TYPE, APPLICATION_JSON);
                ctx.writeAndFlush(response).addListener(ChannelFutureListener.CLOSE);
            });
    }
}
```

---

## 协议对比

### 功能对比表

| 特性 | Streamable (SSE) | Stateless (JSON) |
|-----|------------------|------------------|
| **会话管理** | ✅ 需要 Session ID | ❌ 无需会话 |
| **流式响应** | ✅ 支持 | ❌ 不支持 |
| **服务端推送** | ✅ 支持 | ❌ 不支持 |
| **Keep-Alive** | ✅ 15秒心跳 | ❌ 无 |
| **长时间命令** | ✅ 适合 | ⚠️ 可能超时 |
| **简单查询** | ✅ 支持 | ✅ 更简单 |
| **防火墙友好** | ⚠️ 需要长连接 | ✅ 标准 HTTP |
| **客户端复杂度** | ⚠️ 较高 | ✅ 简单 |
| **适用场景** | 交互式诊断 | 自动化脚本 |

### HTTP 头要求对比

#### Streamable 模式

```http
# 初始化请求
Content-Type: application/json
Accept: text/event-stream, application/json

# 后续请求
Content-Type: application/json
Accept: text/event-stream, application/json
mcp-session-id: <session-id>
```

#### Stateless 模式

```http
# 所有请求
Content-Type: application/json
Accept: text/event-stream, application/json
```

**注意：** 即使是 Stateless 模式，Accept 头也必须同时包含 `text/event-stream` 和 `application/json`，这是 MCP 协议的要求。

### 错误处理对比

#### Streamable 模式

```http
# 缺少 Session ID
HTTP/1.1 400 Bad Request
Content-Type: application/json

{
  "message": "Session ID required in mcp-session-id header",
  "stackTrace": [...]
}
```

#### Stateless 模式

```http
# 缺少 Accept 头
HTTP/1.1 400 Bad Request
Content-Type: application/json

{
  "message": "Both application/json and text/event-stream required in Accept header",
  "stackTrace": [...]
}
```

---

## 客户端实现示例

### Python 客户端（Streamable 模式）

```python
import requests
import json

class ArthasMcpClient:
    def __init__(self, base_url="http://localhost:8563"):
        self.base_url = base_url
        self.session_id = None
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json"
        }
    
    def initialize(self):
        """初始化会话"""
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {
                    "name": "python-client",
                    "version": "1.0.0"
                }
            }
        }
        
        response = requests.post(
            f"{self.base_url}/mcp",
            json=request,
            headers=self.headers
        )
        
        # 保存 Session ID
        self.session_id = response.headers.get('mcp-session-id')
        self.headers['mcp-session-id'] = self.session_id
        
        return response.json()
    
    def call_tool(self, tool_name, arguments=None):
        """调用工具"""
        request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {}
            }
        }
        
        response = requests.post(
            f"{self.base_url}/mcp",
            json=request,
            headers=self.headers,
            stream=True
        )
        
        # 解析 SSE 响应
        content = response.content.decode('utf-8')
        events = content.split('\n\n')
        
        for event_block in events:
            for line in event_block.split('\n'):
                if line.startswith('data:'):
                    data = json.loads(line[5:].strip())
                    return data
        
        return None

# 使用示例
client = ArthasMcpClient()
client.initialize()
result = client.call_tool("jvm", {})
print(result)
```

### Python 客户端（Stateless 模式）

```python
import requests
import json

class ArthasStatelessClient:
    def __init__(self, base_url="http://localhost:8563"):
        self.base_url = base_url
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json"
        }
    
    def call_tool(self, tool_name, arguments=None):
        """调用工具（无需初始化）"""
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {}
            }
        }
        
        response = requests.post(
            f"{self.base_url}/mcp",
            json=request,
            headers=self.headers
        )
        
        return response.json()

# 使用示例
client = ArthasStatelessClient()
result = client.call_tool("jvm", {})
print(result)
```

### cURL 示例

#### Streamable 模式

```bash
# 1. 初始化
curl -X POST http://localhost:8563/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream, application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2025-03-26",
      "capabilities": {},
      "clientInfo": {"name": "curl-client", "version": "1.0.0"}
    }
  }' -i

# 2. 调用工具（使用返回的 Session ID）
curl -X POST http://localhost:8563/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream, application/json" \
  -H "mcp-session-id: <your-session-id>" \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {
      "name": "jvm",
      "arguments": {}
    }
  }'
```

#### Stateless 模式

```bash
# 直接调用（无需初始化）
curl -X POST http://localhost:8563/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream, application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "jvm",
      "arguments": {}
    }
  }'
```

---

## 常见问题

### 1. 为什么 Stateless 模式也要求 `text/event-stream` 在 Accept 头中？

**答：** 这是 MCP 协议规范的要求。即使 Stateless 模式不使用 SSE，但为了保持协议一致性，仍然要求客户端声明支持两种格式。

**代码验证：**
```java
// McpStatelessHttpRequestHandler.java
String accept = request.headers().get(ACCEPT);
if (accept == null || !(accept.contains(APPLICATION_JSON) && accept.contains(TEXT_EVENT_STREAM))) {
    sendError(ctx, HttpResponseStatus.BAD_REQUEST,
            new McpError("Both application/json and text/event-stream required in Accept header"));
    return;
}
```

### 2. 如何选择使用哪种模式？

**决策树：**

```
需要实时推送中间结果？
├─ 是 → Streamable 模式
└─ 否
    └─ 需要服务端主动通知？
        ├─ 是 → Streamable 模式
        └─ 否
            └─ 命令执行时间 > 30秒？
                ├─ 是 → Streamable 模式
                └─ 否 → Stateless 模式（更简单）
```

**推荐场景：**

- **Streamable 模式（必须使用）：**
  
  - **`watch`** - 监控方法调用
    ```bash
    # 监控 UserService.login 方法
    # 每次用户登录时，实时输出参数和返回值
    watch com.example.UserService login '{params, returnObj}' -x 2
    
    # 可能产生的结果：
    # 第 1 次调用：params=[user1, pass123], returnObj=true
    # 第 2 次调用：params=[user2, pass456], returnObj=false
    # 第 3 次调用：params=[user3, pass789], returnObj=true
    # ... 持续监控，直到用户按 Ctrl+C 停止
    ```
    **为什么需要 SSE？** 每次方法调用都是一个独立的事件，需要立即推送给客户端。
  
  - **`trace`** - 追踪方法调用链路
    ```bash
    # 追踪 OrderService.createOrder 的调用链路
    trace com.example.OrderService createOrder
    
    # 每次调用产生一个调用树：
    # `---[15.2ms] com.example.OrderService:createOrder()
    #     +---[2.1ms] com.example.InventoryService:checkStock()
    #     +---[8.3ms] com.example.PaymentService:processPayment()
    #     `---[4.5ms] com.example.NotificationService:sendEmail()
    ```
    **为什么需要 SSE？** 每次调用的链路不同，需要实时展示每个调用树。
  
  - **`monitor`** - 定期统计方法调用
    ```bash
    # 每 5 秒统计一次方法调用情况
    monitor -c 5 com.example.UserService login
    
    # 第 1 个周期：total=10, success=8, fail=2, avg-rt=120ms
    # 第 2 个周期：total=15, success=12, fail=3, avg-rt=135ms
    # 第 3 个周期：total=8, success=8, fail=0, avg-rt=95ms
    # ... 持续统计，直到用户停止
    ```
    **为什么需要 SSE？** 每个统计周期产生一个结果，需要持续推送。
  
  - **`tt` (TimeTunnel)** - 记录方法调用现场
    ```bash
    # 记录方法调用，后续可以回放
    tt -t com.example.UserService login
    
    # 记录 1：timestamp=14:30:01, params=[user1], cost=120ms
    # 记录 2：timestamp=14:30:05, params=[user2], cost=95ms
    # 记录 3：timestamp=14:30:12, params=[user3], cost=150ms
    # ... 持续记录
    ```
    **为什么需要 SSE？** 每次调用都需要记录，且支持后续交互（如回放）。

- **Stateless 模式（推荐使用）：**
  
  - **`jvm`** - 查询 JVM 信息
    ```bash
    # 一次性查询 JVM 信息
    jvm
    
    # 返回：
    # {
    #   "javaVersion": "1.8.0_452",
    #   "javaHome": "/usr/lib/jvm/java-8",
    #   "maxMemory": "4096MB",
    #   "totalMemory": "2048MB",
    #   ...
    # }
    ```
    **为什么可以用 Stateless？** 一次查询，一次返回，结果固定。
  
  - **`thread`** - 查询线程信息
    ```bash
    # 查询当前线程快照
    thread -n 10
    
    # 返回前 10 个线程的信息（一次性）
    ```
    **为什么可以用 Stateless？** 查询当前状态，结果数量固定。
  
  - **`sc`** - 搜索类信息
    ```bash
    # 搜索 UserService 类
    sc *UserService
    
    # 返回匹配的类列表（一次性）
    ```
    **为什么可以用 Stateless？** 搜索结果固定，一次返回即可。

### 3. 实际例子：watch 命令为什么必须用 SSE？

让我们通过一个实际场景来理解：

**场景：** 监控生产环境中的登录方法，查看为什么有些用户登录失败。

```bash
# 执行 watch 命令
watch com.example.UserService login '{params, returnObj, throwExp}' -x 2
```

**如果使用 Stateless 模式（HTTP 请求-响应）：**

```
❌ 问题 1：何时返回响应？
   - 客户端发送请求后，服务端开始监控
   - 但是何时返回响应？
     • 监控 1 次方法调用后？
     • 监控 10 次？
     • 监控 1 小时？
   - HTTP 请求必须等待"完成"才能返回，但监控命令没有明确的结束点

❌ 问题 2：内存占用
   - 假设在 1 小时内，login 方法被调用了 10000 次
   - 服务端必须在内存中缓存所有 10000 个结果
   - 然后一次性返回给客户端
   - 内存占用：10000 * 1KB = 10MB+

❌ 问题 3：无法实时查看
   - 用户必须等待 1 小时后才能看到结果
   - 无法实时发现问题
   - 失去了"实时诊断"的意义

❌ 问题 4：无法中断
   - 用户发现已经找到问题，想停止监控
   - 但是 HTTP 请求已经发出，无法中断
   - 必须等待服务端"自然结束"
```

**使用 Streamable 模式（SSE）：**

```
✅ 优势 1：实时推送
   - 每次 login 方法被调用时，立即推送结果
   - 用户可以实时看到每次登录的情况
   
   时间轴：
   14:30:01 → 推送结果 1：user=alice, success=true
   14:30:05 → 推送结果 2：user=bob, success=false, error=密码错误
   14:30:12 → 推送结果 3：user=charlie, success=true
   ...

✅ 优势 2：低内存占用
   - 服务端不需要缓存所有结果
   - 每个结果推送后即可释放
   - 内存占用：1 * 1KB = 1KB

✅ 优势 3：用户可控
   - 用户看到 bob 登录失败的原因后
   - 按 Ctrl+C 立即停止监控
   - 服务端收到停止信号，关闭 SSE 连接

✅ 优势 4：支持长时间监控
   - 可以监控 1 小时、1 天、甚至更长
   - 不会因为 HTTP 超时而中断
   - SSE 连接支持 Keep-Alive
```

**实际代码对比：**

```python
# ❌ 如果用 Stateless 模式（假设）
response = requests.post(url, json={
    "method": "tools/call",
    "params": {
        "name": "watch",
        "arguments": {"classPattern": "UserService", "methodPattern": "login"}
    }
})

# 问题：这个请求何时返回？
# 答案：无法确定！可能永远不返回（如果方法一直不被调用）

result = response.json()  # ← 阻塞在这里，等待"完成"
print(result)  # ← 可能要等很久才能看到结果


# ✅ 使用 Streamable 模式（SSE）
response = requests.post(url, json=request, headers=headers, stream=True)

# 实时接收每个结果
for event in parse_sse_stream(response):
    result = json.loads(event.data)
    print(f"[{result['ts']}] {result['params']} → {result['returnObj']}")
    
    # 用户可以随时按 Ctrl+C 停止
    if user_wants_to_stop():
        break  # ← 立即停止！
```

**总结对比：**

| 特性 | Stateless 模式 | Streamable 模式 (SSE) |
|-----|---------------|---------------------|
| **适用命令** | 查询型（jvm, thread, sc） | 事件驱动型（watch, trace, monitor） |
| **结果数量** | 固定（1 个） | 不确定（0 ~ ∞） |
| **返回时机** | 立即返回 | 持续推送 |
| **内存占用** | 低（结果小） | 低（不缓存） |
| **实时性** | 无需实时 | 必须实时 |
| **用户控制** | 无需控制 | 可随时停止 |
| **执行时间** | 几毫秒~几秒 | 不确定（可能很长） |

### 4. SSE 连接断开后如何恢复？

**Streamable 模式支持两种恢复策略：**

1. **完全重新初始化**（推荐）
   ```python
   # 重新调用 initialize 获取新的 Session ID
   client.initialize()
   ```

2. **基于 Last-Event-ID 恢复**（当前不支持）
   ```http
   GET /mcp HTTP/1.1
   Last-Event-ID: <last-event-id>
   ```
   
   **注意：** Arthas 当前版本不支持基于 `Last-Event-ID` 的消息回放，会返回 404 错误。

### 4. 如何处理 UTF-8 编码问题？

**问题：** SSE 响应中的中文显示为乱码。

**原因：** 按行读取时可能截断 UTF-8 多字节字符。

**解决方案：**
```python
# ❌ 错误：按行读取
for line in response.iter_lines():
    print(line.decode('utf-8'))  # 可能出现编码错误

# ✅ 正确：读取完整内容后再分割
content = response.content.decode('utf-8')
events = content.split('\n\n')
```

### 5. 如何调试 SSE 响应？

**方法 1：使用 curl**
```bash
curl -N -H "Accept: text/event-stream" \
     -H "mcp-session-id: <session-id>" \
     http://localhost:8563/mcp
```

**方法 2：使用 Python 脚本**
```python
response = requests.post(url, json=request, headers=headers, stream=True)
print("Status:", response.status_code)
print("Headers:", dict(response.headers))
print("Content:", response.content.decode('utf-8'))
```

**方法 3：查看服务端日志**
```java
logger.debug("Sending SSE message to session {}: {}", sessionId, jsonText);
```

---

## 总结

Arthas MCP Server 的双协议设计体现了**灵活性**和**实用性**的平衡：

### 命令分类与协议选择

```
┌─────────────────────────────────────────────────────────────┐
│                    Arthas 命令分类                           │
└─────────────────────────────────────────────────────────────┘

📊 事件驱动型命令（必须使用 Streamable/SSE）
   ├─ watch      - 监控方法调用（每次调用产生一个结果）
   ├─ trace      - 追踪调用链路（每次调用产生一个调用树）
   ├─ monitor    - 定期统计（每个周期产生一个统计结果）
   ├─ stack      - 输出调用堆栈（每次调用产生一个堆栈）
   ├─ tt         - 记录调用现场（每次调用产生一个记录）
   └─ dashboard  - 实时面板（定期刷新数据）
   
   特征：
   • 结果数量不确定（0 ~ ∞）
   • 持续产生结果
   • 需要实时推送
   • 用户可随时中断

📋 查询型命令（推荐使用 Stateless/JSON）
   ├─ jvm        - 查询 JVM 信息
   ├─ thread     - 查询线程信息
   ├─ memory     - 查询内存信息
   ├─ sysprop    - 查询系统属性
   ├─ sysenv     - 查询环境变量
   ├─ sc         - 搜索类信息
   ├─ sm         - 搜索方法信息
   └─ jad        - 反编译类
   
   特征：
   • 结果数量固定（1 个）
   • 一次性返回
   • 执行时间短
   • 无需实时性
```

### 核心设计原则

1. **事件驱动型命令 → Streamable (SSE)**
   - 通过字节码增强在方法执行时触发回调
   - 每次方法调用产生一个新结果
   - 使用 ResultDistributor 实时推送
   - 支持长时间运行和用户中断

2. **查询型命令 → Stateless (JSON)**
   - 执行一次查询，返回固定结果
   - 无需会话管理，简单高效
   - 适合自动化脚本和 REST API 集成

### 选择建议

| 场景 | 推荐协议 | 原因 |
|-----|---------|------|
| 实时诊断问题 | Streamable | 需要立即看到每次方法调用的情况 |
| 性能分析 | Streamable | 需要持续监控方法耗时和调用链路 |
| 自动化脚本 | Stateless | 简单的查询，无需会话管理 |
| CI/CD 集成 | Stateless | 标准 HTTP API，易于集成 |
| 长时间监控 | Streamable | 支持 Keep-Alive，不会超时 |
| 快速查询 | Stateless | 一次请求-响应，更简单 |

选择合适的协议可以显著提升开发效率和用户体验。

---

## 参考资料

- [MCP 协议规范](https://spec.modelcontextprotocol.io/)
- [SSE (Server-Sent Events) 标准](https://html.spec.whatwg.org/multipage/server-sent-events.html)
- [JSON-RPC 2.0 规范](https://www.jsonrpc.org/specification)
- [Arthas 官方文档](https://arthas.aliyun.com/)

---

**文档版本：** 1.0  
**最后更新：** 2026-01-17  
**作者：** Arthas Team
