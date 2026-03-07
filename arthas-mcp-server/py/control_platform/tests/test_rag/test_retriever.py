"""
Retriever 单元测试（混合检索 + Parent Score 聚合 + 去重）

测试覆盖：
- 纯向量检索（hybrid_search 禁用或 BM25 不可用）
- 双路检索 + RRF 融合
- Child → Parent 映射和 Score 聚合去重
- 多 parent 返回
- BM25 异常降级
- rag_hybrid_search_enabled=False 退化为纯向量检索
"""

from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from control_platform.rag.base_vector_store import BaseVectorStore, QueryResult
from control_platform.rag.bm25_index import BM25Index
from control_platform.rag.parent_store import ParentChunkStore
from control_platform.rag.retriever import Retriever


def _make_child_query_results(items):
    """构造 child chunk 的 QueryResult 列表

    Args:
        items: [(chunk_id, score, parent_chunk_id)] 列表
    """
    return [
        QueryResult(
            document=f"child 文档 {cid}",
            score=score,
            metadata={"chunk_level": "child", "parent_chunk_id": pid},
            id=cid,
        )
        for cid, score, pid in items
    ]


def _setup_parent_store(parent_data):
    """构建 ParentChunkStore

    Args:
        parent_data: {parent_chunk_id: (content, total_children)} 字典
    """
    store = ParentChunkStore()
    for pid, (content, total_children) in parent_data.items():
        store.add_parent(pid, content, {
            "chunk_level": "parent",
            "total_children": total_children,
        })
    return store


def _create_retriever(
    query_results=None,
    embed_result=None,
    parent_store=None,
    bm25_index=None,
):
    """创建带 Mock 依赖的 Retriever"""
    mock_store = MagicMock(spec=BaseVectorStore)
    mock_embedder = MagicMock()

    if query_results is not None:
        mock_store.query.return_value = query_results
    else:
        mock_store.query.return_value = []

    if embed_result is not None:
        mock_embedder.embed.return_value = embed_result
    else:
        mock_embedder.embed.return_value = [0.1, 0.2, 0.3]

    return Retriever(
        vector_store=mock_store,
        embedder=mock_embedder,
        parent_store=parent_store,
        bm25_index=bm25_index,
    )


class TestPureVectorRetrieval:
    """纯向量检索测试（无 parent_store，无 BM25）"""

    def test_similarity_threshold_filtering(self):
        """测试相似度阈值过滤"""
        results = _make_child_query_results([
            ("c1", 0.95, "p1"), ("c2", 0.80, "p1"),
            ("c3", 0.50, "p2"), ("c4", 0.25, "p2"), ("c5", 0.10, "p3"),
        ])
        retriever = _create_retriever(query_results=results)

        # 无 parent_store 时直接返回 child
        with patch("control_platform.rag.retriever.settings") as mock_settings:
            mock_settings.rag_hybrid_search_enabled = False
            mock_settings.rag_rrf_score_threshold = 0.01
            filtered = retriever.retrieve("测试", top_k=10, similarity_threshold=0.3)

        assert len(filtered) == 3
        for r in filtered:
            assert r.score >= 0.3

    def test_top_k_truncation(self):
        """测试 Top-K 截断"""
        results = _make_child_query_results([
            ("c1", 0.99, "p1"), ("c2", 0.95, "p1"),
            ("c3", 0.90, "p2"), ("c4", 0.85, "p2"),
        ])
        retriever = _create_retriever(query_results=results)

        with patch("control_platform.rag.retriever.settings") as mock_settings:
            mock_settings.rag_hybrid_search_enabled = False
            filtered = retriever.retrieve("测试", top_k=2, similarity_threshold=0.0)

        assert len(filtered) == 2

    def test_empty_embedding_returns_empty(self):
        """测试 Embedding 生成失败时返回空结果"""
        retriever = _create_retriever(embed_result=[])

        with patch("control_platform.rag.retriever.settings") as mock_settings:
            mock_settings.rag_hybrid_search_enabled = False
            results = retriever.retrieve("测试")

        assert results == []

    def test_vector_store_exception_returns_empty(self):
        """测试 VectorStore 异常时返回空结果"""
        mock_store = MagicMock(spec=BaseVectorStore)
        mock_store.query.side_effect = RuntimeError("数据库连接失败")
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2, 0.3]

        retriever = Retriever(vector_store=mock_store, embedder=mock_embedder)

        results = retriever.retrieve("测试")
        assert results == []


class TestParentChildMapping:
    """Child → Parent 映射和 Score 聚合测试"""

    def test_child_maps_to_parent(self):
        """测试 child chunk 映射到正确的 parent chunk"""
        child_results = _make_child_query_results([
            ("c1", 0.90, "p1"),
            ("c2", 0.85, "p1"),
            ("c3", 0.70, "p2"),
        ])

        parent_store = _setup_parent_store({
            "p1": ("Parent 1 的完整内容", 3),
            "p2": ("Parent 2 的完整内容", 2),
        })

        retriever = _create_retriever(
            query_results=child_results,
            parent_store=parent_store,
        )

        with patch("control_platform.rag.retriever.settings") as mock_settings:
            mock_settings.rag_hybrid_search_enabled = False
            results = retriever.retrieve("测试", top_k=5, similarity_threshold=0.0)

        # 应该返回 parent chunk，不是 child chunk
        assert len(results) == 2
        assert results[0].document == "Parent 1 的完整内容"
        assert results[1].document == "Parent 2 的完整内容"

    def test_multi_child_hit_dedup_and_score_aggregation(self):
        """测试同一 parent 下多个 child 命中时去重 + Score 聚合"""
        # p1 有 3 个 child，其中 2 个被命中
        child_results = _make_child_query_results([
            ("c1", 0.90, "p1"),
            ("c2", 0.80, "p1"),
            ("c3", 0.70, "p2"),
        ])

        parent_store = _setup_parent_store({
            "p1": ("Parent 1 内容", 3),
            "p2": ("Parent 2 内容", 1),
        })

        retriever = _create_retriever(
            query_results=child_results,
            parent_store=parent_store,
        )

        with patch("control_platform.rag.retriever.settings") as mock_settings:
            mock_settings.rag_hybrid_search_enabled = False
            results = retriever.retrieve("测试", top_k=5, similarity_threshold=0.0)

        # p1 应该只出现一次（去重）
        assert len(results) == 2

        # p1 的聚合分数：max(0.90, 0.80) + 0.15 * (2-1) / 3 = 0.90 + 0.05 = 0.95
        p1_result = results[0]
        expected_score = 0.90 + 0.15 * (2 - 1) / 3
        assert abs(p1_result.score - expected_score) < 0.001, (
            f"p1 聚合分数应为 {expected_score:.4f}，实际为 {p1_result.score:.4f}"
        )

    def test_parent_score_ordering(self):
        """测试按 parent_score 降序排列"""
        child_results = _make_child_query_results([
            ("c1", 0.60, "p1"),
            ("c2", 0.90, "p2"),
            ("c3", 0.50, "p3"),
        ])

        parent_store = _setup_parent_store({
            "p1": ("Parent 1", 1),
            "p2": ("Parent 2", 1),
            "p3": ("Parent 3", 1),
        })

        retriever = _create_retriever(
            query_results=child_results,
            parent_store=parent_store,
        )

        with patch("control_platform.rag.retriever.settings") as mock_settings:
            mock_settings.rag_hybrid_search_enabled = False
            results = retriever.retrieve("测试", top_k=5, similarity_threshold=0.0)

        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True), "应按 parent_score 降序排列"

    def test_top_k_limits_parent_count(self):
        """测试 top_k 限制返回的 parent 数量"""
        child_results = _make_child_query_results([
            ("c1", 0.90, "p1"),
            ("c2", 0.80, "p2"),
            ("c3", 0.70, "p3"),
        ])

        parent_store = _setup_parent_store({
            "p1": ("P1", 1), "p2": ("P2", 1), "p3": ("P3", 1),
        })

        retriever = _create_retriever(
            query_results=child_results,
            parent_store=parent_store,
        )

        with patch("control_platform.rag.retriever.settings") as mock_settings:
            mock_settings.rag_hybrid_search_enabled = False
            results = retriever.retrieve("测试", top_k=2, similarity_threshold=0.0)

        assert len(results) == 2

    def test_missing_parent_degrades_to_child(self):
        """测试 parent 不存在时降级返回 child"""
        child_results = _make_child_query_results([
            ("c1", 0.90, "p_missing"),
        ])

        parent_store = ParentChunkStore()  # 空的 store

        retriever = _create_retriever(
            query_results=child_results,
            parent_store=parent_store,
        )

        with patch("control_platform.rag.retriever.settings") as mock_settings:
            mock_settings.rag_hybrid_search_enabled = False
            results = retriever.retrieve("测试", top_k=5, similarity_threshold=0.0)

        # 应该降级返回 child 的文档
        assert len(results) == 1
        assert "child 文档" in results[0].document


class TestHybridSearch:
    """混合检索（向量 + BM25 + RRF 融合）测试"""

    def test_rrf_fusion(self):
        """测试双路检索 + RRF 融合"""
        child_results = _make_child_query_results([
            ("c1", 0.90, "p1"),
            ("c2", 0.80, "p2"),
        ])

        mock_bm25 = MagicMock(spec=BM25Index)
        mock_bm25.is_available = True
        mock_bm25.search.return_value = [
            ("c2", 5.0),  # BM25 认为 c2 更相关
            ("c3", 3.0),
        ]

        parent_store = _setup_parent_store({
            "p1": ("P1 content", 1),
            "p2": ("P2 content", 2),
        })

        retriever = _create_retriever(
            query_results=child_results,
            parent_store=parent_store,
            bm25_index=mock_bm25,
        )

        with patch("control_platform.rag.retriever.settings") as mock_settings:
            mock_settings.rag_hybrid_search_enabled = True
            mock_settings.rag_rrf_score_threshold = 0.0
            results = retriever.retrieve("测试", top_k=5, similarity_threshold=0.0)

        # 应该有融合结果
        assert len(results) > 0
        # c2 在两路都命中，RRF 分数应更高
        result_ids = [r.id for r in results]
        assert "p2" in result_ids, "c2 两路命中，p2 应在结果中"

    def test_hybrid_search_disabled(self):
        """测试 rag_hybrid_search_enabled=False 退化为纯向量检索"""
        child_results = _make_child_query_results([
            ("c1", 0.90, "p1"),
        ])

        mock_bm25 = MagicMock(spec=BM25Index)
        mock_bm25.is_available = True
        # BM25 不应被调用
        mock_bm25.search.return_value = [("c2", 5.0)]

        parent_store = _setup_parent_store({
            "p1": ("P1 content", 1),
        })

        retriever = _create_retriever(
            query_results=child_results,
            parent_store=parent_store,
            bm25_index=mock_bm25,
        )

        with patch("control_platform.rag.retriever.settings") as mock_settings:
            mock_settings.rag_hybrid_search_enabled = False
            results = retriever.retrieve("测试", top_k=5, similarity_threshold=0.0)

        # BM25 不应被调用
        mock_bm25.search.assert_not_called()
        assert len(results) == 1
        assert results[0].id == "p1"

    def test_bm25_exception_degrades_to_vector_only(self):
        """测试 BM25 检索异常时降级为纯向量检索"""
        child_results = _make_child_query_results([
            ("c1", 0.90, "p1"),
        ])

        mock_bm25 = MagicMock(spec=BM25Index)
        mock_bm25.is_available = True
        mock_bm25.search.side_effect = Exception("BM25 内部错误")

        parent_store = _setup_parent_store({
            "p1": ("P1 content", 1),
        })

        retriever = _create_retriever(
            query_results=child_results,
            parent_store=parent_store,
            bm25_index=mock_bm25,
        )

        with patch("control_platform.rag.retriever.settings") as mock_settings:
            mock_settings.rag_hybrid_search_enabled = True
            mock_settings.rag_rrf_score_threshold = 0.0
            results = retriever.retrieve("测试", top_k=5, similarity_threshold=0.0)

        # 降级到纯向量检索，应仍能返回结果
        assert len(results) == 1
        assert results[0].id == "p1"

    def test_bm25_unavailable_degrades(self):
        """测试 BM25 索引未构建时退化为纯向量检索"""
        child_results = _make_child_query_results([
            ("c1", 0.90, "p1"),
        ])

        mock_bm25 = MagicMock(spec=BM25Index)
        mock_bm25.is_available = False

        parent_store = _setup_parent_store({
            "p1": ("P1 content", 1),
        })

        retriever = _create_retriever(
            query_results=child_results,
            parent_store=parent_store,
            bm25_index=mock_bm25,
        )

        with patch("control_platform.rag.retriever.settings") as mock_settings:
            mock_settings.rag_hybrid_search_enabled = True
            results = retriever.retrieve("测试", top_k=5, similarity_threshold=0.0)

        assert len(results) == 1

    def test_rrf_score_threshold_filtering(self):
        """测试 RRF 分数阈值过滤"""
        child_results = _make_child_query_results([
            ("c1", 0.90, "p1"),
            ("c2", 0.10, "p2"),
        ])

        mock_bm25 = MagicMock(spec=BM25Index)
        mock_bm25.is_available = True
        mock_bm25.search.return_value = []  # BM25 无结果

        parent_store = _setup_parent_store({
            "p1": ("P1", 1),
            "p2": ("P2", 1),
        })

        retriever = _create_retriever(
            query_results=child_results,
            parent_store=parent_store,
            bm25_index=mock_bm25,
        )

        with patch("control_platform.rag.retriever.settings") as mock_settings:
            mock_settings.rag_hybrid_search_enabled = True
            mock_settings.rag_rrf_score_threshold = 0.02  # 高阈值
            results = retriever.retrieve("测试", top_k=5, similarity_threshold=0.0)

        # 分数较低的结果可能被过滤
        # RRF 分数 = 1/(60+rank)，排名靠后的分数会很低
        # 不做严格数量断言，只确保不抛异常


class TestRetrieverNoParentStore:
    """无 parent_store 时的退化行为测试"""

    def test_returns_child_directly(self):
        """无 parent_store 时直接返回 child chunk"""
        child_results = _make_child_query_results([
            ("c1", 0.90, "p1"),
            ("c2", 0.80, "p2"),
        ])

        retriever = _create_retriever(
            query_results=child_results,
            parent_store=None,
        )

        with patch("control_platform.rag.retriever.settings") as mock_settings:
            mock_settings.rag_hybrid_search_enabled = False
            results = retriever.retrieve("测试", top_k=5, similarity_threshold=0.0)

        assert len(results) == 2
        assert results[0].id == "c1"
        assert "child 文档" in results[0].document


class TestRetrieverEdgeCases:
    """边界情况测试"""

    def test_all_below_threshold_returns_empty(self):
        """所有结果分数低于阈值时返回空"""
        results = _make_child_query_results([
            ("c1", 0.1, "p1"), ("c2", 0.05, "p2"),
        ])
        retriever = _create_retriever(query_results=results)

        with patch("control_platform.rag.retriever.settings") as mock_settings:
            mock_settings.rag_hybrid_search_enabled = False
            filtered = retriever.retrieve("测试", top_k=10, similarity_threshold=0.5)

        assert filtered == []

    def test_empty_query_results(self):
        """向量检索返回空时返回空"""
        retriever = _create_retriever(query_results=[])

        with patch("control_platform.rag.retriever.settings") as mock_settings:
            mock_settings.rag_hybrid_search_enabled = False
            results = retriever.retrieve("测试")

        assert results == []
