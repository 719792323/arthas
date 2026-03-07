"""
Embedding 封装模块

支持两种 Embedding 提供者：
- local: 使用 sentence-transformers 加载本地模型（默认，零 API 依赖）
- api: 使用 OpenAI 兼容 API（需要配置 api_key）

通过 settings.rag_embedding_provider 配置切换，默认优先本地模型。
"""

import logging
from abc import ABC, abstractmethod
from typing import List, Optional

from control_platform.config import settings

logger = logging.getLogger(__name__)


class BaseEmbedder(ABC):
    """Embedding 策略抽象基类"""

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        """生成单条文本的 Embedding 向量

        Args:
            text: 待向量化的文本

        Returns:
            向量列表（float），异常时返回空列表
        """
        ...

    @abstractmethod
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """批量生成文本的 Embedding 向量

        Args:
            texts: 待向量化的文本列表

        Returns:
            向量列表的列表，异常时返回空列表
        """
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """返回当前模型的向量维度"""
        ...

"""
# 安装 huggingface_hub（通常 sentence-transformers 已带）
pip install huggingface_hub

# 使用镜像下载模型到本地缓存
HF_ENDPOINT=https://hf-mirror.com huggingface-cli download BAAI/bge-m3
"""
class LocalEmbedder(BaseEmbedder):
    """本地 Embedding 生成器

    使用 sentence-transformers 加载 HuggingFace 模型，在本地完成推理。
    首次使用时自动下载模型到 HuggingFace 缓存目录。

    推荐模型：BAAI/bge-m3（多语言，中文效果极佳，1024 维）

    Attributes:
        model_name: 模型名称（HuggingFace repo id）
        _model: SentenceTransformer 模型实例（懒加载）
    """

    def __init__(self, model: Optional[str] = None):
        """初始化 LocalEmbedder

        Args:
            model: HuggingFace 模型名称，默认使用 settings.rag_embedding_model
        """
        self.model_name = model or settings.rag_embedding_model
        self._model = None  # 懒加载，避免 import 时就加载大模型

    def _load_model(self):
        """懒加载模型"""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer

                logger.info("正在加载本地 Embedding 模型: %s（首次加载需要下载）...", self.model_name)
                self._model = SentenceTransformer(self.model_name)
                logger.info(
                    "本地 Embedding 模型加载完成: %s, 维度: %d",
                    self.model_name,
                    self._model.get_sentence_embedding_dimension(),
                )
            except ImportError:
                raise ImportError(
                    "本地 Embedding 需要 sentence-transformers 库，"
                    "请执行: pip install sentence-transformers"
                )
            except Exception as e:
                logger.error("本地 Embedding 模型加载失败: %s", str(e))
                raise

    def embed(self, text: str) -> List[float]:
        """生成单条文本的 Embedding 向量"""
        result = self.embed_batch([text])
        return result[0] if result else []

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """批量生成文本的 Embedding 向量"""
        if not texts:
            return []
        try:
            self._load_model()
            # encode 返回 numpy ndarray，转为 list
            embeddings = self._model.encode(texts, normalize_embeddings=True)
            return [emb.tolist() for emb in embeddings]
        except Exception as e:
            logger.warning(
                "本地 Embedding 生成失败: %s, 模型: %s, 文本数量: %d",
                str(e),
                self.model_name,
                len(texts),
            )
            return []

    @property
    def dimension(self) -> int:
        """返回当前模型的向量维度"""
        self._load_model()
        return self._model.get_sentence_embedding_dimension()


class ApiEmbedder(BaseEmbedder):
    """远程 API Embedding 生成器

    使用 OpenAI 兼容的 Embedding API，适用于已有 API 服务的场景。
    复用项目现有的 LLM API 配置（api_key、base_url）。

    Attributes:
        model: Embedding 模型名称
        client: OpenAI 客户端实例
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        """初始化 ApiEmbedder

        Args:
            api_key: OpenAI API 密钥，默认使用 settings.llm_api_key
            base_url: API 基础 URL，默认使用 settings.llm_base_url
            model: Embedding 模型名称，默认使用 settings.rag_embedding_model
        """
        from openai import OpenAI

        self.model = model or settings.rag_embedding_model
        self.client = OpenAI(
            api_key=api_key or settings.llm_api_key,
            base_url=base_url or settings.llm_base_url,
        )
        self._dimension: Optional[int] = None

    def embed(self, text: str) -> List[float]:
        """生成单条文本的 Embedding 向量"""
        result = self.embed_batch([text])
        return result[0] if result else []

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """批量生成文本的 Embedding 向量"""
        if not texts:
            return []
        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=texts,
            )
            # 按 index 排序确保顺序一致
            sorted_data = sorted(response.data, key=lambda x: x.index)
            embeddings = [item.embedding for item in sorted_data]
            # 记录维度
            if embeddings and self._dimension is None:
                self._dimension = len(embeddings[0])
            return embeddings
        except Exception as e:
            logger.warning(
                "Embedding API 调用失败: %s, 模型: %s, 文本数量: %d",
                str(e),
                self.model,
                len(texts),
            )
            return []

    @property
    def dimension(self) -> int:
        """返回当前模型的向量维度（需要至少调用一次 embed 后才能获取准确值）"""
        if self._dimension is not None:
            return self._dimension
        # 常见模型维度映射
        _known_dimensions = {
            "text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072,
            "text-embedding-ada-002": 1536,
        }
        return _known_dimensions.get(self.model, 1536)


class Embedder(BaseEmbedder):
    """Embedding 统一入口（工厂 + 代理模式）

    根据 settings.rag_embedding_provider 自动选择 LocalEmbedder 或 ApiEmbedder，
    对外暴露统一的 embed / embed_batch 接口，上层调用方无需关心具体实现。

    使用方式：
        embedder = Embedder()  # 自动根据配置选择 provider
        vector = embedder.embed("你的文本")

    环境变量控制：
        CP_RAG_EMBEDDING_PROVIDER=local  → 使用本地模型（默认）
        CP_RAG_EMBEDDING_PROVIDER=api    → 使用远程 API
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        """初始化 Embedder

        Args:
            provider: 提供者类型 "local" 或 "api"，默认使用 settings.rag_embedding_provider
            api_key: API 密钥（仅 api 模式使用）
            base_url: API 基础 URL（仅 api 模式使用）
            model: 模型名称，默认使用 settings.rag_embedding_model
        """
        self._provider_type = provider or settings.rag_embedding_provider

        if self._provider_type == "local":
            self._impl = LocalEmbedder(model=model)
            logger.info("Embedder 使用本地模式, 模型: %s", model or settings.rag_embedding_model)
        elif self._provider_type == "api":
            self._impl = ApiEmbedder(api_key=api_key, base_url=base_url, model=model)
            logger.info("Embedder 使用 API 模式, 模型: %s", model or settings.rag_embedding_model)
        else:
            raise ValueError(
                f"不支持的 Embedding 提供者: {self._provider_type}，"
                f"请设置 CP_RAG_EMBEDDING_PROVIDER 为 'local' 或 'api'"
            )

    def embed(self, text: str) -> List[float]:
        """生成单条文本的 Embedding 向量"""
        return self._impl.embed(text)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """批量生成文本的 Embedding 向量"""
        return self._impl.embed_batch(texts)

    @property
    def dimension(self) -> int:
        """返回当前模型的向量维度"""
        return self._impl.dimension

    @property
    def provider_type(self) -> str:
        """返回当前使用的 provider 类型"""
        return self._provider_type