/*
 * Copyright 2024-2024 the original author or authors.
 */

package com.taobao.arthas.mcp.server.protocol.client;

import java.util.concurrent.ConcurrentHashMap;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * 任务阶段追踪器
 * <p>
 * 管理 taskId+stageId 的执行状态，防止同一 taskId+stageId 组合被重复执行。
 * 使用 ConcurrentHashMap 保证线程安全。
 *
 * @author Arthas Team
 */
public class TaskStageTracker {

    private static final Logger logger = LoggerFactory.getLogger(TaskStageTracker.class);

    /**
     * 存储正在执行中的 taskId+stageId 组合
     * key 格式为 "taskId:stageId"，value 固定为 Boolean.TRUE
     */
    private final ConcurrentHashMap<String, Boolean> executingStages = new ConcurrentHashMap<>();

    /**
     * 尝试获取指定 taskId+stageId 组合的执行权
     * <p>
     * 使用 putIfAbsent 原子操作，保证在多线程环境下只有一个线程能成功获取执行权。
     *
     * @param taskId  任务ID
     * @param stageId 阶段ID
     * @return true 表示成功获取执行权（该组合之前不在执行中），false 表示该组合已在执行中
     */
    public boolean tryAcquire(String taskId, String stageId) {
        String key = buildKey(taskId, stageId);
        Boolean previous = executingStages.putIfAbsent(key, Boolean.TRUE);
        if (previous != null) {
            logger.warn("任务阶段已在执行中，拒绝重复执行: taskId={}, stageId={}", taskId, stageId);
            return false;
        }
        logger.debug("任务阶段开始执行: taskId={}, stageId={}", taskId, stageId);
        return true;
    }

    /**
     * 释放指定 taskId+stageId 组合的执行权
     *
     * @param taskId  任务ID
     * @param stageId 阶段ID
     */
    public void release(String taskId, String stageId) {
        String key = buildKey(taskId, stageId);
        boolean removed = executingStages.remove(key) != null;
        if (removed) {
            logger.debug("任务阶段执行完毕，已释放: taskId={}, stageId={}", taskId, stageId);
        } else {
            logger.warn("尝试释放未追踪的任务阶段: taskId={}, stageId={}", taskId, stageId);
        }
    }

    /**
     * 释放所有正在追踪的任务阶段（用于客户端 reset/重连场景）
     */
    public void releaseAll() {
        int size = executingStages.size();
        executingStages.clear();
        if (size > 0) {
            logger.info("已清除所有追踪状态，共清除 {} 个任务阶段", size);
        }
    }

    /**
     * 构建存储 key
     */
    private String buildKey(String taskId, String stageId) {
        return taskId + ":" + stageId;
    }
}
