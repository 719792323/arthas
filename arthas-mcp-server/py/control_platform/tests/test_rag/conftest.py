"""
RAG 模块测试通用 fixtures
"""

import os
import tempfile
import time

import pytest


def pytest_sessionfinish(session, exitstatus):
    """pytest session 结束后强制退出进程。

    sentence-transformers / PyTorch 会创建非 daemon 线程池（inter-op, intra-op threads），
    这些线程在测试结束后不会自动关闭，导致 pytest 进程挂起。
    通过 os._exit() 确保进程正常退出。
    """
    os._exit(exitstatus)


# ===== Arthas 风格 Markdown fixture =====
ARTHAS_THREAD_MD = """\
# thread 命令

查看当前线程信息，查找本应用最忙的线程。

## 使用方式

### 查看所有线程信息

```bash
thread
```

显示所有线程的 CPU 使用率、状态等信息。

### 查看指定线程

```bash
thread <id>
```

查看指定线程的堆栈信息。

### 查找最忙线程

```bash
thread -n 3
```

列出最忙的前 N 个线程并打印堆栈。

## 参数说明

| 参数名 | 参数说明 |
|--------|---------|
| id | 线程 ID |
| -n | 指定最忙线程的前 N 个 |
| -b | 找出阻塞其他线程的线程 |
| -i | 指定 CPU 使用率的采样间隔（毫秒） |

## 常见问题

### CPU 使用率高时如何排查

1. 使用 `thread -n 3` 找出最忙的线程
2. 查看对应线程的堆栈
3. 分析代码中的热点方法
"""


@pytest.fixture
def arthas_thread_md_content():
    """返回 Arthas thread 命令的 Markdown 文档内容"""
    return ARTHAS_THREAD_MD


@pytest.fixture
def arthas_thread_md_file(tmp_path):
    """创建临时的 Arthas thread 命令 Markdown 文件"""
    file_path = tmp_path / "thread.md"
    file_path.write_text(ARTHAS_THREAD_MD, encoding="utf-8")
    return str(file_path)


@pytest.fixture
def empty_md_file(tmp_path):
    """创建空的 Markdown 文件"""
    file_path = tmp_path / "empty.md"
    file_path.write_text("", encoding="utf-8")
    return str(file_path)


@pytest.fixture
def code_block_md_file(tmp_path):
    """创建包含代码块中 # 符号的 Markdown 文件"""
    content = """\
# 主标题

正文内容。

## 代码示例

```python
# 这是 Python 注释，不应被识别为标题
def foo():
    # 另一个注释
    pass
```

## 实际标题

这是实际标题下的内容。
"""
    file_path = tmp_path / "code_block.md"
    file_path.write_text(content, encoding="utf-8")
    return str(file_path)


@pytest.fixture
def no_heading_md_file(tmp_path):
    """创建无标题的纯文本 Markdown 文件"""
    content = "这是一段没有任何标题的纯文本内容。\n\n包含多个段落。"
    file_path = tmp_path / "no_heading.md"
    file_path.write_text(content, encoding="utf-8")
    return str(file_path)


@pytest.fixture
def knowledge_dir(tmp_path):
    """创建一个模拟的知识库目录结构"""
    # tool_docs 目录
    tool_docs = tmp_path / "knowledge" / "tool_docs"
    tool_docs.mkdir(parents=True)
    (tool_docs / "thread.md").write_text(ARTHAS_THREAD_MD, encoding="utf-8")

    # troubleshooting 目录
    troubleshoot = tmp_path / "knowledge" / "troubleshooting"
    troubleshoot.mkdir(parents=True)
    (troubleshoot / "cpu.md").write_text(
        "# CPU 排查手册\n\n## 高 CPU 使用率\n\n使用 thread -n 命令排查。\n",
        encoding="utf-8",
    )

    # cases 目录
    cases = tmp_path / "knowledge" / "cases"
    cases.mkdir(parents=True)
    (cases / "case_cpu.md").write_text(
        "# 案例：CPU 飙高\n\n## 问题描述\n\n应用 CPU 达到 100%。\n## 解决方案\n\n使用 thread -n 3。\n",
        encoding="utf-8",
    )

    return str(tmp_path / "knowledge")
