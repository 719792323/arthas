package com.taobao.arthas.core.mcp.tool.function.klass100;

import com.taobao.arthas.core.mcp.tool.function.AbstractArthasTool;
import com.taobao.arthas.mcp.server.session.ArthasCommandContext;
import com.taobao.arthas.mcp.server.tool.ToolContext;
import com.taobao.arthas.mcp.server.tool.annotation.Tool;
import com.taobao.arthas.mcp.server.tool.annotation.ToolParam;
import com.taobao.arthas.mcp.server.util.JsonParser;

import java.util.List;
import java.util.Map;

import static com.taobao.arthas.core.mcp.tool.util.McpToolUtils.TOOL_CONTEXT_COMMAND_CONTEXT_KEY;

//public class JadTool extends AbstractArthasTool {
//
//    @Tool(
//            name = "jad",
//            description = "反编译指定已加载类的源码，将JVM中实际运行的class的bytecode反编译成java代码"
//    )
//    public String jad(
//            @ToolParam(description = "类名表达式匹配，如java.lang.String或demo.MathGame")
//            String classPattern,
//
//            @ToolParam(description = "ClassLoader的hashcode（16进制），用于指定特定的ClassLoader", required = false)
//            String classLoaderHash,
//
//            @ToolParam(description = "ClassLoader的完整类名，如sun.misc.Launcher$AppClassLoader，可替代hashcode", required = false)
//            String classLoaderClass,
//
//            @ToolParam(description = "反编译时只显示源代码，默认true", required = false)
//            Boolean sourceOnly,
//
//            @ToolParam(description = "反编译时不显示行号，默认false", required = false)
//            Boolean noLineNumber,
//
//            @ToolParam(description = "开启正则表达式匹配，默认为通配符匹配，默认false", required = false)
//            Boolean useRegex,
//
//            @ToolParam(description = "指定dump class文件目录，默认会dump到logback.xml中配置的log目录下", required = false)
//            String dumpDirectory,
//
//            ToolContext toolContext) {
//
//        // 默认启用 sourceOnly，只返回源代码
//        boolean isSourceOnly = (sourceOnly == null) ? true : sourceOnly;
//
//        StringBuilder cmd = buildCommand("jad");
//
//        addParameter(cmd, classPattern);
//
//        if (classLoaderHash != null && !classLoaderHash.trim().isEmpty()) {
//            addParameter(cmd, "-c", classLoaderHash);
//        } else if (classLoaderClass != null && !classLoaderClass.trim().isEmpty()) {
//            addParameter(cmd, "--classLoaderClass", classLoaderClass);
//        }
//
//        addFlag(cmd, "--source-only", isSourceOnly);
//        addFlag(cmd, "-E", useRegex);
//
//        if (Boolean.TRUE.equals(noLineNumber)) {
//            cmd.append(" --lineNumber false");
//        }
//
//        addParameter(cmd, "-d", dumpDirectory);
//
//        String commandStr = cmd.toString();
//
//        // 如果 sourceOnly 为 true，提取并返回源代码
//        if (isSourceOnly) {
//            return executeAndExtractSource(toolContext, commandStr);
//        }
//
//        return executeSync(toolContext, commandStr);
//    }
//
//    /**
//     * 执行命令并提取源代码
//     */
//    @SuppressWarnings("unchecked")
//    private String executeAndExtractSource(ToolContext toolContext, String commandStr) {
//        try {
//            ArthasCommandContext commandContext = (ArthasCommandContext) toolContext.getContext().get(TOOL_CONTEXT_COMMAND_CONTEXT_KEY);
//            Object result = commandContext.executeSync(commandStr, null, null);
//
//            if (result == null) {
//                return "Error: No result returned from jad command";
//            }
//
//            // 解析结果，提取 source 字段
//            if (result instanceof Map) {
//                Map<String, Object> resultMap = (Map<String, Object>) result;
//                Object resultsObj = resultMap.get("results");
//
//                if (resultsObj instanceof List) {
//                    List<Map<String, Object>> results = (List<Map<String, Object>>) resultsObj;
//                    StringBuilder sourceBuilder = new StringBuilder();
//
//                    for (int i = 0; i < results.size(); i++) {
//                        Map<String, Object> item = results.get(i);
//                        Object source = item.get("source");
//
//                        if (source != null) {
//                            if (results.size() > 1) {
//                                // 多个结果时，添加分隔符
//                                Map<String, Object> classInfo = (Map<String, Object>) item.get("classInfo");
//                                String location = (String) item.get("location");
//                                sourceBuilder.append("/* ========== Result ").append(i + 1).append(" ==========\n");
//                                if (classInfo != null) {
//                                    sourceBuilder.append(" * ClassLoader: ").append(classInfo.get("classLoaderHash")).append("\n");
//                                }
//                                if (location != null) {
//                                    sourceBuilder.append(" * Location: ").append(location).append("\n");
//                                }
//                                sourceBuilder.append(" */\n\n");
//                            }
//                            sourceBuilder.append(source.toString());
//                            if (i < results.size() - 1) {
//                                sourceBuilder.append("\n\n");
//                            }
//                        }
//                    }
//
//                    if (sourceBuilder.length() > 0) {
//                        return sourceBuilder.toString();
//                    }
//                }
//            }
//
//            // 如果无法提取 source，返回完整 JSON
//            return JsonParser.toJson(result);
//
//        } catch (Exception e) {
//            logger.error("Error executing jad command: {}", commandStr, e);
//            return "Error: " + e.getMessage();
//        }
//    }
//}

public class JadTool extends AbstractArthasTool {

    @Tool(
            name = "jad",
            description = "反编译指定已加载类的源码，将JVM中实际运行的class的bytecode反编译成java代码"
    )
    public String jad(
            @ToolParam(description = "类名表达式匹配，如java.lang.String或demo.MathGame")
            String classPattern,

            @ToolParam(description = "ClassLoader的hashcode（16进制），用于指定特定的ClassLoader", required = false)
            String classLoaderHash,

            @ToolParam(description = "ClassLoader的完整类名，如sun.misc.Launcher$AppClassLoader，可替代hashcode", required = false)
            String classLoaderClass,

            @ToolParam(description = "反编译时只显示源代码，默认false", required = false)
            Boolean sourceOnly,

            @ToolParam(description = "反编译时不显示行号，默认false", required = false)
            Boolean noLineNumber,

            @ToolParam(description = "开启正则表达式匹配，默认为通配符匹配，默认false", required = false)
            Boolean useRegex,

            @ToolParam(description = "指定dump class文件目录，默认会dump到logback.xml中配置的log目录下", required = false)
            String dumpDirectory,

            ToolContext toolContext) {

        StringBuilder cmd = buildCommand("jad");

        addParameter(cmd, classPattern);

        if (classLoaderHash != null && !classLoaderHash.trim().isEmpty()) {
            addParameter(cmd, "-c", classLoaderHash);
        } else if (classLoaderClass != null && !classLoaderClass.trim().isEmpty()) {
            addParameter(cmd, "--classLoaderClass", classLoaderClass);
        }

        addFlag(cmd, "--source-only", sourceOnly);
        addFlag(cmd, "-E", useRegex);

        if (Boolean.TRUE.equals(noLineNumber)) {
            cmd.append(" --lineNumber false");
        }

        addParameter(cmd, "-d", dumpDirectory);

        return executeSync(toolContext, cmd.toString());
    }
}
