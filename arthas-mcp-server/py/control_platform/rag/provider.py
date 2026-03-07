"""
RAG 统一入口

RAGProvider 是 RAG 模块的唯一对外接口，供 ContextBuilder 调用。
负责组装内部组件（ChunkerRegistry、Embedder、VectorStoreFactory、Retriever），
提供知识库构建和知识检索功能。
"""

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional

from control_platform.config import settings
from control_platform.decision.context_management.token_counter import TokenCounter
from control_platform.rag.base_vector_store import QueryResult
from control_platform.rag.bm25_index import BM25Index
from control_platform.rag.chunker_registry import ChunkerRegistry
from control_platform.rag.embedder import Embedder
from control_platform.rag.parent_store import ParentChunkStore
from control_platform.rag.retriever import Retriever
from control_platform.rag.vector_store_factory import VectorStoreFactory

logger = logging.getLogger(__name__)


@dataclass
class RAGResult:
    """RAG 检索结果的封装
    
    Attributes:
        results: 检索到的知识片段列表
        total_tokens: 所有片段的总 token 数
    """
    results: List[QueryResult] = field(default_factory=list)
    total_tokens: int = 0


class RAGProvider:
    """RAG 统一入口
    
    组装 ChunkerRegistry、Embedder、VectorStoreFactory、Retriever，
    对外提供 build_index() 和 retrieve() 两个核心方法。
    外部组件（ContextBuilder）仅通过此类交互，不直接依赖内部组件。
    
    降级模式：
    - rag_enabled=False：直接返回 None
    - 知识库目录不存在或为空：记录 WARNING，返回 None
    - 检索异常：返回 None，不阻断主流程
    """

    def __init__(self):
        """初始化 RAGProvider
        
        根据配置创建内部组件。如果 RAG 未启用则不初始化任何组件。
        """
        self._initialized = False
        self._token_counter = TokenCounter()
        self._chunker_registry: Optional[ChunkerRegistry] = None
        self._embedder: Optional[Embedder] = None
        self._retriever: Optional[Retriever] = None
        self._parent_store: Optional[ParentChunkStore] = None
        self._bm25_index: Optional[BM25Index] = None
        # 已索引文件的 hash 缓存，用于增量构建
        self._indexed_hashes: dict = {}
        # 持久化目录（复用向量数据库的持久化路径）
        self._persist_dir: str = ""

        if not settings.rag_enabled:
            logger.info("RAG 模块已禁用 (rag_enabled=False)")
            return

        try:
            self._persist_dir = settings.rag_store_path
            self._chunker_registry = ChunkerRegistry()
            self._embedder = Embedder()
            vector_store = VectorStoreFactory.create(
                store_type=settings.rag_store_type,
                config=settings,
            )
            # ParentChunkStore 使用 JSON 持久化，保持与 ChromaDB 数据一致性
            parent_persist_path = os.path.join(self._persist_dir, "parent_store.json") if self._persist_dir else None
            self._parent_store = ParentChunkStore(persist_path=parent_persist_path)
            self._bm25_index = BM25Index()
            self._retriever = Retriever(
                vector_store=vector_store,
                embedder=self._embedder,
                parent_store=self._parent_store,
                bm25_index=self._bm25_index,
            )
            # 从持久化文件恢复已索引文件的 hash 缓存
            self._load_indexed_hashes()
            self._initialized = True
            logger.info("RAGProvider 初始化完成")
        except Exception as e:
            logger.warning("RAGProvider 初始化失败，将以降级模式运行: %s", str(e))

    @property
    def is_available(self) -> bool:
        """RAG 是否可用"""
        return self._initialized and settings.rag_enabled

    def build_index(self) -> int:
        """构建知识库索引
        
        扫描知识库目录下的所有文档文件，通过 ChunkerRegistry 切片，
        生成 Embedding 并写入向量存储。支持增量构建（基于文件 hash 跳过未变更文件）。
        
        Returns:
            新增索引的文档片段数，降级模式返回 0
        """
        if not self.is_available:
            logger.info("RAG 不可用，跳过索引构建")
            return 0

        knowledge_dir = settings.rag_knowledge_dir
        if not os.path.isdir(knowledge_dir):
            logger.warning("知识库目录不存在: %s, RAG 将以降级模式运行", knowledge_dir)
            return 0

        start_time = time.time()
        total_chunks = 0
        new_child_chunks = 0
        new_parent_chunks = 0
        # 收集所有 child chunk 用于批量构建 BM25 索引
        all_child_ids: List[str] = []
        all_child_texts: List[str] = []

        # 遍历知识库目录
        for root, _, files in os.walk(knowledge_dir):
            for file_name in files:
                file_path = os.path.join(root, file_name)
                
                # 计算文件 hash，检查是否已索引
                file_hash = self._compute_file_hash(file_path)
                if file_hash and file_hash in self._indexed_hashes:
                    logger.debug("文件未变更，跳过: %s", file_path)
                    continue

                # 确定知识源类型
                source_type = self._detect_source_type(file_path, knowledge_dir)
                metadata = {"source_type": source_type}

                # 切片（返回 parent + child 两层 chunk）
                chunks = self._chunker_registry.chunk_file(file_path, metadata)
                if not chunks:
                    continue

                total_chunks += len(chunks)

                # 分离 parent chunk 和 child chunk
                parent_chunks = [
                    c for c in chunks
                    if c.metadata.get("chunk_level") == "parent"
                ]
                child_chunks = [
                    c for c in chunks
                    if c.metadata.get("chunk_level") == "child"
                    and not c.metadata.get("skipped", False)
                ]

                # 将 parent chunk 存入 ParentChunkStore（不生成 embedding）
                if self._parent_store is not None:
                    for pc in parent_chunks:
                        self._parent_store.add_parent(
                            chunk_id=pc.chunk_id,
                            content=pc.content,
                            metadata=pc.metadata,
                        )
                    new_parent_chunks += len(parent_chunks)

                if not child_chunks:
                    if file_hash:
                        self._indexed_hashes[file_hash] = file_path
                    continue

                # 仅对 child chunk 生成 embedding 并写入向量数据库
                texts = [c.content for c in child_chunks]
                embeddings = self._embedder.embed_batch(texts)
                if not embeddings or len(embeddings) != len(child_chunks):
                    logger.warning(
                        "Embedding 生成失败或数量不匹配, 文件: %s, 期望: %d, 实际: %d",
                        file_path,
                        len(child_chunks),
                        len(embeddings) if embeddings else 0,
                    )
                    continue

                # 写入向量存储
                documents = [c.content for c in child_chunks]
                metadatas = [c.metadata for c in child_chunks]
                ids = [c.chunk_id for c in child_chunks]

                self._retriever.vector_store.add_documents(
                    documents=documents,
                    embeddings=embeddings,
                    metadatas=metadatas,
                    ids=ids,
                )

                # 收集 child chunk 用于 BM25 索引
                all_child_ids.extend(ids)
                all_child_texts.extend(documents)

                new_child_chunks += len(child_chunks)
                if file_hash:
                    self._indexed_hashes[file_hash] = file_path

        # 批量构建 BM25 索引（全量重建，包含历史已索引的 child chunk）
        if self._bm25_index is not None:
            try:
                # BM25 索引是纯内存结构，必须用全量 child chunk 重建
                # 如果本次有增量数据，需要从向量数据库获取所有已有的 child chunk
                if all_child_ids:
                    existing = self._retriever.vector_store.get_all_documents()
                    if existing and isinstance(existing, tuple) and len(existing) == 2:
                        existing_ids, existing_texts = existing
                        # 去掉本次已收集的（避免重复），然后合并
                        new_id_set = set(all_child_ids)
                        for eid, etxt in zip(existing_ids, existing_texts):
                            if eid not in new_id_set:
                                all_child_ids.append(eid)
                                all_child_texts.append(etxt)
                    self._bm25_index.build(all_child_ids, all_child_texts)
                elif self._retriever.vector_store.count() > 0 and not self._bm25_index.is_available:
                    # 没有新增数据但 BM25 未构建（如服务重启后），从向量数据库恢复
                    existing = self._retriever.vector_store.get_all_documents()
                    if existing and isinstance(existing, tuple) and len(existing) == 2:
                        existing_ids, existing_texts = existing
                        self._bm25_index.build(existing_ids, existing_texts)
            except Exception as e:
                logger.warning("BM25 索引构建失败，混合检索将降级为纯向量检索: %s", str(e))

        # 持久化 ParentChunkStore 和已索引文件的 hash 缓存
        if self._parent_store is not None:
            self._parent_store.flush()
        self._save_indexed_hashes()

        elapsed = time.time() - start_time
        logger.info(
            "知识库索引构建完成: 耗时=%.2fs, 总片段数=%d, parent=%d, child=%d, 向量存储文档总数=%d",
            elapsed,
            total_chunks,
            new_parent_chunks,
            new_child_chunks,
            self._retriever.vector_store.count(),
        )
        return new_child_chunks

    def retrieve(self, user_query: str) -> Optional[RAGResult]:
        """检索与用户问题相关的知识
        
        调用 Retriever 检索 → Token 预算截断 → 返回结果。
        
        Args:
            user_query: 用户问题文本
            
        Returns:
            RAGResult 包含检索结果和总 token 数，
            降级模式或无结果时返回 None
        """
        if not self.is_available:
            return None

        start_time = time.time()

        try:
            results = self._retriever.retrieve(
                query=user_query,
                top_k=settings.rag_top_k,
                similarity_threshold=settings.rag_similarity_threshold,
            )

            if not results:
                logger.info("RAG 检索无结果, query: %s", user_query[:100])
                return None

            # Token 预算截断
            truncated_results = self._truncate_by_token_budget(results)

            if not truncated_results:
                return None

            # 计算总 token 数
            total_tokens = sum(
                self._token_counter.count_text(r.document) for r in truncated_results
            )

            elapsed = time.time() - start_time
            scores_str = ", ".join(f"{r.score:.4f}" for r in truncated_results)
            logger.info(
                "RAG 检索完成: 耗时=%.3fs, 返回片段数=%d, 相似度=[%s], 总token数=%d",
                elapsed,
                len(truncated_results),
                scores_str,
                total_tokens,
            )

            return RAGResult(results=truncated_results, total_tokens=total_tokens)

        except Exception as e:
            logger.warning("RAG 检索异常: %s, query: %s", str(e), user_query[:100])
            return None

    def _truncate_by_token_budget(
        self, results: List[QueryResult]
    ) -> List[QueryResult]:
        """按 Token 预算截断检索结果
        
        按 score 降序保留片段，直到累计 token 超过 rag_max_tokens 预算。
        如果单个 parent chunk 超过 rag_max_tokens，截断到段落/句子边界。
        
        Args:
            results: 按 score 降序排列的检索结果
            
        Returns:
            截断后的结果列表
        """
        max_tokens = settings.rag_max_tokens
        truncated = []
        current_tokens = 0

        for result in results:
            tokens = self._token_counter.count_text(result.document)

            # 单个 parent chunk 超过 rag_max_tokens 时，截断到段落/句子边界
            if tokens > max_tokens:
                result = QueryResult(
                    document=self._truncate_to_boundary(result.document, max_tokens),
                    score=result.score,
                    metadata=result.metadata,
                    id=result.id,
                )
                tokens = self._token_counter.count_text(result.document)

            if current_tokens + tokens > max_tokens and truncated:
                # 已超预算且已有结果，停止
                logger.debug(
                    "RAG Token 预算截断: 已用=%d, 当前片段=%d, 预算=%d",
                    current_tokens,
                    tokens,
                    max_tokens,
                )
                break
            truncated.append(result)
            current_tokens += tokens

        return truncated

    def _truncate_to_boundary(self, text: str, max_tokens: int) -> str:
        """将文本截断到不超过 max_tokens 的段落/句子边界
        
        优先在段落边界（双换行）截断，其次在句子边界（。！？.!?）截断，
        最后硬截断到 token 边界。
        
        Args:
            text: 待截断的文本
            max_tokens: token 上限
            
        Returns:
            截断后的文本
        """
        if self._token_counter.count_text(text) <= max_tokens:
            return text

        # 策略 1：按段落（双换行）分割，逐段累加
        paragraphs = text.split("\n\n")
        if len(paragraphs) > 1:
            result_parts = []
            for para in paragraphs:
                candidate = "\n\n".join(result_parts + [para])
                if self._token_counter.count_text(candidate) > max_tokens:
                    break
                result_parts.append(para)
            if result_parts:
                return "\n\n".join(result_parts)

        # 策略 2：按句子分割，逐句累加
        sentences = re.split(r'(?<=[。！？.!?\n])\s*', text)
        if len(sentences) > 1:
            result_parts = []
            for sent in sentences:
                candidate = "".join(result_parts + [sent])
                if self._token_counter.count_text(candidate) > max_tokens:
                    break
                result_parts.append(sent)
            if result_parts:
                return "".join(result_parts)

        # 策略 3：硬截断——逐字符缩减（粗略按字符比例估算）
        ratio = max_tokens / max(self._token_counter.count_text(text), 1)
        cut_len = int(len(text) * ratio * 0.9)  # 留 10% 余量
        return text[:cut_len]

    def _compute_file_hash(self, file_path: str) -> Optional[str]:
        """计算文件内容的 MD5 hash"""
        try:
            with open(file_path, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return None

    def _detect_source_type(self, file_path: str, knowledge_dir: str) -> str:
        """根据文件路径检测知识源类型
        
        约定目录结构：
        - knowledge/tool_docs/  -> tool_doc
        - knowledge/troubleshooting/ -> troubleshooting_guide
        - knowledge/cases/ -> historical_case
        
        Args:
            file_path: 文件完整路径
            knowledge_dir: 知识库根目录
            
        Returns:
            知识源类型字符串
        """
        rel_path = os.path.relpath(file_path, knowledge_dir)
        parts = rel_path.split(os.sep)

        if len(parts) > 1:
            first_dir = parts[0].lower()
            if "tool" in first_dir:
                return "tool_doc"
            elif "troubleshoot" in first_dir:
                return "troubleshooting_guide"
            elif "case" in first_dir:
                return "historical_case"

        return "general"

    def _load_indexed_hashes(self) -> None:
        """从持久化文件恢复已索引文件的 hash 缓存"""
        if not self._persist_dir:
            return
        hash_file = os.path.join(self._persist_dir, "indexed_hashes.json")
        if not os.path.exists(hash_file):
            return
        try:
            with open(hash_file, "r", encoding="utf-8") as f:
                self._indexed_hashes = json.load(f)
            logger.info("已从持久化文件恢复 %d 个已索引文件 hash", len(self._indexed_hashes))
        except Exception as e:
            logger.warning("加载已索引文件 hash 缓存失败: %s", str(e))
            self._indexed_hashes = {}

    def _save_indexed_hashes(self) -> None:
        """将已索引文件的 hash 缓存持久化到文件"""
        if not self._persist_dir or not self._indexed_hashes:
            return
        hash_file = os.path.join(self._persist_dir, "indexed_hashes.json")
        try:
            os.makedirs(self._persist_dir, exist_ok=True)
            with open(hash_file, "w", encoding="utf-8") as f:
                json.dump(self._indexed_hashes, f, ensure_ascii=False)
            logger.debug("已持久化 %d 个已索引文件 hash", len(self._indexed_hashes))
        except Exception as e:
            logger.warning("持久化已索引文件 hash 缓存失败: %s", str(e))
