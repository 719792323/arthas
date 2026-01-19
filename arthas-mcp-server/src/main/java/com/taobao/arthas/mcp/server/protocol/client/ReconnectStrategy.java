/*
 * Copyright 2024-2024 the original author or authors.
 */

package com.taobao.arthas.mcp.server.protocol.client;

import java.util.concurrent.atomic.AtomicInteger;

/**
 * 重连策略
 * 
 * 支持指数退避算法：
 * 1. 初始延迟 5 秒
 * 2. 每次失败后延迟翻倍
 * 3. 最大延迟 5 分钟
 *
 * @author Arthas Team
 */
public class ReconnectStrategy {

    private final long initialDelay;   // 初始延迟（毫秒）
    private final long maxDelay;       // 最大延迟（毫秒）
    private final double multiplier;   // 延迟倍数
    
    private final AtomicInteger attemptCount = new AtomicInteger(0);
    private volatile long currentDelay;

    /**
     * 创建重连策略
     *
     * @param initialDelay 初始延迟（毫秒）
     * @param maxDelay     最大延迟（毫秒）
     * @param multiplier   延迟倍数
     */
    public ReconnectStrategy(long initialDelay, long maxDelay, double multiplier) {
        this.initialDelay = initialDelay;
        this.maxDelay = maxDelay;
        this.multiplier = multiplier;
        this.currentDelay = initialDelay;
    }

    /**
     * 使用默认参数创建重连策略
     * 默认：初始 5 秒，最大 5 分钟，倍数 2.0
     */
    public ReconnectStrategy() {
        this(5000, 300000, 2.0);
    }

    /**
     * 获取下次重连延迟
     */
    public synchronized long getNextDelay() {
        attemptCount.incrementAndGet();
        long delay = currentDelay;
        
        // 计算下次延迟
        currentDelay = Math.min((long) (currentDelay * multiplier), maxDelay);
        
        return delay;
    }

    /**
     * 重置计数
     */
    public synchronized void reset() {
        attemptCount.set(0);
        currentDelay = initialDelay;
    }

    /**
     * 获取当前尝试次数
     */
    public int getAttemptCount() {
        return attemptCount.get();
    }

    /**
     * 获取当前延迟
     */
    public long getCurrentDelay() {
        return currentDelay;
    }

    /**
     * 获取初始延迟
     */
    public long getInitialDelay() {
        return initialDelay;
    }

    /**
     * 获取最大延迟
     */
    public long getMaxDelay() {
        return maxDelay;
    }

    /**
     * 获取延迟倍数
     */
    public double getMultiplier() {
        return multiplier;
    }
}
