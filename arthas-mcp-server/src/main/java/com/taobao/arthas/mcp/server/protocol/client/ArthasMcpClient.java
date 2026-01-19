/*
 * Copyright 2024-2024 the original author or authors.
 */

package com.taobao.arthas.mcp.server.protocol.client;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.taobao.arthas.mcp.server.CommandExecutor;
import com.taobao.arthas.mcp.server.protocol.spec.McpSchema;
import com.taobao.arthas.mcp.server.tool.ToolCallback;
import com.taobao.arthas.mcp.server.tool.ToolCallbackProvider;
import com.taobao.arthas.mcp.server.util.Assert;
import com.taobao.arthas.mcp.server.util.JsonParser;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Arthas MCP Client 主类
 * 
 * 主动连接到公网智能体/管控平台，提供 MCP 工具服务
 * 
 * 基于 HTTP/SSE 协议：
 * 1. 通过 HTTP POST 发送 JSON-RPC 请求/响应
 * 2. 通过 SSE 长连接接收管控平台的请求
 * 
 * 复用的组件：
 * - ToolCallbackProvider: 工具提供者
 * - McpSchema: JSON-RPC 消息定义
 * - JsonParser: JSON 序列化
 * - Netty: 网络通信框架
 *
 * 使用示例：
 * <pre>{@code
 * ArthasMcpClient client = ArthasMcpClient.create("http://localhost:8080/mcp")
 *     .authToken("your-token")
 *     .toolCallbackProvider(provider)
 *     .build();
 * 
 * client.start()
 *     .thenRun(() -> System.out.println("Connected!"))
 *     .exceptionally(ex -> { ex.printStackTrace(); return null; });
 * }</pre>
 *
 * @author Arthas Team
 */
public class ArthasMcpClient {

    private static final Logger logger = LoggerFactory.getLogger(ArthasMcpClient.class);

    /**
     * 客户端状态
     */
    public enum State {
        DISCONNECTED,    // 未连接
        CONNECTING,      // 连接中
        CONNECTED,       // 已连接
        RECONNECTING,    // 重连中
        STOPPED          // 已停止
    }

    private final McpClientConfig config;
    private final ObjectMapper objectMapper;
    private final AtomicReference<State> state = new AtomicReference<>(State.DISCONNECTED);

    private McpHttpClient httpClient;
    private McpClientProtocolHandler protocolHandler;
    private HeartbeatManager heartbeatManager;
    private ReconnectStrategy reconnectStrategy;

    private ToolCallbackProvider toolCallbackProvider;
    private CommandExecutor commandExecutor;
    private ScheduledExecutorService scheduler;
    
    private volatile CompletableFuture<Void> startFuture;

    /**
     * 创建客户端（使用环境变量配置）
     */
    public static ArthasMcpClient create() {
        return new ArthasMcpClient(McpClientConfig.fromEnvironment());
    }

    /**
     * 创建客户端（指定服务端 URL）
     */
    public static Builder create(String serverUrl) {
        return new Builder().serverUrl(serverUrl);
    }

    /**
     * 使用指定配置创建客户端
     */
    public ArthasMcpClient(McpClientConfig config) {
        Assert.notNull(config, "config must not be null");
        config.validate();
        
        this.config = config;
        this.objectMapper = JsonParser.getObjectMapper();
        this.reconnectStrategy = new ReconnectStrategy(
                config.getReconnect().getInitialDelay(),
                config.getReconnect().getMaxDelay(),
                config.getReconnect().getMultiplier()
        );
    }

    /**
     * 设置工具提供者
     */
    public ArthasMcpClient toolCallbackProvider(ToolCallbackProvider provider) {
        this.toolCallbackProvider = provider;
        return this;
    }

    /**
     * 设置命令执行器
     */
    public ArthasMcpClient commandExecutor(CommandExecutor executor) {
        this.commandExecutor = executor;
        return this;
    }

    /**
     * 启动客户端
     */
    public CompletableFuture<Void> start() {
        if (!state.compareAndSet(State.DISCONNECTED, State.CONNECTING)) {
            State currentState = state.get();
            if (currentState == State.CONNECTED) {
                return CompletableFuture.completedFuture(null);
            }
            return failedFuture(new IllegalStateException("Cannot start in state: " + currentState));
        }

        startFuture = new CompletableFuture<>();
        
        logger.info("Starting Arthas MCP Client...");
        logger.info("Server URL: {}", config.getServerUrl());

        try {
            // 初始化调度器
            scheduler = Executors.newScheduledThreadPool(2, r -> {
                Thread t = new Thread(r, "mcp-client-scheduler");
                t.setDaemon(true);
                return t;
            });

            // 初始化协议处理器
            protocolHandler = new McpClientProtocolHandler(config, objectMapper, commandExecutor);
            
            // 注册工具
            if (toolCallbackProvider != null) {
                ToolCallback[] callbacks = toolCallbackProvider.getToolCallbacks();
                protocolHandler.registerToolCallbacks(callbacks);
                logger.info("Registered {} tools", callbacks != null ? callbacks.length : 0);
            }

            // 初始化 HTTP 客户端
            httpClient = new McpHttpClient(config, objectMapper);
            protocolHandler.setHttpClient(httpClient);

            // 设置消息处理器
            httpClient.setMessageHandler(protocolHandler::handleMessage);
            
            // 设置连接丢失处理器
            httpClient.setConnectionLostHandler(this::onConnectionLost);

            // 初始化并连接
            httpClient.init()
                    .thenCompose(v -> connect())
                    .thenRun(() -> {
                        state.set(State.CONNECTED);
                        startFuture.complete(null);
                        logger.info("Arthas MCP Client started successfully");
                    })
                    .exceptionally(ex -> {
                        logger.error("Failed to start Arthas MCP Client", ex);
                        state.set(State.DISCONNECTED);
                        startFuture.completeExceptionally(ex);
                        return null;
                    });

        } catch (Exception e) {
            state.set(State.DISCONNECTED);
            startFuture.completeExceptionally(e);
        }

        return startFuture;
    }

    /**
     * 执行连接流程
     */
    private CompletableFuture<Void> connect() {
        logger.info("Connecting to server...");

        // 1. 建立 SSE 连接
        return httpClient.connectSse()
                // 2. 发送 initialize 请求
                .thenCompose(v -> protocolHandler.sendInitialize())
                // 3. 发送 initialized 通知
                .thenCompose(result -> protocolHandler.sendInitialized())
                // 4. 启动心跳
                .thenRun(this::startHeartbeat)
                .thenRun(() -> {
                    reconnectStrategy.reset();
                    logger.info("Connection established successfully");
                });
    }

    /**
     * 启动心跳
     */
    private void startHeartbeat() {
        if (!config.getHeartbeat().isEnabled()) {
            return;
        }

        if (heartbeatManager != null) {
            heartbeatManager.stop();
        }

        heartbeatManager = new HeartbeatManager(
                config.getHeartbeat().getInterval(),
                config.getHeartbeat().getTimeout(),
                scheduler
        );

        heartbeatManager.start(
                // 发送心跳
                () -> protocolHandler.sendPing()
                        .thenRun(() -> {
                            // ping 成功，更新 lastPongTime
                            heartbeatManager.onPong();
                        })
                        .exceptionally(ex -> {
                            logger.warn("Heartbeat failed: {}", ex.getMessage());
                            return null;
                        }),
                // 心跳超时
                () -> {
                    logger.warn("Heartbeat timeout, triggering reconnect");
                    onConnectionLost();
                }
        );

        logger.debug("Heartbeat started, interval: {}ms", config.getHeartbeat().getInterval());
    }

    /**
     * 处理连接丢失
     */
    private void onConnectionLost() {
        State currentState = state.get();
        if (currentState == State.STOPPED || currentState == State.RECONNECTING) {
            return;
        }

        logger.warn("Connection lost, current state: {}", currentState);

        if (!config.getReconnect().isEnabled()) {
            state.set(State.DISCONNECTED);
            return;
        }

        if (state.compareAndSet(State.CONNECTED, State.RECONNECTING) ||
                state.compareAndSet(State.CONNECTING, State.RECONNECTING)) {
            scheduleReconnect();
        }
    }

    /**
     * 调度重连
     */
    private void scheduleReconnect() {
        if (state.get() != State.RECONNECTING) {
            return;
        }

        long delay = reconnectStrategy.getNextDelay();
        int attempt = reconnectStrategy.getAttemptCount();
        
        logger.info("Scheduling reconnect attempt {} in {}ms", attempt, delay);

        scheduler.schedule(() -> {
            if (state.get() != State.RECONNECTING) {
                return;
            }

            logger.info("Attempting reconnect (attempt {})", attempt);

            // 停止心跳
            if (heartbeatManager != null) {
                heartbeatManager.stop();
            }

            // 重置协议状态
            protocolHandler.reset();

            // 重新连接
            connect()
                    .thenRun(() -> {
                        state.set(State.CONNECTED);
                        logger.info("Reconnected successfully after {} attempts", attempt);
                    })
                    .exceptionally(ex -> {
                        logger.warn("Reconnect attempt {} failed: {}", attempt, ex.getMessage());
                        if (state.get() == State.RECONNECTING) {
                            scheduleReconnect();
                        }
                        return null;
                    });
        }, delay, TimeUnit.MILLISECONDS);
    }

    /**
     * 停止客户端
     */
    public CompletableFuture<Void> stop() {
        State currentState = state.getAndSet(State.STOPPED);
        if (currentState == State.STOPPED) {
            return CompletableFuture.completedFuture(null);
        }

        logger.info("Stopping Arthas MCP Client...");

        // 停止心跳
        if (heartbeatManager != null) {
            heartbeatManager.stop();
        }

        // 关闭 HTTP 客户端
        CompletableFuture<Void> closeFuture = httpClient != null ? 
                httpClient.close() : CompletableFuture.completedFuture(null);

        return closeFuture.thenRun(() -> {
            // 关闭调度器
            if (scheduler != null && !scheduler.isShutdown()) {
                scheduler.shutdown();
                try {
                    if (!scheduler.awaitTermination(5, TimeUnit.SECONDS)) {
                        scheduler.shutdownNow();
                    }
                } catch (InterruptedException e) {
                    scheduler.shutdownNow();
                    Thread.currentThread().interrupt();
                }
            }
            logger.info("Arthas MCP Client stopped");
        });
    }

    /**
     * 获取当前状态
     */
    public State getState() {
        return state.get();
    }

    /**
     * 是否已连接
     */
    public boolean isConnected() {
        return state.get() == State.CONNECTED && 
                httpClient != null && httpClient.isSseConnected();
    }

    /**
     * 获取配置
     */
    public McpClientConfig getConfig() {
        return config;
    }

    /**
     * 获取服务端信息
     */
    public McpSchema.Implementation getServerInfo() {
        return protocolHandler != null ? protocolHandler.getServerInfo() : null;
    }

    /**
     * Builder 模式
     */
    public static class Builder {
        private McpClientConfig.Builder configBuilder = McpClientConfig.builder();
        private ToolCallbackProvider toolCallbackProvider;
        private CommandExecutor commandExecutor;

        public Builder serverUrl(String serverUrl) {
            configBuilder.serverUrl(serverUrl);
            return this;
        }

        public Builder authToken(String authToken) {
            configBuilder.authToken(authToken);
            return this;
        }

        public Builder reconnectEnabled(boolean enabled) {
            configBuilder.reconnectEnabled(enabled);
            return this;
        }

        public Builder heartbeatEnabled(boolean enabled) {
            configBuilder.heartbeatEnabled(enabled);
            return this;
        }

        public Builder heartbeatInterval(long interval) {
            configBuilder.heartbeatInterval(interval);
            return this;
        }

        public Builder connectTimeout(long timeout) {
            configBuilder.connectTimeout(timeout);
            return this;
        }

        public Builder requestTimeout(long timeout) {
            configBuilder.requestTimeout(timeout);
            return this;
        }

        public Builder clientName(String name) {
            configBuilder.clientName(name);
            return this;
        }

        public Builder clientVersion(String version) {
            configBuilder.clientVersion(version);
            return this;
        }

        public Builder toolCallbackProvider(ToolCallbackProvider provider) {
            this.toolCallbackProvider = provider;
            return this;
        }

        public Builder commandExecutor(CommandExecutor executor) {
            this.commandExecutor = executor;
            return this;
        }

        public ArthasMcpClient build() {
            ArthasMcpClient client = new ArthasMcpClient(configBuilder.build());
            if (toolCallbackProvider != null) {
                client.toolCallbackProvider(toolCallbackProvider);
            }
            if (commandExecutor != null) {
                client.commandExecutor(commandExecutor);
            }
            return client;
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