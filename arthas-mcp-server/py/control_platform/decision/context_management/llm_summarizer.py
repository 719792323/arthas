"""
LLM 摘要服务

提供单条工具结果摘要和全文对话摘要两种接口，
基于 AsyncOpenAI 客户端实现，支持独立的摘要模型配置和超时控制。
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI

from control_platform.config import settings

logger = logging.getLogger(__name__)

# ==================== 摘要 Prompt 模板 ====================

SINGLE_SUMMARY_SYSTEM_PROMPT = """你是一个专业的诊断信息摘要助手。你需要对工具执行结果进行精炼摘要，同时保留关键诊断信息。

## 摘要规则
1. **必须保留**的信息：
   - 异常堆栈信息（Exception stacktrace）
   - 错误码和错误消息
   - 关键性能指标数值（CPU、内存、GC 等）
   - 异常线程名称和状态
   - 死锁信息
   - 关键的方法调用路径
2. **可以精简**的信息：
   - 重复的正常线程堆栈
   - 大量相似的日志行
   - 标准库的中间调用栈帧
3. 输出格式：纯文本，结构化但简洁
4. 不要添加任何分析或建议，只做信息提取和精简
"""

SINGLE_SUMMARY_USER_TEMPLATE = """请对以下工具执行结果进行摘要。

工具名称: {tool_name}
用户原始问题: {user_query}

工具执行结果:
{content}

请输出精简后的关键信息摘要："""

FULL_SUMMARY_SYSTEM_PROMPT = """你是一个专业的诊断对话摘要助手。你需要将多轮诊断对话历史压缩为一段结构化摘要，帮助后续推理了解之前的诊断进展。

## 摘要输出结构（严格按此格式）
### 已执行的工具
- [工具名] → [简要结果]

### 关键发现
- [异常信息/指标数据/线程状态等关键发现]

### 当前诊断阶段
[描述当前诊断进展到哪一步]

### 待验证假设
- [尚未验证的猜测或下一步方向]

## 规则
1. 保留所有工具的执行记录和关键结果
2. 保留关键的异常信息、指标数据
3. 不要丢失任何可能影响后续诊断的信息
4. 语言简洁，避免冗余
"""

FULL_SUMMARY_USER_TEMPLATE = """请将以下诊断对话历史压缩为结构化摘要。

用户原始问题: {user_query}

对话历史:
{conversation}

请输出结构化摘要："""


class LLMSummarizer:
    """
    LLM 摘要服务

    提供两个核心接口：
    - summarize_single(): 单条工具结果 LLM 摘要
    - summarize_conversation(): 全文对话 LLM 摘要

    支持独立的 summary_model 配置或复用主模型，
    支持超时控制和日志记录。
    """

    def __init__(
            self,
            api_key: Optional[str] = None,
            base_url: Optional[str] = None,
            model: Optional[str] = None,
            timeout: Optional[float] = None,
    ):
        """
        初始化 LLMSummarizer

        Args:
            api_key: API 密钥，默认使用 settings.llm_api_key
            base_url: API 基础 URL，默认使用 settings.llm_base_url
            model: 摘要模型名称，默认使用 settings.summary_model（空则回退到 settings.llm_model）
            timeout: 超时秒数，默认使用 settings.summary_timeout
        """
        self._api_key = api_key or settings.llm_api_key
        self._base_url = base_url or settings.llm_base_url
        self._model = model or settings.summary_model or settings.llm_model
        self._timeout = timeout if timeout is not None else settings.summary_timeout

        self._client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def timeout(self) -> float:
        return self._timeout

    async def summarize_single(
            self,
            content: str,
            context_hint: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        对单条工具结果进行 LLM 摘要

        Args:
            content: 工具执行结果原文
            context_hint: 上下文提示，包含 tool_name 和 user_query

        Returns:
            摘要后的文本

        Raises:
            TimeoutError: 超时
            Exception: 其他 API 错误
        """
        hint = context_hint or {}
        tool_name = hint.get("tool_name", "unknown")
        user_query = hint.get("user_query", "")

        user_message = SINGLE_SUMMARY_USER_TEMPLATE.format(
            tool_name=tool_name,
            user_query=user_query,
            content=content,
        )

        return await self._call_llm(
            system_prompt=SINGLE_SUMMARY_SYSTEM_PROMPT,
            user_message=user_message,
            operation="单条摘要",
            context_info=f"tool={tool_name}",
        )

    async def summarize_conversation(
            self,
            messages: List[Dict[str, Any]],
            user_query: str,
    ) -> str:
        """
        对多条消息进行全文对话摘要

        Args:
            messages: 需要压缩的消息列表
            user_query: 用户原始问题

        Returns:
            结构化摘要文本

        Raises:
            TimeoutError: 超时
            Exception: 其他 API 错误
        """
        # 将消息列表转换为可读的对话文本
        conversation_text = self._format_messages_for_summary(messages)

        user_message = FULL_SUMMARY_USER_TEMPLATE.format(
            user_query=user_query,
            conversation=conversation_text,
        )

        return await self._call_llm(
            system_prompt=FULL_SUMMARY_SYSTEM_PROMPT,
            user_message=user_message,
            operation="全文摘要",
            context_info=f"messages={len(messages)}",
        )

    async def _call_llm(
            self,
            system_prompt: str,
            user_message: str,
            operation: str,
            context_info: str,
    ) -> str:
        """
        统一的 LLM 调用封装

        Args:
            system_prompt: 系统提示词
            user_message: 用户消息
            operation: 操作名称（用于日志）
            context_info: 上下文信息（用于日志）

        Returns:
            LLM 响应文本

        Raises:
            TimeoutError: 超时
            Exception: 其他 API 错误
        """
        start_time = time.monotonic()

        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    temperature=0.1,
                    max_tokens=16384,
                ),
                timeout=self._timeout,
            )

            elapsed = time.monotonic() - start_time
            result = response.choices[0].message.content or ""

            # 记录 token 使用情况
            usage = response.usage
            input_tokens = usage.prompt_tokens if usage else 0
            output_tokens = usage.completion_tokens if usage else 0

            logger.info(
                "[LLMSummarizer] %s完成 (%s): 输入=%d tokens, 输出=%d tokens, 耗时=%.2fs",
                operation,
                context_info,
                input_tokens,
                output_tokens,
                elapsed,
            )

            return result

        except asyncio.TimeoutError:
            elapsed = time.monotonic() - start_time
            logger.warning(
                "[LLMSummarizer] %s超时 (%s): 超时阈值=%.1fs, 实际耗时=%.2fs",
                operation,
                context_info,
                self._timeout,
                elapsed,
            )
            raise TimeoutError(
                f"LLM {operation}超时：超过 {self._timeout}s 阈值"
            )

        except Exception as e:
            elapsed = time.monotonic() - start_time
            logger.warning(
                "[LLMSummarizer] %s失败 (%s): error=%s, 耗时=%.2fs",
                operation,
                context_info,
                str(e),
                elapsed,
            )
            raise

    @staticmethod
    def _format_messages_for_summary(messages: List[Dict[str, Any]]) -> str:
        """
        将消息列表格式化为可读的对话文本

        支持两种消息格式：
        - OpenAI 格式：role=assistant(tool_calls), role=tool
        - ContextBuilder 格式：role=function_call, role=function_result

        Args:
            messages: 消息列表

        Returns:
            格式化后的文本
        """
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            if role == "tool":
                tool_call_id = msg.get("tool_call_id", "")
                lines.append(f"[工具结果] (call_id={tool_call_id})\n{content}")
            elif role == "function_call":
                # ContextBuilder 格式的 TOOL_CALL stage
                # 跳过，因为 content 与 function_result 重复
                tool_name = msg.get("tool_name", "unknown")
                tool_args = msg.get("tool_arguments", {})
                lines.append(f"[工具调用] {tool_name}({json.dumps(tool_args, ensure_ascii=False)})")
            elif role == "function_result":
                # ContextBuilder 格式的 TOOL_RESULT stage
                tool_name = msg.get("tool_name", "unknown")
                lines.append(f"[工具结果] ({tool_name})\n{content}")
            elif role == "assistant":
                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    calls_desc = []
                    for tc in tool_calls:
                        func = tc.get("function", {})
                        calls_desc.append(
                            f"  - {func.get('name', '?')}({func.get('arguments', '')})"
                        )
                    lines.append(f"[助手-工具调用]\n" + "\n".join(calls_desc))
                elif content:
                    lines.append(f"[助手] {content}")
            elif role == "user":
                lines.append(f"[用户] {content}")
            elif role == "system":
                lines.append(f"[系统] {content}")
            else:
                lines.append(f"[{role}] {content}")

        return "\n\n".join(lines)
