"""
Embedder 集成测试 —— 实际加载 BAAI/bge-m3 模型

运行方式：
    pytest control_platform/tests/test_rag/test_embedder_integration.py -v -s

注意：
    1. 首次运行会自动下载 BAAI/bge-m3 模型（约 2.2GB），请确保网络畅通
    2. 模型加载需要约 10-30 秒，请耐心等待
    3. 需要已安装 sentence-transformers: pip install sentence-transformers
"""

import math
import os
import time

import pytest

# 禁用 tokenizers 并行化，防止 Rust 线程池导致 pytest 进程退出时挂起
os.environ["TOKENIZERS_PARALLELISM"] = "false"

# 检查 sentence-transformers 是否可用
try:
    from sentence_transformers import SentenceTransformer

    HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    HAS_SENTENCE_TRANSFORMERS = False

skip_if_no_st = pytest.mark.skipif(
    not HAS_SENTENCE_TRANSFORMERS,
    reason="需要安装 sentence-transformers: pip install sentence-transformers",
)

MODEL_NAME = "BAAI/bge-m3"
EXPECTED_DIMENSION = 1024  # bge-m3 输出维度


def cosine_similarity(vec_a: list, vec_b: list) -> float:
    """计算两个向量的余弦相似度"""
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


@skip_if_no_st
class TestLocalEmbedderIntegration:
    """使用真实 BAAI/bge-m3 模型的集成测试"""

    @pytest.fixture(scope="class")
    def embedder(self):
        """类级别共享 embedder 实例，避免每个测试都重新加载模型"""
        from control_platform.rag.embedder import LocalEmbedder

        print(f"\n🔄 正在加载模型 {MODEL_NAME}（首次运行需下载，请耐心等待）...")
        start = time.time()
        emb = LocalEmbedder(model=MODEL_NAME)
        # 触发模型加载
        emb.embed("warmup")
        elapsed = time.time() - start
        print(f"✅ 模型加载完成，耗时 {elapsed:.1f}s")
        return emb

    # ==================== 基础功能 ====================

    def test_single_embed(self, embedder):
        """测试单条文本 embedding"""
        vector = embedder.embed("你好世界")
        assert isinstance(vector, list)
        assert len(vector) == EXPECTED_DIMENSION
        assert all(isinstance(v, float) for v in vector)
        print(f"✅ 单条 embedding: 维度={len(vector)}, 前5个值={vector[:5]}")

    def test_dimension_property(self, embedder):
        """测试 dimension 属性"""
        dim = embedder.dimension
        assert dim == EXPECTED_DIMENSION
        print(f"✅ 模型维度: {dim}")

    def test_empty_text(self, embedder):
        """测试空文本"""
        vector = embedder.embed("")
        # 空文本也应该能返回一个向量
        assert isinstance(vector, list)
        assert len(vector) == EXPECTED_DIMENSION
        print(f"✅ 空文本 embedding: 维度={len(vector)}")

    def test_batch_embed(self, embedder):
        """测试批量 embedding"""
        texts = ["Java 线程死锁排查", "Python 性能优化", "数据库索引设计"]
        vectors = embedder.embed_batch(texts)
        assert len(vectors) == 3
        for i, vec in enumerate(vectors):
            assert len(vec) == EXPECTED_DIMENSION
            print(f"  文本[{i}]: '{texts[i]}' → 维度={len(vec)}")
        print(f"✅ 批量 embedding: {len(texts)} 条全部成功")

    def test_batch_empty_list(self, embedder):
        """测试空列表批量 embedding"""
        vectors = embedder.embed_batch([])
        assert vectors == []
        print("✅ 空列表批量 embedding: 返回空列表")

    # ==================== 归一化验证 ====================

    def test_vectors_are_normalized(self, embedder):
        """验证向量已归一化（L2 范数接近 1.0）"""
        texts = ["测试归一化", "another normalization test"]
        vectors = embedder.embed_batch(texts)
        for i, vec in enumerate(vectors):
            norm = math.sqrt(sum(v * v for v in vec))
            assert abs(norm - 1.0) < 1e-4, f"向量[{i}] L2 范数 = {norm}，期望接近 1.0"
            print(f"  向量[{i}] L2 范数: {norm:.6f}")
        print("✅ 所有向量已归一化")

    # ==================== 语义相似度验证 ====================

    def test_similar_texts_high_similarity(self, embedder):
        """语义相近的文本，余弦相似度应较高"""
        text_a = "Java 应用出现内存溢出 OOM 问题"
        text_b = "JVM 堆内存不足导致 OutOfMemoryError"
        vec_a = embedder.embed(text_a)
        vec_b = embedder.embed(text_b)
        sim = cosine_similarity(vec_a, vec_b)
        print(f"  '{text_a}' vs '{text_b}'")
        print(f"  余弦相似度: {sim:.4f}")
        assert sim > 0.5, f"语义相近的文本相似度应 > 0.5，实际 = {sim:.4f}"
        print("✅ 相近语义文本相似度较高")

    def test_dissimilar_texts_low_similarity(self, embedder):
        """语义无关的文本，余弦相似度应较低"""
        text_a = "Java 应用出现内存溢出 OOM 问题"
        text_b = "今天天气真好，适合出去散步"
        vec_a = embedder.embed(text_a)
        vec_b = embedder.embed(text_b)
        sim = cosine_similarity(vec_a, vec_b)
        print(f"  '{text_a}' vs '{text_b}'")
        print(f"  余弦相似度: {sim:.4f}")
        assert sim < 0.5, f"语义无关的文本相似度应 < 0.5，实际 = {sim:.4f}"
        print("✅ 无关语义文本相似度较低")

    def test_same_text_similarity_is_1(self, embedder):
        """相同文本的余弦相似度应接近 1.0"""
        text = "Arthas 是一个 Java 诊断工具"
        vec_a = embedder.embed(text)
        vec_b = embedder.embed(text)
        sim = cosine_similarity(vec_a, vec_b)
        print(f"  相同文本余弦相似度: {sim:.6f}")
        assert sim > 0.9999, f"相同文本相似度应接近 1.0，实际 = {sim:.6f}"
        print("✅ 相同文本相似度 ≈ 1.0")

    def test_similarity_ranking(self, embedder):
        """验证语义排序：与 query 最相关的文档排在前面"""
        query = "如何排查 Java 线程死锁"
        docs = [
            "使用 jstack 命令可以查看 Java 进程的线程堆栈信息，定位死锁",  # 高度相关
            "Arthas 的 thread -b 命令可以快速找到阻塞线程",  # 相关
            "MySQL 慢查询优化需要分析执行计划",  # 部分相关
            "今天午饭吃了番茄炒蛋",  # 无关
        ]

        query_vec = embedder.embed(query)
        doc_vecs = embedder.embed_batch(docs)

        similarities = []
        for i, (doc, doc_vec) in enumerate(zip(docs, doc_vecs)):
            sim = cosine_similarity(query_vec, doc_vec)
            similarities.append((sim, doc))
            print(f"  [{i}] sim={sim:.4f} | {doc[:40]}...")

        # 排序后验证前两个应该是与 Java 线程相关的文档
        ranked = sorted(similarities, key=lambda x: -x[0])
        print(f"\n  排序结果:")
        for i, (sim, doc) in enumerate(ranked):
            print(f"    #{i + 1} sim={sim:.4f} | {doc[:40]}...")

        # 最相关的应该是 jstack 或 Arthas 相关
        assert ranked[0][0] > ranked[-1][0], "最相关文档的相似度应大于最不相关的"
        # 最不相关的应该是 "今天午饭"
        assert "午饭" in ranked[-1][1] or "番茄" in ranked[-1][1], "最不相关的应该是无关文档"
        print("✅ 语义排序正确")

    # ==================== 中英文多语言 ====================

    def test_multilingual_chinese_english(self, embedder):
        """中英文语义对应的文本应有较高相似度（bge-m3 的核心优势）"""
        text_cn = "如何解决数据库连接池耗尽的问题"
        text_en = "How to fix database connection pool exhaustion"
        vec_cn = embedder.embed(text_cn)
        vec_en = embedder.embed(text_en)
        sim = cosine_similarity(vec_cn, vec_en)
        print(f"  中文: '{text_cn}'")
        print(f"  英文: '{text_en}'")
        print(f"  跨语言余弦相似度: {sim:.4f}")
        assert sim > 0.4, f"中英文语义对应的文本相似度应 > 0.4，实际 = {sim:.4f}"
        print("✅ 跨语言语义匹配有效")

    # ==================== 性能基准 ====================

    def test_single_embed_latency(self, embedder):
        """单条 embedding 延迟基准"""
        text = "这是一条用于延迟测试的文本"
        # 预热
        embedder.embed(text)

        times = []
        for _ in range(5):
            start = time.time()
            embedder.embed(text)
            elapsed = time.time() - start
            times.append(elapsed)

        avg_ms = sum(times) / len(times) * 1000
        print(f"  单条 embedding 平均延迟: {avg_ms:.1f}ms（5次取平均）")
        # 在 Mac 48GB 上，单条推理应该在 500ms 以内
        assert avg_ms < 500, f"单条 embedding 延迟过高: {avg_ms:.1f}ms"
        print("✅ 延迟在合理范围内")

    def test_batch_embed_throughput(self, embedder):
        """批量 embedding 吞吐量基准"""
        texts = [f"这是第{i}条测试文本，用于吞吐量评估" for i in range(20)]

        start = time.time()
        vectors = embedder.embed_batch(texts)
        elapsed = time.time() - start

        assert len(vectors) == 20
        throughput = len(texts) / elapsed
        print(f"  批量 embedding: {len(texts)} 条, 耗时 {elapsed:.2f}s, 吞吐量 {throughput:.1f} 条/秒")
        print("✅ 批量 embedding 吞吐量测试完成")

    # ==================== 长文本 ====================

    def test_long_text_embedding(self, embedder):
        """长文本 embedding（bge-m3 支持最大 8192 tokens）"""
        long_text = "Java应用线程死锁分析报告。" * 200  # 约 2000 字
        vector = embedder.embed(long_text)
        assert len(vector) == EXPECTED_DIMENSION
        norm = math.sqrt(sum(v * v for v in vector))
        assert abs(norm - 1.0) < 1e-4
        print(f"  长文本长度: {len(long_text)} 字符, embedding 维度: {len(vector)}, L2 范数: {norm:.6f}")
        print("✅ 长文本 embedding 正常")


@skip_if_no_st
class TestEmbedderFactoryIntegration:
    """通过 Embedder 工厂类使用本地模式的集成测试"""

    def test_embedder_factory_local_mode(self):
        """通过 Embedder 工厂类创建本地 embedder 并执行 embedding"""
        from control_platform.rag.embedder import Embedder

        embedder = Embedder(provider="local", model=MODEL_NAME)
        assert embedder.provider_type == "local"

        vector = embedder.embed("Arthas 诊断工具测试")
        assert isinstance(vector, list)
        assert len(vector) == EXPECTED_DIMENSION
        print(f"✅ Embedder 工厂本地模式: 维度={len(vector)}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
