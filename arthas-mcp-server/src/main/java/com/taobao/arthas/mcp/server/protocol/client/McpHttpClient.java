/*
 * Copyright 2024-2024 the original author or authors.
 */

package com.taobao.arthas.mcp.server.protocol.client;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.taobao.arthas.mcp.server.protocol.spec.McpSchema;
import com.taobao.arthas.mcp.server.util.Assert;
import io.netty.bootstrap.Bootstrap;
import io.netty.buffer.ByteBuf;
import io.netty.buffer.Unpooled;
import io.netty.channel.*;
import io.netty.channel.nio.NioEventLoopGroup;
import io.netty.channel.socket.SocketChannel;
import io.netty.channel.socket.nio.NioSocketChannel;
import io.netty.handler.codec.http.*;
import io.netty.handler.ssl.SslContext;
import io.netty.handler.ssl.SslContextBuilder;
import io.netty.handler.ssl.util.InsecureTrustManagerFactory;
import io.netty.handler.timeout.IdleStateEvent;
import io.netty.handler.timeout.IdleStateHandler;
import io.netty.util.CharsetUtil;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import javax.net.ssl.SSLException;
import java.io.BufferedReader;
import java.io.StringReader;
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
 * 基于 Netty 的 HTTP/SSE 客户端
 * 
 * 复用 Arthas 现有的 Netty 组件实现：
 * - Bootstrap: 客户端启动器
 * - NioEventLoopGroup: 事件循环组
 * - HttpClientCodec: HTTP 编解码器
 * - SslContext: SSL 上下文（https:// 支持）
 * 
 * 支持两种请求模式：
 * 1. POST JSON-RPC 请求到 /mcp 端点
 * 2. 建立 SSE 连接接收服务端推送的请求
 *
 * @author Arthas Team
 */
public class McpHttpClient {

    private static final Logger logger = LoggerFactory.getLogger(McpHttpClient.class);

    private final McpClientConfig config;
    private final ObjectMapper objectMapper;
    private final URI serverUri;
    private final String host;
    private final int port;
    private final boolean ssl;

    private EventLoopGroup eventLoopGroup;
    private SslContext sslContext;

    private final AtomicBoolean closed = new AtomicBoolean(false);
    private final AtomicLong requestIdGenerator = new AtomicLong(1);
    
    // SSE 连接相关
    private Channel sseChannel;
    private volatile boolean sseConnected = false;
    private Consumer<McpSchema.JSONRPCMessage> messageHandler;
    private Runnable connectionLostHandler;
    
    // 存储 SSE session ID
    private volatile String sessionId;

    // 请求响应映射
    private final Map<Object, CompletableFuture<McpSchema.JSONRPCResponse>> pendingRequests = new ConcurrentHashMap<>();

    public McpHttpClient(McpClientConfig config, ObjectMapper objectMapper) {
        Assert.notNull(config, "config must not be null");
        Assert.notNull(objectMapper, "objectMapper must not be null");
        
        this.config = config;
        this.objectMapper = objectMapper;
        
        try {
            this.serverUri = new URI(config.getServerUrl());
            this.host = serverUri.getHost();
            this.port = serverUri.getPort() == -1 ? 
                    (serverUri.getScheme().equals("https") ? 443 : 80) : serverUri.getPort();
            this.ssl = "https".equalsIgnoreCase(serverUri.getScheme());
        } catch (URISyntaxException e) {
            throw new IllegalArgumentException("Invalid server URL: " + config.getServerUrl(), e);
        }
    }

    /**
     * 初始化客户端
     */
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
            logger.info("MCP HTTP Client initialized, server: {}:{}, ssl: {}", host, port, ssl);
        } catch (SSLException e) {
            future.completeExceptionally(new RuntimeException("Failed to initialize SSL context", e));
        }
        
        return future;
    }

    /**
     * 建立 SSE 连接监听服务端请求
     * 
     * SSE 连接用于接收来自管控平台的工具调用请求
     */
    public CompletableFuture<Void> connectSse() {
        CompletableFuture<Void> future = new CompletableFuture<>();
        
        if (closed.get()) {
            future.completeExceptionally(new IllegalStateException("Client is closed"));
            return future;
        }
        
        // 关闭旧的 SSE 连接（如果存在）
        if (sseChannel != null && sseChannel.isOpen()) {
            logger.warn("Closing existing SSE channel before creating new connection");
            sseChannel.close();
            sseChannel = null;
        }
        
        try {
            Bootstrap bootstrap = new Bootstrap();
            bootstrap.group(eventLoopGroup)
                    .channel(NioSocketChannel.class)
                    .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, (int) config.getConnectTimeout())
                    .option(ChannelOption.SO_KEEPALIVE, true)
                    .handler(new ChannelInitializer<SocketChannel>() {
                        @Override
                        protected void initChannel(SocketChannel ch) {
                            ChannelPipeline pipeline = ch.pipeline();
                            
                            if (ssl && sslContext != null) {
                                pipeline.addLast("ssl", sslContext.newHandler(ch.alloc(), host, port));
                            }
                            
                            pipeline.addLast("http-codec", new HttpClientCodec());
                            // 注意：SSE 是流式的，不能使用 HttpObjectAggregator，否则会等到连接关闭才收到消息
                            // SSE 读超时设置为 2.5 倍心跳间隔，避免在边界上超时（服务端心跳可能有延迟）
                            long sseReadTimeout = (long) (config.getHeartbeat().getInterval() * 2.5 / 1000);
                            pipeline.addLast("idle-state", new IdleStateHandler(
                                    sseReadTimeout, 0, 0, TimeUnit.SECONDS));
                            pipeline.addLast("sse-handler", new SseHandler(future));
                        }
                    });

            ChannelFuture connectFuture = bootstrap.connect(host, port);
            connectFuture.addListener((ChannelFutureListener) f -> {
                if (f.isSuccess()) {
                    sseChannel = f.channel();
                    sendSseRequest(sseChannel);
                    logger.info("SSE connection established to {}:{}", host, port);
                } else {
                    future.completeExceptionally(f.cause());
                    logger.error("Failed to establish SSE connection to {}:{}", host, port, f.cause());
                }
            });
            
        } catch (Exception e) {
            future.completeExceptionally(e);
        }
        
        return future;
    }

    /**
     * 发送 SSE GET 请求
     */
    private void sendSseRequest(Channel channel) {
        String path = serverUri.getPath();
        if (path == null || path.isEmpty()) {
            path = "/mcp";
        }
        
        // 添加 session id 参数（如果有）
        String uri = path;
        if (sessionId != null) {
            uri += "?sessionId=" + sessionId;
        }
        
        DefaultFullHttpRequest request = new DefaultFullHttpRequest(
                HttpVersion.HTTP_1_1, HttpMethod.GET, uri);
        
        request.headers().set(HttpHeaderNames.HOST, host + ":" + port);
        request.headers().set(HttpHeaderNames.ACCEPT, "text/event-stream");
        request.headers().set(HttpHeaderNames.CACHE_CONTROL, "no-cache");
        request.headers().set(HttpHeaderNames.CONNECTION, HttpHeaderValues.KEEP_ALIVE);
        
        // 添加认证头
        if (config.getAuthToken() != null && !config.getAuthToken().isEmpty()) {
            request.headers().set(HttpHeaderNames.AUTHORIZATION, "Bearer " + config.getAuthToken());
        }
        
        channel.writeAndFlush(request);
        logger.debug("Sent SSE request to {}", uri);
    }

    /**
     * 发送 JSON-RPC 请求并等待响应
     */
    public CompletableFuture<McpSchema.JSONRPCResponse> sendRequest(McpSchema.JSONRPCRequest request) {
        CompletableFuture<McpSchema.JSONRPCResponse> future = new CompletableFuture<>();
        
        if (closed.get()) {
            future.completeExceptionally(new IllegalStateException("Client is closed"));
            return future;
        }
        
        // 存储 pending 请求
        pendingRequests.put(request.getId(), future);
        
        // 设置超时
        eventLoopGroup.schedule(() -> {
            CompletableFuture<McpSchema.JSONRPCResponse> pending = pendingRequests.remove(request.getId());
            if (pending != null && !pending.isDone()) {
                pending.completeExceptionally(new RuntimeException("Request timeout: " + request.getMethod()));
            }
        }, config.getRequestTimeout(), TimeUnit.MILLISECONDS);
        
        // 发送 HTTP POST 请求
        sendHttpPost(request, future);
        
        return future;
    }

    /**
     * 发送 JSON-RPC 响应
     */
    public CompletableFuture<Void> sendResponse(McpSchema.JSONRPCResponse response) {
        CompletableFuture<Void> future = new CompletableFuture<>();
        
        if (closed.get()) {
            future.completeExceptionally(new IllegalStateException("Client is closed"));
            return future;
        }
        
        sendHttpPost(response, future);
        
        return future;
    }

    /**
     * 发送 JSON-RPC 通知
     */
    public CompletableFuture<Void> sendNotification(McpSchema.JSONRPCNotification notification) {
        CompletableFuture<Void> future = new CompletableFuture<>();
        
        if (closed.get()) {
            future.completeExceptionally(new IllegalStateException("Client is closed"));
            return future;
        }
        
        sendHttpPost(notification, future);
        
        return future;
    }

    /**
     * 发送 HTTP POST 请求
     */
    private <T> void sendHttpPost(Object message, CompletableFuture<T> future) {
        try {
            Bootstrap bootstrap = new Bootstrap();
            bootstrap.group(eventLoopGroup)
                    .channel(NioSocketChannel.class)
                    .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, (int) config.getConnectTimeout())
                    .handler(new ChannelInitializer<SocketChannel>() {
                        @Override
                        protected void initChannel(SocketChannel ch) {
                            ChannelPipeline pipeline = ch.pipeline();
                            
                            if (ssl && sslContext != null) {
                                pipeline.addLast("ssl", sslContext.newHandler(ch.alloc(), host, port));
                            }
                            
                            pipeline.addLast("http-codec", new HttpClientCodec());
                            pipeline.addLast("http-aggregator", new HttpObjectAggregator(65536));
                            pipeline.addLast("response-handler", new HttpResponseHandler<>(message, future));
                        }
                    });

            ChannelFuture connectFuture = bootstrap.connect(host, port);
            connectFuture.addListener((ChannelFutureListener) f -> {
                if (f.isSuccess()) {
                    sendPostRequest(f.channel(), message);
                } else {
                    future.completeExceptionally(f.cause());
                }
            });
            
        } catch (Exception e) {
            future.completeExceptionally(e);
        }
    }

    /**
     * 构造并发送 POST 请求
     */
    private void sendPostRequest(Channel channel, Object message) {
        try {
            String jsonBody = objectMapper.writeValueAsString(message);
            ByteBuf content = Unpooled.copiedBuffer(jsonBody, CharsetUtil.UTF_8);
            
            String path = serverUri.getPath();
            if (path == null || path.isEmpty()) {
                path = "/mcp";
            }
            
            // 添加 session id 参数（如果有）
            String uri = path;
            if (sessionId != null) {
                uri += "?sessionId=" + sessionId;
            }
            
            logger.info("Sending POST request to {}, sessionId={}", uri, sessionId);
            
            DefaultFullHttpRequest request = new DefaultFullHttpRequest(
                    HttpVersion.HTTP_1_1, HttpMethod.POST, uri, content);
            
            request.headers().set(HttpHeaderNames.HOST, host + ":" + port);
            request.headers().set(HttpHeaderNames.CONTENT_TYPE, "application/json");
            request.headers().set(HttpHeaderNames.ACCEPT, "application/json, text/event-stream");
            request.headers().set(HttpHeaderNames.CONTENT_LENGTH, content.readableBytes());
            request.headers().set(HttpHeaderNames.CONNECTION, HttpHeaderValues.CLOSE);
            
            // 添加认证头
            if (config.getAuthToken() != null && !config.getAuthToken().isEmpty()) {
                request.headers().set(HttpHeaderNames.AUTHORIZATION, "Bearer " + config.getAuthToken());
            }
            
            // 添加 session id 头（如果有）
            if (sessionId != null) {
                request.headers().set("Mcp-Session-Id", sessionId);
            }
            
            channel.writeAndFlush(request);
            logger.debug("Sent HTTP POST request: {}", jsonBody);
            
        } catch (Exception e) {
            logger.error("Failed to send HTTP POST request", e);
            channel.close();
        }
    }

    /**
     * 生成请求 ID
     */
    public long nextRequestId() {
        return requestIdGenerator.getAndIncrement();
    }

    /**
     * 设置消息处理器
     */
    public void setMessageHandler(Consumer<McpSchema.JSONRPCMessage> handler) {
        this.messageHandler = handler;
    }

    /**
     * 设置连接丢失处理器
     */
    public void setConnectionLostHandler(Runnable handler) {
        this.connectionLostHandler = handler;
    }

    /**
     * 关闭 SSE Channel
     */
    public void closeSseChannel() {
        if (sseChannel != null && sseChannel.isOpen()) {
            logger.info("Closing SSE channel");
            sseChannel.close();
            sseChannel = null;
            sseConnected = false;
        }
    }

    /**
     * 关闭客户端
     */
    public CompletableFuture<Void> close() {
        CompletableFuture<Void> future = new CompletableFuture<>();
        
        if (closed.compareAndSet(false, true)) {
            logger.info("Closing MCP HTTP Client...");
            
            // 取消所有 pending 请求
            pendingRequests.values().forEach(f -> 
                    f.completeExceptionally(new RuntimeException("Client closed")));
            pendingRequests.clear();
            
            // 关闭 SSE 连接
            if (sseChannel != null && sseChannel.isOpen()) {
                sseChannel.close();
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

    /**
     * SSE 是否已连接
     */
    public boolean isSseConnected() {
        return sseConnected && sseChannel != null && sseChannel.isActive();
    }

    /**
     * 获取 Session ID
     */
    public String getSessionId() {
        return sessionId;
    }

    /**
     * 设置 Session ID
     */
    public void setSessionId(String sessionId) {
        this.sessionId = sessionId;
    }

    /**
     * SSE 响应处理器
     */
    private class SseHandler extends SimpleChannelInboundHandler<HttpObject> {
        
        private final CompletableFuture<Void> connectFuture;
        private StringBuilder sseBuffer = new StringBuilder();
        private boolean headersReceived = false;

        public SseHandler(CompletableFuture<Void> connectFuture) {
            this.connectFuture = connectFuture;
        }

        @Override
        protected void channelRead0(ChannelHandlerContext ctx, HttpObject msg) throws Exception {
            if (msg instanceof HttpResponse) {
                HttpResponse response = (HttpResponse) msg;
                
                if (response.status().code() != 200) {
                    connectFuture.completeExceptionally(
                            new RuntimeException("SSE connection failed: " + response.status()));
                    ctx.close();
                    return;
                }
                
                // 检查是否为 SSE 响应
                String contentType = response.headers().get(HttpHeaderNames.CONTENT_TYPE);
                if (contentType == null || !contentType.contains("text/event-stream")) {
                    connectFuture.completeExceptionally(
                            new RuntimeException("Expected SSE response, got: " + contentType));
                    ctx.close();
                    return;
                }
                
                // 获取 session id
                String newSessionId = response.headers().get("Mcp-Session-Id");
                if (newSessionId != null) {
                    sessionId = newSessionId;
                    logger.info("Received session ID from SSE response: {}", sessionId);
                } else {
                    logger.warn("No Mcp-Session-Id in SSE response headers!");
                }
                
                headersReceived = true;
                sseConnected = true;
                logger.info("SSE headers received, completing connectSse future, sessionId={}", sessionId);
                connectFuture.complete(null);
            }
            
            if (msg instanceof HttpContent) {
                HttpContent content = (HttpContent) msg;
                String chunk = content.content().toString(CharsetUtil.UTF_8);
                sseBuffer.append(chunk);
                
                // 处理完整的 SSE 事件
                processSseEvents();
                
                if (content instanceof LastHttpContent) {
                    logger.debug("SSE stream ended");
                    sseConnected = false;
                    if (connectionLostHandler != null) {
                        connectionLostHandler.run();
                    }
                }
            }
        }

        private void processSseEvents() {
            String buffer = sseBuffer.toString();
            int lastEventEnd = 0;
            
            // 查找完整的事件（以双换行结束）
            int eventEnd;
            while ((eventEnd = buffer.indexOf("\n\n", lastEventEnd)) != -1) {
                String event = buffer.substring(lastEventEnd, eventEnd);
                lastEventEnd = eventEnd + 2;
                
                try {
                    processEvent(event);
                } catch (Exception e) {
                    logger.error("Failed to process SSE event: {}", event, e);
                }
            }
            
            // 保留未处理的部分
            if (lastEventEnd > 0) {
                sseBuffer = new StringBuilder(buffer.substring(lastEventEnd));
            }
        }

        private void processEvent(String event) throws Exception {
            String eventType = "message";
            String data = null;
            
            BufferedReader reader = new BufferedReader(new StringReader(event));
            String line;
            StringBuilder dataBuilder = new StringBuilder();
            
            while ((line = reader.readLine()) != null) {
                if (line.startsWith("event:")) {
                    eventType = line.substring(6).trim();
                } else if (line.startsWith("data:")) {
                    if (dataBuilder.length() > 0) {
                        dataBuilder.append("\n");
                    }
                    dataBuilder.append(line.substring(5).trim());
                } else if (line.startsWith("id:")) {
                    // 处理事件 ID（可选）
                    logger.trace("SSE event id: {}", line.substring(3).trim());
                }
            }
            
            data = dataBuilder.toString();
            
            if (data.isEmpty()) {
                return;
            }
            
            logger.debug("Received SSE event: type={}, data={}", eventType, data);
            
            if ("message".equals(eventType) && messageHandler != null) {
                McpSchema.JSONRPCMessage message = McpSchema.deserializeJsonRpcMessage(objectMapper, data);
                
                // 检查是否是我们发送请求的响应
                if (message instanceof McpSchema.JSONRPCResponse) {
                    McpSchema.JSONRPCResponse response = (McpSchema.JSONRPCResponse) message;
                    CompletableFuture<McpSchema.JSONRPCResponse> pending = pendingRequests.remove(response.getId());
                    if (pending != null) {
                        pending.complete(response);
                        return;
                    }
                }
                
                // 否则交给消息处理器
                messageHandler.accept(message);
            }
        }

        @Override
        public void userEventTriggered(ChannelHandlerContext ctx, Object evt) throws Exception {
            if (evt instanceof IdleStateEvent) {
                // 心跳超时，触发重连
                logger.warn("SSE connection idle, closing...");
                ctx.close();
            }
            super.userEventTriggered(ctx, evt);
        }

        @Override
        public void exceptionCaught(ChannelHandlerContext ctx, Throwable cause) {
            logger.error("SSE connection error", cause);
            if (!connectFuture.isDone()) {
                connectFuture.completeExceptionally(cause);
            }
            sseConnected = false;
            ctx.close();
            
            if (connectionLostHandler != null) {
                connectionLostHandler.run();
            }
        }

        @Override
        public void channelInactive(ChannelHandlerContext ctx) throws Exception {
            logger.info("SSE channel inactive");
            sseConnected = false;
            if (connectionLostHandler != null && headersReceived) {
                connectionLostHandler.run();
            }
            super.channelInactive(ctx);
        }
    }

    /**
     * HTTP 响应处理器（用于 POST 请求）
     */
    private class HttpResponseHandler<T> extends SimpleChannelInboundHandler<FullHttpResponse> {
        
        private final Object request;
        private final CompletableFuture<T> future;

        public HttpResponseHandler(Object request, CompletableFuture<T> future) {
            this.request = request;
            this.future = future;
        }

        @Override
        protected void channelRead0(ChannelHandlerContext ctx, FullHttpResponse response) throws Exception {
            try {
                String contentType = response.headers().get(HttpHeaderNames.CONTENT_TYPE);
                String body = response.content().toString(CharsetUtil.UTF_8);
                
                logger.debug("Received HTTP response: status={}, contentType={}, body={}", 
                        response.status(), contentType, body);
                
                // 获取 session id
                String newSessionId = response.headers().get("Mcp-Session-Id");
                if (newSessionId != null) {
                    sessionId = newSessionId;
                }
                
                if (response.status().code() >= 400) {
                    future.completeExceptionally(
                            new RuntimeException("HTTP error: " + response.status() + ", body: " + body));
                    return;
                }
                
                // 处理响应
                if (contentType != null && contentType.contains("text/event-stream")) {
                    // SSE 响应，解析事件
                    processSseResponse(body);
                } else if (contentType != null && contentType.contains("application/json")) {
                    // JSON 响应
                    if (!body.isEmpty()) {
                        McpSchema.JSONRPCMessage message = McpSchema.deserializeJsonRpcMessage(objectMapper, body);
                        if (message instanceof McpSchema.JSONRPCResponse) {
                            @SuppressWarnings("unchecked")
                            T result = (T) message;
                            future.complete(result);
                        } else {
                            future.complete(null);
                        }
                    } else {
                        future.complete(null);
                    }
                } else {
                    // 202 Accepted 或空响应
                    future.complete(null);
                }
                
            } finally {
                ctx.close();
            }
        }

        private void processSseResponse(String body) {
            // 解析 SSE 格式的响应
            String[] events = body.split("\n\n");
            for (String event : events) {
                if (event.trim().isEmpty()) continue;
                
                try {
                    String data = null;
                    for (String line : event.split("\n")) {
                        if (line.startsWith("data:")) {
                            data = line.substring(5).trim();
                        }
                    }
                    
                    if (data != null && !data.isEmpty()) {
                        McpSchema.JSONRPCMessage message = McpSchema.deserializeJsonRpcMessage(objectMapper, data);
                        if (message instanceof McpSchema.JSONRPCResponse) {
                            @SuppressWarnings("unchecked")
                            T result = (T) message;
                            future.complete(result);
                            return;
                        }
                    }
                } catch (Exception e) {
                    logger.error("Failed to parse SSE response event", e);
                }
            }
            
            // 没有找到响应
            future.complete(null);
        }

        @Override
        public void exceptionCaught(ChannelHandlerContext ctx, Throwable cause) {
            logger.error("HTTP request failed", cause);
            future.completeExceptionally(cause);
            ctx.close();
        }
    }
}
