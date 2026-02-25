"""
诊断仓储层（DiagnosisRepository）测试

这是测试量最大、最核心的模块，覆盖：
- 任务 CRUD（创建、查询、完成、失败）
- 阶段轮询（get_pending_stages 的复杂查询逻辑）
- 状态流转（complete_and_next、complete_stage、mark_failed）
- 审核操作（mark_waiting_approval、approve、reject）
- 阶段查询（get_task_stages、get_stage、get_latest_stage）
- 故障恢复（check_and_fail_stale_tasks）
"""

import pytest
import pytest_asyncio

from control_platform.db.models import (
    ApprovalStatus,
    DiagnosisStage,
    DiagnosisTask,
    StageStatus,
    StageType,
    TaskStatus,
)
from control_platform.db.repository import DiagnosisRepository


# ======================== 任务 CRUD ========================

class TestTaskCRUD:
    """任务增删改查测试"""

    @pytest.mark.asyncio
    async def test_create_task_returns_running_task_with_initial_stage(self, repo: DiagnosisRepository):
        """测试目的：create_task 应创建 running 状态的任务并附带一个 USER_QUERY 初始 stage"""
        task = await repo.create_task(
            session_id="session-001",
            user_query="帮我排查内存泄漏",
            metadata={"source": "test"},
        )

        assert task.task_id is not None
        assert task.session_id == "session-001"
        assert task.user_query == "帮我排查内存泄漏"
        assert task.status == TaskStatus.RUNNING.value
        assert task.current_stage_seq == 1
        assert task.metadata_ == {"source": "test"}

        # 验证初始 stage 也被创建
        stages = await repo.get_task_stages(task.task_id)
        assert len(stages) == 1
        assert stages[0].stage_type == StageType.USER_QUERY.value
        assert stages[0].status == StageStatus.PENDING.value
        assert stages[0].stage_seq == 1
        assert stages[0].input_data == {"user_query": "帮我排查内存泄漏"}

    @pytest.mark.asyncio
    async def test_get_task_existing(self, repo: DiagnosisRepository, sample_task):
        """测试目的：get_task 查询已存在的任务应返回完整的 task 对象"""
        task, _ = sample_task
        loaded = await repo.get_task(task.task_id)
        assert loaded is not None
        assert loaded.task_id == task.task_id
        assert loaded.user_query == task.user_query

    @pytest.mark.asyncio
    async def test_get_task_not_found(self, repo: DiagnosisRepository):
        """测试目的：get_task 查询不存在的 task_id 应返回 None"""
        loaded = await repo.get_task("non-existent-task-id")
        assert loaded is None

    @pytest.mark.asyncio
    async def test_get_tasks_no_filter(self, repo: DiagnosisRepository):
        """测试目的：get_tasks 无筛选条件应返回所有任务，按 created_at 降序"""
        await repo.create_task(session_id="s1", user_query="问题1")
        await repo.create_task(session_id="s2", user_query="问题2")
        await repo.create_task(session_id="s3", user_query="问题3")

        tasks = await repo.get_tasks()
        assert len(tasks) == 3

    @pytest.mark.asyncio
    async def test_get_tasks_filter_by_session_id(self, repo: DiagnosisRepository):
        """测试目的：get_tasks 按 session_id 筛选应只返回匹配的任务"""
        await repo.create_task(session_id="target-session", user_query="问题1")
        await repo.create_task(session_id="other-session", user_query="问题2")
        await repo.create_task(session_id="target-session", user_query="问题3")

        tasks = await repo.get_tasks(session_id="target-session")
        assert len(tasks) == 2
        for t in tasks:
            assert t.session_id == "target-session"

    @pytest.mark.asyncio
    async def test_get_tasks_filter_by_status(self, repo: DiagnosisRepository):
        """测试目的：get_tasks 按 status 筛选应只返回匹配状态的任务"""
        task1 = await repo.create_task(session_id="s1", user_query="问题1")
        await repo.create_task(session_id="s2", user_query="问题2")
        await repo.complete_task(task1.task_id, "结论")

        tasks = await repo.get_tasks(status=TaskStatus.COMPLETED.value)
        assert len(tasks) == 1
        assert tasks[0].task_id == task1.task_id

    @pytest.mark.asyncio
    async def test_get_tasks_pagination(self, repo: DiagnosisRepository):
        """测试目的：get_tasks 的 limit 和 offset 分页参数应正确工作"""
        for i in range(5):
            await repo.create_task(session_id=f"s{i}", user_query=f"问题{i}")

        page1 = await repo.get_tasks(limit=2, offset=0)
        page2 = await repo.get_tasks(limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        # 不应有重复
        ids1 = {t.task_id for t in page1}
        ids2 = {t.task_id for t in page2}
        assert ids1.isdisjoint(ids2)

    @pytest.mark.asyncio
    async def test_complete_task(self, repo: DiagnosisRepository, sample_task):
        """测试目的：complete_task 应将状态改为 completed 并写入 conclusion"""
        task, _ = sample_task
        await repo.complete_task(task.task_id, "诊断结论：一切正常")

        loaded = await repo.get_task(task.task_id)
        assert loaded.status == TaskStatus.COMPLETED.value
        assert loaded.conclusion == "诊断结论：一切正常"

    @pytest.mark.asyncio
    async def test_fail_task(self, repo: DiagnosisRepository, sample_task):
        """测试目的：fail_task 应将任务状态改为 failed"""
        task, _ = sample_task
        await repo.fail_task(task.task_id)

        loaded = await repo.get_task(task.task_id)
        assert loaded.status == TaskStatus.FAILED.value


# ======================== 阶段轮询 ========================

class TestPendingStages:
    """get_pending_stages 复杂查询逻辑测试"""

    @pytest.mark.asyncio
    async def test_pending_stages_returns_pending_stage(self, repo: DiagnosisRepository, sample_task):
        """测试目的：running 任务中有 pending 的最新 stage 时，get_pending_stages 应返回它"""
        task, stage = sample_task
        pending = await repo.get_pending_stages()
        assert len(pending) == 1
        t, s = pending[0]
        assert t.task_id == task.task_id
        assert s.id == stage.id
        assert s.status == StageStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_pending_stages_empty_when_no_pending(self, repo: DiagnosisRepository, sample_task):
        """测试目的：所有 stage 都已 completed 时，get_pending_stages 应返回空列表"""
        task, stage = sample_task
        await repo.complete_stage(stage.id, output_data={"done": True})

        pending = await repo.get_pending_stages()
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_pending_stages_excludes_non_running_tasks(self, repo: DiagnosisRepository, sample_task):
        """测试目的：非 running 状态的任务，即使有 pending stage 也不应被返回"""
        task, stage = sample_task
        await repo.fail_task(task.task_id)

        pending = await repo.get_pending_stages()
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_pending_stages_only_returns_latest_stage(self, repo: DiagnosisRepository, sample_task):
        """测试目的：只返回每个 task 中 stage_seq 最大的 pending stage，忽略中间的"""
        task, stage = sample_task
        # 完成 stage1，创建 stage2
        next_stage = await repo.complete_and_next(
            stage_id=stage.id,
            output_data={"done": True},
            next_stage_type=StageType.LLM_THINKING.value,
        )

        pending = await repo.get_pending_stages()
        assert len(pending) == 1
        _, s = pending[0]
        assert s.stage_seq == 2  # 只返回最新的

    @pytest.mark.asyncio
    async def test_pending_stages_multiple_tasks(self, repo: DiagnosisRepository):
        """测试目的：多个 running 任务同时有 pending stage 时，应全部返回"""
        await repo.create_task(session_id="s1", user_query="问题1")
        await repo.create_task(session_id="s2", user_query="问题2")
        await repo.create_task(session_id="s3", user_query="问题3")

        pending = await repo.get_pending_stages()
        assert len(pending) == 3


# ======================== 状态流转 ========================

class TestStateTransition:
    """stage 状态流转测试"""

    @pytest.mark.asyncio
    async def test_complete_and_next_creates_next_stage(self, repo: DiagnosisRepository, sample_task):
        """测试目的：complete_and_next 应将当前 stage 标记为 completed，并创建新的 pending stage"""
        task, stage = sample_task

        next_stage = await repo.complete_and_next(
            stage_id=stage.id,
            output_data={"user_query": "帮我排查 JVM 内存泄漏问题"},
            next_stage_type=StageType.LLM_THINKING.value,
            next_input_data={"instruction": "请分析用户问题"},
        )

        # 验证当前 stage 已完成
        current = await repo.get_stage(stage.id)
        assert current.status == StageStatus.COMPLETED.value
        assert current.output_data == {"user_query": "帮我排查 JVM 内存泄漏问题"}

        # 验证下一个 stage 已创建
        assert next_stage is not None
        assert next_stage.stage_seq == 2
        assert next_stage.stage_type == StageType.LLM_THINKING.value
        assert next_stage.status == StageStatus.PENDING.value
        assert next_stage.input_data == {"instruction": "请分析用户问题"}

    @pytest.mark.asyncio
    async def test_complete_and_next_updates_task_seq(self, repo: DiagnosisRepository, sample_task):
        """测试目的：complete_and_next 应同步更新 task.current_stage_seq"""
        task, stage = sample_task

        await repo.complete_and_next(
            stage_id=stage.id,
            output_data={},
            next_stage_type=StageType.LLM_THINKING.value,
        )

        loaded_task = await repo.get_task(task.task_id)
        assert loaded_task.current_stage_seq == 2

    @pytest.mark.asyncio
    async def test_complete_and_next_with_tool_fields(self, repo: DiagnosisRepository, sample_task):
        """测试目的：complete_and_next 创建 TOOL_CALL 时应正确写入 tool_name 和 tool_arguments"""
        task, stage = sample_task

        next_stage = await repo.complete_and_next(
            stage_id=stage.id,
            output_data={},
            next_stage_type=StageType.TOOL_CALL.value,
            next_tool_name="jvm",
            next_tool_arguments={"verbose": True},
        )

        assert next_stage.tool_name == "jvm"
        assert next_stage.tool_arguments == {"verbose": True}

    @pytest.mark.asyncio
    async def test_complete_and_next_with_approval_status(self, repo: DiagnosisRepository, sample_task):
        """测试目的：complete_and_next 应能设置 next_approval_status"""
        task, stage = sample_task

        next_stage = await repo.complete_and_next(
            stage_id=stage.id,
            output_data={},
            next_stage_type=StageType.TOOL_CALL.value,
            next_approval_status=ApprovalStatus.PENDING.value,
        )

        assert next_stage.approval_status == ApprovalStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_complete_and_next_nonexistent_stage_raises(self, repo: DiagnosisRepository, memory_db):
        """测试目的：complete_and_next 传入不存在的 stage_id 应抛出 ValueError"""
        with pytest.raises(ValueError, match="Stage not found"):
            await repo.complete_and_next(
                stage_id=99999,
                output_data={},
                next_stage_type=StageType.LLM_THINKING.value,
            )

    @pytest.mark.asyncio
    async def test_complete_stage_only_marks_completed(self, repo: DiagnosisRepository, sample_task):
        """测试目的：complete_stage 仅标记当前 stage 为 completed，不创建新 stage"""
        task, stage = sample_task

        await repo.complete_stage(stage.id, output_data={"conclusion": "完成"})

        loaded = await repo.get_stage(stage.id)
        assert loaded.status == StageStatus.COMPLETED.value
        assert loaded.output_data == {"conclusion": "完成"}

        # 不应有新 stage
        stages = await repo.get_task_stages(task.task_id)
        assert len(stages) == 1

    @pytest.mark.asyncio
    async def test_complete_stage_with_tool_result(self, repo: DiagnosisRepository, sample_task):
        """测试目的：complete_stage 带 tool_result 参数时应正确写入 tool_result 字段"""
        task, stage = sample_task

        await repo.complete_stage(stage.id, tool_result="工具执行结果内容")

        loaded = await repo.get_stage(stage.id)
        assert loaded.tool_result == "工具执行结果内容"

    @pytest.mark.asyncio
    async def test_mark_failed_below_max_retries(self, repo: DiagnosisRepository, sample_task):
        """测试目的：mark_failed 未达到 max_retries 时应返回 False，stage 保持 pending 且 retry_count +1"""
        _, stage = sample_task

        result = await repo.mark_failed(stage.id, "临时错误")
        assert result is False

        loaded = await repo.get_stage(stage.id)
        assert loaded.retry_count == 1
        assert loaded.status == StageStatus.PENDING.value  # 仍为 pending
        assert loaded.error_message == "临时错误"

    @pytest.mark.asyncio
    async def test_mark_failed_reaches_max_retries(self, repo: DiagnosisRepository, sample_task):
        """测试目的：mark_failed 达到 max_retries(3) 时应返回 True，stage 变为 failed 终态"""
        _, stage = sample_task

        # 连续 3 次 mark_failed
        await repo.mark_failed(stage.id, "错误1")
        await repo.mark_failed(stage.id, "错误2")
        result = await repo.mark_failed(stage.id, "错误3")

        assert result is True

        loaded = await repo.get_stage(stage.id)
        assert loaded.retry_count == 3
        assert loaded.status == StageStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_mark_failed_nonexistent_stage_raises(self, repo: DiagnosisRepository, memory_db):
        """测试目的：mark_failed 传入不存在的 stage_id 应抛出 ValueError"""
        with pytest.raises(ValueError, match="Stage not found"):
            await repo.mark_failed(99999, "不存在的 stage")


# ======================== 审核操作 ========================

class TestApproval:
    """审核操作测试"""

    @pytest.mark.asyncio
    async def test_mark_waiting_approval(self, repo: DiagnosisRepository, sample_task):
        """测试目的：mark_waiting_approval 应将 stage 状态改为 waiting_approval，审核状态改为 pending"""
        _, stage = sample_task
        await repo.mark_waiting_approval(stage.id)

        loaded = await repo.get_stage(stage.id)
        assert loaded.status == StageStatus.WAITING_APPROVAL.value
        assert loaded.approval_status == ApprovalStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_approve_stage(self, repo: DiagnosisRepository, sample_task):
        """测试目的：approve_stage 应将 stage 状态恢复为 pending，审核状态改为 approved"""
        _, stage = sample_task
        await repo.mark_waiting_approval(stage.id)
        await repo.approve_stage(stage.id, approved_by="admin")

        loaded = await repo.get_stage(stage.id)
        assert loaded.status == StageStatus.PENDING.value
        assert loaded.approval_status == ApprovalStatus.APPROVED.value
        assert loaded.approved_by == "admin"
        assert loaded.approved_at is not None

    @pytest.mark.asyncio
    async def test_reject_stage_creates_new_llm_thinking(self, repo: DiagnosisRepository, sample_task):
        """测试目的：reject_stage 应标记当前 stage 为 failed，并创建新的 LLM_THINKING stage"""
        task, stage = sample_task
        # 先通过 complete_and_next 创建一个 TOOL_CALL stage
        tool_stage = await repo.complete_and_next(
            stage_id=stage.id,
            output_data={},
            next_stage_type=StageType.TOOL_CALL.value,
            next_tool_name="heapdump",
            next_tool_arguments={"file": "/tmp/dump.hprof"},
        )

        # 拒绝审核
        new_stage = await repo.reject_stage(tool_stage.id, rejected_by="security_admin")

        # 验证 tool_call stage 被标记为 failed
        rejected = await repo.get_stage(tool_stage.id)
        assert rejected.status == StageStatus.FAILED.value
        assert rejected.approval_status == ApprovalStatus.REJECTED.value
        assert rejected.approved_by == "security_admin"
        assert "拒绝" in rejected.error_message

        # 验证新创建的 LLM_THINKING stage
        assert new_stage is not None
        assert new_stage.stage_type == StageType.LLM_THINKING.value
        assert new_stage.status == StageStatus.PENDING.value
        assert "rejected_tool_call" in new_stage.input_data
        assert new_stage.input_data["rejected_tool_call"]["tool_name"] == "heapdump"

    @pytest.mark.asyncio
    async def test_reject_nonexistent_stage_raises(self, repo: DiagnosisRepository, memory_db):
        """测试目的：reject_stage 传入不存在的 stage_id 应抛出 ValueError"""
        with pytest.raises(ValueError, match="Stage not found"):
            await repo.reject_stage(99999, rejected_by="admin")

    @pytest.mark.asyncio
    async def test_get_pending_approval_stages(self, repo: DiagnosisRepository, sample_task):
        """测试目的：get_pending_approval_stages 应只返回 waiting_approval 状态的 stage"""
        _, stage = sample_task
        await repo.mark_waiting_approval(stage.id)

        pending_approvals = await repo.get_pending_approval_stages()
        assert len(pending_approvals) == 1
        assert pending_approvals[0].id == stage.id

    @pytest.mark.asyncio
    async def test_get_pending_approval_stages_empty(self, repo: DiagnosisRepository, sample_task):
        """测试目的：没有 waiting_approval 的 stage 时，应返回空列表"""
        pending_approvals = await repo.get_pending_approval_stages()
        assert len(pending_approvals) == 0


# ======================== 阶段查询 ========================

class TestStageQueries:
    """阶段查询测试"""

    @pytest.mark.asyncio
    async def test_get_task_stages_ordered_by_seq(self, repo: DiagnosisRepository, sample_task):
        """测试目的：get_task_stages 应返回 task 下所有 stage，按 stage_seq 升序排列"""
        task, stage = sample_task

        # 创建更多 stage
        await repo.complete_and_next(
            stage_id=stage.id,
            output_data={},
            next_stage_type=StageType.LLM_THINKING.value,
        )

        stages = await repo.get_task_stages(task.task_id)
        assert len(stages) == 2
        assert stages[0].stage_seq == 1
        assert stages[1].stage_seq == 2

    @pytest.mark.asyncio
    async def test_get_stage_by_id(self, repo: DiagnosisRepository, sample_task):
        """测试目的：get_stage 根据数据库 ID 获取 stage 应返回正确的记录"""
        _, stage = sample_task
        loaded = await repo.get_stage(stage.id)
        assert loaded is not None
        assert loaded.id == stage.id

    @pytest.mark.asyncio
    async def test_get_stage_not_found(self, repo: DiagnosisRepository, memory_db):
        """测试目的：get_stage 查询不存在的 ID 应返回 None"""
        loaded = await repo.get_stage(99999)
        assert loaded is None

    @pytest.mark.asyncio
    async def test_get_latest_stage(self, repo: DiagnosisRepository, sample_task):
        """测试目的：get_latest_stage 应返回 task 下 stage_seq 最大的 stage"""
        task, stage = sample_task

        next_stage = await repo.complete_and_next(
            stage_id=stage.id,
            output_data={},
            next_stage_type=StageType.LLM_THINKING.value,
        )

        latest = await repo.get_latest_stage(task.task_id)
        assert latest is not None
        assert latest.stage_seq == 2
        assert latest.id == next_stage.id

    @pytest.mark.asyncio
    async def test_get_latest_stage_for_empty_task(self, repo: DiagnosisRepository, memory_db):
        """测试目的：不存在的 task_id 调用 get_latest_stage 应返回 None"""
        latest = await repo.get_latest_stage("non-existent-task")
        assert latest is None


# ======================== 故障恢复 ========================

class TestFaultRecovery:
    """故障恢复机制测试"""

    @pytest.mark.asyncio
    async def test_check_and_fail_stale_tasks_marks_failed(self, repo: DiagnosisRepository, sample_task):
        """测试目的：running 任务的最新 stage 为 failed 时，check_and_fail_stale_tasks 应标记任务为 failed"""
        task, stage = sample_task

        # 让 stage 达到最大重试次数，变为 failed
        await repo.mark_failed(stage.id, "错误1")
        await repo.mark_failed(stage.id, "错误2")
        await repo.mark_failed(stage.id, "错误3")

        count = await repo.check_and_fail_stale_tasks()
        assert count == 1

        loaded = await repo.get_task(task.task_id)
        assert loaded.status == TaskStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_check_and_fail_stale_tasks_no_failures(self, repo: DiagnosisRepository, sample_task):
        """测试目的：没有 failed stage 时，check_and_fail_stale_tasks 应返回 0"""
        count = await repo.check_and_fail_stale_tasks()
        assert count == 0

    @pytest.mark.asyncio
    async def test_check_and_fail_stale_tasks_ignores_completed_tasks(self, repo: DiagnosisRepository, sample_task):
        """测试目的：已 completed 的任务不应被 check_and_fail_stale_tasks 检查或修改"""
        task, stage = sample_task
        await repo.complete_task(task.task_id, "已完成")

        count = await repo.check_and_fail_stale_tasks()
        assert count == 0

        loaded = await repo.get_task(task.task_id)
        assert loaded.status == TaskStatus.COMPLETED.value  # 保持不变


# ======================== 完整 ReAct 流转 ========================

class TestReActFlow:
    """模拟完整的 ReAct 诊断链路状态流转"""

    @pytest.mark.asyncio
    async def test_full_react_cycle(self, repo: DiagnosisRepository):
        """
        测试目的：验证完整的 USER_QUERY → LLM_THINKING → TOOL_CALL → TOOL_RESULT
                   → LLM_THINKING → LLM_CONCLUSION → task completed 流程
        """
        # 1. 创建任务
        task = await repo.create_task(session_id="s1", user_query="查看 JVM 状态")
        stages = await repo.get_task_stages(task.task_id)
        uq_stage = stages[0]
        assert uq_stage.stage_type == StageType.USER_QUERY.value

        # 2. USER_QUERY → LLM_THINKING
        llm1 = await repo.complete_and_next(
            stage_id=uq_stage.id,
            output_data={"user_query": "查看 JVM 状态"},
            next_stage_type=StageType.LLM_THINKING.value,
            next_input_data={"instruction": "分析用户问题"},
        )
        assert llm1.stage_type == StageType.LLM_THINKING.value
        assert llm1.stage_seq == 2

        # 3. LLM_THINKING → TOOL_CALL
        tc = await repo.complete_and_next(
            stage_id=llm1.id,
            output_data={"thinking": "需要调用 jvm 命令", "action_type": "tool_call"},
            next_stage_type=StageType.TOOL_CALL.value,
            next_tool_name="jvm",
            next_tool_arguments={},
        )
        assert tc.stage_type == StageType.TOOL_CALL.value
        assert tc.stage_seq == 3

        # 4. TOOL_CALL → TOOL_RESULT
        tr = await repo.complete_and_next(
            stage_id=tc.id,
            output_data={"tool_result": "JVM info: ..."},
            next_stage_type=StageType.TOOL_RESULT.value,
            next_input_data={"tool_name": "jvm", "tool_result": "JVM info: ..."},
        )
        assert tr.stage_type == StageType.TOOL_RESULT.value
        assert tr.stage_seq == 4

        # 5. TOOL_RESULT → LLM_THINKING (第二轮)
        llm2 = await repo.complete_and_next(
            stage_id=tr.id,
            output_data={"forwarded_to_llm": True},
            next_stage_type=StageType.LLM_THINKING.value,
            next_input_data={"tool_result": "JVM info: ..."},
        )
        assert llm2.stage_type == StageType.LLM_THINKING.value
        assert llm2.stage_seq == 5

        # 6. LLM_THINKING → LLM_CONCLUSION
        conclusion = await repo.complete_and_next(
            stage_id=llm2.id,
            output_data={"thinking": "分析完毕", "action_type": "conclude"},
            next_stage_type=StageType.LLM_CONCLUSION.value,
            next_input_data={"conclusion": "JVM 状态正常"},
        )
        assert conclusion.stage_type == StageType.LLM_CONCLUSION.value
        assert conclusion.stage_seq == 6

        # 7. LLM_CONCLUSION → completed
        await repo.complete_stage(conclusion.id, output_data={"conclusion": "JVM 状态正常"})
        await repo.complete_task(task.task_id, "JVM 状态正常")

        # 验证最终状态
        final_task = await repo.get_task(task.task_id)
        assert final_task.status == TaskStatus.COMPLETED.value
        assert final_task.conclusion == "JVM 状态正常"
        assert final_task.current_stage_seq == 6

        all_stages = await repo.get_task_stages(task.task_id)
        assert len(all_stages) == 6
        # 前 5 个都应该是 completed，最后一个也是 completed
        for s in all_stages:
            assert s.status == StageStatus.COMPLETED.value
