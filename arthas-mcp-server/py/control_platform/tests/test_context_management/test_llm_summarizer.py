"""
LLMSummarizer 单元测试

使用 mock 替代真实 LLM 调用，验证摘要接口的行为。
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from control_platform.decision.context_management.llm_summarizer import LLMSummarizer


def _make_mock_response(content: str, prompt_tokens: int = 100, completion_tokens: int = 50):
    """构造模拟的 OpenAI API 响应"""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    response.usage = MagicMock()
    response.usage.prompt_tokens = prompt_tokens
    response.usage.completion_tokens = completion_tokens
    return response


class TestSummarizeSingle:
    """单条工具结果摘要测试"""

    @pytest.mark.asyncio
    async def test_summarize_single_success(self):
        """LLM 摘要成功"""
        summarizer = LLMSummarizer(api_key="test-key", base_url="http://test", model="test-model")
        mock_response = _make_mock_response("摘要：线程 Thread-1 处于 WAITING 状态，疑似死锁")

        with patch.object(
            summarizer._client.chat.completions,
            "create",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await summarizer.summarize_single(
                content="大量的 jstack 输出...",
                context_hint={"tool_name": "jstack", "user_query": "排查死锁"},
            )

        assert "WAITING" in result
        print(f"[对比报告] 单条摘要结果: {result}")

    @pytest.mark.asyncio
    async def test_summarize_single_timeout(self):
        """LLM 超时应抛出 TimeoutError"""
        summarizer = LLMSummarizer(
            api_key="test-key", base_url="http://test", model="test-model", timeout=0.1
        )

        async def slow_response(*args, **kwargs):
            await asyncio.sleep(5)

        with patch.object(
            summarizer._client.chat.completions,
            "create",
            new_callable=AsyncMock,
            side_effect=slow_response,
        ):
            with pytest.raises(TimeoutError):
                await summarizer.summarize_single(
                    content="测试超时",
                    context_hint={"tool_name": "test", "user_query": "test"},
                )

    @pytest.mark.asyncio
    async def test_summarize_single_api_error(self):
        """LLM API 错误应向上抛出"""
        summarizer = LLMSummarizer(api_key="test-key", base_url="http://test", model="test-model")

        with patch.object(
            summarizer._client.chat.completions,
            "create",
            new_callable=AsyncMock,
            side_effect=Exception("API Error"),
        ):
            with pytest.raises(Exception, match="API Error"):
                await summarizer.summarize_single(content="测试", context_hint={})


class TestSummarizeConversation:
    """全文对话摘要测试"""

    @pytest.mark.asyncio
    async def test_summarize_conversation_success(self):
        """全文对话摘要成功"""
        summarizer = LLMSummarizer(api_key="test-key", base_url="http://test", model="test-model")
        mock_summary = (
            "### 已执行的工具\n- jstack → 发现 Thread-1 WAITING\n"
            "### 关键发现\n- 线程死锁\n"
            "### 当前诊断阶段\n初步分析完成\n"
            "### 待验证假设\n- 需确认是否为数据库连接池耗尽"
        )
        mock_response = _make_mock_response(mock_summary, prompt_tokens=500, completion_tokens=100)

        messages = [
            {"role": "user", "content": "排查内存泄漏"},
            {"role": "assistant", "content": "我来用 jstack 分析"},
            {"role": "tool", "tool_call_id": "c1", "content": "Thread-1: WAITING"},
            {"role": "assistant", "content": "发现线程阻塞"},
        ]

        with patch.object(
            summarizer._client.chat.completions,
            "create",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await summarizer.summarize_conversation(
                messages=messages,
                user_query="排查内存泄漏",
            )

        assert "已执行的工具" in result
        assert "关键发现" in result
        print(f"[对比报告] 全文摘要结果:\n{result}")

    @pytest.mark.asyncio
    async def test_summarize_conversation_timeout(self):
        """全文摘要超时"""
        summarizer = LLMSummarizer(
            api_key="test-key", base_url="http://test", model="test-model", timeout=0.1
        )

        async def slow_response(*args, **kwargs):
            await asyncio.sleep(5)

        with patch.object(
            summarizer._client.chat.completions,
            "create",
            new_callable=AsyncMock,
            side_effect=slow_response,
        ):
            with pytest.raises(TimeoutError):
                await summarizer.summarize_conversation(
                    messages=[{"role": "user", "content": "test"}],
                    user_query="test",
                )


class TestFormatMessages:
    """消息格式化测试"""

    def test_format_various_roles(self):
        messages = [
            {"role": "user", "content": "帮我排查"},
            {"role": "assistant", "content": "好的"},
            {"role": "tool", "tool_call_id": "c1", "content": "结果"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"function": {"name": "jstack", "arguments": '{"pid": "1"}'}}
                ],
            },
            {"role": "system", "content": "系统消息"},
        ]
        result = LLMSummarizer._format_messages_for_summary(messages)
        assert "[用户]" in result
        assert "[助手]" in result
        assert "[工具结果]" in result
        assert "[助手-工具调用]" in result
        assert "[系统]" in result


class TestModelConfig:
    """模型配置测试"""

    def test_custom_model(self):
        summarizer = LLMSummarizer(api_key="k", base_url="u", model="gpt-4o-mini")
        assert summarizer.model == "gpt-4o-mini"

    def test_custom_timeout(self):
        summarizer = LLMSummarizer(api_key="k", base_url="u", model="m", timeout=30.0)
        assert summarizer.timeout == 30.0
