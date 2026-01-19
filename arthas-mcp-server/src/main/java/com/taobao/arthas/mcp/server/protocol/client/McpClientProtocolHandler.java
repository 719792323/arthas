/*
 * Copyright 2024-2024 the original author or authors.
 */

package com.taobao.arthas.mcp.server.protocol.client;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.taobao.arthas.mcp.server.CommandExecutor;
import com.taobao.arthas.mcp.server.session.ArthasCommandContext;
import com.taobao.arthas.mcp.server.session.ArthasCommandSessionManager;
import com.taobao.arthas.mcp.server.protocol.spec.McpSchema;
import com.taobao.arthas.mcp.server.protocol.spec.ProtocolVersions;
import com.taobao.arthas.mcp.server.tool.ToolCallback;
import com.taobao.arthas.mcp.server.tool.ToolContext;
import com.taobao.arthas.mcp.server.util.Assert;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.*;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;

/**
 * MCP 协议处理器（客户端版本，HTTP/SSE 模式）
 * 
 * 复用的组件：
 * - McpSchema.Tool: 工具定义
 * - McpSchema.JSONRPCRequest/Response: JSON-RPC 消息格式
 * - ToolCallback: 工具执行回调
 * - ObjectMapper: JSON 序列化
 * 
 * 处理的请求（来自管控平台）：
 * - tools/list: 返回可用工具列表
 * - tools/call: 执行指定工具
 * - ping: 心跳响应
 * 
 * 发送的请求（到管控平台）：
 * - initialize: 连接建立后发送
 * - notifications/initialized: initialize 成功后发送
 * - ping: 心跳
 *
 * @author Arthas Team
 */
public class McpClientProtocolHandler {

    private static final Logger logger = LoggerFactory.getLogger(McpClientProtocolHandler.class);

    /**
     * ToolContext 中 commandContext 的 key
     */
    public static final String TOOL_CONTEXT_COMMAND_CONTEXT_KEY = "commandContext";

    private final McpClientConfig config;
    private final ObjectMapper objectMapper;
    private final CommandExecutor commandExecutor;
    private final Map<String, ToolCallback> toolCallbacks = new ConcurrentHashMap<>();
    
    // 会话管理器，用于支持异步命令执行（如 dashboard）
    private final ArthasCommandSessionManager commandSessionManager;
    
    // 客户端模式下使用固定的 session ID（一个客户端实例对应一个 session）
    private static final String CLIENT_SESSION_ID = "mcp-client-session";
    
    private McpHttpClient httpClient;
    
    // 服务端信息（initialize 后获取）
    private volatile McpSchema.Implementation serverInfo;
    private volatile McpSchema.ServerCapabilities serverCapabilities;
    private volatile String negotiatedProtocolVersion;
    
    // 协议状态
    private volatile boolean initialized = false;

    public McpClientProtocolHandler(McpClientConfig config, ObjectMapper objectMapper, CommandExecutor commandExecutor) {
        Assert.notNull(config, "config must not be null");
        Assert.notNull(objectMapper, "objectMapper must not be null");
        
        this.config = config;
        this.objectMapper = objectMapper;
        this.commandExecutor = commandExecutor;
        
        // 初始化会话管理器，支持异步命令执行
        if (commandExecutor != null) {
            this.commandSessionManager = new ArthasCommandSessionManager(commandExecutor);
        } else {
            this.commandSessionManager = null;
        }
    }

    /**
     * 设置 HTTP 客户端
     */
    public void setHttpClient(McpHttpClient httpClient) {
        this.httpClient = httpClient;
    }

    /**
     * 注册工具回调
     */
    public void registerToolCallback(String name, ToolCallback callback) {
        Assert.hasText(name, "name must not be empty");
        Assert.notNull(callback, "callback must not be null");
        
        toolCallbacks.put(name, callback);
        logger.debug("Registered tool callback: {}", name);
    }

    /**
     * 批量注册工具回调
     */
    public void registerToolCallbacks(ToolCallback[] callbacks) {
        if (callbacks != null) {
            for (ToolCallback callback : callbacks) {
                if (callback != null && callback.getToolDefinition() != null) {
                    registerToolCallback(callback.getToolDefinition().getName(), callback);
                }
            }
        }
    }

    /**
     * 处理收到的消息
     */
    public void handleMessage(McpSchema.JSONRPCMessage message) {
        if (message instanceof McpSchema.JSONRPCRequest) {
            handleRequest((McpSchema.JSONRPCRequest) message);
        } else if (message instanceof McpSchema.JSONRPCNotification) {
            handleNotification((McpSchema.JSONRPCNotification) message);
        } else if (message instanceof McpSchema.JSONRPCResponse) {
            // 响应通常由 pending requests 处理，这里作为 fallback
            logger.debug("Received unexpected response: {}", message);
        }
    }

    /**
     * 处理 JSON-RPC 请求
     */
    private void handleRequest(McpSchema.JSONRPCRequest request) {
        String method = request.getMethod();
        Object id = request.getId();
        
        logger.debug("Handling request: method={}, id={}", method, id);
        
        CompletableFuture<Object> resultFuture;
        
        switch (method) {
            case McpSchema.METHOD_TOOLS_LIST:
                resultFuture = handleToolsList(request);
                break;
            case McpSchema.METHOD_TOOLS_CALL:
                resultFuture = handleToolsCall(request);
                break;
            case McpSchema.METHOD_PING:
                resultFuture = CompletableFuture.completedFuture(Collections.emptyMap());
                break;
            default:
                sendErrorResponse(id, McpSchema.ErrorCodes.METHOD_NOT_FOUND, 
                        "Method not supported: " + method, null);
                return;
        }
        
        resultFuture
                .thenAccept(result -> sendSuccessResponse(id, result))
                .exceptionally(ex -> {
                    sendErrorResponse(id, McpSchema.ErrorCodes.INTERNAL_ERROR, 
                            ex.getMessage(), null);
                    return null;
                });
    }

    /**
     * 处理 tools/list 请求
     */
    private CompletableFuture<Object> handleToolsList(McpSchema.JSONRPCRequest request) {
        List<McpSchema.Tool> tools = new ArrayList<>();
        
        for (ToolCallback callback : toolCallbacks.values()) {
            if (callback.getToolDefinition() != null) {
                McpSchema.Tool tool = new McpSchema.Tool(
                        callback.getToolDefinition().getName(),
                        callback.getToolDefinition().getDescription(),
                        callback.getToolDefinition().getInputSchema()
                );
                tools.add(tool);
            }
        }
        
        McpSchema.ListToolsResult result = new McpSchema.ListToolsResult(tools, null);
        logger.debug("Returning {} tools", tools.size());
        
        return CompletableFuture.completedFuture(result);
    }

    /**
     * 处理 tools/call 请求
     */
    private CompletableFuture<Object> handleToolsCall(McpSchema.JSONRPCRequest request) {
        return CompletableFuture.supplyAsync(() -> {
            try {
                McpSchema.CallToolRequest callRequest = objectMapper.convertValue(
                        request.getParams(), McpSchema.CallToolRequest.class);
                
                String toolName = callRequest.getName();
                Map<String, Object> arguments = callRequest.getArguments();
                
                logger.debug("Calling tool: name={}, arguments={}", toolName, arguments);
                
                ToolCallback callback = toolCallbacks.get(toolName);
                if (callback == null) {
                    throw new IllegalArgumentException("Tool not found: " + toolName);
                }
                
                // 构建工具输入
                String toolInput = arguments != null ? 
                        objectMapper.writeValueAsString(arguments) : "{}";
                
                // 构建工具上下文
                // 注意：必须提供非空的上下文，否则 DefaultToolCallback.validateToolContextSupport 会报错
                Map<String, Object> contextMap = new HashMap<>();
                if (callRequest.meta() != null) {
                    contextMap.putAll(callRequest.meta());
                }
                // 添加默认标记，表示这是来自 MCP 客户端的调用
                contextMap.put("_mcp_client_mode", "reverse");
                contextMap.put("_mcp_client_name", config.getClientName());
                
                // 添加 ArthasCommandContext，这是执行 Arthas 命令的关键
                // 使用带会话支持的构造函数，支持 dashboard 等异步命令
                if (commandSessionManager != null) {
                    ArthasCommandSessionManager.CommandSessionBinding binding = 
                            commandSessionManager.getCommandSession(CLIENT_SESSION_ID, null);
                    ArthasCommandContext commandContext = new ArthasCommandContext(commandExecutor, binding);
                    contextMap.put(TOOL_CONTEXT_COMMAND_CONTEXT_KEY, commandContext);
                    logger.debug("Created command context with session support: arthasSessionId={}", 
                            binding.getArthasSessionId());
                } else if (commandExecutor != null) {
                    // 降级：使用无会话支持的上下文（仅支持同步命令）
                    ArthasCommandContext commandContext = new ArthasCommandContext(commandExecutor);
                    contextMap.put(TOOL_CONTEXT_COMMAND_CONTEXT_KEY, commandContext);
                    logger.warn("CommandSessionManager is not available, using temporary mode (async commands may fail)");
                } else {
                    logger.warn("CommandExecutor is not set, tool execution may fail");
                }
                
                ToolContext toolContext = new ToolContext(contextMap);
                
                // 执行工具
                String result = callback.call(toolInput, toolContext);
                
                // 构建响应
                return McpSchema.CallToolResult.builder()
                        .addTextContent(result != null ? result : "")
                        .isError(false)
                        .build();
                
            } catch (Exception e) {
                logger.error("Tool execution failed", e);
                return McpSchema.CallToolResult.builder()
                        .addTextContent("Error: " + e.getMessage())
                        .isError(true)
                        .build();
            }
        });
    }

    /**
     * 处理通知消息
     */
    private void handleNotification(McpSchema.JSONRPCNotification notification) {
        String method = notification.getMethod();
        logger.debug("Handling notification: method={}", method);
        
        // 目前客户端主要接收请求，通知较少
        switch (method) {
            case "notifications/cancelled":
                // 请求被取消
                logger.info("Request cancelled");
                break;
            default:
                logger.debug("Unhandled notification: {}", method);
        }
    }

    /**
     * 发送 initialize 请求
     */
    public CompletableFuture<McpSchema.InitializeResult> sendInitialize() {
        if (httpClient == null) {
            return failedFuture(new IllegalStateException("HTTP client not set"));
        }
        
        // 构建客户端能力
        McpSchema.ClientCapabilities capabilities = McpSchema.ClientCapabilities.builder()
                .build();
        
        // 构建客户端信息
        McpSchema.Implementation clientInfo = new McpSchema.Implementation(
                config.getClientName(),
                config.getClientVersion()
        );
        
        // 构建 initialize 请求参数
        McpSchema.InitializeRequest params = new McpSchema.InitializeRequest(
                ProtocolVersions.MCP_2025_06_18,
                capabilities,
                clientInfo
        );
        
        // 构建请求
        McpSchema.JSONRPCRequest request = new McpSchema.JSONRPCRequest(
                McpSchema.JSONRPC_VERSION,
                McpSchema.METHOD_INITIALIZE,
                httpClient.nextRequestId(),
                params
        );
        
        logger.info("Sending initialize request...");
        
        return httpClient.sendRequest(request)
                .thenApply(response -> {
                    if (response.getError() != null) {
                        throw new RuntimeException("Initialize failed: " + response.getError().getMessage());
                    }
                    
                    McpSchema.InitializeResult result = objectMapper.convertValue(
                            response.getResult(), McpSchema.InitializeResult.class);
                    
                    // 保存服务端信息
                    serverInfo = result.getServerInfo();
                    serverCapabilities = result.getCapabilities();
                    negotiatedProtocolVersion = result.getProtocolVersion();
                    
                    logger.info("Initialize success: server={}, version={}, protocol={}", 
                            serverInfo != null ? serverInfo.getName() : "unknown",
                            serverInfo != null ? serverInfo.getVersion() : "unknown",
                            negotiatedProtocolVersion);
                    
                    return result;
                });
    }

    /**
     * 发送 initialized 通知
     */
    public CompletableFuture<Void> sendInitialized() {
        if (httpClient == null) {
            return failedFuture(new IllegalStateException("HTTP client not set"));
        }
        
        McpSchema.JSONRPCNotification notification = new McpSchema.JSONRPCNotification(
                McpSchema.JSONRPC_VERSION,
                McpSchema.METHOD_NOTIFICATION_INITIALIZED,
                null
        );
        
        return httpClient.sendNotification(notification)
                .thenRun(() -> {
                    initialized = true;
                    logger.info("Sent initialized notification");
                });
    }

    /**
     * 发送 ping 请求
     */
    public CompletableFuture<Void> sendPing() {
        if (httpClient == null) {
            return failedFuture(new IllegalStateException("HTTP client not set"));
        }
        
        McpSchema.JSONRPCRequest request = new McpSchema.JSONRPCRequest(
                McpSchema.JSONRPC_VERSION,
                McpSchema.METHOD_PING,
                httpClient.nextRequestId(),
                null
        );
        
        return httpClient.sendRequest(request)
                .thenAccept(response -> {
                    if (response.getError() != null) {
                        logger.warn("Ping failed: {}", response.getError().getMessage());
                    } else {
                        logger.trace("Ping success");
                    }
                });
    }

    /**
     * 发送成功响应
     */
    private void sendSuccessResponse(Object id, Object result) {
        McpSchema.JSONRPCResponse response = new McpSchema.JSONRPCResponse(
                McpSchema.JSONRPC_VERSION,
                id,
                result,
                null
        );
        
        httpClient.sendResponse(response)
                .exceptionally(ex -> {
                    logger.error("Failed to send response", ex);
                    return null;
                });
    }

    /**
     * 发送错误响应
     */
    private void sendErrorResponse(Object id, int code, String message, Object data) {
        McpSchema.JSONRPCResponse.JSONRPCError error = 
                new McpSchema.JSONRPCResponse.JSONRPCError(code, message, data);
        
        McpSchema.JSONRPCResponse response = new McpSchema.JSONRPCResponse(
                McpSchema.JSONRPC_VERSION,
                id,
                null,
                error
        );
        
        httpClient.sendResponse(response)
                .exceptionally(ex -> {
                    logger.error("Failed to send error response", ex);
                    return null;
                });
    }

    /**
     * 获取服务端信息
     */
    public McpSchema.Implementation getServerInfo() {
        return serverInfo;
    }

    /**
     * 获取服务端能力
     */
    public McpSchema.ServerCapabilities getServerCapabilities() {
        return serverCapabilities;
    }

    /**
     * 获取协商的协议版本
     */
    public String getNegotiatedProtocolVersion() {
        return negotiatedProtocolVersion;
    }

    /**
     * 是否已初始化
     */
    public boolean isInitialized() {
        return initialized;
    }

    /**
     * 获取已注册的工具数量
     */
    public int getToolCount() {
        return toolCallbacks.size();
    }

    /**
     * 重置状态（用于重连）
     */
    public void reset() {
        initialized = false;
        serverInfo = null;
        serverCapabilities = null;
        negotiatedProtocolVersion = null;
        
        // 清理会话（重连时会创建新的会话）
        if (commandSessionManager != null) {
            commandSessionManager.closeCommandSession(CLIENT_SESSION_ID);
            logger.debug("Closed command session on reset");
        }
    }

    /**
     * 创建一个已失败的 CompletableFuture（Java 8 兼容）
     */
    private static <T> CompletableFuture<T> failedFuture(Throwable ex) {
        CompletableFuture<T> future = new CompletableFuture<>();
        future.completeExceptionally(ex);
        return future;
    }
}