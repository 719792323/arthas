"""
数据库 ORM 模型定义

定义 DiagnosisTask 和 DiagnosisStage 两张表，
以及 StageType、StageStatus 枚举。
"""

from __future__ import annotations

import uuid
from enum import Enum as PyEnum

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    ForeignKey,
    JSON,
    func,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import DeclarativeBase, relationship

# 兼容 SQLite 和 MySQL 的大文本类型：
# SQLite 下使用 Text（无长度限制），MySQL 下使用 LONGTEXT（最大 4GB）
LargeText = Text().with_variant(LONGTEXT, "mysql")


# ======================== 枚举定义 ========================

class TaskStatus(str, PyEnum):
    """诊断任务整体状态"""
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageType(str, PyEnum):
    """诊断阶段类型"""
    USER_QUERY = "USER_QUERY"           # 用户提问（task 起点，stage_seq=1）
    LLM_THINKING = "LLM_THINKING"       # LLM 推理（将上下文发给 LLM，获取推理结果和 Action）
    TOOL_CALL = "TOOL_CALL"             # 工具调用（向 Arthas 客户端发送命令执行）
    TOOL_RESULT = "TOOL_RESULT"         # 工具结果（接收 Arthas 执行结果）
    LLM_CONCLUSION = "LLM_CONCLUSION"   # LLM 结论（LLM 判断诊断结束，生成最终结论）
    CONTEXT_SUMMARY = "CONTEXT_SUMMARY" # 全文摘要事件（上下文压缩产生的摘要）


class StageStatus(str, PyEnum):
    """
    诊断阶段状态（无 processing 中间状态）

    - pending: 待处理 / 可执行，定时任务每次扫到都尝试执行
    - waiting_approval: 等待人工审核，需审核通过后变回 pending
    - completed: 已完成（终态）
    - failed: 执行失败，已达最大重试次数（终态）
    """
    PENDING = "pending"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"


class ApprovalStatus(str, PyEnum):
    """审核状态"""
    NOT_REQUIRED = "not_required"   # 无需审核
    PENDING = "pending"             # 待审核
    APPROVED = "approved"           # 已通过
    REJECTED = "rejected"           # 已拒绝


# ======================== ORM Base ========================

class Base(DeclarativeBase):
    """声明式 ORM 基类"""
    pass


# ======================== 表定义 ========================

def _generate_uuid() -> str:
    return str(uuid.uuid4())


class DiagnosisTask(Base):
    """
    诊断任务表

    一个 DiagnosisTask 对应一次完整的诊断流程（如"帮我排查内存泄漏"）。
    """
    __tablename__ = "diagnosis_task"

    task_id = Column(String(36), primary_key=True, default=_generate_uuid, comment="任务唯一标识（UUID）")
    session_id = Column(String(128), nullable=False, index=True, comment="关联的 Arthas 客户端会话 ID")
    user_query = Column(LargeText, nullable=False, comment="用户原始提问")
    status = Column(String(20), nullable=False, default=TaskStatus.RUNNING.value, comment="任务整体状态")
    current_stage_seq = Column(Integer, nullable=False, default=1, comment="当前最新 stage 序号")
    created_at = Column(DateTime, nullable=False, default=func.now(), comment="创建时间")
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now(), comment="最后更新时间")
    conclusion = Column(LargeText, nullable=True, comment="最终诊断结论（LLM 生成）")
    metadata_ = Column("metadata", JSON, nullable=True, comment="附加元数据")

    # 关联关系
    stages = relationship("DiagnosisStage", back_populates="task", order_by="DiagnosisStage.stage_seq", lazy="selectin")

    def __repr__(self) -> str:
        return f"<DiagnosisTask task_id={self.task_id} status={self.status} stage_seq={self.current_stage_seq}>"


class DiagnosisStage(Base):
    """
    诊断阶段表（事件表）

    每个 stage 代表诊断流程中的一个步骤，通过 stage_seq 在同一 task 下排序。
    """
    __tablename__ = "diagnosis_stage"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="数据库自增主键")
    task_id = Column(String(36), ForeignKey("diagnosis_task.task_id"), nullable=False, index=True, comment="所属任务 ID")
    stage_seq = Column(Integer, nullable=False, comment="阶段序号（同一 task 下从 1 递增）")
    stage_type = Column(String(30), nullable=False, comment="阶段类型")
    status = Column(String(20), nullable=False, default=StageStatus.PENDING.value, comment="阶段状态")

    # 输入输出数据
    input_data = Column(JSON, nullable=True, comment="阶段输入（如用户问题、LLM prompt、命令参数等）")
    output_data = Column(JSON, nullable=True, comment="阶段输出（如 LLM 回复、Arthas 执行结果等）")
    error_message = Column(LargeText, nullable=True, comment="失败时的错误信息")

    # 重试控制
    retry_count = Column(Integer, nullable=False, default=0, comment="已重试次数")
    max_retries = Column(Integer, nullable=False, default=3, comment="最大重试次数")

    # 工具调用专属字段（仅 TOOL_CALL 类型有值）
    tool_name = Column(String(100), nullable=True, comment="工具/命令名称")
    tool_arguments = Column(JSON, nullable=True, comment="工具调用参数")
    tool_result = Column(LargeText, nullable=True, comment="工具执行结果原文")

    # 审核相关字段
    approval_status = Column(String(20), nullable=True, default=ApprovalStatus.NOT_REQUIRED.value, comment="审核状态")
    approved_by = Column(String(100), nullable=True, comment="审核人")
    approved_at = Column(DateTime, nullable=True, comment="审核时间")

    # 工具调用冷却控制
    last_sent_at = Column(DateTime, nullable=True, comment="最近一次发送工具调用请求的时间（用于冷却判断）")

    # 上下文摘要相关字段（不修改原始字段，仅新增）
    summarized_content = Column(LargeText, nullable=True, comment="LLM 摘要后的内容，NULL 表示未摘要")
    summary_tokens = Column(Integer, nullable=True, comment="摘要后的 token 数量")
    original_tokens = Column(Integer, nullable=True, comment="原始内容的 token 数量")
    summary_type = Column(String(20), nullable=True, comment="摘要类型: llm / rule / NULL")

    # 时间戳
    created_at = Column(DateTime, nullable=False, default=func.now(), comment="创建时间")
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now(), comment="最后更新时间")

    # 关联关系
    task = relationship("DiagnosisTask", back_populates="stages")

    # 唯一约束：同一 task 下 stage_seq 唯一
    __table_args__ = (
        UniqueConstraint("task_id", "stage_seq", name="uq_task_stage_seq"),
    )

    def __repr__(self) -> str:
        return (
            f"<DiagnosisStage id={self.id} task_id={self.task_id} "
            f"seq={self.stage_seq} type={self.stage_type} status={self.status}>"
        )


class LlmPromptLog(Base):
    """
    LLM Prompt 日志表

    记录每次发送给 LLM 的完整 prompt 内容和响应信息，
    仅在配置 CP_ENABLE_PROMPT_LOGGING=true 时启用。
    """
    __tablename__ = "llm_prompt_log"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    task_id = Column(String(36), ForeignKey("diagnosis_task.task_id"), nullable=False, index=True, comment="关联任务 ID")
    stage_seq = Column(Integer, nullable=False, comment="触发推理的 stage 序号")
    model = Column(String(100), nullable=True, comment="使用的 LLM 模型名称")

    # Prompt 内容
    system_prompt = Column(LargeText, nullable=True, comment="系统提示词")
    chat_messages = Column(JSON, nullable=True, comment="完整的 chat messages（JSON 数组）")
    tools_schema = Column(JSON, nullable=True, comment="传给 LLM 的 tools schema（JSON 数组）")

    # 响应信息
    response_content = Column(LargeText, nullable=True, comment="LLM 原始响应文本")
    response_tool_calls = Column(JSON, nullable=True, comment="LLM 返回的 tool_calls（JSON）")
    finish_reason = Column(String(50), nullable=True, comment="LLM finish_reason")
    prompt_tokens = Column(Integer, nullable=True, comment="Prompt token 数")
    completion_tokens = Column(Integer, nullable=True, comment="Completion token 数")
    total_tokens = Column(Integer, nullable=True, comment="总 token 数")

    # 时间戳
    created_at = Column(DateTime, nullable=False, default=func.now(), comment="记录时间")

    def __repr__(self) -> str:
        return (
            f"<LlmPromptLog id={self.id} task_id={self.task_id} "
            f"stage_seq={self.stage_seq} model={self.model}>"
        )
