/*
 * Copyright 2024-2024 the original author or authors.
 */

package com.taobao.arthas.mcp.server.protocol.client.ws;

import io.netty.channel.*;
import io.netty.handler.codec.http.FullHttpResponse;
import io.netty.handler.codec.http.websocketx.*;
import io.netty.handler.timeout.IdleStateEvent;
import io.netty.util.CharsetUtil;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.concurrent.CompletableFuture;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * WebSocket Frame 处理器
 * 
 * <p>负责处理 WebSocket 握手和各种 Frame 类型：
 * <ul>
 *   <li>{@link TextWebSocketFrame}: 接收 JSON-RPC 消息，交给 {@link McpWebSocketClient} 处理</li>
 *   <li>{@link PongWebSocketFrame}: 更新传输层活跃时间戳</li>
 *   <li>{@link CloseWebSocketFrame}: 标记连接断开，触发连接丢失回调</li>
 *   <li>{@link IdleStateEvent}: 读空闲时发送 Ping 进行传输层保活</li>
 * </ul>
 * 
 * <p>心跳保活策略：
 * <ul>
 *   <li>通过 IdleStateHandler 检测读空闲</li>
 *   <li>读空闲时发送 WebSocket PingFrame</li>
 *   <li>连续 {@link #MAX_MISSED_PONGS} 次未收到 Pong 则关闭连接</li>
 * </ul>
 *
 * @author Arthas Team
 */
public class WebSocketFrameHandler extends ChannelInboundHandlerAdapter {

    private static final Logger logger = LoggerFactory.getLogger(WebSocketFrameHandler.class);

    /**
     * 连续未收到 Pong 的最大次数，超过后关闭连接
     */
    private static final int MAX_MISSED_PONGS = 2;

    private final WebSocketClientHandshaker handshaker;
    private final CompletableFuture<Void> handshakeFuture;
    private final McpWebSocketClient client;

    /**
     * 连续未收到 Pong 的计数器
     */
    private final AtomicInteger missedPongs = new AtomicInteger(0);

    public WebSocketFrameHandler(WebSocketClientHandshaker handshaker,
                                 CompletableFuture<Void> handshakeFuture,
                                 McpWebSocketClient client) {
        this.handshaker = handshaker;
        this.handshakeFuture = handshakeFuture;
        this.client = client;
    }

    @Override
    public void channelActive(ChannelHandlerContext ctx) throws Exception {
        logger.debug("Channel 激活，发起 WebSocket 握手...");
        handshaker.handshake(ctx.channel());
        super.channelActive(ctx);
    }

    @Override
    public void channelRead(ChannelHandlerContext ctx, Object msg) throws Exception {
        Channel ch = ctx.channel();

        // 握手阶段：处理 HTTP 响应
        if (!handshaker.isHandshakeComplete()) {
            try {
                handshaker.finishHandshake(ch, (FullHttpResponse) msg);
                logger.info("WebSocket 握手完成");
                client.onHandshakeComplete();
                handshakeFuture.complete(null);
            } catch (WebSocketHandshakeException e) {
                logger.error("WebSocket 握手失败", e);
                handshakeFuture.completeExceptionally(e);
            }
            return;
        }

        // 握手完成后：处理 WebSocket Frame
        if (msg instanceof WebSocketFrame) {
            handleWebSocketFrame(ctx, (WebSocketFrame) msg);
        } else {
            logger.warn("收到非预期消息类型: {}", msg.getClass().getName());
        }
    }

    /**
     * 处理各种 WebSocket Frame 类型
     */
    private void handleWebSocketFrame(ChannelHandlerContext ctx, WebSocketFrame frame) {
        if (frame instanceof TextWebSocketFrame) {
            // 文本帧：JSON-RPC 消息
            String text = ((TextWebSocketFrame) frame).text();
            client.onTextMessage(text);

        } else if (frame instanceof PongWebSocketFrame) {
            // Pong 帧：传输层心跳响应
            missedPongs.set(0);
            logger.trace("收到 Pong");

        } else if (frame instanceof PingWebSocketFrame) {
            // Ping 帧：自动回复 Pong（Netty 的 WebSocketClientProtocolHandler 通常会自动处理，这里作为兜底）
            ctx.channel().writeAndFlush(new PongWebSocketFrame(frame.content().retain()));
            logger.trace("收到 Ping，已回复 Pong");

        } else if (frame instanceof CloseWebSocketFrame) {
            CloseWebSocketFrame closeFrame = (CloseWebSocketFrame) frame;
            logger.info("收到 CloseFrame: statusCode={}, reasonText={}",
                    closeFrame.statusCode(), closeFrame.reasonText());
            ctx.close();
            client.onConnectionLost("收到 CloseFrame: " + closeFrame.statusCode() + " " + closeFrame.reasonText());

        } else {
            logger.debug("收到未处理的帧类型: {}", frame.getClass().getName());
        }
    }

    @Override
    public void userEventTriggered(ChannelHandlerContext ctx, Object evt) throws Exception {
        if (evt instanceof IdleStateEvent) {
            int missed = missedPongs.incrementAndGet();

            if (missed > MAX_MISSED_PONGS) {
                logger.warn("连续 {} 次未收到 Pong，关闭连接触发重连", missed);
                ctx.close();
                client.onConnectionLost("传输层心跳超时：连续 " + missed + " 次未收到 Pong");
                return;
            }

            logger.trace("读空闲，发送 Ping (missed: {})", missed);
            ctx.channel().writeAndFlush(new PingWebSocketFrame());
        }
        super.userEventTriggered(ctx, evt);
    }

    @Override
    public void channelInactive(ChannelHandlerContext ctx) throws Exception {
        logger.info("WebSocket Channel 变为 inactive");

        // 如果握手还没完成就断开了
        if (!handshakeFuture.isDone()) {
            handshakeFuture.completeExceptionally(new RuntimeException("连接在握手完成前断开"));
        }

        client.onConnectionLost("Channel inactive");
        super.channelInactive(ctx);
    }

    @Override
    public void exceptionCaught(ChannelHandlerContext ctx, Throwable cause) {
        logger.error("WebSocket 连接异常", cause);

        if (!handshakeFuture.isDone()) {
            handshakeFuture.completeExceptionally(cause);
        }

        ctx.close();
        client.onConnectionLost("异常: " + cause.getMessage());
    }
}
