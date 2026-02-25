# SSE 与 HTTP POST 通信机制分析

## 📌 核心问题

**为什么 Arthas MCP Server 不能"都用 SSE"，而是需要 SSE + HTTP POST 的组合？**

## 🎯 核心答案

**SSE (Server-Sent Events) 是单向通信协议，只能服务端推送给客户端，客户端无法通过 SSE 发送数据！**

---

## 1. SSE 的本质限制

### 1.1 SSE 协议特性

SSE 是基于 HTTP 的单向推送协议：

```
客户端发起连接：
GET /mcp HTTP/1.1
Accept: text/event-stream

服务端响应：
HTTP/1.1 200 OK
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive

event: message
data: {"result": "..."}

event: message
data: {"result": "..."}
```

**关键特点**：
- ✅ 服务端可以持续推送数据
- ❌ 客户端无法通过这个连接发送数据
- ✅ 自动重连机制
- ✅ 基于标准 HTTP

### 1.2 浏览器 API 验证

```javascript
// 浏览器中的 SSE API
const eventSource = new EventSource('/mcp');

// ✅ 可以接收数据
eventSource.onmessage = (event) => {
    console.log('收到数据:', event.data);
};

// ❌ 无法发送数据！EventSource API 没有 send() 方法
eventSource.send({...});  // TypeError: eventSource.send is not a function

// 必须用 fetch 或 XMLHttpRequest 发送数据
fetch('/mcp', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({method: 'tools/call', params: {...}})
});
```

---

## 2. 为什么需要 HTTP POST？

### 2.1 双向通信需求

MCP 协议需要双向通信：

```
客户端 → 服务端：
- 发送工具调用请求
- 发送初始化参数
- 发送取消命令

服务端 → 客户端：
- 推送工具执行结果
- 推送进度通知
- 推送错误信息
```

### 2.2 Server 模式的通信流程

```
┌──────────┐                           ┌──────────┐
│  客户端   │                           │  Arthas  │
└────┬─────┘                           └────┬─────┘
     │                                      │
     │ ① GET /mcp (建立 SSE 连接)           │
     │ ────────────────────────────────────►│
     │    Accept: text/event-stream         │
     │                                      │
     │ ② POST /mcp (发送工具调用请求)       │
     │ ────────────────────────────────────►│
     │    {"method": "tools/call", ...}     │
     │                                      │
     │ ③ SSE event (推送进度通知)           │
     │ ◄────────────────────────────────────│
     │    event: progress                   │
     │    data: {"progress": 5/10}          │
     │                                      │
     │ ④ SSE event (推送最终结果)           │
     │ ◄────────────────────────────────────│
     │    event: message                    │
     │    data: {"results": [...]}          │
     │                                      │
```

**关键点**：
- SSE 连接用于接收服务端推送（步骤 ③④）
- HTTP POST 用于发送客户端请求（步骤 ②）
- 两者缺一不可

### 2.3 Client 模式的通信流程

```
┌──────────┐                           ┌──────────┐
│  Arthas  │                           │ 管控平台  │
└────┬─────┘                           └────┬─────┘
     │                                      │
     │ ① GET /mcp (建立 SSE 连接)           │
     │ ────────────────────────────────────►│
     │    Accept: text/event-stream         │
     │                                      │
     │ ② SSE event (推送工具调用请求)       │
     │ ◄────────────────────────────────────│
     │    event: message                    │
     │    data: {"method": "tools/call"}    │
     │                                      │
     │ ③ POST /mcp (发送工具执行结果)       │
     │ ────────────────────────────────────►│
     │    {"results": [...]}                │
     │                                      │
```

**关键点**：
- SSE 连接用于接收管控平台的请求（步骤 ②）
- HTTP POST 用于发送执行结果（步骤 ③）
- 方向与 Server 模式相反，但原理相同

---

## 3. 为什么不用 WebSocket？

### 3.1 技术对比

| 特性 | WebSocket | SSE + HTTP POST |
|------|-----------|-----------------|
| **双向通信** | ✅ 原生支持 | ✅ 通过组合实现 |
| **协议复杂度** | ❌ 需要握手、心跳、重连 | ✅ 基于标准 HTTP |
| **防火墙友好** | ❌ 可能被阻止 | ✅ 标准 HTTP 端口 |
| **调试难度** | ❌ 需要特殊工具 | ✅ 可用 curl 测试 |
| **客户端库** | ❌ 需要特殊库 | ✅ 浏览器原生支持 |
| **自动重连** | ❌ 需要手动实现 | ✅ SSE 规范内置 |
| **连接数** | ✅ 单一连接 | ❌ 需要两个连接 |
| **延迟** | ✅ 低延迟 | ⚠️ POST 有建连开销 |

### 3.2 MCP 协议的设计目标

根据 MCP 协议规范，设计目标是：

1. **简单易用 (Simple)**
   - SSE + POST 比 WebSocket 更简单
   - POST 请求是无状态的，不需要管理连接状态

2. **防火墙友好 (Firewall-friendly)**
   - 都是标准 HTTP 协议
   - 不需要特殊端口或协议升级

3. **易于调试 (Debuggable)**
   - 可以用 `curl` 发送 POST 请求
   - 可以用浏览器 `EventSource` API 测试 SSE

4. **渐进增强 (Progressive Enhancement)**
   - 不需要 SSE 也能工作（只用 POST，轮询模式）
   - 需要进度通知时再启用 SSE

### 3.3 实际场景考虑

```
企业内网环境：
┌─────────────────────────────────────────────────────────┐
│  防火墙/代理服务器                                       │
│                                                         │
│  ✅ 允许 HTTP/HTTPS (80/443)                            │
│  ✅ 允许 GET/POST 请求                                  │
│  ✅ 允许 text/event-stream                              │
│                                                         │
│  ❌ 可能阻止 WebSocket (协议升级)                        │
│  ❌ 可能阻止非标准端口                                   │
│  ❌ 可能不支持 Upgrade 头                                │
└─────────────────────────────────────────────────────────┘

结论：SSE + HTTP POST 更容易通过企业防火墙
```

---

## 4. 代码实现分析

### 4.1 Server 模式：接收 POST 请求

```java
// McpStreamableHttpRequestHandler.java
@Override
protected void channelRead0(ChannelHandlerContext ctx, FullHttpRequest request) {
    if (request.method() == HttpMethod.POST) {
        // ✅ 处理客户端发送的工具调用请求
        String body = request.content().toString(CharsetUtil.UTF_8);
        Map<String, Object> message = parseJson(body);
        
        // 执行工具并通过 SSE 推送结果
        executeToolAndSendResponse(ctx, message);
    }
}

private void sendSseEvent(ChannelHandlerContext ctx, String eventType, String data) {
    // ✅ 通过 SSE 推送响应
    StringBuilder sseData = new StringBuilder();
    sseData.append("event: ").append(eventType).append("\n");
    sseData.append("data: ").append(data).append("\n\n");
    
    ByteBuf buffer = Unpooled.copiedBuffer(sseData.toString(), CharsetUtil.UTF_8);
    ctx.writeAndFlush(new DefaultHttpContent(buffer));
}
```

### 4.2 Client 模式：发送 POST 响应

```java
// McpHttpClient.java
private void sendHttpPost(String url, Map<String, Object> message) {
    // ✅ 通过 HTTP POST 发送工具执行结果
    HttpRequest request = HttpRequest.newBuilder()
        .uri(URI.create(url))
        .header("Content-Type", "application/json")
        .POST(HttpRequest.BodyPublishers.ofString(toJson(message)))
        .build();
    
    httpClient.sendAsync(request, HttpResponse.BodyHandlers.ofString());
}

// SseHandler.java
private void handleSseEvent(String eventData) {
    // ✅ 接收通过 SSE 推送的工具调用请求
    Map<String, Object> message = parseJson(eventData);
    
    // 执行工具并通过 POST 发送结果
    Map<String, Object> result = executeTool(message);
    sendHttpPost(responseUrl, result);
}
```

### 4.3 为什么 SSE 无法发送数据

```java
// Netty SSE 实现
public class SseHandler extends SimpleChannelInboundHandler<HttpObject> {
    @Override
    protected void channelRead0(ChannelHandlerContext ctx, HttpObject msg) {
        if (msg instanceof HttpRequest) {
            HttpRequest request = (HttpRequest) msg;
            
            if (request.method() == HttpMethod.GET) {
                // ✅ 建立 SSE 连接
                HttpResponse response = new DefaultHttpResponse(
                    HttpVersion.HTTP_1_1, HttpResponseStatus.OK);
                response.headers().set(HttpHeaderNames.CONTENT_TYPE, "text/event-stream");
                response.headers().set(HttpHeaderNames.CACHE_CONTROL, "no-cache");
                response.headers().set(HttpHeaderNames.CONNECTION, "keep-alive");
                ctx.write(response);
                
                // 持续推送数据
                ctx.writeAndFlush(Unpooled.copiedBuffer(
                    "event: message\ndata: {...}\n\n", CharsetUtil.UTF_8));
            }
        }
        
        // ❌ SSE 连接建立后，客户端无法通过这个连接发送数据
        // 如果客户端尝试发送数据，会被视为新的 HTTP 请求
        // 因此必须通过另一个 POST 请求来发送数据
    }
}
```

---

## 5. 三种通信方案对比

### 5.1 方案 1：SSE + HTTP POST（当前方案 ✅）

#### 通信流程

```
┌──────────┐                           ┌──────────┐
│  客户端   │                           │  服务端   │
└────┬─────┘                           └────┬─────┘
     │                                      │
     │ ① GET /mcp (建立 SSE 连接)           │
     │ ────────────────────────────────────►│
     │    Accept: text/event-stream         │
     │    Connection: keep-alive            │
     │                                      │
     │ ② POST /mcp (发送请求)               │
     │ ────────────────────────────────────►│
     │    {"method": "tools/call"}          │
     │                                      │
     │ ③ SSE event (推送进度)               │
     │ ◄────────────────────────────────────│
     │    event: progress                   │
     │    data: {"progress": 30%}           │
     │                                      │
     │ ④ SSE event (推送结果)               │
     │ ◄────────────────────────────────────│
     │    event: message                    │
     │    data: {"results": [...]}          │
     │                                      │
```

#### 代码示例

```java
// 客户端代码
public class SseHttpClient {
    private EventSource sseConnection;
    private HttpClient httpClient;
    
    public void connect(String url) {
        // ① 建立 SSE 连接（接收推送）
        sseConnection = new EventSource(url);
        sseConnection.onMessage(event -> {
            System.out.println("收到推送: " + event.getData());
        });
        
        httpClient = HttpClient.newHttpClient();
    }
    
    public void callTool(String toolName, Map<String, Object> args) {
        // ② 发送 POST 请求（调用工具）
        HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create(url))
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(toJson(message)))
            .build();
        
        httpClient.sendAsync(request, HttpResponse.BodyHandlers.ofString());
        // ③④ 结果通过 SSE 连接推送回来
    }
}
```

#### 优缺点

```
优点：
✅ 基于标准 HTTP，防火墙友好
✅ POST 请求无状态，易于实现
✅ 可以用 curl 测试
✅ SSE 自动重连
✅ 浏览器原生支持
✅ 易于调试（Chrome DevTools 可查看）
✅ 支持渐进增强（可降级为纯 POST）

缺点：
❌ 需要两个连接（SSE + POST）
❌ POST 请求有建连开销
❌ 不适合高频双向通信

适用场景：
✅ 企业内网环境
✅ 需要易于调试
✅ 需要渐进增强
✅ 偶尔调用工具，不需要高频通信
```

---

### 5.2 方案 2：WebSocket（备选方案）

#### 通信流程

```
┌──────────┐                           ┌──────────┐
│  客户端   │                           │  服务端   │
└────┬─────┘                           └────┬─────┘
     │                                      │
     │ ① GET /mcp (WebSocket 握手)          │
     │ ────────────────────────────────────►│
     │    Upgrade: websocket                │
     │    Connection: Upgrade               │
     │                                      │
     │ ② 101 Switching Protocols            │
     │ ◄────────────────────────────────────│
     │    Upgrade: websocket                │
     │                                      │
     │ ③ WS Frame (发送请求)                │
     │ ────────────────────────────────────►│
     │    {"method": "tools/call"}          │
     │                                      │
     │ ④ WS Frame (推送进度)                │
     │ ◄────────────────────────────────────│
     │    {"progress": 30%}                 │
     │                                      │
     │ ⑤ WS Frame (推送结果)                │
     │ ◄────────────────────────────────────│
     │    {"results": [...]}                │
     │                                      │
     │ ⑥ Ping/Pong (心跳保活)               │
     │ ◄───────────────────────────────────►│
     │                                      │
```

#### 代码示例

```java
// 客户端代码
public class WebSocketClient {
    private WebSocket webSocket;
    
    public void connect(String url) {
        // ① 建立 WebSocket 连接
        webSocket = HttpClient.newHttpClient()
            .newWebSocketBuilder()
            .buildAsync(URI.create(url), new WebSocket.Listener() {
                @Override
                public CompletionStage<?> onText(WebSocket webSocket, 
                                                  CharSequence data, 
                                                  boolean last) {
                    // ④⑤ 接收服务端推送
                    System.out.println("收到消息: " + data);
                    return CompletableFuture.completedFuture(null);
                }
                
                @Override
                public CompletionStage<?> onPing(WebSocket webSocket, 
                                                  ByteBuffer message) {
                    // ⑥ 响应心跳
                    webSocket.sendPong(message);
                    return CompletableFuture.completedFuture(null);
                }
            })
            .join();
    }
    
    public void callTool(String toolName, Map<String, Object> args) {
        // ③ 通过 WebSocket 发送请求
        Map<String, Object> message = Map.of(
            "method", "tools/call",
            "params", Map.of("name", toolName, "arguments", args)
        );
        webSocket.sendText(toJson(message), true);
        // ④⑤ 结果通过同一个 WebSocket 连接返回
    }
    
    // 需要手动实现重连逻辑
    private void reconnect() {
        try {
            connect(url);
        } catch (Exception e) {
            // 指数退避重试
            Thread.sleep(Math.min(1000 * Math.pow(2, retryCount), 30000));
            reconnect();
        }
    }
}

// 服务端代码
public class WebSocketServer {
    @OnWebSocketMessage
    public void onMessage(Session session, String message) {
        // ③ 接收客户端请求
        Map<String, Object> request = parseJson(message);
        
        // 执行工具
        executeTool(request, (progress) -> {
            // ④ 推送进度
            session.getRemote().sendString(toJson(Map.of(
                "type", "progress",
                "progress", progress
            )));
        }, (result) -> {
            // ⑤ 推送结果
            session.getRemote().sendString(toJson(Map.of(
                "type", "result",
                "results", result
            )));
        });
    }
    
    // 需要手动实现心跳
    @Scheduled(fixedRate = 30000)
    public void sendPing() {
        sessions.forEach(session -> {
            try {
                session.getRemote().sendPing(ByteBuffer.allocate(0));
            } catch (IOException e) {
                // 连接断开，清理 session
                sessions.remove(session);
            }
        });
    }
}
```

#### 优缺点

```
优点：
✅ 真正的双向通信
✅ 低延迟（无需建连开销）
✅ 单一连接（节省资源）
✅ 适合高频通信
✅ 支持二进制数据

缺点：
❌ 协议复杂（握手、心跳、重连都需要手动实现）
❌ 防火墙可能阻止（协议升级可能被拦截）
❌ 调试困难（需要特殊工具，Chrome DevTools 支持有限）
❌ 需要特殊客户端库（浏览器支持好，但其他语言需要额外库）
❌ 状态管理复杂（需要维护连接状态、会话状态）
❌ 重连逻辑需要手动实现（SSE 是自动的）

适用场景：
✅ 实时性要求极高（如游戏、聊天）
✅ 高频双向通信（每秒多次交互）
✅ 网络环境可控（内网或专用网络）
✅ 有专业运维支持
❌ 不适合 Arthas 这种偶尔调用的场景
```

---

### 5.3 方案 3：纯 HTTP POST（无 SSE，降级方案）

#### 通信流程

```
┌──────────┐                           ┌──────────┐
│  客户端   │                           │  服务端   │
└────┬─────┘                           └────┬─────┘
     │                                      │
     │ ① POST /mcp (发送请求)               │
     │ ────────────────────────────────────►│
     │    {"method": "tools/call"}          │
     │                                      │
     │    ... 等待执行 ...                  │
     │    (可能很长时间)                    │
     │                                      │
     │ ② 200 OK (返回结果)                  │
     │ ◄────────────────────────────────────│
     │    {"results": [...]}                │
     │                                      │
     │ ❌ 无法获取进度通知                   │
     │ ❌ 长时间阻塞                         │
     │                                      │
```

#### 代码示例

```java
// 客户端代码
public class SimpleHttpClient {
    private HttpClient httpClient = HttpClient.newHttpClient();
    
    public Map<String, Object> callTool(String url, 
                                         String toolName, 
                                         Map<String, Object> args) {
        // ① 发送 POST 请求并阻塞等待
        HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create(url))
            .header("Content-Type", "application/json")
            .timeout(Duration.ofMinutes(5))  // 需要设置长超时
            .POST(HttpRequest.BodyPublishers.ofString(toJson(Map.of(
                "method", "tools/call",
                "params", Map.of("name", toolName, "arguments", args)
            ))))
            .build();
        
        try {
            // ② 阻塞等待响应（可能很久）
            HttpResponse<String> response = httpClient.send(
                request, HttpResponse.BodyHandlers.ofString());
            
            // ❌ 期间无法获取进度，用户不知道是否还在执行
            return parseJson(response.body());
            
        } catch (HttpTimeoutException e) {
            // ❌ 超时了，但不知道服务端是否还在执行
            throw new RuntimeException("请求超时，无法确定执行状态");
        }
    }
}

// 服务端代码
public class SimpleHttpServer {
    @PostMapping("/mcp")
    public Map<String, Object> handleRequest(@RequestBody Map<String, Object> request) {
        // ① 接收请求
        String method = (String) request.get("method");
        
        if ("tools/call".equals(method)) {
            // 执行工具（可能很耗时）
            List<Object> results = executeTool(request);
            
            // ❌ 无法推送进度，客户端一直在等待
            // ❌ 如果执行时间过长，可能超时
            
            // ② 返回结果
            return Map.of(
                "results", results,
                "status", "completed"
            );
        }
        
        return Map.of("error", "Unknown method");
    }
}
```

#### 优缺点

```
优点：
✅ 最简单（标准的 HTTP 请求-响应）
✅ 完全无状态
✅ 最易调试（curl 一行搞定）
✅ 无需维护长连接
✅ 适合快速执行的命令

缺点：
❌ 无法推送进度通知（用户体验差）
❌ 长时间阻塞（trace 可能执行几分钟）
❌ 可能超时（HTTP 超时通常 30-60 秒）
❌ 无法取消执行（请求已发出）
❌ 资源浪费（连接一直占用）
❌ 不适合长时间运行的命令

适用场景：
✅ 快速执行的命令（< 5 秒）
✅ 不需要进度反馈
✅ 简单脚本集成
✅ 一次性查询
❌ 不适合 trace、watch 等长时间命令
```

---

### 5.4 三种方案对比总结

| 维度 | SSE + HTTP POST | WebSocket | 纯 HTTP POST |
|------|----------------|-----------|--------------|
| **双向通信** | ✅ 组合实现 | ✅ 原生支持 | ❌ 单向 |
| **进度通知** | ✅ 实时推送 | ✅ 实时推送 | ❌ 无法推送 |
| **连接数** | ⚠️ 2 个 | ✅ 1 个 | ✅ 按需建立 |
| **协议复杂度** | ✅ 简单 | ❌ 复杂 | ✅ 最简单 |
| **防火墙友好** | ✅ 标准 HTTP | ❌ 可能被阻止 | ✅ 标准 HTTP |
| **调试难度** | ✅ 易于调试 | ❌ 需要特殊工具 | ✅ 最易调试 |
| **自动重连** | ✅ SSE 内置 | ❌ 需手动实现 | ✅ 无需重连 |
| **状态管理** | ✅ 简单 | ❌ 复杂 | ✅ 无状态 |
| **延迟** | ⚠️ POST 有开销 | ✅ 低延迟 | ⚠️ 每次建连 |
| **长时间执行** | ✅ 支持 | ✅ 支持 | ❌ 可能超时 |
| **浏览器支持** | ✅ 原生支持 | ✅ 原生支持 | ✅ 原生支持 |
| **客户端库** | ✅ 标准库 | ⚠️ 需额外库 | ✅ 标准库 |
| **适用场景** | 企业内网诊断 | 实时游戏/聊天 | 简单查询 |

#### 为什么 Arthas 选择 SSE + HTTP POST？

1. **命令执行特点**：
   - ✅ 偶尔调用（不是高频通信）
   - ✅ 可能长时间执行（trace 可能几分钟）
   - ✅ 需要进度反馈（用户体验）
   - ❌ 不需要毫秒级延迟

2. **部署环境考虑**：
   - ✅ 企业内网环境（防火墙限制）
   - ✅ 多种客户端（CLI、IDE、Web）
   - ✅ 易于调试（开发者工具）
   - ✅ 渐进增强（可降级为纯 POST）

3. **实现复杂度**：
   - ✅ SSE + POST 实现简单
   - ✅ 无需管理连接状态
   - ✅ 无需实现心跳和重连
   - ✅ 易于测试和维护

**结论**：SSE + HTTP POST 是 Arthas 场景下的最佳选择！

---

## 6. 实际应用场景

### 6.1 Server 模式：适合直连场景

```
场景：开发者本地调试

┌──────────────┐         ┌──────────────┐
│  IDE/CLI     │         │  Arthas      │
│  (客户端)    │◄───────►│  (服务端)    │
└──────────────┘         └──────────────┘
    localhost:8080

通信方式：
1. 客户端建立 SSE 连接（接收响应）
2. 客户端发送 POST 请求（调用工具）
3. 服务端通过 SSE 推送进度和结果

优势：
✅ 直接连接，低延迟
✅ 可以实时看到进度
✅ 易于调试
```

### 6.2 Client 模式：适合内网穿透场景

```
场景：生产环境远程诊断

┌──────────────┐         ┌──────────────┐         ┌──────────────┐
│  管控平台    │         │  防火墙      │         │  Arthas      │
│  (公网)      │◄───────►│              │◄───────►│  (内网)      │
└──────────────┘         └──────────────┘         └──────────────┘
                              只允许出站连接

通信方式：
1. Arthas 主动连接管控平台（建立 SSE 连接）
2. 管控平台通过 SSE 推送工具调用请求
3. Arthas 通过 POST 发送执行结果

优势：
✅ 无需开放入站端口
✅ 穿透防火墙
✅ 安全可控
```

---

## 7. 常见误解澄清

### 误解 1：SSE 可以双向通信

❌ **错误**：SSE 是双向的，可以发送和接收数据

✅ **正确**：SSE 是单向的，只能服务端推送给客户端

```javascript
// EventSource API 只有接收方法，没有发送方法
const es = new EventSource('/mcp');
es.onmessage = (e) => console.log(e.data);  // ✅ 可以接收
es.send({...});  // ❌ 不存在这个方法
```

### 误解 2：可以"都用 SSE"

❌ **错误**：建立两个 SSE 连接实现双向通信

✅ **正确**：SSE 只能服务端发起推送，客户端无法建立"反向 SSE"

```
❌ 错误方案：
客户端 → 服务端：建立 SSE 连接 A
服务端 → 客户端：建立 SSE 连接 B

问题：
1. 服务端无法主动发起 GET 请求建立 SSE
2. 即使建立了两个连接，也只是两个单向通道
3. 这本质上就是 WebSocket，为什么不直接用？
```

### 误解 3：Server 模式的流式响应是实时推送每条结果

❌ **错误**：trace 命令的每条结果都实时推送

✅ **正确**：推送的是进度通知，最终结果仍然是一次性返回

```
实际流程：
SSE Event 1: {"progress": 1/10}   ← 进度通知
SSE Event 2: {"progress": 5/10}   ← 进度通知
SSE Event 3: {"progress": 10/10}  ← 进度通知
SSE Event 4: {"results": [...]}   ← 一次性返回所有 10 条结果
```

---

## 8. 总结

### 8.1 核心结论

**为什么不能"都用 SSE"？**

1. **技术限制**：SSE 是单向协议，客户端无法通过 SSE 发送数据
2. **协议设计**：HTTP 的请求-响应模型需要 POST 来发送请求
3. **实用性**：SSE + POST 比 WebSocket 更简单、更易调试、更防火墙友好

### 8.2 设计原则

```
SSE 的作用：
✅ 服务端主动推送（进度通知、异步响应）
✅ 自动重连
✅ 长连接

HTTP POST 的作用：
✅ 客户端发送请求
✅ 客户端发送响应
✅ 无状态、易于实现

两者配合使用，实现双向通信：
- Server 模式：客户端 POST 请求 → 服务端 SSE 响应
- Client 模式：服务端 SSE 请求 → 客户端 POST 响应
```

### 8.3 最佳实践

1. **需要进度反馈**：使用 SSE + HTTP POST
2. **简单集成**：只用 HTTP POST（轮询模式）
3. **实时性要求极高**：考虑 WebSocket
4. **企业内网环境**：优先选择 SSE + HTTP POST

### 8.4 技术选型建议

| 场景 | 推荐方案 | 原因 |
|------|---------|------|
| 企业内网诊断 | SSE + POST | 防火墙友好 |
| 本地开发调试 | SSE + POST | 易于调试 |
| 云原生环境 | SSE + POST | 标准 HTTP |
| 实时游戏/聊天 | WebSocket | 低延迟 |
| 简单脚本集成 | 纯 POST | 最简单 |

---

## 9. 参考资料

- [MDN: Server-Sent Events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)
- [MDN: WebSocket](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket)
- [MCP Protocol Specification](https://spec.modelcontextprotocol.io/)
- [Arthas MCP Tools Architecture](./ARTHAS_MCP_TOOLS_ARCHITECTURE.md)

---

**文档版本**：1.0  
**最后更新**：2026-01-22  
**作者**：Arthas Team
