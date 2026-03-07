"""
Parent Chunk 存储组件

基于内存字典实现 Parent chunk 的存储与查询，支持 JSON 文件持久化。
Parent chunk 不参与向量检索（不生成 embedding），仅用于检索命中 child chunk 后
返回对应的完整上下文。
"""

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ParentChunkStore:
    """Parent chunk 存储（内存 + JSON 持久化）
    
    使用字典按 chunk_id 存储 parent chunk 的原始文本和 metadata。
    Parent chunk 不写入向量数据库，仅在检索阶段通过 child chunk 的
    parent_chunk_id 查找并返回。
    
    当提供 persist_path 时，数据会同步写入 JSON 文件，
    服务重启后自动从文件恢复，保持与 ChromaDB 持久化数据的一致性。
    
    数据结构:
        _store: {chunk_id: {"content": str, "metadata": dict}}
    """

    def __init__(self, persist_path: Optional[str] = None) -> None:
        """初始化 ParentChunkStore
        
        Args:
            persist_path: JSON 持久化文件路径，为 None 时使用纯内存模式
        """
        self._store: Dict[str, Dict[str, Any]] = {}
        self._persist_path = persist_path
        self._dirty = False  # 标记是否有未持久化的变更
        
        # 从持久化文件恢复数据
        if self._persist_path:
            self._load()

    def add_parent(self, chunk_id: str, content: str, metadata: Optional[dict] = None) -> None:
        """存储一个 parent chunk
        
        如果 chunk_id 已存在则覆盖。
        
        Args:
            chunk_id: parent chunk 的唯一标识
            content: parent chunk 的完整文本内容
            metadata: 元数据字典，应包含 chunk_level="parent"、total_children 等
        """
        self._store[chunk_id] = {
            "content": content,
            "metadata": metadata or {},
        }
        self._dirty = True

    def flush(self) -> None:
        """将内存中的数据持久化到文件（批量操作结束后调用）"""
        if self._persist_path and self._dirty:
            self._save()
            self._dirty = False

    def get_parent(self, chunk_id: str) -> Optional[Dict[str, Any]]:
        """按 chunk_id 查询 parent chunk
        
        Args:
            chunk_id: parent chunk 的唯一标识
            
        Returns:
            包含 content 和 metadata 的字典，不存在时返回 None
        """
        return self._store.get(chunk_id)

    def get_children_count(self, parent_chunk_id: str) -> int:
        """获取某个 parent chunk 下的 child chunk 总数
        
        从 parent chunk 的 metadata["total_children"] 中获取。
        
        Args:
            parent_chunk_id: parent chunk 的唯一标识
            
        Returns:
            child chunk 数量，parent 不存在或字段缺失时返回 0
        """
        parent = self._store.get(parent_chunk_id)
        if parent is None:
            return 0
        return parent.get("metadata", {}).get("total_children", 0)

    def reset(self) -> None:
        """清空所有存储的 parent chunk"""
        self._store.clear()
        self._dirty = False
        # 同时删除持久化文件
        if self._persist_path and os.path.exists(self._persist_path):
            try:
                os.remove(self._persist_path)
            except OSError as e:
                logger.warning("删除 ParentChunkStore 持久化文件失败: %s", str(e))
        logger.debug("ParentChunkStore 已重置")

    def __len__(self) -> int:
        """返回当前存储的 parent chunk 数量"""
        return len(self._store)

    def __contains__(self, chunk_id: str) -> bool:
        """检查是否包含指定的 parent chunk"""
        return chunk_id in self._store

    def _load(self) -> None:
        """从 JSON 文件加载数据"""
        if not self._persist_path or not os.path.exists(self._persist_path):
            return
        try:
            with open(self._persist_path, "r", encoding="utf-8") as f:
                self._store = json.load(f)
            logger.info("ParentChunkStore 从文件恢复 %d 个 parent chunk: %s", 
                       len(self._store), self._persist_path)
        except Exception as e:
            logger.warning("ParentChunkStore 加载持久化文件失败: %s", str(e))
            self._store = {}

    def _save(self) -> None:
        """将数据保存到 JSON 文件"""
        if not self._persist_path:
            return
        try:
            # 确保目录存在
            dir_path = os.path.dirname(self._persist_path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            with open(self._persist_path, "w", encoding="utf-8") as f:
                json.dump(self._store, f, ensure_ascii=False)
            logger.debug("ParentChunkStore 已持久化 %d 个 parent chunk", len(self._store))
        except Exception as e:
            logger.warning("ParentChunkStore 持久化失败: %s", str(e))