"""
数据库 ORM 模型测试

测试 DiagnosisTask、DiagnosisStage 的 ORM 定义、枚举值、
默认值、唯一约束和关联关系。
"""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from control_platform.db.models import (
    ApprovalStatus,
    Base,
    DiagnosisStage,
    DiagnosisTask,
    StageStatus,
    StageType,
    TaskStatus,
)


# ======================== 枚举测试 ========================

class TestEnums:
    """枚举定义的正确性测试"""

    def test_task_status_values(self):
        """测试目的：验证 TaskStatus 枚举包含所有预期的状态值"""
        assert TaskStatus.RUNNING.value == "running"
        assert TaskStatus.COMPLETED.value == "completed"
        assert TaskStatus.FAILED.value == "failed"
        assert TaskStatus.CANCELLED.value == "cancelled"

    def test_stage_type_values(self):
        """测试目的：验证 StageType 枚举包含完整的 ReAct 循环阶段类型"""
        assert StageType.USER_QUERY.value == "USER_QUERY"
        assert StageType.LLM_THINKING.value == "LLM_THINKING"
        assert StageType.TOOL_CALL.value == "TOOL_CALL"
        assert StageType.TOOL_RESULT.value == "TOOL_RESULT"
        assert StageType.LLM_CONCLUSION.value == "LLM_CONCLUSION"

    def test_stage_status_values(self):
        """测试目的：验证 StageStatus 枚举包含无 processing 的四种状态"""
        assert StageStatus.PENDING.value == "pending"
        assert StageStatus.WAITING_APPROVAL.value == "waiting_approval"
        assert StageStatus.COMPLETED.value == "completed"
        assert StageStatus.FAILED.value == "failed"

    def test_approval_status_values(self):
        """测试目的：验证 ApprovalStatus 枚举包含完整的审核状态"""
        assert ApprovalStatus.NOT_REQUIRED.value == "not_required"
        assert ApprovalStatus.PENDING.value == "pending"
        assert ApprovalStatus.APPROVED.value == "approved"
        assert ApprovalStatus.REJECTED.value == "rejected"

    def test_enum_is_str(self):
        """测试目的：验证枚举继承自 str，支持字符串比较"""
        assert TaskStatus.RUNNING == "running"
        assert StageType.USER_QUERY == "USER_QUERY"
        assert StageStatus.PENDING == "pending"
        assert ApprovalStatus.APPROVED == "approved"


# ======================== ORM 模型测试 ========================

class TestDiagnosisTask:
    """DiagnosisTask ORM 模型测试"""

    @pytest.mark.asyncio
    async def test_create_task_default_values(self, memory_db):
        """测试目的：验证 DiagnosisTask 创建时的默认值（status=running, current_stage_seq=1）"""
        async with memory_db() as session:
            task = DiagnosisTask(
                session_id="test-session",
                user_query="测试问题",
            )
            session.add(task)
            await session.commit()

            # 验证自动生成的 UUID
            assert task.task_id is not None
            assert len(task.task_id) == 36  # UUID 格式

            # 验证默认值
            assert task.status == TaskStatus.RUNNING.value
            assert task.current_stage_seq == 1
            assert task.conclusion is None
            assert task.metadata_ is None

    @pytest.mark.asyncio
    async def test_create_task_with_metadata(self, memory_db):
        """测试目的：验证 DiagnosisTask 可以正确存储 JSON 元数据"""
        metadata = {"source": "api", "priority": "high"}
        async with memory_db() as session:
            task = DiagnosisTask(
                session_id="test-session",
                user_query="测试问题",
                metadata_=metadata,
            )
            session.add(task)
            await session.commit()

        async with memory_db() as session:
            result = await session.execute(
                select(DiagnosisTask).where(DiagnosisTask.task_id == task.task_id)
            )
            loaded_task = result.scalar_one()
            assert loaded_task.metadata_ == metadata

    @pytest.mark.asyncio
    async def test_task_uuid_uniqueness(self, memory_db):
        """测试目的：验证多个 Task 的 task_id 互不相同"""
        async with memory_db() as session:
            tasks = []
            for i in range(5):
                t = DiagnosisTask(
                    session_id=f"session-{i}",
                    user_query=f"问题 {i}",
                )
                session.add(t)
                tasks.append(t)
            await session.commit()

            task_ids = [t.task_id for t in tasks]
            assert len(set(task_ids)) == 5  # 全部唯一


class TestDiagnosisStage:
    """DiagnosisStage ORM 模型测试"""

    @pytest.mark.asyncio
    async def test_create_stage_default_values(self, memory_db):
        """测试目的：验证 DiagnosisStage 创建时的默认值（status=pending, retry_count=0, max_retries=3）"""
        async with memory_db() as session:
            # 先创建 task
            task = DiagnosisTask(
                session_id="test-session",
                user_query="测试问题",
            )
            session.add(task)
            await session.flush()

            stage = DiagnosisStage(
                task_id=task.task_id,
                stage_seq=1,
                stage_type=StageType.USER_QUERY.value,
            )
            session.add(stage)
            await session.commit()

            assert stage.status == StageStatus.PENDING.value
            assert stage.retry_count == 0
            assert stage.max_retries == 3
            assert stage.approval_status == ApprovalStatus.NOT_REQUIRED.value

    @pytest.mark.asyncio
    async def test_stage_unique_constraint(self, memory_db):
        """测试目的：验证同一 task 下 stage_seq 的唯一约束，防止重复插入"""
        async with memory_db() as session:
            task = DiagnosisTask(
                session_id="test-session",
                user_query="测试问题",
            )
            session.add(task)
            await session.flush()

            stage1 = DiagnosisStage(
                task_id=task.task_id,
                stage_seq=1,
                stage_type=StageType.USER_QUERY.value,
            )
            session.add(stage1)
            await session.flush()

            # 再插入同一个 stage_seq，应该抛出唯一约束异常
            stage2 = DiagnosisStage(
                task_id=task.task_id,
                stage_seq=1,  # 重复的 stage_seq
                stage_type=StageType.LLM_THINKING.value,
            )
            session.add(stage2)
            with pytest.raises(IntegrityError):
                await session.flush()

    @pytest.mark.asyncio
    async def test_task_stages_relationship(self, memory_db):
        """测试目的：验证 task.stages 关联关系能正确加载所有 stage 并按 stage_seq 排序"""
        async with memory_db() as session:
            task = DiagnosisTask(
                session_id="test-session",
                user_query="测试问题",
            )
            session.add(task)
            await session.flush()

            # 按非顺序插入 3 个 stage
            for seq in [3, 1, 2]:
                stage = DiagnosisStage(
                    task_id=task.task_id,
                    stage_seq=seq,
                    stage_type=StageType.USER_QUERY.value,
                )
                session.add(stage)
            await session.commit()

        # 重新加载 task，检查 stages 关联
        async with memory_db() as session:
            result = await session.execute(
                select(DiagnosisTask).where(DiagnosisTask.task_id == task.task_id)
            )
            loaded_task = result.scalar_one()
            assert len(loaded_task.stages) == 3
            # 验证按 stage_seq 升序
            seqs = [s.stage_seq for s in loaded_task.stages]
            assert seqs == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_stage_tool_fields(self, memory_db):
        """测试目的：验证 TOOL_CALL 类型 stage 的工具相关字段能正确存储"""
        async with memory_db() as session:
            task = DiagnosisTask(
                session_id="test-session",
                user_query="测试问题",
            )
            session.add(task)
            await session.flush()

            stage = DiagnosisStage(
                task_id=task.task_id,
                stage_seq=1,
                stage_type=StageType.TOOL_CALL.value,
                tool_name="jvm",
                tool_arguments={"verbose": True},
                tool_result="JVM info: ...",
            )
            session.add(stage)
            await session.commit()

        async with memory_db() as session:
            result = await session.execute(
                select(DiagnosisStage).where(DiagnosisStage.task_id == task.task_id)
            )
            loaded = result.scalar_one()
            assert loaded.tool_name == "jvm"
            assert loaded.tool_arguments == {"verbose": True}
            assert loaded.tool_result == "JVM info: ..."
