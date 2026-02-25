"""
决策上下文模型

DecisionContext 提供给 DecisionEngine 的上下文信息，
包含完整的历史消息链、可用工具列表和 RAG 预留字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DecisionContext:
    """
    决策上下文

    提供给 DecisionEngine 做决策所需的全部上下文信息。

    Attributes:
        task_id: 当前任务 ID
        session_id: 关联的 Arthas 客户端会话 ID
        user_query: 用户原始提问
        messages: 结构化历史消息列表（按 stage_seq 排序）
            - role="user": 用户提问
            - role="assistant": LLM 推理
            - role="function_call": 工具调用
            - role="function_result": 工具结果
        available_tools: 可用工具列表（从客户端 tools/list 获取）
        current_stage_seq: 当前最新 stage 序号
        rag_context: 预留 RAG 检索增强知识片段
        metadata: 附加元数据
    """
    task_id: str = ""
    session_id: str = ""
    user_query: str = ""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    available_tools: List[Dict[str, Any]] = field(default_factory=list)
    current_stage_seq: int = 0
    rag_context: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
