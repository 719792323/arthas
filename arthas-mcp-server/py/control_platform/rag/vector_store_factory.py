"""
向量数据库工厂

根据配置项 rag_store_type 创建对应的 BaseVectorStore 实现。
新增向量数据库实现时，只需在 _STORE_REGISTRY 中注册映射即可。
"""

import logging
from typing import Dict, Type

from control_platform.config import Settings
from control_platform.rag.base_vector_store import BaseVectorStore

logger = logging.getLogger(__name__)

# 向量数据库类型 -> 实现类的映射注册表
# 使用延迟导入避免不必要的依赖
_STORE_REGISTRY: Dict[str, str] = {
    "chroma": "control_platform.rag.chroma_vector_store.ChromaVectorStore",
}


class VectorStoreFactory:
    """向量数据库工厂类
    
    根据 rag_store_type 配置项实例化对应的向量数据库实现。
    """

    @staticmethod
    def create(store_type: str, config: Settings) -> BaseVectorStore:
        """根据类型创建向量数据库实例
        
        Args:
            store_type: 向量数据库类型（如 "chroma"、"qdrant"）
            config: 全局配置对象
            
        Returns:
            BaseVectorStore 实例
            
        Raises:
            ValueError: 未注册的向量数据库类型
        """
        store_type_lower = store_type.lower()
        if store_type_lower not in _STORE_REGISTRY:
            supported = ", ".join(sorted(_STORE_REGISTRY.keys()))
            raise ValueError(
                f"不支持的向量数据库类型: '{store_type}'。"
                f"支持的类型: [{supported}]"
            )

        # 延迟导入具体实现类
        class_path = _STORE_REGISTRY[store_type_lower]
        module_path, class_name = class_path.rsplit(".", 1)

        import importlib
        module = importlib.import_module(module_path)
        store_class: Type[BaseVectorStore] = getattr(module, class_name)

        # 根据不同的类型传递不同的配置参数
        if store_type_lower == "chroma":
            instance = store_class(persist_directory=config.rag_store_path)
        else:
            instance = store_class()

        logger.info(
            "已创建向量数据库实例: 类型=%s, 实现=%s",
            store_type,
            class_name,
        )
        return instance

    @staticmethod
    def supported_types() -> list:
        """返回所有支持的向量数据库类型"""
        return list(_STORE_REGISTRY.keys())
