"""
StageHandler 和 StageHandlerRegistry 测试

测试处理器注册表和 5 个具体阶段处理器的业务逻辑：
- UserQueryHandler
- LlmThinkingHandler
- ToolCallHandler
- ToolResultHandler
- LlmConclusionHandler
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

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
from control_platform.event.handler import (
    LlmConclusionHandler,
    LlmThinkingHandler,
    StageHandler,
    StageHandlerRegistry,
    ToolCallHandler,
    ToolResultHandler,
    UserQueryHandler,
)
from control_platform.models.action import ActionType, DecisionResult


# ======================== 注册表测试 ========================

class TestStageHandlerRegistry:
    """阶段处理器注册表测试"""

    def test_register_and_get_handler(self):
        """测试目的：注册一个处理器后，通过 stage_type 应能查找到它"""
        registry = StageHandlerRegistry()
        handler = MagicMock(spec=StageHandler)
        handler.handler_name = "TestHandler"

        registry.register("USER_QUERY", handler)
        assert registry.get_handler("USER_QUERY") is handler

    def test_override_registration(self):
        """测试目的：对同一 stage_type 重复注册，后者应覆盖前者"""
        registry = StageHandlerRegistry()
        handler1 = MagicMock(spec=StageHandler)
        handler1.handler_name = "Handler1"
        handler2 = MagicMock(spec=StageHandler)
        handler2.handler_name = "Handler2"

        registry.register("USER_QUERY", handler1)
        registry.register("USER_QUERY", handler2)
        assert registry.get_handler("USER_QUERY") is handler2

    def test_get_handler_not_found(self):
        """测试目的：查询未注册的 stage_type 应返回 None"""
        registry = StageHandlerRegistry()
        assert registry.get_handler("UNKNOWN_TYPE") is None

    def test_registered_types(self):
        """测试目的：registered_types 应返回所有已注册的 stage_type 列表"""
        registry = StageHandlerRegistry()
        handler = MagicMock(spec=StageHandler)
        handler.handler_name = "H"

        registry.register("A", handler)
        registry.register("B", handler)
        registry.register("C", handler)

        types = registry.registered_types
        assert set(types) == {"A", "B", "C"}


# ======================== UserQueryHandler 测试 ========================

class TestUserQueryHandler:
    """USER_QUERY 阶段处理器测试"""

    @pytest.mark.asyncio
    async def test_handle_creates_llm_thinking_stage(self, repo: DiagnosisRepository, sample_task):
        """测试目的：UserQueryHandler 应完成当前 stage 并创建 LLM_THINKING 类型的下一个 stage"""
        task, stage = sample_task
        handler = UserQueryHandler()

        await handler.handle(task, stage, repo)

        # 验证当前 stage 已完成
        current = await repo.get_stage(stage.id)
        assert current.status == StageStatus.COMPLETED.value

        # 验证创建了 LLM_THINKING stage
        stages = await repo.get_task_stages(task.task_id)
        assert len(stages) == 2
        next_stage = stages[1]
        assert next_stage.stage_type == StageType.LLM_THINKING.value
        assert next_stage.status == StageStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_handle_output_contains_user_query(self, repo: DiagnosisRepository, sample_task):
        """测试目的：UserQueryHandler 的 output_data 应包含原始用户问题"""
        task, stage = sample_task
        handler = UserQueryHandler()

        await handler.handle(task, stage, repo)

        current = await repo.get_stage(stage.id)
        assert current.output_data["user_query"] == task.user_query


# ======================== LlmThinkingHandler 测试 ========================

class TestLlmThinkingHandler:
    """LLM_THINKING 阶段处理器测试"""

    @pytest.mark.asyncio
    async def test_tool_call_decision(self, repo: DiagnosisRepository, sample_task,
                                       mock_decision_engine, mock_context_builder):
        """测试目的：决策引擎返回 TOOL_CALL 时，应创建 TOOL_CALL 类型的下一个 stage 并携带工具信息"""
        task, stage = sample_task
        # 先完成 USER_QUERY，创建 LLM_THINKING
        llm_stage = await repo.complete_and_next(
            stage_id=stage.id,
            output_data={},
            next_stage_type=StageType.LLM_THINKING.value,
        )

        mock_decision_engine.decide.return_value = DecisionResult(
            action_type=ActionType.TOOL_CALL,
            tool_name="jvm",
            tool_arguments={"verbose": True},
            thinking="需要查看 JVM 信息",
        )

        handler = LlmThinkingHandler(mock_decision_engine, mock_context_builder)
        await handler.handle(task, llm_stage, repo)

        # 验证创建了 TOOL_CALL stage
        stages = await repo.get_task_stages(task.task_id)
        assert len(stages) == 3
        tool_stage = stages[2]
        assert tool_stage.stage_type == StageType.TOOL_CALL.value
        assert tool_stage.tool_name == "jvm"
        assert tool_stage.tool_arguments == {"verbose": True}

    @pytest.mark.asyncio
    async def test_conclude_decision(self, repo: DiagnosisRepository, sample_task,
                                      mock_decision_engine, mock_context_builder):
        """测试目的：决策引擎返回 CONCLUDE 时，应创建 LLM_CONCLUSION 类型的下一个 stage"""
        task, stage = sample_task
        llm_stage = await repo.complete_and_next(
            stage_id=stage.id,
            output_data={},
            next_stage_type=StageType.LLM_THINKING.value,
        )

        mock_decision_engine.decide.return_value = DecisionResult(
            action_type=ActionType.CONCLUDE,
            conclusion="诊断结论：一切正常",
            thinking="分析完毕",
        )

        handler = LlmThinkingHandler(mock_decision_engine, mock_context_builder)
        await handler.handle(task, llm_stage, repo)

        stages = await repo.get_task_stages(task.task_id)
        assert len(stages) == 3
        conclusion_stage = stages[2]
        assert conclusion_stage.stage_type == StageType.LLM_CONCLUSION.value
        assert conclusion_stage.input_data["conclusion"] == "诊断结论：一切正常"

    @pytest.mark.asyncio
    async def test_engine_error_marks_failed(self, repo: DiagnosisRepository, sample_task,
                                              mock_decision_engine, mock_context_builder):
        """测试目的：决策引擎抛出异常时，应调用 mark_failed，且未达上限时保持 pending"""
        task, stage = sample_task
        llm_stage = await repo.complete_and_next(
            stage_id=stage.id,
            output_data={},
            next_stage_type=StageType.LLM_THINKING.value,
        )

        mock_decision_engine.decide.side_effect = Exception("LLM API 超时")

        handler = LlmThinkingHandler(mock_decision_engine, mock_context_builder)
        await handler.handle(task, llm_stage, repo)

        loaded = await repo.get_stage(llm_stage.id)
        assert loaded.retry_count == 1
        assert loaded.error_message == "LLM API 超时"

    @pytest.mark.asyncio
    async def test_engine_error_final_failure_fails_task(self, repo: DiagnosisRepository, sample_task,
                                                          mock_decision_engine, mock_context_builder):
        """测试目的：决策引擎连续失败达到 max_retries 时，应标记 task 为 failed"""
        task, stage = sample_task
        llm_stage = await repo.complete_and_next(
            stage_id=stage.id,
            output_data={},
            next_stage_type=StageType.LLM_THINKING.value,
        )

        mock_decision_engine.decide.side_effect = Exception("持续错误")
        handler = LlmThinkingHandler(mock_decision_engine, mock_context_builder)

        # 执行 3 次（max_retries=3）
        for _ in range(3):
            await handler.handle(task, llm_stage, repo)

        loaded_task = await repo.get_task(task.task_id)
        assert loaded_task.status == TaskStatus.FAILED.value


# ======================== ToolCallHandler 测试 ========================

class TestToolCallHandler:
    """TOOL_CALL 阶段处理器测试"""

    @pytest.fixture
    def mock_client_session(self):
        """创建 Mock ClientSession"""
        session = AsyncMock()
        session.next_request_id = MagicMock(return_value=42)  # 同步方法
        session.active = True
        session.initialized = True
        return session

    @pytest.mark.asyncio
    async def test_needs_approval_stops_processing(self, repo: DiagnosisRepository, sample_task,
                                                     mock_session_manager, mock_mcp_handler):
        """测试目的：高危命令需要审核时，应标记为 waiting_approval 并停止处理"""
        task, stage = sample_task
        tool_stage = await repo.complete_and_next(
            stage_id=stage.id,
            output_data={},
            next_stage_type=StageType.TOOL_CALL.value,
            next_tool_name="heapdump",  # 高危命令
            next_tool_arguments={"file": "/tmp/dump.hprof"},
        )

        handler = ToolCallHandler(mock_session_manager, mock_mcp_handler)
        await handler.handle(task, tool_stage, repo)

        loaded = await repo.get_stage(tool_stage.id)
        assert loaded.status == StageStatus.WAITING_APPROVAL.value

    @pytest.mark.asyncio
    async def test_approved_stage_skips_approval(self, repo: DiagnosisRepository, sample_task,
                                                   mock_session_manager, mock_mcp_handler,
                                                   mock_client_session):
        """测试目的：已经 approved 的 stage 不需要再走审核流程，应直接发送请求"""
        task, stage = sample_task
        tool_stage = await repo.complete_and_next(
            stage_id=stage.id,
            output_data={},
            next_stage_type=StageType.TOOL_CALL.value,
            next_tool_name="heapdump",
            next_tool_arguments={},
            next_approval_status=ApprovalStatus.APPROVED.value,
        )

        # 模拟有可用 session
        mock_session_manager.get_session.return_value = mock_client_session
        mock_client_session.send_message.return_value = True

        handler = ToolCallHandler(mock_session_manager, mock_mcp_handler)
        await handler.handle(task, tool_stage, repo)

        # 异步模式下，handler 只发送请求不等待，stage 仍然是 pending
        loaded = await repo.get_stage(tool_stage.id)
        assert loaded.status == StageStatus.PENDING.value
        # 应该调用了 send_message
        mock_client_session.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_session_unavailable_stays_pending(self, repo: DiagnosisRepository, sample_task,
                                                      mock_session_manager, mock_mcp_handler):
        """测试目的：Arthas 客户端 session 不可用时，应保持 pending 不消耗重试次数，等待下次轮询重试"""
        task, stage = sample_task
        tool_stage = await repo.complete_and_next(
            stage_id=stage.id,
            output_data={},
            next_stage_type=StageType.TOOL_CALL.value,
            next_tool_name="jvm",
        )

        mock_session_manager.get_session.return_value = None  # 不可用

        handler = ToolCallHandler(mock_session_manager, mock_mcp_handler)
        await handler.handle(task, tool_stage, repo)

        loaded = await repo.get_stage(tool_stage.id)
        # session 断连是临时状态，不应消耗重试次数，保持 pending 等待重连
        assert loaded.status == StageStatus.PENDING.value
        assert loaded.retry_count == 0
        assert loaded.error_message is None

    @pytest.mark.asyncio
    async def test_tool_call_success_sends_message(self, repo: DiagnosisRepository, sample_task,
                                                     mock_session_manager, mock_mcp_handler,
                                                     mock_client_session):
        """测试目的：工具调用应只发送请求不等待，handler 结束后 stage 仍为 pending"""
        task, stage = sample_task
        tool_stage = await repo.complete_and_next(
            stage_id=stage.id,
            output_data={},
            next_stage_type=StageType.TOOL_CALL.value,
            next_tool_name="jvm",
            next_tool_arguments={},
        )

        mock_session_manager.get_session.return_value = mock_client_session
        mock_client_session.send_message.return_value = True

        handler = ToolCallHandler(mock_session_manager, mock_mcp_handler)
        await handler.handle(task, tool_stage, repo)

        # 异步模式下，handler 只发送不等待，stage 仍为 pending
        loaded = await repo.get_stage(tool_stage.id)
        assert loaded.status == StageStatus.PENDING.value

        # 验证调用了 send_message
        mock_client_session.send_message.assert_called_once()

        # 验证构建了正确的 tools/call 请求（含 _meta 中的 taskId 和 stageId）
        call_args = mock_client_session.send_message.call_args
        request_msg = call_args[0][0]
        assert request_msg["method"] == "tools/call"
        assert request_msg["params"]["name"] == "jvm"
        assert request_msg["params"]["_meta"]["taskId"] == task.task_id
        assert request_msg["params"]["_meta"]["stageId"] == str(tool_stage.stage_seq)

    @pytest.mark.asyncio
    async def test_tool_call_send_failure_no_mark_failed(self, repo: DiagnosisRepository, sample_task,
                                                          mock_session_manager, mock_mcp_handler,
                                                          mock_client_session):
        """测试目的：发送失败时不 mark_failed，stage 保持 pending 等待下次轮询重试"""
        task, stage = sample_task
        tool_stage = await repo.complete_and_next(
            stage_id=stage.id,
            output_data={},
            next_stage_type=StageType.TOOL_CALL.value,
            next_tool_name="jvm",
        )

        mock_session_manager.get_session.return_value = mock_client_session
        mock_client_session.send_message.return_value = False  # 发送失败

        handler = ToolCallHandler(mock_session_manager, mock_mcp_handler)
        await handler.handle(task, tool_stage, repo)

        # 发送失败不应 mark_failed，stage 保持 pending 等下次轮询重试（幂等安全）
        loaded = await repo.get_stage(tool_stage.id)
        assert loaded.status == StageStatus.PENDING.value
        assert loaded.retry_count == 0

    @pytest.mark.asyncio
    async def test_tool_call_request_contains_meta(self, repo: DiagnosisRepository, sample_task,
                                                     mock_session_manager, mock_mcp_handler,
                                                     mock_client_session):
        """测试目的：handler 发送的请求应在 _meta 中包含 taskId 和 stageId，供 Java 端回传"""
        task, stage = sample_task
        tool_stage = await repo.complete_and_next(
            stage_id=stage.id,
            output_data={},
            next_stage_type=StageType.TOOL_CALL.value,
            next_tool_name="thread",
            next_tool_arguments={"n": 5},
        )

        mock_session_manager.get_session.return_value = mock_client_session
        mock_client_session.send_message.return_value = True

        handler = ToolCallHandler(mock_session_manager, mock_mcp_handler)
        await handler.handle(task, tool_stage, repo)

        call_args = mock_client_session.send_message.call_args
        request_msg = call_args[0][0]
        meta = request_msg["params"]["_meta"]
        assert meta["taskId"] == task.task_id
        assert meta["stageId"] == str(tool_stage.stage_seq)
        assert request_msg["params"]["name"] == "thread"
        assert request_msg["params"]["arguments"] == {"n": 5}


class TestExtractToolResult:
    """ToolCallHandler.extract_tool_result 静态方法测试"""

    def test_extract_text_content(self):
        """测试目的：从标准 content[].text 格式中提取文本"""
        result = {"content": [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]}
        text = ToolCallHandler.extract_tool_result(result)
        assert text == "hello\nworld"

    def test_extract_empty_content(self):
        """测试目的：content 为空列表时应回退到 JSON 序列化"""
        result = {"content": []}
        text = ToolCallHandler.extract_tool_result(result)
        assert "content" in text  # 应该是 JSON 格式

    def test_extract_non_text_content(self):
        """测试目的：content 中没有 type=text 的项时，应回退到 JSON 序列化"""
        result = {"content": [{"type": "image", "data": "..."}]}
        text = ToolCallHandler.extract_tool_result(result)
        assert "content" in text

    def test_extract_no_content_key(self):
        """测试目的：result 中没有 content 字段时，应回退到 JSON 序列化"""
        result = {"data": "raw_data"}
        text = ToolCallHandler.extract_tool_result(result)
        assert "data" in text


# ======================== ToolResultHandler 测试 ========================

class TestToolResultHandler:
    """TOOL_RESULT 阶段处理器测试"""

    @pytest.mark.asyncio
    async def test_handle_creates_llm_thinking(self, repo: DiagnosisRepository, sample_task):
        """测试目的：ToolResultHandler 应完成当前 stage 并创建 LLM_THINKING 类型的下一个 stage"""
        task, stage = sample_task
        # 构建到 TOOL_RESULT 阶段
        llm_stage = await repo.complete_and_next(
            stage_id=stage.id,
            output_data={},
            next_stage_type=StageType.LLM_THINKING.value,
        )
        tc_stage = await repo.complete_and_next(
            stage_id=llm_stage.id,
            output_data={},
            next_stage_type=StageType.TOOL_CALL.value,
            next_tool_name="jvm",
        )
        tr_stage = await repo.complete_and_next(
            stage_id=tc_stage.id,
            output_data={},
            next_stage_type=StageType.TOOL_RESULT.value,
            next_input_data={"tool_name": "jvm", "tool_result": "JVM info"},
        )

        handler = ToolResultHandler()
        await handler.handle(task, tr_stage, repo)

        stages = await repo.get_task_stages(task.task_id)
        assert len(stages) == 5
        next_llm = stages[4]
        assert next_llm.stage_type == StageType.LLM_THINKING.value
        assert next_llm.input_data["tool_name"] == "jvm"
        assert next_llm.input_data["tool_result"] == "JVM info"


# ======================== LlmConclusionHandler 测试 ========================

class TestLlmConclusionHandler:
    """LLM_CONCLUSION 阶段处理器测试"""

    @pytest.mark.asyncio
    async def test_handle_completes_task(self, repo: DiagnosisRepository, sample_task):
        """测试目的：LlmConclusionHandler 应完成 stage 并标记 task 为 completed，写入结论"""
        task, stage = sample_task
        llm_stage = await repo.complete_and_next(
            stage_id=stage.id,
            output_data={},
            next_stage_type=StageType.LLM_CONCLUSION.value,
            next_input_data={"conclusion": "JVM 状态正常", "thinking": "分析完毕"},
        )

        handler = LlmConclusionHandler()
        await handler.handle(task, llm_stage, repo)

        # 验证 stage 完成
        loaded_stage = await repo.get_stage(llm_stage.id)
        assert loaded_stage.status == StageStatus.COMPLETED.value

        # 验证 task 完成
        loaded_task = await repo.get_task(task.task_id)
        assert loaded_task.status == TaskStatus.COMPLETED.value
        assert loaded_task.conclusion == "JVM 状态正常"

    @pytest.mark.asyncio
    async def test_handle_default_conclusion(self, repo: DiagnosisRepository, sample_task):
        """测试目的：input_data 缺少 conclusion 时，应使用默认值 '诊断完成（无结论）'"""
        task, stage = sample_task
        llm_stage = await repo.complete_and_next(
            stage_id=stage.id,
            output_data={},
            next_stage_type=StageType.LLM_CONCLUSION.value,
            next_input_data={},  # 不含 conclusion
        )

        handler = LlmConclusionHandler()
        await handler.handle(task, llm_stage, repo)

        loaded_task = await repo.get_task(task.task_id)
        assert loaded_task.conclusion == "诊断完成（无结论）"
