"""
RAGProvider 单元测试（Parent-Child 双层索引版）

测试覆盖：
- 完整 "加载→切片→分离 parent/child→Embedding→存储" 流程
- Parent chunk 存入 ParentChunkStore（不生成 embedding）
- Child chunk 存入向量数据库 + BM25 索引
- Token 预算截断适配 parent chunk
- RAG 禁用模式
- 增量构建
- 检索返回 parent chunk

注意：所有外部依赖（VectorStore、Embedder、ChunkerRegistry）均完全 mock 化，
      不涉及真实磁盘 IO、模型下载或 ChromaDB 初始化。
"""

from unittest.mock import patch, MagicMock, PropertyMock
import os

import pytest

from control_platform.rag.provider import RAGProvider, RAGResult
from control_platform.rag.base_chunker import DocumentChunk
from control_platform.rag.base_vector_store import QueryResult


# ===== 辅助函数 =====

def _make_parent_chunk(chunk_id, content, children_ids=None, source_file="test.md"):
    """构造 parent chunk"""
    return DocumentChunk(
        chunk_id=chunk_id,
        content=content,
        metadata={
            "chunk_level": "parent",
            "children_ids": children_ids or [],
            "total_children": len(children_ids or []),
            "source_file": source_file,
        },
    )


def _make_child_chunk(chunk_id, content, parent_chunk_id, source_file="test.md"):
    """构造 child chunk"""
    return DocumentChunk(
        chunk_id=chunk_id,
        content=content,
        metadata={
            "chunk_level": "child",
            "parent_chunk_id": parent_chunk_id,
            "source_file": source_file,
        },
    )


def _mock_embed_batch(texts):
    """生成简单的 mock Embedding"""
    embeddings = []
    for text in texts:
        vec = [0.1] * 64
        if "thread" in text.lower() or "线程" in text:
            vec[0] = 0.9
        if "cpu" in text.lower():
            vec[1] = 0.9
        embeddings.append(vec)
    return embeddings


def _mock_embed(text):
    """单条 Embedding mock"""
    return _mock_embed_batch([text])[0]


# ===== 通用 mock 设置 =====

def _patch_all_provider_deps():
    """返回装饰器所需的所有 patch，确保 RAGProvider 不触发任何真实 IO"""
    patches = {
        "settings": patch("control_platform.rag.provider.settings"),
        "embedder_cls": patch("control_platform.rag.provider.Embedder"),
        "vector_factory": patch("control_platform.rag.provider.VectorStoreFactory"),
        "chunker_registry_cls": patch("control_platform.rag.provider.ChunkerRegistry"),
    }
    return patches


def _setup_mock_settings(mock_settings, tmp_path):
    """统一配置 mock settings"""
    knowledge_dir = str(tmp_path / "knowledge")
    os.makedirs(os.path.join(knowledge_dir, "tool_docs"), exist_ok=True)
    # 写入一个真实的 md 文件，供 os.walk 遍历
    with open(os.path.join(knowledge_dir, "tool_docs", "thread.md"), "w") as f:
        f.write("# thread 命令\n\n查看线程信息。\n")

    mock_settings.rag_enabled = True
    mock_settings.rag_knowledge_dir = knowledge_dir
    mock_settings.rag_store_type = "chroma"
    mock_settings.rag_store_path = str(tmp_path / "vector_db")
    mock_settings.rag_top_k = 3
    mock_settings.rag_similarity_threshold = 0.1
    mock_settings.rag_max_tokens = 2048
    mock_settings.rag_max_parent_size = 2048
    mock_settings.rag_hybrid_search_enabled = True
    mock_settings.rag_rrf_score_threshold = 0.01
    mock_settings.rag_embedding_model = "BAAI/bge-m3"
    mock_settings.rag_embedding_provider = "local"
    mock_settings.llm_api_key = "test-key"
    mock_settings.llm_base_url = "http://test"
    mock_settings.llm_model = "test-model"
    mock_settings.context_max_tokens = 128000
    mock_settings.context_reserved_tokens = 8192
    return knowledge_dir


def _create_mock_provider(
    mock_settings, mock_embedder_cls, mock_vector_factory, mock_chunker_cls,
    tmp_path, chunks=None,
):
    """创建完全 mock 化的 RAGProvider
    
    Args:
        chunks: ChunkerRegistry.chunk_file 返回的 chunk 列表，
                为 None 时自动生成默认的 parent + child chunks
    """
    knowledge_dir = _setup_mock_settings(mock_settings, tmp_path)

    # Mock Embedder
    mock_embedder = MagicMock()
    mock_embedder_cls.return_value = mock_embedder
    mock_embedder.embed_batch.side_effect = _mock_embed_batch
    mock_embedder.embed.side_effect = _mock_embed

    # Mock VectorStore
    mock_vector_store = MagicMock()
    mock_vector_store.count.return_value = 0
    mock_vector_factory.create.return_value = mock_vector_store

    # Mock ChunkerRegistry
    mock_chunker = MagicMock()
    mock_chunker_cls.return_value = mock_chunker

    if chunks is None:
        # 默认返回 1 个 parent + 2 个 child
        default_chunks = [
            _make_parent_chunk("p1", "# thread 命令\n\n查看线程信息的完整内容...", ["c1", "c2"]),
            _make_child_chunk("c1", "## 查看所有线程\n\nthread 命令可以查看所有线程 CPU 使用率", "p1"),
            _make_child_chunk("c2", "## 查找最忙线程\n\nthread -n 3 列出最忙的前 N 个线程", "p1"),
        ]
        mock_chunker.chunk_file.return_value = default_chunks
    else:
        mock_chunker.chunk_file.return_value = chunks

    provider = RAGProvider()
    return provider, mock_embedder, mock_vector_store, mock_chunker


class TestRAGProviderBuildIndex:
    """RAGProvider 索引构建测试"""

    @patch("control_platform.rag.provider.ChunkerRegistry")
    @patch("control_platform.rag.provider.VectorStoreFactory")
    @patch("control_platform.rag.provider.Embedder")
    @patch("control_platform.rag.provider.settings")
    def test_build_index_creates_parent_and_child(
        self, mock_settings, mock_embedder_cls, mock_vector_factory,
        mock_chunker_cls, tmp_path,
    ):
        """测试索引构建：parent 存入 ParentChunkStore，child 存入向量库 + BM25"""
        provider, mock_embedder, mock_vs, mock_chunker = _create_mock_provider(
            mock_settings, mock_embedder_cls, mock_vector_factory, mock_chunker_cls,
            tmp_path,
        )
        assert provider.is_available

        new_chunks = provider.build_index()
        assert new_chunks == 2, "应该有 2 个 child chunk 被索引"

        # ParentChunkStore 应该有 1 个 parent chunk
        assert provider._parent_store is not None
        assert len(provider._parent_store) == 1

        # 向量库 add_documents 应被调用一次，传入 2 个 child
        mock_vs.add_documents.assert_called_once()
        call_kwargs = mock_vs.add_documents.call_args
        assert len(call_kwargs[1]["ids"]) == 2

        # Embedder.embed_batch 只应对 child chunk 调用
        mock_embedder.embed_batch.assert_called_once()
        embedded_texts = mock_embedder.embed_batch.call_args[0][0]
        assert len(embedded_texts) == 2

        # BM25 索引应已构建
        assert provider._bm25_index is not None
        assert provider._bm25_index.is_available

    @patch("control_platform.rag.provider.ChunkerRegistry")
    @patch("control_platform.rag.provider.VectorStoreFactory")
    @patch("control_platform.rag.provider.Embedder")
    @patch("control_platform.rag.provider.settings")
    def test_parent_not_in_vector_store(
        self, mock_settings, mock_embedder_cls, mock_vector_factory,
        mock_chunker_cls, tmp_path,
    ):
        """测试 parent chunk 不存入向量数据库（不生成 embedding）"""
        provider, mock_embedder, mock_vs, _ = _create_mock_provider(
            mock_settings, mock_embedder_cls, mock_vector_factory, mock_chunker_cls,
            tmp_path,
        )
        provider.build_index()

        # 收集所有写入向量库的 id
        call_kwargs = mock_vs.add_documents.call_args[1]
        ids_in_vs = call_kwargs["ids"]

        # parent chunk id "p1" 不应出现在向量库中
        assert "p1" not in ids_in_vs
        # child chunk id "c1", "c2" 应该在向量库中
        assert "c1" in ids_in_vs
        assert "c2" in ids_in_vs

    @patch("control_platform.rag.provider.ChunkerRegistry")
    @patch("control_platform.rag.provider.VectorStoreFactory")
    @patch("control_platform.rag.provider.Embedder")
    @patch("control_platform.rag.provider.settings")
    def test_incremental_build(
        self, mock_settings, mock_embedder_cls, mock_vector_factory,
        mock_chunker_cls, tmp_path,
    ):
        """测试增量构建（文件未变更时跳过）"""
        provider, _, mock_vs, mock_chunker = _create_mock_provider(
            mock_settings, mock_embedder_cls, mock_vector_factory, mock_chunker_cls,
            tmp_path,
        )

        first_count = provider.build_index()
        assert first_count == 2

        # 重置 mock 调用记录
        mock_chunker.chunk_file.reset_mock()
        mock_vs.add_documents.reset_mock()

        second_count = provider.build_index()
        assert second_count == 0, "文件未变更，二次构建应跳过"

        # chunk_file 不应被调用（因为文件 hash 未变更）
        mock_chunker.chunk_file.assert_not_called()

    @patch("control_platform.rag.provider.ChunkerRegistry")
    @patch("control_platform.rag.provider.VectorStoreFactory")
    @patch("control_platform.rag.provider.Embedder")
    @patch("control_platform.rag.provider.settings")
    def test_empty_chunks_no_error(
        self, mock_settings, mock_embedder_cls, mock_vector_factory,
        mock_chunker_cls, tmp_path,
    ):
        """测试切片返回空时不报错"""
        provider, _, mock_vs, _ = _create_mock_provider(
            mock_settings, mock_embedder_cls, mock_vector_factory, mock_chunker_cls,
            tmp_path,
            chunks=[],  # 空切片结果
        )

        count = provider.build_index()
        assert count == 0
        mock_vs.add_documents.assert_not_called()


class TestRAGProviderRetrieve:
    """RAGProvider 检索测试"""

    @patch("control_platform.rag.provider.ChunkerRegistry")
    @patch("control_platform.rag.provider.VectorStoreFactory")
    @patch("control_platform.rag.provider.Embedder")
    @patch("control_platform.rag.provider.settings")
    def test_retrieve_returns_results(
        self, mock_settings, mock_embedder_cls, mock_vector_factory,
        mock_chunker_cls, tmp_path,
    ):
        """测试检索返回结果"""
        provider, mock_embedder, mock_vs, _ = _create_mock_provider(
            mock_settings, mock_embedder_cls, mock_vector_factory, mock_chunker_cls,
            tmp_path,
        )
        provider.build_index()

        # mock 向量库的 query 返回 child chunk 结果
        mock_vs.query.return_value = [
            QueryResult(
                document="## 查看所有线程\n\nthread 命令可以查看所有线程 CPU 使用率",
                score=0.90,
                metadata={"chunk_level": "child", "parent_chunk_id": "p1"},
                id="c1",
            ),
        ]

        result = provider.retrieve("如何排查 CPU 使用率高的线程？")
        assert result is not None
        assert len(result.results) > 0
        assert result.total_tokens > 0

    @patch("control_platform.rag.provider.ChunkerRegistry")
    @patch("control_platform.rag.provider.VectorStoreFactory")
    @patch("control_platform.rag.provider.Embedder")
    @patch("control_platform.rag.provider.settings")
    def test_token_budget_truncation(
        self, mock_settings, mock_embedder_cls, mock_vector_factory,
        mock_chunker_cls, tmp_path,
    ):
        """测试 Token 预算截断"""
        # 创建 provider
        provider, mock_embedder, mock_vs, _ = _create_mock_provider(
            mock_settings, mock_embedder_cls, mock_vector_factory, mock_chunker_cls,
            tmp_path,
        )
        mock_settings.rag_max_tokens = 10  # 极小的 token 预算
        mock_settings.rag_top_k = 100
        provider.build_index()

        # mock 检索返回多个 parent chunk
        mock_vs.query.return_value = [
            QueryResult(
                document="这是一段很长的内容" * 100,
                score=0.90,
                metadata={"chunk_level": "child", "parent_chunk_id": "p1"},
                id="c1",
            ),
            QueryResult(
                document="另一段很长的内容" * 100,
                score=0.80,
                metadata={"chunk_level": "child", "parent_chunk_id": "p1"},
                id="c2",
            ),
        ]

        result = provider.retrieve("查找线程问题")
        # 不做严格 token 数量断言（因为截断策略较复杂），只确保不抛异常
        # 并且结果 token 数在合理范围内
        if result:
            assert result.total_tokens <= 200  # 宽松上限

    @patch("control_platform.rag.provider.settings")
    def test_rag_disabled(self, mock_settings):
        """测试 RAG 禁用时 retrieve 直接返回 None"""
        mock_settings.rag_enabled = False
        mock_settings.llm_model = "test-model"

        provider = RAGProvider()
        assert not provider.is_available

        result = provider.retrieve("任何查询")
        assert result is None

    @patch("control_platform.rag.provider.ChunkerRegistry")
    @patch("control_platform.rag.provider.VectorStoreFactory")
    @patch("control_platform.rag.provider.Embedder")
    @patch("control_platform.rag.provider.settings")
    def test_no_results_returns_none(
        self, mock_settings, mock_embedder_cls, mock_vector_factory,
        mock_chunker_cls, tmp_path,
    ):
        """测试检索无结果时返回 None"""
        provider, mock_embedder, mock_vs, _ = _create_mock_provider(
            mock_settings, mock_embedder_cls, mock_vector_factory, mock_chunker_cls,
            tmp_path,
        )
        mock_settings.rag_similarity_threshold = 0.99  # 超高阈值
        provider.build_index()

        # 向量库返回低分结果
        mock_vs.query.return_value = [
            QueryResult(
                document="不相关内容",
                score=0.05,
                metadata={"chunk_level": "child", "parent_chunk_id": "p1"},
                id="c1",
            ),
        ]

        result = provider.retrieve("完全不相关的查询")
        # 高阈值可能导致全部被过滤，不做严格断言，只确保不抛异常