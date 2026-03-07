"""
向量数据库抽象基类

定义统一的向量数据库接口，所有具体实现（ChromaDB、Qdrant、Milvus 等）
必须继承此基类并实现全部抽象方法。上层组件（Retriever、Provider）仅依赖
此抽象接口，不感知底层具体实现。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class QueryResult:
    """向量检索结果的统一数据结构
    
    屏蔽各向量数据库返回格式的差异，为上层组件提供一致的结果表示。
    
    Attributes:
        document: 知识片段的原始文本内容
        score: 相似度分数（0~1，越高越相似）
        metadata: 元数据字典（包含 source_file、source_type、heading_path 等）
        id: 文档在向量数据库中的唯一标识
    """
    document: str
    score: float
    metadata: dict = field(default_factory=dict)
    id: str = ""


class BaseVectorStore(ABC):
    """向量数据库抽象基类
    
    定义了向量存储的标准接口，所有具体实现必须继承此类。
    新增向量数据库实现时，只需：
    1. 新建 xxx_vector_store.py 继承 BaseVectorStore
    2. 在 VectorStoreFactory 中注册映射
    3. 在 requirements.txt 中添加依赖
    无需修改 Retriever、Provider 等任何上层文件。
    """

    @abstractmethod
    def add_documents(
        self,
        documents: List[str],
        embeddings: List[List[float]],
        metadatas: List[dict],
        ids: List[str],
    ) -> None:
        """批量写入文档向量
        
        Args:
            documents: 文档原始文本列表
            embeddings: 对应的向量列表，每个向量为 float 列表
            metadatas: 对应的元数据列表
            ids: 对应的唯一标识列表
        """
        ...

    @abstractmethod
    def query(
        self,
        query_embedding: List[float],
        top_k: int = 3,
        filter: Optional[dict] = None,
    ) -> List[QueryResult]:
        """向量近邻搜索
        
        Args:
            query_embedding: 查询向量
            top_k: 返回最相似的前 K 个结果
            filter: 可选的元数据过滤条件
            
        Returns:
            按相似度降序排列的 QueryResult 列表
        """
        ...

    @abstractmethod
    def delete(self, ids: List[str]) -> None:
        """按 ID 删除文档
        
        Args:
            ids: 要删除的文档 ID 列表
        """
        ...

    @abstractmethod
    def count(self) -> int:
        """返回当前存储的文档总数
        
        Returns:
            文档数量
        """
        ...

    def get_all_documents(self) -> Optional[tuple]:
        """获取所有文档的 ID 和文本内容
        
        用于 BM25 索引全量重建时获取已持久化的所有 child chunk。
        
        Returns:
            (ids_list, documents_list) 元组，无数据时返回 None
        """
        return None

    def get_documents_by_ids(self, ids: List[str]) -> Optional[List[tuple]]:
        """按 ID 列表获取文档内容和元数据
        
        用于补充 BM25 独占命中但缺少 document/metadata 的 chunk。
        
        Args:
            ids: 需要查询的文档 ID 列表
            
        Returns:
            [(id, document, metadata), ...] 列表，无数据时返回 None
        """
        return None

    @abstractmethod
    def reset(self) -> None:
        """清空所有数据
        
        用于重建索引时的全量清理。
        """
        ...
