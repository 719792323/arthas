"""
知识检索器

基于向量近邻搜索 + BM25 关键词检索实现混合知识检索。
支持 Parent-Child 双层索引：用 child chunk 检索，返回 parent chunk。
支持 RRF 融合、Parent Score 聚合与去重、相似度阈值过滤。
异常时自动降级，不阻断主流程。
"""

import logging
from typing import Dict, List, Optional, Tuple

from control_platform.config import settings
from control_platform.rag.base_vector_store import BaseVectorStore, QueryResult
from control_platform.rag.bm25_index import BM25Index
from control_platform.rag.embedder import Embedder
from control_platform.rag.parent_store import ParentChunkStore

logger = logging.getLogger(__name__)

# RRF 融合常数 k，标准值 60
_RRF_K = 60
# 多命中加成系数 α
_MULTI_HIT_ALPHA = 0.15


class Retriever:
    """知识检索器

    将用户查询转换为向量，并通过向量数据库 + BM25 执行混合检索，
    将命中的 child chunk 映射到 parent chunk 后返回。

    仅依赖 BaseVectorStore 抽象接口，不引入任何具体实现类。

    Attributes:
        vector_store: 向量数据库实例（通过抽象接口注入）
        embedder: Embedding 生成器实例
        parent_store: Parent chunk 存储（可选，为 None 时退化为直接返回 child）
        bm25_index: BM25 关键词检索索引（可选，为 None 或不可用时退化为纯向量检索）
    """

    def __init__(
        self,
        vector_store: BaseVectorStore,
        embedder: Embedder,
        parent_store: Optional[ParentChunkStore] = None,
        bm25_index: Optional[BM25Index] = None,
    ):
        """初始化 Retriever

        Args:
            vector_store: 向量数据库实例（BaseVectorStore 抽象类型）
            embedder: Embedding 生成器实例
            parent_store: Parent chunk 存储实例（可选）
            bm25_index: BM25 检索索引实例（可选）
        """
        self.vector_store = vector_store
        self.embedder = embedder
        self.parent_store = parent_store
        self.bm25_index = bm25_index

    def retrieve(
        self,
        query: str,
        top_k: int = 3,
        similarity_threshold: float = 0.5,
        filter: Optional[dict] = None,
    ) -> List[QueryResult]:
        """检索与查询最相关的知识片段

        流程：
        1. 向量语义检索 + BM25 关键词检索（双路）
        2. RRF 融合两路 child chunk 结果
        3. Child → Parent 映射 + Score 聚合与去重
        4. 按 parent_score 降序返回 top_k 个 parent chunk

        如果 parent_store 未配置，退化为原有行为（直接返回 child chunk）。
        如果 BM25 不可用或被禁用，退化为纯向量检索。

        Args:
            query: 用户查询文本
            top_k: 返回最相似的前 K 个结果
            similarity_threshold: 相似度过滤阈值（纯向量检索时使用）
            filter: 可选的元数据过滤条件

        Returns:
            按相关性降序排列的 QueryResult 列表，异常时返回空列表
        """
        try:
            # Step 1: 向量语义检索
            vector_results = self._vector_search(query, top_k * 3, filter)

            # Step 2: 判断是否启用混合检索
            hybrid_enabled = (
                settings.rag_hybrid_search_enabled
                and self.bm25_index is not None
                and self.bm25_index.is_available
            )

            if hybrid_enabled:
                # BM25 关键词检索
                bm25_results = self._bm25_search(query, top_k * 3)

                if bm25_results is not None:
                    # RRF 融合
                    fused_children = self._rrf_fusion(vector_results, bm25_results)
                    # 使用 RRF 分数阈值过滤
                    rrf_threshold = settings.rag_rrf_score_threshold
                    fused_children = [
                        (cid, score, meta, doc)
                        for cid, score, meta, doc in fused_children
                        if score >= rrf_threshold
                    ]
                else:
                    # BM25 异常，降级为纯向量检索
                    fused_children = self._vector_to_child_tuples(
                        vector_results, similarity_threshold
                    )
            else:
                # 纯向量检索
                fused_children = self._vector_to_child_tuples(
                    vector_results, similarity_threshold
                )

            if not fused_children:
                return []

            # Step 3: Child → Parent 映射 + Score 聚合
            if self.parent_store is not None:
                parent_results = self._aggregate_to_parents(fused_children, top_k)
            else:
                # 无 parent_store，直接返回 child chunk
                parent_results = self._child_tuples_to_results(fused_children, top_k)

            return parent_results

        except Exception as e:
            logger.warning(
                "知识检索异常: %s, query: %s",
                str(e),
                query[:100],
            )
            return []

    def _vector_search(
        self,
        query: str,
        candidate_count: int,
        filter: Optional[dict] = None,
    ) -> List[QueryResult]:
        """执行向量语义检索

        Args:
            query: 查询文本
            candidate_count: 候选结果数量
            filter: 元数据过滤条件

        Returns:
            QueryResult 列表，异常时返回空列表
        """
        query_embedding = self.embedder.embed(query)
        if not query_embedding:
            logger.warning("查询文本 Embedding 生成失败, query: %s", query[:100])
            return []

        results = self.vector_store.query(
            query_embedding=query_embedding,
            top_k=candidate_count,
            filter=filter,
        )
        return results or []

    def _bm25_search(
        self, query: str, candidate_count: int
    ) -> Optional[List[Tuple[str, float]]]:
        """执行 BM25 关键词检索

        Args:
            query: 查询文本
            candidate_count: 候选结果数量

        Returns:
            (chunk_id, score) 列表，异常时返回 None（触发降级）
        """
        try:
            if self.bm25_index is None or not self.bm25_index.is_available:
                return None
            results = self.bm25_index.search(query, top_k=candidate_count)
            return results
        except Exception as e:
            logger.warning("BM25 检索异常，降级为纯向量检索: %s", str(e))
            return None

    def _vector_to_child_tuples(
        self,
        vector_results: List[QueryResult],
        similarity_threshold: float,
    ) -> List[Tuple[str, float, dict, str]]:
        """将向量检索结果转换为统一的 child tuple 格式，并按阈值过滤

        Returns:
            (chunk_id, score, metadata, document) 元组列表
        """
        filtered = [r for r in vector_results if r.score >= similarity_threshold]
        filtered.sort(key=lambda x: x.score, reverse=True)
        return [(r.id, r.score, r.metadata, r.document) for r in filtered]

    def _rrf_fusion(
        self,
        vector_results: List[QueryResult],
        bm25_results: List[Tuple[str, float]],
    ) -> List[Tuple[str, float, dict, str]]:
        """使用 RRF（Reciprocal Rank Fusion）融合两路检索结果

        公式: rrf_score(d) = Σ 1 / (k + rank_i(d))
        其中 k=60（标准值），rank 从 1 开始。

        Args:
            vector_results: 向量检索结果
            bm25_results: BM25 检索结果 (chunk_id, score)

        Returns:
            融合后的 (chunk_id, rrf_score, metadata, document) 元组列表，按 rrf_score 降序
        """
        # 构建 chunk_id → 信息 的映射
        chunk_info: Dict[str, dict] = {}
        rrf_scores: Dict[str, float] = {}

        # 向量检索结果 — 按 score 降序排序后计算排名
        sorted_vector = sorted(vector_results, key=lambda x: x.score, reverse=True)
        for rank, result in enumerate(sorted_vector, start=1):
            cid = result.id
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (_RRF_K + rank)
            if cid not in chunk_info:
                chunk_info[cid] = {
                    "metadata": result.metadata,
                    "document": result.document,
                }

        # BM25 检索结果 — 已按 score 降序排列
        for rank, (cid, _bm25_score) in enumerate(bm25_results, start=1):
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (_RRF_K + rank)
            # BM25 结果可能没有 metadata 和 document，需要从向量结果中补充
            # 如果两路都没有，后续会从向量数据库中查询补充

        # 构建融合结果
        fused: List[Tuple[str, float, dict, str]] = []
        # 收集 BM25 独占命中但缺少 metadata/document 的 chunk_id
        missing_ids = [
            cid for cid in rrf_scores
            if cid not in chunk_info
        ]

        # 从向量数据库中补充缺失的 chunk 信息
        if missing_ids:
            try:
                supplement_results = self.vector_store.get_documents_by_ids(missing_ids)
                if supplement_results:
                    for mid, mdoc, mmeta in supplement_results:
                        chunk_info[mid] = {
                            "metadata": mmeta or {},
                            "document": mdoc or "",
                        }
            except Exception as e:
                logger.debug("BM25 独占命中 chunk 信息补充失败: %s", str(e))
        for cid, score in rrf_scores.items():
            info = chunk_info.get(cid, {"metadata": {}, "document": ""})
            fused.append((cid, score, info["metadata"], info["document"]))

        # 按 RRF 分数降序排列
        fused.sort(key=lambda x: x[1], reverse=True)
        return fused

    def _aggregate_to_parents(
        self,
        child_tuples: List[Tuple[str, float, dict, str]],
        top_k: int,
    ) -> List[QueryResult]:
        """将 child chunk 结果聚合到 parent chunk

        同一 parent 下多个 child 命中时去重，聚合公式:
        parent_score = max(child_scores) + α * (hit_count - 1) / total_children

        Args:
            child_tuples: (chunk_id, score, metadata, document) 列表
            top_k: 最终返回的 parent 数量

        Returns:
            按 parent_score 降序排列的 QueryResult 列表
        """
        # parent_chunk_id → 聚合信息
        parent_agg: Dict[str, dict] = {}

        for cid, score, metadata, _document in child_tuples:
            parent_id = metadata.get("parent_chunk_id", cid)

            if parent_id not in parent_agg:
                parent_agg[parent_id] = {
                    "max_score": score,
                    "hit_count": 1,
                    "child_ids": [cid],
                }
            else:
                agg = parent_agg[parent_id]
                agg["max_score"] = max(agg["max_score"], score)
                agg["hit_count"] += 1
                agg["child_ids"].append(cid)

        # 计算每个 parent 的最终分数
        parent_scores: List[Tuple[str, float]] = []
        for parent_id, agg in parent_agg.items():
            total_children = self.parent_store.get_children_count(parent_id)
            if total_children <= 0:
                total_children = 1  # 避免除零

            parent_score = agg["max_score"] + _MULTI_HIT_ALPHA * (
                agg["hit_count"] - 1
            ) / total_children

            parent_scores.append((parent_id, parent_score))

        # 按 parent_score 降序排列
        parent_scores.sort(key=lambda x: x[1], reverse=True)

        # 取 top_k 个 parent，构建 QueryResult
        results: List[QueryResult] = []
        for parent_id, parent_score in parent_scores[:top_k]:
            parent_data = self.parent_store.get_parent(parent_id)
            if parent_data is not None:
                results.append(
                    QueryResult(
                        document=parent_data["content"],
                        score=parent_score,
                        metadata=parent_data["metadata"],
                        id=parent_id,
                    )
                )
            else:
                # parent 不存在（不应发生），降级返回第一个命中的 child
                logger.warning(
                    "Parent chunk 未找到: %s, 降级返回 child", parent_id
                )
                agg = parent_agg[parent_id]
                first_child_id = agg["child_ids"][0]
                # 从 child_tuples 中找到对应的 child
                for cid, _s, meta, doc in child_tuples:
                    if cid == first_child_id:
                        results.append(
                            QueryResult(
                                document=doc,
                                score=parent_score,
                                metadata=meta,
                                id=cid,
                            )
                        )
                        break

        logger.debug(
            "Parent 聚合结果: child 候选=%d, 去重后 parent=%d, 返回=%d",
            len(child_tuples),
            len(parent_agg),
            len(results),
        )
        return results

    def _child_tuples_to_results(
        self,
        child_tuples: List[Tuple[str, float, dict, str]],
        top_k: int,
    ) -> List[QueryResult]:
        """将 child tuple 直接转换为 QueryResult（无 parent_store 时的降级路径）

        Args:
            child_tuples: (chunk_id, score, metadata, document) 列表
            top_k: 返回数量

        Returns:
            QueryResult 列表
        """
        results = []
        for cid, score, metadata, document in child_tuples[:top_k]:
            results.append(
                QueryResult(
                    document=document,
                    score=score,
                    metadata=metadata,
                    id=cid,
                )
            )
        return results