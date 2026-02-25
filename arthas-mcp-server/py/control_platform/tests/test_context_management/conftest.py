"""
共享测试 Fixture

提供模拟 10 轮诊断对话数据工厂、mock 工具等共享 fixture。
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from control_platform.decision.context import DecisionContext
from control_platform.decision.context_management.token_counter import TokenCounter
from control_platform.db.models import StageStatus, StageType


# ==================== 测试数据工厂 ====================


def make_ten_round_messages():
    """
    生成模拟 10 轮诊断对话的消息列表

    包含：
    - 1 条用户提问
    - 10 轮 (assistant + tool) 交互
    - 其中 3 条大体积工具结果（>3000 tokens）
    - 其中 7 条正常体积工具结果
    """
    messages = [
        {
            "role": "user",
            "content": "帮我排查 Java 应用内存泄漏问题，PID 为 12345，最近频繁 Full GC",
            "stage_seq": 1,
            "stage_type": "USER_QUERY",
        }
    ]

    # 大体积轮次
    big_rounds = {2, 5, 8}
    seq = 2

    for i in range(1, 11):
        # LLM 推理
        messages.append({
            "role": "assistant",
            "content": f"第{i}轮分析：根据之前的数据，我需要进一步执行诊断工具。"
                       f"{'内存分析显示可能存在大量对象未被回收。' if i > 3 else '让我先收集基础信息。'}",
            "stage_seq": seq,
            "stage_type": "LLM_THINKING",
        })
        seq += 1

        # 工具调用
        tool_name = ["jstack", "heapdump", "vmopt", "dashboard", "thread",
                      "jad", "sc", "sm", "ognl", "mbean"][i - 1]

        if i in big_rounds:
            # 大体积工具结果（模拟 jstack/heapdump 输出）
            tool_content = f"=== {tool_name} 输出 ===\n"
            tool_content += "\n".join([
                f'"thread-{j}" #1{j:03d} prio=5 os_prio=0 tid=0x00007f{j:04x} nid=0x{j:04x} '
                f'waiting on condition [0x00007f{j:04x}000]\n'
                f'   java.lang.Thread.State: WAITING (parking)\n'
                f'\tat sun.misc.Unsafe.park(Native Method)\n'
                f'\tat java.util.concurrent.locks.LockSupport.park(LockSupport.java:175)\n'
                f'\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject.await(AbstractQueuedSynchronizer.java:2039)\n'
                for j in range(80)
            ])
        else:
            # 正常体积
            tool_content = f"=== {tool_name} 输出 ===\n"
            tool_content += f"工具 {tool_name} 执行完成，结果正常。\n" * 5

        messages.append({
            "role": "tool",
            "content": tool_content,
            "tool_call_id": f"call_{i}",
            "stage_seq": seq,
            "stage_type": "TOOL_CALL",
            "tool_name": tool_name,
        })
        seq += 1

    return messages


def make_ten_round_stages():
    """
    生成模拟 10 轮诊断对话的 DiagnosisStage 对象列表

    用于 ContextBuilder 和 repository 相关测试。
    """
    messages = make_ten_round_messages()
    stages = []

    for msg in messages:
        stage = MagicMock()
        stage.stage_seq = msg["stage_seq"]
        stage.stage_type = msg["stage_type"]
        stage.status = StageStatus.COMPLETED.value
        stage.summarized_content = None
        stage.summary_type = None
        stage.original_tokens = None
        stage.summary_tokens = None
        stage.tool_name = msg.get("tool_name")
        stage.tool_arguments = None
        stage.tool_result = msg.get("content") if msg.get("stage_type") == "TOOL_CALL" else None
        stage.approval_status = "not_required"

        if msg["stage_type"] == "USER_QUERY":
            stage.input_data = {"user_query": msg["content"]}
            stage.output_data = {}
        elif msg["stage_type"] == "LLM_THINKING":
            stage.input_data = {}
            stage.output_data = {"thinking": msg["content"]}
        elif msg["stage_type"] == "TOOL_CALL":
            stage.input_data = {}
            stage.output_data = {"tool_result": msg["content"]}
        else:
            stage.input_data = {}
            stage.output_data = {}

        stages.append(stage)

    return stages


def make_ten_round_context():
    """生成完整的 10 轮诊断 DecisionContext"""
    messages = make_ten_round_messages()
    return DecisionContext(
        task_id="test-task-10round",
        session_id="test-session",
        user_query="帮我排查 Java 应用内存泄漏问题，PID 为 12345，最近频繁 Full GC",
        messages=messages,
        available_tools=[],
        current_stage_seq=len(messages),
        metadata={},
    )


# ==================== Pytest Fixtures ====================


@pytest.fixture
def token_counter():
    """共享的 TokenCounter 实例"""
    return TokenCounter()


@pytest.fixture
def ten_round_messages():
    """10 轮诊断对话消息列表"""
    return make_ten_round_messages()


@pytest.fixture
def ten_round_stages():
    """10 轮诊断对话 Stage 列表"""
    return make_ten_round_stages()


@pytest.fixture
def ten_round_context():
    """10 轮诊断对话 DecisionContext"""
    return make_ten_round_context()


@pytest.fixture
def mock_llm_summarizer():
    """Mock 的 LLMSummarizer"""
    mock = MagicMock()
    mock.summarize_single = AsyncMock(return_value="摘要：线程阻塞，发现死锁")
    mock.summarize_conversation = AsyncMock(
        return_value=(
            "### 已执行的工具\n"
            "- jstack → 发现多个线程 WAITING\n"
            "- heapdump → 内存中大量未回收对象\n"
            "- dashboard → CPU 使用率异常\n\n"
            "### 关键发现\n"
            "- 线程池中 80% 的线程处于 WAITING 状态\n"
            "- 堆内存中 HashMap 对象占比过高\n\n"
            "### 当前诊断阶段\n"
            "已完成 10 轮诊断\n\n"
            "### 待验证假设\n"
            "- 可能存在连接池泄漏"
        )
    )
    mock.model = "test-mock-model"
    return mock


@pytest.fixture
def mock_repo():
    """Mock 的 DiagnosisRepository"""
    repo = MagicMock()
    repo.update_stage_summary = AsyncMock()
    repo.create_context_summary_stage = AsyncMock()
    return repo
