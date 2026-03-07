"""
文档解析器抽象基类

定义统一的文档切片接口，所有具体实现（MarkdownChunker、TextChunker 等）
必须继承此基类并实现全部抽象方法。ChunkerRegistry 根据文件扩展名
分发给对应的 Chunker 进行解析。
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

from control_platform.decision.context_management.token_counter import TokenCounter

logger = logging.getLogger(__name__)


@dataclass
class DocumentChunk:
    """文档知识片段的统一数据结构
    
    由 Chunker 解析文档后产生，作为后续 Embedding 和向量存储的输入。
    
    Attributes:
        content: 知识片段的文本内容
        metadata: 元数据字典，至少包含 source_file（来源文件路径）和 file_type（文件类型）
        chunk_id: 全局唯一标识，格式为 {file_hash}_{chunk_index}
    """
    content: str
    metadata: dict = field(default_factory=dict)
    chunk_id: str = ""


class BaseChunker(ABC):
    """文档解析器抽象基类
    
    定义了文档切片的标准接口，所有具体实现必须继承此类。
    新增文档格式支持时，只需：
    1. 新建 xxx_chunker.py 继承 BaseChunker
    2. 在 ChunkerRegistry 初始化时注册
    无需修改 Provider、Retriever 等任何上层文件。
    
    Attributes:
        max_chunk_size: child chunk 最大 token 数，超过则二级切分（默认 512）
        min_chunk_size: child chunk 最小 token 数，低于则跳过不索引（默认 32）
        overlap_size: 二级切分时相邻子 chunk 的重叠 token 数（默认 128）
        token_counter: TokenCounter 实例，用于 token 计数
    """

    def __init__(
        self,
        max_chunk_size: int = 512,
        min_chunk_size: int = 32,
        overlap_size: int = 128,
        token_counter: Optional[TokenCounter] = None,
    ):
        """初始化 BaseChunker
        
        Args:
            max_chunk_size: child chunk 最大 token 数（默认 512），超过则二级切分
            min_chunk_size: child chunk 最小 token 数（默认 32），低于则跳过不索引
            overlap_size: 二级切分时相邻子 chunk 的重叠 token 数（默认 128）
            token_counter: TokenCounter 实例，为 None 时自动创建
        """
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size
        self.overlap_size = overlap_size
        self.token_counter = token_counter or TokenCounter()

    def count_tokens(self, text: str) -> int:
        """计算文本的 token 数
        
        Args:
            text: 待计算的文本
            
        Returns:
            token 数量
        """
        return self.token_counter.count_text(text)

    @abstractmethod
    def supported_extensions(self) -> List[str]:
        """返回该 Chunker 支持的文件扩展名列表
        
        Returns:
            扩展名列表，如 [".md", ".markdown"]
        """
        ...

    @abstractmethod
    def chunk(
        self,
        file_path: str,
        metadata: Optional[dict] = None,
    ) -> List[DocumentChunk]:
        """将文件解析为知识片段列表
        
        Args:
            file_path: 文件的完整路径
            metadata: 可选的额外元数据，会合并到每个片段的 metadata 中
            
        Returns:
            DocumentChunk 列表，空文档返回空列表
        """
        ...