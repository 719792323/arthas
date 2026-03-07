"""
OpenAI 兼容决策引擎

基于 OpenAI Python SDK 调用 LLM（DeepSeek-V3 等）实现智能诊断决策。
支持两种工具调用模式：
  1. OpenAI 标准 function calling（tool_calls 字段）
  2. 文本 JSON 解析 fallback（适配不支持 function calling 的模型）

配置：
    CP_LLM_API_KEY: API 密钥（必须）
    CP_LLM_BASE_URL: API 地址（默认腾讯云 LKE 端点）
    CP_LLM_MODEL: 模型名称（默认 deepseek-v3-0324）
    CP_LLM_MAX_TOKENS: 最大生成 token 数
    CP_LLM_TEMPERATURE: 采样温度
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from control_platform.config import settings
from control_platform.decision.context import DecisionContext
from control_platform.decision.engine import DecisionEngine
from control_platform.decision.context_management.manager import ContextWindowManager
from control_platform.models.action import ActionType, DecisionResult

logger = logging.getLogger(__name__)


# ==================== 系统提示词模板 ====================

# 基础角色设定（固定不变）
_ROLE_PROMPT = """\
你是 Arthas 智能诊断助手，一个专业的 Java 应用诊断专家。
你通过 Arthas 提供的诊断工具来分析 Java 应用的运行时问题（CPU 飙高、内存泄漏、线程死锁、慢方法等）。
"""

# ReAct 循环指令（固定不变）
_REACT_PROMPT = """\
## 工作流程（ReAct 循环）

你必须遵循 **思考 → 行动 → 观察** 的循环：

1. **思考（Thought）**：分析当前已有的信息，判断下一步该做什么
2. **行动（Action）**：调用一个工具来收集诊断数据
3. **观察（Observation）**：分析工具返回的结果
4. 重复 1~3 直到信息充分，然后给出最终结论

## 输出格式要求

### 当你需要调用工具时，必须严格输出以下 JSON（不要包含任何其他内容）：

```json
{
  "action": "tool_call",
  "tool_name": "工具名称",
  "tool_arguments": { "参数名": "参数值" },
  "thinking": "你的思考过程（为什么要调用这个工具）"
}
```

### 当你已经收集到足够信息，要给出最终结论时，必须严格输出以下 JSON：

```json
{
  "action": "conclude",
  "thinking": "你的分析过程",
  "conclusion": "## 诊断结论\\n\\n你的完整结论，包含：问题分析、根因定位、优化建议"
}
```

## 重要规则

- **每次只输出一个 JSON**，不要输出多个
- **每次只调用一个工具**
- 如果工具调用失败，尝试换一种方式或直接根据已有信息给出结论
- 最多调用 10 次工具，之后必须给出结论
- 结论要包含：**问题分析**、**根因定位**、**优化建议**
- 所有内容使用中文
"""

# 工具列表模板（动态填充）
_TOOLS_PROMPT_HEADER = """\
## 可用工具列表

以下是你可以调用的 Arthas 诊断工具：
"""

_TOOL_TEMPLATE = """\
### {index}. `{name}`
- **描述**: {description}
- **参数**: {parameters}
"""

_NO_TOOLS_PROMPT = """\
## 可用工具

当前没有可用的工具列表。请直接根据你的 Java 诊断经验给出分析和建议。
"""


def build_system_prompt(
    available_tools: List[Dict[str, Any]],
    rag_context: Optional[Dict[str, Any]] = None,
) -> str:
    """
    动态构建系统提示词

    将角色设定 + RAG 知识（可选）+ ReAct 指令 + 工具列表拼接成完整的 system prompt。
    工具列表根据 Arthas 客户端的 tools/list 响应动态生成。

    Args:
        available_tools: 从 MCP tools/list 获取的工具定义列表
        rag_context: RAG 检索结果，包含 results 列表和 total_tokens

    Returns:
        完整的 system prompt 字符串
    """
    parts = [_ROLE_PROMPT]

    # RAG 知识注入：在 _ROLE_PROMPT 和 _REACT_PROMPT 之间插入
    if rag_context and rag_context.get("results"):
        rag_section = _build_rag_section(rag_context)
        parts.append(rag_section)

    parts.append(_REACT_PROMPT)

    if available_tools:
        tools_section = _TOOLS_PROMPT_HEADER
        for i, tool in enumerate(available_tools, 1):
            name = tool.get("name", "unknown")
            description = tool.get("description", "无描述")
            input_schema = tool.get("inputSchema", {})
            properties = input_schema.get("properties", {})
            required = input_schema.get("required", [])

            if properties:
                params_lines = []
                for param_name, param_info in properties.items():
                    param_desc = param_info.get("description", "无描述")
                    param_type = param_info.get("type", "any")
                    is_required = param_name in required
                    req_mark = "**必填**" if is_required else "可选"
                    params_lines.append(
                        f"  - `{param_name}` ({param_type}, {req_mark}): {param_desc}"
                    )
                parameters_str = "\n" + "\n".join(params_lines)
            else:
                parameters_str = "无参数（直接调用即可）"

            tools_section += _TOOL_TEMPLATE.format(
                index=i,
                name=name,
                description=description,
                parameters=parameters_str,
            )
        parts.append(tools_section)
    else:
        parts.append(_NO_TOOLS_PROMPT)

    return "\n".join(parts)


def _build_rag_section(rag_context: Dict[str, Any]) -> str:
    """构建 RAG 知识段落

    将 RAG 检索结果格式化为 Markdown 段落，插入到 System Prompt 中。

    Args:
        rag_context: RAG 检索结果，包含 results 列表

    Returns:
        格式化的 RAG 知识段落字符串
    """
    lines = [
        "\n## 参考知识",
        "以下是与用户问题相关的 Arthas 诊断知识，请参考但不要照搬，结合实际情况分析：",
        "",
    ]

    for result in rag_context.get("results", []):
        metadata = result.get("metadata", {})
        file_name = metadata.get("file_name", "未知文档")
        heading_path = metadata.get("heading_path", "")
        source_label = f"{file_name} > {heading_path}" if heading_path else file_name

        score = result.get("score", 0.0)
        lines.append(f"### 来源: {source_label}（相似度: {score:.2f}）")
        lines.append(result.get("document", ""))
        lines.append("")

    return "\n".join(lines)

# 匹配 ```json ... ``` 代码块 或 裸 JSON 对象
_JSON_BLOCK_RE = re.compile(
    r"```json\s*\n?(.*?)\n?\s*```",
    re.DOTALL,
)
_JSON_OBJECT_RE = re.compile(
    r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}",
    re.DOTALL,
)


def parse_text_response(text: str) -> Optional[Dict[str, Any]]:
    """
    从 LLM 文本输出中解析 JSON 动作

    尝试两种模式：
    1. ```json ... ``` 代码块
    2. 裸 JSON 对象（从文本中提取最大的 JSON）

    Returns:
        解析出的 JSON 字典，如果无法解析则返回 None
    """
    # 策略 1: 匹配 ```json ``` 代码块
    match = _JSON_BLOCK_RE.search(text)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 策略 2: 匹配裸 JSON 对象
    matches = _JSON_OBJECT_RE.findall(text)
    for candidate in reversed(matches):  # 优先取最后一个（最可能是输出的 action）
        try:
            obj = json.loads(candidate)
            # 至少要有 action 字段才算有效
            if "action" in obj:
                return obj
        except json.JSONDecodeError:
            continue

    return None


# ==================== 决策引擎 ====================

class OpenAIDecisionEngine(DecisionEngine):
    """
    基于 OpenAI 兼容 API 的决策引擎

    支持两种工具调用模式：

    1. **function calling 模式**（优先）：
       通过 OpenAI API 的 tools + tool_choice 参数让模型返回结构化的 tool_calls。
       适用于完全兼容 OpenAI function calling 的模型（GPT-4、DeepSeek-V3 等）。

    2. **文本 JSON 模式**（fallback）：
       如果模型不返回 tool_calls，则从文本输出中解析 JSON 格式的动作指令。
       通过 SYSTEM_PROMPT 中的格式要求引导模型输出标准 JSON。

    解析优先级：
       tool_calls 字段 > 文本中的 JSON > 纯文本结论
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.lkeap.cloud.tencent.com/v1",
        model: str = "deepseek-v3-0324",
        max_tokens: int = 8192,
        temperature: float = 0.1,
    ):
        """
        Args:
            api_key: API 密钥
            base_url: API 基础 URL
            model: 模型名称
            max_tokens: 最大生成 token 数
            temperature: 采样温度（低温度 = 更确定性的输出）
        """
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature

        logger.info(
            "✅ OpenAI 决策引擎初始化: model=%s, base_url=%s, "
            "max_tokens=%d, temperature=%.2f",
            model, base_url, max_tokens, temperature,
        )

        # Prompt 日志数据（每次 decide 后更新，由调用方读取并写入 DB）
        self._last_prompt_log: Optional[Dict[str, Any]] = None

        # 待持久化的上下文摘要（每次 decide 后更新，由调用方根据 LLM 结果决定是否持久化）
        self._pending_summary: Optional[Dict[str, Any]] = None

        # 上下文窗口管理器
        self._context_window_manager = ContextWindowManager()

    @property
    def engine_name(self) -> str:
        return f"OpenAIDecisionEngine({self._model})"

    # ==================== 核心入口 ====================

    async def decide(self, context: DecisionContext) -> DecisionResult:
        """
        调用 LLM 进行诊断推理

        流程：
        1. 动态构建 system prompt（含工具列表）
        2. 将 DecisionContext.messages 转换为 OpenAI chat messages
        3. 同时传入 tools 参数（function calling）和 prompt 中的工具描述（双保险）
        4. 调用 LLM API
        5. 按优先级解析：tool_calls → 文本 JSON → 纯文本结论
        """
        logger.info(
            "[OpenAI] 开始推理: task_id=%s, messages=%d, tools=%d",
            context.task_id, len(context.messages), len(context.available_tools),
        )

        # 0. 上下文窗口优化（Token 计数 → 全文摘要 → 滑动窗口）
        self._pending_summary = None  # 重置上一次的待持久化摘要
        try:
            context = await self._context_window_manager.optimize(context)
            # 提取待持久化的摘要数据
            self._pending_summary = (context.metadata or {}).get("pending_summary")
            logger.info(
                "[OpenAI] 上下文优化完成: task_id=%s, messages=%d, has_pending_summary=%s",
                context.task_id, len(context.messages), self._pending_summary is not None,
            )
        except Exception as opt_err:
            logger.warning(
                "[OpenAI] 上下文优化失败（使用原始上下文继续）: %s", opt_err
            )

        # 1. 动态构建 system prompt（含完整工具列表描述 + RAG 知识）
        system_prompt = build_system_prompt(
            context.available_tools,
            rag_context=context.rag_context,
        )

        # 2. 构建 OpenAI chat messages（system + 历史消息链）
        chat_messages = self._build_chat_messages(context, system_prompt)

        # 3. 构建 tools schema（function calling 参数）
        tools_schema = self._build_tools_schema(context.available_tools)

        # 4. 调用 LLM
        try:
            kwargs: Dict[str, Any] = {
                "model": self._model,
                "messages": chat_messages,
                "max_tokens": self._max_tokens,
                "temperature": self._temperature,
            }

            # 如果有可用工具，同时传入 function calling 参数
            if tools_schema:
                kwargs["tools"] = tools_schema
                kwargs["tool_choice"] = "auto"

            logger.debug(
                "[OpenAI] 发送请求: messages_count=%d, has_tools=%s",
                len(chat_messages), bool(tools_schema),
            )

            response = await self._client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            message = choice.message

            logger.info(
                "[OpenAI] LLM 响应: finish_reason=%s, "
                "prompt_tokens=%s, completion_tokens=%s",
                choice.finish_reason,
                getattr(response.usage, "prompt_tokens", "?"),
                getattr(response.usage, "completion_tokens", "?"),
            )

            # 5. 记录 Prompt 日志（仅在配置启用时）
            if settings.enable_prompt_logging:
                self._last_prompt_log = self._build_prompt_log_data(
                    context=context,
                    system_prompt=system_prompt,
                    chat_messages=chat_messages,
                    tools_schema=tools_schema,
                    response=response,
                    choice=choice,
                    message=message,
                )

            # 6. 按优先级解析返回结果
            return self._parse_response(message, context)

        except Exception as e:
            logger.error("[OpenAI] LLM 调用失败: %s", e, exc_info=True)
            return DecisionResult(
                action_type=ActionType.CONCLUDE,
                thinking=f"LLM 调用异常: {str(e)}",
                conclusion=(
                    f"诊断过程中 LLM 服务调用失败: {str(e)}\n\n"
                    f"用户原始问题: {context.user_query}\n\n"
                    f"建议：请检查 LLM API 配置后重新创建诊断任务。"
                ),
            )

    # ==================== 消息构建 ====================

    def _build_chat_messages(
        self, context: DecisionContext, system_prompt: str,
    ) -> List[Dict[str, Any]]:
        """
        将 DecisionContext.messages 转换为 OpenAI chat messages 格式

        映射规则：
        - system prompt  → role="system" （动态生成，包含工具列表）
        - role="user"             → role="user"
        - role="assistant"(思考)  → role="assistant" (带 tool_calls 或纯文本)
        - role="function_call"    → 跳过（信息已在 assistant+tool_calls 中）
        - role="function_result"  → role="tool"（匹配对应的 tool_call_id）
        """
        chat_messages: List[Dict[str, Any]] = []

        # 系统提示词（包含工具列表描述）
        chat_messages.append({
            "role": "system",
            "content": system_prompt,
        })

        for msg in context.messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "user":
                chat_messages.append({
                    "role": "user",
                    "content": content,
                })

            elif role == "assistant":
                action_type = msg.get("action_type")
                tool_name = msg.get("tool_name")
                tool_arguments = msg.get("tool_arguments")

                if action_type == "tool_call" and tool_name:
                    # 历史 LLM_THINKING（决定调用工具）→ assistant + tool_calls
                    thinking = msg.get("content", "") or ""
                    call_id = f"call_{msg.get('stage_seq', 0)}"
                    chat_messages.append({
                        "role": "assistant",
                        "content": thinking or None,
                        "tool_calls": [{
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(
                                    tool_arguments or {},
                                    ensure_ascii=False,
                                ),
                            },
                        }],
                    })
                else:
                    # 普通 assistant 消息（包括中间思考和结论）
                    if content:
                        chat_messages.append({
                            "role": "assistant",
                            "content": content,
                        })

            elif role == "system":
                # CONTEXT_SUMMARY → 诊断历史摘要，作为 system 消息插入
                chat_messages.append({
                    "role": "system",
                    "content": content,
                })

            elif role == "function_call":
                # TOOL_CALL stage → 信息已在 assistant+tool_calls 中表达，跳过
                pass

            elif role == "function_result":
                # TOOL_RESULT → OpenAI tool role
                tool_name = msg.get("tool_name", "unknown")
                tool_call_id = self._find_tool_call_id(chat_messages, tool_name)

                chat_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": content or "（无返回结果）",
                })

        # 保底：确保至少有一条 user 消息
        has_user_msg = any(m.get("role") == "user" for m in chat_messages)
        if not has_user_msg and context.user_query:
            chat_messages.append({
                "role": "user",
                "content": context.user_query,
            })

        return chat_messages

    def _find_tool_call_id(
        self, chat_messages: List[Dict[str, Any]], tool_name: str,
    ) -> str:
        """从 chat_messages 中反向查找匹配 tool_name 的 tool_call_id"""
        for msg in reversed(chat_messages):
            for tc in msg.get("tool_calls", []):
                if tc.get("function", {}).get("name") == tool_name:
                    return tc.get("id", f"call_{tool_name}")
        return f"call_{tool_name}"

    # ==================== Tools Schema 构建 ====================

    def _build_tools_schema(
        self, available_tools: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        将 MCP 工具列表转换为 OpenAI function calling 格式

        MCP 格式 (tools/list):          OpenAI 格式 (tools 参数):
        {                                {
          "name": "jvm",                   "type": "function",
          "description": "...",            "function": {
          "inputSchema": {                   "name": "jvm",
            "type": "object",                "description": "...",
            "properties": {...}              "parameters": {
          }                                    "type": "object",
        }                                      "properties": {...}
                                             }
                                           }
                                         }
        """
        if not available_tools:
            return []

        tools = []
        for tool in available_tools:
            name = tool.get("name", "")
            if not name:
                continue

            function_def: Dict[str, Any] = {
                "name": name,
                "description": tool.get("description", f"Arthas {name} 命令"),
            }

            input_schema = tool.get("inputSchema")
            if input_schema:
                # 清理 schema：确保符合 OpenAI 要求
                parameters = {
                    "type": input_schema.get("type", "object"),
                    "properties": input_schema.get("properties", {}),
                }
                if "required" in input_schema:
                    parameters["required"] = input_schema["required"]
                function_def["parameters"] = parameters
            else:
                function_def["parameters"] = {
                    "type": "object",
                    "properties": {},
                }

            tools.append({
                "type": "function",
                "function": function_def,
            })

        return tools

    # ==================== 响应解析（核心） ====================

    def _parse_response(
        self,
        message: Any,
        context: DecisionContext,
    ) -> DecisionResult:
        """
        按优先级解析 LLM 响应，转换为 DecisionResult

        解析优先级（从高到低）：
        1. message.tool_calls 字段（OpenAI 标准 function calling）
        2. 文本中的 JSON 动作指令（fallback 模式）
        3. 纯文本作为最终结论

        Args:
            message: OpenAI ChatCompletionMessage 对象
            context: 当前决策上下文
        """
        text_content = message.content or ""

        # ---- 优先级 1: OpenAI 标准 function calling ----
        if message.tool_calls:
            result = self._parse_function_calling(message.tool_calls, text_content)
            if result:
                return result

        # ---- 优先级 2: 从文本中解析 JSON 动作 ----
        if text_content:
            result = self._parse_text_json(text_content)
            if result:
                return result

        # ---- 优先级 3: 纯文本作为结论 ----
        if text_content:
            logger.info(
                "[OpenAI] 文本非 JSON 格式，视为最终结论: length=%d",
                len(text_content),
            )
            return DecisionResult(
                action_type=ActionType.CONCLUDE,
                thinking="",
                conclusion=text_content,
            )

        # 空响应
        logger.warning("[OpenAI] LLM 返回空响应")
        return DecisionResult(
            action_type=ActionType.CONCLUDE,
            thinking="LLM 返回空响应",
            conclusion=(
                f"诊断引擎未能生成有效响应。\n\n"
                f"用户原始问题: {context.user_query}\n\n"
                f"建议：请重新描述问题后再试。"
            ),
        )

    def _parse_function_calling(
        self, tool_calls: List[Any], thinking: str,
    ) -> Optional[DecisionResult]:
        """
        解析 OpenAI 标准 function calling 响应

        Args:
            tool_calls: message.tool_calls 列表
            thinking: message.content 中的思考文本
        """
        tool_call = tool_calls[0]  # 每次只取第一个
        func = tool_call.function

        try:
            arguments = json.loads(func.arguments) if func.arguments else {}
        except json.JSONDecodeError:
            logger.warning(
                "[OpenAI] function calling arguments 解析失败: %s",
                func.arguments,
            )
            arguments = {}

        logger.info(
            "[OpenAI] [function_calling] 调用工具: name=%s, args=%s",
            func.name, arguments,
        )

        return DecisionResult(
            action_type=ActionType.TOOL_CALL,
            tool_name=func.name,
            tool_arguments=arguments,
            thinking=thinking,
        )

    def _parse_text_json(self, text: str) -> Optional[DecisionResult]:
        """
        从 LLM 文本输出中解析 JSON 格式的动作指令（fallback 模式）

        期望的 JSON 格式：
        - 工具调用: {"action": "tool_call", "tool_name": "xxx", "tool_arguments": {...}, "thinking": "..."}
        - 最终结论: {"action": "conclude", "thinking": "...", "conclusion": "..."}
        """
        parsed = parse_text_response(text)
        if not parsed:
            return None

        action = parsed.get("action", "")

        if action == "tool_call":
            tool_name = parsed.get("tool_name", "")
            if not tool_name:
                logger.warning("[OpenAI] [text_json] tool_call 缺少 tool_name")
                return None

            tool_arguments = parsed.get("tool_arguments", {})
            if not isinstance(tool_arguments, dict):
                tool_arguments = {}

            thinking = parsed.get("thinking", "")

            logger.info(
                "[OpenAI] [text_json] 调用工具: name=%s, args=%s",
                tool_name, tool_arguments,
            )

            return DecisionResult(
                action_type=ActionType.TOOL_CALL,
                tool_name=tool_name,
                tool_arguments=tool_arguments,
                thinking=thinking,
            )

        elif action == "conclude":
            conclusion = parsed.get("conclusion", "")
            thinking = parsed.get("thinking", "")

            if not conclusion:
                logger.warning("[OpenAI] [text_json] conclude 缺少 conclusion")
                return None

            logger.info(
                "[OpenAI] [text_json] 最终结论: length=%d", len(conclusion),
            )

            return DecisionResult(
                action_type=ActionType.CONCLUDE,
                thinking=thinking,
                conclusion=conclusion,
            )

        else:
            logger.warning("[OpenAI] [text_json] 未知 action: %s", action)
            return None

    # ==================== Prompt 日志 ====================

    @property
    def last_prompt_log(self) -> Optional[Dict[str, Any]]:
        """
        获取最近一次 decide 调用的 prompt 日志数据。

        调用方（LlmThinkingHandler）在 decide 后读取此属性并写入 DB。
        每次 decide 调用后自动更新，调用方读取后应视为已消费。

        Returns:
            日志数据字典，如果 prompt 日志未启用则返回 None
        """
        return self._last_prompt_log

    @property
    def pending_summary(self) -> Optional[Dict[str, Any]]:
        """
        获取最近一次 decide 调用中产生的待持久化上下文摘要数据。

        调用方（LlmThinkingHandler）在 decide 后根据 LLM 的决策结果决定是否持久化：
        - LLM 返回 tool_call → 调用 persist_pending_summary() 持久化
        - LLM 返回 conclude → 不持久化，摘要数据被丢弃

        Returns:
            待持久化的摘要数据字典，如果没有待持久化的摘要则返回 None
        """
        return self._pending_summary

    async def persist_pending_summary(self) -> None:
        """
        持久化待处理的上下文摘要到数据库。

        应在 LLM 返回 tool_call 时调用，确保摘要在后续推理中可用。
        """
        if self._pending_summary is None:
            return
        await self._context_window_manager._full_summary.persist_summary(
            self._pending_summary
        )
        logger.info(
            "[OpenAI] 延迟持久化 CONTEXT_SUMMARY: task_id=%s",
            self._pending_summary.get("task_id"),
        )
        self._pending_summary = None  # 消费后清空

    def _build_prompt_log_data(
        self,
        context: DecisionContext,
        system_prompt: str,
        chat_messages: List[Dict[str, Any]],
        tools_schema: List[Dict[str, Any]],
        response: Any,
        choice: Any,
        message: Any,
    ) -> Dict[str, Any]:
        """
        构建 prompt 日志数据字典。

        Args:
            context: 决策上下文
            system_prompt: 系统提示词
            chat_messages: 完整的 chat messages
            tools_schema: tools schema
            response: OpenAI 完整响应对象
            choice: response.choices[0]
            message: choice.message

        Returns:
            适合传给 repo.save_prompt_log 的字典
        """
        # 序列化 tool_calls
        tool_calls_data = None
        if message.tool_calls:
            tool_calls_data = [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]

        return {
            "task_id": context.task_id,
            "stage_seq": context.current_stage_seq,
            "model": self._model,
            "system_prompt": system_prompt,
            "chat_messages": chat_messages,
            "tools_schema": tools_schema,
            "response_content": message.content or "",
            "response_tool_calls": tool_calls_data,
            "finish_reason": choice.finish_reason,
            "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
            "completion_tokens": getattr(response.usage, "completion_tokens", None),
            "total_tokens": getattr(response.usage, "total_tokens", None),
        }
