"""
BM25Index 单元测试

测试覆盖：
- 索引构建与检索
- 中文分词检索
- 异常降级（依赖未安装）
- reset 方法
- 边界情况（空文档、空查询等）
"""

import pytest
from unittest.mock import patch, MagicMock

from control_platform.rag.bm25_index import BM25Index, _tokenize


class TestTokenize:
    """分词函数测试"""

    def test_chinese_tokenize(self):
        """测试中文文本分词"""
        tokens = _tokenize("使用 thread 命令排查 CPU 高")
        assert len(tokens) > 0
        # 应该包含关键词
        assert any("thread" in t for t in tokens)

    def test_english_tokenize(self):
        """测试英文文本分词"""
        tokens = _tokenize("use thread command to check CPU")
        assert len(tokens) > 0

    def test_empty_text(self):
        """测试空文本分词"""
        tokens = _tokenize("")
        assert tokens == [] or all(t == "" for t in tokens)

    def test_mixed_text(self):
        """测试中英文混合文本分词"""
        tokens = _tokenize("Arthas 的 watch 命令用于观察方法调用")
        assert len(tokens) > 0


class TestBM25IndexBuild:
    """BM25 索引构建测试"""

    def setup_method(self):
        self.index = BM25Index()

    def test_build_index(self):
        """测试正常构建索引"""
        chunk_ids = ["c1", "c2", "c3"]
        documents = [
            "thread 命令查看线程信息",
            "watch 命令观察方法调用",
            "trace 命令跟踪方法调用链路",
        ]
        self.index.build(chunk_ids, documents)

        assert self.index.is_available
        assert len(self.index) == 3

    def test_build_empty_index(self):
        """测试空文档列表构建索引"""
        self.index.build([], [])
        assert not self.index.is_available
        assert len(self.index) == 0

    def test_build_mismatched_lengths(self):
        """测试 chunk_ids 和 documents 数量不一致时抛异常"""
        with pytest.raises(ValueError, match="不一致"):
            self.index.build(["c1", "c2"], ["doc1"])

    def test_is_available_before_build(self):
        """测试未构建索引时 is_available 为 False"""
        assert not self.index.is_available


class TestBM25IndexSearch:
    """BM25 检索测试"""

    def setup_method(self):
        self.index = BM25Index()
        self.chunk_ids = ["c1", "c2", "c3", "c4"]
        self.documents = [
            "thread 命令用于查看当前线程信息和 CPU 使用率",
            "watch 命令用于观察方法的入参和出参",
            "trace 命令用于跟踪方法调用链路和耗时",
            "jvm 命令用于查看 JVM 基本信息和内存使用情况",
        ]
        self.index.build(self.chunk_ids, self.documents)

    def test_search_returns_results(self):
        """测试检索返回结果"""
        results = self.index.search("thread 线程 CPU", top_k=3)
        assert len(results) > 0
        # 返回格式为 (chunk_id, score) 元组
        for chunk_id, score in results:
            assert isinstance(chunk_id, str)
            assert isinstance(score, float)
            assert score > 0

    def test_search_relevance(self):
        """测试检索相关性：thread 查询应该优先返回 thread 文档"""
        results = self.index.search("thread 线程", top_k=4)
        if results:
            top_id = results[0][0]
            assert top_id == "c1", f"thread 查询应优先返回 c1，实际返回 {top_id}"

    def test_search_top_k_limit(self):
        """测试 top_k 限制"""
        results = self.index.search("命令", top_k=2)
        assert len(results) <= 2

    def test_search_before_build(self):
        """测试未构建索引时检索返回空列表"""
        empty_index = BM25Index()
        results = empty_index.search("test query")
        assert results == []

    def test_search_empty_query(self):
        """测试空查询返回空列表"""
        results = self.index.search("")
        assert results == []

    def test_search_no_match(self):
        """测试完全不匹配的查询"""
        results = self.index.search("zzzzxyzxyz completely unrelated")
        # BM25 可能返回空列表或低分结果
        # 不做严格断言，只确保不抛异常

    def test_search_keyword_exact_match(self):
        """测试关键词精确匹配（Arthas 命令名）"""
        results = self.index.search("watch", top_k=4)
        if results:
            # watch 应该在结果中
            result_ids = [r[0] for r in results]
            assert "c2" in result_ids, "watch 查询应命中 c2"

    def test_search_descending_score(self):
        """测试结果按 score 降序排列"""
        results = self.index.search("命令 方法", top_k=4)
        if len(results) >= 2:
            for i in range(len(results) - 1):
                assert results[i][1] >= results[i + 1][1], "结果应按 score 降序"


class TestBM25IndexReset:
    """BM25 索引重置测试"""

    def test_reset_clears_index(self):
        """测试 reset 清空索引"""
        index = BM25Index()
        index.build(["c1"], ["文档内容"])
        assert index.is_available

        index.reset()
        assert not index.is_available
        assert len(index) == 0
        assert index.search("文档") == []

    def test_rebuild_after_reset(self):
        """测试 reset 后可以重新构建索引"""
        index = BM25Index()
        index.build(["c1"], ["旧文档"])
        index.reset()

        index.build(["c2"], ["新文档"])
        assert index.is_available
        assert len(index) == 1
        results = index.search("新文档")
        if results:
            assert results[0][0] == "c2"


class TestBM25IndexDegradation:
    """BM25 异常降级测试"""

    def test_search_exception_returns_empty(self):
        """测试检索过程中异常返回空列表"""
        index = BM25Index()
        index.build(["c1"], ["文档内容"])

        # mock BM25 内部的 get_scores 抛异常
        with patch.object(index._bm25, "get_scores", side_effect=Exception("模拟异常")):
            results = index.search("文档")
            assert results == []
