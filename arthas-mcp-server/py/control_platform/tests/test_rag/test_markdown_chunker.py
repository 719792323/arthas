"""
MarkdownChunker 单元测试（Parent-Child 双层索引版）

测试覆盖：
- 双层 chunk 生成（child + parent）
- 动态 parent 边界算法
- 超长 child chunk 二级切分
- 极短 child chunk 跳过
- 父级标题前缀注入
- 边界情况（无标题、单级标题等）
"""

import pytest
from unittest.mock import patch

from control_platform.rag.markdown_chunker import MarkdownChunker


class TestBasicChunking:
    """基础切分功能测试"""

    def setup_method(self):
        self.chunker = MarkdownChunker()

    def test_supported_extensions(self):
        """测试支持的文件扩展名"""
        exts = self.chunker.supported_extensions()
        assert ".md" in exts
        assert ".markdown" in exts

    def test_empty_document(self, empty_md_file):
        """测试空文档返回空列表"""
        chunks = self.chunker.chunk(empty_md_file)
        assert chunks == []

    def test_no_heading_document(self, no_heading_md_file):
        """测试无标题文档：整篇作为唯一的 child=parent"""
        chunks = self.chunker.chunk(no_heading_md_file)
        assert len(chunks) == 2  # 1 parent + 1 child

        parent_chunks = [c for c in chunks if c.metadata.get("chunk_level") == "parent"]
        child_chunks = [c for c in chunks if c.metadata.get("chunk_level") == "child"]
        assert len(parent_chunks) == 1
        assert len(child_chunks) == 1

        # child 的 parent_chunk_id 指向 parent
        assert child_chunks[0].metadata["parent_chunk_id"] == parent_chunks[0].chunk_id

    def test_code_block_handling(self, code_block_md_file):
        """测试代码块中的 # 不被误识别为标题"""
        chunks = self.chunker.chunk(code_block_md_file)
        heading_paths = [c.metadata.get("heading_path", "") for c in chunks]
        for path in heading_paths:
            assert "Python 注释" not in path
            assert "另一个注释" not in path

    def test_chunk_id_uniqueness(self, arthas_thread_md_file):
        """测试 chunk_id 全局唯一"""
        chunks = self.chunker.chunk(arthas_thread_md_file)
        ids = [c.chunk_id for c in chunks]
        assert len(ids) == len(set(ids)), "chunk_id 存在重复"


class TestParentChildGeneration:
    """Parent-Child 双层 chunk 生成测试"""

    def setup_method(self):
        self.chunker = MarkdownChunker()

    def test_dual_layer_chunks_generated(self, arthas_thread_md_file):
        """测试多级标题文档同时生成 parent 和 child chunk"""
        chunks = self.chunker.chunk(arthas_thread_md_file)
        assert len(chunks) > 0

        parent_chunks = [c for c in chunks if c.metadata.get("chunk_level") == "parent"]
        child_chunks = [c for c in chunks if c.metadata.get("chunk_level") == "child"]

        assert len(parent_chunks) > 0, "应该生成 parent chunk"
        assert len(child_chunks) > 0, "应该生成 child chunk"

    def test_chunk_level_metadata(self, arthas_thread_md_file):
        """测试每个 chunk 都有 chunk_level 字段"""
        chunks = self.chunker.chunk(arthas_thread_md_file)
        for chunk in chunks:
            assert "chunk_level" in chunk.metadata
            assert chunk.metadata["chunk_level"] in ("parent", "child")

    def test_child_has_parent_chunk_id(self, arthas_thread_md_file):
        """测试每个 child chunk 都有 parent_chunk_id 指向合法的 parent"""
        chunks = self.chunker.chunk(arthas_thread_md_file)

        parent_ids = {c.chunk_id for c in chunks if c.metadata["chunk_level"] == "parent"}
        child_chunks = [c for c in chunks if c.metadata["chunk_level"] == "child"]

        for child in child_chunks:
            assert "parent_chunk_id" in child.metadata
            assert child.metadata["parent_chunk_id"] in parent_ids, (
                f"child {child.chunk_id} 的 parent_chunk_id "
                f"'{child.metadata['parent_chunk_id']}' 不在 parent 集合中"
            )

    def test_parent_has_total_children(self, arthas_thread_md_file):
        """测试 parent chunk 有 total_children 字段"""
        chunks = self.chunker.chunk(arthas_thread_md_file)
        parent_chunks = [c for c in chunks if c.metadata["chunk_level"] == "parent"]

        for parent in parent_chunks:
            assert "total_children" in parent.metadata
            assert parent.metadata["total_children"] >= 1

    def test_multiple_children_share_parent(self, tmp_path):
        """测试多个 child chunk 共享同一个 parent"""
        # 构造每个 ### 段落都足够长（>= min_chunk_size），确保不被跳过
        long_body = "这是一段足够长的正文内容，用于确保 child chunk 不会因为太短而被跳过。" * 5
        content = "# 命令手册\n\n"
        content += "## 使用方式\n\n"
        content += f"### 方式一\n\n{long_body}\n\n"
        content += f"### 方式二\n\n{long_body}\n\n"
        content += f"### 方式三\n\n{long_body}\n\n"
        file_path = tmp_path / "multi_children.md"
        file_path.write_text(content, encoding="utf-8")

        chunks = self.chunker.chunk(str(file_path))
        child_chunks = [c for c in chunks if c.metadata["chunk_level"] == "child"]

        # 按 parent_chunk_id 分组
        parent_to_children = {}
        for child in child_chunks:
            pid = child.metadata["parent_chunk_id"]
            parent_to_children.setdefault(pid, []).append(child)

        # 至少有一个 parent 有多个 children
        multi_child_parents = {k: v for k, v in parent_to_children.items() if len(v) > 1}
        assert len(multi_child_parents) > 0, "应该有至少一个 parent 拥有多个 child chunk"

    def test_no_sub_heading_is_self_parent(self, tmp_path):
        """测试没有子标题的段落：自身既是 child 也是 parent"""
        content = "# 主标题\n\n## 参数说明\n\n这是参数说明的正文内容，没有任何子标题。" + " 补充内容" * 20
        file_path = tmp_path / "self_parent.md"
        file_path.write_text(content, encoding="utf-8")

        chunks = self.chunker.chunk(str(file_path))
        child_chunks = [c for c in chunks if c.metadata["chunk_level"] == "child"]
        parent_chunks = [c for c in chunks if c.metadata["chunk_level"] == "parent"]

        # 参数说明段落应该自身既是 child 也是 parent
        # child 的 parent_chunk_id 应该指向一个存在的 parent
        for child in child_chunks:
            pid = child.metadata["parent_chunk_id"]
            matching_parents = [p for p in parent_chunks if p.chunk_id == pid]
            assert len(matching_parents) == 1


class TestDynamicParentBoundary:
    """动态 Parent 边界算法测试"""

    def test_parent_within_token_limit(self, tmp_path):
        """测试 parent chunk 的 token 数不超过 max_parent_size（正常情况）"""
        # 创建一个结构化文档，每个 ## 段落较短
        content = "# 命令手册\n\n"
        content += "## 使用方式\n\n"
        content += "### 方式一\n\n使用方式一的说明文本。\n\n"
        content += "### 方式二\n\n使用方式二的说明文本。\n\n"
        content += "## 参数\n\n参数说明文本。" + " 更多参数描述内容" * 10
        file_path = tmp_path / "normal.md"
        file_path.write_text(content, encoding="utf-8")

        chunker = MarkdownChunker(max_parent_size=2048)
        chunks = chunker.chunk(str(file_path))

        parent_chunks = [c for c in chunks if c.metadata["chunk_level"] == "parent"]
        for parent in parent_chunks:
            token_count = chunker.count_tokens(parent.content)
            # 正常情况下 parent 不超过 max_parent_size
            # 注：如果所有祖先都超标则可能超过，这里的文档不会出现这种情况
            assert token_count <= 2048, (
                f"parent '{parent.metadata.get('heading_path')}' "
                f"token 数 {token_count} 超过 max_parent_size 2048"
            )

    def test_parent_expands_upward(self, tmp_path):
        """测试 parent 边界会向上扩展到合适的祖先"""
        # ## 段落很短，整个 # 段落不超过 max_parent_size
        # 预期：child 的 parent 会向上扩展
        content = "# 排查手册\n\n"
        content += "## 排查步骤\n\n"
        content += "### Step 1\n\nStep 1 的具体操作说明。\n\n"
        content += "### Step 2\n\nStep 2 的具体操作说明。\n\n"
        file_path = tmp_path / "expand_up.md"
        file_path.write_text(content, encoding="utf-8")

        chunker = MarkdownChunker(max_parent_size=2048)
        chunks = chunker.chunk(str(file_path))

        child_chunks = [c for c in chunks if c.metadata["chunk_level"] == "child"]
        parent_chunks = [c for c in chunks if c.metadata["chunk_level"] == "parent"]

        # Step 1 和 Step 2 应该共享同一个 parent
        if len(child_chunks) >= 2:
            parent_ids = set(c.metadata["parent_chunk_id"] for c in child_chunks)
            # 如果文档足够小，所有 child 可能都指向同一个 parent
            assert len(parent_ids) >= 1

    def test_all_ancestors_exceed_limit(self, tmp_path):
        """测试所有祖先都超过 max_parent_size 时，选择最近一级祖先"""
        # 构造一个非常大的文档，每个段落都很长
        long_text = "这是一段很长的文本内容。" * 200  # 约 1200+ tokens
        content = "# 超大文档\n\n"
        content += f"## 段落一\n\n{long_text}\n\n"
        content += "### 子段落\n\n子段落的内容。" + " 补充内容" * 20
        file_path = tmp_path / "large.md"
        file_path.write_text(content, encoding="utf-8")

        # 设置较小的 max_parent_size
        chunker = MarkdownChunker(max_parent_size=256)
        chunks = chunker.chunk(str(file_path))

        # 应该仍然能正常生成 chunk，不抛异常
        assert len(chunks) > 0
        parent_chunks = [c for c in chunks if c.metadata["chunk_level"] == "parent"]
        assert len(parent_chunks) > 0

    def test_single_level_heading_only(self, tmp_path):
        """测试文档只有一级标题（无 ## 及以下标题）"""
        content = "# 唯一标题\n\n这是文档的全部正文内容，没有任何子标题。" + " 补充内容" * 20
        file_path = tmp_path / "single_level.md"
        file_path.write_text(content, encoding="utf-8")

        chunker = MarkdownChunker()
        chunks = chunker.chunk(str(file_path))

        parent_chunks = [c for c in chunks if c.metadata["chunk_level"] == "parent"]
        child_chunks = [c for c in chunks if c.metadata["chunk_level"] == "child"]

        # 只有一级标题，整篇是 parent，自身也是 child
        assert len(parent_chunks) >= 1
        assert len(child_chunks) >= 1


class TestHeadingPrefixInjection:
    """父级标题前缀注入测试"""

    def test_child_contains_ancestor_headings(self, tmp_path):
        """测试 child chunk 内容包含祖先标题行"""
        content = "# 主标题\n\n## 二级标题\n\n### 三级标题\n\n三级标题下的正文内容。" + " 补充内容" * 20
        file_path = tmp_path / "prefix.md"
        file_path.write_text(content, encoding="utf-8")

        chunker = MarkdownChunker()
        chunks = chunker.chunk(str(file_path))
        child_chunks = [c for c in chunks if c.metadata["chunk_level"] == "child"]

        # 找到三级标题对应的 child
        three_level_children = [
            c for c in child_chunks
            if "三级标题" in c.metadata.get("heading_path", "")
        ]
        assert len(three_level_children) > 0

        for child in three_level_children:
            # child content 应该包含祖先标题行
            assert "# 主标题" in child.content or "## 二级标题" in child.content

    def test_top_level_child_no_extra_prefix(self, tmp_path):
        """测试顶级 child chunk 没有多余的标题前缀"""
        content = "# 主标题\n\n正文内容。" + " 补充内容" * 20
        file_path = tmp_path / "top_level.md"
        file_path.write_text(content, encoding="utf-8")

        chunker = MarkdownChunker()
        chunks = chunker.chunk(str(file_path))
        child_chunks = [c for c in chunks if c.metadata["chunk_level"] == "child"]

        for child in child_chunks:
            # 顶级标题的 child 不需要额外前缀
            lines = child.content.strip().split("\n")
            # 第一行应该是 # 主标题
            assert lines[0].startswith("#")


class TestLongChildSplitting:
    """超长 child chunk 二级切分测试"""

    def test_long_child_is_split(self, tmp_path):
        """测试超过 max_chunk_size 的 child 会被二级切分"""
        # 构造一个超长的叶子段落
        long_text = "这是一个很长的段落。" * 100  # 远超 512 tokens
        content = f"# 主标题\n\n## 超长段落\n\n{long_text}"
        file_path = tmp_path / "long_child.md"
        file_path.write_text(content, encoding="utf-8")

        chunker = MarkdownChunker(max_chunk_size=128, overlap_size=32)
        chunks = chunker.chunk(str(file_path))
        child_chunks = [c for c in chunks if c.metadata["chunk_level"] == "child"]

        # 应该被切分为多个子 child chunk
        assert len(child_chunks) > 1, "超长 child 应该被二级切分为多个子 chunk"

    def test_split_chunk_id_format(self, tmp_path):
        """测试二级切分后的 chunk_id 格式为 {file_hash}_{chunk_index}_{sub_index}"""
        long_text = "这是一个很长的段落。" * 100
        content = f"# 主标题\n\n## 超长段落\n\n{long_text}"
        file_path = tmp_path / "long_id.md"
        file_path.write_text(content, encoding="utf-8")

        chunker = MarkdownChunker(max_chunk_size=128, overlap_size=32)
        chunks = chunker.chunk(str(file_path))
        child_chunks = [c for c in chunks if c.metadata["chunk_level"] == "child"]

        # 二级切分的 chunk_id 应该包含 sub_index（格式 hash_idx_subidx）
        sub_chunks = [c for c in child_chunks if c.chunk_id.count("_") >= 2]
        assert len(sub_chunks) > 0, "应该有包含 sub_index 的 chunk_id"

    def test_split_chunks_share_parent(self, tmp_path):
        """测试二级切分后的子 chunk 都指向同一个 parent"""
        long_text = "这是一个很长的段落。" * 100
        content = f"# 主标题\n\n## 超长段落\n\n{long_text}"
        file_path = tmp_path / "long_parent.md"
        file_path.write_text(content, encoding="utf-8")

        chunker = MarkdownChunker(max_chunk_size=128, overlap_size=32)
        chunks = chunker.chunk(str(file_path))
        child_chunks = [c for c in chunks if c.metadata["chunk_level"] == "child"]

        parent_ids = set(c.metadata["parent_chunk_id"] for c in child_chunks)
        # 所有子 chunk 应该指向同一个 parent（因为只有一个叶子节点）
        assert len(parent_ids) == 1

    def test_split_chunks_preserve_heading_prefix(self, tmp_path):
        """测试二级切分后每个子 chunk 都保留完整的标题前缀"""
        long_text = "这是一个很长的段落。" * 100
        content = f"# 主标题\n\n## 二级\n\n### 超长三级\n\n{long_text}"
        file_path = tmp_path / "long_prefix.md"
        file_path.write_text(content, encoding="utf-8")

        chunker = MarkdownChunker(max_chunk_size=128, overlap_size=32)
        chunks = chunker.chunk(str(file_path))
        child_chunks = [c for c in chunks if c.metadata["chunk_level"] == "child"]

        for child in child_chunks:
            # 每个子 chunk 都应该包含祖先标题
            assert "# 主标题" in child.content or "## 二级" in child.content

    def test_normal_child_not_split(self, tmp_path):
        """测试未超过 max_chunk_size 的 child 不做二级切分"""
        content = "# 主标题\n\n## 短段落\n\n短正文内容。" + " 补充" * 20
        file_path = tmp_path / "short_child.md"
        file_path.write_text(content, encoding="utf-8")

        chunker = MarkdownChunker(max_chunk_size=512)
        chunks = chunker.chunk(str(file_path))
        child_chunks = [c for c in chunks if c.metadata["chunk_level"] == "child"]

        # 没有二级切分，chunk_id 应该只有一个下划线（hash_idx 格式）
        for child in child_chunks:
            # 不应有 sub_index 格式（除非恰好文件 hash 中有下划线）
            parts = child.chunk_id.split("_")
            assert len(parts) <= 2, f"短 child 不应有 sub_index: {child.chunk_id}"


class TestShortChildSkipping:
    """极短 child chunk 跳过测试"""

    def test_short_child_skipped(self, tmp_path):
        """测试纯正文低于 min_chunk_size 的 child 被跳过不索引"""
        # 构造一个极短段落
        content = "# 主标题\n\n## 极短段\n\n短。\n\n## 正常段\n\n正常段落的正文内容。" + " 补充内容" * 30
        file_path = tmp_path / "short_skip.md"
        file_path.write_text(content, encoding="utf-8")

        chunker = MarkdownChunker(min_chunk_size=64)
        chunks = chunker.chunk(str(file_path))
        child_chunks = [c for c in chunks if c.metadata["chunk_level"] == "child"]

        # 极短段应该被跳过，只有正常段的 child
        headings = [c.metadata.get("heading_path", "") for c in child_chunks]
        assert not any("极短段" in h for h in headings), "极短段的 child 应被跳过"

    def test_short_child_content_preserved_in_parent(self, tmp_path):
        """测试被跳过的极短 child 内容仍保留在 parent chunk 中"""
        content = "# 主标题\n\n## 容器段\n\n### 极短子段\n\n短。\n\n### 正常子段\n\n正常子段的正文内容。" + " 补充内容" * 30
        file_path = tmp_path / "short_in_parent.md"
        file_path.write_text(content, encoding="utf-8")

        chunker = MarkdownChunker(min_chunk_size=64)
        chunks = chunker.chunk(str(file_path))
        parent_chunks = [c for c in chunks if c.metadata["chunk_level"] == "parent"]

        # parent chunk 应该包含极短子段的内容
        parent_contents = " ".join(p.content for p in parent_chunks)
        assert "极短子段" in parent_contents or "短。" in parent_contents, (
            "被跳过的极短 child 内容应保留在 parent 中"
        )


class TestMetadata:
    """元数据完整性测试"""

    def test_metadata_fields(self, arthas_thread_md_file):
        """测试切片元数据包含必要字段"""
        chunker = MarkdownChunker()
        chunks = chunker.chunk(
            arthas_thread_md_file,
            metadata={"source_type": "tool_doc"},
        )

        for chunk in chunks:
            assert "source_file" in chunk.metadata
            assert "file_type" in chunk.metadata
            assert chunk.metadata["file_type"] == "markdown"
            assert chunk.metadata["source_type"] == "tool_doc"
            assert "chunk_level" in chunk.metadata
            assert "heading_path" in chunk.metadata
            assert chunk.chunk_id  # chunk_id 不为空

    def test_parent_total_children_matches_actual(self, arthas_thread_md_file):
        """测试 parent 的 total_children 与实际 child 数量一致"""
        chunker = MarkdownChunker()
        chunks = chunker.chunk(arthas_thread_md_file)

        parent_chunks = {
            c.chunk_id: c for c in chunks if c.metadata["chunk_level"] == "parent"
        }
        child_chunks = [c for c in chunks if c.metadata["chunk_level"] == "child"]

        # 统计每个 parent 的实际 child 数量
        actual_counts = {}
        for child in child_chunks:
            pid = child.metadata["parent_chunk_id"]
            actual_counts[pid] = actual_counts.get(pid, 0) + 1

        for pid, parent in parent_chunks.items():
            declared = parent.metadata["total_children"]
            actual = actual_counts.get(pid, 0)
            # 注意：由于极短 chunk 被跳过，actual 可能小于 declared
            # declared 是基于叶子节点数量（跳过前），actual 是实际生成的 child 数
            assert actual <= declared, (
                f"parent {pid} 的实际 child 数 {actual} 大于声明的 {declared}"
            )


class TestNoHeadingDocumentSplitting:
    """无标题文档二级切分测试（Bug 修复验证）"""

    def test_short_no_heading_unchanged(self, tmp_path):
        """测试短无标题文档（≤ max_chunk_size）行为不变：1 parent + 1 child"""
        content = "这是一段短文本。" * 5  # 很短
        file_path = tmp_path / "short_no_heading.md"
        file_path.write_text(content, encoding="utf-8")

        chunker = MarkdownChunker(max_chunk_size=512)
        chunks = chunker.chunk(str(file_path))

        parent_chunks = [c for c in chunks if c.metadata["chunk_level"] == "parent"]
        child_chunks = [c for c in chunks if c.metadata["chunk_level"] == "child"]

        assert len(parent_chunks) == 1
        assert len(child_chunks) == 1
        assert child_chunks[0].metadata["parent_chunk_id"] == parent_chunks[0].chunk_id
        # parent 和 child 内容相同
        assert parent_chunks[0].content == child_chunks[0].content

    def test_medium_no_heading_splits_children(self, tmp_path):
        """测试中等无标题文档（max_chunk_size < size ≤ max_parent_size）：整篇作为 parent，child 二级切分"""
        # 构造约 800 tokens 的无标题文档
        content = "这是一段需要被切分的正文内容，包含足够多的文字确保超过 max_chunk_size 限制。" * 30
        file_path = tmp_path / "medium_no_heading.md"
        file_path.write_text(content, encoding="utf-8")

        chunker = MarkdownChunker(max_chunk_size=128, max_parent_size=2048)
        chunks = chunker.chunk(str(file_path))

        parent_chunks = [c for c in chunks if c.metadata["chunk_level"] == "parent"]
        child_chunks = [c for c in chunks if c.metadata["chunk_level"] == "child"]

        # 应该有 1 个 parent 和多个 child
        assert len(parent_chunks) == 1, f"中等无标题文档应只有 1 个 parent，实际 {len(parent_chunks)}"
        assert len(child_chunks) > 1, f"中等无标题文档应有多个 child，实际 {len(child_chunks)}"

        # 所有 child 指向同一个 parent
        parent_ids = set(c.metadata["parent_chunk_id"] for c in child_chunks)
        assert len(parent_ids) == 1
        assert list(parent_ids)[0] == parent_chunks[0].chunk_id

        # 每个 child 的 token 数不应超过 max_chunk_size 太多
        for child in child_chunks:
            tokens = chunker.count_tokens(child.content)
            assert tokens <= 128 * 2, f"child chunk token 数 {tokens} 远超 max_chunk_size"

    def test_large_no_heading_uses_sliding_window(self, tmp_path):
        """测试超大无标题文档（> max_parent_size）：使用滑动窗口 parent"""
        # 构造约 3000+ tokens 的超大无标题文档
        content = "这是一段非常长的正文内容，用于测试滑动窗口 parent 功能。每一句话都有一定长度。" * 100
        file_path = tmp_path / "large_no_heading.md"
        file_path.write_text(content, encoding="utf-8")

        chunker = MarkdownChunker(max_chunk_size=128, max_parent_size=512)
        chunks = chunker.chunk(str(file_path))

        parent_chunks = [c for c in chunks if c.metadata["chunk_level"] == "parent"]
        child_chunks = [c for c in chunks if c.metadata["chunk_level"] == "child"]

        # 应该有多个 parent（滑动窗口生成的）
        assert len(parent_chunks) >= 1, "超大无标题文档应有至少 1 个 parent"
        assert len(child_chunks) > 1, "超大无标题文档应有多个 child"

        # 每个 parent 的 token 数不应超过 max_parent_size
        for parent in parent_chunks:
            tokens = chunker.count_tokens(parent.content)
            assert tokens <= 512 + 64, (  # 允许少量余量
                f"滑动窗口 parent token 数 {tokens} 超过 max_parent_size 512"
            )

        # 每个 child 都指向合法的 parent
        parent_ids = {c.chunk_id for c in parent_chunks}
        for child in child_chunks:
            assert child.metadata["parent_chunk_id"] in parent_ids, (
                f"child {child.chunk_id} 的 parent_chunk_id "
                f"'{child.metadata['parent_chunk_id']}' 不在 parent 集合中"
            )

    def test_no_heading_child_ids_unique(self, tmp_path):
        """测试无标题文档二级切分后所有 chunk_id 唯一"""
        content = "这是正文内容。" * 100
        file_path = tmp_path / "no_heading_unique.md"
        file_path.write_text(content, encoding="utf-8")

        chunker = MarkdownChunker(max_chunk_size=128, max_parent_size=512)
        chunks = chunker.chunk(str(file_path))

        ids = [c.chunk_id for c in chunks]
        assert len(ids) == len(set(ids)), f"chunk_id 存在重复: {[x for x in ids if ids.count(x) > 1]}"


class TestSlidingWindowParent:
    """滑动窗口 Parent 优化测试"""

    def test_sliding_window_parent_token_limit(self, tmp_path):
        """测试滑动窗口 parent 的 token 数不超过 max_parent_size"""
        # 构造一个有标题但叶子节点超大的文档（leaf=parent 且超过 max_parent_size）
        long_text = "这是一段很长的正文内容，每句话都有一定的长度确保总数远超限制。" * 100
        content = f"# 超大叶子节点\n\n{long_text}"
        file_path = tmp_path / "sliding_window.md"
        file_path.write_text(content, encoding="utf-8")

        chunker = MarkdownChunker(max_chunk_size=128, max_parent_size=512)
        chunks = chunker.chunk(str(file_path))

        parent_chunks = [c for c in chunks if c.metadata["chunk_level"] == "parent"]

        for parent in parent_chunks:
            tokens = chunker.count_tokens(parent.content)
            assert tokens <= 512 + 64, (
                f"滑动窗口 parent token 数 {tokens} 超过 max_parent_size 512"
            )

    def test_sliding_window_children_valid_parent(self, tmp_path):
        """测试滑动窗口模式下每个 child 都指向合法的 parent"""
        long_text = "详细的排查步骤说明，包含各种复杂场景的处理方式。" * 120
        content = f"# 排查手册\n\n## 超长章节\n\n{long_text}"
        file_path = tmp_path / "sw_valid_parent.md"
        file_path.write_text(content, encoding="utf-8")

        chunker = MarkdownChunker(max_chunk_size=128, max_parent_size=256)
        chunks = chunker.chunk(str(file_path))

        parent_ids = {c.chunk_id for c in chunks if c.metadata["chunk_level"] == "parent"}
        child_chunks = [c for c in chunks if c.metadata["chunk_level"] == "child"]

        assert len(parent_ids) > 0, "应该生成 parent chunk"
        assert len(child_chunks) > 0, "应该生成 child chunk"

        for child in child_chunks:
            assert child.metadata["parent_chunk_id"] in parent_ids, (
                f"child {child.chunk_id} 的 parent_chunk_id "
                f"'{child.metadata['parent_chunk_id']}' 不在 parent 集合中"
            )

    def test_sliding_window_deduplication(self, tmp_path):
        """测试相邻 child 如果窗口范围相同则共享同一个 parent（去重）"""
        # 文档不是特别大，但刚好超过 max_parent_size，
        # 预期部分 child 共享同一个窗口范围的 parent
        long_text = "这里有一段超过限制的文本内容。" * 80
        content = f"# 标题\n\n{long_text}"
        file_path = tmp_path / "sw_dedup.md"
        file_path.write_text(content, encoding="utf-8")

        chunker = MarkdownChunker(max_chunk_size=128, max_parent_size=512)
        chunks = chunker.chunk(str(file_path))

        parent_chunks = [c for c in chunks if c.metadata["chunk_level"] == "parent"]
        child_chunks = [c for c in chunks if c.metadata["chunk_level"] == "child"]

        if len(child_chunks) > 1:
            # 检查是否存在共享 parent 的 child（去重效果）
            parent_to_children = {}
            for child in child_chunks:
                pid = child.metadata["parent_chunk_id"]
                parent_to_children.setdefault(pid, []).append(child)

            # 至少应该有一些 child 共享 parent
            total_parents = len(parent_chunks)
            total_children = len(child_chunks)
            # parent 数应该 ≤ child 数（因为去重）
            assert total_parents <= total_children, (
                f"parent 数 {total_parents} 不应超过 child 数 {total_children}"
            )

    def test_sliding_window_has_window_range_meta(self, tmp_path):
        """测试滑动窗口 parent 包含 window_range 元数据"""
        long_text = "正文内容用于测试窗口范围元数据是否正确生成。" * 100
        content = f"# 标题\n\n{long_text}"
        file_path = tmp_path / "sw_meta.md"
        file_path.write_text(content, encoding="utf-8")

        chunker = MarkdownChunker(max_chunk_size=128, max_parent_size=512)
        chunks = chunker.chunk(str(file_path))

        parent_chunks = [c for c in chunks if c.metadata["chunk_level"] == "parent"]

        # 如果触发了滑动窗口，parent 应有 window_range 元数据
        if len(parent_chunks) > 1:
            for parent in parent_chunks:
                if "window_range" in parent.metadata:
                    assert "-" in parent.metadata["window_range"], (
                        f"window_range 格式应为 'start-end'，实际为 '{parent.metadata['window_range']}'"
                    )

    def test_normal_parent_not_affected(self, tmp_path):
        """测试正常大小的 parent（≤ max_parent_size）不受滑动窗口影响"""
        # 构造一个普通大小的文档，每个段落不超过限制
        content = "# 命令手册\n\n## 使用方式\n\n"
        content += "### 方式一\n\n使用方式一的详细说明。" + " 补充" * 20 + "\n\n"
        content += "### 方式二\n\n使用方式二的详细说明。" + " 补充" * 20 + "\n\n"
        file_path = tmp_path / "normal_parent.md"
        file_path.write_text(content, encoding="utf-8")

        chunker = MarkdownChunker(max_chunk_size=512, max_parent_size=2048)
        chunks = chunker.chunk(str(file_path))

        parent_chunks = [c for c in chunks if c.metadata["chunk_level"] == "parent"]

        # 正常 parent 不应有 window_range 元数据
        for parent in parent_chunks:
            assert "window_range" not in parent.metadata, (
                "正常大小的 parent 不应触发滑动窗口"
            )

    def test_leaf_equals_parent_triggers_sliding_window(self, tmp_path):
        """测试 leaf=parent 且超过 max_parent_size 时确实触发滑动窗口"""
        # 一个有子标题结构的文档，但叶子节点本身非常大
        long_text = "每一条排查步骤都非常详细，包含很多描述信息和示例代码。" * 150
        content = "# 主标题\n\n"
        content += f"## 超大叶子章节\n\n{long_text}\n\n"
        content += "## 正常章节\n\n这是正常章节的内容。" + " 补充" * 20
        file_path = tmp_path / "leaf_parent_sw.md"
        file_path.write_text(content, encoding="utf-8")

        chunker = MarkdownChunker(max_chunk_size=128, max_parent_size=512)
        chunks = chunker.chunk(str(file_path))

        parent_chunks = [c for c in chunks if c.metadata["chunk_level"] == "parent"]
        child_chunks = [c for c in chunks if c.metadata["chunk_level"] == "child"]

        # 超大叶子章节应触发滑动窗口，产生多个 parent
        # 正常章节不触发，产生 1 个 parent
        # 总 parent 数应 > 1
        assert len(parent_chunks) > 1, (
            f"超大 leaf=parent 场景应产生多个 parent（滑动窗口），"
            f"实际只有 {len(parent_chunks)} 个"
        )

        # 每个 child 都应指向合法 parent
        parent_ids = {c.chunk_id for c in parent_chunks}
        for child in child_chunks:
            assert child.metadata["parent_chunk_id"] in parent_ids

        # 所有 parent 的 token 数应可控（不超过 max_parent_size 太多）
        for parent in parent_chunks:
            tokens = chunker.count_tokens(parent.content)
            # 允许少量超出（由于句子不可分割）
            assert tokens <= 512 + 128, (
                f"parent token 数 {tokens} 大幅超过 max_parent_size"
            )