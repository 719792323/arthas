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

### 6.1 ToolContext 设计

`ToolContext` 是工具执行的上下文容器，携带执行所需的所有信息：

```java
public class ToolContext {
    private final Map<String, Object> context;
    
    public ToolContext(Map<String, Object> context) {
        this.context = context != null ? context : new HashMap<>();
    }
    
    public Map<String, Object> getContext() {
        return context;
    }
}
```

### 6.2 上下文内容

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

**上下文传递链路**：
```
MCP 请求
  → McpNettyServer 构造 ToolContext
    → DefaultToolCallback 传递给工具方法
      → AbstractArthasTool 提取执行上下文
        → ArthasCommandContext 执行命令
```

---

## 7. 同步与异步执行

### 7.1 同步执行（Sync）

适用于快速返回的命令（如 `jvm`, `memory`, `thread`）：

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

**执行流程**：
```
1. 发送命令到 Arthas
2. 等待命令执行完成
3. 收集所有结果
4. 一次性返回
```

### 7.2 异步执行（Streamable）

适用于长时间运行的命令（如 `trace`, `watch`, `monitor`）：

```java
protected String executeStreamable(ToolContext toolContext, String commandStr, 
                                 Integer expectedResultCount, Integer pollIntervalMs, 
                                 Integer timeoutMs, String successMessage) {
    ToolExecutionContext execContext = null;
    try {
        execContext = new ToolExecutionContext(toolContext, true);
        
        logger.info("Starting streamable execution: {}", commandStr);

        // 1. 异步启动命令（带重试）
        Map<String, Object> asyncResult = executeAsyncWithRetry(execContext, commandStr, timeoutMs);
        if (!isAsyncExecutionStarted(asyncResult)) {
            String errorMessage = asyncResult != null ? String.valueOf(asyncResult.get("error")) : "unknown error";
            return JsonParser.toJson(createErrorResponse("Failed to start command: " + errorMessage));
        }
        
        // 2. 轮询收集结果
        Map<String, Object> results = executeAndCollectResults(
            execContext.getExchange(), 
            execContext.getCommandContext(), 
            expectedResultCount, 
            pollIntervalMs, 
            timeoutMs,
            execContext.getProgressToken()
        );
        
        // 3. 返回收集到的结果
        if (results != null) {
            String message = successMessage != null ? successMessage : "Command execution completed successfully";
            
            if (Boolean.TRUE.equals(results.get("timedOut"))) {
                Integer count = (Integer) results.get("resultCount");
                if (count != null && count > 0) {
                    message = "Command execution ended (Timed out). Captured " + count + " results.";
                } else {
                    message = "Command execution ended (Timed out). No results captured within the time limit.";
                }
            }
            
            return JsonParser.toJson(createCompletedResponse(message, results));
        } else {
            return JsonParser.toJson(createErrorResponse("Command execution failed due to timeout or error limits exceeded"));
        }
        
    } catch (Exception e) {
        logger.error("Error executing streamable command: {}", commandStr, e);
        return JsonParser.toJson(createErrorResponse("Error executing command: " + e.getMessage()));
    } finally {
        if (execContext != null) {
            try {
                // 确保前台任务被及时释放
                execContext.getCommandContext().interruptJob();
            } catch (Exception ignored) {
            }
        }
    }
}
```

**执行流程**：
```
1. 异步启动命令（立即返回）
2. 轮询收集结果
   - 每隔 pollIntervalMs 检查一次
   - 收集到 expectedResultCount 个结果后停止
   - 或者超时后停止
3. 返回收集到的所有结果
```

### 7.3 异步启动重试机制

```java
private static Map<String, Object> executeAsyncWithRetry(ToolExecutionContext execContext, 
                                                         String commandStr, Integer timeoutMs) {
    long maxWaitMs = DEFAULT_ASYNC_START_MAX_WAIT_MS;  // 默认 3 秒
    if (timeoutMs != null && timeoutMs > 0) {
        maxWaitMs = Math.min(maxWaitMs, timeoutMs);
    }

    long deadline = System.currentTimeMillis() + maxWaitMs;
    Map<String, Object> asyncResult = null;

    while (System.currentTimeMillis() < deadline) {
        // 尝试启动异步命令
        asyncResult = execContext.getCommandContext().executeAsync(commandStr);
        
        // 启动成功
        if (isAsyncExecutionStarted(asyncResult)) {
            return asyncResult;
        }

        // 如果是"另一个任务正在运行"错误，则重试
        if (isRetryableAsyncStartError(asyncResult)) {
            try {
                // 中断当前任务
                execContext.getCommandContext().interruptJob();
            } catch (Exception ignored) {
            }
            
            // 等待一段时间后重试
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
```

**重试场景**：
- 错误信息包含 "Another job is running"
- 错误信息包含 "Another command is executing"

**重试策略**：
- 最大重试时间：3 秒（或命令超时时间的较小值）
- 重试间隔：100 毫秒
- 每次重试前先中断当前任务

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