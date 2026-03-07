"""
ChunkerRegistry 单元测试

测试扩展名分发、未注册扩展名处理、自定义 Chunker 注册。
"""

from typing import List, Optional

import pytest

from control_platform.rag.base_chunker import BaseChunker, DocumentChunk
from control_platform.rag.chunker_registry import ChunkerRegistry


class DummyChunker(BaseChunker):
    """测试用自定义 Chunker"""

    def supported_extensions(self) -> List[str]:
        return [".txt", ".text"]

    def chunk(self, file_path: str, metadata: Optional[dict] = None) -> List[DocumentChunk]:
        return [DocumentChunk(content="dummy", metadata=metadata or {}, chunk_id="dummy_0")]


class TestChunkerRegistry:
    """ChunkerRegistry 单元测试"""

    def test_builtin_markdown_registration(self):
        """测试初始化时自动注册 MarkdownChunker"""
        registry = ChunkerRegistry()
        assert registry.get_chunker(".md") is not None
        assert registry.get_chunker(".markdown") is not None
        print(f"✅ 内置注册: {registry.supported_extensions}")

    def test_md_extension_dispatch(self, arthas_thread_md_file):
        """测试 .md 扩展名正确分发给 MarkdownChunker"""
        registry = ChunkerRegistry()
        chunks = registry.chunk_file(arthas_thread_md_file)
        assert len(chunks) > 0
        print(f"✅ .md 分发: {len(chunks)} 个片段")

    def test_unknown_extension_warning(self, tmp_path):
        """测试未注册扩展名（如 .xyz）返回空并记录 WARNING"""
        registry = ChunkerRegistry()
        unknown_file = tmp_path / "test.xyz"
        unknown_file.write_text("some content", encoding="utf-8")

        chunks = registry.chunk_file(str(unknown_file))
        assert chunks == []
        print("✅ 未注册扩展名 .xyz: 返回空列表")

    def test_register_custom_chunker(self, tmp_path):
        """测试注册自定义 Chunker 后能正确分发"""
        registry = ChunkerRegistry()
        dummy = DummyChunker()
        registry.register(dummy)

        assert registry.get_chunker(".txt") is dummy
        assert registry.get_chunker(".text") is dummy

        # 创建 .txt 文件测试分发
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("hello", encoding="utf-8")
        chunks = registry.chunk_file(str(txt_file))
        assert len(chunks) == 1
        assert chunks[0].content == "dummy"
        print("✅ 自定义 Chunker 注册并分发成功")

    def test_case_insensitive_extension(self):
        """测试扩展名大小写不敏感"""
        registry = ChunkerRegistry()
        assert registry.get_chunker(".MD") is not None
        assert registry.get_chunker(".Md") is not None
        print("✅ 扩展名大小写不敏感")
