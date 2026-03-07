"""
VectorStoreFactory 单元测试

测试工厂创建 ChromaVectorStore、无效类型抛出 ValueError。
"""

import pytest

from control_platform.config import Settings
from control_platform.rag.vector_store_factory import VectorStoreFactory
from control_platform.rag.chroma_vector_store import ChromaVectorStore


class TestVectorStoreFactory:
    """VectorStoreFactory 单元测试"""

    def test_create_chroma(self, tmp_path):
        """测试工厂创建 ChromaVectorStore 实例"""
        config = Settings(
            rag_store_path=str(tmp_path / "test_vector_db"),
        )
        store = VectorStoreFactory.create("chroma", config)

        assert isinstance(store, ChromaVectorStore)
        print(f"✅ 创建 ChromaVectorStore 成功, 类型: {type(store).__name__}")

    def test_create_chroma_case_insensitive(self, tmp_path):
        """测试类型名大小写不敏感"""
        config = Settings(
            rag_store_path=str(tmp_path / "test_vector_db"),
        )
        store = VectorStoreFactory.create("Chroma", config)
        assert isinstance(store, ChromaVectorStore)
        print("✅ 大小写不敏感: 'Chroma' -> ChromaVectorStore")

    def test_create_invalid_type(self):
        """测试无效类型抛出 ValueError"""
        config = Settings()
        with pytest.raises(ValueError) as exc_info:
            VectorStoreFactory.create("invalid_db", config)

        error_msg = str(exc_info.value)
        assert "不支持" in error_msg or "invalid_db" in error_msg
        assert "chroma" in error_msg  # 错误信息应列出支持的类型
        print(f"✅ 无效类型 ValueError: {error_msg}")

    def test_supported_types(self):
        """测试获取所有支持的类型"""
        types = VectorStoreFactory.supported_types()
        assert "chroma" in types
        print(f"✅ 支持的类型: {types}")
