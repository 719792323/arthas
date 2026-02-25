"""
执行池抽象基类

定义任务执行池的通用接口，所有执行池实现必须继承此基类。
执行池负责：并发控制、锁管理、handler 执行、错误处理、链式投递。
"""

from __future__ import annotations

import abc
import logging
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from control_platform.db.repository import DiagnosisRepository
    from control_platform.event.handler import StageHandlerRegistry
    from control_platform.lock.base import TaskLock

logger = logging.getLogger(__name__)


class TaskExecutor(abc.ABC):
    """
    任务执行池抽象基类

    执行池是真正的执行引擎，负责：
    1. 并发控制（信号量）
    2. 锁管理（acquire / release）
    3. 调用 handler 执行业务逻辑
    4. 错误处理（mark_failed / fail_task）
    5. 链式投递（handler 返回 next_stage 时递归提交）

    Scheduler 只负责轮询 + 提交到 Pool，不涉及任何执行逻辑。

    Attributes:
        _max_concurrency: 最大并发数
        _repo: 诊断仓储层
        _handler_registry: 阶段处理器注册表
        _task_lock: 任务锁
    """

    def __init__(
        self,
        max_concurrency: int = 10,
        repo: Optional[DiagnosisRepository] = None,
        handler_registry: Optional[StageHandlerRegistry] = None,
        task_lock: Optional[TaskLock] = None,
    ):
        """
        Args:
            max_concurrency: 最大并发数
            repo: 诊断仓储层
            handler_registry: 阶段处理器注册表
            task_lock: 任务锁
        """
        self._max_concurrency = max_concurrency
        self._repo = repo
        self._handler_registry = handler_registry
        self._task_lock = task_lock

    @abc.abstractmethod
    async def submit(self, task: Any, stage: Any) -> None:
        """
        提交 (task, stage) 到执行池

        执行池内部会处理：加锁 → 执行 handler → 错误处理 → 释放锁 → 链式投递

        Args:
            task: 要执行的任务对象（需有 task_id 属性）
            stage: 当前待处理的 DiagnosisStage
        """
        ...

    @abc.abstractmethod
    async def shutdown(self) -> None:
        """关闭执行池，等待所有正在执行的任务完成"""
        ...

    @property
    def max_concurrency(self) -> int:
        """最大并发数"""
        return self._max_concurrency