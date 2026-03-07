"""全面审计测试脚本"""
import signal, sys, os, uuid

signal.signal(signal.SIGALRM, lambda s, f: (print("TIMEOUT!"), sys.exit(1)))
signal.alarm(120)

from control_platform.rag.markdown_chunker import MarkdownChunker

errors = []

def run_test(name, func):
    try:
        func()
        print(f"  OK {name}")
    except Exception as e:
        errors.append((name, str(e)))
        print(f"  FAIL {name}: {e}")

def make_file(content):
    path = os.path.join("/tmp", f"test_{uuid.uuid4().hex[:8]}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path

def check_integrity(chunks, chunker, max_parent=None):
    all_ids = [c.chunk_id for c in chunks]
    dupes = [x for x in all_ids if all_ids.count(x) > 1]
    assert len(all_ids) == len(set(all_ids)), f"chunk_id duplicates: {set(dupes)}"

    parents = [c for c in chunks if c.metadata["chunk_level"] == "parent"]
    children = [c for c in chunks if c.metadata["chunk_level"] == "child"]
    parent_ids = {c.chunk_id for c in parents}

    for child in children:
        pid = child.metadata.get("parent_chunk_id")
        assert pid, f"child {child.chunk_id} missing parent_chunk_id"
        assert pid in parent_ids, f"child {child.chunk_id} parent {pid} not in parent set"

    if max_parent:
        for p in parents:
            tokens = chunker.count_tokens(p.content)
            assert tokens <= max_parent + 256, f"parent {p.chunk_id} too large: {tokens} tokens (limit {max_parent})"

    return parents, children


# === 1. 基础场景 ===
print("=== 1. 基础场景 ===")

def test_empty():
    chunker = MarkdownChunker()
    path = make_file("")
    assert chunker.chunk(path) == []
    os.unlink(path)
run_test("空文档", test_empty)

def test_whitespace_only():
    chunker = MarkdownChunker()
    path = make_file("   \n\n  \n  ")
    assert chunker.chunk(path) == []
    os.unlink(path)
run_test("纯空白文档", test_whitespace_only)

def test_short_no_heading():
    chunker = MarkdownChunker()
    path = make_file("短文本内容。")
    chunks = chunker.chunk(path)
    os.unlink(path)
    assert len(chunks) == 2  # 1 parent + 1 child
    check_integrity(chunks, chunker)
run_test("短无标题文档", test_short_no_heading)

def test_only_headings_no_body():
    chunker = MarkdownChunker()
    path = make_file("# 标题\n\n## 子标题\n\n### 三级\n")
    chunks = chunker.chunk(path)
    os.unlink(path)
    children = [c for c in chunks if c.metadata["chunk_level"] == "child"]
    parents = [c for c in chunks if c.metadata["chunk_level"] == "parent"]
    print(f"    只有标题无正文: {len(chunks)} chunks, {len(parents)} parents, {len(children)} children")
    # 检查是否有孤立 parent（有 parent 但没有对应的 child）
    if parents and not children:
        print(f"    WARNING: 孤立 parent 问题 - {len(parents)} parents but 0 children")
run_test("只有标题无正文", test_only_headings_no_body)


# === 2. 标题前正文 ===
print("=== 2. 标题前正文 ===")

def test_text_before_first_heading():
    chunker = MarkdownChunker()
    content = "这是标题之前的正文内容，应该被保留。" * 10 + "\n\n# 第一个标题\n\n标题下的内容。" + " 补充" * 20
    path = make_file(content)
    chunks = chunker.chunk(path)
    os.unlink(path)
    all_content = " ".join(c.content for c in chunks)
    has_pre_heading = "标题之前的正文内容" in all_content
    print(f"    标题前正文是否保留: {has_pre_heading}")
    if not has_pre_heading:
        raise AssertionError("标题前正文丢失! root.body_lines 未被收集")
run_test("标题前正文保留", test_text_before_first_heading)


# === 3. _split_long_child 边界 ===
print("=== 3. _split_long_child 边界 ===")

def test_overlap_deadloop():
    chunker = MarkdownChunker(max_chunk_size=128, max_parent_size=2048)
    content = "这是一段需要被切分的正文内容，包含足够多的文字。" * 30
    path = make_file(content)
    chunks = chunker.chunk(path)
    os.unlink(path)
    assert len(chunks) > 2
    check_integrity(chunks, chunker)
run_test("overlap死循环场景(已修)", test_overlap_deadloop)

def test_no_punctuation_long_paragraph():
    chunker = MarkdownChunker(max_chunk_size=128, max_parent_size=2048)
    content = "# 标题\n\n" + "无标点内容 " * 500
    path = make_file(content)
    chunks = chunker.chunk(path)
    os.unlink(path)
    children = [c for c in chunks if c.metadata["chunk_level"] == "child"]
    # 无标点长段落：_split_into_sentences 把整段作为一个句子
    # _split_long_child 强制放入
    print(f"    无标点长段落: {len(children)} children")
    check_integrity(chunks, chunker)
run_test("无标点长段落", test_no_punctuation_long_paragraph)

def test_single_very_long_sentence():
    chunker = MarkdownChunker(max_chunk_size=64, max_parent_size=2048)
    content = "# 标题\n\n" + "长" * 500
    path = make_file(content)
    chunks = chunker.chunk(path)
    os.unlink(path)
    children = [c for c in chunks if c.metadata["chunk_level"] == "child"]
    assert len(children) >= 1
    check_integrity(chunks, chunker)
run_test("单个超长句子强制放入", test_single_very_long_sentence)

def test_mixed_overlap_edge():
    """ancestor_lines + overlap 导致无法前进的场景"""
    chunker = MarkdownChunker(max_chunk_size=128, max_parent_size=256)
    content = "# 主标题\n\n"
    content += "## 超大章节\n\n" + "详细内容。" * 200 + "\n\n"
    content += "## 正常章节\n\n正常内容。" + " 补充" * 30 + "\n\n"
    content += "## 另一个正常\n\n另一个正常。" + " 补充" * 30
    path = make_file(content)
    chunks = chunker.chunk(path)
    os.unlink(path)
    check_integrity(chunks, chunker)
run_test("混合正常+超大段落(ancestor+overlap边界)", test_mixed_overlap_edge)


# === 4. 滑动窗口 parent ===
print("=== 4. 滑动窗口 parent ===")

def test_sliding_window_with_heading():
    chunker = MarkdownChunker(max_chunk_size=128, max_parent_size=256)
    long_text = "排查步骤详细描述。" * 150
    content = f"# 主标题\n\n{long_text}"
    path = make_file(content)
    chunks = chunker.chunk(path)
    os.unlink(path)
    parents, children = check_integrity(chunks, chunker, max_parent=256)
    assert len(parents) > 1, f"应有多个滑动窗口 parent, 实际 {len(parents)}"
run_test("有标题超大leaf滑动窗口", test_sliding_window_with_heading)

def test_no_heading_huge():
    chunker = MarkdownChunker(max_chunk_size=128, max_parent_size=256)
    content = "这是一段非常长的正文内容。" * 200
    path = make_file(content)
    chunks = chunker.chunk(path)
    os.unlink(path)
    parents, children = check_integrity(chunks, chunker, max_parent=256)
    assert len(parents) >= 1
run_test("无标题超大文档滑动窗口", test_no_heading_huge)

def test_multiple_huge_leaves():
    chunker = MarkdownChunker(max_chunk_size=128, max_parent_size=256)
    content = "# 主标题\n\n"
    for i in range(3):
        content += f"## 超大章节{i}\n\n" + f"章节{i}的详细内容。" * 150 + "\n\n"
    path = make_file(content)
    chunks = chunker.chunk(path)
    os.unlink(path)
    parents, children = check_integrity(chunks, chunker, max_parent=256)
run_test("多个超大叶子节点", test_multiple_huge_leaves)


# === 5. 代码块处理 ===
print("=== 5. 代码块处理 ===")

def test_heading_in_code_block():
    chunker = MarkdownChunker()
    content = "# 真标题\n\n正文内容。" + " 补充" * 20 + "\n\n```python\n# 这不是标题\ndef foo():\n    pass\n```\n"
    path = make_file(content)
    chunks = chunker.chunk(path)
    os.unlink(path)
    for c in chunks:
        assert "这不是标题" not in c.metadata.get("heading_path", "")
    check_integrity(chunks, chunker)
run_test("代码块中的#不被误识别", test_heading_in_code_block)

def test_unclosed_code_block():
    chunker = MarkdownChunker()
    content = "# 标题\n\n正文。" + " 补充" * 20 + "\n\n```python\ndef foo():\n    pass\n# 未闭合的代码块"
    path = make_file(content)
    chunks = chunker.chunk(path)
    os.unlink(path)
    assert len(chunks) > 0
    check_integrity(chunks, chunker)
run_test("未闭合代码块", test_unclosed_code_block)


# === 6. chunk_index 递增 ===
print("=== 6. chunk_index 递增 ===")

def test_chunk_index_uniqueness():
    chunker = MarkdownChunker(max_chunk_size=128, max_parent_size=2048)
    content = "# 主标题\n\n"
    for i in range(5):
        if i % 2 == 0:
            content += f"## 短章节{i}\n\n短内容{i}。" + " 补充" * 20 + "\n\n"
        else:
            content += f"## 长章节{i}\n\n" + f"长内容{i}。" * 50 + "\n\n"
    path = make_file(content)
    chunks = chunker.chunk(path)
    os.unlink(path)
    check_integrity(chunks, chunker)
run_test("chunk_index唯一性(混合长短章节)", test_chunk_index_uniqueness)


# === 7. _split_into_sentences 硬编码 128 ===
print("=== 7. _split_into_sentences 硬编码 128 ===")

def test_split_sentences_small_max():
    chunker = MarkdownChunker(max_chunk_size=64, max_parent_size=2048)
    content = "# 标题\n\n" + "中文句子。" * 25
    path = make_file(content)
    chunks = chunker.chunk(path)
    os.unlink(path)
    children = [c for c in chunks if c.metadata["chunk_level"] == "child"]
    print(f"    小max_chunk场景: {len(children)} children")
    check_integrity(chunks, chunker)
run_test("小max_chunk_size下的句子分割", test_split_sentences_small_max)


# === 8. total_children 准确性 ===
print("=== 8. total_children 准确性 ===")

def test_total_children_accuracy():
    chunker = MarkdownChunker(min_chunk_size=64)
    content = "# 主标题\n\n## 容器\n\n"
    content += "### 极短\n\n短。\n\n"
    content += "### 正常1\n\n正常内容。" + " 补充" * 30 + "\n\n"
    content += "### 正常2\n\n正常内容。" + " 补充" * 30 + "\n"
    path = make_file(content)
    chunks = chunker.chunk(path)
    os.unlink(path)
    parents = {c.chunk_id: c for c in chunks if c.metadata["chunk_level"] == "parent"}
    children = [c for c in chunks if c.metadata["chunk_level"] == "child"]

    actual_counts = {}
    for child in children:
        pid = child.metadata["parent_chunk_id"]
        actual_counts[pid] = actual_counts.get(pid, 0) + 1

    for pid, parent in parents.items():
        declared = parent.metadata["total_children"]
        actual = actual_counts.get(pid, 0)
        print(f"    parent {pid}: declared={declared}, actual={actual}")
        if actual == 0:
            raise AssertionError(f"孤立 parent {pid} (declared {declared} but actual 0)")
run_test("total_children准确性", test_total_children_accuracy)


# === 9. 深层嵌套 ===
print("=== 9. 深层嵌套 ===")

def test_deep_nesting():
    chunker = MarkdownChunker(max_chunk_size=128, max_parent_size=256)
    content = "# A\n\n## B\n\n### C\n\n#### D\n\n##### E\n\n" + "深层嵌套内容。" * 200
    path = make_file(content)
    chunks = chunker.chunk(path)
    os.unlink(path)
    check_integrity(chunks, chunker, max_parent=256)
    children = [c for c in chunks if c.metadata["chunk_level"] == "child"]
    for child in children[:3]:
        assert "#" in child.content, "child should contain heading prefix"
run_test("深层嵌套(5级)", test_deep_nesting)


# === 10. 标题跳级 ===
print("=== 10. 标题跳级 ===")

def test_heading_level_skip():
    """直接从 # 跳到 ### （没有 ##）"""
    chunker = MarkdownChunker()
    content = "# 主标题\n\n### 跳级三级\n\n三级内容。" + " 补充" * 20 + "\n\n### 另一个三级\n\n另一个三级内容。" + " 补充" * 20
    path = make_file(content)
    chunks = chunker.chunk(path)
    os.unlink(path)
    check_integrity(chunks, chunker)
run_test("标题跳级(#直接到###)", test_heading_level_skip)


# === 11. del parent_map[pid] 后再次遇到同 pid ===
print("=== 11. parent_map 一致性 ===")

def test_parent_map_after_sliding_window():
    """滑动窗口路径中 del parent_map[pid] 后，如果同一个 pid 再次出现（不太可能但验证）"""
    chunker = MarkdownChunker(max_chunk_size=128, max_parent_size=256)
    # 单个超大 leaf=parent，不会再次遇到同 pid
    long_text = "排查步骤说明。" * 200
    content = f"# 唯一标题\n\n{long_text}"
    path = make_file(content)
    chunks = chunker.chunk(path)
    os.unlink(path)
    check_integrity(chunks, chunker, max_parent=256)
run_test("parent_map一致性(滑动窗口后)", test_parent_map_after_sliding_window)


# === 总结 ===
print()
if errors:
    print(f"=== 发现 {len(errors)} 个问题 ===")
    for name, err in errors:
        print(f"  FAIL {name}: {err}")
    sys.exit(1)
else:
    print("=== 全部检查通过 ===")
