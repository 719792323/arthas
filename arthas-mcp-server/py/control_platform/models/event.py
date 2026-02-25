"""
诊断任务与阶段的 Pydantic Schema（用于 API 序列化）

这些 schema 用于 REST API 的输入输出序列化，
ORM 模型定义在 db/models.py 中。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DiagnosisStageSchema(BaseModel):
    """诊断阶段的 API 序列化 schema"""
    id: int
    task_id: str
    stage_seq: int
    stage_type: str
    status: str
    input_data: Optional[Dict[str, Any]] = None
    output_data: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    tool_name: Optional[str] = None
    tool_arguments: Optional[Dict[str, Any]] = None
    tool_result: Optional[str] = None
    approval_status: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    summarized_content: Optional[str] = None
    summary_tokens: Optional[int] = None
    original_tokens: Optional[int] = None
    summary_type: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DiagnosisTaskSchema(BaseModel):
    """诊断任务的 API 序列化 schema"""
    task_id: str
    session_id: str
    user_query: str
    status: str
    current_stage_seq: int
    created_at: datetime
    updated_at: datetime
    conclusion: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = Field(default=None, alias="metadata_")
    stages: List[DiagnosisStageSchema] = []

    model_config = {"from_attributes": True, "populate_by_name": True}


class DiagnosisTaskSummarySchema(BaseModel):
    """诊断任务摘要 schema（列表查询用，不包含 stages）"""
    task_id: str
    session_id: str
    user_query: str
    status: str
    current_stage_seq: int
    created_at: datetime
    updated_at: datetime
    conclusion: Optional[str] = None

    model_config = {"from_attributes": True}


class DiagnosisProgressSchema(BaseModel):
    """诊断任务进度 schema"""
    task_id: str
    status: str
    total_stages: int
    completed_stages: int
    current_stage_seq: int
    current_stage_type: Optional[str] = None
    current_stage_status: Optional[str] = None