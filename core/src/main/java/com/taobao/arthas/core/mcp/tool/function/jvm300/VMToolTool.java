package com.taobao.arthas.core.mcp.tool.function.jvm300;

import com.taobao.arthas.core.mcp.tool.function.AbstractArthasTool;
import com.taobao.arthas.mcp.server.tool.ToolContext;
import com.taobao.arthas.mcp.server.tool.annotation.Tool;
import com.taobao.arthas.mcp.server.tool.annotation.ToolParam;

public class VMToolTool extends AbstractArthasTool {

    public static final String ACTION_GET_INSTANCES = "getInstances";
    public static final String ACTION_INTERRUPT_THREAD = "interruptThread";
    public static final String ACTION_HEAP_ANALYZE = "heapAnalyze";
    
    // heapAnalyze 默认参数
    private static final int DEFAULT_HEAP_ANALYZE_POLL_INTERVAL_MS = 200;
    private static final int DEFAULT_HEAP_ANALYZE_TIMEOUT_SECONDS = 60;

    @Tool(
            name = "vmtool",
            description = "虚拟机工具诊断工具: 查询实例、强制 GC、线程中断、堆内存分析等，对应 Arthas 的 vmtool 命令。",
            streamable = true
    )
    public String vmtool(
            @ToolParam(description = "操作类型: getInstances/forceGc/interruptThread/heapAnalyze 等", required = true)
            String action,

            @ToolParam(description = "ClassLoader的hashcode（16进制），用于指定特定的ClassLoader", required = false)
            String classLoaderHash,

            @ToolParam(description = "ClassLoader的完整类名，如sun.misc.Launcher$AppClassLoader，可替代hashcode", required = false)
            String classLoaderClass,

            @ToolParam(description = "类名，全限定（getInstances 时使用）", required = false)
            String className,

            @ToolParam(description = "返回实例限制数量 (-l)，getInstances 时使用，默认 10；<=0 表示不限制", required = false)
            Integer limit,

            @ToolParam(description = "结果对象展开层次 (-x)，默认 1", required = false)
            Integer expandLevel,

            @ToolParam(description = "OGNL 表达式，对 getInstances 返回的 instances 执行 (--express)", required = false)
            String express,

            @ToolParam(description = "线程 ID (-t)，interruptThread 时使用", required = false)
            Long threadId,

            @ToolParam(description = "heapAnalyze 输出类数量，默认 20", required = false)
            Integer classNum,

            @ToolParam(description = "heapAnalyze 输出每个类的对象数量，默认 20", required = false)
            Integer objectNum,

            ToolContext toolContext
    ) {
        StringBuilder cmd = buildCommand("vmtool");

        if (action == null || action.trim().isEmpty()) {
            throw new IllegalArgumentException("vmtool: action 参数不能为空");
        }
        cmd.append(" --action ").append(action.trim());

        if (classLoaderHash != null && !classLoaderHash.trim().isEmpty()) {
            addParameter(cmd, "-c", classLoaderHash);
        } else if (classLoaderClass != null && !classLoaderClass.trim().isEmpty()) {
            addParameter(cmd, "--classLoaderClass", classLoaderClass);
        }

        if (ACTION_GET_INSTANCES.equals(action.trim())) {
            if (className != null && !className.trim().isEmpty()) {
                addParameter(cmd, "--className", className);
            }
            if (limit != null) {
                cmd.append(" --limit ").append(limit);
            }
            if (expandLevel != null && expandLevel > 0) {
                cmd.append(" -x ").append(expandLevel);
            }
            if (express != null && !express.trim().isEmpty()) {
                addParameter(cmd, "--express", express);
            }
        }

        if (ACTION_INTERRUPT_THREAD.equals(action.trim())) {
            if (threadId != null && threadId > 0) {
                cmd.append(" -t ").append(threadId);
            } else {
                throw new IllegalArgumentException("vmtool interruptThread 需要指定线程 ID (threadId)");
            }
        }

        // heapAnalyze 参数处理
        if (ACTION_HEAP_ANALYZE.equals(action.trim())) {
            if (classNum != null && classNum > 0) {
                cmd.append(" --classNum ").append(classNum);
            }
            if (objectNum != null && objectNum > 0) {
                cmd.append(" --objectNum ").append(objectNum);
            }
            // heapAnalyze 需要等待结果，使用 executeStreamable
            return executeStreamable(toolContext, cmd.toString(), 
                    1, // 期望 1 个结果
                    DEFAULT_HEAP_ANALYZE_POLL_INTERVAL_MS,
                    DEFAULT_HEAP_ANALYZE_TIMEOUT_SECONDS * 1000,
                    "Heap analysis completed successfully");
        }

        // 其他 action（getInstances, forceGc, interruptThread 等）使用同步执行
        return executeSync(toolContext, cmd.toString());
    }


}
