/*
 * Copyright 2024-2024 the original author or authors.
 */

package com.taobao.arthas.mcp.server.protocol.client;

import com.taobao.arthas.mcp.server.tool.DefaultToolCallbackProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * MCP Client 测试启动类
 * <p>
 * 用于测试反向连接功能
 * <p>
 * 使用方式：
 * 1. 设置环境变量：
 * export ARTHAS_MCP_CLIENT_SERVER_URL=http://localhost:8080/mcp
 * export ARTHAS_MCP_CLIENT_AUTH_TOKEN=your-token
 * <p>
 * 2. 运行测试：
 * java -cp arthas-mcp-server.jar com.taobao.arthas.mcp.server.protocol.client.McpClientBootstrap
 *
 * @author Arthas Team
 */
public class McpClientBootstrap {

    private static final Logger logger = LoggerFactory.getLogger(McpClientBootstrap.class);

    public static void main(String[] args) {
        logger.info("========================================");
        logger.info("  Arthas MCP Client Bootstrap");
        logger.info("========================================");

        // 从环境变量获取配置
        String serverUrl = System.getenv(McpClientConfig.ENV_SERVER_URL);
        if (serverUrl == null || serverUrl.isEmpty()) {
            // 默认测试地址
            serverUrl = "http://localhost:8080/mcp";
            logger.warn("Environment variable {} not set, using default: {}", McpClientConfig.ENV_SERVER_URL, serverUrl);
        }

        String authToken = System.getenv(McpClientConfig.ENV_AUTH_TOKEN);
        if (authToken == null || authToken.isEmpty()) {
            logger.info("No auth token configured");
        }

        try {
            // 创建工具提供者
            DefaultToolCallbackProvider toolProvider = new DefaultToolCallbackProvider();
            // 设置测试工具路径
            toolProvider.setToolBasePackage("com.taobao.arthas.mcp.server.tool.function");
            // 创建客户端
            ArthasMcpClient client = ArthasMcpClient.create(serverUrl).authToken(authToken)
                    .reconnectEnabled(true).heartbeatEnabled(true).heartbeatInterval(30000)
                    .connectTimeout(10000).requestTimeout(30000).toolCallbackProvider(toolProvider).build();

            // 启动客户端
            logger.info("Starting MCP Client...");
            logger.info("Server URL: {}", serverUrl);

            client.start().thenRun(() -> {
                logger.info("========================================");
                logger.info("  MCP Client Connected Successfully!");
                logger.info("========================================");
                logger.info("Server Info: {}", client.getServerInfo());
                logger.info("Client State: {}", client.getState());
            }).exceptionally(ex -> {
                logger.error("Failed to start MCP Client", ex);
                return null;
            });

            // 添加 Shutdown Hook
            Runtime.getRuntime().addShutdownHook(new Thread(() -> {
                logger.info("Shutting down MCP Client...");
                client.stop().join();
                logger.info("MCP Client stopped");
            }));

            // 保持运行
            logger.info("Press Ctrl+C to stop...");
            Thread.currentThread().join();

        } catch (Exception e) {
            logger.error("Bootstrap failed", e);
            System.exit(1);
        }
    }
}
