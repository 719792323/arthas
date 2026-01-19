/*
 * Copyright 2024-2024 the original author or authors.
 */

package com.taobao.arthas.mcp.server.tool.function;

import com.taobao.arthas.mcp.server.tool.annotation.Tool;
import com.taobao.arthas.mcp.server.tool.annotation.ToolParam;

import java.lang.management.ManagementFactory;
import java.lang.management.MemoryMXBean;
import java.lang.management.RuntimeMXBean;
import java.util.HashMap;
import java.util.Map;

/**
 * 测试工具类
 * 
 * 提供一些简单的测试工具，用于验证 MCP Client 的功能。
 * 这些工具不依赖 Arthas Core，可以独立运行。
 *
 * @author Arthas Team
 */
public class EchoTool {

    /**
     * Echo 工具：返回输入的消息
     */
    @Tool(name = "echo", description = "Echo 工具：返回输入的消息，用于测试连接")
    public String echo(
            @ToolParam(description = "要回显的消息", required = false) String message
    ) {
        if (message == null || message.isEmpty()) {
            message = "Hello from Arthas MCP Client!";
        }
        return "Echo: " + message;
    }

    /**
     * 简单的 JVM 信息工具
     */
    @Tool(name = "jvm_info", description = "JVM 信息工具：获取当前 JVM 的基本运行信息")
    public Map<String, Object> jvmInfo() {
        Map<String, Object> info = new HashMap<>();
        
        RuntimeMXBean runtimeMXBean = ManagementFactory.getRuntimeMXBean();
        MemoryMXBean memoryMXBean = ManagementFactory.getMemoryMXBean();
        
        // 运行时信息
        info.put("vmName", runtimeMXBean.getVmName());
        info.put("vmVersion", runtimeMXBean.getVmVersion());
        info.put("vmVendor", runtimeMXBean.getVmVendor());
        info.put("specVersion", runtimeMXBean.getSpecVersion());
        info.put("startTime", runtimeMXBean.getStartTime());
        info.put("uptime", runtimeMXBean.getUptime());
        info.put("pid", runtimeMXBean.getName().split("@")[0]);
        
        // 内存信息
        Map<String, Object> heapMemory = new HashMap<>();
        heapMemory.put("init", memoryMXBean.getHeapMemoryUsage().getInit());
        heapMemory.put("used", memoryMXBean.getHeapMemoryUsage().getUsed());
        heapMemory.put("committed", memoryMXBean.getHeapMemoryUsage().getCommitted());
        heapMemory.put("max", memoryMXBean.getHeapMemoryUsage().getMax());
        info.put("heapMemory", heapMemory);
        
        Map<String, Object> nonHeapMemory = new HashMap<>();
        nonHeapMemory.put("init", memoryMXBean.getNonHeapMemoryUsage().getInit());
        nonHeapMemory.put("used", memoryMXBean.getNonHeapMemoryUsage().getUsed());
        nonHeapMemory.put("committed", memoryMXBean.getNonHeapMemoryUsage().getCommitted());
        nonHeapMemory.put("max", memoryMXBean.getNonHeapMemoryUsage().getMax());
        info.put("nonHeapMemory", nonHeapMemory);
        
        // 系统属性
        info.put("javaVersion", System.getProperty("java.version"));
        info.put("javaHome", System.getProperty("java.home"));
        info.put("osName", System.getProperty("os.name"));
        info.put("osVersion", System.getProperty("os.version"));
        info.put("osArch", System.getProperty("os.arch"));
        info.put("userDir", System.getProperty("user.dir"));
        
        return info;
    }

    /**
     * 系统时间工具
     */
    @Tool(name = "system_time", description = "系统时间工具：获取当前系统时间")
    public Map<String, Object> systemTime() {
        Map<String, Object> time = new HashMap<>();
        time.put("currentTimeMillis", System.currentTimeMillis());
        time.put("nanoTime", System.nanoTime());
        time.put("formattedTime", new java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss.SSS").format(new java.util.Date()));
        time.put("timezone", java.util.TimeZone.getDefault().getID());
        return time;
    }

    /**
     * 线程计数工具
     */
    @Tool(name = "thread_count", description = "线程计数工具：获取当前 JVM 的线程数量")
    public Map<String, Object> threadCount() {
        Map<String, Object> result = new HashMap<>();
        java.lang.management.ThreadMXBean threadMXBean = ManagementFactory.getThreadMXBean();
        
        result.put("threadCount", threadMXBean.getThreadCount());
        result.put("peakThreadCount", threadMXBean.getPeakThreadCount());
        result.put("totalStartedThreadCount", threadMXBean.getTotalStartedThreadCount());
        result.put("daemonThreadCount", threadMXBean.getDaemonThreadCount());
        
        return result;
    }
}
