/*
 * Copyright 2024-2024 the original author or authors.
 */

package com.taobao.arthas.mcp.server.protocol.client.ws;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.taobao.arthas.mcp.server.protocol.client.McpClientConfig;
import com.taobao.arthas.mcp.server.protocol.client.McpTransport;
import com.taobao.arthas.mcp.server.protocol.spec.McpSchema;
import com.taobao.arthas.mcp.server.util.Assert;
import io.netty.bootstrap.Bootstrap;
import io.netty.channel.*;
import io.netty.channel.nio.NioEventLoopGroup;
import io.netty.channel.socket.SocketChannel;
import io.netty.channel.socket.nio.NioSocketChannel;
import io.netty.handler.codec.http.DefaultHttpHeaders;
import io.netty.handler.codec.http.HttpClientCodec;
import io.netty.handler.codec.http.HttpObjectAggregator;
import io.netty.handler.codec.http.websocketx.TextWebSocketFrame;
import io.netty.handler.codec.http.websocketx.WebSocketClientHandshakerFactory;
import io.netty.handler.codec.http.websocketx.WebSocketClientHandshaker;
import io.netty.handler.codec.http.websocketx.WebSocketVersion;
import io.netty.handler.ssl.SslContext;
import io.netty.handler.ssl.SslContextBuilder;
import io.netty.handler.ssl.util.InsecureTrustManagerFactory;
import io.netty.handler.timeout.IdleStateHandler;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import javax.net.ssl.SSLException;
import java.net.URI;
import java.net.URISyntaxException;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Consumer;

/**
 * 基于 Netty 的 WebSocket 传输层客户端
 * 
 * 通过单一 WebSocket 连接实现双向全双工通信，替代 HTTP/SSE 双通道模式。
 * 
 * <p>Pipeline 结构：
 * <pre>
 * SslHandler(可选) → HttpClientCodec → HttpObjectAggregator
 *     → WebSocketClientProtocolHandler → IdleStateHandler → WebSocketFrameHandler
 * </pre>
 * 
 * <p>核心职责：
 * <ul>
 *   <li>基于 Netty Bootstrap 创建 WebSocket 客户端连接</li>
 *   <li>将 JSON-RPC 消息序列化为 TextWebSocketFrame 发送</li>
 *   <li>通过 pendingRequests 映射表管理请求-响应匹配</li>
 *   <li>支持 ws:// 和 wss:// URL</li>
 *   <li>支持认证头和 sessionId 参数</li>
 *   <li>集成 WebSocket Ping/Pong 传输层心跳</li>
 * </ul>
 *
 * @author Arthas Team
 */
public class McpWebSocketClient implements McpTransport {

    private static final Logger logger = LoggerFactory.getLogger(McpWebSocketClient.class);

    private final McpClientConfig config;
    private final ObjectMapper objectMapper;
    private final URI serverUri;
    private final String host;
    private final int port;
    private final boolean ssl;

    private EventLoopGroup eventLoopGroup;
    private SslContext sslContext;
    private volatile Channel channel;

    private final AtomicBoolean closed = new AtomicBoolean(false);
    private final AtomicLong requestIdGenerator = new AtomicLong(1);
    private volatile boolean connected = false;

    private Consumer<McpSchema.JSONRPCMessage> messageHandler;
    private Runnable connectionLostHandler;
    private volatile String sessionId;

    // 请求响应映射（key 统一使用 String 类型，避免 Long/Integer 类型不匹配问题）
    private final Map<String, CompletableFuture<McpSchema.JSONRPCResponse>> pendingRequests = new ConcurrentHashMap<>();

    public McpWebSocketClient(McpClientConfig config, ObjectMapper objectMapper) {
        Assert.notNull(config, "config must not be null");
        Assert.notNull(objectMapper, "objectMapper must not be null");

        this.config = config;
        this.objectMapper = objectMapper;

        try {
            // 将 http/https URL 自动转换为 ws/wss
            String url = config.getServerUrl();
            if (url.startsWith("http://")) {
                url = "ws://" + url.substring(7);
            } else if (url.startsWith("https://")) {
                url = "wss://" + url.substring(8);
            }
            this.serverUri = new URI(url);
            this.host = serverUri.getHost();
            this.port = serverUri.getPort() == -1 ?
                    ("wss".equalsIgnoreCase(serverUri.getScheme()) ? 443 : 80) : serverUri.getPort();
            this.ssl = "wss".equalsIgnoreCase(serverUri.getScheme());
        } catch (URISyntaxException e) {
            throw new IllegalArgumentException("Invalid server URL: " + config.getServerUrl(), e);
        }
    }

    @Override
    public CompletableFuture<Void> init() {
        CompletableFuture<Void> future = new CompletableFuture<>();

        try {
            eventLoopGroup = new NioEventLoopGroup(2);

            if (ssl) {
                sslContext = SslContextBuilder.forClient()
                        .trustManager(InsecureTrustManagerFactory.INSTANCE)
                        .build();
            }

            future.complete(null);
            logger.info("MCP WebSocket Client 初始化完成, server: {}:{}, ssl: {}", host, port, ssl);
        } catch (SSLException e) {
            future.completeExceptionally(new RuntimeException("初始化 SSL 上下文失败", e));
        }

        return future;
    }

    @Override
    public CompletableFuture<Void> connect() {
        CompletableFuture<Void> future = new CompletableFuture<>();

        if (closed.get()) {
            future.completeExceptionally(new IllegalStateException("Client is closed"));
            return future;
        }

        // 关闭旧的连接
        if (channel != null && channel.isOpen()) {
            logger.warn("关闭已存在的 WebSocket Channel，准备创建新连接");
            channel.close();
            channel = null;
        }

        try {
            // 构建握手 URI（携带 sessionId）
            String handshakePath = serverUri.getPath();
            if (handshakePath == null || handshakePath.isEmpty()) {
                handshakePath = "/mcp";
            }
            // sessionId 必须放在 query 参数中，不能拼到 path 里
            // 因为 URI 构造函数会对 path 中的 ? = 等字符进行 URL 编码
            String query = sessionId != null ? "sessionId=" + sessionId : null;

            URI handshakeUri = new URI(serverUri.getScheme(), null, host, port, handshakePath, query, null);

            // 构建自定义 HTTP 头（用于认证和 sessionId）
            DefaultHttpHeaders customHeaders = new DefaultHttpHeaders();
            if (config.getAuthToken() != null && !config.getAuthToken().isEmpty()) {
                customHeaders.add("Authorization", "Bearer " + config.getAuthToken());
            }
            // 在握手请求头中也携带 sessionId，确保管控平台可以通过 URI 参数或 HTTP 头获取
            if (sessionId != null) {
                customHeaders.add("Mcp-Session-Id", sessionId);
            }

            // 创建 WebSocket 握手器
            WebSocketClientHandshaker handshaker = WebSocketClientHandshakerFactory.newHandshaker(
                    handshakeUri,
                    WebSocketVersion.V13,
                    null,  // subprotocol
                    true,  // allowExtensions
                    customHeaders,
                    65536  // maxFramePayloadLength
            );

            // 传输层心跳：读空闲检测（2.5 倍心跳间隔）
            long heartbeatInterval = config.getHeartbeat().getInterval();
            long readIdleSeconds = (long) (heartbeatInterval * 2.5 / 1000);

            Bootstrap bootstrap = new Bootstrap();
            bootstrap.group(eventLoopGroup)
                    .channel(NioSocketChannel.class)
                    .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, (int) config.getConnectTimeout())
                    .option(ChannelOption.SO_KEEPALIVE, true)
                    .handler(new ChannelInitializer<SocketChannel>() {
                        @Override
                        protected void initChannel(SocketChannel ch) {
                            ChannelPipeline pipeline = ch.pipeline();

                            // SSL（可选）
                            if (ssl && sslContext != null) {
                                pipeline.addLast("ssl", sslContext.newHandler(ch.alloc(), host, port));
                            }

                            // HTTP 编解码（WebSocket 握手阶段需要）
                            pipeline.addLast("http-codec", new HttpClientCodec());
                            pipeline.addLast("http-aggregator", new HttpObjectAggregator(65536));

                            // 读空闲检测（传输层心跳）
                            pipeline.addLast("idle-state", new IdleStateHandler(
                                    readIdleSeconds, 0, 0, TimeUnit.SECONDS));

                            // WebSocket Frame 处理器
                            pipeline.addLast("ws-handler", new WebSocketFrameHandler(
                                    handshaker, future, McpWebSocketClient.this));
                        }
                    });

            ChannelFuture connectFuture = bootstrap.connect(host, port);
            connectFuture.addListener((ChannelFutureListener) f -> {
                if (f.isSuccess()) {
                    channel = f.channel();
                    logger.info("TCP 连接建立到 {}:{}，正在进行 WebSocket 握手...", host, port);
                } else {
                    future.completeExceptionally(f.cause());
                    logger.error("TCP 连接失败: {}:{}", host, port, f.cause());
                }
            });

        } catch (Exception e) {
            future.completeExceptionally(e);
        }

        return future;
    }

    @Override
    public CompletableFuture<McpSchema.JSONRPCResponse> sendRequest(McpSchema.JSONRPCRequest request) {
        CompletableFuture<McpSchema.JSONRPCResponse> future = new CompletableFuture<>();

        if (closed.get() || !isConnected()) {
            future.completeExceptionally(new IllegalStateException("WebSocket not connected"));
            return future;
        }

        // 存储 pending 请求（key 统一转为 String，避免 Long vs Integer 类型不匹配）
        String requestKey = String.valueOf(request.getId());
        pendingRequests.put(requestKey, future);

        // 设置超时
        eventLoopGroup.schedule(() -> {
            CompletableFuture<McpSchema.JSONRPCResponse> pending = pendingRequests.remove(requestKey);
            if (pending != null && !pending.isDone()) {
                pending.completeExceptionally(new RuntimeException("请求超时: " + request.getMethod()));
            }
        }, config.getRequestTimeout(), TimeUnit.MILLISECONDS);

        // 序列化并发送
        writeMessage(request, future);

        return future;
    }

    @Override
    public CompletableFuture<Void> sendResponse(McpSchema.JSONRPCResponse response) {
        CompletableFuture<Void> future = new CompletableFuture<>();

        if (closed.get() || !isConnected()) {
            future.completeExceptionally(new IllegalStateException("WebSocket not connected"));
            return future;
        }

        writeMessage(response, future);

        return future;
    }

    @Override
    public CompletableFuture<Void> sendNotification(McpSchema.JSONRPCNotification notification) {
        CompletableFuture<Void> future = new CompletableFuture<>();

        if (closed.get() || !isConnected()) {
            future.completeExceptionally(new IllegalStateException("WebSocket not connected"));
            return future;
        }

        writeMessage(notification, future);

        return future;
    }

    /**
     * 将消息序列化为 JSON 并封装为 TextWebSocketFrame 写入 Channel
     */
    private <T> void writeMessage(Object message, CompletableFuture<T> future) {
        try {
            String json = objectMapper.writeValueAsString(message);
            logger.debug("发送 WebSocket 消息: {}", json);

            channel.writeAndFlush(new TextWebSocketFrame(json))
                    .addListener((ChannelFutureListener) f -> {
                        if (f.isSuccess()) {
                            // 对于非请求类消息，直接完成 future
                            if (!(message instanceof McpSchema.JSONRPCRequest)) {
                                @SuppressWarnings("unchecked")
                                CompletableFuture<Void> voidFuture = (CompletableFuture<Void>) future;
                                voidFuture.complete(null);
                            }
                        } else {
                            future.completeExceptionally(f.cause());
                        }
                    });
        } catch (Exception e) {
            logger.error("序列化消息失败", e);
            future.completeExceptionally(e);
        }
    }

    @Override
    public long nextRequestId() {
        return requestIdGenerator.getAndIncrement();
    }

    @Override
    public void setMessageHandler(Consumer<McpSchema.JSONRPCMessage> handler) {
        this.messageHandler = handler;
    }

    @Override
    public void setConnectionLostHandler(Runnable handler) {
        this.connectionLostHandler = handler;
    }

    @Override
    public String getSessionId() {
        return sessionId;
    }

    @Override
    public void setSessionId(String sessionId) {
        this.sessionId = sessionId;
    }

    @Override
    public boolean isConnected() {
        return connected && channel != null && channel.isActive();
    }

    @Override
    public void closeChannel() {
        if (channel != null && channel.isOpen()) {
            logger.info("关闭 WebSocket Channel");
            channel.close();
            channel = null;
            connected = false;
        }
    }

    @Override
    public CompletableFuture<Void> close() {
        CompletableFuture<Void> future = new CompletableFuture<>();

        if (closed.compareAndSet(false, true)) {
            logger.info("关闭 MCP WebSocket Client...");

            // 取消所有 pending 请求
            pendingRequests.values().forEach(f ->
                    f.completeExceptionally(new RuntimeException("Client closed")));
            pendingRequests.clear();

            // 关闭 WebSocket 连接
            if (channel != null && channel.isOpen()) {
                channel.close();
            }

            // 关闭事件循环组
            if (eventLoopGroup != null) {
                eventLoopGroup.shutdownGracefully().addListener(f -> {
                    if (f.isSuccess()) {
                        future.complete(null);
                    } else {
                        future.completeExceptionally(f.cause());
                    }
                });
            } else {
                future.complete(null);
            }
        } else {
            future.complete(null);
        }

        return future;
    }

    // ========== 内部回调方法（供 WebSocketFrameHandler 调用） ==========

    /**
     * WebSocket 握手完成回调
     */
    void onHandshakeComplete() {
        connected = true;
        logger.info("WebSocket 连接建立成功, server: {}:{}, ssl: {}, sessionId: {}", host, port, ssl, sessionId);
    }

    /**
     * 收到文本消息回调
     */
    void onTextMessage(String text) {
        logger.debug("收到 WebSocket 消息: {}", text);

        try {
            McpSchema.JSONRPCMessage message = McpSchema.deserializeJsonRpcMessage(objectMapper, text);

            // 检查是否是 pending 请求的响应
            if (message instanceof McpSchema.JSONRPCResponse) {
                McpSchema.JSONRPCResponse response = (McpSchema.JSONRPCResponse) message;
                // 统一转为 String 匹配，因为 Jackson 反序列化 JSON 中的数字 id 可能得到 Integer，
                // 而 sendRequest 存入的是 Long，两者 equals() 不相等
                String responseKey = String.valueOf(response.getId());
                CompletableFuture<McpSchema.JSONRPCResponse> pending = pendingRequests.remove(responseKey);
                if (pending != null) {
                    pending.complete(response);
                    return;
                }
            }

            // 否则交给消息处理器
            if (messageHandler != null) {
                messageHandler.accept(message);
            }
        } catch (Exception e) {
            logger.error("解析 WebSocket 消息失败: {}", text, e);
        }
    }

    /**
     * 连接丢失回调
     */
    void onConnectionLost(String reason) {
        connected = false;
        logger.warn("WebSocket 连接断开: {}", reason);

        if (connectionLostHandler != null) {
            connectionLostHandler.run();
        }
    }

    /**
     * 获取 pendingRequests（供 WebSocketFrameHandler 访问）
     */
    Map<String, CompletableFuture<McpSchema.JSONRPCResponse>> getPendingRequests() {
        return pendingRequests;
    }

    /**
     * 获取 ObjectMapper
     */
    ObjectMapper getObjectMapper() {
        return objectMapper;
    }
}
