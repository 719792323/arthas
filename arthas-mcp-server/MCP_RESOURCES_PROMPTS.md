# MCP 协议中的 Resources 和 Prompts 功能说明

> **写在前面**：本文档通过 Arthas 的实际使用场景，用大白话解释 Resources 和 Prompts 到底是什么、怎么用、有什么用。

## 目录

- [一句话总结](#一句话总结)
- [为什么需要 Resources 和 Prompts](#为什么需要-resources-和-prompts)
- [Resources（资源）- 查数据](#resources资源--查数据)
  - [实际场景 1：查看 JVM 内存](#实际场景-1查看-jvm-内存)
  - [实际场景 2：查看线程快照](#实际场景-2查看线程快照)
  - [实际场景 3：查看命令历史](#实际场景-3查看命令历史)
  - [Resources 技术细节](#resources-技术细节)
- [Prompts（提示词）- 教智能体怎么干活](#prompts提示词--教智能体怎么干活)
  - [实际场景 1：诊断线程死锁](#实际场景-1诊断线程死锁)
  - [实际场景 2：分析内存泄漏](#实际场景-2分析内存泄漏)
  - [实际场景 3：性能优化建议](#实际场景-3性能优化建议)
  - [Prompts 技术细节](#prompts-技术细节)
- [三者对比：Tools vs Resources vs Prompts](#三者对比tools-vs-resources-vs-prompts)
- [在 Arthas 中如何实现](#在-arthas-中如何实现)

---

## 一句话总结

- **Tools（工具）**：让智能体**执行命令**，比如 `thread -b` 查死锁
- **Resources（资源）**：让智能体**查询数据**，比如查看当前 JVM 内存使用情况
- **Prompts（提示词）**：**教智能体怎么干活**，比如告诉它诊断死锁的标准流程

---

## 为什么需要 Resources 和 Prompts

### 问题场景

假设用户问智能体："我的应用内存占用很高，帮我看看是不是有内存泄漏？"

#### 只有 Tools 的情况（当前实现）

```
用户：我的应用内存占用很高，帮我看看是不是有内存泄漏？

智能体：好的，我来执行一些命令看看
  → 调用 tools/call("dashboard")  // 查看整体情况
  → 调用 tools/call("memory")     // 查看内存详情
  → 调用 tools/call("heapdump")   // 生成堆转储

问题：
1. 智能体不知道历史数据，无法对比"内存是不是一直在涨"
2. 智能体不知道标准的诊断流程，可能遗漏关键步骤
3. 每次都要执行命令，效率低
```

#### 有 Resources + Prompts 的情况

```
用户：我的应用内存占用很高，帮我看看是不是有内存泄漏？

智能体：
1. 先查 Prompt "diagnose-memory-leak" 获取诊断流程
   → 知道了标准流程：先看趋势 → 再看堆内存 → 最后分析对象

2. 调用 Resource "metrics://jvm/memory/history" 查看历史趋势
   → 发现：过去 1 小时内存从 500MB 涨到 1.5GB，持续上涨！

3. 调用 Tool "memory" 查看当前详情
   → 发现：堆内存使用率 85%

4. 调用 Tool "heapdump" 生成堆转储分析
   → 发现：某个 List 对象占用了 800MB

智能体回答：
"检测到内存泄漏！过去 1 小时内存持续上涨，从 500MB 涨到 1.5GB。
 经分析，com.example.Cache 类中的 List 对象持续增长，建议检查缓存清理逻辑。"
```

**核心区别**：
- **Resources** 提供历史数据和监控指标，让智能体能"看到趋势"
- **Prompts** 提供诊断流程，让智能体知道"该怎么做"

---

## Resources（资源）- 查数据

### 一句话解释

**Resources 就是让智能体能"查询"各种数据，而不用每次都执行命令。**

比如：
- 查看 JVM 内存使用情况 → 不用执行 `dashboard` 命令，直接读取 `metrics://jvm/memory`
- 查看过去 1 小时的 CPU 趋势 → 不用反复执行命令，直接读取 `metrics://jvm/cpu/history`
- 查看最近执行的命令 → 直接读取 `log://arthas/command-history`

**核心特点**：
- ✅ **只读**：只能查，不能改
- ✅ **快速**：不需要执行命令，直接返回数据
- ✅ **历史数据**：可以提供趋势、历史记录等
- ✅ **URI 标识**：每个资源有唯一的地址，像网址一样

### 实际场景 1：查看 JVM 内存

**用户问题**："我想看看当前 JVM 的内存使用情况"

#### 传统方式（只用 Tools）

```
智能体：好的，我来执行 dashboard 命令
  → 调用 tools/call("dashboard")
  → 返回：一大堆信息，包括 CPU、内存、线程等
  → 智能体需要从中提取内存信息

问题：
1. 每次都要执行命令，慢
2. 返回信息太多，需要解析
3. 无法看到历史趋势
```

#### 使用 Resources

```
智能体：我来查询内存资源
  → 调用 resources/read("metrics://jvm/memory")
  → 直接返回：
  {
    "heap": {
      "used": "512MB",
      "max": "2GB",
      "usage": "25%"
    },
    "nonHeap": {
      "used": "128MB",
      "max": "512MB"
    }
  }

智能体回答："当前 JVM 堆内存使用了 512MB，占总内存 2GB 的 25%"
```

**优势**：
- ✅ 快速：不需要执行命令
- ✅ 精准：只返回需要的数据
- ✅ 结构化：JSON 格式，易于解析

---

### 实际场景 2：查看线程快照

**用户问题**："帮我看看线程 123 的详细信息"

#### 使用 Resource Template（参数化资源）

```
1. Arthas 注册资源模板：
   ResourceTemplate(
     uriTemplate: "thread://{threadId}",
     name: "线程详情",
     description: "查看指定线程的详细信息"
   )

2. 智能体调用：
   resources/read("thread://123")
   
3. 返回线程 123 的详细信息：
   {
     "threadId": 123,
     "threadName": "http-nio-8080-exec-5",
     "state": "RUNNABLE",
     "cpuUsage": "15%",
     "stackTrace": [...]
   }
```

**为什么用 Resource 而不是 Tool？**
- Resource：查询已有的数据（线程快照）
- Tool：执行操作（比如 `thread -b` 查找死锁）

---

### 实际场景 3：查看命令历史

**用户问题**："我刚才执行了什么命令来着？"

```
智能体：
  → 调用 resources/read("log://arthas/command-history")
  → 返回：
  [
    {"time": "10:30:15", "command": "dashboard"},
    {"time": "10:31:20", "command": "thread -b"},
    {"time": "10:32:05", "command": "jvm"}
  ]

智能体回答："您最近执行了这些命令：
  10:30:15 - dashboard
  10:31:20 - thread -b
  10:32:05 - jvm"
```

### Resources 技术细节

#### Arthas 可以暴露哪些 Resources？

```java
// 1. JVM 监控指标
Resource("metrics://jvm/memory", "JVM 内存指标")
Resource("metrics://jvm/cpu", "JVM CPU 使用率")
Resource("metrics://jvm/threads", "JVM 线程统计")
Resource("metrics://jvm/gc", "GC 统计信息")

// 2. Arthas 日志和历史
Resource("log://arthas/command-history", "命令历史")
Resource("log://arthas/execution-log", "执行日志")

// 3. 线程快照（参数化）
ResourceTemplate("thread://{threadId}", "线程快照")
ResourceTemplate("thread://{threadId}/stack", "线程堆栈")

// 4. 配置信息
Resource("config://arthas/settings", "Arthas 配置")
Resource("config://jvm/properties", "JVM 系统属性")

// 5. 类信息（参数化）
ResourceTemplate("class://{className}", "类详情")
ResourceTemplate("class://{className}/methods", "类方法列表")
```

#### API 调用示例

**1. 列出所有可用资源**
```json
请求：resources/list
响应：
{
  "resources": [
    {"uri": "metrics://jvm/memory", "name": "JVM 内存指标"},
    {"uri": "log://arthas/command-history", "name": "命令历史"}
  ]
}
```

**2. 读取具体资源**
```json
请求：resources/read("metrics://jvm/memory")
响应：
{
  "contents": [{
    "uri": "metrics://jvm/memory",
    "text": "{\"heap\": {\"used\": \"512MB\"}}"
  }]
}
```

---

## Prompts（提示词）- 教智能体怎么干活

### 一句话解释

**Prompts 就是给智能体提供“作业指导”，告诉它遇到某个问题该怎么做。**

比如：
- 诊断线程死锁的标准流程
- 分析内存泄漏的步骤
- 性能优化的最佳实践

**核心特点**：
- ✅ **模板化**：预先定义好的对话结构
- ✅ **参数化**：可以根据具体情况调整
- ✅ **多轮对话**：包含 system、user、assistant 多个角色
- ✅ **知识传递**：把专家经验固化下来

---

### 实际场景 1：诊断线程死锁

**用户问题**："帮我分析一下是否有线程死锁"

#### 没有 Prompt 的情况

```
智能体：好的，我来执行 thread 命令
  → 调用 tools/call("thread")
  → 返回：100 个线程的信息
  → 智能体：呵，这么多线程，我该怎么分析？

问题：
1. 智能体不知道死锁诊断的标准流程
2. 可能遗漏关键步骤（比如忘记用 -b 参数）
3. 分析不专业，可能误判
```

#### 使用 Prompt

**1. Arthas 注册 Prompt**

```java
Prompt(
  name: "diagnose-thread-deadlock",
  description: "诊断线程死锁问题",
  arguments: [
    {name: "threadId", description: "线程 ID（可选）", required: false}
  ]
)
```

**2. 智能体调用 Prompt**

```
智能体：我先查一下死锁诊断的标准流程
  → 调用 prompts/get("diagnose-thread-deadlock")
  → 返回提示词模板：
  {
    "messages": [
      {
        "role": "system",
        "content": "你是一个 Java 性能分析专家，擅长使用 Arthas 诊断线程问题。"
      },
      {
        "role": "user",
        "content": "请按以下步骤诊断线程死锁：\n
                    1. 使用 'thread -b' 命令查找阻塞的线程\n
                    2. 如果发现死锁，分析涉及的线程和锁\n
                    3. 使用 'thread <threadId>' 查看具体线程的堆栈\n
                    4. 给出解决建议"
      }
    ]
  }
```

**3. 智能体按照 Prompt 执行**

```
智能体：好的，我按照标准流程来

步骤 1：→ 调用 tools/call("thread", {"-b": true})
       → 返回：发现线程 123 和 456 相互阻塞

步骤 2：分析死锁
       线程 123 持有锁 A，等待锁 B
       线程 456 持有锁 B，等待锁 A

步骤 3：→ 调用 tools/call("thread", {"id": 123})
       → 查看线程 123 的堆栈

步骤 4：给出建议

智能体回答：
"检测到线程死锁！
 线程 123 (http-nio-8080-exec-1) 持有 com.example.LockA，等待 com.example.LockB
 线程 456 (http-nio-8080-exec-2) 持有 com.example.LockB，等待 com.example.LockA
 
 建议：
 1. 检查 com.example.Service 类中的锁获取顺序
 2. 确保所有线程按相同顺序获取锁
 3. 考虑使用 java.util.concurrent 包中的高级锁"
```

**优势**：
- ✅ 有条理：按照标准流程执行
- ✅ 不遗漏：不会忘记关键步骤
- ✅ 专业：分析结果更准确

---

### 实际场景 2：分析内存泄漏

**用户问题**："应用内存一直在涨，怎么办？"

#### Prompt 模板

```java
Prompt(
  name: "diagnose-memory-leak",
  description: "诊断内存泄漏问题",
  messages: [
    {
      role: "system",
      content: "你是 Java 内存分析专家"
    },
    {
      role: "user",
      content: "请按以下步骤诊断内存泄漏：\n
                1. 使用 Resource 'metrics://jvm/memory/history' 查看内存趋势\n
                2. 如果内存持续上涨，使用 'memory' 命令查看详情\n
                3. 使用 'heapdump' 生成堆转储\n
                4. 分析占用内存最多的对象\n
                5. 给出优化建议"
    }
  ]
)
```

#### 智能体执行流程

```
1. 查看历史趋势
   → resources/read("metrics://jvm/memory/history")
   → 发现：过去 1 小时内存从 500MB 涨到 1.5GB

2. 查看当前详情
   → tools/call("memory")
   → 发现：堆内存使用率 85%

3. 生成堆转储
   → tools/call("heapdump", {"live": true})
   → 生成文件：/tmp/heapdump.hprof

4. 分析结果
   → 发现：com.example.Cache 类的 List 对象占用 800MB

5. 给出建议
   "检测到内存泄漏！com.example.Cache 类中的 List 持续增长。
    建议：
    1. 检查缓存清理逻辑
    2. 设置缓存大小限制
    3. 使用 WeakReference 或 SoftReference"
```

---

### 实际场景 3：性能优化建议

**用户问题**："应用响应慢，给些优化建议"

#### Prompt 模板

```java
Prompt(
  name: "performance-optimization",
  description: "性能优化建议",
  arguments: [
    {name: "metric", description: "关注的指标：cpu/memory/response-time", required: false}
  ],
  messages: [
    {
      role: "system",
      content: "你是 Java 性能优化专家"
    },
    {
      role: "user",
      content: "请按以下步骤分析性能问题：\n
                1. 使用 'dashboard' 查看整体情况\n
                2. 如果 CPU 高，使用 'thread -n 10' 查看热点线程\n
                3. 如果内存高，使用 'memory' 查看内存分布\n
                4. 使用 'trace' 追踪慢方法\n
                5. 给出针对性的优化建议"
    }
  ]
)
```

**优势**：
- ✅ 系统化：按照专家经验执行
- ✅ 全面：不会遗漏关键指标
- ✅ 针对性：根据具体问题给建议

### Prompts 技术细节

#### Arthas 可以提供哪些 Prompts？

```java
// 1. 问题诊断类
Prompt("diagnose-cpu-high", "诊断 CPU 占用高的问题")
Prompt("diagnose-memory-leak", "诊断内存泄漏问题")
Prompt("diagnose-thread-deadlock", "诊断线程死锁问题")
Prompt("diagnose-slow-response", "诊断响应慢问题")

// 2. 性能分析类
Prompt("analyze-slow-method", "分析慢方法")
Prompt("analyze-gc-issue", "分析 GC 问题")
Prompt("analyze-thread-pool", "分析线程池问题")

// 3. 使用指南类
Prompt("guide-first-time", "Arthas 新手指南")
Prompt("guide-best-practices", "Arthas 最佳实践")
Prompt("guide-production-safety", "生产环境安全使用指南")

// 4. 报告生成类
Prompt("report-performance-analysis", "生成性能分析报告")
Prompt("report-health-check", "生成健康检查报告")
```

#### API 调用示例

**1. 列出所有可用提示词**
```json
请求：prompts/list
响应：
{
  "prompts": [
    {
      "name": "diagnose-thread-deadlock",
      "description": "诊断线程死锁问题",
      "arguments": [{"name": "threadId", "required": false}]
    }
  ]
}
```

**2. 获取具体提示词**
```json
请求：prompts/get("diagnose-thread-deadlock")
响应：
{
  "messages": [
    {"role": "system", "content": "你是 Java 性能分析专家..."},
    {"role": "user", "content": "请按以下步骤诊断..."}
  ]
}
```

---

## 三者对比：Tools vs Resources vs Prompts

### 用一个完整的例子说明

**场景**：用户问"我的应用 CPU 占用很高，怎么办？"

#### 只有 Tools 的情况

```
智能体：
  → 执行 tools/call("dashboard")
  → 执行 tools/call("thread", {"-n": 10})
  → 给出答案

问题：
- 不知道历史 CPU 趋势
- 不知道诊断流程
- 可能遗漏关键步骤
```

#### 有 Tools + Resources + Prompts

```
智能体：

1. 获取诊断流程（Prompt）
   → prompts/get("diagnose-cpu-high")
   → 知道了标准流程：先看趋势 → 再看热点线程 → 最后分析方法

2. 查看历史趋势（Resource）
   → resources/read("metrics://jvm/cpu/history")
   → 发现：过去 1 小时 CPU 从 20% 涨到 90%

3. 查看当前情况（Tool）
   → tools/call("dashboard")
   → 发现：当前 CPU 90%

4. 查找热点线程（Tool）
   → tools/call("thread", {"-n": 10})
   → 发现：线程 "task-executor-5" 占用 CPU 60%

5. 分析具体方法（Tool）
   → tools/call("trace", {"class": "com.example.Service", "method": "process"})
   → 发现：processData() 方法耗时 500ms

6. 给出专业建议
   "检测到 CPU 占用高的原因：
    - 过去 1 小时 CPU 从 20% 涨到 90%
    - 热点线程：task-executor-5
    - 热点方法：com.example.Service.processData()
    - 该方法平均耗时 500ms
    
    建议：
    1. 优化 processData() 方法的算法
    2. 考虑增加缓存
    3. 调整线程池大小"
```

### 对比表

| 特性 | Tools（工具） | Resources（资源） | Prompts（提示词） |
|------|--------------|------------------|------------------|
| **用途** | 执行操作 | 查询数据 | 提供指导 |
| **读写** | 可读可写 | 只读 | 只读 |
| **副作用** | 有副作用 | 无副作用 | 无副作用 |
| **返回值** | 执行结果 | 数据内容 | 消息模板 |
| **使用时机** | 需要执行命令时 | 需要查询数据时 | 需要任务指导时 |
| **Arthas 例子** | `thread -b` 查死锁 | 查看 JVM 内存指标 | 死锁诊断流程 |
| **类比** | 像“执行命令” | 像“查数据库” | 像“操作手册” |

### 三者协同工作

```
场景：智能体诊断性能问题

1. Prompt 提供诊断流程
   prompts/get("diagnose-performance")
   → 返回：先看 CPU，再看内存，最后看线程

2. Resource 查看监控数据
   resources/read("metrics://jvm/cpu")
   → 返回：CPU 使用率 85%

3. Tool 执行深入分析
   tools/call("thread", {"n": 10})
   → 返回：CPU 占用最高的 10 个线程

4. Resource 查看历史数据
   resources/read("log://performance/history")
   → 返回：过去 1 小时的性能趋势

5. 综合分析并给出结论
```

---

## 在 Arthas 中如何实现

### 当前实现状态

**Arthas MCP Server 已经实现了基础框架**：

```java
// 在 McpNettyServer.java 中
if (serverCapabilities.getResources() != null) {
    requestHandlers.put("resources/list", resourcesListRequestHandler());
    requestHandlers.put("resources/read", resourcesReadRequestHandler());
    requestHandlers.put("resources/templates/list", resourceTemplateListRequestHandler());
}

if (serverCapabilities.getPrompts() != null) {
    requestHandlers.put("prompts/list", promptsListRequestHandler());
    requestHandlers.put("prompts/get", promptsGetRequestHandler());
}
```

### 对于反向注册客户端（ArthasMcpClient）

#### 是否需要实现？

| 功能 | 优先级 | 理由 | 建议 |
|------|--------|------|------|
| **Tools** | ✅ P0 必需 | 核心功能，执行 Arthas 命令 | 已实现 |
| **Resources** | ⚠️ P1 推荐 | 提供监控数据、历史趋势 | **建议实现** |
| **Prompts** | 💡 P2 可选 | 提供诊断流程指导 | 看需求 |

#### 为什么建议实现 Resources？

**场景 1：管控平台需要查看监控数据**
```
管控平台的智能体：
  → 调用 resources/read("metrics://jvm/memory")
  → 快速获取内存数据，不需要执行命令
  → 可以定期轮询，构建监控大盘
```

**场景 2：智能体需要历史数据做对比**
```
用户：内存是不是在涨？
智能体：
  → 调用 resources/read("metrics://jvm/memory/history")
  → 看到过去 1 小时的趋势
  → 给出准确判断："是的，过去 1 小时从 500MB 涨到 1.5GB"
```

**场景 3：减少命令执行次数**
```
传统方式：
  → 每次都执行 dashboard 命令
  → 解析输出
  → 提取需要的数据

使用 Resources：
  → 直接读取 metrics://jvm/memory
  → 返回结构化 JSON
  → 快速、准确
```

### 实现建议

#### 1. 优先实现的 Resources

```java
// 高优先级：JVM 监控指标
Resource("metrics://jvm/memory", "JVM 内存指标")
  → 返回：堆内存、非堆内存、使用率等

Resource("metrics://jvm/cpu", "JVM CPU 使用率")
  → 返回：当前 CPU 使用率

Resource("metrics://jvm/threads", "JVM 线程统计")
  → 返回：线程总数、活跃线程数、死锁线程数

Resource("metrics://jvm/gc", "GC 统计信息")
  → 返回：GC 次数、GC 耗时等

// 中优先级：历史数据
Resource("metrics://jvm/memory/history", "内存历史趋势")
  → 返回：过去 N 小时的内存使用趋势

Resource("log://arthas/command-history", "命令历史")
  → 返回：最近执行的命令列表

// 低优先级：配置信息
Resource("config://arthas/settings", "Arthas 配置")
  → 返回：当前 Arthas 的配置信息

Resource("config://jvm/properties", "JVM 系统属性")
  → 返回：JVM 的系统属性
```

#### 2. 可选实现的 Prompts

```java
// 如果管控平台的智能体需要诊断指导，可以实现：

Prompt("diagnose-thread-deadlock", "诊断线程死锁")
Prompt("diagnose-memory-leak", "诊断内存泄漏")
Prompt("diagnose-cpu-high", "诊断 CPU 占用高")
Prompt("guide-best-practices", "Arthas 最佳实践")
```

### 实现示例

#### 在 ArthasMcpClient 中添加 Resources 支持

```java
// 1. 在初始化时声明支持 Resources
ClientCapabilities clientCapabilities = new ClientCapabilities();
clientCapabilities.setResources(new ResourcesCapability(true));

// 2. 注册 Resource 处理器
public void registerResources() {
    // JVM 内存指标
    addResource(new ResourceSpecification(
        new Resource("metrics://jvm/memory", "JVM 内存指标", "application/json"),
        (exchange, request) -> {
            // 调用 Arthas 的 memory 命令获取数据
            String result = executeArthasCommand("memory");
            // 解析并返回 JSON 格式
            return CompletableFuture.completedFuture(
                new ReadResourceResult(List.of(
                    new ResourceContents(request.getUri(), "application/json", parseMemoryData(result))
                ))
            );
        }
    ));
    
    // 命令历史
    addResource(new ResourceSpecification(
        new Resource("log://arthas/command-history", "命令历史", "application/json"),
        (exchange, request) -> {
            // 从本地缓存获取命令历史
            List<CommandHistory> history = getCommandHistory();
            return CompletableFuture.completedFuture(
                new ReadResourceResult(List.of(
                    new ResourceContents(request.getUri(), "application/json", toJson(history))
                ))
            );
        }
    ));
}
```

### 总结

#### 核心要点

1. **Tools（工具）** = 执行命令
   - 必须实现 ✅
   - 用于执行 Arthas 命令
   - 有副作用

2. **Resources（资源）** = 查询数据
   - 建议实现 ⚠️
   - 用于提供监控数据、历史趋势
   - 只读、无副作用
   - **能显著提升智能体的诊断能力**

3. **Prompts（提示词）** = 任务指导
   - 可选实现 💡
   - 用于提供诊断流程、最佳实践
   - 只读、无副作用
   - 适合有经验沉淀需求的场景

#### 实现优先级

```
第一阶段（已完成）：
  ✅ Tools - 工具调用
  ✅ Initialize - 初始化
  ✅ Ping - 心跳

第二阶段（建议实现）：
  ⚠️ Resources - 资源访问
    优先实现：
    - metrics://jvm/memory
    - metrics://jvm/cpu
    - metrics://jvm/threads
    - log://arthas/command-history

第三阶段（可选）：
  💡 Prompts - 提示词
    - diagnose-thread-deadlock
    - diagnose-memory-leak
    - diagnose-cpu-high
```

#### 为什么这样设计？

- **Tools**：让智能体能"动手"（执行命令）
- **Resources**：让智能体能"看到"（查询数据、看趋势）
- **Prompts**：让智能体"会干活"（知道标准流程）

三者结合，智能体才能真正成为一个专业的 Java 性能诊断助手！
