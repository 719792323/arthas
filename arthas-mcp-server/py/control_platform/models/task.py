"""
任务相关的 Pydantic Schema

TaskStatus 枚举定义在 db/models.py 中（ORM 层），
此处定义用于 API 层的请求/响应 schema。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class CreateDiagnosisRequest(BaseModel):
    """创建诊断任务的请求 schema"""
    session_id: str = Field(..., description="目标 Arthas 客户端的 session ID")
    user_query: str = Field(..., description="用户原始提问", min_length=1)
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="附加元数据")


class TaskStatusResponse(BaseModel):
    """任务状态变更的响应 schema"""
    task_id: str
    status: str
    message: str = ""