/*
 * Copyright 2024-2024 the original author or authors.
 */

package com.taobao.arthas.mcp.server.protocol.client;

import com.taobao.arthas.mcp.server.protocol.spec.McpSchema;

import java.util.concurrent.CompletableFuture;
import java.util.function.Consumer;

/**
 * MCP 传输层抽象接口
 * 
 * 定义传输层的公共操作契约，支持 WebSocket 和 HTTP/SSE 两种传输实现。
 * 协议层（{@link McpClientProtocolHandler}）和客户端层（{@link ArthasMcpClient}）
 * 通过此接口与传输层交互，实现传输方式的灵活切换。
 *
 * @author Arthas Team
 */
public interface McpTransport {

    /**
     * 初始化传输层资源（EventLoopGroup、SslContext 等）
     *
     * @return 初始化完成的 Future
     */
    CompletableFuture<Void> init();

    /**
     * 建立连接
     * <p>
     * 对于 HTTP/SSE 模式：建立 SSE 长连接
     * 对于 WebSocket 模式：建立 WebSocket 连接并完成握手
     *
     * @return 连接建立完成的 Future
     */
    CompletableFuture<Void> connect();

    /**
     * 发送 JSON-RPC 请求并等待响应
     *
     * @param request JSON-RPC 请求
     * @return 包含响应的 Future
     */
    CompletableFuture<McpSchema.JSONRPCResponse> sendRequest(McpSchema.JSONRPCRequest request);

    /**
     * 发送 JSON-RPC 响应
     *
     * @param response JSON-RPC 响应
     * @return 发送完成的 Future
     */
    CompletableFuture<Void> sendResponse(McpSchema.JSONRPCResponse response);

    /**
     * 发送 JSON-RPC 通知
     *
     * @param notification JSON-RPC 通知
     * @return 发送完成的 Future
     */
    CompletableFuture<Void> sendNotification(McpSchema.JSONRPCNotification notification);

    /**
     * 生成下一个请求 ID
     *
     * @return 唯一的请求 ID
     */
    long nextRequestId();

    /**
     * 设置消息处理器
     * <p>
     * 当传输层接收到来自对端的 JSON-RPC 消息（非 pendingRequest 的响应）时，
     * 通过此回调通知上层。
     *
     * @param handler 消息处理回调
     */
    void setMessageHandler(Consumer<McpSchema.JSONRPCMessage> handler);

    /**
     * 设置连接丢失处理器
     * <p>
     * 当传输层检测到连接断开时，通过此回调通知上层触发重连。
     *
     * @param handler 连接丢失回调
     */
    void setConnectionLostHandler(Runnable handler);

    /**
     * 获取当前 Session ID
     *
     * @return Session ID，若尚未分配则返回 null
     */
    String getSessionId();

    /**
     * 设置 Session ID
     *
     * @param sessionId Session ID
     */
    void setSessionId(String sessionId);

    /**
     * 查询连接是否处于活跃状态
     *
     * @return true 表示连接已建立且可收发消息
     */
    boolean isConnected();

    /**
     * 关闭当前连接通道（不销毁传输层资源）
     * <p>
     * 用于重连前关闭旧连接。对于 HTTP/SSE 模式关闭 SSE Channel，
     * 对于 WebSocket 模式关闭 WebSocket Channel。
     */
    void closeChannel();

    /**
     * 关闭传输层并释放所有资源
     *
     * @return 关闭完成的 Future
     */
    CompletableFuture<Void> close();
}
