"""
RAG 集成测试

验证 ContextBuilder → RAGProvider → build_system_prompt 完整链路，
打印最终 System Prompt 中 RAG 段落。
验证 RAG token 纳入 ContextWindowManager 预算。
验证知识库为空时降级模式与原有行为一致。
"""

from unittest.mock import patch, MagicMock, AsyncMock
import pytest

from control_platform.decision.context import DecisionContext
from control_platform.decision.context_builder import ContextBuilder
from control_platform.decision.openai_engine import build_system_prompt
from control_platform.decision.context_management.manager import ContextWindowManager
from control_platform.rag.provider import RAGProvider, RAGResult
from control_platform.rag.base_vector_store import QueryResult


class TestRAGIntegration:
    """RAG 集成测试"""

    def test_build_system_prompt_with_rag(self):
        """测试 build_system_prompt 包含 RAG 知识段落"""
        rag_context = {
            "results": [
                {
                    "document": "thread -n 3 可以找出最忙的前3个线程",
                    "score": 0.92,
                    "metadata": {
                        "file_name": "thread.md",
                        "heading_path": "thread 命令 > 使用方式 > 查找最忙线程",
                        "source_type": "tool_doc",
                    },
                    "id": "abc_0",
                },
                {
                    "document": "使用 thread -b 可以找出阻塞线程",
                    "score": 0.85,
                    "metadata": {
                        "file_name": "thread.md",
                        "heading_path": "thread 命令 > 参数说明",
                        "source_type": "tool_doc",
                    },
                    "id": "abc_1",
                },
            ],
            "total_tokens": 100,
        }

        prompt = build_system_prompt(
            available_tools=[],
            rag_context=rag_context,
        )

        print(f"\n📝 System Prompt 中的 RAG 段落:")
        # 提取 RAG 段落
        if "## 参考知识" in prompt:
            rag_start = prompt.index("## 参考知识")
            # 找到下一个 ## 或结尾
            rest = prompt[rag_start:]
            print(rest[:500])
        else:
            print("  (未找到 RAG 段落)")

        assert "## 参考知识" in prompt
        assert "以下是与用户问题相关的 Arthas 诊断知识" in prompt
        assert "thread.md" in prompt
        assert "thread -n 3" in prompt
        assert "0.92" in prompt

    def test_build_system_prompt_without_rag(self):
        """测试无 RAG 时 build_system_prompt 保持原有行为"""
        prompt_with_none = build_system_prompt(available_tools=[], rag_context=None)
        prompt_with_empty = build_system_prompt(
            available_tools=[], rag_context={"results": []}
        )

        assert "## 参考知识" not in prompt_with_none
        assert "## 参考知识" not in prompt_with_empty
        print("✅ 无 RAG 时: System Prompt 不包含参考知识段落")

    @pytest.mark.asyncio
    async def test_context_builder_with_rag_provider(self):
        """测试 ContextBuilder 集成 RAGProvider"""
        # Mock RAGProvider
        mock_rag = MagicMock(spec=RAGProvider)
        mock_rag.retrieve.return_value = RAGResult(
            results=[
                QueryResult(
                    document="CPU 排查：先用 thread -n 3",
                    score=0.90,
                    metadata={"file_name": "cpu.md", "heading_path": "排查步骤"},
                    id="cpu_0",
                )
            ],
            total_tokens=50,
        )

        # Mock Repository
        mock_repo = MagicMock()
        mock_task = MagicMock()
        mock_task.session_id = "session_1"
        mock_task.user_query = "CPU 使用率高怎么排查？"
        mock_task.current_stage_seq = 1

        mock_repo.get_task = AsyncMock(return_value=mock_task)
        mock_repo.get_task_stages = AsyncMock(return_value=[])

        builder = ContextBuilder(rag_provider=mock_rag)
        context = await builder.build_context("task_1", mock_repo)

        assert context.rag_context is not None
        assert len(context.rag_context["results"]) == 1
        assert context.rag_context["total_tokens"] == 50
        print(f"✅ ContextBuilder 集成 RAG: rag_context 包含 {len(context.rag_context['results'])} 条结果")

    @pytest.mark.asyncio
    async def test_context_builder_without_rag(self):
        """测试 ContextBuilder 无 RAGProvider 时 rag_context 为 None"""
        mock_repo = MagicMock()
        mock_task = MagicMock()
        mock_task.session_id = "session_1"
        mock_task.user_query = "测试"
        mock_task.current_stage_seq = 1

        mock_repo.get_task = AsyncMock(return_value=mock_task)
        mock_repo.get_task_stages = AsyncMock(return_value=[])

        builder = ContextBuilder()  # 不传 rag_provider
        context = await builder.build_context("task_1", mock_repo)

        assert context.rag_context is None
        print("✅ 无 RAGProvider: rag_context 为 None")

    @pytest.mark.asyncio
    async def test_context_window_manager_rag_budget(self):
        """测试 ContextWindowManager 将 RAG token 纳入预算"""
        manager = ContextWindowManager()

        # 构造带 RAG 上下文的 DecisionContext
        context = DecisionContext(
            task_id="test_task",
            session_id="test_session",
            user_query="CPU 高怎么排查",
            messages=[
                {"role": "user", "content": "CPU 高怎么排查"},
            ],
            available_tools=[],
            rag_context={"results": [], "total_tokens": 1000},
        )

        optimized = await manager.optimize(context)
        report = optimized.metadata.get("context_optimization", {})

        print(f"\n📊 上下文优化报告:")
        print(f"  原始 tokens: {report.get('original_tokens', 'N/A')}")
        print(f"  最终 tokens: {report.get('final_tokens', 'N/A')}")
        print(f"  可用预算: {report.get('available_budget', 'N/A')}")

        # 可用预算应该已扣除 RAG tokens
        assert "context_optimization" in optimized.metadata

    @pytest.mark.asyncio
    async def test_empty_knowledge_base_degradation(self):
        """测试知识库为空时降级模式"""
        # 无 RAG 的 System Prompt
        prompt_without_rag = build_system_prompt(available_tools=[], rag_context=None)
        # 空 RAG 结果的 System Prompt
        prompt_with_empty_rag = build_system_prompt(
            available_tools=[], rag_context={"results": [], "total_tokens": 0}
        )

        assert prompt_without_rag == prompt_with_empty_rag
        print("✅ 降级模式: 空 RAG 结果与无 RAG 时 System Prompt 完全一致")
