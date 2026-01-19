# Arthas MCP Server 反向连接模式 - 问题分析与方案设计

## 📋 文档信息

- **创建日期**: 2026-01-16
- **更新日期**: 2026-01-19
- **版本**: v3.1
- **作者**: Arthas Team
- **状态**: 实现完成

## 📌 重要说明

**术语澄清**：
- **Arthas 在反向连接模式下仍然是 MCP Server**（提供 Tools 能力）
- **只是连接方向相反**：从"被动等待连接"变为"主动发起连接"
- **正确术语**：Reverse Connection MCP Server（反向连接 MCP 服务器）
- **不是**：MCP Client（MCP 客户端通常指使用工具的一方，如 Cherry Studio）

---

## 1. 问题背景

### 1.1 当前架构

Arthas MCP 目前采用 **Server 模式**，架构如下：

```
┌─────────────────────┐         HTTP/SSE          ┌──────────────────────┐
│   AI 客户端          │ ──────────────────────>  │  Arthas MCP Server   │
│ (Cherry Studio等)   │    主动发起连接            │  (监听 8563 端口)    │
└─────────────────────┘                           └──────────────────────┘
        公网                                              内网/本地
```

**核心组件**：
- `McpNettyServer`: 基于 Netty 的 HTTP Server
- `McpStatelessNettyServer`: 无状态版本的 MCP Server
- `McpHttpHandler`: 处理 HTTP 请求和 SSE 连接
- `ToolCallback`: 工具执行回调接口

### 1.2 存在的问题

#### 问题 1: 安全风险
- **端口暴露**: 内网服务需要暴露 8563 端口到公网
- **攻击面扩大**: 增加了被攻击的可能性
- **合规性问题**: 部分企业安全策略禁止内网服务暴露端口

#### 问题 2: 部署复杂
- **网络配置**: 需要配置防火墙规则、端口映射
- **证书管理**: HTTPS 需要配置 SSL 证书
- **运维成本**: 增加网络运维复杂度

#### 问题 3: 扩展性受限
- **单向连接**: 只能被动接受连接，无法主动推送
- **多实例管理**: 难以统一管理多个 Arthas 实例
- **负载均衡**: 需要额外的负载均衡器

### 1.3 典型应用场景

**场景描述**：
- 智能体部署在公网（如云服务）
- Arthas 部署在内网（如企业内部服务器）
- 内网服务不应暴露端口到公网
- 需要智能体能够调用 Arthas 的诊断工具

**需求**：
> Arthas 主动发起连接到公网智能体，建立 MCP 通信通道，实现反向连接模式。

---

## 2. 方案设计

### 2.1 目标架构

```
┌──────────────────────────────────┐                          ┌─────────────────────┐
│  Arthas MCP Server               │   HTTP/SSE 反向连接       │  公网智能体/管控平台  │
│  (反向连接模式)                   │ ──────────────────────>  │  MCP Server         │
│  - 仍然是 MCP Server             │                          │                     │
│  - 提供 Tools 能力               │   http://agent.com/mcp   │                     │
│  - 只是主动发起连接               │                          │                     │
└──────────────────────────────────┘                          └─────────────────────┘
         ↑                                                            ↓
         │                                                            │
         │                    MCP 协议双向通信                         │
         │                    - tools/list (SSE推送)                 │
         │                    - tools/call (SSE推送)                 │
         │                    - 响应 (HTTP POST)                     │
         └────────────────────────────────────────────────────────────┘
```

**核心变化**：
1. Arthas 从 **被动接受连接** 变为 **主动发起连接**
2. 使用 **HTTP/SSE** 实现通信（与原有 Server 模式保持一致）
3. 保持 MCP 协议不变，只反转连接方向
4. **Arthas 仍然是 MCP Server**（提供工具能力），不是 MCP Client

### 2.2 技术选型

#### 2.2.1 为什么选择 HTTP/SSE 而非 WebSocket

| 特性 | HTTP/SSE | WebSocket |
|------|----------|-----------|
| **与原有架构一致性** | ✅ 完全一致 | ❌ 不同协议 |
| **代码复用** | ✅ 可复用现有组件 | ⚠️ 需新实现 |
| **MCP 协议兼容** | ✅ 原生支持 | ⚠️ 需适配 |
| **调试友好** | ✅ HTTP 易于抓包 | ⚠️ 需要专用工具 |
| **防火墙穿透** | ✅ 纯 HTTP 协议 | ⚠️ 可能被阻止 |
| **实现复杂度** | ✅ 中等 | ✅ 中等 |

**最终选择**: **HTTP/SSE**

**理由**：
- ✅ 与原有 MCP Server 架构保持一致
- ✅ 可以复用 Netty HTTP 组件
- ✅ 协议级别完全兼容
- ✅ 便于调试和问题排查

#### 2.2.2 复用的 Netty 组件

| 组件 | 说明 |
|------|------|
| `Bootstrap` | 客户端启动器 |
| `NioEventLoopGroup` | 事件循环组 |
| `HttpClientCodec` | HTTP 编解码器 |
| `HttpObjectAggregator` | HTTP 消息聚合器 |
| `SslContext` | SSL/TLS 支持 |

### 2.3 架构设计

#### 2.3.1 模块结构

```
arthas-mcp-server/
├── src/main/java/com/taobao/arthas/mcp/
│   ├── server/protocol/client/          # 新增：Client 模式
│   │   ├── ArthasMcpClient.java         # MCP 客户端主类
│   │   ├── McpClientConfig.java         # 客户端配置
│   │   ├── McpHttpClient.java           # HTTP/SSE 客户端
│   │   ├── McpClientProtocolHandler.java # 协议处理器
│   │   ├── ReconnectStrategy.java       # 重连策略
│   │   ├── HeartbeatManager.java        # 心跳管理
│   │   └── McpClientBootstrap.java      # 测试入口
│   ├── server/protocol/server/          # 原有：Server 模式
│   │   ├── McpNettyServer.java
│   │   └── ...
│   ├── protocol/spec/                   # 共享：协议定义
│   │   ├── McpSchema.java
│   │   └── ...
│   └── tool/                            # 共享：工具实现
│       ├── ToolCallback.java
│       └── ...
└── test_mcp_http_server.py              # Python 测试服务端
```

#### 2.3.2 核心类设计

##### ArthasMcpClient

```java
/**
 * Arthas MCP Server - 反向连接模式
 * 主动连接到公网智能体，提供 MCP 工具服务
 * 
 * 注意：虽然类名叫 Client，但本质上仍然是 MCP Server（提供 Tools）
 * "Client" 仅指连接方向（主动发起连接），而非协议角色
 */
public class ArthasMcpClient {
    // 配置
    private final McpClientConfig config;
    
    // HTTP/SSE 客户端
    private McpHttpClient httpClient;
    
    // 协议处理器
    private final McpClientProtocolHandler protocolHandler;
    
    // 连接状态
    private volatile State state;
    
    // 心跳管理
    private final HeartbeatManager heartbeatManager;
    
    // 重连策略
    private final ReconnectStrategy reconnectStrategy;
    
    /**
     * 启动客户端，连接到服务端
     */
    public CompletableFuture<Void> start();
    
    /**
     * 停止客户端
     */
    public CompletableFuture<Void> stop();
}
```

##### McpHttpClient

```java
/**
 * HTTP/SSE 客户端封装
 * 基于 Netty 实现
 */
public class McpHttpClient {
    private final McpClientConfig config;
    private EventLoopGroup eventLoopGroup;
    
    /**
     * 建立 SSE 连接
     */
    public CompletableFuture<Void> connectSse();
    
    /**
     * 发送 JSON-RPC 请求
     */
    public CompletableFuture<JSONRPCResponse> sendRequest(JSONRPCRequest request);
    
    /**
     * 发送 JSON-RPC 响应
     */
    public CompletableFuture<Void> sendResponse(JSONRPCResponse response);
    
    /**
     * 关闭连接
     */
    public CompletableFuture<Void> close();
}
```

##### McpClientProtocolHandler

```java
/**
 * MCP 协议处理器（客户端）
 * 处理来自服务端的请求
 */
public class McpClientProtocolHandler {
    private final Map<String, ToolCallback> toolCallbacks;
    
    /**
     * 处理接收到的消息
     */
    public void handleMessage(JSONRPCMessage message);
    
    /**
     * 发送 initialize 请求
     */
    public CompletableFuture<InitializeResult> sendInitialize();
    
    /**
     * 处理 tools/list 请求
     */
    private CompletableFuture<Object> handleToolsList(JSONRPCRequest request);
    
    /**
     * 处理 tools/call 请求
     */
    private CompletableFuture<Object> handleToolsCall(JSONRPCRequest request);
}
```

#### 2.3.3 配置设计

##### 环境变量

```bash
# 服务端地址（支持 http:// 和 https://）
ARTHAS_MCP_CLIENT_SERVER_URL=http://your-ai-agent.com/mcp

# 认证 Token（可选）
ARTHAS_MCP_CLIENT_AUTH_TOKEN=your-secret-token

# 重连配置
ARTHAS_MCP_CLIENT_RECONNECT_ENABLED=true
ARTHAS_MCP_CLIENT_RECONNECT_INITIAL_DELAY=5000
ARTHAS_MCP_CLIENT_RECONNECT_MAX_DELAY=300000
ARTHAS_MCP_CLIENT_RECONNECT_MULTIPLIER=2.0

# 心跳配置
ARTHAS_MCP_CLIENT_HEARTBEAT_ENABLED=true
ARTHAS_MCP_CLIENT_HEARTBEAT_INTERVAL=30000
ARTHAS_MCP_CLIENT_HEARTBEAT_TIMEOUT=10000

# 连接配置
ARTHAS_MCP_CLIENT_CONNECT_TIMEOUT=10000
ARTHAS_MCP_CLIENT_REQUEST_TIMEOUT=30000
```

### 2.4 协议流程

#### 2.4.1 连接建立流程

```
Arthas Client                                    管控平台
      |                                                  |
      |  1. 建立 SSE 长连接                               |
      |  GET /mcp                                       |
      |  Accept: text/event-stream                      |
      |  Authorization: Bearer <token>                  |
      |------------------------------------------------->|
      |                                                  |
      |  2. SSE 连接响应                                  |
      |  HTTP 200 OK                                    |
      |  Content-Type: text/event-stream               |
      |  Mcp-Session-Id: <session-id>                  |
      |<-------------------------------------------------|
      |                                                  |
      |  3. 发送 initialize 请求 (HTTP POST)            |
      |  {                                               |
      |    "jsonrpc": "2.0",                            |
      |    "id": 1,                                     |
      |    "method": "initialize",                      |
      |    "params": {                                  |
      |      "protocolVersion": "2025-03-26",          |
      |      "capabilities": {},                        |
      |      "clientInfo": {                            |
      |        "name": "arthas-mcp-client",            |
      |        "version": "4.1.5"                      |
      |      }                                          |
      |    }                                            |
      |  }                                              |
      |------------------------------------------------->|
      |                                                  |
      |  4. 返回 initialize 响应                         |
      |  {                                               |
      |    "jsonrpc": "2.0",                            |
      |    "id": 1,                                     |
      |    "result": {                                  |
      |      "protocolVersion": "2025-03-26",          |
      |      "capabilities": {},                        |
      |      "serverInfo": {                            |
      |        "name": "ai-agent",                     |
      |        "version": "1.0.0"                      |
      |      }                                          |
      |    }                                            |
      |  }                                              |
      |<-------------------------------------------------|
      |                                                  |
      |  5. 发送 initialized 通知 (HTTP POST)           |
      |  {                                               |
      |    "jsonrpc": "2.0",                            |
      |    "method": "notifications/initialized"        |
      |  }                                              |
      |------------------------------------------------->|
      |                                                  |
      |  6. 202 Accepted                                |
      |<-------------------------------------------------|
      |                                                  |
      |  连接建立完成，开始心跳                            |
      |                                                  |
```

#### 2.4.2 工具调用流程

```
Arthas Client                                    管控平台
      |                                                  |
      |  1. 管控平台通过 SSE 发送请求                     |
      |  event: message                                 |
      |  data: {                                        |
      |    "jsonrpc": "2.0",                            |
      |    "id": 2,                                     |
      |    "method": "tools/call",                      |
      |    "params": {                                  |
      |      "name": "thread",                         |
      |      "arguments": {"n": 5}                     |
      |    }                                            |
      |  }                                              |
      |<-------------------------------------------------|
      |                                                  |
      |  2. 执行工具: ToolCallback.call()               |
      |  (复用 Arthas 现有工具实现)                      |
      |                                                  |
      |  3. 返回执行结果 (HTTP POST)                     |
      |  {                                               |
      |    "jsonrpc": "2.0",                            |
      |    "id": 2,                                     |
      |    "result": {                                  |
      |      "content": [                               |
      |        {                                        |
      |          "type": "text",                       |
      |          "text": "{\"threads\": [...]}"        |
      |        }                                        |
      |      ]                                          |
      |    }                                            |
      |  }                                              |
      |------------------------------------------------->|
      |                                                  |
      |  4. 202 Accepted                                |
      |<-------------------------------------------------|
```

#### 2.4.3 心跳保活流程

```
Arthas Client                                    管控平台
      |                                                  |
      |  每 30 秒发送一次 ping (HTTP POST)               |
      |  {                                               |
      |    "jsonrpc": "2.0",                            |
      |    "id": N,                                     |
      |    "method": "ping"                             |
      |  }                                              |
      |------------------------------------------------->|
      |                                                  |
      |  返回响应                                         |
      |  {                                               |
      |    "jsonrpc": "2.0",                            |
      |    "id": N,                                     |
      |    "result": {}                                 |
      |  }                                              |
      |<-------------------------------------------------|
      |                                                  |
      |  如果请求超时，触发重连                           |
      |                                                  |
```

#### 2.4.4 断线重连流程

```
Arthas Client                                    管控平台
      |                                                  |
      |  检测到连接断开                                   |
      |  （SSE 断开 / 心跳超时）                          |
      |                                                  |
      |  等待 5 秒（初始延迟）                            |
      |                                                  |
      |  尝试重连（第 1 次）                              |
      |------------------------------------------------->|
      |                                                  |
      |  连接失败                                         |
      |<-------------------------------------------------|
      |                                                  |
      |  等待 10 秒（5 * 2）                             |
      |                                                  |
      |  尝试重连（第 2 次）                              |
      |------------------------------------------------->|
      |                                                  |
      |  连接成功                                         |
      |<-------------------------------------------------|
      |                                                  |
      |  重新执行 initialize 流程                         |
      |                                                  |
```

### 2.5 安全设计

#### 2.5.1 认证机制

**Bearer Token 认证**：

```
HTTP 请求时携带 Token：

POST /mcp HTTP/1.1
Host: your-ai-agent.com
Content-Type: application/json
Authorization: Bearer your-secret-token
```

#### 2.5.2 SSL/TLS

- 支持 `https://` 安全连接
- 使用 Netty SslContext
- 可配置证书验证

#### 2.5.3 会话管理

- 通过 `Mcp-Session-Id` 头维护会话
- 重连后可能获得新的 Session ID
- 服务端可基于 Session 进行访问控制

---

## 3. 实施结果

### 3.1 完成的文件

#### Java 源文件

| 文件 | 行数 | 说明 |
|------|------|------|
| `McpClientConfig.java` | ~320 | 配置类，支持环境变量 |
| `ArthasMcpClient.java` | ~300 | 主客户端类 |
| `McpHttpClient.java` | ~480 | HTTP/SSE 客户端 |
| `McpClientProtocolHandler.java` | ~280 | 协议处理 |
| `ReconnectStrategy.java` | ~95 | 重连策略 |
| `HeartbeatManager.java` | ~115 | 心跳管理 |
| `McpClientBootstrap.java` | ~90 | 测试入口 |

#### 主流程集成

| 文件 | 模块 | 说明 |
|------|------|------|
| `ArthasMcpServer.java` | core | Arthas 主 MCP 服务，已集成 Client 模式 |

#### Python 测试脚本

| 文件 | 说明 |
|------|------|
| `test_mcp_http_server.py` | HTTP/SSE 模式测试服务端 |

### 3.2 集成到 Arthas 主启动流程

MCP Client 功能已经**集成到 `ArthasMcpServer.java`** 中，与 Arthas 主流程无缝集成。

#### 工作模式

支持两种模式，可以同时启用：

| 模式 | 说明 | 启用条件 | 适用场景 |
|------|------|----------|----------|
| **Server 模式** | 等待客户端连接 | 默认启用 | 本地开发、同网段 |
| **Client 模式** | 主动连接管控平台 | 设置 `ARTHAS_MCP_CLIENT_SERVER_URL` | 内网穿透、统一管控 |

#### 启动流程

```
┌─────────────────────────────────────────────────────────────────┐
│                  ArthasMcpServer.start()                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. 注册 JSON Filter                                            │
│                                                                 │
│  2. 创建 DefaultToolCallbackProvider（共享）                     │
│     - 扫描 ARTHAS_TOOL_BASE_PACKAGE 下的工具                    │
│                                                                 │
│  3. 检查启动模式                                                 │
│     ├── ARTHAS_MCP_CLIENT_ONLY=true ?                           │
│     │   └── 是: 跳过 Server 模式                                │
│     │                                                           │
│     └── ARTHAS_MCP_CLIENT_SERVER_URL 已配置 ?                   │
│         └── 是: 启动 Client 模式                                │
│                                                                 │
│  4. 启动 Server 模式（除非 CLIENT_ONLY）                        │
│     - 创建 McpHttpRequestHandler                                │
│     - 创建 McpNettyServer                                       │
│     - 注册工具                                                   │
│                                                                 │
│  5. 启动 Client 模式（如果配置了 SERVER_URL）                    │
│     - 创建 ArthasMcpClient                                      │
│     - 异步连接到管控平台                                         │
│     - 复用同一个 ToolCallbackProvider                           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

#### 环境变量配置

```bash
# ==============================
# 启用 Client 模式（与 Server 模式同时运行）
# ==============================
export ARTHAS_MCP_CLIENT_SERVER_URL=http://your-server.com:8080/mcp
java -jar arthas-boot.jar

# ==============================
# 只启用 Client 模式（不启动 Server）
# ==============================
export ARTHAS_MCP_CLIENT_ONLY=true
export ARTHAS_MCP_CLIENT_SERVER_URL=http://your-server.com:8080/mcp
java -jar arthas-boot.jar

# ==============================
# 完整配置
# ==============================
export ARTHAS_MCP_CLIENT_SERVER_URL=http://your-server.com:8080/mcp
export ARTHAS_MCP_CLIENT_AUTH_TOKEN=your-token
export ARTHAS_MCP_CLIENT_RECONNECT_ENABLED=true
export ARTHAS_MCP_CLIENT_HEARTBEAT_ENABLED=true
export ARTHAS_MCP_CLIENT_HEARTBEAT_INTERVAL=30000
java -jar arthas-boot.jar
```

### 3.3 测试方法

```bash
# 1. 启动 Python 测试服务端
python test_mcp_http_server.py

# 2. 设置环境变量
export ARTHAS_MCP_CLIENT_SERVER_URL=http://localhost:8080/mcp

# 3. 启动 Arthas（自动启动 Server + Client 模式）
java -jar arthas-boot.jar

# 或者单独测试 Client（运行 McpClientBootstrap）
java -cp arthas-mcp-server.jar \
  com.taobao.arthas.mcp.server.protocol.client.McpClientBootstrap

# 4. 在测试服务端执行命令
>>> list     # 获取工具列表
>>> jvm      # 调用 jvm 工具
>>> thread   # 调用 thread 工具
```

---

## 5. 代码详细设计说明

本章详细说明各个类的功能职责、核心方法和交互方式，帮助理解整体设计。

### 5.1 类职责概览

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          ArthasMcpClient                                     │
│                         （门面类/入口类）                                      │
│  职责：对外暴露简洁 API，协调各组件，管理生命周期                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────────────┐   ┌──────────────────────┐   ┌────────────────┐ │
│  │   McpHttpClient       │   │ McpClientProtocol    │   │ McpClientConfig│ │
│  │   (网络层)            │   │    Handler           │   │   (配置类)      │ │
│  │                       │   │   (协议层)           │   │                │ │
│  │ - Netty Bootstrap     │   │                      │   │ - 环境变量解析  │ │
│  │ - SSE 长连接          │   │ - JSON-RPC 处理      │   │ - 重连配置     │ │
│  │ - HTTP POST 请求      │   │ - 工具调用执行       │   │ - 心跳配置     │ │
│  │ - SSL/TLS 支持        │   │ - initialize 握手   │   │ - 超时配置     │ │
│  └──────────┬───────────┘   └──────────┬───────────┘   └────────────────┘ │
│             │                          │                                    │
│             └──────────┬───────────────┘                                    │
│                        │                                                    │
│  ┌──────────────────────┐   ┌──────────────────────┐                        │
│  │  ReconnectStrategy   │   │  HeartbeatManager    │                        │
│  │    (重连策略)         │   │    (心跳管理)         │                        │
│  │                      │   │                      │                        │
│  │ - 指数退避算法        │   │ - 定时发送 ping      │                        │
│  │ - 延迟计算           │   │ - 超时检测           │                        │
│  │ - 尝试次数计数       │   │ - 触发重连回调       │                        │
│  └──────────────────────┘   └──────────────────────┘                        │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 5.2 ArthasMcpClient（主入口类）

**文件**: `ArthasMcpClient.java` (~444 行)

**重要说明**：虽然类名叫 `ArthasMcpClient`，但它仍然是一个 **MCP Server**（提供 Tools 能力）。
"Client" 仅表示连接方向（主动发起连接），而非协议角色。

#### 5.2.1 核心职责

1. **对外门面**：提供简洁的 Builder API 创建反向连接服务器
2. **生命周期管理**：管理 `start()` 和 `stop()` 流程
3. **组件协调**：协调 HttpClient、ProtocolHandler、HeartbeatManager
4. **状态管理**：维护连接状态机

#### 5.2.2 状态机设计

```
                    ┌──────────────────────────────────────────────────┐
                    │                                                  │
                    ▼                                                  │
┌─────────────┐  start()  ┌─────────────┐  成功   ┌─────────────┐     │
│ DISCONNECTED│─────────>│  CONNECTING  │─────────>│  CONNECTED  │     │
└─────────────┘          └─────────────┘          └──────┬──────┘     │
      ▲                         │                        │            │
      │                         │ 失败                   │ 连接丢失   │
      │                         ▼                        ▼            │
      │                  ┌─────────────┐          ┌─────────────┐     │
      │                  │DISCONNECTED │          │ RECONNECTING│─────┘
      │                  └─────────────┘          └─────────────┘ 重连成功
      │                                                  │
      │                         stop()                   │ stop()
      │                           │                      │
      │                           ▼                      ▼
      │                    ┌─────────────┐
      └────────────────────│   STOPPED   │
                           └─────────────┘
```

#### 5.2.3 核心方法

```java
// 1. 创建客户端（Builder 模式）
ArthasMcpClient client = ArthasMcpClient.create("http://server:8080/mcp")
    .authToken("token")
    .reconnectEnabled(true)
    .heartbeatEnabled(true)
    .toolCallbackProvider(provider)
    .build();

// 2. 启动流程（异步）
public CompletableFuture<Void> start() {
    // a. 状态检查：DISCONNECTED -> CONNECTING
    // b. 初始化调度器 (ScheduledExecutorService)
    // c. 初始化协议处理器 (McpClientProtocolHandler)
    // d. 注册工具 (ToolCallback[])
    // e. 初始化 HTTP 客户端 (McpHttpClient)
    // f. 执行连接流程 connect()
}

// 3. 连接流程
private CompletableFuture<Void> connect() {
    return httpClient.connectSse()           // 1. 建立 SSE 连接
        .thenCompose(v -> protocolHandler.sendInitialize())  // 2. 发送 initialize
        .thenCompose(result -> protocolHandler.sendInitialized())  // 3. 发送 initialized
        .thenRun(this::startHeartbeat);      // 4. 启动心跳
}

// 4. 连接丢失处理
private void onConnectionLost() {
    // a. 检查当前状态
    // b. 如果启用重连，状态改为 RECONNECTING
    // c. 调度重连任务
}

// 5. 停止流程
public CompletableFuture<Void> stop() {
    // a. 状态改为 STOPPED
    // b. 停止心跳
    // c. 关闭 HTTP 客户端
    // d. 关闭调度器
}
```

### 5.3 McpHttpClient（网络层）

**文件**: `McpHttpClient.java` (~711 行)

#### 5.3.1 核心职责

1. **SSE 长连接**：建立和维护 SSE 连接，接收服务端推送
2. **HTTP POST**：发送 JSON-RPC 请求/响应/通知
3. **SSL 支持**：处理 HTTPS 连接
4. **Session 管理**：维护 `Mcp-Session-Id`

#### 5.3.2 架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                       McpHttpClient                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    Netty Bootstrap                       │   │
│  │  - NioEventLoopGroup (2 threads)                        │   │
│  │  - SslContext (可选，用于 HTTPS)                         │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────────┐     ┌─────────────────────────────┐   │
│  │   SSE Channel        │     │      POST Channels          │   │
│  │   (长连接)           │     │      (短连接，每次新建)      │   │
│  │                      │     │                             │   │
│  │  Pipeline:           │     │  Pipeline:                  │   │
│  │  - SslHandler       │     │  - SslHandler              │   │
│  │  - HttpClientCodec  │     │  - HttpClientCodec         │   │
│  │  - HttpAggregator   │     │  - HttpAggregator          │   │
│  │  - IdleStateHandler │     │  - HttpResponseHandler     │   │
│  │  - SseHandler       │     │                             │   │
│  └─────────────────────┘     └─────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

#### 5.3.3 SSE 连接处理

```java
// SSE 请求格式
GET /mcp HTTP/1.1
Host: server:8080
Accept: text/event-stream
Cache-Control: no-cache
Authorization: Bearer <token>

// SSE 响应格式
HTTP/1.1 200 OK
Content-Type: text/event-stream
Mcp-Session-Id: <session-id>

event: message
data: {"jsonrpc":"2.0","id":1,"method":"tools/call",...}

event: message
data: {"jsonrpc":"2.0","id":2,"method":"tools/list"}
```

**SseHandler 内部类核心逻辑**：

```java
private class SseHandler extends SimpleChannelInboundHandler<HttpObject> {
    
    @Override
    protected void channelRead0(ChannelHandlerContext ctx, HttpObject msg) {
        if (msg instanceof HttpResponse) {
            // 1. 检查状态码 == 200
            // 2. 检查 Content-Type 包含 text/event-stream
            // 3. 提取 Mcp-Session-Id 保存
            // 4. 标记 sseConnected = true
        }
        
        if (msg instanceof HttpContent) {
            // 1. 将数据追加到 sseBuffer
            // 2. 按 "\n\n" 分割完整事件
            // 3. 解析 event: 和 data: 字段
            // 4. 反序列化为 JSONRPCMessage
            // 5. 调用 messageHandler.accept(message)
        }
    }
}
```

#### 5.3.4 HTTP POST 请求

```java
// POST 请求格式
POST /mcp?sessionId=<session-id> HTTP/1.1
Host: server:8080
Content-Type: application/json
Authorization: Bearer <token>
Mcp-Session-Id: <session-id>

{"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}

// 响应格式
HTTP/1.1 200 OK
Content-Type: application/json

{"jsonrpc":"2.0","id":1,"result":{...}}
```

### 5.4 McpClientProtocolHandler（协议层）

**文件**: `McpClientProtocolHandler.java` (~434 行)

#### 5.4.1 核心职责

1. **消息分发**：根据消息类型路由到对应处理器
2. **工具管理**：注册和执行工具回调
3. **握手处理**：发送 initialize/initialized
4. **响应构造**：构造 JSON-RPC 响应

#### 5.4.2 消息处理流程

```
              收到 SSE 消息
                   │
                   ▼
          ┌─────────────────┐
          │  handleMessage  │
          └────────┬────────┘
                   │
       ┌───────────┼───────────┐
       │           │           │
       ▼           ▼           ▼
┌────────────┐┌────────────┐┌────────────┐
│JSONRPCReq  ││JSONRPCNotif││JSONRPCResp │
└─────┬──────┘└─────┬──────┘└─────┬──────┘
      │             │             │
      ▼             ▼             │
 handleRequest  handleNotif      │
      │             │             │
      │             │             │
 ┌────┴────┐        │             │
 │ method? │        │             │
 └────┬────┘        │             │
      │             │             │
┌─────┼─────┐       │             │
│     │     │       │             │
▼     ▼     ▼       │             │
tools/ tools/ ping  │             │
list   call         │             │
│      │     │      │             │
│      │     │      │             │
▼      ▼     ▼      ▼             ▼
 构造响应 → HTTP POST 发送  →  pendingRequests
                                   完成
```

#### 5.4.3 工具调用处理

```java
private CompletableFuture<Object> handleToolsCall(JSONRPCRequest request) {
    return CompletableFuture.supplyAsync(() -> {
        // 1. 解析请求参数
        CallToolRequest callRequest = objectMapper.convertValue(
            request.getParams(), CallToolRequest.class);
        
        String toolName = callRequest.getName();
        Map<String, Object> arguments = callRequest.getArguments();
        
        // 2. 查找工具回调
        ToolCallback callback = toolCallbacks.get(toolName);
        if (callback == null) {
            throw new IllegalArgumentException("Tool not found: " + toolName);
        }
        
        // 3. 构建工具输入（JSON 字符串）
        String toolInput = objectMapper.writeValueAsString(arguments);
        
        // 4. 构建工具上下文
        ToolContext toolContext = new ToolContext(callRequest.meta());
        
        // 5. 执行工具（复用 Arthas 现有工具实现）
        String result = callback.call(toolInput, toolContext);
        
        // 6. 构造响应
        return CallToolResult.builder()
            .addTextContent(result)
            .isError(false)
            .build();
    });
}
```

### 5.5 McpClientConfig（配置类）

**文件**: `McpClientConfig.java` (~447 行)

#### 5.5.1 配置结构

```java
McpClientConfig
├── serverUrl          // 管控平台地址（必填）
├── authToken          // 认证 Token（可选）
├── connectTimeout     // 连接超时（默认 10s）
├── requestTimeout     // 请求超时（默认 30s）
├── sseReconnectDelay  // SSE 重连延迟（默认 3s）
├── clientName         // 客户端名称
├── clientVersion      // 客户端版本
├── ReconnectConfig    // 重连配置
│   ├── enabled        // 是否启用（默认 true）
│   ├── initialDelay   // 初始延迟（默认 5s）
│   ├── maxDelay       // 最大延迟（默认 5min）
│   └── multiplier     // 延迟倍数（默认 2.0）
└── HeartbeatConfig    // 心跳配置
    ├── enabled        // 是否启用（默认 true）
    ├── interval       // 心跳间隔（默认 30s）
    └── timeout        // 超时时间（默认 10s）
```

#### 5.5.2 环境变量映射

```java
// 从环境变量加载配置
public static McpClientConfig fromEnvironment() {
    McpClientConfig config = new McpClientConfig();
    
    // 服务端地址
    config.setServerUrl(getEnv("ARTHAS_MCP_CLIENT_SERVER_URL"));
    
    // 认证 Token
    config.setAuthToken(getEnv("ARTHAS_MCP_CLIENT_AUTH_TOKEN"));
    
    // 重连配置
    config.getReconnect().setEnabled(
        Boolean.parseBoolean(getEnv("ARTHAS_MCP_CLIENT_RECONNECT_ENABLED")));
    config.getReconnect().setInitialDelay(
        Long.parseLong(getEnv("ARTHAS_MCP_CLIENT_RECONNECT_INITIAL_DELAY")));
    
    // 心跳配置
    config.getHeartbeat().setEnabled(
        Boolean.parseBoolean(getEnv("ARTHAS_MCP_CLIENT_HEARTBEAT_ENABLED")));
    config.getHeartbeat().setInterval(
        Long.parseLong(getEnv("ARTHAS_MCP_CLIENT_HEARTBEAT_INTERVAL")));
    
    return config;
}
```

### 5.6 ReconnectStrategy（重连策略）

**文件**: `ReconnectStrategy.java` (~106 行)

#### 5.6.1 指数退避算法

```
重连延迟序列（默认配置）：
  第1次: 5s
  第2次: 10s  (5 * 2)
  第3次: 20s  (10 * 2)
  第4次: 40s  (20 * 2)
  第5次: 80s  (40 * 2)
  第6次: 160s (80 * 2)
  第7次: 300s (160 * 2 = 320, 但不超过 maxDelay=300s)
  ...
  后续: 300s (保持最大延迟)
```

#### 5.6.2 核心方法

```java
public synchronized long getNextDelay() {
    attemptCount.incrementAndGet();
    long delay = currentDelay;
    
    // 计算下次延迟（指数增长，不超过 maxDelay）
    currentDelay = Math.min((long)(currentDelay * multiplier), maxDelay);
    
    return delay;
}

public synchronized void reset() {
    attemptCount.set(0);
    currentDelay = initialDelay;  // 连接成功后重置
}
```

### 5.7 HeartbeatManager（心跳管理）

**文件**: `HeartbeatManager.java` (~130 行)

#### 5.7.1 心跳流程

```
        ┌─────────────────────────────────────────────────────┐
        │                 HeartbeatManager                     │
        │                                                      │
        │   ScheduledExecutorService                          │
        │        │                                             │
        │        │ scheduleAtFixedRate(interval)              │
        │        ▼                                             │
        │   ┌─────────────────────────────────────────────┐   │
        │   │            心跳任务循环                       │   │
        │   │                                             │   │
        │   │  1. 计算 elapsed = now - lastPongTime       │   │
        │   │                                             │   │
        │   │  2. if (elapsed > interval + timeout)       │   │
        │   │        → 触发 timeoutAction (重连)          │   │
        │   │                                             │   │
        │   │  3. 调用 pingAction (发送 ping)             │   │
        │   │                                             │   │
        │   └─────────────────────────────────────────────┘   │
        │                                                      │
        │   收到 pong 响应 → onPong() → 更新 lastPongTime      │
        │                                                      │
        └─────────────────────────────────────────────────────┘
```

#### 5.7.2 超时检测

```java
// 心跳任务
heartbeatTask = scheduler.scheduleAtFixedRate(() -> {
    long now = System.currentTimeMillis();
    long elapsed = now - lastPongTime.get();
    
    // 超时判断：距离上次 pong 超过 (心跳间隔 + 超时时间)
    // 例如：30s + 10s = 40s
    if (elapsed > interval + timeout) {
        logger.warn("Heartbeat timeout: {}ms since last pong", elapsed);
        timeoutAction.run();  // 触发重连
        return;
    }
    
    // 发送心跳
    pingAction.run();
    
}, interval, interval, TimeUnit.MILLISECONDS);
```

### 5.8 McpClientBootstrap（测试入口）

**文件**: `McpClientBootstrap.java` (~100 行)

用于独立测试 Client 功能的启动类：

```java
public static void main(String[] args) {
    // 1. 读取环境变量配置
    String serverUrl = System.getenv("ARTHAS_MCP_CLIENT_SERVER_URL");
    
    // 2. 创建工具提供者
    DefaultToolCallbackProvider toolProvider = new DefaultToolCallbackProvider();
    
    // 3. 创建并配置客户端
    ArthasMcpClient client = ArthasMcpClient.create(serverUrl)
        .authToken(authToken)
        .reconnectEnabled(true)
        .heartbeatEnabled(true)
        .toolCallbackProvider(toolProvider)
        .build();
    
    // 4. 启动客户端
    client.start()
        .thenRun(() -> logger.info("Connected!"))
        .exceptionally(ex -> { logger.error("Failed", ex); return null; });
    
    // 5. 添加 Shutdown Hook
    Runtime.getRuntime().addShutdownHook(new Thread(() -> {
        client.stop().join();
    }));
    
    // 6. 保持运行
    Thread.currentThread().join();
}
```

### 5.9 组件交互时序图

#### 5.9.1 启动连接时序

```
ArthasMcpClient     McpHttpClient    McpClientProtocolHandler    Server
       │                   │                    │                   │
       │ start()           │                    │                   │
       ├──────────────────>│                    │                   │
       │                   │ init()             │                   │
       │                   ├───────>            │                   │
       │                   │                    │                   │
       │                   │ connectSse()       │                   │
       │                   ├────────────────────────────────────────>
       │                   │           GET /mcp (Accept: text/event-stream)
       │                   │<────────────────────────────────────────
       │                   │           200 OK (Mcp-Session-Id: xxx)
       │                   │                    │                   │
       │                   │                    │ sendInitialize()  │
       │                   │<───────────────────┤                   │
       │                   │                    │                   │
       │                   │ POST /mcp          │                   │
       │                   ├────────────────────────────────────────>
       │                   │           {initialize request}          
       │                   │<────────────────────────────────────────
       │                   │           {initialize response}         
       │                   │                    │                   │
       │                   │                    │ sendInitialized() │
       │                   │<───────────────────┤                   │
       │                   │                    │                   │
       │                   │ POST /mcp          │                   │
       │                   ├────────────────────────────────────────>
       │                   │        {initialized notification}       
       │                   │<────────────────────────────────────────
       │                   │                202 Accepted             
       │                   │                    │                   │
       │ startHeartbeat()  │                    │                   │
       ├──────────────────>│                    │                   │
       │                   │                    │                   │
       │ 状态: CONNECTED   │                    │                   │
       │                   │                    │                   │
```

#### 5.9.2 工具调用时序

```
Server                McpHttpClient    McpClientProtocolHandler    ToolCallback
   │                        │                    │                      │
   │  SSE: tools/call       │                    │                      │
   │─────────────────────────>                   │                      │
   │  {name:"thread",args:{}}│                   │                      │
   │                        │ messageHandler()   │                      │
   │                        ├───────────────────>│                      │
   │                        │                    │                      │
   │                        │                    │ handleToolsCall()    │
   │                        │                    ├─────────────────────>│
   │                        │                    │                      │
   │                        │                    │     call(input, ctx) │
   │                        │                    │<─────────────────────┤
   │                        │                    │     result (JSON)    │
   │                        │                    │                      │
   │                        │  sendResponse()    │                      │
   │                        │<───────────────────┤                      │
   │                        │                    │                      │
   │  POST /mcp             │                    │                      │
   │<─────────────────────────                   │                      │
   │  {id:x, result:{...}}  │                    │                      │
   │                        │                    │                      │
   │  202 Accepted          │                    │                      │
   │─────────────────────────>                   │                      │
   │                        │                    │                      │
```

---

## 6. 总结

### 6.1 方案优势

1. ✅ **与原有架构一致**: 使用相同的 HTTP/SSE 协议
2. ✅ **代码复用**: 复用 McpSchema、ToolCallback、Netty 等组件
3. ✅ **安全性提升**: 内网服务不暴露端口
4. ✅ **部署简化**: 无需配置防火墙、端口映射
5. ✅ **调试友好**: 纯 HTTP 协议，易于抓包调试

### 6.2 技术亮点

1. **反向连接**: 创新的架构设计，解决内网穿透问题
2. **协议复用**: 最大化复用现有代码和组件
3. **稳定可靠**: 完善的重连和心跳机制
4. **安全加固**: 支持 Token 认证和 HTTPS

### 6.3 后续优化

1. **多服务端支持**: 支持连接到多个管控平台
2. **消息压缩**: 支持 gzip 压缩
3. **流式响应**: 支持大数据量的流式传输
4. **监控指标**: 集成 Prometheus 指标上报

---

## 附录

### A. 参考资料

- [MCP 协议规范](https://spec.modelcontextprotocol.io/)
- [Server-Sent Events 规范](https://html.spec.whatwg.org/multipage/server-sent-events.html)
- [Netty HTTP Client 文档](https://netty.io/wiki/user-guide-for-4.x.html)

### B. 术语表

| 术语 | 说明 |
|------|------|
| MCP | Model Context Protocol，模型上下文协议 |
| SSE | Server-Sent Events，服务端推送事件 |
| JSON-RPC | 基于 JSON 的远程过程调用协议 |
| 指数退避 | Exponential Backoff，重试延迟指数增长 |

### C. 变更历史

| 版本 | 日期 | 作者 | 变更说明 |
|------|------|------|----------|
| v1.0 | 2026-01-16 | Arthas Team | 初始版本（WebSocket 设计） |
| v2.0 | 2026-01-17 | Arthas Team | 改为 HTTP/SSE 模式实现 |
| v3.0 | 2026-01-19 | Arthas Team | 添加代码详细设计说明、集成到 Arthas 主启动流程 |

---

**文档结束**
