"""
决策引擎抽象接口

DecisionEngine 定义智能决策的抽象层，后续可替换为 LLM 实现。

接口设计考虑：
1. 输入使用 DecisionContext（含完整历史消息链、可用工具列表）
2. 输出使用 DecisionResult（含 action_type、tool_name、conclusion 等）
3. 预留 context_window_manager 接口用于 LLM token 管理
4. 预留 RAG 接口字段（DecisionContext.rag_context）
"""

from __future__ import annotations

import abc

from control_platform.decision.context import DecisionContext
from control_platform.models.action import DecisionResult


class DecisionEngine(abc.ABC):
    """
    决策引擎抽象基类

    根据诊断上下文（包含完整的 stage 历史链）做出决策，
    返回 DecisionResult 指导下一步操作。

    后续可替换为 LLM 实现（如调用 OpenAI/Claude API），实现智能诊断决策。
    """

    @abc.abstractmethod
    async def decide(self, context: DecisionContext) -> DecisionResult:
        """
        根据上下文做出决策

        Args:
            context: 决策上下文，包含：
                - 完整历史 stage 链（结构化消息格式）
                - 可用工具列表（从客户端 tools/list 获取）
                - 可用 session 列表
                - RAG 上下文（预留）

        Returns:
            DecisionResult，包含：
                - action_type: tool_call（调用工具）或 conclude（结束诊断）
                - tool_name + tool_arguments（当 action_type=tool_call）
                - conclusion（当 action_type=conclude）
                - thinking: LLM 的推理过程文本
        """
        ...

    @property
    def engine_name(self) -> str:
        """引擎名称（默认为类名）"""
        return self.__class__.__name__

    # ========== 预留接口 ==========

    async def manage_context_window(self, context: DecisionContext) -> DecisionContext:
        """
        上下文窗口管理（预留接口）

        当历史消息超出 LLM token 限制时，通过摘要/截断策略缩减上下文。
        当前版本直接返回原 context，后续实现可覆盖此方法。

        Args:
            context: 原始决策上下文

        Returns:
            经过窗口管理后的决策上下文
        """
        # TODO: 后续实现上下文窗口管理策略
        #   - 保留首尾 N 条消息
        #   - 中间部分做摘要压缩
        #   - 按 token 数限制总长度
        return context