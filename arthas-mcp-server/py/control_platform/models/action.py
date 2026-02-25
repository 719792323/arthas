"""
决策结果数据模型

DecisionResult 表示决策引擎（或 LLM）的推理结果，
包含下一步动作类型和对应参数。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ActionType(str, Enum):
    """决策动作类型"""
    TOOL_CALL = "tool_call"     # 调用工具（继续 ReAct 循环）
    CONCLUDE = "conclude"       # 结束诊断（生成最终结论）


class DecisionResult(BaseModel):
    """
    决策引擎的推理结果

    Attributes:
        action_type: 动作类型（tool_call 或 conclude）
        tool_name: 工具名称（当 action_type=tool_call 时有值）
        tool_arguments: 工具调用参数（当 action_type=tool_call 时有值）
        conclusion: 最终结论（当 action_type=conclude 时有值）
        thinking: LLM 的推理过程文本（用于渲染和调试）
    """
    action_type: ActionType = Field(..., description="动作类型")
    tool_name: Optional[str] = Field(default=None, description="工具名称")
    tool_arguments: Optional[Dict[str, Any]] = Field(default=None, description="工具调用参数")
    conclusion: Optional[str] = Field(default=None, description="最终结论")
    thinking: Optional[str] = Field(default=None, description="LLM 推理过程")