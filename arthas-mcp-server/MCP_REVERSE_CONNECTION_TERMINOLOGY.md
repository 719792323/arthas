# Arthas MCP 反向连接模式 - 术语说明

## 📋 文档信息

- **创建日期**: 2026-01-19
- **版本**: v1.0
- **作者**: Arthas Team

---

## 1. 核心问题

### 1.1 命名困惑

在实现 Arthas 反向连接功能时，代码中使用了 `ArthasMcpClient` 这个类名，这可能会引起混淆：

**❓ 问题**：Arthas 在反向连接模式下，还叫 MCP Server 吗？

**✅ 答案**：**是的！Arthas 仍然是 MCP Server！**

---

## 2. 术语澄清

### 2.1 两个维度的区分

在 MCP 协议中，需要区分两个不同的维度：

#### 维度 1：连接方向（Connection Direction）

| 术语 | 说明 | 行为 |
|------|------|------|
| **Server 模式** | 被动等待连接 | Listen on port |
| **Client 模式** | 主动发起连接 | Connect to server |

#### 维度 2：能力提供（Capability Provider）

| 术语 | 说明 | 提供的能力 |
|------|------|-----------|
| **MCP Server** | 提供工具/资源/提示词 | Tools, Prompts, Resources |
| **MCP Client** | 使用工具/资源/提示词 | 调用 tools/call, prompts/get 等 |

### 2.2 Arthas 的角色

```
┌─────────────────────────────────────────────────────────────────┐
│                        Arthas 的双重身份                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  连接方向维度：                                                  │
│  ┌──────────────────┐         ┌──────────────────┐             │
│  │  传统模式         │         │  反向连接模式     │             │
│  │  (Server Mode)   │         │  (Client Mode)   │             │
│  │                  │         │                  │             │
│  │  被动等待连接     │         │  主动发起连接     │             │
│  │  Listen :8563    │         │  Connect to URL  │             │
│  └──────────────────┘         └──────────────────┘             │
│                                                                 │
│  能力提供维度：                                                  │
│  ┌─────────────────────────────────────────────┐               │
│  │            两种模式都是 MCP Server            │               │
│  │                                             │               │
│  │  ✅ 提供 Tools (thread, jvm, memory...)     │               │
│  │  ✅ 响应 tools/list 请求                    │               │
│  │  ✅ 响应 tools/call 请求                    │               │
│  │  ✅ 返回工具执行结果                         │               │
│  └─────────────────────────────────────────────┘               │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 2.3 关键认知

**Arthas 的反向连接模式：**
- ✅ **在连接方向上是 Client**（主动发起连接）
- ✅ **在能力提供上是 Server**（提供 Tools）
- ✅ **仍然是 MCP Server**（符合 MCP Server 规范）

---

## 3. 正确的术语

### 3.1 官方术语

| 英文 | 中文 | 说明 |
|------|------|------|
| **Reverse Connection MCP Server** | 反向连接 MCP 服务器 | 最准确的术语 |
| **Client-Initiated MCP Server** | 客户端发起的 MCP 服务器 | 强调连接发起方 |
| **Outbound MCP Server** | 出站 MCP 服务器 | 强调连接方向 |

### 3.2 Arthas 特定术语

| 术语 | 使用场景 |
|------|----------|
| **Arthas MCP Server (Reverse Mode)** | 正式文档、配置说明 |
| **Arthas 反向连接模式** | 用户文档、教程 |
| **Arthas MCP 反向连接服务器** | 技术文档 |

### 3.3 不推荐的术语

| 术语 | 问题 | 为什么不推荐 |
|------|------|-------------|
| **Arthas MCP Client** | ❌ 容易混淆 | 暗示 Arthas 是工具使用者，而非提供者 |
| **Arthas 客户端模式** | ❌ 不准确 | 忽略了 Arthas 仍然提供服务的本质 |

---

## 4. 架构对比

### 4.1 传统 MCP 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    传统 MCP 架构                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   MCP Client (Cherry Studio)                                    │
│         │                                                       │
│         │ 主动连接                                               │
│         │ http://localhost:8563/mcp                            │
│         ▼                                                       │
│   MCP Server (Arthas)                                           │
│         │                                                       │
│         └─ 提供 Tools                                           │
│                                                                 │
│   角色：Arthas 是 MCP Server                                    │
│   连接：被动等待（Listen on :8563）                              │
│   能力：提供诊断工具                                             │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 反向连接 MCP 架构

```
┌─────────────────────────────────────────────────────────────────┐
│              反向连接 MCP 架构（Arthas 的实现）                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   MCP Server (Remote Agent)                                     │
│         ▲                                                       │
│         │ 反向连接                                               │
│         │ http://agent.com/mcp                                 │
│         │                                                       │
│   MCP Server (Arthas) ← 注意：仍然是 Server！                   │
│         │                                                       │
│         └─ 提供 Tools                                           │
│                                                                 │
│   角色：Arthas 仍然是 MCP Server（提供能力）                     │
│   连接：主动发起（Connect to Remote Agent）                      │
│   能力：提供诊断工具（与传统模式相同）                            │
└─────────────────────────────────────────────────────────────────┘
```

### 4.3 关键区别

| 维度 | 传统模式 | 反向连接模式 |
|------|----------|-------------|
| **连接方向** | 被动等待 | 主动发起 |
| **网络要求** | 需要开放端口 | 不需要开放端口 |
| **MCP 角色** | ✅ MCP Server | ✅ MCP Server（相同） |
| **提供能力** | ✅ Tools | ✅ Tools（相同） |
| **协议实现** | ✅ MCP 协议 | ✅ MCP 协议（相同） |

---

## 5. 业界类比

这种模式在其他协议中也很常见，可以帮助理解：

### 5.1 SSH 反向隧道

```bash
# 正向连接：客户端连接服务器
ssh user@server

# 反向连接：服务器主动连接网关
ssh -R 8080:localhost:80 user@gateway

# 但 SSH 服务仍然是"服务"，只是连接方向相反
```

**类比**：
- SSH 服务器在反向隧道中仍然是"服务器"
- Arthas 在反向连接中仍然是"MCP Server"

### 5.2 WebSocket 反向代理

```
正向：Browser → WebSocket Server
反向：WebSocket Server → Gateway
      (Server 主动连接 Gateway，但仍然提供服务)
```

### 5.3 IoT 设备连接

```
传统：云平台 → IoT 设备（需要设备开放端口）
反向：IoT 设备 → 云平台（设备主动连接，无需开放端口）

但 IoT 设备仍然是"服务提供者"（提供传感器数据）
```

---

## 6. 代码命名说明

### 6.1 当前命名

```java
// 类名
public class ArthasMcpClient {
    // ...
}

// 配置
public class McpClientConfig {
    // ...
}

// HTTP 客户端
public class McpHttpClient {
    // ...
}
```

### 6.2 命名原因

**为什么使用 "Client" 命名？**

1. **强调连接方向**：这些类负责"主动发起连接"，在网络层面是 HTTP Client
2. **代码清晰性**：与 `McpNettyServer`（被动监听）形成对比
3. **实现细节**：内部使用 Netty Bootstrap（客户端）而非 ServerBootstrap

### 6.3 更准确的命名建议

如果要避免混淆，可以考虑以下命名：

```java
// 方案 1：强调反向连接
public class ArthasMcpReverseConnectionServer {
    // ...
}

// 方案 2：强调连接模式
public class ArthasMcpServerClientMode {
    // ...
}

// 方案 3：强调出站连接
public class ArthasMcpOutboundServer {
    // ...
}
```

**但是**：当前的 `ArthasMcpClient` 命名在代码层面是合理的，只要在文档中明确说明其 MCP Server 的本质即可。

---

## 7. 对外宣传建议

### 7.1 用户文档

**推荐表述**：

```
Arthas MCP 支持两种工作模式：

1. **传统模式**（Server Mode）
   - Arthas 监听端口，等待客户端连接
   - 适用于本地开发、同网段访问

2. **反向连接模式**（Reverse Connection Mode）
   - Arthas 主动连接到远程管控平台
   - 适用于内网穿透、统一管控
   - 无需开放端口，更加安全

两种模式下，Arthas 都是 MCP Server，提供相同的诊断工具能力。
```

### 7.2 技术文档

**推荐表述**：

```
Arthas MCP Server - Reverse Connection Mode

Arthas implements a reverse connection mode where it acts as an 
HTTP client to initiate the connection, but still functions as 
an MCP Server providing diagnostic tools.

Architecture:
- Connection Direction: Client (outbound connection)
- Protocol Role: Server (provides tools)
- Implementation: HTTP/SSE with Netty Bootstrap
```

### 7.3 配置说明

**推荐表述**：

```bash
# 启用反向连接模式
# Arthas 将主动连接到指定的管控平台，但仍然作为 MCP Server 提供工具
export ARTHAS_MCP_CLIENT_SERVER_URL=http://your-server.com:8080/mcp

# 注意：虽然环境变量名包含 "CLIENT"，但 Arthas 仍然是 MCP Server
# "CLIENT" 仅表示连接方向（主动发起），而非协议角色
```

---

## 8. 常见问题 FAQ

### Q1: Arthas 在反向连接模式下还是 MCP Server 吗？

**A**: 是的！Arthas 仍然是 MCP Server。

- ✅ 提供 Tools 能力
- ✅ 响应 tools/list 和 tools/call 请求
- ✅ 符合 MCP Server 规范

"反向连接"只是改变了连接方向，不改变协议角色。

### Q2: 为什么代码中叫 ArthasMcpClient？

**A**: 这个命名强调的是**连接方向**（主动发起连接），而非**协议角色**。

- 在网络层面：是 HTTP Client（使用 Netty Bootstrap）
- 在协议层面：是 MCP Server（提供 Tools）

可以理解为："一个使用 HTTP Client 实现的 MCP Server"。

### Q3: 这种模式在 MCP 协议中叫什么？

**A**: 官方术语是 **Reverse Connection MCP Server**（反向连接 MCP 服务器）。

其他类似术语：
- Client-Initiated MCP Server
- Outbound MCP Server

### Q4: 与传统模式有什么区别？

**A**: 

| 维度 | 传统模式 | 反向连接模式 |
|------|----------|-------------|
| 连接方向 | 被动等待 | 主动发起 |
| 端口开放 | 需要 | 不需要 |
| MCP 角色 | Server | Server（相同） |
| 提供能力 | Tools | Tools（相同） |

### Q5: 可以同时启用两种模式吗？

**A**: 可以！Arthas 支持同时启用传统模式和反向连接模式：

```bash
# 同时启用两种模式
export ARTHAS_MCP_CLIENT_SERVER_URL=http://remote-agent.com/mcp
java -jar arthas-boot.jar

# 此时：
# - 本地监听 8563 端口（传统模式）
# - 同时连接到远程管控平台（反向连接模式）
```

### Q6: 哪种模式更好？

**A**: 取决于使用场景：

**传统模式适合**：
- 本地开发调试
- 同网段内访问
- 简单快速的场景

**反向连接模式适合**：
- 内网环境（无法开放端口）
- 统一管控多个实例
- 企业级部署
- 安全性要求高的场景

---

## 9. 总结

### 9.1 核心要点

1. **Arthas 在反向连接模式下仍然是 MCP Server**
2. **"Client" 仅指连接方向，不是协议角色**
3. **正确术语是"反向连接 MCP 服务器"**
4. **两种模式提供相同的工具能力**

### 9.2 记忆口诀

```
连接方向可以反，
协议角色不会变。
主动发起叫 Client，
提供工具是 Server。
```

### 9.3 类比理解

```
就像一个外卖员：
- 传统模式：在店里等顾客上门（被动）
- 反向模式：主动送餐上门（主动）

但无论哪种模式，外卖员的角色都是"服务提供者"，
只是服务方式不同而已。

Arthas 也是如此：
- 传统模式：等待连接
- 反向模式：主动连接

但都是"MCP Server"（服务提供者）。
```

---

## 10. 参考资料

- [MCP 协议规范](https://spec.modelcontextprotocol.io/)
- [Arthas MCP Client 设计文档](./MCP_CLIENT_DESIGN.md)
- [Arthas MCP 协议自动发现文档](./MCP_PROTOCOL_AUTO_DISCOVERY.md)

---

**文档结束**
