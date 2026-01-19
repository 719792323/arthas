/*
 * Copyright 2024-2024 the original author or authors.
 */

package com.taobao.arthas.mcp.server.protocol.client;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 心跳管理器
 * 
 * 定期发送心跳，检测连接状态
 * 
 * 功能：
 * 1. 按指定间隔发送心跳
 * 2. 检测心跳超时
 * 3. 超时后触发回调
 *
 * @author Arthas Team
 */
public class HeartbeatManager {

    private static final Logger logger = LoggerFactory.getLogger(HeartbeatManager.class);

    private final long interval;  // 心跳间隔（毫秒）
    private final long timeout;   // 超时时间（毫秒）
    private final ScheduledExecutorService scheduler;
    
    private ScheduledFuture<?> heartbeatTask;
    private final AtomicBoolean running = new AtomicBoolean(false);
    private final AtomicLong lastPongTime = new AtomicLong(0);

    /**
     * 创建心跳管理器
     *
     * @param interval  心跳间隔（毫秒）
     * @param timeout   超时时间（毫秒）
     * @param scheduler 调度器
     */
    public HeartbeatManager(long interval, long timeout, ScheduledExecutorService scheduler) {
        this.interval = interval;
        this.timeout = timeout;
        this.scheduler = scheduler;
    }

    /**
     * 启动心跳
     *
     * @param pingAction    发送心跳的动作
     * @param timeoutAction 超时时的动作
     */
    public void start(Runnable pingAction, Runnable timeoutAction) {
        if (!running.compareAndSet(false, true)) {
            logger.warn("Heartbeat already running");
            return;
        }

        lastPongTime.set(System.currentTimeMillis());
        
        heartbeatTask = scheduler.scheduleAtFixedRate(() -> {
            try {
                // 检查是否超时
                long now = System.currentTimeMillis();
                long elapsed = now - lastPongTime.get();
                
                if (elapsed > interval + timeout) {
                    logger.warn("Heartbeat timeout: {}ms since last pong", elapsed);
                    if (timeoutAction != null) {
                        timeoutAction.run();
                    }
                    return;
                }
                
                // 发送心跳
                logger.trace("Sending heartbeat...");
                if (pingAction != null) {
                    pingAction.run();
                }
                
            } catch (Exception e) {
                logger.error("Heartbeat error", e);
            }
        }, interval, interval, TimeUnit.MILLISECONDS);
        
        logger.debug("Heartbeat started, interval: {}ms, timeout: {}ms", interval, timeout);
    }

    /**
     * 停止心跳
     */
    public void stop() {
        if (!running.compareAndSet(true, false)) {
            return;
        }

        if (heartbeatTask != null) {
            heartbeatTask.cancel(false);
            heartbeatTask = null;
        }
        
        logger.debug("Heartbeat stopped");
    }

    /**
     * 记录收到心跳响应
     */
    public void onPong() {
        lastPongTime.set(System.currentTimeMillis());
        logger.trace("Received pong");
    }

    /**
     * 是否正在运行
     */
    public boolean isRunning() {
        return running.get();
    }

    /**
     * 获取上次收到 pong 的时间戳
     */
    public long getLastPongTime() {
        return lastPongTime.get();
    }
}