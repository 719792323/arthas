"""
Embedder 单元测试

测试策略模式下的两种 provider：
- ApiEmbedder: Mock OpenAI API，测试向量生成、批处理、异常处理
- LocalEmbedder: Mock sentence-transformers，测试本地模型加载和推理
- Embedder: 工厂类自动选择测试
"""

from unittest.mock import patch, MagicMock
import numpy as np
import pytest

from control_platform.rag.embedder import Embedder, LocalEmbedder, ApiEmbedder


# ==================== ApiEmbedder 测试 ====================


class TestApiEmbedder:
    """API 模式 Embedding 单元测试"""

    @patch("control_platform.rag.embedder.ApiEmbedder.__init__", return_value=None)
    def _create_mock_api_embedder(self, mock_init):
        """创建一个带 Mock client 的 ApiEmbedder"""
        embedder = ApiEmbedder.__new__(ApiEmbedder)
        embedder.model = "text-embedding-3-small"
        embedder.client = MagicMock()
        embedder._dimension = None
        return embedder

    def test_embed_single_text(self):
        """测试 API 模式单条文本 Embedding 生成"""
        embedder = self._create_mock_api_embedder()

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
        mock_embedding.index = 0

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        embedder.client.embeddings.create.return_value = mock_response

        result = embedder.embed("测试文本")

        assert len(result) == 5
        assert result == [0.1, 0.2, 0.3, 0.4, 0.5]
        print(f"✅ API 单条 Embedding 向量维度: {len(result)}, 值: {result}")

    def test_embed_batch(self):
        """测试 API 模式批量文本 Embedding 生成"""
        embedder = self._create_mock_api_embedder()

        # 模拟 3 条文本的 Embedding 返回（无序）
        mock_embeddings = []
        for i in [2, 0, 1]:  # 故意乱序，测试排序逻辑
            emb = MagicMock()
            emb.embedding = [float(i)] * 4
            emb.index = i
            mock_embeddings.append(emb)

        mock_response = MagicMock()
        mock_response.data = mock_embeddings
        embedder.client.embeddings.create.return_value = mock_response

        results = embedder.embed_batch(["文本1", "文本2", "文本3"])

        assert len(results) == 3
        # 验证排序后顺序正确
        assert results[0] == [0.0, 0.0, 0.0, 0.0]
        assert results[1] == [1.0, 1.0, 1.0, 1.0]
        assert results[2] == [2.0, 2.0, 2.0, 2.0]
        print(f"✅ API 批量 Embedding: {len(results)} 条, 每条维度: {len(results[0])}")
        embedder.client.embeddings.create.assert_called_once()
        print(f"✅ API 调用次数: 1 (批量处理)")

    def test_embed_empty_list(self):
        """测试空列表 Embedding"""
        embedder = self._create_mock_api_embedder()

        results = embedder.embed_batch([])

        assert results == []
        embedder.client.embeddings.create.assert_not_called()
        print("✅ 空列表返回空结果，未调用 API")

    def test_embed_api_timeout(self):
        """测试 API 超时异常处理"""
        embedder = self._create_mock_api_embedder()
        embedder.client.embeddings.create.side_effect = TimeoutError("请求超时")

        result = embedder.embed("测试文本")

        assert result == []
        print("✅ API 超时: 返回空列表，未抛出异常")

    def test_embed_api_key_invalid(self):
        """测试 API Key 无效异常处理"""
        embedder = self._create_mock_api_embedder()
        embedder.client.embeddings.create.side_effect = Exception("Invalid API key")

        results = embedder.embed_batch(["文本"])

        assert results == []
        print("✅ API Key 无效: 返回空列表，未抛出异常")

    def test_dimension_known_model(self):
        """测试已知模型的维度查询"""
        embedder = self._create_mock_api_embedder()
        embedder.model = "text-embedding-3-small"

        assert embedder.dimension == 1536
        print("✅ text-embedding-3-small 维度: 1536")

    def test_dimension_after_embed(self):
        """测试 embed 后维度自动记录"""
        embedder = self._create_mock_api_embedder()
        embedder.model = "custom-model"

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 768
        mock_embedding.index = 0
        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        embedder.client.embeddings.create.return_value = mock_response

        embedder.embed_batch(["测试"])
        assert embedder.dimension == 768
        print("✅ 自定义模型 embed 后维度: 768")


# ==================== LocalEmbedder 测试 ====================


class TestLocalEmbedder:
    """本地模式 Embedding 单元测试"""

    @patch("control_platform.rag.embedder.settings")
    def test_embed_single_text(self, mock_settings):
        """测试本地模式单条文本 Embedding 生成"""
        mock_settings.rag_embedding_model = "test-model"

        embedder = LocalEmbedder(model="test-model")
        # Mock _load_model 和 _model
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([[0.1, 0.2, 0.3, 0.4, 0.5]])
        embedder._model = mock_model

        result = embedder.embed("测试文本")

        assert len(result) == 5
        assert abs(result[0] - 0.1) < 1e-6
        print(f"✅ 本地单条 Embedding 向量维度: {len(result)}")

    @patch("control_platform.rag.embedder.settings")
    def test_embed_batch(self, mock_settings):
        """测试本地模式批量文本 Embedding 生成"""
        mock_settings.rag_embedding_model = "test-model"

        embedder = LocalEmbedder(model="test-model")
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([
            [0.1, 0.2, 0.3],
            [0.4, 0.5, 0.6],
            [0.7, 0.8, 0.9],
        ])
        embedder._model = mock_model

        results = embedder.embed_batch(["文本1", "文本2", "文本3"])

        assert len(results) == 3
        assert len(results[0]) == 3
        # 验证 normalize_embeddings=True 被传入
        mock_model.encode.assert_called_once()
        call_kwargs = mock_model.encode.call_args
        assert call_kwargs[1].get("normalize_embeddings") is True
        print(f"✅ 本地批量 Embedding: {len(results)} 条, 每条维度: {len(results[0])}")

    @patch("control_platform.rag.embedder.settings")
    def test_embed_empty_list(self, mock_settings):
        """测试空列表返回空结果"""
        mock_settings.rag_embedding_model = "test-model"

        embedder = LocalEmbedder(model="test-model")

        results = embedder.embed_batch([])

        assert results == []
        print("✅ 空列表返回空结果，未加载模型")

    @patch("control_platform.rag.embedder.settings")
    def test_lazy_loading(self, mock_settings):
        """测试懒加载：初始化时不加载模型"""
        mock_settings.rag_embedding_model = "test-model"

        embedder = LocalEmbedder(model="test-model")

        # 初始化后 _model 应为 None
        assert embedder._model is None
        print("✅ 懒加载: 初始化时未加载模型")

    @patch("control_platform.rag.embedder.settings")
    def test_embed_exception_handling(self, mock_settings):
        """测试本地推理异常处理"""
        mock_settings.rag_embedding_model = "test-model"

        embedder = LocalEmbedder(model="test-model")
        mock_model = MagicMock()
        mock_model.encode.side_effect = RuntimeError("GPU 内存不足")
        embedder._model = mock_model

        results = embedder.embed_batch(["文本"])

        assert results == []
        print("✅ 本地推理异常: 返回空列表，未抛出异常")

    @patch("control_platform.rag.embedder.settings")
    def test_dimension_property(self, mock_settings):
        """测试 dimension 属性"""
        mock_settings.rag_embedding_model = "test-model"

        embedder = LocalEmbedder(model="test-model")
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 1024
        embedder._model = mock_model

        assert embedder.dimension == 1024
        print("✅ 本地模型维度: 1024")


# ==================== Embedder 工厂类测试 ====================


class TestEmbedderFactory:
    """Embedder 工厂类单元测试"""

    @patch("control_platform.rag.embedder.settings")
    def test_create_local_embedder(self, mock_settings):
        """测试 provider=local 创建 LocalEmbedder"""
        mock_settings.rag_embedding_provider = "local"
        mock_settings.rag_embedding_model = "BAAI/bge-m3"

        embedder = Embedder(provider="local", model="test-model")

        assert embedder.provider_type == "local"
        assert isinstance(embedder._impl, LocalEmbedder)
        print("✅ provider=local → LocalEmbedder")

    @patch("control_platform.rag.embedder.ApiEmbedder.__init__", return_value=None)
    @patch("control_platform.rag.embedder.settings")
    def test_create_api_embedder(self, mock_settings, mock_api_init):
        """测试 provider=api 创建 ApiEmbedder"""
        mock_settings.rag_embedding_provider = "api"
        mock_settings.rag_embedding_model = "text-embedding-3-small"

        embedder = Embedder(provider="api", api_key="test-key", base_url="http://test")

        assert embedder.provider_type == "api"
        assert isinstance(embedder._impl, ApiEmbedder)
        print("✅ provider=api → ApiEmbedder")

    @patch("control_platform.rag.embedder.settings")
    def test_invalid_provider(self, mock_settings):
        """测试无效的 provider 类型"""
        mock_settings.rag_embedding_provider = "invalid"

        with pytest.raises(ValueError, match="不支持的 Embedding 提供者"):
            Embedder(provider="invalid")
        print("✅ 无效 provider: 抛出 ValueError")

    @patch("control_platform.rag.embedder.settings")
    def test_default_provider_from_settings(self, mock_settings):
        """测试从 settings 读取默认 provider"""
        mock_settings.rag_embedding_provider = "local"
        mock_settings.rag_embedding_model = "BAAI/bge-m3"

        embedder = Embedder()

        assert embedder.provider_type == "local"
        print("✅ 默认 provider 从 settings 读取: local")

    @patch("control_platform.rag.embedder.settings")
    def test_delegate_to_impl(self, mock_settings):
        """测试 Embedder 正确委托给内部实现"""
        mock_settings.rag_embedding_provider = "local"
        mock_settings.rag_embedding_model = "test-model"

        embedder = Embedder(provider="local", model="test-model")

        # Mock 内部实现
        mock_impl = MagicMock()
        mock_impl.embed.return_value = [0.1, 0.2, 0.3]
        mock_impl.embed_batch.return_value = [[0.1, 0.2], [0.3, 0.4]]
        mock_impl.dimension = 1024
        embedder._impl = mock_impl

        # 测试委托
        assert embedder.embed("test") == [0.1, 0.2, 0.3]
        assert embedder.embed_batch(["a", "b"]) == [[0.1, 0.2], [0.3, 0.4]]
        assert embedder.dimension == 1024

        mock_impl.embed.assert_called_once_with("test")
        mock_impl.embed_batch.assert_called_once_with(["a", "b"])
        print("✅ Embedder 正确委托给内部实现")