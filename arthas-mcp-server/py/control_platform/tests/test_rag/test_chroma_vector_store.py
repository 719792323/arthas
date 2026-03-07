"""
ChromaVectorStore 单元测试

使用 ChromaDB 内存模式，测试写入、检索精度（打印相似度分数）、metadata 过滤。
"""

import pytest

from control_platform.rag.chroma_vector_store import ChromaVectorStore


class TestChromaVectorStore:
    """ChromaVectorStore 单元测试"""

    def setup_method(self):
        """每个测试方法使用全新的内存模式实例（唯一集合名避免数据残留）"""
        import uuid
        self.store = ChromaVectorStore(
            persist_directory="",  # 内存模式
            collection_name=f"test_{uuid.uuid4().hex[:8]}",
        )

    def test_add_and_count(self):
        """测试文档写入和计数"""
        self.store.add_documents(
            documents=["文档1", "文档2", "文档3"],
            embeddings=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            metadatas=[{"type": "a"}, {"type": "b"}, {"type": "c"}],
            ids=["id_1", "id_2", "id_3"],
        )
        assert self.store.count() == 3
        print(f"✅ 文档写入: 3 条, count={self.store.count()}")

    def test_query_top_k(self):
        """测试 Top-K 检索并打印相似度分数"""
        # 写入 5 个文档
        docs = [f"文档{i}" for i in range(5)]
        embeddings = [
            [1.0, 0.0, 0.0],
            [0.9, 0.1, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
            [0.5, 0.5, 0.0],
        ]
        metadatas = [{"idx": str(i)} for i in range(5)]
        ids = [f"doc_{i}" for i in range(5)]

        self.store.add_documents(docs, embeddings, metadatas, ids)

        # 查询与 [1.0, 0.0, 0.0] 最相似的 3 个
        results = self.store.query(
            query_embedding=[1.0, 0.0, 0.0],
            top_k=3,
        )

        assert len(results) <= 3
        print(f"\n🔍 检索结果 (Top-3, query=[1.0, 0.0, 0.0]):")
        for i, r in enumerate(results):
            print(f"  [{i}] ID={r.id}, score={r.score:.4f}, doc={r.document}")

        # 验证结果按分数降序
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_query_with_metadata_filter(self):
        """测试按 metadata 过滤检索"""
        self.store.add_documents(
            documents=["工具文档", "排查手册", "历史案例"],
            embeddings=[[1.0, 0.0, 0.0], [0.9, 0.1, 0.0], [0.8, 0.2, 0.0]],
            metadatas=[
                {"source_type": "tool_doc"},
                {"source_type": "troubleshooting"},
                {"source_type": "historical_case"},
            ],
            ids=["t1", "t2", "t3"],
        )

        results = self.store.query(
            query_embedding=[1.0, 0.0, 0.0],
            top_k=10,
            filter={"source_type": "tool_doc"},
        )

        assert len(results) == 1
        assert results[0].metadata["source_type"] == "tool_doc"
        print(f"✅ metadata 过滤: 只返回 tool_doc, 得到 {len(results)} 条")

    def test_delete_documents(self):
        """测试按 ID 删除文档"""
        self.store.add_documents(
            documents=["文档A", "文档B"],
            embeddings=[[1.0, 0.0], [0.0, 1.0]],
            metadatas=[{"tag": "a"}, {"tag": "b"}],
            ids=["a", "b"],
        )
        assert self.store.count() == 2

        self.store.delete(["a"])
        assert self.store.count() == 1
        print("✅ 删除后: count=1")

    def test_reset(self):
        """测试清空所有数据"""
        self.store.add_documents(
            documents=["文档1", "文档2"],
            embeddings=[[1.0, 0.0], [0.0, 1.0]],
            metadatas=[{"tag": "1"}, {"tag": "2"}],
            ids=["1", "2"],
        )
        assert self.store.count() == 2

        self.store.reset()
        assert self.store.count() == 0
        print("✅ reset 后: count=0")

    def test_query_empty_store(self):
        """测试空存储查询"""
        results = self.store.query(
            query_embedding=[1.0, 0.0, 0.0],
            top_k=3,
        )
        assert results == []
        print("✅ 空存储查询: 返回空列表")
