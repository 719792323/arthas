"""
BM25 关键词检索组件

基于 rank-bm25 库实现 BM25 关键词检索，使用 jieba 进行中文分词。
作为向量语义检索的补充路径，增强对 Arthas 命令名、Java 类名等
关键词的精确匹配能力。
"""

import logging
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# 延迟导入，避免未安装时直接报错
_bm25_available = True
try:
    from rank_bm25 import BM25Okapi
except ImportError:
    _bm25_available = False
    logger.warning("rank-bm25 未安装，BM25 检索不可用")

_jieba_available = True
try:
    import jieba
    # 静默 jieba 初始化日志
    jieba.setLogLevel(logging.WARNING)
except ImportError:
    _jieba_available = False
    logger.warning("jieba 未安装，中文分词不可用，BM25 将使用空格分词")


def _tokenize(text: str) -> List[str]:
    """对文本进行分词
    
    优先使用 jieba 中文分词，不可用时回退到简单的空格分词。
    
    Args:
        text: 待分词文本
        
    Returns:
        分词结果列表
    """
    if _jieba_available:
        # jieba.lcut 返回精确模式分词结果，过滤空白和单字符标点
        tokens = jieba.lcut(text)
        return [t for t in (tok.strip() for tok in tokens) if t]
    else:
        return [t for t in text.split() if t.strip()]


class BM25Index:
    """BM25 关键词检索索引
    
    为 child chunk 构建 BM25 索引，支持中文分词检索。
    作为混合检索中的关键词匹配路径。
    
    Attributes:
        _chunk_ids: 已索引的 chunk_id 列表，与 BM25 内部文档顺序对应
        _bm25: BM25Okapi 实例
        _built: 索引是否已构建
    """

    def __init__(self) -> None:
        self._chunk_ids: List[str] = []
        self._bm25: Optional["BM25Okapi"] = None
        self._built: bool = False

    @property
    def is_available(self) -> bool:
        """BM25 检索是否可用（依赖已安装且索引已构建）"""
        return _bm25_available and self._built

    def build(self, chunk_ids: List[str], documents: List[str]) -> None:
        """构建 BM25 索引
        
        Args:
            chunk_ids: chunk_id 列表，与 documents 一一对应
            documents: 文档文本列表
            
        Raises:
            RuntimeError: rank-bm25 未安装时抛出
        """
        if not _bm25_available:
            raise RuntimeError("rank-bm25 未安装，无法构建 BM25 索引")

        if len(chunk_ids) != len(documents):
            raise ValueError(
                f"chunk_ids 数量 ({len(chunk_ids)}) 与 documents 数量 ({len(documents)}) 不一致"
            )

        if not chunk_ids:
            logger.warning("BM25 索引构建：文档列表为空")
            self._built = False
            return

        self._chunk_ids = list(chunk_ids)
        # 对每个文档进行分词
        tokenized_docs = [_tokenize(doc) for doc in documents]
        self._bm25 = BM25Okapi(tokenized_docs)
        self._built = True
        logger.info("BM25 索引构建完成，共 %d 个文档", len(chunk_ids))

    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """执行 BM25 关键词检索
        
        Args:
            query: 查询文本
            top_k: 返回的最大结果数
            
        Returns:
            (chunk_id, score) 元组列表，按 score 降序排列。
            索引未构建或异常时返回空列表。
        """
        if not self.is_available or self._bm25 is None:
            return []

        try:
            tokenized_query = _tokenize(query)
            if not tokenized_query:
                return []

            scores = self._bm25.get_scores(tokenized_query)

            # 将 chunk_id 和 score 配对，过滤 score <= 0 的结果
            scored = [
                (self._chunk_ids[i], float(scores[i]))
                for i in range(len(scores))
                if scores[i] > 0
            ]
            # 按 score 降序排列
            scored.sort(key=lambda x: x[1], reverse=True)
            return scored[:top_k]

        except Exception as e:
            logger.warning("BM25 检索异常: %s", str(e))
            return []

    def reset(self) -> None:
        """重置索引，清空所有数据"""
        self._chunk_ids = []
        self._bm25 = None
        self._built = False
        logger.debug("BM25Index 已重置")

    def __len__(self) -> int:
        """返回当前索引的文档数量"""
        return len(self._chunk_ids)
