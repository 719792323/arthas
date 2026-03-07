"""
文档解析器注册表

根据文件扩展名分发给对应的 Chunker 进行解析。
初始化时自动注册内置的 MarkdownChunker，支持外部注册自定义 Chunker。
"""

import logging
import os
from typing import Dict, List, Optional

from control_platform.rag.base_chunker import BaseChunker, DocumentChunk

logger = logging.getLogger(__name__)


class ChunkerRegistry:
    """文档解析器注册表
    
    管理文件扩展名到 Chunker 的映射关系，按文件扩展名分发解析请求。
    初始化时自动注册内置 Chunker（当前仅 MarkdownChunker）。
    """

    def __init__(self):
        """初始化注册表并注册内置 Chunker"""
        self._chunkers: Dict[str, BaseChunker] = {}
        self._register_builtins()

    def _register_builtins(self):
        """注册内置的 Chunker"""
        from control_platform.rag.markdown_chunker import MarkdownChunker
        self.register(MarkdownChunker())

    def register(self, chunker: BaseChunker) -> None:
        """注册一个 Chunker
        
        将 Chunker 支持的所有扩展名注册到映射表中。
        
        Args:
            chunker: 待注册的 Chunker 实例
        """
        for ext in chunker.supported_extensions():
            ext_lower = ext.lower()
            self._chunkers[ext_lower] = chunker
            logger.debug("已注册 Chunker: %s -> %s", ext_lower, type(chunker).__name__)

    def get_chunker(self, file_extension: str) -> Optional[BaseChunker]:
        """根据文件扩展名获取对应的 Chunker
        
        Args:
            file_extension: 文件扩展名（如 ".md"）
            
        Returns:
            对应的 Chunker 实例，未找到则返回 None
        """
        return self._chunkers.get(file_extension.lower())

    def chunk_file(
        self,
        file_path: str,
        metadata: Optional[dict] = None,
    ) -> List[DocumentChunk]:
        """解析文件为知识片段
        
        根据文件扩展名查找对应的 Chunker，并调用其 chunk() 方法。
        
        Args:
            file_path: 文件的完整路径
            metadata: 可选的额外元数据
            
        Returns:
            DocumentChunk 列表，未注册的扩展名返回空列表
        """
        ext = os.path.splitext(file_path)[1].lower()
        chunker = self.get_chunker(ext)
        if chunker is None:
            logger.warning(
                "未注册的文件扩展名: %s, 文件: %s, 已跳过",
                ext,
                file_path,
            )
            return []
        return chunker.chunk(file_path, metadata)

    @property
    def supported_extensions(self) -> List[str]:
        """返回所有已注册的文件扩展名列表"""
        return list(self._chunkers.keys())
