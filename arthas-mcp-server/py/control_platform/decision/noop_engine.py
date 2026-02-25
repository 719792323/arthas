"""
Mock 决策引擎实现

用于端到端测试，不依赖真实 LLM。
根据用户问题返回预设的 DecisionResult：
- 包含关键词 "jvm" → 调用 jvm 工具
- 包含关键词 "gc" → 调用 gc 工具
- 包含关键词 "thread" → 调用 thread 工具
- 其他 → 直接生成结论

当历史中已有工具调用结果时，直接生成结论（模拟完整 ReAct 循环）。
"""

from __future__ import annotations

import logging

from control_platform.decision.context import DecisionContext
from control_platform.decision.engine import DecisionEngine
from control_platform.models.action import ActionType, DecisionResult

logger = logging.getLogger(__name__)

# 预设的工具调用映射
_KEYWORD_TOOL_MAP = {
    "jvm": ("jvm", {}),
    "内存": ("memory", {}),
    "gc": ("gc", {}),
    "thread": ("thread", {}),
    "线程": ("thread", {}),
    "cpu": ("thread", {"n": 5, "i": 1000}),
    "堆": ("heapdump", {"file": "/tmp/dump.hprof"}),
}


class MockDecisionEngine(DecisionEngine):
    """
    Mock 决策引擎

    不调用真实 LLM，根据预设规则返回 DecisionResult。
    用于端到端测试和开发调试。
    """

    async def decide(self, context: DecisionContext) -> DecisionResult:
        """
        根据预设规则做出决策

        策略：
        1. 如果历史中已有 function_result 消息，说明工具已执行过，直接生成结论
        2. 否则根据用户提问的关键词匹配工具
        3. 都不匹配则直接生成结论
        """
        messages = context.messages
        user_query = context.user_query

        logger.info(
            f"[MockDecisionEngine] 决策中: task_id={context.task_id}, "
            f"messages={len(messages)}, query={user_query[:50]}..."
        )

        # 检查历史中是否已有工具执行结果
        has_tool_result = any(
            msg.get("role") == "function_result" for msg in messages
        )

        if has_tool_result:
            # 已有工具结果，生成结论
            # 获取最后一个工具结果
            last_result = ""
            last_tool = ""
            for msg in reversed(messages):
                if msg.get("role") == "function_result":
                    last_result = msg.get("content", "")[:200]
                    last_tool = msg.get("tool_name", "")
                    break

            return DecisionResult(
                action_type=ActionType.CONCLUDE,
                thinking=f"已获得 {last_tool} 工具的执行结果，基于结果进行分析和总结。",
                conclusion=(
                    f"[Mock 诊断结论]\n\n"
                    f"用户问题: {user_query}\n\n"
                    f"执行工具: {last_tool}\n"
                    f"执行结果摘要: {last_result}\n\n"
                    f"分析结论: 基于工具执行结果，系统运行状态正常。"
                    f"（此为 Mock 引擎自动生成的结论，接入 LLM 后将提供真实诊断分析。）"
                ),
            )

        # 根据关键词匹配工具
        for keyword, (tool_name, tool_args) in _KEYWORD_TOOL_MAP.items():
            if keyword in user_query.lower():
                return DecisionResult(
                    action_type=ActionType.TOOL_CALL,
                    tool_name=tool_name,
                    tool_arguments=tool_args,
                    thinking=f"用户提问涉及 '{keyword}' 相关问题，需要调用 {tool_name} 工具获取信息。",
                )

        # 默认：直接生成结论
        return DecisionResult(
            action_type=ActionType.CONCLUDE,
            thinking="用户的问题不需要额外的工具调用即可回答。",
            conclusion=(
                f"[Mock 诊断结论]\n\n"
                f"用户问题: {user_query}\n\n"
                f"分析结论: 根据问题描述，暂无需要进一步诊断的内容。"
                f"（此为 Mock 引擎自动生成的结论，接入 LLM 后将提供真实诊断分析。）"
            ),
        )