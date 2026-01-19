# Arthas MCP Client 架构设计与源码分析

> **文档版本**: 1.0  
> **创建时间**: 2026-01-19  
> **作者**: Arthas Team

---

## 📋 目录

1. [概述](#1-概述)
2. [整体架构](#2-整体架构)
3. [核心组件详解](#3-核心组件详解)
4. [连接流程](#4-连接流程)
5. [通信协议](#5-通信协议)
6. [心跳与重连机制](#6-心跳与重连机制)
7. [工具调用流程](#7-工具调用流程)
8. [状态管理](#8-状态管理)
9. [配置系统](#9-配置系统)
10. [关键技术点](#10-关键技术点)

---

## 1. 概述

### 1.1 什么是 MCP Client？

Arthas MCP Client 是一个**反向连接客户端**，用于将本地 Arthas 实例主动连接到远程管控平台或智能体服务，提供 MCP（Model Context Protocol）工具服务。

### 1.2 设计目标

- ✅ **主动连接**: 客户端主动连接到公网服务，无需暴露本地端口
- ✅ **双向通信**: 基于 HTTP/SSE 实现双向消息传递
- ✅ **高可用性**: 支持自动重连、心跳检测、连接保活
- ✅ **工具复用**: 复用现有的 Arthas 工具体系（jad、thread、dashboard 等）
- ✅ **易于配置**: 支持环境变量配置，无需修改代码

### 1.3 应用场景

```
┌─────────────────┐         Internet         ┌──────────────────┐
│  Arthas Agent   │ ──────────────────────> │  管控平台/智能体  │
│  (MCP Client)   │ <────────────────────── │  (MCP Server)    │
└─────────────────┘                          └──────────────────┘
   本地环境                                      公网服务
   - 无需公网 IP                                - 接收工具调用请求
   - 主动连接                                   - 返回执行结果
   - 提供诊断工具                               - 管理多个客户端
```

---

## 2. 整体架构

### 2.1 架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                      ArthasMcpClient                            │
│                    (客户端主控制器)                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │ 状态管理      │  │ 生命周期管理  │  │ 组件协调      │         │
│  │ State Machine│  │ start/stop   │  │ Orchestration│         │
│  └──────────────┘  └──────────────┘  └──────────────┘         │
└─────────────────────────────────────────────────────────────────┘
           │                    │                    │
           ▼                    ▼                    ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│  McpHttpClient   │  │ McpClientProtocol│  │ HeartbeatManager │
│  (HTTP/SSE通信)  │  │ Handler          │  │ (心跳管理)        │
│                  │  │ (协议处理)        │  │                  │
│ • SSE 长连接     │  │ • initialize     │  │ • 定时 ping      │
│ • POST 请求      │  │ • tools/list     │  │ • 超时检测        │
│ • 响应处理       │  │ • tools/call     │  │ • 触发重连        │
└──────────────────┘  └──────────────────┘  └──────────────────┘
           │                    │                    │
           └────────────────────┴────────────────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │ ReconnectStrategy│
                    │ (重连策略)        │
                    │ • 指数退避        │
                    │ • 延迟计算        │
                    └──────────────────┘
```

### 2.2 核心类关系

```java
ArthasMcpClient (主控制器)
    ├── McpClientConfig (配置)
    ├── McpHttpClient (HTTP/SSE 通信层)
    │   └── Netty (Bootstrap, EventLoopGroup, Channel)
    ├── McpClientProtocolHandler (协议处理层)
    │   └── ToolCallback[] (工具回调)
    ├── HeartbeatManager (心跳管理)
    │   └── ScheduledExecutorService (定时任务)
    └── ReconnectStrategy (重连策略)
```

---

## 3. 核心组件详解

### 3.1 ArthasMcpClient - 主控制器

**职责**: 客户端的总指挥，负责组件初始化、生命周期管理、状态协调。

#### 3.1.1 状态机

```java
public enum State {
    DISCONNECTED,    // 未连接
    CONNECTING,      // 连接中
    CONNECTED,       // 已连接
    RECONNECTING,    // 重连中
    STOPPED          // 已停止
}
```

**状态转换图**:

```
DISCONNECTED ──start()──> CONNECTING ──success──> CONNECTED
                              │                       │
                              │                       │
                          fail│                  lost │
                              │                       │
                              ▼                       ▼
                         RECONNECTING ──success──> CONNECTED
                              │
                              │ stop()
                              ▼
                           STOPPED
```

#### 3.1.2 启动流程

```java
public CompletableFuture<Void> start() {
    // 1. 状态检查：DISCONNECTED -> CONNECTING
    if (!state.compareAndSet(State.DISCONNECTED, State.CONNECTING)) {
        return failedFuture(new IllegalStateException("Cannot start"));
    }

    // 2. 初始化调度器（用于心跳和重连）
    scheduler = Executors.newScheduledThreadPool(2);

    // 3. 初始化协议处理器
    protocolHandler = new McpClientProtocolHandler(config, objectMapper, commandExecutor);
    
    // 4. 注册工具回调
    if (toolCallbackProvider != null) {
        ToolCallback[] callbacks = toolCallbackProvider.getToolCallbacks();
        protocolHandler.registerToolCallbacks(callbacks);
    }

    // 5. 初始化 HTTP 客户端
    httpClient = new McpHttpClient(config, objectMapper);
    protocolHandler.setHttpClient(httpClient);
    httpClient.setMessageHandler(protocolHandler::handleMessage);
    httpClient.setConnectionLostHandler(this::onConnectionLost);

    // 6. 执行连接流程
    return httpClient.init()
            .thenCompose(v -> connect())
            .thenRun(() -> {
                state.set(State.CONNECTED);
                logger.info("Client started successfully");
            });
}
```

#### 3.1.3 连接流程

```java
private CompletableFuture<Void> connect() {
    return httpClient.connectSse()                    // 1. 建立 SSE 长连接
            .thenCompose(v -> protocolHandler.sendInitialize())  // 2. 发送 initialize
            .thenCompose(result -> protocolHandler.sendInitialized()) // 3. 发送 initialized
            .thenRun(this::startHeartbeat)            // 4. 启动心跳
            .thenRun(() -> reconnectStrategy.reset()); // 5. 重置重连计数
}
```

---

### 3.2 McpHttpClient - HTTP/SSE 通信层

**职责**: 基于 Netty 实现 HTTP 和 SSE 通信，处理底层网络 I/O。

#### 3.2.1 双通道设计

```
┌─────────────────────────────────────────────────────────────┐
│                      McpHttpClient                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────┐      ┌─────────────────────┐     │
│  │   SSE Channel       │      │   POST Channels     │     │
│  │   (长连接)           │      │   (短连接)           │     │
│  │                     │      │                     │     │
│  │ • 接收服务端请求     │      │ • 发送客户端请求     │     │
│  │ • tools/call        │      │ • initialize        │     │
│  │ • tools/list        │      │ • ping              │     │
│  │ • 保持连接           │      │ • 响应服务端请求     │     │
│  │                     │      │                     │     │
│  └─────────────────────┘      └─────────────────────┘     │
│           ▲                            │                   │
│           │                            │                   │
│           │ SSE Events                 │ HTTP POST         │
│           │                            ▼                   │
└───────────┼────────────────────────────┼───────────────────┘
            │                            │
            │                            │
    ┌───────┴────────┐          ┌───────┴────────┐
    │  SseHandler    │          │ HttpResponse   │
    │                │          │ Handler        │
    │ • 解析 SSE     │          │ • 处理响应     │
    │ • 分发消息     │          │ • 完成 Future  │
    └────────────────┘          └────────────────┘
```

#### 3.2.2 SSE 连接建立

```java
public CompletableFuture<Void> connectSse() {
    Bootstrap bootstrap = new Bootstrap();
    bootstrap.group(eventLoopGroup)
            .channel(NioSocketChannel.class)
            .handler(new ChannelInitializer<SocketChannel>() {
                @Override
                protected void initChannel(SocketChannel ch) {
                    ChannelPipeline pipeline = ch.pipeline();
                    
                    // 1. SSL 支持（如果是 https）
                    if (ssl && sslContext != null) {
                        pipeline.addLast("ssl", sslContext.newHandler(...));
                    }
                    
                    // 2. HTTP 编解码器
                    pipeline.addLast("http-codec", new HttpClientCodec());
                    
                    // 3. 空闲检测（2.5 倍心跳间隔）
                    long sseReadTimeout = (long) (config.getHeartbeat().getInterval() * 2.5 / 1000);
                    pipeline.addLast("idle-state", new IdleStateHandler(sseReadTimeout, 0, 0));
                    
                    // 4. SSE 事件处理器
                    pipeline.addLast("sse-handler", new SseHandler(future));
                }
            });

    // 连接服务器
    ChannelFuture connectFuture = bootstrap.connect(host, port);
    connectFuture.addListener(f -> {
        if (f.isSuccess()) {
            sseChannel = f.channel();
            sendSseRequest(sseChannel);  // 发送 GET 请求
        }
    });
}
```

#### 3.2.3 SSE 事件解析

```java
private class SseHandler extends SimpleChannelInboundHandler<HttpObject> {
    private StringBuilder sseBuffer = new StringBuilder();

    @Override
    protected void channelRead0(ChannelHandlerContext ctx, HttpObject msg) {
        if (msg instanceof HttpResponse) {
            HttpResponse response = (HttpResponse) msg;
            
            // 检查响应状态
            if (response.status().code() != 200) {
                connectFuture.completeExceptionally(...);
                return;
            }
            
            // 获取 session ID
            String newSessionId = response.headers().get("Mcp-Session-Id");
            if (newSessionId != null) {
                sessionId = newSessionId;
            }
            
            sseConnected = true;
            connectFuture.complete(null);
        }
        
        if (msg instanceof HttpContent) {
            HttpContent content = (HttpContent) msg;
            String chunk = content.content().toString(CharsetUtil.UTF_8);
            sseBuffer.append(chunk);
            
            // 处理完整的 SSE 事件（以 \n\n 分隔）
            processSseEvents();
        }
    }

    private void processSseEvents() {
        String buffer = sseBuffer.toString();
        int lastEventEnd = 0;
        
        // 查找完整事件
        int eventEnd;
        while ((eventEnd = buffer.indexOf("\n\n", lastEventEnd)) != -1) {
            String event = buffer.substring(lastEventEnd, eventEnd);
            lastEventEnd = eventEnd + 2;
            
            processEvent(event);  // 解析单个事件
        }
        
        // 保留未处理的部分
        if (lastEventEnd > 0) {
            sseBuffer = new StringBuilder(buffer.substring(lastEventEnd));
        }
    }

    private void processEvent(String event) throws Exception {
        // 解析 SSE 格式
        // event: message
        // data: {"jsonrpc":"2.0","method":"tools/call",...}
        
        String eventType = "message";
        StringBuilder dataBuilder = new StringBuilder();
        
        BufferedReader reader = new BufferedReader(new StringReader(event));
        String line;
        while ((line = reader.readLine()) != null) {
            if (line.startsWith("event:")) {
                eventType = line.substring(6).trim();
            } else if (line.startsWith("data:")) {
                dataBuilder.append(line.substring(5).trim());
            }
        }
        
        String data = dataBuilder.toString();
        if ("message".equals(eventType) && messageHandler != null) {
            McpSchema.JSONRPCMessage message = 
                McpSchema.deserializeJsonRpcMessage(objectMapper, data);
            
            // 交给协议处理器
            messageHandler.accept(message);
        }
    }
}
```

#### 3.2.4 POST 请求发送

```java
public CompletableFuture<McpSchema.JSONRPCResponse> sendRequest(McpSchema.JSONRPCRequest request) {
    CompletableFuture<McpSchema.JSONRPCResponse> future = new CompletableFuture<>();
    
    // 1. 存储 pending 请求
    pendingRequests.put(request.getId(), future);
    
    // 2. 设置超时
    scheduler.schedule(() -> {
        CompletableFuture<McpSchema.JSONRPCResponse> pending = pendingRequests.remove(request.getId());
        if (pending != null && !pending.isDone()) {
            pending.completeExceptionally(new RuntimeException("Request timeout"));
        }
    }, config.getRequestTimeout(), TimeUnit.MILLISECONDS);
    
    // 3. 发送 HTTP POST
    sendHttpPost(request, future);
    
    return future;
}

private void sendHttpPost(Object message, CompletableFuture<T> future) {
    // 创建新的短连接
    Bootstrap bootstrap = new Bootstrap();
    bootstrap.group(eventLoopGroup)
            .channel(NioSocketChannel.class)
            .handler(new ChannelInitializer<SocketChannel>() {
                @Override
                protected void initChannel(SocketChannel ch) {
                    ChannelPipeline pipeline = ch.pipeline();
                    
                    if (ssl && sslContext != null) {
                        pipeline.addLast("ssl", sslContext.newHandler(...));
                    }
                    
                    pipeline.addLast("http-codec", new HttpClientCodec());
                    pipeline.addLast("http-aggregator", new HttpObjectAggregator(65536));
                    pipeline.addLast("response-handler", new HttpResponseHandler<>(message, future));
                }
            });

    // 连接并发送
    bootstrap.connect(host, port).addListener(f -> {
        if (f.isSuccess()) {
            sendPostRequest(f.channel(), message);
        }
    });
}
```

---

### 3.3 McpClientProtocolHandler - 协议处理层

**职责**: 处理 MCP 协议消息，包括请求分发、工具调用、响应构造。

#### 3.3.1 消息处理流程

```java
public void handleMessage(McpSchema.JSONRPCMessage message) {
    if (message instanceof McpSchema.JSONRPCRequest) {
        handleRequest((McpSchema.JSONRPCRequest) message);
    } else if (message instanceof McpSchema.JSONRPCNotification) {
        handleNotification((McpSchema.JSONRPCNotification) message);
    }
}

private void handleRequest(McpSchema.JSONRPCRequest request) {
    String method = request.getMethod();
    Object id = request.getId();
    
    CompletableFuture<Object> resultFuture;
    
    switch (method) {
        case "tools/list":
            resultFuture = handleToolsList(request);
            break;
        case "tools/call":
            resultFuture = handleToolsCall(request);
            break;
        case "ping":
            resultFuture = CompletableFuture.completedFuture(Collections.emptyMap());
            break;
        default:
            sendErrorResponse(id, ErrorCodes.METHOD_NOT_FOUND, "Method not supported", null);
            return;
    }
    
    resultFuture
            .thenAccept(result -> sendSuccessResponse(id, result))
            .exceptionally(ex -> {
                sendErrorResponse(id, ErrorCodes.INTERNAL_ERROR, ex.getMessage(), null);
                return null;
            });
}
```

#### 3.3.2 工具调用处理

```java
private CompletableFuture<Object> handleToolsCall(McpSchema.JSONRPCRequest request) {
    return CompletableFuture.supplyAsync(() -> {
        // 1. 解析请求参数
        McpSchema.CallToolRequest callRequest = objectMapper.convertValue(
                request.getParams(), McpSchema.CallToolRequest.class);
        
        String toolName = callRequest.getName();
        Map<String, Object> arguments = callRequest.getArguments();
        
        // 2. 查找工具回调
        ToolCallback callback = toolCallbacks.get(toolName);
        if (callback == null) {
            throw new IllegalArgumentException("Tool not found: " + toolName);
        }
        
        // 3. 构建工具输入
        String toolInput = objectMapper.writeValueAsString(arguments);
        
        // 4. 构建工具上下文（关键！）
        Map<String, Object> contextMap = new HashMap<>();
        contextMap.put("_mcp_client_mode", "reverse");
        contextMap.put("_mcp_client_name", config.getClientName());
        
        // 添加 ArthasCommandContext（执行 Arthas 命令的关键）
        if (commandExecutor != null) {
            ArthasCommandContext commandContext = new ArthasCommandContext(commandExecutor);
            contextMap.put(TOOL_CONTEXT_COMMAND_CONTEXT_KEY, commandContext);
        }
        
        ToolContext toolContext = new ToolContext(contextMap);
        
        // 5. 执行工具
        String result = callback.call(toolInput, toolContext);
        
        // 6. 构建响应
        return McpSchema.CallToolResult.builder()
                .addTextContent(result != null ? result : "")
                .isError(false)
                .build();
    });
}
```

#### 3.3.3 Initialize 握手

```java
public CompletableFuture<McpSchema.InitializeResult> sendInitialize() {
    // 1. 构建客户端能力
    McpSchema.ClientCapabilities capabilities = McpSchema.ClientCapabilities.builder().build();
    
    // 2. 构建客户端信息
    McpSchema.Implementation clientInfo = new McpSchema.Implementation(
            config.getClientName(),
            config.getClientVersion()
    );
    
    // 3. 构建请求
    McpSchema.InitializeRequest params = new McpSchema.InitializeRequest(
            ProtocolVersions.MCP_2025_06_18,
            capabilities,
            clientInfo
    );
    
    McpSchema.JSONRPCRequest request = new McpSchema.JSONRPCRequest(
            "2.0",
            "initialize",
            httpClient.nextRequestId(),
            params
    );
    
    // 4. 发送并处理响应
    return httpClient.sendRequest(request)
            .thenApply(response -> {
                McpSchema.InitializeResult result = objectMapper.convertValue(
                        response.getResult(), McpSchema.InitializeResult.class);
                
                // 保存服务端信息
                serverInfo = result.getServerInfo();
                serverCapabilities = result.getCapabilities();
                negotiatedProtocolVersion = result.getProtocolVersion();
                
                return result;
            });
}
```

---

### 3.4 HeartbeatManager - 心跳管理

**职责**: 定期发送心跳，检测连接状态，触发超时处理。

#### 3.4.1 心跳机制

```java
public void start(Runnable pingAction, Runnable timeoutAction) {
    lastPongTime.set(System.currentTimeMillis());
    
    heartbeatTask = scheduler.scheduleAtFixedRate(() -> {
        // 1. 检查是否超时
        long now = System.currentTimeMillis();
        long elapsed = now - lastPongTime.get();
        
        if (elapsed > interval + timeout) {
            logger.warn("Heartbeat timeout: {}ms since last pong", elapsed);
            if (timeoutAction != null) {
                timeoutAction.run();  // 触发重连
            }
            return;
        }
        
        // 2. 发送心跳
        if (pingAction != null) {
            pingAction.run();
        }
        
    }, interval, interval, TimeUnit.MILLISECONDS);
}

public void onPong() {
    lastPongTime.set(System.currentTimeMillis());
}
```

#### 3.4.2 超时计算

```
时间轴：
├─────────┼─────────┼─────────┼─────────┼─────────┼─────────>
0        30s       60s       90s      120s      150s
│         │         │         │         │         │
ping1    ping2    ping3    ping4    ping5    timeout!
         │                   │
         pong1               pong2 (未收到)
         
超时判断：
elapsed = now - lastPongTime
if (elapsed > interval + timeout) {
    // 超时！触发重连
}

示例：
interval = 30s
timeout = 10s
lastPongTime = 60s
now = 120s
elapsed = 60s > 40s  => 超时！
```

---

### 3.5 ReconnectStrategy - 重连策略

**职责**: 实现指数退避算法，计算重连延迟。

#### 3.5.1 指数退避算法

```java
public synchronized long getNextDelay() {
    attemptCount.incrementAndGet();
    long delay = currentDelay;
    
    // 计算下次延迟：delay = min(currentDelay * multiplier, maxDelay)
    currentDelay = Math.min((long) (currentDelay * multiplier), maxDelay);
    
    return delay;
}
```

#### 3.5.2 延迟计算示例

```
配置：
initialDelay = 5000ms (5s)
maxDelay = 300000ms (5min)
multiplier = 2.0

重连序列：
Attempt 1: 5s
Attempt 2: 10s   (5s * 2)
Attempt 3: 20s   (10s * 2)
Attempt 4: 40s   (20s * 2)
Attempt 5: 80s   (40s * 2)
Attempt 6: 160s  (80s * 2)
Attempt 7: 300s  (160s * 2 = 320s, 但 max = 300s)
Attempt 8: 300s  (已达上限)
...
```

---

## 4. 连接流程

### 4.1 完整连接时序图

```
Client                HttpClient           ProtocolHandler         Server
  │                       │                       │                   │
  │──start()──>           │                       │                   │
  │                       │                       │                   │
  │           ┌───init()──┤                       │                   │
  │           │           │                       │                   │
  │           └──────────>│                       │                   │
  │                       │                       │                   │
  │       ┌──connectSse()─┤                       │                   │
  │       │               │                       │                   │
  │       │               │────GET /mcp?sessionId=xxx──────────────>  │
  │       │               │                       │                   │
  │       │               │<───200 OK (SSE)───────────────────────────│
  │       │               │    Mcp-Session-Id: xxx                    │
  │       │               │                       │                   │
  │       └──────────────>│                       │                   │
  │                       │                       │                   │
  │           ┌───────────┼──sendInitialize()────>│                   │
  │           │           │                       │                   │
  │           │           │                       │──POST /mcp───────>│
  │           │           │                       │  {"method":"initialize"}
  │           │           │                       │                   │
  │           │           │                       │<──200 OK──────────│
  │           │           │                       │  {"result":{...}} │
  │           │           │                       │                   │
  │           └───────────┼<──────────────────────┤                   │
  │                       │                       │                   │
  │               ┌───────┼──sendInitialized()───>│                   │
  │               │       │                       │                   │
  │               │       │                       │──POST /mcp───────>│
  │               │       │                       │  {"method":"notifications/initialized"}
  │               │       │                       │                   │
  │               │       │                       │<──202 Accepted────│
  │               │       │                       │                   │
  │               └───────┼<──────────────────────┤                   │
  │                       │                       │                   │
  │<──connected───────────┤                       │                   │
  │                       │                       │                   │
  │──startHeartbeat()────>│                       │                   │
  │                       │                       │                   │
  │                       │  (每 30s 发送 ping)    │                   │
  │                       │                       │──POST /mcp───────>│
  │                       │                       │  {"method":"ping"}│
  │                       │                       │<──200 OK──────────│
  │                       │                       │                   │
```

### 4.2 Session ID 管理

```java
// Session ID 的作用：
// 1. 服务端用于识别客户端会话
// 2. 客户端在所有请求中携带 session ID
// 3. 重连时复用 session ID，保持会话连续性

// 获取 Session ID 的时机：
// 1. SSE 连接建立时，从响应头获取
String sessionId = response.headers().get("Mcp-Session-Id");

// 2. 后续所有请求都携带 session ID
// - URL 参数: /mcp?sessionId=xxx
// - HTTP 头: Mcp-Session-Id: xxx
```

---

## 5. 通信协议

### 5.1 JSON-RPC 2.0 格式

#### 5.1.1 请求格式

```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "id": 1,
  "params": {
    "name": "jad",
    "arguments": {
      "classPattern": "com.example.MyClass"
    }
  }
}
```

#### 5.1.2 响应格式

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "/* decompiled source code */"
      }
    ],
    "isError": false
  }
}
```

#### 5.1.3 通知格式

```json
{
  "jsonrpc": "2.0",
  "method": "notifications/initialized"
}
```

### 5.2 MCP 协议方法

#### 5.2.1 客户端发送的方法

| 方法 | 类型 | 说明 |
|------|------|------|
| `initialize` | Request | 连接建立后的握手请求 |
| `notifications/initialized` | Notification | 握手成功后的通知 |
| `ping` | Request | 心跳请求 |

#### 5.2.2 服务端发送的方法

| 方法 | 类型 | 说明 |
|------|------|------|
| `tools/list` | Request | 查询可用工具列表 |
| `tools/call` | Request | 调用指定工具 |
| `ping` | Request | 心跳请求（双向） |

### 5.3 SSE 事件格式

```
event: message
data: {"jsonrpc":"2.0","method":"tools/call","id":1,"params":{...}}

event: message
data: {"jsonrpc":"2.0","method":"ping","id":2}

```

---

## 6. 心跳与重连机制

### 6.1 心跳流程

```
┌─────────────────────────────────────────────────────────────┐
│                     Heartbeat Cycle                         │
└─────────────────────────────────────────────────────────────┘

Time: 0s ────────> 30s ────────> 60s ────────> 90s ────────>
      │             │             │             │
      │             │             │             │
      ▼             ▼             ▼             ▼
   [Start]       [Ping]        [Ping]        [Ping]
      │             │             │             │
      │             │             │             │
      ▼             ▼             ▼             ▼
  lastPongTime  onPong()      onPong()      timeout!
   = now()      lastPongTime  lastPongTime  (no pong)
                 = now()       = now()
                 
检查逻辑（每次 ping 前）：
if (now - lastPongTime > interval + timeout) {
    // 超时！触发重连
    timeoutAction.run();
}
```

### 6.2 重连流程

```java
private void onConnectionLost() {
    // 1. 状态检查
    if (state.get() == State.STOPPED || state.get() == State.RECONNECTING) {
        return;
    }
    
    // 2. 切换到重连状态
    if (state.compareAndSet(State.CONNECTED, State.RECONNECTING)) {
        scheduleReconnect();
    }
}

private void scheduleReconnect() {
    // 1. 计算延迟
    long delay = reconnectStrategy.getNextDelay();
    int attempt = reconnectStrategy.getAttemptCount();
    
    // 2. 调度重连任务
    scheduler.schedule(() -> {
        // 3. 停止心跳
        if (heartbeatManager != null) {
            heartbeatManager.stop();
        }
        
        // 4. 重置协议状态
        protocolHandler.reset();
        
        // 5. 重新连接
        connect()
                .thenRun(() -> {
                    state.set(State.CONNECTED);
                    logger.info("Reconnected successfully");
                })
                .exceptionally(ex -> {
                    // 继续重连
                    if (state.get() == State.RECONNECTING) {
                        scheduleReconnect();
                    }
                    return null;
                });
    }, delay, TimeUnit.MILLISECONDS);
}
```

### 6.3 连接保活策略

```
多层保活机制：

1. TCP Keep-Alive
   └─> ChannelOption.SO_KEEPALIVE = true

2. HTTP SSE 长连接
   └─> Connection: keep-alive
   └─> 服务端定期发送注释或空事件

3. 应用层心跳
   └─> 每 30s 发送 ping
   └─> 超时 10s 未收到 pong 则重连

4. 空闲检测
   └─> IdleStateHandler(2.5 * heartbeatInterval)
   └─> 超时关闭连接，触发重连
```

---

## 7. 工具调用流程

### 7.1 完整调用链

```
Server                Client              ProtocolHandler      ToolCallback        Arthas
  │                     │                       │                   │                 │
  │──tools/call────────>│                       │                   │                 │
  │  (via SSE)          │                       │                   │                 │
  │                     │                       │                   │                 │
  │                     │──handleMessage()─────>│                   │                 │
  │                     │                       │                   │                 │
  │                     │                       │──handleToolsCall()│                 │
  │                     │                       │                   │                 │
  │                     │                       │──lookup tool─────>│                 │
  │                     │                       │                   │                 │
  │                     │                       │──build context───>│                 │
  │                     │                       │  (with CommandContext)              │
  │                     │                       │                   │                 │
  │                     │                       │──call()──────────>│                 │
  │                     │                       │                   │                 │
  │                     │                       │                   │──execute()─────>│
  │                     │                       │                   │  (jad, thread, etc.)
  │                     │                       │                   │                 │
  │                     │                       │                   │<──result────────│
  │                     │                       │                   │                 │
  │                     │                       │<──result──────────│                 │
  │                     │                       │                   │                 │
  │                     │<──CallToolResult──────│                   │                 │
  │                     │                       │                   │                 │
  │<──response──────────│                       │                   │                 │
  │  (via HTTP POST)    │                       │                   │                 │
```

### 7.2 ToolContext 构造

```java
// ToolContext 是工具执行的上下文，包含执行所需的所有信息

Map<String, Object> contextMap = new HashMap<>();

// 1. 标记客户端模式
contextMap.put("_mcp_client_mode", "reverse");
contextMap.put("_mcp_client_name", config.getClientName());

// 2. 添加 ArthasCommandContext（关键！）
// 这是执行 Arthas 命令的核心组件
if (commandExecutor != null) {
    ArthasCommandContext commandContext = new ArthasCommandContext(commandExecutor);
    contextMap.put(TOOL_CONTEXT_COMMAND_CONTEXT_KEY, commandContext);
}

// 3. 添加元数据（如果有）
if (callRequest.meta() != null) {
    contextMap.putAll(callRequest.meta());
}

ToolContext toolContext = new ToolContext(contextMap);
```

### 7.3 工具执行示例

```java
// 以 jad 工具为例

// 1. 服务端发送请求
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "id": 1,
  "params": {
    "name": "jad",
    "arguments": {
      "classPattern": "com.example.MyClass",
      "sourceOnly": true
    }
  }
}

// 2. 客户端处理
ToolCallback jadCallback = toolCallbacks.get("jad");
String toolInput = "{\"classPattern\":\"com.example.MyClass\",\"sourceOnly\":true}";
ToolContext toolContext = new ToolContext(contextMap);

// 3. 执行工具
String result = jadCallback.call(toolInput, toolContext);
// result = "/* decompiled source code */\npackage com.example;\n..."

// 4. 构造响应
CallToolResult callResult = CallToolResult.builder()
        .addTextContent(result)
        .isError(false)
        .build();

// 5. 发送响应
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "/* decompiled source code */\npackage com.example;\n..."
      }
    ],
    "isError": false
  }
}
```

---

## 8. 状态管理

### 8.1 状态转换详解

```java
// 状态转换必须是原子的，使用 AtomicReference + CAS

private final AtomicReference<State> state = new AtomicReference<>(State.DISCONNECTED);

// 启动时
if (!state.compareAndSet(State.DISCONNECTED, State.CONNECTING)) {
    // 不允许从其他状态启动
    throw new IllegalStateException("Cannot start in state: " + state.get());
}

// 连接成功
state.set(State.CONNECTED);

// 连接丢失
if (state.compareAndSet(State.CONNECTED, State.RECONNECTING)) {
    scheduleReconnect();
}

// 停止
state.set(State.STOPPED);
```

### 8.2 状态查询

```java
public boolean isConnected() {
    return state.get() == State.CONNECTED && 
            httpClient != null && httpClient.isSseConnected();
}

// 注意：需要同时检查两个条件
// 1. 状态为 CONNECTED
// 2. SSE 连接实际存活
```

---

## 9. 配置系统

### 9.1 环境变量配置

```bash
# 服务端地址（必需）
export ARTHAS_MCP_CLIENT_SERVER_URL=http://localhost:8080/mcp

# 认证 Token（可选）
export ARTHAS_MCP_CLIENT_AUTH_TOKEN=your-token

# 重连配置
export ARTHAS_MCP_CLIENT_RECONNECT_ENABLED=true
export ARTHAS_MCP_CLIENT_RECONNECT_INITIAL_DELAY=5000
export ARTHAS_MCP_CLIENT_RECONNECT_MAX_DELAY=300000
export ARTHAS_MCP_CLIENT_RECONNECT_MULTIPLIER=2.0

# 心跳配置
export ARTHAS_MCP_CLIENT_HEARTBEAT_ENABLED=true
export ARTHAS_MCP_CLIENT_HEARTBEAT_INTERVAL=30000
export ARTHAS_MCP_CLIENT_HEARTBEAT_TIMEOUT=10000

# 超时配置
export ARTHAS_MCP_CLIENT_CONNECT_TIMEOUT=10000
export ARTHAS_MCP_CLIENT_REQUEST_TIMEOUT=30000

# 客户端信息
export ARTHAS_MCP_CLIENT_CLIENT_NAME=arthas-mcp-client
export ARTHAS_MCP_CLIENT_CLIENT_VERSION=4.1.5
```

### 9.2 Builder 模式配置

```java
ArthasMcpClient client = ArthasMcpClient.create("http://localhost:8080/mcp")
        .authToken("your-token")
        .reconnectEnabled(true)
        .reconnectInitialDelay(5000)
        .reconnectMaxDelay(300000)
        .reconnectMultiplier(2.0)
        .heartbeatEnabled(true)
        .heartbeatInterval(30000)
        .heartbeatTimeout(10000)
        .connectTimeout(10000)
        .requestTimeout(30000)
        .clientName("my-arthas-client")
        .clientVersion("1.0.0")
        .toolCallbackProvider(toolProvider)
        .commandExecutor(executor)
        .build();
```

---

## 10. 关键技术点

### 10.1 Netty 复用

```java
// 复用 Arthas 现有的 Netty 组件

// 1. EventLoopGroup
eventLoopGroup = new NioEventLoopGroup(2);

// 2. Bootstrap
Bootstrap bootstrap = new Bootstrap();
bootstrap.group(eventLoopGroup)
        .channel(NioSocketChannel.class)
        .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, timeout)
        .option(ChannelOption.SO_KEEPALIVE, true);

// 3. ChannelPipeline
pipeline.addLast("http-codec", new HttpClientCodec());
pipeline.addLast("http-aggregator", new HttpObjectAggregator(65536));
pipeline.addLast("ssl", sslContext.newHandler(...));
pipeline.addLast("idle-state", new IdleStateHandler(...));
```

### 10.2 CompletableFuture 异步编程

```java
// 链式异步调用

httpClient.init()
        .thenCompose(v -> httpClient.connectSse())
        .thenCompose(v -> protocolHandler.sendInitialize())
        .thenCompose(result -> protocolHandler.sendInitialized())
        .thenRun(this::startHeartbeat)
        .thenRun(() -> {
            state.set(State.CONNECTED);
            logger.info("Connected!");
        })
        .exceptionally(ex -> {
            logger.error("Connection failed", ex);
            return null;
        });
```

### 10.3 SSE 流式解析

```java
// SSE 是流式的，需要逐块解析

private StringBuilder sseBuffer = new StringBuilder();

@Override
protected void channelRead0(ChannelHandlerContext ctx, HttpObject msg) {
    if (msg instanceof HttpContent) {
        HttpContent content = (HttpContent) msg;
        String chunk = content.content().toString(CharsetUtil.UTF_8);
        sseBuffer.append(chunk);
        
        // 查找完整事件（以 \n\n 分隔）
        processSseEvents();
    }
}

private void processSseEvents() {
    String buffer = sseBuffer.toString();
    int lastEventEnd = 0;
    
    int eventEnd;
    while ((eventEnd = buffer.indexOf("\n\n", lastEventEnd)) != -1) {
        String event = buffer.substring(lastEventEnd, eventEnd);
        lastEventEnd = eventEnd + 2;
        processEvent(event);
    }
    
    // 保留未处理的部分
    if (lastEventEnd > 0) {
        sseBuffer = new StringBuilder(buffer.substring(lastEventEnd));
    }
}
```

### 10.4 Session 管理

```java
// Session ID 的生命周期

// 1. 首次连接：服务端分配 session ID
String sessionId = response.headers().get("Mcp-Session-Id");

// 2. 后续请求：携带 session ID
request.headers().set("Mcp-Session-Id", sessionId);
String uri = "/mcp?sessionId=" + sessionId;

// 3. 重连：复用 session ID
// 服务端可以根据 session ID 恢复会话状态
```

### 10.5 线程安全

```java
// 1. 状态管理：AtomicReference + CAS
private final AtomicReference<State> state = new AtomicReference<>(State.DISCONNECTED);
state.compareAndSet(State.DISCONNECTED, State.CONNECTING);

// 2. Pending 请求：ConcurrentHashMap
private final Map<Object, CompletableFuture<JSONRPCResponse>> pendingRequests = new ConcurrentHashMap<>();

// 3. 心跳时间：AtomicLong
private final AtomicLong lastPongTime = new AtomicLong(0);

// 4. 重连延迟：synchronized
public synchronized long getNextDelay() {
    // ...
}
```

---

## 11. 使用示例

### 11.1 基本使用

```java
// 1. 创建工具提供者
DefaultToolCallbackProvider toolProvider = new DefaultToolCallbackProvider();
toolProvider.setToolBasePackage("com.taobao.arthas.mcp.server.tool.function");

// 2. 创建客户端
ArthasMcpClient client = ArthasMcpClient.create("http://localhost:8080/mcp")
        .authToken("your-token")
        .toolCallbackProvider(toolProvider)
        .build();

// 3. 启动客户端
client.start()
        .thenRun(() -> System.out.println("Connected!"))
        .exceptionally(ex -> {
            ex.printStackTrace();
            return null;
        });

// 4. 保持运行
Thread.currentThread().join();

// 5. 停止客户端
client.stop().join();
```

### 11.2 自定义工具

```java
// 1. 实现 ToolCallback
public class MyTool implements ToolCallback {
    @Override
    public McpSchema.Tool getToolDefinition() {
        return new McpSchema.Tool(
                "my-tool",
                "My custom tool",
                inputSchema
        );
    }
    
    @Override
    public String call(String input, ToolContext context) {
        // 执行工具逻辑
        return "result";
    }
}

// 2. 注册工具
protocolHandler.registerToolCallback("my-tool", new MyTool());
```

---

## 12. 总结

### 12.1 核心特性

✅ **反向连接**: 客户端主动连接服务端，无需暴露端口  
✅ **双向通信**: SSE 接收请求，HTTP POST 发送响应  
✅ **高可用**: 自动重连、心跳检测、指数退避  
✅ **工具复用**: 复用 Arthas 现有工具体系  
✅ **易于扩展**: 支持自定义工具、配置灵活  

### 12.2 技术亮点

🔥 **Netty 异步 I/O**: 高性能网络通信  
🔥 **CompletableFuture**: 优雅的异步编程  
🔥 **SSE 流式解析**: 实时接收服务端推送  
🔥 **状态机管理**: 清晰的状态转换逻辑  
🔥 **指数退避**: 智能的重连策略  

### 12.3 适用场景

- 🎯 远程诊断：将本地 Arthas 能力暴露给远程管控平台
- 🎯 智能运维：接入 AI 智能体，实现自动化诊断
- 🎯 多实例管理：统一管理多个 Arthas 实例
- 🎯 安全隔离：无需暴露本地端口，通过反向连接保证安全

---

**文档结束**

如有疑问，请参考源码或联系 Arthas 团队。
