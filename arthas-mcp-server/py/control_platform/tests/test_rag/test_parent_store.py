"""
ParentChunkStore 单元测试

测试覆盖：
- 存储和查询 parent chunk
- get_children_count 方法
- reset 方法
- __len__ 和 __contains__ 方法
- 边界情况（不存在的 chunk_id、覆盖写入等）
"""

import pytest

from control_platform.rag.parent_store import ParentChunkStore


class TestParentChunkStore:
    """ParentChunkStore 单元测试"""

    def setup_method(self):
        self.store = ParentChunkStore()

    def test_add_and_get_parent(self):
        """测试存储和查询 parent chunk"""
        self.store.add_parent(
            chunk_id="abc123_p0",
            content="# 标题\n\n正文内容",
            metadata={"chunk_level": "parent", "total_children": 3},
        )

        result = self.store.get_parent("abc123_p0")
        assert result is not None
        assert result["content"] == "# 标题\n\n正文内容"
        assert result["metadata"]["chunk_level"] == "parent"
        assert result["metadata"]["total_children"] == 3

    def test_get_nonexistent_parent(self):
        """测试查询不存在的 parent chunk 返回 None"""
        result = self.store.get_parent("nonexistent_id")
        assert result is None

    def test_add_parent_overwrite(self):
        """测试重复写入同一 chunk_id 会覆盖"""
        self.store.add_parent("abc_p0", "旧内容", {"v": 1})
        self.store.add_parent("abc_p0", "新内容", {"v": 2})

        result = self.store.get_parent("abc_p0")
        assert result["content"] == "新内容"
        assert result["metadata"]["v"] == 2

    def test_add_parent_default_metadata(self):
        """测试不传 metadata 时默认为空字典"""
        self.store.add_parent("abc_p0", "内容")

        result = self.store.get_parent("abc_p0")
        assert result["metadata"] == {}

    def test_get_children_count(self):
        """测试获取 parent 下的 child 数量"""
        self.store.add_parent(
            "abc_p0", "内容",
            {"chunk_level": "parent", "total_children": 5},
        )
        assert self.store.get_children_count("abc_p0") == 5

    def test_get_children_count_missing_field(self):
        """测试 metadata 中缺少 total_children 字段时返回 0"""
        self.store.add_parent("abc_p0", "内容", {"chunk_level": "parent"})
        assert self.store.get_children_count("abc_p0") == 0

    def test_get_children_count_nonexistent(self):
        """测试不存在的 parent 返回 0"""
        assert self.store.get_children_count("nonexistent") == 0

    def test_reset(self):
        """测试 reset 清空所有数据"""
        self.store.add_parent("abc_p0", "内容1")
        self.store.add_parent("abc_p1", "内容2")
        assert len(self.store) == 2

        self.store.reset()
        assert len(self.store) == 0
        assert self.store.get_parent("abc_p0") is None

    def test_len(self):
        """测试 __len__ 返回正确的数量"""
        assert len(self.store) == 0
        self.store.add_parent("a", "1")
        assert len(self.store) == 1
        self.store.add_parent("b", "2")
        assert len(self.store) == 2

    def test_contains(self):
        """测试 __contains__ 检查"""
        assert "abc_p0" not in self.store
        self.store.add_parent("abc_p0", "内容")
        assert "abc_p0" in self.store

    def test_multiple_parents(self):
        """测试存储多个 parent chunk"""
        for i in range(10):
            self.store.add_parent(
                f"chunk_p{i}",
                f"Parent {i} 的内容",
                {"total_children": i + 1},
            )

        assert len(self.store) == 10
        for i in range(10):
            result = self.store.get_parent(f"chunk_p{i}")
            assert result is not None
            assert result["content"] == f"Parent {i} 的内容"
            assert self.store.get_children_count(f"chunk_p{i}") == i + 1
