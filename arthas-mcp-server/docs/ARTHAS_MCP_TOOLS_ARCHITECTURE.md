# Arthas MCP 工具调用架构设计与实现分析

> **文档版本**: v1.0  
> **创建时间**: 2025-01-21  
> **作者**: Arthas Team  
> **目的**: 深入分析 Arthas MCP 工具系统的设计理念、实现机制和与 MCP 协议的适配方式

---

## 📋 目录

1. [架构概览](#1-架构概览)
2. [工具定义机制](#2-工具定义机制)
3. [工具注册与发现](#3-工具注册与发现)
4. [工具调用流程](#4-工具调用流程)
5. [与 MCP 协议的适配](#5-与-mcp-协议的适配)
6. [工具执行上下文](#6-工具执行上下文)
7. [同步与异步执行](#7-同步与异步执行)
8. [完整调用链路](#8-完整调用链路)
9. [设计亮点](#9-设计亮点)

---

## 1. 架构概览

### 1.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         MCP 客户端 (Cherry Studio)                       │
└────────────────────────────┬────────────────────────────────────────────┘
                             │ JSON-RPC 2.0 over HTTP/SSE
                             │ tools/list, tools/call
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    MCP 协议层 (McpNettyServer)                           │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  handleToolsList()  →  返回所有工具定义                          │   │
│  │  handleToolsCall()  →  调用指定工具                              │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└────────────────────────────┬────────────────────────────────────────────┘
                             │ ToolCallback.call(toolInput, toolContext)
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    工具回调层 (DefaultToolCallback)                      │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  1. 解析 JSON 参数                                                │   │
│  │  2. 验证必填参数                                                  │   │
│  │  3. 类型转换                                                      │   │
│  │  4. 反射调用工具方法                                              │   │
│  │  5. 结果转换为 JSON                                               │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└────────────────────────────┬────────────────────────────────────────────┘
                             │ Method.invoke(toolObject, args)
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    工具实现层 (ThreadTool, JvmTool...)                   │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  @Tool(name="thread", description="...")                         │   │
│  │  public String thread(                                           │   │
│  │      @ToolParam Long threadId,                                   │   │
│  │      @ToolParam Integer topN,                                    │   │
│  │      ToolContext toolContext                                     │   │
│  │  ) {                                                             │   │
│  │      // 构建 Arthas 命令                                          │   │
│  │      // 执行命令                                                  │   │
│  │      // 返回结果                                                  │   │
│  │  }                                                               │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└────────────────────────────┬────────────────────────────────────────────┘
                             │ executeSync() / executeStreamable()
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    Arthas 命令执行层 (ArthasCommandContext)              │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  executeSync()   →  同步执行命令，等待结果                        │   │
│  │  executeAsync()  →  异步执行命令，立即返回                        │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.2 核心组件

| 组件 | 职责 | 关键类 |
|------|------|--------|
| **工具定义** | 声明工具的元数据（名称、描述、参数） | `@Tool`, `@ToolParam`, `ToolDefinition` |
| **工具注册** | 扫描和注册所有工具 | `DefaultToolCallbackProvider` |
| **工具回调** | 执行工具调用的桥梁 | `ToolCallback`, `DefaultToolCallback` |
| **工具实现** | 具体的诊断工具逻辑 | `ThreadTool`, `JvmTool`, `MemoryTool`... |
| **MCP 适配** | 将工具暴露为 MCP 协议 | `McpNettyServer`, `McpClientProtocolHandler` |
| **命令执行** | 执行 Arthas 命令 | `ArthasCommandContext` |

---

## 2. 工具定义机制

### 2.1 注解驱动的工具定义

Arthas MCP 使用 **注解驱动** 的方式定义工具，开发者只需要：
1. 在方法上添加 `@Tool` 注解
2. 在参数上添加 `@ToolParam` 注解
3. 系统自动生成工具定义和 JSON Schema

#### 2.1.1 `@Tool` 注解

```java
@Target({ ElementType.METHOD, ElementType.ANNOTATION_TYPE })
@Retention(RetentionPolicy.RUNTIME)
@Documented
public @interface Tool {
    String name() default "";           // 工具名称
    String description() default "";    // 工具描述
    boolean streamable() default false; // 是否为流式工具
}
```

**作用**：
- 标记一个方法为 MCP 工具
- 定义工具的基本元数据
- 区分同步工具和流式工具

#### 2.1.2 `@ToolParam` 注解

```java
@Target({ ElementType.PARAMETER, ElementType.FIELD, ElementType.ANNOTATION_TYPE })
@Retention(RetentionPolicy.RUNTIME)
@Documented
public @interface ToolParam {
    boolean required() default true;    // 是否必填
    String description() default "";    // 参数描述
}
```

**作用**：
- 标记方法参数为工具参数
- 定义参数的约束（必填/可选）
- 提供参数的自然语言描述

### 2.2 工具定义示例

以 `ThreadTool` 为例：

```java
public class ThreadTool extends AbstractArthasTool {

    @Tool(
        name = "thread",
        description = "Thread 诊断工具: 查看线程信息及堆栈，对应 Arthas 的 thread 命令。一次性输出结果。"
    )
    public String thread(
            @ToolParam(description = "线程 ID", required = false)
            Long threadId,

            @ToolParam(description = "最忙前 N 个线程并打印堆栈 (-n)", required = false)
            Integer topN,

            @ToolParam(description = "是否查找阻塞其他线程的线程 (-b)", required = false)
            Boolean blocking,

            @ToolParam(description = "是否显示所有匹配线程 (--all)", required = false)
            Boolean all,

            ToolContext toolContext  // 特殊参数，不会暴露给 MCP
    ) {
        // 1. 构建 Arthas 命令
        StringBuilder cmd = buildCommand("thread");
        addFlag(cmd, "-b", blocking);
        if (topN != null && topN > 0) {
            cmd.append(" -n ").append(topN);
        }
        addFlag(cmd, "--all", all);
        if (threadId != null && threadId > 0) {
            cmd.append(" ").append(threadId);
        }

        // 2. 执行命令
        logger.info("Executing thread command: {}", cmd.toString());
        return executeSync(toolContext, cmd.toString());
    }
}
```

**关键点**：
1. **方法签名即工具定义**：参数名、类型、注解自动映射为 JSON Schema
2. **ToolContext 特殊处理**：不会暴露给 MCP，用于传递执行上下文
3. **返回值统一为 String**：JSON 格式的执行结果

### 2.3 自动生成 JSON Schema

系统会自动将工具定义转换为 MCP 协议要求的 JSON Schema：

```json
{
  "name": "thread",
  "description": "Thread 诊断工具: 查看线程信息及堆栈，对应 Arthas 的 thread 命令。一次性输出结果。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "threadId": {
        "type": "integer",
        "description": "线程 ID"
      },
      "topN": {
        "type": "integer",
        "description": "最忙前 N 个线程并打印堆栈 (-n)"
      },
      "blocking": {
        "type": "boolean",
        "description": "是否查找阻塞其他线程的线程 (-b)"
      },
      "all": {
        "type": "boolean",
        "description": "是否显示所有匹配线程 (--all)"
      }
    },
    "required": []  // 所有参数都是可选的
  }
}
```

**转换规则**：
- Java 类型 → JSON Schema 类型（Long/Integer → integer, Boolean → boolean, String → string）
- `@ToolParam(required=true)` → `required` 数组
- `@ToolParam(description)` → `description` 字段
- `ToolContext` 参数被过滤，不出现在 Schema 中

---

## 3. 工具注册与发现

### 3.1 工具扫描机制

`DefaultToolCallbackProvider` 负责扫描和注册所有工具：

```java
public class DefaultToolCallbackProvider implements ToolCallbackProvider {
    
    private String toolBasePackage;  // 扫描的基础包名
    
    @Override
    public ToolCallback[] getToolCallbacks() {
        // 懒加载 + 双重检查锁
        if (toolCallbacks == null) {
            synchronized (this) {
                if (toolCallbacks == null) {
                    toolCallbacks = scanForToolCallbacks();
                }
            }
        }
        return toolCallbacks;
    }
    
    private ToolCallback[] scanForToolCallbacks() {
        List<ToolCallback> callbacks = new ArrayList<>();
        
        // 扫描指定包下的所有类
        scanPackageForToolMethods(toolBasePackage, callbacks);
        
        return callbacks.toArray(new ToolCallback[0]);
    }
}
```

### 3.2 扫描流程

```
┌─────────────────────────────────────────────────────────────────┐
│  1. 扫描包路径                                                   │
│     - 支持文件系统扫描 (file://)                                 │
│     - 支持 JAR 包扫描 (jar://)                                   │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  2. 加载类                                                       │
│     - Class.forName(className, false, classLoader)              │
│     - 跳过接口、枚举、注解                                       │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  3. 查找 @Tool 方法                                              │
│     - clazz.getDeclaredMethods()                                │
│     - method.isAnnotationPresent(Tool.class)                    │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  4. 生成 ToolDefinition                                          │
│     - 提取 @Tool 注解信息                                        │
│     - 提取 @ToolParam 注解信息                                   │
│     - 生成 JSON Schema                                           │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  5. 创建 ToolCallback                                            │
│     - 实例化工具类（如果是非静态方法）                           │
│     - 包装为 DefaultToolCallback                                 │
│     - 注册到工具列表                                             │
└─────────────────────────────────────────────────────────────────┘
```

### 3.3 工具注册示例

```java
private void registerToolMethod(Class<?> clazz, Method method, List<ToolCallback> callbacks) {
    try {
        // 1. 生成工具定义
        ToolDefinition toolDefinition = ToolDefinitions.from(method);
        
        // 2. 实例化工具对象（如果是非静态方法）
        Object toolObject = Modifier.isStatic(method.getModifiers()) 
            ? null 
            : clazz.getDeclaredConstructor().newInstance();
        
        // 3. 创建工具回调
        ToolCallback callback = DefaultToolCallback.builder()
                .toolDefinition(toolDefinition)
                .toolMethod(method)
                .toolObject(toolObject)
                .toolCallResultConverter(toolCallResultConverter)
                .build();
        
        // 4. 注册
        callbacks.add(callback);
        logger.info("Registered tool: {} from class: {}", 
                    toolDefinition.getName(), clazz.getName());
    } catch (Exception e) {
        logger.error("Failed to register tool {}.{}", 
                     clazz.getName(), method.getName(), e);
    }
}
```

---

## 4. 工具调用流程

### 4.1 DefaultToolCallback 核心逻辑

`DefaultToolCallback` 是工具调用的核心，负责：
1. 参数解析和验证
2. 类型转换
3. 反射调用
4. 结果转换

```java
public class DefaultToolCallback implements ToolCallback {
    
    private final ToolDefinition toolDefinition;
    private final Method toolMethod;
    private final Object toolObject;
    private final ToolCallResultConverter toolCallResultConverter;
    
    @Override
    public String call(String toolInput, ToolContext toolContext) {
        // 1. 验证 ToolContext
        validateToolContextSupport(toolContext);
        
        // 2. 解析 JSON 参数
        Map<String, Object> toolArguments = extractToolArguments(toolInput);
        
        // 3. 验证必填参数
        validateRequiredParameters(toolArguments);
        
        // 4. 构建方法参数
        Object[] methodArguments = buildMethodArguments(toolArguments, toolContext);
        
        // 5. 反射调用方法
        Object result = callMethod(methodArguments);
        
        // 6. 转换结果为 JSON
        Type returnType = this.toolMethod.getGenericReturnType();
        return this.toolCallResultConverter.convert(result, returnType);
    }
}
```

### 4.2 参数处理详解

#### 4.2.1 参数解析

```java
private Map<String, Object> extractToolArguments(String toolInput) {
    // 将 JSON 字符串解析为 Map
    return JsonParser.fromJson(toolInput, new TypeReference<Map<String, Object>>() {});
}
```

**输入示例**：
```json
{
  "threadId": 123,
  "topN": 5,
  "blocking": true
}
```

#### 4.2.2 参数验证

```java
private void validateRequiredParameters(Map<String, Object> toolArguments) {
    Parameter[] parameters = this.toolMethod.getParameters();
    
    for (Parameter parameter : parameters) {
        // 跳过 ToolContext
        if (parameter.getType().isAssignableFrom(ToolContext.class)) {
            continue;
        }
        
        // 检查必填参数
        ToolParam toolParam = parameter.getAnnotation(ToolParam.class);
        if (toolParam != null && toolParam.required()) {
            String paramName = parameter.getName();
            Object paramValue = toolArguments.get(paramName);
            
            if (paramValue == null) {
                throw new IllegalArgumentException(
                    "Required parameter '" + paramName + "' is missing");
            }
            
            if (paramValue instanceof String && ((String) paramValue).trim().isEmpty()) {
                throw new IllegalArgumentException(
                    "Required parameter '" + paramName + "' cannot be empty");
            }
        }
    }
}
```

#### 4.2.3 参数类型转换

```java
private Object[] buildMethodArguments(Map<String, Object> toolInputArguments, 
                                     ToolContext toolContext) {
    return Stream.of(this.toolMethod.getParameters()).map(parameter -> {
        // 特殊处理 ToolContext
        if (parameter.getType().isAssignableFrom(ToolContext.class)) {
            return toolContext;
        }
        
        // 获取原始参数值
        Object rawArgument = toolInputArguments.get(parameter.getName());
        
        // 类型转换
        return buildTypedArgument(rawArgument, parameter.getParameterizedType());
    }).toArray();
}

private Object buildTypedArgument(Object value, Type type) {
    if (value == null) {
        return null;
    }
    
    if (type instanceof Class<?>) {
        // 简单类型转换
        return JsonParser.toTypedObject(value, (Class<?>) type);
    }
    
    // 复杂类型转换（泛型）
    String json = JsonParser.toJson(value);
    return JsonParser.fromJson(json, type);
}
```

**类型转换示例**：
- JSON `123` → Java `Long` 或 `Integer`
- JSON `true` → Java `Boolean`
- JSON `"hello"` → Java `String`
- JSON `[1,2,3]` → Java `List<Integer>`

#### 4.2.4 反射调用

```java
private Object callMethod(Object[] methodArguments) {
    // 设置可访问性（如果是私有方法）
    if (isObjectNotPublic() || isMethodNotPublic()) {
        this.toolMethod.setAccessible(true);
    }
    
    try {
        // 反射调用
        return this.toolMethod.invoke(this.toolObject, methodArguments);
    } catch (IllegalAccessException ex) {
        throw new IllegalStateException("Could not access method: " + ex.getMessage(), ex);
    } catch (InvocationTargetException ex) {
        // 包装异常
        throw new ToolExecutionException(this.toolDefinition, ex.getCause());
    }
}
```

---

## 5. 与 MCP 协议的适配

### 5.1 MCP 协议要求

MCP 协议定义了两个核心方法：
1. **`tools/list`**：返回所有可用工具的列表
2. **`tools/call`**：调用指定的工具

### 5.2 `tools/list` 实现

```java
private CompletableFuture<Object> handleToolsList(McpSchema.JSONRPCRequest request) {
    List<McpSchema.Tool> tools = new ArrayList<>();
    
    // 遍历所有已注册的工具回调
    for (ToolCallback callback : toolCallbacks.values()) {
        if (callback.getToolDefinition() != null) {
            // 转换为 MCP 工具定义
            McpSchema.Tool tool = new McpSchema.Tool(
                    callback.getToolDefinition().getName(),
                    callback.getToolDefinition().getDescription(),
                    callback.getToolDefinition().getInputSchema()
            );
            tools.add(tool);
        }
    }
    
    // 返回标准的 MCP 响应
    McpSchema.ListToolsResult result = new McpSchema.ListToolsResult(tools, null);
    return CompletableFuture.completedFuture(result);
}
```

**响应示例**：
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "tools": [
      {
        "name": "thread",
        "description": "Thread 诊断工具: 查看线程信息及堆栈",
        "inputSchema": { ... }
      },
      {
        "name": "jvm",
        "description": "JVM 信息查看工具",
        "inputSchema": { ... }
      }
    ]
  }
}
```

### 5.3 `tools/call` 实现

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
        
        // 5. 执行工具（调用实际的 Arthas 命令）
        String result = callback.call(toolInput, toolContext);
        
        // 6. 构造标准响应
        return CallToolResult.builder()
            .addTextContent(result)
            .isError(false)
            .build();
    });
}
```

**请求示例**：
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "thread",
    "arguments": {
      "topN": 5
    }
  }
}
```

**响应示例**：
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"threads\": [...], \"totalThreads\": 42}"
      }
    ],
    "isError": false
  }
}
```

### 5.4 适配层设计

```
┌─────────────────────────────────────────────────────────────────┐
│                    MCP 协议层                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  JSON-RPC 2.0 消息                                        │   │
│  │  - method: "tools/list" / "tools/call"                   │   │
│  │  - params: { name, arguments }                           │   │
│  └──────────────────────────────────────────────────────────┘   │
└────────────────────────────┬────────────────────────────────────┘
                             │ 协议转换
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    工具回调层                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  ToolCallback 接口                                        │   │
│  │  - getToolDefinition() → ToolDefinition                  │   │
│  │  - call(toolInput, toolContext) → String                 │   │
│  └──────────────────────────────────────────────────────────┘   │
└────────────────────────────┬────────────────────────────────────┘
                             │ 反射调用
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    工具实现层                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  @Tool 注解的方法                                         │   │
│  │  - 参数：Java 类型                                        │   │
│  │  - 返回：String (JSON)                                    │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

**关键设计**：
1. **协议无关性**：工具实现不依赖 MCP 协议，只需要标准的 Java 方法
2. **自动转换**：MCP JSON ↔ Java 对象的转换由框架自动完成
3. **统一接口**：所有工具通过 `ToolCallback` 统一调用

---

## 6. 工具执行上下文

### 6.1 ToolContext 设计目的

`ToolContext` 是一个**不可变的上下文容器**，用于在工具调用链路中传递执行所需的各种信息。

```java
public final class ToolContext {
    private final Map<String, Object> context;
    
    public ToolContext(Map<String, Object> context) {
        this.context = Collections.unmodifiableMap(context);
    }
    
    public Map<String, Object> getContext() {
        return this.context;
    }
}
```

**设计目标**：

| 目标 | 说明 |
|------|------|
| **协议无关性** | 工具实现不依赖 MCP 协议，只需要接收 `ToolContext` |
| **信息隔离** | 工具不需要知道上下文的具体内容，按需提取 |
| **扩展性** | 可以随时添加新的上下文信息，不影响现有工具 |
| **类型安全** | 通过 key 提取，避免参数爆炸 |
| **不可变性** | 防止工具修改上下文，保证线程安全 |

**类比其他框架**：

| 框架 | 上下文对象 | 作用 |
|------|-----------|------|
| **Servlet** | `HttpServletRequest` | 携带 HTTP 请求信息 |
| **Spring** | `ApplicationContext` | 携带 Spring 容器信息 |
| **Netty** | `ChannelHandlerContext` | 携带 Channel 和 Pipeline 信息 |
| **Arthas MCP** | `ToolContext` | 携带工具执行所需的信息 |

### 6.2 ToolContext 携带的信息

#### 6.2.1 完整的上下文内容

```java
// 在 McpClientProtocolHandler 中构造（Client 模式）
Map<String, Object> contextMap = new HashMap<>();
contextMap.put(TOOL_CONTEXT_COMMAND_CONTEXT_KEY, commandContext);  // ArthasCommandContext

// 在 McpNettyServer 中构造（Server 模式）
Map<String, Object> contextMap = new HashMap<>();
contextMap.put(TOOL_CONTEXT_COMMAND_CONTEXT_KEY, commandContext);  // ArthasCommandContext
contextMap.put(TOOL_CONTEXT_MCP_EXCHANGE_KEY, exchange);           // McpNettyServerExchange
contextMap.put(TOOL_CONTEXT_MCP_TRANSPORT_CONTEXT_KEY, transportContext);  // McpTransportContext
contextMap.put(PROGRESS_TOKEN, progressToken);                     // 进度令牌
contextMap.put(AUTH_SUBJECT_KEY, authSubject);                     // 认证主体
contextMap.put(USER_ID_KEY, userId);                               // 用户 ID
```

#### 6.2.2 各字段的作用

| Key | 类型 | 作用 | 必需性 |
|-----|------|------|--------|
| **TOOL_CONTEXT_COMMAND_CONTEXT_KEY** | `ArthasCommandContext` | 执行 Arthas 命令的核心对象 | ✅ 必需 |
| **TOOL_CONTEXT_MCP_EXCHANGE_KEY** | `McpNettyServerExchange` | MCP 服务端交换对象（用于流式响应） | Server 模式需要 |
| **TOOL_CONTEXT_MCP_TRANSPORT_CONTEXT_KEY** | `McpTransportContext` | MCP 传输上下文 | Server 模式需要 |
| **PROGRESS_TOKEN** | `String` | 进度令牌（用于报告进度） | 可选 |
| **AUTH_SUBJECT_KEY** | `Object` | 认证主体（用于权限控制） | 可选 |
| **USER_ID_KEY** | `String` | 用户 ID（用于统计上报） | 可选 |

#### 6.2.3 Client 模式 vs Server 模式

```
┌─────────────────────────────────────────────────────────────────┐
│                      Client 模式                                 │
│  ToolContext 内容：                                              │
│  - ArthasCommandContext (必需)                                   │
│                                                                 │
│  特点：                                                          │
│  - 简化的上下文，只包含命令执行所需的信息                         │
│  - 不需要 MCP 交换对象（响应通过 HTTP POST 返回）                │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                      Server 模式                                 │
│  ToolContext 内容：                                              │
│  - ArthasCommandContext (必需)                                   │
│  - McpNettyServerExchange (流式响应需要)                         │
│  - McpTransportContext (传输上下文)                              │
│  - progressToken (进度令牌)                                      │
│  - authSubject (认证主体)                                        │
│  - userId (用户 ID)                                              │
│                                                                 │
│  特点：                                                          │
│  - 完整的上下文，支持流式响应、进度报告、权限控制                 │
│  - 需要 MCP 交换对象（响应通过 SSE 推送）                        │
└─────────────────────────────────────────────────────────────────┘
```

### 6.3 为什么需要 ToolContext

#### 6.3.1 问题：如果没有 ToolContext

假设没有 `ToolContext`，工具方法的签名会变成：

```java
// ❌ 参数爆炸，难以维护
@Tool(name = "thread")
public String thread(
    @ToolParam Long threadId,
    @ToolParam Integer topN,
    CommandExecutor commandExecutor,           // 需要传递
    String sessionId,                          // 需要传递
    String consumerId,                         // 需要传递
    McpNettyServerExchange exchange,           // 需要传递（Server 模式）
    String progressToken,                      // 需要传递（可选）
    Object authSubject,                        // 需要传递（可选）
    String userId                              // 需要传递（可选）
) {
    // ...
}
```

**问题**：
1. ❌ **参数爆炸**：每个工具都需要声明这些参数
2. ❌ **协议耦合**：工具实现依赖 MCP 协议的具体对象
3. ❌ **难以扩展**：添加新的上下文信息需要修改所有工具
4. ❌ **类型不安全**：可选参数可能为 null，容易出错
5. ❌ **模式差异**：Client 模式和 Server 模式的参数不同

#### 6.3.2 解决方案：使用 ToolContext

```java
// ✅ 简洁的签名，易于维护
@Tool(name = "thread")
public String thread(
    @ToolParam Long threadId,
    @ToolParam Integer topN,
    ToolContext toolContext  // 统一的上下文对象
) {
    // 按需提取上下文信息
    ArthasCommandContext commandContext = extractCommandContext(toolContext);
    
    // 执行命令
    return executeSync(toolContext, "thread -n " + topN);
}
```

**优势**：
1. ✅ **参数简洁**：只需要一个 `ToolContext` 参数
2. ✅ **协议无关**：工具不依赖 MCP 协议的具体对象
3. ✅ **易于扩展**：添加新的上下文信息不影响工具签名
4. ✅ **按需提取**：工具只提取需要的信息
5. ✅ **模式统一**：Client 模式和 Server 模式使用相同的接口

### 6.4 ToolContext 的传递链路

```
┌─────────────────────────────────────────────────────────────────┐
│  1. MCP 请求到达                                                 │
│     - Client 模式: SSE 推送 tools/call                           │
│     - Server 模式: HTTP POST tools/call                          │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  2. 协议处理器构造 ToolContext                                   │
│     McpClientProtocolHandler.handleToolsCall() (Client)         │
│     或 McpNettyServer.handleToolsCall() (Server)                │
│                                                                 │
│     Map<String, Object> contextMap = new HashMap<>();           │
│     contextMap.put(COMMAND_CONTEXT_KEY, commandContext);        │
│     // ... 添加其他上下文信息                                    │
│     ToolContext toolContext = new ToolContext(contextMap);      │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  3. 调用工具回调                                                 │
│     ToolCallback callback = toolCallbacks.get(toolName);        │
│     String result = callback.call(toolInput, toolContext);      │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  4. DefaultToolCallback 反射调用工具方法                         │
│     Object[] args = buildMethodArguments(toolInput, toolContext);│
│     Object result = toolMethod.invoke(toolObject, args);        │
│                                                                 │
│     其中：                                                       │
│     - 普通参数从 toolInput (JSON) 中提取                         │
│     - ToolContext 参数直接传递                                   │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  5. 工具方法执行                                                 │
│     public String thread(Long threadId, Integer topN,           │
│                          ToolContext toolContext) {             │
│         // 提取命令上下文                                        │
│         ArthasCommandContext ctx = extractCommandContext(       │
│             toolContext);                                       │
│                                                                 │
│         // 执行命令                                              │
│         return executeSync(toolContext, "thread -n " + topN);   │
│     }                                                           │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  6. AbstractArthasTool 提取执行上下文                            │
│     protected String executeSync(ToolContext toolContext,       │
│                                  String commandStr) {           │
│         ToolExecutionContext execContext =                      │
│             new ToolExecutionContext(toolContext, false);       │
│                                                                 │
│         // 执行命令                                              │
│         Object result = execContext.getCommandContext()         │
│             .executeSync(commandStr);                           │
│                                                                 │
│         return JsonParser.toJson(result);                       │
│     }                                                           │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  7. ArthasCommandContext 执行命令                                │
│     public Map<String, Object> executeSync(String commandStr) { │
│         return commandExecutor.executeSync(commandStr, timeout);│
│     }                                                           │
└─────────────────────────────────────────────────────────────────┘
```

**关键代码：DefaultToolCallback 如何处理 ToolContext**：

```java
// DefaultToolCallback.buildMethodArguments()
private Object[] buildMethodArguments(Map<String, Object> toolInputArguments, 
                                     ToolContext toolContext) {
    return Stream.of(this.toolMethod.getParameters()).map(parameter -> {
        // 特殊处理 ToolContext 参数
        if (parameter.getType().isAssignableFrom(ToolContext.class)) {
            return toolContext;  // 直接传递
        }
        
        // 普通参数从 JSON 中提取
        Object rawArgument = toolInputArguments.get(parameter.getName());
        return buildTypedArgument(rawArgument, parameter.getParameterizedType());
    }).toArray();
}
```

**关键点**：
1. `ToolContext` 参数**不会暴露给 MCP 客户端**（不在 JSON Schema 中）
2. `ToolContext` 参数由**框架自动注入**，工具开发者无需关心
3. 工具方法可以**选择性地接收** `ToolContext`（不是必需的）

### 6.5 ArthasCommandContext 的关键作用

#### 6.5.1 定义

```java
public class ArthasCommandContext {
    private final CommandExecutor commandExecutor;  // 命令执行器
    private final CommandSessionBinding binding;    // 会话绑定（可选）
    
    // 构造器 1：无会话支持（仅支持同步命令）
    public ArthasCommandContext(CommandExecutor commandExecutor) {
        this.commandExecutor = commandExecutor;
        this.binding = null;
    }
    
    // 构造器 2：有会话支持（支持异步命令）
    public ArthasCommandContext(CommandExecutor commandExecutor, 
                               CommandSessionBinding binding) {
        this.commandExecutor = commandExecutor;
        this.binding = binding;
    }
}
```

#### 6.5.2 核心作用

`ArthasCommandContext` 是**工具执行 Arthas 命令的唯一入口**，提供以下功能：

| 方法 | 作用 | 是否需要会话 |
|------|------|-------------|
| `executeSync(commandStr)` | 同步执行命令，等待结果 | ❌ 不需要 |
| `executeAsync(commandStr)` | 异步启动命令，立即返回 | ✅ 需要 |
| `pullResults()` | 拉取异步命令的结果 | ✅ 需要 |
| `interruptJob()` | 中断当前任务 | ✅ 需要 |
| `getSessionId()` | 获取会话 ID | ✅ 需要 |

#### 6.5.3 异步命令的会话管理

Arthas 的某些命令是**异步的**，需要持续运行并产生多个结果：

| 命令类型 | 示例 | 特点 |
|---------|------|------|
| **同步命令** | `jvm`, `memory`, `thread` | 立即返回结果 |
| **异步命令** | `trace`, `watch`, `monitor`, `dashboard` | 持续运行，产生多个结果 |

**异步命令的执行流程**：

```
1. 启动命令
   └─▶ executeAsync("trace com.example.MyClass myMethod")
       └─▶ 返回 jobId
       └─▶ 命令在后台持续运行

2. 拉取结果（轮询）
   └─▶ pullResults()
       └─▶ 返回自上次拉取以来的所有结果
       └─▶ 可能返回 0 个、1 个或多个结果

3. 停止命令
   └─▶ interruptJob()
       └─▶ 停止后台任务
```

**关键问题**：如何区分不同的异步命令？

答案：**会话 ID (Session ID)**

```
┌─────────────────────────────────────────────────────────────────┐
│  Arthas 内部的会话管理                                           │
│                                                                 │
│  Session 1 (arthasSessionId = "abc123")                        │
│  ├─ Job 1: trace com.example.MyClass myMethod                  │
│  │  └─ 结果队列: [result1, result2, result3]                   │
│  └─ Consumer 1 (consumerId = "consumer-1")                     │
│     └─ 已消费位置: 2 (下次从 result3 开始)                      │
│                                                                 │
│  Session 2 (arthasSessionId = "def456")                        │
│  ├─ Job 1: watch com.example.MyClass myMethod                  │
│  │  └─ 结果队列: [result1, result2]                            │
│  └─ Consumer 1 (consumerId = "consumer-2")                     │
│     └─ 已消费位置: 1 (下次从 result2 开始)                      │
└─────────────────────────────────────────────────────────────────┘
```

### 6.6 会话管理的必要性

#### 6.6.1 场景 1：多个客户端同时使用

```
┌─────────────────────────────────────────────────────────────────┐
│  Arthas 服务端                                                   │
│                                                                 │
│  ❌ 没有会话管理：                                               │
│  - 只有一个全局的任务队列                                        │
│  - Client A 启动 trace 命令                                      │
│  - Client B 启动 watch 命令 → 失败！（只能有一个前台任务）       │
│                                                                 │
│  ✅ 有会话管理：                                                 │
│  - 每个客户端有独立的会话                                        │
│  - Client A (Session 1) 启动 trace 命令                         │
│  - Client B (Session 2) 启动 watch 命令 → 成功！                │
└─────────────────────────────────────────────────────────────────┘
```

#### 6.6.2 场景 2：Client 模式的会话复用

```
┌─────────────────────────────────────────────────────────────────┐
│  MCP Client 模式                                                 │
│                                                                 │
│  ❌ 没有会话管理（每次创建临时会话）：                            │
│  - 调用 1: trace MyClass method1                                │
│    └─▶ 创建临时会话 Session-temp-1                              │
│    └─▶ 启动 trace 命令                                          │
│    └─▶ 拉取结果                                                 │
│    └─▶ 关闭会话 → trace 命令被中断！                            │
│                                                                 │
│  - 调用 2: trace MyClass method2                                │
│    └─▶ 创建临时会话 Session-temp-2                              │
│    └─▶ 启动 trace 命令                                          │
│    └─▶ 拉取结果                                                 │
│    └─▶ 关闭会话 → trace 命令被中断！                            │
│                                                                 │
│  问题：每次调用都创建新会话，无法复用                             │
│                                                                 │
│  ✅ 有会话管理（固定会话）：                                      │
│  - 启动时创建固定会话 Session-client-1                           │
│                                                                 │
│  - 调用 1: trace MyClass method1                                │
│    └─▶ 使用 Session-client-1                                    │
│    └─▶ 启动 trace 命令                                          │
│    └─▶ 拉取结果                                                 │
│    └─▶ 中断任务（但会话保持）                                    │
│                                                                 │
│  - 调用 2: trace MyClass method2                                │
│    └─▶ 使用 Session-client-1（复用）                            │
│    └─▶ 启动 trace 命令                                          │
│    └─▶ 拉取结果                                                 │
│    └─▶ 中断任务（但会话保持）                                    │
│                                                                 │
│  优势：会话复用，避免频繁创建销毁                                 │
└─────────────────────────────────────────────────────────────────┘
```

#### 6.6.3 Client 模式的会话管理实现

```java
// McpClientProtocolHandler.handleToolsCall()

// 固定的客户端会话 ID
private static final String CLIENT_SESSION_ID = "mcp-client-session";

// 构造工具上下文
if (commandSessionManager != null) {
    // 获取或创建固定会话
    ArthasCommandSessionManager.CommandSessionBinding binding = 
            commandSessionManager.getCommandSession(CLIENT_SESSION_ID, null);
    
    // 创建带会话支持的命令上下文
    ArthasCommandContext commandContext = new ArthasCommandContext(
        commandExecutor, binding);
    
    contextMap.put(TOOL_CONTEXT_COMMAND_CONTEXT_KEY, commandContext);
}
```

**关键点**：
1. **固定会话 ID**：`CLIENT_SESSION_ID = "mcp-client-session"`
2. **会话复用**：每次工具调用都使用同一个会话
3. **会话生命周期**：与客户端生命周期一致（启动时创建，停止时销毁）

#### 6.6.4 两种模式对比

```
┌─────────────────────────────────────────────────────────────────┐
│  有会话支持（commandSessionManager != null）                     │
│                                                                 │
│  ArthasCommandContext commandContext = new ArthasCommandContext(│
│      commandExecutor,                                           │
│      binding  // 包含 sessionId 和 consumerId                   │
│  );                                                             │
│                                                                 │
│  支持的操作：                                                    │
│  ✅ executeSync()   - 同步执行命令                               │
│  ✅ executeAsync()  - 异步启动命令                               │
│  ✅ pullResults()   - 拉取异步结果                               │
│  ✅ interruptJob()  - 中断任务                                   │
│  ✅ getSessionId()  - 获取会话 ID                                │
│                                                                 │
│  适用场景：                                                      │
│  - 需要执行异步命令（trace, watch, monitor, dashboard）         │
│  - 需要长时间运行的工具                                          │
│  - 需要会话复用                                                  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  无会话支持（commandSessionManager == null）                     │
│                                                                 │
│  ArthasCommandContext commandContext = new ArthasCommandContext(│
│      commandExecutor                                            │
│      // 没有 binding                                            │
│  );                                                             │
│                                                                 │
│  支持的操作：                                                    │
│  ✅ executeSync()   - 同步执行命令                               │
│  ❌ executeAsync()  - 抛出异常！                                 │
│  ❌ pullResults()   - 抛出异常！                                 │
│  ❌ interruptJob()  - 抛出异常！                                 │
│  ❌ getSessionId()  - 抛出异常！                                 │
│                                                                 │
│  适用场景：                                                      │
│  - 只需要执行同步命令（jvm, memory, thread）                     │
│  - 快速返回的工具                                                │
│  - 降级模式（会话管理器不可用）                                   │
└─────────────────────────────────────────────────────────────────┘
```

### 6.7 ToolExecutionContext 的提取

```java
protected static class ToolExecutionContext {
    private final ArthasCommandContext commandContext;  // Arthas 命令执行上下文
    private final McpTransportContext mcpTransportContext;  // MCP 传输上下文
    private final Object authSubject;  // 认证主体
    private final String userId;  // 用户 ID
    private final McpNettyServerExchange exchange;  // MCP 交换对象（用于流式响应）
    private final String progressToken;  // 进度令牌
    private final boolean isStreamable;  // 是否为流式工具
    
    public ToolExecutionContext(ToolContext toolContext, boolean isStreamable) {
        // 从 ToolContext 中提取各种上下文信息
        this.commandContext = (ArthasCommandContext) toolContext.getContext()
            .get(TOOL_CONTEXT_COMMAND_CONTEXT_KEY);
        this.exchange = (McpNettyServerExchange) toolContext.getContext()
            .get(TOOL_CONTEXT_MCP_EXCHANGE_KEY);
        this.progressToken = (String) toolContext.getContext()
            .get(PROGRESS_TOKEN);
        // ...
    }
}

```

## 7. 同步与异步执行

### 7.1 命令类型对比

| 特性 | 同步命令 (Sync) | 异步命令 (Streamable) |
|------|----------------|---------------------|
| **适用场景** | 快速返回的命令 | 长时间运行的命令 |
| **典型命令** | `jvm`, `memory`, `thread`, `sc`, `sm` | `trace`, `watch`, `monitor`, `dashboard` |
| **执行方式** | 阻塞等待结果 | 立即返回，轮询拉取结果 |
| **结果产生** | 一次性产生所有结果 | 持续产生结果（事件驱动） |
| **会话要求** | 可使用临时会话 | 必须使用持久会话 |
| **超时处理** | 超时后中断并返回错误 | 超时后返回已收集的结果 |

### 7.2 同步执行详解

#### 7.2.1 执行流程

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          同步执行流程 (executeSync)                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  1. 工具方法调用                                                             │
│     │                                                                       │
│     ▼                                                                       │
│  ┌─────────────────────────────────────────────────────────────┐           │
│  │ AbstractArthasTool.executeSync(toolContext, commandStr)     │           │
│  │   └─▶ 创建 ToolExecutionContext                             │           │
│  │   └─▶ 提取 authSubject, userId                              │           │
│  └────────────────────────┬────────────────────────────────────┘           │
│                           │                                                 │
│                           ▼                                                 │
│  2. 命令执行器处理                                                           │
│  ┌─────────────────────────────────────────────────────────────┐           │
│  │ CommandExecutorImpl.executeSync()                           │           │
│  │   ├─▶ 获取/创建 Session（支持临时会话）                      │           │
│  │   ├─▶ 设置认证信息到 Session                                 │           │
│  │   ├─▶ 创建 PackingResultDistributor（结果收集器）            │           │
│  │   ├─▶ 创建 Job 并执行                                        │           │
│  │   └─▶ 等待 Job 完成（轮询状态）                               │           │
│  └────────────────────────┬────────────────────────────────────┘           │
│                           │                                                 │
│                           ▼                                                 │
│  3. 等待任务完成                                                             │
│  ┌─────────────────────────────────────────────────────────────┐           │
│  │ waitForJob(job, timeout)                                    │           │
│  │   while (true) {                                            │           │
│  │       if (job.status() == STOPPED || TERMINATED) return;    │           │
│  │       if (超时) return false;                                │           │
│  │       Thread.sleep(100ms);                                  │           │
│  │   }                                                         │           │
│  └────────────────────────┬────────────────────────────────────┘           │
│                           │                                                 │
│                           ▼                                                 │
│  4. 收集结果并返回                                                           │
│  ┌─────────────────────────────────────────────────────────────┐           │
│  │ 从 PackingResultDistributor 获取所有结果                     │           │
│  │ 返回 Map: {success, results, resultCount, sessionId}        │           │
│  └─────────────────────────────────────────────────────────────┘           │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### 7.2.2 核心代码实现

**AbstractArthasTool.executeSync()**：
```java
protected String executeSync(ToolContext toolContext, String commandStr) {
    try {
        ToolExecutionContext execContext = new ToolExecutionContext(toolContext, false);
        
        // 同步执行命令，等待结果
        Object result = execContext.getCommandContext().executeSync(
                commandStr, 
                execContext.getAuthSubject(),
                execContext.getUserId()
        );
        
        // 转换为 JSON
        return JsonParser.toJson(result);
    } catch (Exception e) {
        logger.error("Error executing sync command: {}", commandStr, e);
        return JsonParser.toJson(createErrorResponse("Error executing command: " + e.getMessage()));
    }
}
```

**CommandExecutorImpl.executeSync()**：
```java
public Map<String, Object> executeSync(String commandLine, long timeout, 
                                       String sessionId, Object authSubject, String userId) {
    Session session = null;
    boolean oneTimeAccess = false;
    
    try {
        // 1. 获取或创建会话（允许临时会话）
        session = getCurrentSession(sessionId, true);
        
        // 2. 设置认证信息
        if (authSubject != null) {
            session.put(SUBJECT_KEY, authSubject);
        }
        if (userId != null && !userId.trim().isEmpty()) {
            session.setUserId(userId);
        }
        
        // 3. 标记是否为临时会话
        if (session.get(ONETIME_SESSION_KEY) != null) {
            oneTimeAccess = true;
        }

        // 4. 创建结果收集器
        PackingResultDistributorImpl resultDistributor = 
            new PackingResultDistributorImpl(session);
        
        // 5. 创建并执行 Job
        Job job = this.createJob(commandLine, session, resultDistributor);
        if (job == null) {
            return createErrorResult(commandLine, "Failed to create job");
        }
        job.run();
        
        // 6. 等待完成（阻塞）
        boolean finished = waitForJob(job, (int) timeout);
        if (!finished) {
            job.interrupt();  // 超时中断
            return createTimeoutResult(commandLine, timeout);
        }

        // 7. 收集结果
        List<ResultModel> results = resultDistributor.getResults();
        
        Map<String, Object> result = new TreeMap<>();
        result.put("success", true);
        result.put("results", results);
        result.put("resultCount", results != null ? results.size() : 0);
        return result;

    } finally {
        // 8. 清理临时会话
        if (oneTimeAccess && session != null) {
            sessionManager.removeSession(session.getSessionId());
        }
    }
}
```

#### 7.2.3 同步执行的错误处理

```
┌─────────────────────────────────────────────────────────────────┐
│                    同步执行错误处理流程                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  错误类型 1: Session 获取失败                                    │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  getCurrentSession() 抛出 SessionNotFoundException      │   │
│  │  └─▶ 返回 {success: false, error: "Session not found"} │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  错误类型 2: Job 创建失败                                        │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  createJob() 返回 null                                   │   │
│  │  └─▶ 返回 {success: false, error: "Failed to create job"}│   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  错误类型 3: 执行超时                                            │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  waitForJob() 返回 false（超时）                         │   │
│  │  ├─▶ 调用 job.interrupt() 中断任务                       │   │
│  │  └─▶ 返回 {success: false, error: "Command timeout",    │   │
│  │           timeout: true}                                 │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  错误类型 4: 运行时异常                                          │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  任何 Exception                                          │   │
│  │  └─▶ 返回 {success: false, error: "Error executing..."}  │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  清理逻辑（finally）：                                           │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  如果是临时会话（oneTimeAccess = true）                   │   │
│  │  └─▶ 销毁会话 sessionManager.removeSession()             │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

### 7.3 异步执行详解

#### 7.3.1 执行流程总览

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       异步执行流程 (executeStreamable)                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  阶段 1: 启动异步命令                                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  executeAsyncWithRetry()                                            │   │
│  │  ├─▶ 尝试启动异步命令                                                │   │
│  │  ├─▶ 如果失败且可重试 → 中断现有任务 → 等待 → 重试                   │   │
│  │  └─▶ 返回启动结果 {success, jobId, jobStatus}                        │   │
│  └─────────────────────────────┬───────────────────────────────────────┘   │
│                                │                                            │
│                                ▼                                            │
│  阶段 2: 轮询收集结果                                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  executeAndCollectResults()                                         │   │
│  │  while (未超时 && 未达到预期结果数) {                                 │   │
│  │      results = pullResults()           // 拉取一批结果               │   │
│  │      allResults.addAll(results)        // 累积结果                   │   │
│  │      sendProgressNotification()        // 发送进度通知               │   │
│  │      检查是否完成 (TERMINATED / ALLOW_INPUT / 达到预期数量)          │   │
│  │      Thread.sleep(pollIntervalMs)      // 轮询间隔                   │   │
│  │  }                                                                   │   │
│  └─────────────────────────────┬───────────────────────────────────────┘   │
│                                │                                            │
│                                ▼                                            │
│  阶段 3: 清理并返回结果                                                       │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  finally {                                                          │   │
│  │      interruptJob()  // 确保中断前台任务，释放会话资源               │   │
│  │  }                                                                   │   │
│  │  return {status, results, resultCount, timedOut}                    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### 7.3.2 异步启动机制

**executeAsync() 实现**：
```java
// CommandExecutorImpl.executeAsync()
public Map<String, Object> executeAsync(String commandLine, String sessionId) {
    Map<String, Object> result = new TreeMap<>();
    
    // 1. 获取会话（不允许临时会话）
    Session session = getCurrentSession(sessionId, false);
    
    // 2. 尝试获取会话锁
    if (!session.tryLock()) {
        return createErrorResult(commandLine, "Another command is executing");
    }
    int lock = session.getLock();

    try {
        // 3. 检查是否有前台任务正在运行
        Job foregroundJob = session.getForegroundJob();
        if (foregroundJob != null) {
            session.unLock();
            return createErrorResult(commandLine, 
                "Another job is running, jobId: " + foregroundJob.id());
        }

        // 4. 创建 Job
        Job job = this.createJob(commandLine, session, session.getResultDistributor());
        if (job == null) {
            session.unLock();
            return createErrorResult(commandLine, "Failed to create job");
        }

        // 5. 设置为前台任务
        session.setForegroundJob(job);
        updateSessionInputStatus(session, InputStatus.ALLOW_INTERRUPT);

        // 6. 启动任务（非阻塞）
        job.run();

        // 7. 返回成功信息
        result.put("success", true);
        result.put("jobId", job.id());
        result.put("jobStatus", job.status().toString());  // RUNNING
        return result;

    } finally {
        if (session.getLock() == lock) {
            session.unLock();
        }
    }
}
```

**关键点**：
1. **会话锁**：防止同一会话同时执行多个命令
2. **前台任务检查**：确保没有其他任务正在运行
3. **非阻塞启动**：`job.run()` 立即返回，任务在后台执行

#### 7.3.3 异步启动重试机制

当启动异步命令失败时（如有其他任务正在运行），系统会自动重试：

```java
private static Map<String, Object> executeAsyncWithRetry(
        ToolExecutionContext execContext, String commandStr, Integer timeoutMs) {
    
    // 最大等待时间：3秒 或 命令超时时间的较小值
    long maxWaitMs = DEFAULT_ASYNC_START_MAX_WAIT_MS;  // 3000ms
    if (timeoutMs != null && timeoutMs > 0) {
        maxWaitMs = Math.min(maxWaitMs, timeoutMs);
    }

    long deadline = System.currentTimeMillis() + maxWaitMs;
    Map<String, Object> asyncResult = null;

    while (System.currentTimeMillis() < deadline) {
        // 尝试启动
        asyncResult = execContext.getCommandContext().executeAsync(commandStr);
        
        // 启动成功
        if (isAsyncExecutionStarted(asyncResult)) {
            return asyncResult;
        }

        // 检查是否为可重试的错误
        if (isRetryableAsyncStartError(asyncResult)) {
            try {
                // 中断当前任务
                execContext.getCommandContext().interruptJob();
            } catch (Exception ignored) {}
            
            // 等待后重试
            try {
                Thread.sleep(DEFAULT_ASYNC_START_RETRY_INTERVAL_MS);  // 100ms
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return asyncResult;
            }
            continue;
        }

        // 其他错误，直接返回
        return asyncResult;
    }

    return asyncResult;
}

// 判断是否为可重试的错误
private static boolean isRetryableAsyncStartError(Map<String, Object> asyncResult) {
    if (asyncResult == null) return false;
    if (Boolean.TRUE.equals(asyncResult.get("success"))) return false;
    
    Object error = asyncResult.get("error");
    if (error == null) return false;
    
    String message = String.valueOf(error);
    return message.contains("Another job is running") 
        || message.contains("Another command is executing");
}
```

**重试策略图示**：
```
┌─────────────────────────────────────────────────────────────────┐
│                      异步启动重试流程                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐                                               │
│  │ executeAsync │                                               │
│  └──────┬───────┘                                               │
│         │                                                       │
│         ▼                                                       │
│  ┌──────────────────┐     成功                                  │
│  │  启动成功?        ├─────────────────────▶ 返回成功            │
│  └──────┬───────────┘                                           │
│         │ 失败                                                   │
│         ▼                                                       │
│  ┌──────────────────┐     否                                    │
│  │  可重试错误?      ├─────────────────────▶ 返回错误            │
│  │  (Another job    │                                           │
│  │   is running)    │                                           │
│  └──────┬───────────┘                                           │
│         │ 是                                                     │
│         ▼                                                       │
│  ┌──────────────────┐                                           │
│  │  interruptJob()  │  中断当前任务                              │
│  └──────┬───────────┘                                           │
│         │                                                       │
│         ▼                                                       │
│  ┌──────────────────┐                                           │
│  │  sleep(100ms)    │  等待                                     │
│  └──────┬───────────┘                                           │
│         │                                                       │
│         ▼                                                       │
│  ┌──────────────────┐     否                                    │
│  │  超过 deadline?  ├─────────────────────▶ 重新尝试启动        │
│  └──────┬───────────┘              (回到 executeAsync)          │
│         │ 是                                                     │
│         ▼                                                       │
│      返回最后一次的结果                                          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

#### 7.3.4 结果轮询机制

**pullResults 的行为**：

```java
// ResultConsumerImpl.pollResults()
public List<ResultModel> pollResults() {
    try {
        lastAccessTime = System.currentTimeMillis();
        
        if (lock.tryLock(500, TimeUnit.MILLISECONDS)) {
            polling = true;
            sendingItemCount = 0;
            long firstResultTime = 0;
            long sendingDelay = 0;
            long waitingTime = 0;
            
            List<ResultModel> sendingResults = new ArrayList<>(resultBatchSizeLimit);

            // 核心循环：收集一批结果
            while (!closed
                    && sendingResults.size() < resultBatchSizeLimit  // 最多 20 个
                    && sendingDelay < 100                            // 发送延迟 < 100ms
                    && waitingTime < pollTimeLimit) {                // 等待时间 < 2秒
                
                // 从队列取一个结果（阻塞最多 100ms）
                ResultModel aResult = resultQueue.poll(100, TimeUnit.MILLISECONDS);
                
                if (aResult != null) {
                    sendingResults.add(aResult);
                    if (firstResultTime == 0) {
                        firstResultTime = System.currentTimeMillis();
                    }
                    // 检查是否需要立即返回（如结果数量超过阈值）
                    if (shouldFlush(sendingResults, aResult)) {
                        break;
                    }
                } else {
                    // 队列为空，计算延迟和等待时间
                    if (firstResultTime > 0) {
                        sendingDelay = System.currentTimeMillis() - firstResultTime;
                    }
                    waitingTime = System.currentTimeMillis() - accessTime;
                }
            }

            return sendingResults;
        }
    } finally {
        if (lock.isHeldByCurrentThread()) {
            lastAccessTime = System.currentTimeMillis();
            polling = false;
            lock.unlock();
        }
    }
    return Collections.emptyList();
}
```

**pullResults 的关键参数**：

| 参数 | 值 | 说明 |
|------|-----|------|
| `resultBatchSizeLimit` | 20 | 每次最多返回 20 个结果 |
| `pollTimeLimit` | 2000ms | 长轮询最大等待时间 |
| `sendingDelay` | 100ms | 获取第一个结果后的最大延迟 |
| `shouldFlush` | 100 items | 结果数量超过阈值时立即返回 |

**轮询收集流程**：
```
┌─────────────────────────────────────────────────────────────────┐
│              executeAndCollectResults 轮询流程                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  while (System.currentTimeMillis() < deadline) {                │
│      │                                                          │
│      ▼                                                          │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  results = commandContext.pullResults()                   │  │
│  │  // 每次返回 0~20 个结果                                   │  │
│  │  // 如果队列为空，会等待最多 2 秒（长轮询）                │  │
│  └──────────────────────────────────────────────────────────┘  │
│      │                                                          │
│      ▼                                                          │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  if (results == null) {                                   │  │
│  │      Thread.sleep(pollIntervalMs);  // 100ms              │  │
│  │      continue;                                            │  │
│  │  }                                                        │  │
│  └──────────────────────────────────────────────────────────┘  │
│      │                                                          │
│      ▼                                                          │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  检查错误消息 checkForErrorMessages()                     │  │
│  │  if (errorMessage != null) {                              │  │
│  │      return createErrorResponseWithResults(...)           │  │
│  │  }                                                        │  │
│  └──────────────────────────────────────────────────────────┘  │
│      │                                                          │
│      ▼                                                          │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  过滤辅助类型结果 filterCommandSpecificResults()          │  │
│  │  累积结果 allResults.addAll(currentBatchResults)          │  │
│  │  发送进度通知 sendProgressNotification()                  │  │
│  └──────────────────────────────────────────────────────────┘  │
│      │                                                          │
│      ▼                                                          │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  检查完成条件：                                            │  │
│  │  - jobStatus == "TERMINATED"                              │  │
│  │  - allowInputCount >= 2 (命令自然结束)                    │  │
│  │  - totalResultCount >= expectedResultCount                │  │
│  │  if (满足任一条件) break;                                 │  │
│  └──────────────────────────────────────────────────────────┘  │
│  }                                                              │
│                                                                 │
│  return createFinalResult(allResults, totalResultCount, ...)   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

### 7.4 任务取消机制

#### 7.4.1 interruptJob 实现

```java
// CommandExecutorImpl.interruptJob()
public Map<String, Object> interruptJob(String sessionId) {
    try {
        Session session = getCurrentSession(sessionId, false);
        
        // 获取前台任务
        Job job = session.getForegroundJob();
        if (job == null) {
            return createErrorResult(null, "no foreground job is running");
        }
        
        // 中断任务
        job.interrupt();

        Map<String, Object> result = new TreeMap<>();
        result.put("success", true);
        result.put("sessionId", sessionId);
        result.put("jobId", job.id());
        result.put("jobStatus", job.status().toString());  // TERMINATED
        return result;

    } catch (SessionNotFoundException e) {
        return createErrorResult(null, e.getMessage());
    }
}
```

#### 7.4.2 任务状态转换

```
┌─────────────────────────────────────────────────────────────────┐
│                       Job 状态转换图                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│                         ┌─────────┐                             │
│                         │  READY  │  初始状态                    │
│                         └────┬────┘                             │
│                              │ job.run()                        │
│                              ▼                                  │
│                         ┌─────────┐                             │
│              ┌──────────│ RUNNING │──────────┐                  │
│              │          └────┬────┘          │                  │
│              │               │               │                  │
│     job.suspend()       job完成/出错    job.interrupt()         │
│              │               │               │                  │
│              ▼               ▼               ▼                  │
│         ┌─────────┐   ┌────────────┐  ┌────────────┐           │
│         │ STOPPED │   │ TERMINATED │  │ TERMINATED │           │
│         └────┬────┘   │  (正常结束) │  │  (被中断)  │           │
│              │        └────────────┘  └────────────┘           │
│     job.resume()                                                │
│              │                                                  │
│              ▼                                                  │
│         ┌─────────┐                                             │
│         │ RUNNING │                                             │
│         └─────────┘                                             │
│                                                                 │
│  状态说明：                                                       │
│  - READY: 任务已创建，等待运行                                    │
│  - RUNNING: 任务正在执行                                          │
│  - STOPPED: 任务已暂停，可以恢复                                  │
│  - TERMINATED: 任务已结束（正常完成或被中断）                      │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

#### 7.4.3 取消时机

异步任务在以下时机会被取消：

```java
// AbstractArthasTool.executeStreamable() - finally 块
finally {
    if (execContext != null) {
        try {
            // 确保前台任务被及时释放，避免占用 session 影响后续 streamable 工具执行
            execContext.getCommandContext().interruptJob();
        } catch (Exception ignored) {
        }
    }
}
```

**取消场景**：

| 场景 | 触发条件 | 行为 |
|------|---------|------|
| **正常完成** | 达到预期结果数量 | 中断残留任务 |
| **超时** | 执行时间超过 timeoutMs | 中断任务，返回已收集结果 |
| **命令完成** | jobStatus == TERMINATED | 任务已结束，无需中断 |
| **异常** | 执行过程中抛出异常 | 中断任务，返回错误 |

---

### 7.5 失败处理机制

#### 7.5.1 异步执行的错误类型

```
┌─────────────────────────────────────────────────────────────────┐
│                    异步执行错误处理流程                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  错误类型 1: 启动失败                                            │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  executeAsyncWithRetry() 返回失败                        │   │
│  │  ├─ "Another job is running" → 重试                      │   │
│  │  ├─ "Another command is executing" → 重试               │   │
│  │  └─ 其他错误 → 返回 "Failed to start command"            │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  错误类型 2: 命令执行错误（结果中检测到错误消息）                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  checkForErrorMessages() 检测到错误消息                  │   │
│  │  匹配规则：                                               │   │
│  │  ├─ 包含 "failed", "error", "exception"                  │   │
│  │  ├─ 包含 "Malformed OGNL expression"                     │   │
│  │  ├─ 包含 "ParseException"                                │   │
│  │  └─ 包含 "ExpressionSyntaxException"                     │   │
│  │  → 返回 {error: true, message: ..., results: [...]}      │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  错误类型 3: 轮询过程中出错                                      │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  pullResults() 抛出异常                                  │   │
│  │  ├─ 错误计数 errorRetries++                              │   │
│  │  ├─ 等待 500ms 后重试                                    │   │
│  │  └─ 超过 MAX_ERROR_RETRIES(10次) → 返回 null             │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  错误类型 4: 超时                                                │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  System.currentTimeMillis() >= deadline                 │   │
│  │  ├─ timedOut = true                                      │   │
│  │  └─ 返回 {timedOut: true, results: [...], warning: ...}  │   │
│  │  注意：超时不算错误，会返回已收集的结果                    │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  错误类型 5: 线程中断                                            │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  InterruptedException                                    │   │
│  │  ├─ Thread.currentThread().interrupt()                   │   │
│  │  └─ 返回 null                                            │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

#### 7.5.2 错误消息检测

```java
// StreamableToolUtils.checkForErrorMessages()
private static String checkForErrorMessages(Map<String, Object> results) {
    if (results == null) return null;
    
    List<Object> resultList = (List<Object>) results.get("results");
    if (resultList == null || resultList.isEmpty()) return null;

    for (Object result : resultList) {
        String message = null;
        
        // 从不同类型的结果模型中提取消息
        if (result instanceof MessageModel) {
            message = ((MessageModel) result).getMessage();
        } else if (result instanceof EnhancerModel) {
            message = ((EnhancerModel) result).getMessage();
        } else if (result instanceof StatusModel) {
            message = ((StatusModel) result).getMessage();
        } else if (result instanceof CommandRequestModel) {
            message = ((CommandRequestModel) result).getMessage();
        }
        
        if (message != null && isErrorMessage(message)) {
            return message;
        }
    }
    
    return null;
}

private static boolean isErrorMessage(String message) {
    return message.matches(".*\\b(failed|error|exception)\\b.*") 
        || message.contains("Malformed OGNL expression") 
        || message.contains("ParseException") 
        || message.contains("ExpressionSyntaxException")
        || message.matches(".*Exception.*")
        || message.matches(".*Error.*");
}
```

#### 7.5.3 结果分发器的健康检查

当消费者不健康时，结果分发器会自动中断任务：

```java
// SharingResultDistributorImpl.distribute()
private void distribute() {
    while (running) {
        ResultModel result = pendingResultQueue.poll(100, TimeUnit.MILLISECONDS);
        if (result != null) {
            // 检查 consumer 健康状态
            int healthCount = 0;
            for (ResultConsumer consumer : consumers) {
                if (consumer.isHealthy()) {
                    healthCount++;
                }
                consumer.appendResult(result);
            }
            
            // 所有 consumer 都不健康，中断任务
            if (healthCount == 0 && !interruptedForUnhealthy) {
                interruptedForUnhealthy = true;
                interruptJob("all consumers are unhealthy");
            }
        }
    }
}

// 中断任务
private void interruptJob(String message) {
    Job job = session.getForegroundJob();
    if (job != null) {
        logger.warn(message + ", current job was interrupted.", job.id());
        job.interrupt();
        pendingResultQueue.offer(new MessageModel(message + ", current job was interrupted."));
    }
}
```

**Consumer 健康检查逻辑**：
```java
// ResultConsumerImpl.isHealthy()
public boolean isHealthy() {
    return isPolling()                                    // 正在轮询
        || resultQueue.size() < resultQueueSize           // 队列未满
        || System.currentTimeMillis() - lastAccessTime < 1000;  // 最近 1 秒内有访问
}
```

**不健康的判定**：
- 没有在轮询
- 结果队列已满
- 超过 1 秒没有访问

---

### 7.6 完成条件判断

异步任务的完成条件：

```java
// StreamableToolUtils.executeAndCollectResults()

// 判断条件
boolean hasExpectedResultCount = (expectedResultCount != null);
boolean reachedExpectedResultCount = hasExpectedResultCount 
    && totalResultCount >= expectedResultCount;
boolean allowInputCompletion = !hasExpectedResultCount
    && commandCompleted
    && allowInputCount >= MIN_ALLOW_INPUT_COUNT_TO_COMPLETE;  // 2

// 完成判断
if ("TERMINATED".equals(jobStatus) || allowInputCompletion || reachedExpectedResultCount) {
    break;  // 退出轮询循环
}
```

**完成条件说明**：

| 条件 | 说明 | 典型场景 |
|------|------|---------|
| **TERMINATED** | Job 状态为已终止 | 命令执行完毕或被中断 |
| **allowInputCompletion** | 收到 2 次 ALLOW_INPUT 状态 | 命令自然结束（如 trace 匹配完成） |
| **reachedExpectedResultCount** | 达到预期结果数量 | 指定了 expectedResultCount 参数 |

**ALLOW_INPUT 检测**：
```java
private static boolean checkCommandCompletion(Map<String, Object> results, 
                                              int currentAllowInputCount) {
    List<Object> resultList = (List<Object>) results.get("results");
    if (resultList == null || resultList.isEmpty()) return false;

    for (Object result : resultList) {
        if (result instanceof InputStatusModel) {
            InputStatusModel inputStatusModel = (InputStatusModel) result;
            if (inputStatusModel.getInputStatus() == InputStatus.ALLOW_INPUT) {
                return true;  // 命令完成，允许新输入
            }
        }
    }
    return false;
}
```

---

### 7.7 异步执行总结

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          异步执行完整流程图                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  1. executeStreamable() 开始                                         │   │
│  └────────────────────────────────┬────────────────────────────────────┘   │
│                                   │                                         │
│                                   ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  2. executeAsyncWithRetry()                                         │   │
│  │     ├─ 成功 → 继续                                                   │   │
│  │     └─ 失败 → 返回错误                                               │   │
│  └────────────────────────────────┬────────────────────────────────────┘   │
│                                   │                                         │
│                                   ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  3. executeAndCollectResults() 轮询循环                              │   │
│  │                                                                      │   │
│  │     while (未超时) {                                                 │   │
│  │         ├─ pullResults()                                            │   │
│  │         ├─ 检查错误消息 → 有错误则返回                               │   │
│  │         ├─ 累积结果                                                  │   │
│  │         ├─ 发送进度通知                                              │   │
│  │         └─ 检查完成条件 → 满足则退出                                 │   │
│  │     }                                                                │   │
│  └────────────────────────────────┬────────────────────────────────────┘   │
│                                   │                                         │
│                                   ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  4. finally: interruptJob()                                         │   │
│  │     无论成功或失败，确保中断前台任务                                   │   │
│  └────────────────────────────────┬────────────────────────────────────┘   │
│                                   │                                         │
│                                   ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  5. 返回结果                                                         │   │
│  │     ├─ 成功: {status: completed, results: [...], resultCount: N}    │   │
│  │     ├─ 超时: {status: completed, timedOut: true, results: [...]}    │   │
│  │     └─ 失败: {error: true, message: "..."}                          │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 8. 完整调用链路

### 8.1 时序图

```
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│  MCP     │   │   MCP    │   │ Default  │   │  Thread  │   │ Abstract │   │ Arthas   │
│ Client   │   │  Server  │   │ Tool     │   │  Tool    │   │ Arthas   │   │ Command  │
│          │   │          │   │ Callback │   │          │   │  Tool    │   │ Context  │
└────┬─────┘   └────┬─────┘   └────┬─────┘   └────┬─────┘   └────┬─────┘   └────┬─────┘
     │              │              │              │              │              │
     │ tools/call   │              │              │              │              │
     │ thread(topN=5)              │              │              │              │
     ├─────────────>│              │              │              │              │
     │              │              │              │              │              │
     │              │ call(        │              │              │              │
     │              │  toolInput,  │              │              │              │
     │              │  toolContext)│              │              │              │
     │              ├─────────────>│              │              │              │
     │              │              │              │              │              │
     │              │              │ 1. 解析 JSON │              │              │
     │              │              │ 2. 验证参数  │              │              │
     │              │              │ 3. 类型转换  │              │              │
     │              │              │              │              │              │
     │              │              │ invoke(      │              │              │
     │              │              │  threadId=null,             │              │
     │              │              │  topN=5,     │              │              │
     │              │              │  toolContext)│              │              │
     │              │              ├─────────────>│              │              │
     │              │              │              │              │              │
     │              │              │              │ executeSync( │              │
     │              │              │              │  toolContext,│              │
     │              │              │              │  "thread -n 5")             │
     │              │              │              ├─────────────>│              │
     │              │              │              │              │              │
     │              │              │              │              │ executeSync( │
     │              │              │              │              │  "thread -n 5",
     │              │              │              │              │  authSubject,│
     │              │              │              │              │  userId)     │
     │              │              │              │              ├─────────────>│
     │              │              │              │              │              │
     │              │              │              │              │              │ 执行命令
     │              │              │              │              │              │ 收集结果
     │              │              │              │              │              │
     │              │              │              │              │ result (JSON)│
     │              │              │              │              │<─────────────┤
     │              │              │              │              │              │
     │              │              │              │ result (JSON)│              │
     │              │              │              │<─────────────┤              │
     │              │              │              │              │              │
     │              │              │ result (JSON)│              │              │
     │              │              │<─────────────┤              │              │
     │              │              │              │              │              │
     │              │ CallToolResult              │              │              │
     │              │<─────────────┤              │              │              │
     │              │              │              │              │              │
     │ JSON-RPC     │              │              │              │              │
     │ Response     │              │              │              │              │
     │<─────────────┤              │              │              │              │
     │              │              │              │              │              │
```

### 8.2 数据流转

```
1. MCP 请求（JSON-RPC）
   ↓
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "thread",
    "arguments": {
      "topN": 5
    }
  }
}

2. 工具输入（JSON 字符串）
   ↓
"{\"topN\": 5}"

3. 方法参数（Java 对象）
   ↓
threadId = null
topN = 5
blocking = null
all = null
toolContext = ToolContext{...}

4. Arthas 命令（字符串）
   ↓
"thread -n 5"

5. 命令结果（Java 对象）
   ↓
ThreadModel {
  threads: [...],
  totalThreads: 42
}

6. 工具结果（JSON 字符串）
   ↓
"{\"threads\": [...], \"totalThreads\": 42}"

7. MCP 响应（JSON-RPC）
   ↓
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"threads\": [...], \"totalThreads\": 42}"
      }
    ],
    "isError": false
  }
}
```

---

## 9. 设计亮点

### 9.1 注解驱动，零配置

**传统方式**（需要手动配置）：
```yaml
tools:
  - name: thread
    class: com.taobao.arthas.core.mcp.tool.function.jvm300.ThreadTool
    method: thread
    parameters:
      - name: threadId
        type: Long
        required: false
        description: "线程 ID"
      - name: topN
        type: Integer
        required: false
        description: "最忙前 N 个线程"
```

**Arthas MCP 方式**（注解驱动）：
```java
@Tool(name = "thread", description = "Thread 诊断工具")
public String thread(
    @ToolParam(description = "线程 ID", required = false) Long threadId,
    @ToolParam(description = "最忙前 N 个线程", required = false) Integer topN,
    ToolContext toolContext
) {
    // 实现
}
```

**优势**：
- ✅ 工具定义和实现在一起，易于维护
- ✅ 自动生成 JSON Schema，无需手动编写
- ✅ 类型安全，编译时检查
- ✅ 支持 IDE 自动补全和重构

### 9.2 协议无关性

工具实现不依赖 MCP 协议，可以轻松适配其他协议：

```
┌─────────────────────────────────────────────────────────────────┐
│                    工具实现层（协议无关）                         │
│  @Tool public String thread(...) { ... }                        │
└────────────────────────────┬────────────────────────────────────┘
                             │
                ┌────────────┴────────────┐
                │                         │
                ▼                         ▼
┌───────────────────────────┐   ┌───────────────────────────┐
│    MCP 协议适配层          │   │   其他协议适配层           │
│  - tools/list             │   │  - REST API               │
│  - tools/call             │   │  - gRPC                   │
└───────────────────────────┘   └───────────────────────────┘
```

### 9.3 自动类型转换

框架自动处理 JSON ↔ Java 对象的转换：

```java
// MCP 请求
{"topN": 5}

// 自动转换为
Integer topN = 5;

// 工具返回
return "{\"threads\": [...]}";

// 自动包装为 MCP 响应
{
  "content": [
    {"type": "text", "text": "{\"threads\": [...]}"}
  ]
}
```

### 9.4 统一的错误处理

```java
try {
    result = this.toolMethod.invoke(this.toolObject, methodArguments);
} catch (InvocationTargetException ex) {
    // 包装为 ToolExecutionException
    throw new ToolExecutionException(this.toolDefinition, ex.getCause());
}
```

所有工具异常都被统一包装，便于上层处理。

### 9.5 灵活的执行模式

支持两种执行模式：
1. **同步模式**：适用于快速返回的命令
2. **异步模式**：适用于长时间运行的命令

工具开发者只需要调用不同的方法：
```java
// 同步执行
return executeSync(toolContext, cmd.toString());

// 异步执行
return executeStreamable(toolContext, cmd.toString(), 
                        expectedResultCount, pollIntervalMs, timeoutMs, successMessage);
```

### 9.6 自动重试机制

异步命令启动失败时自动重试：
- 检测 "Another job is running" 错误
- 自动中断当前任务
- 等待后重试
- 最多重试 3 秒

### 9.7 上下文传递

通过 `ToolContext` 传递执行上下文：
- 认证信息（authSubject, userId）
- 命令执行上下文（ArthasCommandContext）
- MCP 交换对象（McpNettyServerExchange）
- 进度令牌（progressToken）

工具实现无需关心这些细节，框架自动处理。

---

## 10. 执行限制与并发控制

本章详细说明 Arthas MCP Tools 的执行限制机制，包括会话锁、并发控制、以及多命令并行运行时的行为。

### 10.1 核心限制：单会话单任务

**每个 Arthas Session 同一时间只能运行一个任务（Job）**，这是最重要的执行限制。

```
┌─────────────────────────────────────────────────────────────────┐
│                      会话任务执行限制                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Session A                    Session B                         │
│  ┌─────────────────┐          ┌─────────────────┐              │
│  │ Job 1 (running) │          │ Job 3 (running) │  ← 可以并行   │
│  └─────────────────┘          └─────────────────┘              │
│         ↓                                                       │
│  ┌─────────────────┐                                            │
│  │ Job 2 (waiting) │  ← 必须等待 Job 1 结束                      │
│  └─────────────────┘                                            │
│                                                                 │
│  同一 Session 内：串行执行                                        │
│  不同 Session 间：可以并行执行                                    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 10.2 两级锁机制

`CommandExecutorImpl.executeAsync()` 中实现了两级检查：

```java
// 第一级：会话锁
if (!session.tryLock()) {
    return createErrorResult(commandLine, "Another command is executing");
}

// 第二级：前台任务检查
Job foregroundJob = session.getForegroundJob();
if (foregroundJob != null) {
    return createErrorResult(commandLine, "Another job is running, jobId: " + foregroundJob.id());
}
```

| 级别 | 检查内容 | 错误信息 |
|------|---------|---------|
| **会话锁** | `session.tryLock()` | "Another command is executing" |
| **前台任务** | `session.getForegroundJob()` | "Another job is running, jobId: xxx" |

### 10.3 同步命令 vs 异步命令的并发能力

| 特性 | 同步命令 | 异步命令 |
|------|---------|---------|
| **会话要求** | 支持临时会话 | 必须持久会话 |
| **并发能力** | 可在不同临时会话中并发 | 同会话必须串行 |
| **锁占用时间** | 仅在执行期间 | 从启动到中断/完成 |

#### 同步命令可以"并发"的原因

```java
// 同步执行时允许创建临时会话
session = getCurrentSession(sessionId, true);  // allowOneTime = true

// 临时会话会在执行完成后销毁
if (oneTimeAccess && session != null) {
    sessionManager.removeSession(session.getSessionId());
}
```

每次同步命令可以使用独立的临时会话，因此**多个同步命令可以同时执行**（在不同的临时会话中）。

### 10.4 MCP 客户端模式的特殊限制

```java
// 客户端模式下使用固定的 session ID（一个客户端实例对应一个 session）
private static final String CLIENT_SESSION_ID = "mcp-client-session";
```

**在 MCP 客户端模式下，同一个客户端实例的所有异步命令共享同一个 Session，因此必须串行执行。**

### 10.5 实际并发场景

```
场景 1: 多个同步命令（如 jvm, memory, thread）
┌─────────────────────────────────────────────────────────────────┐
│  请求 1: jvm       → 临时会话 A → 执行 → 销毁会话 A              │
│  请求 2: memory    → 临时会话 B → 执行 → 销毁会话 B    ✅ 可并行  │
│  请求 3: thread    → 临时会话 C → 执行 → 销毁会话 C              │
└─────────────────────────────────────────────────────────────────┘

场景 2: 同一会话的多个异步命令（如 trace, watch）
┌─────────────────────────────────────────────────────────────────┐
│  请求 1: trace     → Session X → 执行中...                      │
│  请求 2: watch     → Session X → ❌ 报错 "Another job is running"│
│                                    ↓                            │
│                              自动重试机制：                       │
│                              1. 中断请求 1                        │
│                              2. 等待 100ms                       │
│                              3. 重新启动请求 2                    │
└─────────────────────────────────────────────────────────────────┘

场景 3: 不同会话的异步命令
┌─────────────────────────────────────────────────────────────────┐
│  请求 1: trace     → Session X → 执行中...                      │
│  请求 2: watch     → Session Y → 执行中...    ✅ 可并行          │
│  （来自不同 MCP 客户端，使用不同会话）                            │
└─────────────────────────────────────────────────────────────────┘
```

### 10.6 执行限制总结表

| 限制项 | 说明 | 影响范围 |
|--------|------|---------|
| **单会话单任务** | 同一 Session 只能有一个前台 Job | 异步命令 |
| **会话锁** | 防止同一会话同时执行多个操作 | 所有命令 |
| **临时会话** | 同步命令可使用临时会话，支持并发 | 仅同步命令 |
| **自动重试** | 遇到 "Another job is running" 会自动重试 | 异步命令启动 |
| **强制中断** | finally 中会中断前台任务 | 异步命令 |

### 10.7 如何规避限制

| 方法 | 适用场景 | 说明 |
|------|---------|------|
| **使用同步命令** | 快速查询 | 同步命令使用临时会话，可并发 |
| **多 MCP 客户端** | 需要并行监控 | 每个客户端有独立 Session |
| **等待完成后再执行** | 顺序依赖 | 等前一个异步命令完成再执行下一个 |
| **依赖自动重试** | 简单场景 | 系统会自动中断旧任务并重试新任务 |

---

## 11. 命令并行执行与重复增强问题

当多个监控命令（如 trace、watch）同时运行时，可能会对同一个方法进行多次增强。本章分析这种情况下的行为和潜在问题。

### 11.1 多命令共存的架构设计

Arthas 采用了**多 Listener 共享增强**的设计模式：

```
┌─────────────────────────────────────────────────────────────────┐
│                        字节码增强层                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│     目标方法: UserService.login()                               │
│     ┌──────────────────────────────────────────────────────┐   │
│     │  SpyAPI.atEnter(...)     // 只插入一次               │   │
│     │  // ... 原始代码 ...                                  │   │
│     │  SpyAPI.atExit(...)      // 只插入一次               │   │
│     └──────────────────────────────────────────────────────┘   │
│                          │                                      │
│                          ▼                                      │
│     ┌──────────────────────────────────────────────────────┐   │
│     │           AdviceListenerManager                       │   │
│     │  ┌────────────────────────────────────────────────┐  │   │
│     │  │  Key: "UserService|login|methodDesc"           │  │   │
│     │  │  Listeners: [TraceListener, WatchListener, ...] │  │   │
│     │  └────────────────────────────────────────────────┘  │   │
│     └──────────────────────────────────────────────────────┘   │
│                          │                                      │
│                          ▼                                      │
│     ┌──────────────────────────────────────────────────────┐   │
│     │  for (listener : listeners) {                        │   │
│     │      listener.before(...);  // 多个 listener 都会收到 │   │
│     │  }                                                   │   │
│     └──────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 11.2 关键组件说明

#### 11.2.1 TransformerManager - 转换器管理

```java
// TransformerManager.java
public class TransformerManager {
    // 不同类型的 Transformer 分开管理
    private List<ClassFileTransformer> watchTransformers = new CopyOnWriteArrayList<>();
    private List<ClassFileTransformer> traceTransformers = new CopyOnWriteArrayList<>();
    
    // 统一的 transform 入口，按顺序执行所有 transformer
    @Override
    public byte[] transform(...) {
        // reTransformers 先执行
        for (ClassFileTransformer transformer : reTransformers) {
            classfileBuffer = transformer.transform(...);
        }
        // watchTransformers 执行
        for (ClassFileTransformer transformer : watchTransformers) {
            classfileBuffer = transformer.transform(...);
        }
        // traceTransformers 执行
        for (ClassFileTransformer transformer : traceTransformers) {
            classfileBuffer = transformer.transform(...);
        }
        return classfileBuffer;
    }
}
```

#### 11.2.2 防止重复插桩的机制

Enhancer 使用 `LocationFilter` 来检测方法是否已经被增强：

```java
// Enhancer.java - 防止重复插入 Spy 调用
// 用于检查是否已插入了 spy函数，如果已有则不重复处理
GroupLocationFilter groupLocationFilter = new GroupLocationFilter();

LocationFilter enterFilter = new InvokeContainLocationFilter(
    Type.getInternalName(SpyAPI.class), "atEnter", LocationType.ENTER);
LocationFilter existFilter = new InvokeContainLocationFilter(
    Type.getInternalName(SpyAPI.class), "atExit", LocationType.EXIT);
// ...

// 先查找是否有 atBeforeInvoke 函数，如果有，则说明已经有trace了
// 则直接不再尝试增强，直接插入 listener
if(AsmUtils.containsMethodInsnNode(methodNode, 
        Type.getInternalName(SpyAPI.class), "atBeforeInvoke")) {
    // 直接注册 listener，不重复增强字节码
    AdviceListenerManager.registerTraceAdviceListener(...);
} else {
    // 首次增强，插入 Spy 调用并注册 listener
    MethodProcessor methodProcessor = new MethodProcessor(...);
    // ...
}
```

#### 11.2.3 AdviceListenerManager - 多 Listener 管理

```java
// AdviceListenerManager.java
class ClassLoaderAdviceListenerManager {
    // key = className + methodName + methodDesc
    private ConcurrentHashMap<String, List<AdviceListener>> map = new ConcurrentHashMap<>();
    
    public void registerAdviceListener(String className, String methodName, 
            String methodDesc, AdviceListener listener) {
        synchronized (this) {
            String key = key(className, methodName, methodDesc);
            List<AdviceListener> listeners = map.get(key);
            if (listeners == null) {
                listeners = new ArrayList<>();
                map.put(key, listeners);
            }
            // 防止重复添加同一个 listener
            if (!listeners.contains(listener)) {
                listeners.add(listener);
            }
        }
    }
}
```

#### 11.2.4 SpyImpl - 回调分发

```java
// SpyImpl.java - 方法入口回调
@Override
public void atEnter(Class<?> clazz, String methodInfo, Object target, Object[] args) {
    // 查询所有注册的 listener
    List<AdviceListener> listeners = AdviceListenerManager.queryAdviceListeners(
            classLoader, className, methodName, methodDesc);
    
    if (listeners != null) {
        // 遍历所有 listener，每个都会收到通知
        for (AdviceListener adviceListener : listeners) {
            try {
                // 跳过已停止的 listener
                if (skipAdviceListener(adviceListener)) {
                    continue;
                }
                adviceListener.before(clazz, methodName, methodDesc, target, args);
            } catch (Throwable e) {
                logger.error("...", e);
            }
        }
    }
}
```

### 11.3 同时 trace 同一方法的行为

当从不同会话同时对同一方法执行 trace 时：

```
Session A: trace com.example.UserService login
Session B: trace com.example.UserService login

┌─────────────────────────────────────────────────────────────────┐
│                      执行过程                                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. Session A 执行 trace                                        │
│     └─→ Enhancer 检测：方法未增强                                │
│     └─→ 插入 SpyAPI.atEnter/atExit 调用                         │
│     └─→ 注册 TraceAdviceListener_A                              │
│                                                                 │
│  2. Session B 执行 trace                                        │
│     └─→ Enhancer 检测：方法已增强（发现 SpyAPI 调用）            │
│     └─→ 跳过字节码增强                                          │
│     └─→ 只注册 TraceAdviceListener_B                            │
│                                                                 │
│  3. 方法被调用时                                                │
│     └─→ SpyAPI.atEnter() 触发                                   │
│     └─→ SpyImpl 查询 listener 列表                              │
│     └─→ 调用 TraceAdviceListener_A.before()                     │
│     └─→ 调用 TraceAdviceListener_B.before()                     │
│     └─→ 两个会话都会收到结果                                     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 11.4 不会产生重复的原因

| 检查点 | 机制 | 说明 |
|--------|------|------|
| **字节码层面** | `InvokeContainLocationFilter` | 检测 SpyAPI 调用是否已存在，已存在则跳过增强 |
| **Listener 层面** | `!listeners.contains(listener)` | 同一个 listener 不会重复添加 |
| **执行层面** | `skipAdviceListener()` | 跳过已停止进程的 listener |

### 11.5 潜在的重复/冲突问题

虽然 Arthas 有防止重复增强的机制，但在某些场景下仍可能出现问题：

#### 11.5.1 输出重复问题（用户层面）

```
问题：两个 trace 命令监控同一方法，用户看到"重复"的输出

Session A 输出：
  +---[10ms] login()
  
Session B 输出：
  +---[10ms] login()

实际上：这不是重复，是两个独立的监控都正常输出
```

**这不是 bug，而是预期行为** - 每个会话都有独立的 listener，都会独立输出。

#### 11.5.2 性能影响

```java
// 多个 listener 会增加方法调用的开销
for (AdviceListener adviceListener : listeners) {
    adviceListener.before(...);  // 每个都要执行
}
```

**建议**：避免对同一高频方法同时启动多个监控。

#### 11.5.3 reset 命令的影响

```
Session A: trace UserService login   (运行中)
Session B: reset UserService         (执行重置)

结果：Session A 的 trace 会失效！
```

这是因为 `reset` 会移除所有增强，包括其他会话的监控。

### 11.6 Listener 生命周期与清理

```java
// AdviceListenerManager.java - 定时清理机制
static {
    // 每 3 秒清理一次失效的 listener
    ArthasBootstrap.getInstance().getScheduledExecutorService()
        .scheduleWithFixedDelay(new Runnable() {
            @Override
            public void run() {
                for (Entry<String, List<AdviceListener>> entry : map.entrySet()) {
                    List<AdviceListener> listeners = entry.getValue();
                    List<AdviceListener> newResult = new ArrayList<>();
                    for (AdviceListener listener : listeners) {
                        if (listener instanceof ProcessAware) {
                            Process process = ((ProcessAware) listener).getProcess();
                            if (process != null) {
                                ExecStatus status = process.status();
                                // 只保留未终止的 listener
                                if (!status.equals(ExecStatus.TERMINATED)) {
                                    newResult.add(listener);
                                }
                            }
                        }
                    }
                    // 更新 listener 列表
                    if (newResult.size() != listeners.size()) {
                        map.put(entry.getKey(), newResult);
                    }
                }
            }
        }, 3, 3, TimeUnit.SECONDS);
}
```

### 11.7 并行执行最佳实践

| 场景 | 建议 | 原因 |
|------|------|------|
| **多人同时调试** | 使用不同的 MCP 客户端连接 | 独立会话，互不影响 |
| **监控同一方法** | 尽量避免，或接受多份输出 | 每个会话都会输出 |
| **高频方法监控** | 使用 `-n` 限制次数 | 减少性能影响 |
| **需要重置类** | 通知其他使用者 | reset 会影响所有监控 |
| **查看当前增强** | 使用 `sm -d` 或 `jad` | 确认增强状态 |

### 11.8 并行运行完整流程图

```
                      ┌─────────────────────────────────────────┐
                      │          目标 JVM 进程                   │
                      └─────────────────────────────────────────┘
                                        │
              ┌─────────────────────────┼─────────────────────────┐
              │                         │                         │
              ▼                         ▼                         ▼
    ┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
    │  MCP Client A    │     │  MCP Client B    │     │  Arthas CLI      │
    │  Session: sess-A │     │  Session: sess-B │     │  Session: sess-C │
    └────────┬─────────┘     └────────┬─────────┘     └────────┬─────────┘
             │                        │                        │
             │ trace foo()            │ watch foo()            │ trace foo()
             ▼                        ▼                        ▼
    ┌──────────────────────────────────────────────────────────────────────┐
    │                      TransformerManager                              │
    │  ┌────────────────────────────────────────────────────────────────┐ │
    │  │ watchTransformers: [Enhancer_B]                                │ │
    │  │ traceTransformers: [Enhancer_A, Enhancer_C]                    │ │
    │  └────────────────────────────────────────────────────────────────┘ │
    └──────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
    ┌──────────────────────────────────────────────────────────────────────┐
    │                      AdviceListenerManager                           │
    │  ┌────────────────────────────────────────────────────────────────┐ │
    │  │ "com.example.Foo|foo|()V" → [                                  │ │
    │  │     TraceAdviceListener (Session A),                           │ │
    │  │     WatchAdviceListener (Session B),                           │ │
    │  │     TraceAdviceListener (Session C)                            │ │
    │  │ ]                                                              │ │
    │  └────────────────────────────────────────────────────────────────┘ │
    └──────────────────────────────────────────────────────────────────────┘
                                        │
                                        │ foo() 被调用
                                        ▼
    ┌──────────────────────────────────────────────────────────────────────┐
    │                          SpyImpl.atEnter()                           │
    │                                                                      │
    │  for (listener : [TraceListener_A, WatchListener_B, TraceListener_C])│
    │      if (!skipAdviceListener(listener))                              │
    │          listener.before(...)   // 每个都会执行                       │
    │                                                                      │
    └──────────────────────────────────────────────────────────────────────┘
                                        │
              ┌─────────────────────────┼─────────────────────────┐
              │                         │                         │
              ▼                         ▼                         ▼
    ┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
    │  结果 → Client A │     │  结果 → Client B │     │  结果 → CLI C    │
    │  (trace 输出)    │     │  (watch 输出)    │     │  (trace 输出)    │
    └──────────────────┘     └──────────────────┘     └──────────────────┘
```

---

## 总结

Arthas MCP 工具系统的核心设计理念：

1. **注解驱动**：通过 `@Tool` 和 `@ToolParam` 注解声明工具，自动生成 JSON Schema
2. **自动扫描**：启动时自动扫描指定包，注册所有工具
3. **协议适配**：通过 `ToolCallback` 接口适配 MCP 协议，工具实现与协议解耦
4. **类型转换**：自动处理 JSON ↔ Java 对象的转换
5. **灵活执行**：支持同步和异步两种执行模式
6. **上下文传递**：通过 `ToolContext` 传递执行上下文
7. **统一错误处理**：所有异常统一包装和处理

这种设计使得：
- ✅ 开发新工具非常简单（只需要添加注解）
- ✅ 工具定义和实现在一起（易于维护）
- ✅ 自动生成 MCP 协议所需的元数据（无需手动配置）
- ✅ 工具实现与协议解耦（可以轻松适配其他协议）
- ✅ 类型安全（编译时检查）

---

**文档结束**