package com.taobao.arthas.core.mcp;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.taobao.arthas.core.mcp.tool.util.McpToolUtils;
import com.taobao.arthas.mcp.server.CommandExecutor;
import com.taobao.arthas.mcp.server.protocol.client.ArthasMcpClient;
import com.taobao.arthas.mcp.server.protocol.client.McpClientConfig;
import com.taobao.arthas.mcp.server.protocol.config.McpServerProperties;
import com.taobao.arthas.mcp.server.protocol.config.McpServerProperties.ServerProtocol;
import com.taobao.arthas.mcp.server.protocol.server.McpNettyServer;
import com.taobao.arthas.mcp.server.protocol.server.McpServer;
import com.taobao.arthas.mcp.server.protocol.server.McpStatelessNettyServer;
import com.taobao.arthas.mcp.server.protocol.server.handler.McpHttpRequestHandler;
import com.taobao.arthas.mcp.server.protocol.server.handler.McpStatelessHttpRequestHandler;
import com.taobao.arthas.mcp.server.protocol.server.handler.McpStreamableHttpRequestHandler;
import com.taobao.arthas.mcp.server.protocol.server.transport.NettyStatelessServerTransport;
import com.taobao.arthas.mcp.server.protocol.server.transport.NettyStreamableServerTransportProvider;
import com.taobao.arthas.mcp.server.protocol.spec.McpSchema.Implementation;
import com.taobao.arthas.mcp.server.protocol.spec.McpSchema.ServerCapabilities;
import com.taobao.arthas.mcp.server.protocol.spec.McpStreamableServerTransportProvider;
import com.taobao.arthas.mcp.server.tool.DefaultToolCallbackProvider;
import com.taobao.arthas.mcp.server.tool.ToolCallback;
import com.taobao.arthas.mcp.server.tool.ToolCallbackProvider;
import com.taobao.arthas.mcp.server.util.JsonParser;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.time.Duration;
import java.util.*;
import java.util.stream.Collectors;

/**
 * Arthas MCP Server
 * Used to expose HTTP service after Arthas startup
 * 
 * 支持两种工作模式：
 * 1. Server 模式（默认）：等待 MCP 客户端连接
 * 2. Client 模式（反向连接）：主动连接到管控平台
 * 
 * 通过设置环境变量 ARTHAS_MCP_CLIENT_SERVER_URL 启用 Client 模式
 * 两种模式可以同时启用
 */
public class ArthasMcpServer {
    private static final Logger logger = LoggerFactory.getLogger(ArthasMcpServer.class);

    /**
     * Arthas tool base package in core module
     */
    public static final String ARTHAS_TOOL_BASE_PACKAGE = "com.taobao.arthas.core.mcp.tool.function";

    // ========== Server 模式相关 ==========
    private McpNettyServer streamableServer;
    private McpStatelessNettyServer statelessServer;

    private final String mcpEndpoint;
    private final ServerProtocol protocol;

    private final CommandExecutor commandExecutor;

    private McpHttpRequestHandler unifiedMcpHandler;

    private McpStreamableHttpRequestHandler streamableHandler;

    private McpStatelessHttpRequestHandler statelessHandler;

    public static final String DEFAULT_MCP_ENDPOINT = "/mcp";
    
    // ========== Client 模式相关 ==========
    private ArthasMcpClient mcpClient;
    
    /**
     * 是否只启动 Client 模式（不启动 Server）
     * 设置为 true 时，只有在配置了 ARTHAS_MCP_CLIENT_SERVER_URL 时才会启动
     */
    public static final String ENV_CLIENT_ONLY = "ARTHAS_MCP_CLIENT_ONLY";
    
    public ArthasMcpServer(String mcpEndpoint, CommandExecutor commandExecutor, String protocol) {
        this.mcpEndpoint = mcpEndpoint != null ? mcpEndpoint : DEFAULT_MCP_ENDPOINT;
        this.commandExecutor = commandExecutor;
        
        ServerProtocol resolvedProtocol = ServerProtocol.STREAMABLE;
        if (protocol != null && !protocol.trim().isEmpty()) {
            try {
                resolvedProtocol = ServerProtocol.valueOf(protocol.toUpperCase());
            } catch (IllegalArgumentException e) {
                logger.warn("Invalid MCP protocol: {}. Using default: STREAMABLE", protocol);
            }
        }
        this.protocol = resolvedProtocol;
    }

    public McpHttpRequestHandler getMcpRequestHandler() {
        return unifiedMcpHandler;
    }

    /**
     * Start MCP server
     */
    public void start() {
        try {
            // Register Arthas-specific JSON filter
            com.taobao.arthas.core.mcp.util.McpObjectVOFilter.register();
            
            // 创建工具提供者（Server 和 Client 共用）
            DefaultToolCallbackProvider toolCallbackProvider = new DefaultToolCallbackProvider();
            toolCallbackProvider.setToolBasePackage(ARTHAS_TOOL_BASE_PACKAGE);
            
            ToolCallback[] callbacks = toolCallbackProvider.getToolCallbacks();
            List<ToolCallback> providerToolCallbacks = Arrays.stream(callbacks)
                    .filter(Objects::nonNull)
                    .collect(Collectors.toList());
            
            // 检查是否启用 Client Only 模式
            boolean clientOnly = "true".equalsIgnoreCase(System.getenv(ENV_CLIENT_ONLY));
            
            // 检查是否配置了 Client 模式
            String clientServerUrl = System.getenv(McpClientConfig.ENV_SERVER_URL);
            boolean clientEnabled = clientServerUrl != null && !clientServerUrl.trim().isEmpty();
            
            // 启动 Server 模式（除非设置了 Client Only）
            if (!clientOnly) {
                startServer(toolCallbackProvider, providerToolCallbacks);
            } else {
                logger.info("Server mode disabled (ARTHAS_MCP_CLIENT_ONLY=true)");
            }
            
            // 启动 Client 模式（如果配置了 Server URL）
            if (clientEnabled) {
                startClient(toolCallbackProvider, clientServerUrl);
            }
            
            // 如果两种模式都没启动，则报警告
            if (clientOnly && !clientEnabled) {
                logger.warn("Both Server mode disabled and Client mode not configured. No MCP service started!");
                logger.warn("Set ARTHAS_MCP_CLIENT_SERVER_URL to enable Client mode, or remove ARTHAS_MCP_CLIENT_ONLY to enable Server mode");
            }
            
        } catch (Exception e) {
            logger.error("Failed to start Arthas MCP server", e);
            throw new RuntimeException("Failed to start Arthas MCP server", e);
        }
    }
    
    /**
     * 启动 Server 模式
     */
    private void startServer(DefaultToolCallbackProvider toolCallbackProvider, List<ToolCallback> providerToolCallbacks) {
        logger.info("Starting MCP Server mode...");
        
        McpServerProperties properties = new McpServerProperties.Builder()
                .name("arthas-mcp-server")
                .version("4.1.5")
                .mcpEndpoint(mcpEndpoint)
                .toolChangeNotification(true)
                .resourceChangeNotification(true)
                .promptChangeNotification(true)
                .objectMapper(JsonParser.getObjectMapper())
                .protocol(this.protocol)
                .build();

        unifiedMcpHandler = McpHttpRequestHandler.builder()
                .mcpEndpoint(properties.getMcpEndpoint())
                .objectMapper(properties.getObjectMapper())
                .protocol(properties.getProtocol())
                .build();

        if (properties.getProtocol() == ServerProtocol.STREAMABLE) {
            McpStreamableServerTransportProvider transportProvider = createStreamableHttpTransportProvider(properties);
            streamableHandler = transportProvider.getMcpRequestHandler();
            unifiedMcpHandler.setStreamableHandler(streamableHandler);

            McpServer.StreamableServerNettySpecification streamableServerNettySpecification = McpServer.netty(transportProvider)
                    .serverInfo(new Implementation(properties.getName(), properties.getVersion()))
                    .capabilities(buildServerCapabilities(properties))
                    .instructions(properties.getInstructions())
                    .requestTimeout(properties.getRequestTimeout())
                    .commandExecutor(commandExecutor)
                    .objectMapper(properties.getObjectMapper() != null ? properties.getObjectMapper() : JsonParser.getObjectMapper());

            streamableServerNettySpecification.tools(
                    McpToolUtils.toStreamableToolSpecifications(providerToolCallbacks));

            streamableServer = streamableServerNettySpecification.build();
        } else {
            NettyStatelessServerTransport statelessTransport = createStatelessHttpTransport(properties);
            statelessHandler = statelessTransport.getMcpRequestHandler();
            unifiedMcpHandler.setStatelessHandler(statelessHandler);

            McpServer.StatelessServerNettySpecification statelessServerNettySpecification = McpServer.netty(statelessTransport)
                    .serverInfo(new Implementation(properties.getName(), properties.getVersion()))
                    .capabilities(buildServerCapabilities(properties))
                    .instructions(properties.getInstructions())
                    .requestTimeout(properties.getRequestTimeout())
                    .commandExecutor(commandExecutor)
                    .objectMapper(properties.getObjectMapper() != null ? properties.getObjectMapper() : JsonParser.getObjectMapper());

            statelessServerNettySpecification.tools(
                    McpToolUtils.toStatelessToolSpecifications(providerToolCallbacks));

            statelessServer = statelessServerNettySpecification.build();
        }

        logger.info("Arthas MCP Server mode started successfully");
        logger.info("- MCP Endpoint: {}", properties.getMcpEndpoint());
        logger.info("- Transport mode: {}", properties.getProtocol());
        logger.info("- Available tools: {}", providerToolCallbacks.size());
        logger.info("- Server ready to accept connections");
    }
    
    /**
     * 启动 Client 模式（反向连接）
     * 
     * 该模式主动连接到公网智能体/管控平台，适用于：
     * - 内网穿透场景
     * - 统一管控场景
     * - 云端调试场景
     */
    private void startClient(DefaultToolCallbackProvider toolCallbackProvider, String serverUrl) {
        logger.info("Starting MCP Client mode (Reverse Connection)...");
        logger.info("- Target Server URL: {}", serverUrl);
        
        try {
            // 从环境变量获取其他配置
            String authToken = System.getenv(McpClientConfig.ENV_AUTH_TOKEN);
            
            // 构建客户端
            ArthasMcpClient.Builder clientBuilder = ArthasMcpClient.create(serverUrl)
                    .toolCallbackProvider(toolCallbackProvider)
                    .commandExecutor(commandExecutor)  // 传入命令执行器，用于执行 Arthas 命令
                    .reconnectEnabled(getBoolEnv(McpClientConfig.ENV_RECONNECT_ENABLED, true))
                    .heartbeatEnabled(getBoolEnv(McpClientConfig.ENV_HEARTBEAT_ENABLED, true));
            
            if (authToken != null && !authToken.isEmpty()) {
                clientBuilder.authToken(authToken);
                logger.info("- Auth Token: configured");
            }
            
            // 配置超时
            long heartbeatInterval = getLongEnv(McpClientConfig.ENV_HEARTBEAT_INTERVAL, 30000);
            long connectTimeout = getLongEnv(McpClientConfig.ENV_CONNECT_TIMEOUT, 10000);
            long requestTimeout = getLongEnv(McpClientConfig.ENV_REQUEST_TIMEOUT, 30000);
            
            clientBuilder.heartbeatInterval(heartbeatInterval)
                    .connectTimeout(connectTimeout)
                    .requestTimeout(requestTimeout);
            
            mcpClient = clientBuilder.build();
            
            // 异步启动客户端
            mcpClient.start()
                    .thenRun(() -> {
                        logger.info("========================================");
                        logger.info("  MCP Client Connected Successfully!");
                        logger.info("========================================");
                        logger.info("- Server Info: {}", mcpClient.getServerInfo());
                        logger.info("- Client State: {}", mcpClient.getState());
                    })
                    .exceptionally(ex -> {
                        logger.error("Failed to start MCP Client, will retry if reconnect enabled", ex);
                        return null;
                    });
            
            logger.info("MCP Client mode initialization completed");
            
        } catch (Exception e) {
            logger.error("Failed to initialize MCP Client", e);
            // Client 模式失败不影响 Server 模式
        }
    }
    
    /**
     * 获取布尔类型环境变量
     */
    private boolean getBoolEnv(String name, boolean defaultValue) {
        String value = System.getenv(name);
        if (value == null || value.isEmpty()) {
            return defaultValue;
        }
        return "true".equalsIgnoreCase(value);
    }
    
    /**
     * 获取 long 类型环境变量
     */
    private long getLongEnv(String name, long defaultValue) {
        String value = System.getenv(name);
        if (value == null || value.isEmpty()) {
            return defaultValue;
        }
        try {
            return Long.parseLong(value);
        } catch (NumberFormatException e) {
            logger.warn("Invalid value for {}: {}, using default: {}", name, value, defaultValue);
            return defaultValue;
        }
    }
    
    /**
     * Default keep-alive interval for MCP server (15 seconds)
     */
    public static final Duration DEFAULT_KEEP_ALIVE_INTERVAL = Duration.ofSeconds(15);
    
    /**
     * Create HTTP transport provider
     */
    private NettyStreamableServerTransportProvider createStreamableHttpTransportProvider(McpServerProperties properties) {
        return NettyStreamableServerTransportProvider.builder()
                .mcpEndpoint(properties.getMcpEndpoint())
                .objectMapper(properties.getObjectMapper() != null ? properties.getObjectMapper() : new ObjectMapper())
                .keepAliveInterval(DEFAULT_KEEP_ALIVE_INTERVAL)
                .build();
    }

    private NettyStatelessServerTransport createStatelessHttpTransport(McpServerProperties properties) {
        return NettyStatelessServerTransport.builder()
                .mcpEndpoint(properties.getMcpEndpoint())
                .objectMapper(properties.getObjectMapper() != null ? properties.getObjectMapper() : new ObjectMapper())
                .build();
    }

    private ServerCapabilities buildServerCapabilities(McpServerProperties properties) {
        return ServerCapabilities.builder()
                .prompts(new ServerCapabilities.PromptCapabilities(properties.isPromptChangeNotification()))
                .resources(new ServerCapabilities.ResourceCapabilities(properties.isResourceSubscribe(), properties.isResourceChangeNotification()))
                .tools(new ServerCapabilities.ToolCapabilities(properties.isToolChangeNotification()))
                .build();
    }

    public void stop() {
        logger.info("Stopping Arthas MCP server...");
        try {
            // 停止 Client 模式
            if (mcpClient != null) {
                logger.debug("Shutting down MCP Client");
                try {
                    mcpClient.stop().get(10, java.util.concurrent.TimeUnit.SECONDS);
                    logger.info("MCP Client stopped successfully");
                } catch (Exception e) {
                    logger.warn("Failed to stop MCP Client gracefully", e);
                }
            }
            
            // 停止 Server 模式
            if (unifiedMcpHandler != null) {
                logger.debug("Shutting down unified MCP handler");
                unifiedMcpHandler.closeGracefully().get();
                logger.info("Unified MCP handler stopped successfully");
            }

            if (streamableServer != null) {
                logger.debug("Shutting down streamable server");
                streamableServer.closeGracefully().get();
                logger.info("Streamable server stopped successfully");
            }

            if (statelessServer != null) {
                logger.debug("Shutting down stateless server");
                statelessServer.closeGracefully().get();
                logger.info("Stateless server stopped successfully");
            }
            
            logger.info("Arthas MCP server stopped completely");
        } catch (Exception e) {
            logger.error("Failed to stop Arthas MCP server gracefully", e);
        }
    }
    
    /**
     * 获取 MCP Client 实例（用于状态查询等）
     */
    public ArthasMcpClient getMcpClient() {
        return mcpClient;
    }
    
    /**
     * 检查 Client 模式是否已连接
     */
    public boolean isClientConnected() {
        return mcpClient != null && mcpClient.isConnected();
    }
}
