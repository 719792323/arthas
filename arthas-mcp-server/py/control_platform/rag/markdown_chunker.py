"""
Markdown 文档解析器（Parent-Child 双层索引版）

按 Markdown 标题层级（#/##/###）切分文档为双层知识片段：
- Child chunk：最细粒度的标题段落，用于向量检索（embedding 语义集中）
- Parent chunk：child 所属的更大段落，用于返回给 LLM（上下文完整）

核心原则：用小 chunk 检索，返回大 chunk 回答。
"""

import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from control_platform.config import settings
from control_platform.decision.context_management.token_counter import TokenCounter
from control_platform.rag.base_chunker import BaseChunker, DocumentChunk

logger = logging.getLogger(__name__)


@dataclass
class _HeadingNode:
    """标题层级树的节点
    
    Attributes:
        level: 标题级别（1~6）
        title: 标题文本（不含 # 前缀）
        heading_line: 标题的原始行（含 # 前缀）
        body_lines: 该标题下的正文行（不含子标题的内容）
        children: 子标题节点列表
        parent: 父节点引用
    """
    level: int
    title: str
    heading_line: str
    body_lines: List[str] = field(default_factory=list)
    children: List["_HeadingNode"] = field(default_factory=list)
    parent: Optional["_HeadingNode"] = None

    def full_text(self) -> str:
        """递归获取该节点及所有子节点的完整文本"""
        parts = []
        if self.heading_line:
            parts.append(self.heading_line)
        parts.extend(self.body_lines)
        for child in self.children:
            parts.append(child.full_text())
        return "\n".join(parts)

    def own_text(self) -> str:
        """仅获取该节点自身的文本（标题行 + 正文行，不含子标题）"""
        parts = []
        if self.heading_line:
            parts.append(self.heading_line)
        parts.extend(self.body_lines)
        return "\n".join(parts)

    def heading_path(self) -> str:
        """获取从根到当前节点的标题路径，如 'CPU排查 > 排查步骤 > Step1'"""
        path_parts = []
        node = self
        while node and node.level > 0:
            path_parts.append(node.title)
            node = node.parent
        path_parts.reverse()
        return " > ".join(path_parts)

    def ancestor_heading_lines(self) -> List[str]:
        """获取所有祖先标题行（从根到当前节点的上一级），用于注入 child chunk 前缀"""
        lines = []
        node = self.parent
        while node and node.level > 0:
            lines.append(node.heading_line)
            node = node.parent
        lines.reverse()
        return lines


class MarkdownChunker(BaseChunker):
    """Markdown 文档解析器（Parent-Child 双层索引版）
    
    将 Markdown 文档按标题层级切分为双层知识片段，支持 .md 和 .markdown 文件。
    
    Attributes:
        max_parent_size: Parent chunk 动态边界的 token 上限
    """

    def __init__(
        self,
        max_chunk_size: int = 512,
        min_chunk_size: int = 32,
        overlap_size: int = 128,
        max_parent_size: Optional[int] = None,
        token_counter: Optional[TokenCounter] = None,
    ):
        """初始化 MarkdownChunker
        
        Args:
            max_chunk_size: child chunk 最大 token 数（默认 512），超过则二级切分
            min_chunk_size: child chunk 最小 token 数（默认 32），低于则跳过不索引
            overlap_size: 二级切分时相邻子 chunk 的重叠 token 数（默认 128）
            max_parent_size: parent chunk 动态边界的 token 上限，默认使用配置项
            token_counter: TokenCounter 实例，为 None 时自动创建
        """
        super().__init__(
            max_chunk_size=max_chunk_size,
            min_chunk_size=min_chunk_size,
            overlap_size=overlap_size,
            token_counter=token_counter,
        )
        self.max_parent_size = max_parent_size or settings.rag_max_parent_size

    def supported_extensions(self) -> List[str]:
        """返回支持的文件扩展名"""
        return [".md", ".markdown"]

    def chunk(
        self,
        file_path: str,
        metadata: Optional[dict] = None,
    ) -> List[DocumentChunk]:
        """将 Markdown 文件按标题层级切分为双层知识片段
        
        返回同时包含 parent chunk 和 child chunk（通过 metadata["chunk_level"] 区分）。
        
        Args:
            file_path: Markdown 文件的完整路径
            metadata: 可选的额外元数据
            
        Returns:
            DocumentChunk 列表（含 parent 和 child 两种层级）
        """
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.warning("读取 Markdown 文件失败: %s, 错误: %s", file_path, str(e))
            return []

        if not content.strip():
            return []

        # 计算文件 hash 用于生成 chunk_id
        file_hash = hashlib.md5(content.encode("utf-8")).hexdigest()[:12]
        file_name = os.path.basename(file_path)
        base_metadata = {
            "source_file": file_path,
            "file_name": file_name,
            "file_type": "markdown",
        }
        if metadata:
            base_metadata.update(metadata)

        # 1. 解析标题层级树
        root = self._build_heading_tree(content)

        # 2. 收集所有叶子标题段落（最细粒度），作为 child chunk 候选
        leaf_nodes = self._collect_leaf_nodes(root)

        # 3. 如果文档没有任何标题，整篇作为唯一的 child=parent
        if not leaf_nodes:
            return self._handle_no_heading_document(content, file_hash, base_metadata)

        # 4. 为每个 leaf node 确定 parent chunk 边界，并生成双层 chunk
        return self._generate_parent_child_chunks(
            leaf_nodes, file_hash, base_metadata
        )

    # ==================== 标题树构建 ====================

    def _build_heading_tree(self, content: str) -> _HeadingNode:
        """将 Markdown 内容解析为标题层级树
        
        Args:
            content: Markdown 文本内容
            
        Returns:
            虚拟根节点（level=0），所有顶级标题为其 children
        """
        lines = content.split("\n")
        heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$")
        in_code_block = False

        # 虚拟根节点
        root = _HeadingNode(level=0, title="", heading_line="")
        # 栈：用于追踪当前层级，栈中总是保持 level 递增
        stack: List[_HeadingNode] = [root]

        for line in lines:
            stripped = line.strip()
            # 检测代码块边界
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                stack[-1].body_lines.append(line)
                continue

            if in_code_block:
                stack[-1].body_lines.append(line)
                continue

            match = heading_pattern.match(line)
            if match:
                level = len(match.group(1))
                title = match.group(2).strip()

                new_node = _HeadingNode(
                    level=level,
                    title=title,
                    heading_line=line,
                )

                # 弹出栈中所有 level >= 当前 level 的节点，找到合适的父节点
                while len(stack) > 1 and stack[-1].level >= level:
                    stack.pop()

                parent_node = stack[-1]
                new_node.parent = parent_node
                parent_node.children.append(new_node)
                stack.append(new_node)
            else:
                # 普通行归属于栈顶节点
                stack[-1].body_lines.append(line)

        return root

    def _collect_leaf_nodes(self, root: _HeadingNode) -> List[_HeadingNode]:
        """收集所有叶子标题节点（没有子标题的节点）
        
        对于没有子标题的节点，它同时是 child 和 parent。
        对于有子标题的节点，只收集其叶子子节点。
        
        特殊处理：根节点（level=0）的 body_lines 代表标题前正文，
        如果非空则作为一个虚拟叶子节点收集，确保标题前正文不丢失。
        
        Args:
            root: 标题树的根节点
            
        Returns:
            叶子节点列表（按文档顺序）
        """
        leaves: List[_HeadingNode] = []

        # 特殊处理：收集标题前正文（root.body_lines）
        # root 的 body_lines 包含第一个标题之前的所有内容
        # 仅当文档有真正的标题（root.children 非空）时才需要处理，
        # 纯无标题文档由 _handle_no_heading_document 单独处理
        if root.children and root.body_lines:
            pre_heading_text = "\n".join(root.body_lines).strip()
            if pre_heading_text:
                # 创建虚拟叶子节点来承载标题前正文
                virtual_node = _HeadingNode(
                    level=1,  # 视为一级标题下的内容
                    title="",
                    heading_line="",
                    body_lines=root.body_lines,
                    parent=root,
                )
                leaves.append(virtual_node)

        def _dfs(node: _HeadingNode):
            if node.level > 0 and not node.children:
                # 叶子标题节点
                leaves.append(node)
            for child in node.children:
                _dfs(child)

        _dfs(root)
        return leaves

    # ==================== Parent 边界确定 ====================

    def _find_parent_node(self, leaf: _HeadingNode) -> _HeadingNode:
        """为叶子节点动态确定 parent chunk 的边界节点
        
        算法：从 leaf 所在标题段落开始，沿标题层级向上逐级查找祖先，
        找到第一个总 token 数不超过 max_parent_size 的祖先段落。
        
        边界情况：
        - 如果 leaf 自身就不超过 max_parent_size 且没有更高祖先 → leaf 自身就是 parent
        - 如果所有祖先都超过 max_parent_size → 选择最近一级祖先（leaf 自身）
        
        Args:
            leaf: 叶子标题节点
            
        Returns:
            作为 parent chunk 的标题节点
        """
        # 从 leaf 自身开始向上查找
        candidate = leaf
        node = leaf.parent

        while node and node.level > 0:
            full_text = node.full_text()
            token_count = self.count_tokens(full_text)
            if token_count <= self.max_parent_size:
                candidate = node
                # 继续向上看有没有更大但仍然不超标的祖先
                node = node.parent
            else:
                # 当前祖先超标了，停止
                break

        return candidate

    # ==================== 双层 Chunk 生成 ====================

    def _generate_parent_child_chunks(
        self,
        leaf_nodes: List[_HeadingNode],
        file_hash: str,
        base_metadata: dict,
    ) -> List[DocumentChunk]:
        """为所有叶子节点生成 Parent-Child 双层 chunk
        
        Args:
            leaf_nodes: 叶子标题节点列表
            file_hash: 文件 hash
            base_metadata: 基础元数据
            
        Returns:
            DocumentChunk 列表（含 parent 和 child）
        """
        all_chunks: List[DocumentChunk] = []
        # 跟踪已生成的 parent chunk，避免重复：parent_node_id -> parent_chunk_id
        parent_map: Dict[int, str] = {}
        # child chunk 序号
        chunk_index = 0
        # 滑动窗口 parent 计数器（全局递增，避免与外层 parent id 冲突）
        sliding_window_parent_counter = 0

        # 第一遍：为每个 leaf 确定 parent node
        leaf_parent_pairs: List[Tuple[_HeadingNode, _HeadingNode]] = []
        for leaf in leaf_nodes:
            parent_node = self._find_parent_node(leaf)
            leaf_parent_pairs.append((leaf, parent_node))

        # 第二遍：先过滤极短 chunk，统计每个 parent 的实际 child 数，再生成 chunk
        actual_children_count: Dict[int, int] = {}
        # 先过滤掉极短 chunk，以便正确统计实际 child 数
        valid_leaf_parent_pairs = []
        for leaf, parent_node in leaf_parent_pairs:
            body_only = "\n".join(leaf.body_lines).strip()
            body_tokens = self.count_tokens(body_only)
            if body_tokens < self.min_chunk_size:
                logger.debug(
                    "跳过极短 child chunk（%d tokens < %d）: heading_path=%s",
                    body_tokens,
                    self.min_chunk_size,
                    leaf.heading_path(),
                )
                continue
            valid_leaf_parent_pairs.append((leaf, parent_node))
            pid = id(parent_node)
            actual_children_count[pid] = actual_children_count.get(pid, 0) + 1

        for leaf, parent_node in valid_leaf_parent_pairs:
            pid = id(parent_node)

            # === 生成 Parent Chunk（如果尚未生成）===
            if pid not in parent_map:
                parent_chunk_id = f"{file_hash}_p{len(parent_map)}"
                parent_map[pid] = parent_chunk_id

                parent_content = parent_node.full_text().strip()
                # parent 的祖先标题前缀
                parent_ancestor_lines = parent_node.ancestor_heading_lines()
                if parent_ancestor_lines:
                    parent_content = "\n".join(parent_ancestor_lines) + "\n" + parent_content

                parent_meta = dict(base_metadata)
                parent_meta["chunk_level"] = "parent"
                parent_meta["heading_path"] = parent_node.heading_path()
                parent_meta["total_children"] = actual_children_count.get(pid, 0)

                all_chunks.append(DocumentChunk(
                    content=parent_content,
                    metadata=parent_meta,
                    chunk_id=parent_chunk_id,
                ))

            parent_chunk_id = parent_map[pid]

            # === 生成 Child Chunk ===
            # child 内容 = 祖先标题前缀 + 自身文本
            ancestor_lines = leaf.ancestor_heading_lines()
            own_text = leaf.own_text().strip()

            # 构建带前缀的完整 child 内容
            if ancestor_lines:
                child_content = "\n".join(ancestor_lines) + "\n" + own_text
            else:
                child_content = own_text

            actual_parent_id = parent_chunk_id

            # 检查是否需要二级切分
            child_tokens = self.count_tokens(child_content)
            if child_tokens > self.max_chunk_size:
                # 超长 child chunk 二级切分
                sub_chunks = self._split_long_child(
                    child_content=child_content,
                    ancestor_lines=ancestor_lines,
                    file_hash=file_hash,
                    chunk_index=chunk_index,
                    parent_chunk_id=actual_parent_id,
                    heading_path=leaf.heading_path(),
                    base_metadata=base_metadata,
                )

                # 检查 leaf=parent 且 parent 超过 max_parent_size 的情况
                # 此时需要使用滑动窗口 parent 替代超大的整体 parent
                if id(leaf) == pid:
                    parent_text = parent_node.full_text().strip()
                    parent_tokens = self.count_tokens(parent_text)
                    if parent_tokens > self.max_parent_size:
                        logger.debug(
                            "检测到超大 leaf=parent（%d tokens > %d），启用滑动窗口 parent: %s",
                            parent_tokens,
                            self.max_parent_size,
                            leaf.heading_path(),
                        )
                        # 移除已经生成的超大 parent chunk
                        all_chunks = [
                            c for c in all_chunks
                            if c.chunk_id != actual_parent_id
                        ]
                        # 同时从 parent_map 中移除，以保持一致性
                        del parent_map[pid]
                        # 使用滑动窗口 parent 替代
                        # 用 'sw{counter}' 前缀避免与外层普通 parent id 冲突
                        window_chunks = self._assign_sliding_window_parents(
                            sub_children=sub_chunks,
                            full_text=child_content,
                            file_hash=file_hash,
                            heading_path=leaf.heading_path(),
                            base_metadata=base_metadata,
                            parent_id_prefix=f"sw{sliding_window_parent_counter}",
                        )
                        sliding_window_parent_counter += 1
                        all_chunks.extend(window_chunks)
                        chunk_index += 1
                        continue

                all_chunks.extend(sub_chunks)
                chunk_index += 1
            else:
                child_meta = dict(base_metadata)
                child_meta["chunk_level"] = "child"
                child_meta["parent_chunk_id"] = actual_parent_id
                child_meta["heading_path"] = leaf.heading_path()

                all_chunks.append(DocumentChunk(
                    content=child_content,
                    metadata=child_meta,
                    chunk_id=f"{file_hash}_{chunk_index}",
                ))
                chunk_index += 1

        return all_chunks

    # ==================== 超长 Child 二级切分 ====================

    def _split_long_child(
        self,
        child_content: str,
        ancestor_lines: List[str],
        file_hash: str,
        chunk_index: int,
        parent_chunk_id: str,
        heading_path: str,
        base_metadata: dict,
    ) -> List[DocumentChunk]:
        """将超长 child chunk 按句子/段落边界拆分为多个子 child chunk
        
        相邻子 chunk 之间保留 overlap_size 的重叠内容，
        每个子 chunk 都保留完整的祖先标题前缀。
        
        Args:
            child_content: 带标题前缀的完整 child 内容
            ancestor_lines: 祖先标题行列表
            file_hash: 文件 hash
            chunk_index: 当前 chunk 序号
            parent_chunk_id: 所属 parent chunk 的 ID
            heading_path: 标题路径
            base_metadata: 基础元数据
            
        Returns:
            子 child chunk 列表
        """
        # 标题前缀部分（每个子 chunk 都需要保留）
        prefix = "\n".join(ancestor_lines) + "\n" if ancestor_lines else ""
        prefix_tokens = self.count_tokens(prefix) if prefix else 0

        # 可用于正文的 token 预算
        available_tokens = self.max_chunk_size - prefix_tokens
        if available_tokens < 64:
            # 如果标题前缀本身就占了大部分预算，至少保留 64 tokens 给正文
            available_tokens = 64

        # overlap 不能超过 available_tokens 的一半，否则回退后直接满了会导致死循环
        effective_overlap = min(self.overlap_size, available_tokens // 2)

        # 去掉前缀，获取纯正文部分
        if prefix and child_content.startswith(prefix):
            body_text = child_content[len(prefix):]
        else:
            body_text = child_content

        # 按句子/段落边界拆分正文
        sentences = self._split_into_sentences(body_text)

        sub_chunks: List[DocumentChunk] = []
        sub_index = 0
        current_sentences: List[str] = []
        current_tokens = 0

        i = 0
        while i < len(sentences):
            sent = sentences[i]
            sent_tokens = self.count_tokens(sent)

            if current_tokens + sent_tokens <= available_tokens:
                current_sentences.append(sent)
                current_tokens += sent_tokens
                i += 1
            else:
                # 当前已积累的句子足够一个子 chunk
                if current_sentences:
                    sub_content = prefix + "\n".join(current_sentences)
                    sub_meta = dict(base_metadata)
                    sub_meta["chunk_level"] = "child"
                    sub_meta["parent_chunk_id"] = parent_chunk_id
                    sub_meta["heading_path"] = heading_path

                    sub_chunks.append(DocumentChunk(
                        content=sub_content.strip(),
                        metadata=sub_meta,
                        chunk_id=f"{file_hash}_{chunk_index}_{sub_index}",
                    ))
                    sub_index += 1

                    # 计算 overlap：回退若干句子，使重叠部分约为 effective_overlap tokens
                    overlap_tokens = 0
                    overlap_start = len(current_sentences)
                    while overlap_start > 0 and overlap_tokens < effective_overlap:
                        overlap_start -= 1
                        overlap_tokens += self.count_tokens(current_sentences[overlap_start])

                    # 用重叠部分的句子作为下一个子 chunk 的开头
                    current_sentences = list(current_sentences[overlap_start:])
                    current_tokens = sum(self.count_tokens(s) for s in current_sentences)

                    # 安全检查：如果 overlap 回退后，加上下一个待处理的句子
                    # 仍然超过 available_tokens，说明 overlap 导致循环无法前进，
                    # 必须清空 overlap 以避免死循环
                    if current_tokens >= available_tokens or (
                        current_tokens + sent_tokens > available_tokens
                    ):
                        current_sentences = []
                        current_tokens = 0
                else:
                    # 单个句子就超过了 available_tokens，强制放入
                    sub_content = prefix + sent
                    sub_meta = dict(base_metadata)
                    sub_meta["chunk_level"] = "child"
                    sub_meta["parent_chunk_id"] = parent_chunk_id
                    sub_meta["heading_path"] = heading_path

                    sub_chunks.append(DocumentChunk(
                        content=sub_content.strip(),
                        metadata=sub_meta,
                        chunk_id=f"{file_hash}_{chunk_index}_{sub_index}",
                    ))
                    sub_index += 1
                    current_sentences = []
                    current_tokens = 0
                    i += 1

        # 处理剩余句子
        if current_sentences:
            sub_content = prefix + "\n".join(current_sentences)
            sub_meta = dict(base_metadata)
            sub_meta["chunk_level"] = "child"
            sub_meta["parent_chunk_id"] = parent_chunk_id
            sub_meta["heading_path"] = heading_path

            sub_chunks.append(DocumentChunk(
                content=sub_content.strip(),
                metadata=sub_meta,
                chunk_id=f"{file_hash}_{chunk_index}_{sub_index}",
            ))

        return sub_chunks

    def _split_into_sentences(self, text: str) -> List[str]:
        """将文本按段落和句子边界拆分
        
        优先按空行（段落）拆分，段落内按中英文句号拆分。
        
        Args:
            text: 待拆分的文本
            
        Returns:
            句子/段落列表
        """
        # 先按空行拆分为段落
        paragraphs = re.split(r"\n\s*\n", text)
        result: List[str] = []

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            # 如果段落较短（不超过 max_chunk_size），整段作为一个单元
            if self.count_tokens(para) <= self.max_chunk_size:
                result.append(para)
            else:
                # 按句子边界拆分（中英文句号、问号、感叹号）
                sentences = re.split(r"(?<=[。！？.!?])\s*", para)
                for sent in sentences:
                    sent = sent.strip()
                    if sent:
                        result.append(sent)

        return result

    # ==================== 滑动窗口 Parent ====================

    def _assign_sliding_window_parents(
        self,
        sub_children: List[DocumentChunk],
        full_text: str,
        file_hash: str,
        heading_path: str,
        base_metadata: dict,
        parent_id_prefix: str = "sw",
    ) -> List[DocumentChunk]:
        """为超大文本的子 child chunk 分配滑动窗口局部 parent
        
        当 leaf=parent 且内容超过 max_parent_size 时，不再让所有 child 指向同一个
        超大 parent，而是为每个 child 生成一个以该 child 为中心、向前后扩展到
        max_parent_size 的局部 parent chunk。
        
        算法：
        1. 将所有子 child 的纯正文内容按句子列表排列
        2. 对每个子 child，以其句子范围为中心，向前后扩展直到总 token 达到 max_parent_size
        3. 相同覆盖范围的 child 共享同一个 parent chunk（去重）
        
        Args:
            sub_children: 已经二级切分好的子 child chunk 列表（parent_chunk_id 为占位符）
            full_text: 完整的原始文本
            file_hash: 文件 hash
            heading_path: 标题路径
            base_metadata: 基础元数据
            
        Returns:
            包含滑动窗口 parent 和更新后的 child 的 DocumentChunk 列表
        """
        if not sub_children:
            return []

        # 将全文按句子/段落拆分，用于构建滑动窗口
        sentences = self._split_into_sentences(full_text)
        if not sentences:
            return list(sub_children)

        # 计算每个句子的 token 数和累计 token 数
        sent_tokens = [self.count_tokens(s) for s in sentences]
        total_sents = len(sentences)

        # 为每个子 child 确定其在句子列表中的大致位置（中心句子索引）
        # 策略：按子 child 在列表中的顺序，均匀分配到句子列表上
        n_children = len(sub_children)
        child_center_indices = []
        for idx in range(n_children):
            # 每个 child 大约覆盖的句子中心位置
            center = int((idx + 0.5) * total_sents / n_children)
            center = min(center, total_sents - 1)
            child_center_indices.append(center)

        # 为每个子 child 计算滑动窗口的 [start, end) 范围
        window_ranges: List[Tuple[int, int]] = []
        for center in child_center_indices:
            # 从 center 向两侧扩展，直到总 token 达到 max_parent_size
            left = center
            right = center + 1
            current_tokens = sent_tokens[center]

            while current_tokens < self.max_parent_size:
                expanded = False
                # 优先向后扩展
                if right < total_sents:
                    if current_tokens + sent_tokens[right] <= self.max_parent_size:
                        current_tokens += sent_tokens[right]
                        right += 1
                        expanded = True
                # 再向前扩展
                if left > 0:
                    if current_tokens + sent_tokens[left - 1] <= self.max_parent_size:
                        current_tokens += sent_tokens[left - 1]
                        left -= 1
                        expanded = True
                if not expanded:
                    break

            window_ranges.append((left, right))

        # 去重：相同窗口范围的 child 共享同一个 parent
        # range_tuple → parent_chunk_id
        range_to_parent_id: Dict[Tuple[int, int], str] = {}
        all_chunks: List[DocumentChunk] = []
        parent_index = 0

        for i, (child_chunk, window_range) in enumerate(zip(sub_children, window_ranges)):
            if window_range not in range_to_parent_id:
                # 生成新的滑动窗口 parent chunk
                parent_chunk_id = f"{file_hash}_{parent_id_prefix}_p{parent_index}"
                range_to_parent_id[window_range] = parent_chunk_id
                parent_index += 1

                start, end = window_range
                parent_content = "\n".join(sentences[start:end]).strip()

                # 统计有多少个 child 共享这个 parent
                shared_count = sum(
                    1 for wr in window_ranges if wr == window_range
                )

                parent_meta = dict(base_metadata)
                parent_meta["chunk_level"] = "parent"
                parent_meta["heading_path"] = heading_path
                parent_meta["total_children"] = shared_count
                parent_meta["window_range"] = f"{start}-{end}"

                all_chunks.append(DocumentChunk(
                    content=parent_content,
                    metadata=parent_meta,
                    chunk_id=parent_chunk_id,
                ))

            # 更新子 child 的 parent_chunk_id
            parent_chunk_id = range_to_parent_id[window_range]
            child_chunk.metadata["parent_chunk_id"] = parent_chunk_id
            all_chunks.append(child_chunk)

        logger.debug(
            "滑动窗口 Parent 分配完成: 子 child 数=%d, 去重 parent 数=%d",
            len(sub_children),
            len(range_to_parent_id),
        )

        return all_chunks

    # ==================== 无标题文档处理 ====================

    def _handle_no_heading_document(
        self,
        content: str,
        file_hash: str,
        base_metadata: dict,
    ) -> List[DocumentChunk]:
        """处理没有任何标题的文档
        
        如果内容不超过 max_chunk_size，整篇作为唯一的 child=parent。
        如果内容超过 max_chunk_size，进行二级切分生成多个 child chunk。
        如果内容超过 max_parent_size，为每个子 child 生成滑动窗口局部 parent。
        
        Args:
            content: 文档全文
            file_hash: 文件 hash
            base_metadata: 基础元数据
            
        Returns:
            DocumentChunk 列表
        """
        content = content.strip()
        content_tokens = self.count_tokens(content)

        # 内容不超过 max_chunk_size，整篇作为唯一的 child=parent（原有逻辑）
        if content_tokens <= self.max_chunk_size:
            parent_chunk_id = f"{file_hash}_p0"

            parent_meta = dict(base_metadata)
            parent_meta["chunk_level"] = "parent"
            parent_meta["heading_path"] = ""
            parent_meta["total_children"] = 1

            parent_chunk = DocumentChunk(
                content=content,
                metadata=parent_meta,
                chunk_id=parent_chunk_id,
            )

            child_meta = dict(base_metadata)
            child_meta["chunk_level"] = "child"
            child_meta["parent_chunk_id"] = parent_chunk_id
            child_meta["heading_path"] = ""

            child_chunk = DocumentChunk(
                content=content,
                metadata=child_meta,
                chunk_id=f"{file_hash}_0",
            )

            return [parent_chunk, child_chunk]

        # 内容超过 max_chunk_size，需要二级切分
        all_chunks: List[DocumentChunk] = []

        # 判断是否需要滑动窗口 parent（内容超过 max_parent_size）
        if content_tokens > self.max_parent_size:
            # 超大文档：为每组子 child 生成滑动窗口局部 parent
            sub_children = self._split_long_child(
                child_content=content,
                ancestor_lines=[],
                file_hash=file_hash,
                chunk_index=0,
                parent_chunk_id="__placeholder__",  # 临时占位，后续替换
                heading_path="",
                base_metadata=base_metadata,
            )
            all_chunks.extend(
                self._assign_sliding_window_parents(
                    sub_children=sub_children,
                    full_text=content,
                    file_hash=file_hash,
                    heading_path="",
                    base_metadata=base_metadata,
                )
            )
        else:
            # 中等文档：整篇作为 parent，child 二级切分
            parent_chunk_id = f"{file_hash}_p0"

            parent_meta = dict(base_metadata)
            parent_meta["chunk_level"] = "parent"
            parent_meta["heading_path"] = ""

            # 先生成子 child chunk
            sub_children = self._split_long_child(
                child_content=content,
                ancestor_lines=[],
                file_hash=file_hash,
                chunk_index=0,
                parent_chunk_id=parent_chunk_id,
                heading_path="",
                base_metadata=base_metadata,
            )

            parent_meta["total_children"] = len(sub_children)

            parent_chunk = DocumentChunk(
                content=content,
                metadata=parent_meta,
                chunk_id=parent_chunk_id,
            )

            all_chunks.append(parent_chunk)
            all_chunks.extend(sub_children)

        return all_chunks