"""
ChromaDB 向量数据库实现

基于 chromadb 库实现 BaseVectorStore 抽象接口，
使用 PersistentClient 持久化到配置指定的路径。
"""

import logging
import os
from typing import List, Optional, Tuple

import chromadb

from control_platform.rag.base_vector_store import BaseVectorStore, QueryResult

logger = logging.getLogger(__name__)

# ChromaDB 默认的集合名称
_DEFAULT_COLLECTION_NAME = "arthas_knowledge"


class ChromaVectorStore(BaseVectorStore):
    """ChromaDB 向量数据库实现
    
    使用 chromadb.PersistentClient 将向量持久化到本地磁盘。
    支持内存模式（persist_directory 为空时使用 EphemeralClient，用于测试）。
    
    Attributes:
        client: ChromaDB 客户端实例
        collection: ChromaDB 集合实例
    """

    def __init__(
        self,
        persist_directory: str = "",
        collection_name: str = _DEFAULT_COLLECTION_NAME,
    ):
        """初始化 ChromaVectorStore
        
        Args:
            persist_directory: 持久化目录路径，为空则使用内存模式
            collection_name: 集合名称
        """
        if persist_directory:
            os.makedirs(persist_directory, exist_ok=True)
            self.client = chromadb.PersistentClient(path=persist_directory)
        else:
            # 内存模式，用于测试
            self.client = chromadb.EphemeralClient()

        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},  # 使用余弦相似度
        )
        logger.info(
            "ChromaVectorStore 初始化完成, 持久化目录: %s, 集合: %s, 现有文档数: %d",
            persist_directory or "(内存模式)",
            collection_name,
            self.collection.count(),
        )

    def add_documents(
        self,
        documents: List[str],
        embeddings: List[List[float]],
        metadatas: List[dict],
        ids: List[str],
    ) -> None:
        """批量写入文档向量"""
        if not documents:
            return
        self.collection.add(
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
            ids=ids,
        )
        logger.debug("已写入 %d 个文档到 ChromaDB", len(documents))

    def query(
        self,
        query_embedding: List[float],
        top_k: int = 3,
        filter: Optional[dict] = None,
    ) -> List[QueryResult]:
        """向量近邻搜索
        
        ChromaDB 的 distances 使用余弦距离（cosine distance），
        需要将其转换为相似度分数（score = 1 - distance）。
        """
        query_params = {
            "query_embeddings": [query_embedding],
            "n_results": min(top_k, self.collection.count()) if self.collection.count() > 0 else top_k,
        }
        if filter:
            query_params["where"] = filter

        # 空集合时直接返回空结果，避免某些 ChromaDB 版本对空集合查询报错
        if self.collection.count() == 0:
            return []

        try:
            results = self.collection.query(**query_params)
        except Exception as e:
            logger.warning("ChromaDB 查询失败: %s", str(e))
            return []

        if not results or not results.get("documents") or not results["documents"][0]:
            return []

        query_results = []
        documents = results["documents"][0]
        distances = results["distances"][0] if results.get("distances") else [0.0] * len(documents)
        metadatas = results["metadatas"][0] if results.get("metadatas") else [{}] * len(documents)
        ids = results["ids"][0] if results.get("ids") else [""] * len(documents)

        for doc, dist, meta, doc_id in zip(documents, distances, metadatas, ids):
            # ChromaDB cosine distance -> similarity score
            # cosine distance = 1 - cosine_similarity
            score = 1.0 - dist
            query_results.append(QueryResult(
                document=doc,
                score=score,
                metadata=meta,
                id=doc_id,
            ))

        # 按相似度降序排列
        query_results.sort(key=lambda x: x.score, reverse=True)
        return query_results

    def delete(self, ids: List[str]) -> None:
        """按 ID 删除文档"""
        if not ids:
            return
        self.collection.delete(ids=ids)
        logger.debug("已从 ChromaDB 删除 %d 个文档", len(ids))

    def count(self) -> int:
        """返回当前存储的文档总数"""
        return self.collection.count()

    def get_all_documents(self) -> Optional[Tuple[List[str], List[str]]]:
        """获取所有文档的 ID 和文本内容
        
        用于 BM25 索引全量重建时获取已持久化的所有 child chunk。
        
        Returns:
            (ids_list, documents_list) 元组，无数据时返回 None
        """
        total = self.collection.count()
        if total == 0:
            return None
        try:
            result = self.collection.get(
                include=["documents"],
            )
            if result and result.get("ids") and result.get("documents"):
                return result["ids"], result["documents"]
            return None
        except Exception as e:
            logger.warning("ChromaDB 获取全量文档失败: %s", str(e))
            return None

    def get_documents_by_ids(self, ids: List[str]) -> Optional[List[Tuple[str, str, dict]]]:
        """按 ID 列表获取文档内容和元数据
        
        用于补充 BM25 独占命中但缺少 document/metadata 的 chunk。
        
        Args:
            ids: 需要查询的文档 ID 列表
            
        Returns:
            [(id, document, metadata), ...] 列表，无数据时返回 None
        """
        if not ids:
            return None
        try:
            result = self.collection.get(
                ids=ids,
                include=["documents", "metadatas"],
            )
            if result and result.get("ids"):
                docs = result.get("documents", [])
                metas = result.get("metadatas", [])
                return [
                    (rid, docs[i] if i < len(docs) else "", metas[i] if i < len(metas) else {})
                    for i, rid in enumerate(result["ids"])
                ]
            return None
        except Exception as e:
            logger.warning("ChromaDB 按 ID 查询文档失败: %s", str(e))
            return None

    def reset(self) -> None:
        """清空所有数据
        
        通过删除并重建集合实现全量清理。
        """
        collection_name = self.collection.name
        collection_metadata = self.collection.metadata
        self.client.delete_collection(name=collection_name)
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata=collection_metadata,
        )
        logger.info("ChromaDB 集合已重置: %s", collection_name)
