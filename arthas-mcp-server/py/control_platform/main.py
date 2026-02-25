"""
Arthas 管控平台 - FastAPI 主应用

基于 FastAPI + WebSocket 的管控服务入口，承担以下职责：
1. WebSocket 端点 /mcp：接收 Arthas MCP Client 的反向连接
2. REST API 端点：提供会话查询、诊断任务管理、审核管理等
3. 启动时初始化数据库、组件并启动后台轮询任务

使用方式：
    cd py/control_platform
    pip install -r requirements.txt
    python -m control_platform.main
    # 或
    uvicorn control_platform.main:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Header, HTTPException
from pydantic import BaseModel

from control_platform.config import settings
from control_platform.db.database import init_db, close_db
from control_platform.db.models import StageStatus, StageType
from control_platform.db.repository import DiagnosisRepository
from control_platform.decision.context_builder import ContextBuilder
from control_platform.decision.noop_engine import MockDecisionEngine
from control_platform.decision.openai_engine import OpenAIDecisionEngine, build_system_prompt
from control_platform.event.handler import (
    LlmConclusionHandler,
    LlmThinkingHandler,
    StageHandlerRegistry,
    ToolCallHandler,
    ToolResultHandler,
    UserQueryHandler,
)
from control_platform.event.scheduler import EventScheduler
from control_platform.executor.task_pool import TaskPool
from control_platform.lock.base import TaskLockNotAcquired
from control_platform.lock.local_lock import LocalTaskLock
from control_platform.models.event import (
    DiagnosisProgressSchema,
    DiagnosisStageSchema,
    DiagnosisTaskSchema,
    DiagnosisTaskSummarySchema,
)
from control_platform.models.task import CreateDiagnosisRequest
from control_platform.protocol.jsonrpc import parse_message
from control_platform.models.action import ActionType
from control_platform.protocol.mcp_handler import McpHandler
from control_platform.session.session_manager import SessionManager

# ========== 日志配置 ==========
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("control_platform")

# ========== 全局组件 ==========
mcp_handler: Optional[McpHandler] = None
session_manager: Optional[SessionManager] = None
task_lock: Optional[LocalTaskLock] = None
task_pool: Optional[TaskPool] = None
event_scheduler: Optional[EventScheduler] = None
handler_registry: Optional[StageHandlerRegistry] = None
decision_engine: Optional[Any] = None  # DecisionEngine 实例（Mock 或 OpenAI）
context_builder: Optional[ContextBuilder] = None
diagnosis_repo: Optional[DiagnosisRepository] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理

    启动时初始化数据库和所有组件，关闭时优雅停止。
    """
    global mcp_handler, session_manager, task_lock
    global task_pool
    global event_scheduler, handler_registry
    global decision_engine, context_builder, diagnosis_repo

    logger.info("=" * 60)
    logger.info("  Arthas 管控平台启动中...")
    logger.info("=" * 60)

    # 1. 初始化数据库（自动建表）
    await init_db()
    logger.info("✅ 数据库初始化完成")

    # 2. 初始化诊断仓储层
    diagnosis_repo = DiagnosisRepository()

    # 3. 初始化 MCP 协议处理器
    mcp_handler = McpHandler(
        on_initialized=_on_client_initialized,
    )

    # 4. 初始化会话管理器
    session_manager = SessionManager(mcp_handler)

    # 5. 初始化任务锁
    # 注意：context_builder 的 on_unregister 回调在步骤 7 创建后注册
    task_lock = LocalTaskLock(ttl=300.0)

    # 6. 初始化执行池（占位，handler_registry 尚未初始化，后续注入）
    task_pool = TaskPool(
        max_concurrency=settings.task_pool_max_concurrency,
        repo=diagnosis_repo,
        handler_registry=None,  # 稍后注入
        task_lock=task_lock,
    )

    # 7. 初始化决策引擎和上下文构建器
    if settings.llm_engine == "openai" and settings.llm_api_key:
        decision_engine = OpenAIDecisionEngine(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
        )
        logger.info(f"✅ 使用 OpenAI 决策引擎: model={settings.llm_model}")
    else:
        decision_engine = MockDecisionEngine()
        logger.info("✅ 使用 Mock 决策引擎（开发模式）")
    context_builder = ContextBuilder()

    # 注册 session 注销回调：断连时自动清理该 session 的工具列表缓存
    session_manager.add_on_unregister_callback(context_builder.remove_session_tools)

    # 8. 初始化阶段处理器注册表，注册所有处理器
    handler_registry = StageHandlerRegistry()
    handler_registry.register(
        StageType.USER_QUERY.value,
        UserQueryHandler(),
    )
    handler_registry.register(
        StageType.LLM_THINKING.value,
        LlmThinkingHandler(
            decision_engine=decision_engine,
            context_builder=context_builder,
        ),
    )
    handler_registry.register(
        StageType.TOOL_CALL.value,
        ToolCallHandler(
            session_manager=session_manager,
            mcp_handler=mcp_handler,
        ),
    )
    handler_registry.register(
        StageType.TOOL_RESULT.value,
        ToolResultHandler(),
    )
    handler_registry.register(
        StageType.LLM_CONCLUSION.value,
        LlmConclusionHandler(),
    )

    # 9. 注入 handler_registry 到执行池（解决循环初始化问题）
    task_pool._handler_registry = handler_registry

    # 10. 初始化事件调度器（纯轮询 + 提交到 Pool）
    event_scheduler = EventScheduler(
        repo=diagnosis_repo,
        pool=task_pool,
        session_manager=session_manager,
    )

    # 11. 启动时检查并标记失败任务（故障恢复）
    failed_count = await diagnosis_repo.check_and_fail_stale_tasks()
    if failed_count > 0:
        logger.info(f"⚠️ 启动恢复：标记了 {failed_count} 个失败任务")

    # 12. 启动后台任务
    session_manager.start_heartbeat()
    event_scheduler.start()

    logger.info(f"  端口: {settings.port}")
    logger.info(f"  认证: {'已启用' if settings.auth_token else '未启用'}")
    logger.info(f"  调试: {'已启用' if settings.debug else '未启用'}")
    logger.info(f"  决策引擎: {decision_engine.engine_name}")
    logger.info(f"  数据库: {settings.db_url}")
    logger.info(f"  事件轮询间隔: {settings.event_poll_interval}s")
    logger.info(f"  需审核命令: {settings.commands_requiring_approval}")
    logger.info("=" * 60)
    logger.info(f"🚀 管控平台已启动: ws://0.0.0.0:{settings.port}/mcp")

    yield  # 应用运行中

    # 关闭阶段
    logger.info("管控平台关闭中...")
    event_scheduler.stop()
    await task_pool.shutdown()
    await session_manager.close_all()
    await close_db()
    logger.info("管控平台已关闭")


def _on_client_initialized(session_id: str) -> None:
    """
    客户端初始化完成回调（由 McpHandler 触发）

    完成两件事：
    1. 标记 session 为已初始化
    2. 立即拉取客户端的可用工具列表
       解决「创建任务时客户端未连接，后续无法获取工具」的问题
    """
    loop = asyncio.get_event_loop()
    if loop.is_running():
        asyncio.ensure_future(session_manager.mark_initialized(session_id))
        asyncio.ensure_future(_fetch_tools_on_initialized(session_id))


async def _fetch_tools_on_initialized(session_id: str) -> None:
    """
    客户端初始化完成后拉取工具列表。

    等待 session 注册完毕后，尝试拉取工具列表。
    失败不影响 session 使用，后续 create_diagnosis 仍可兗底拉取。
    """
    try:
        # 短暂等待确保 session 已注册到 session_manager
        await asyncio.sleep(0.1)
        client_session = await session_manager.get_session(session_id)
        if client_session:
            await _fetch_tools_if_needed(client_session, session_id)
        else:
            logger.warning(
                f"初始化回调中未找到 session: {session_id[:8]}，跳过工具拉取"
            )
    except Exception as e:
        logger.warning(
            f"初始化回调中拉取工具列表失败（不影响使用）: "
            f"session={session_id[:8]}, error={e}"
        )


async def _handle_tool_call_response(
    response: Dict[str, Any],
    task_id: str,
    stage_seq: int,
    session_id: str,
) -> None:
    """
    处理工具调用的异步 WS 响应（由 WebSocket 回调驱动）

    收到 Java 端的工具调用响应后：
    1. 获取 TaskLock 保护（使用统一的 locked() 上下文管理器）
    2. 通过 task_id + stage_seq 从数据库查到具体的 TOOL_CALL stage
    3. 解析响应，提取工具执行结果
    4. 完成 TOOL_CALL stage，创建 TOOL_RESULT stage
    5. 锁释放后，通过 task_pool.submit 投递到执行池加速执行

    完全无内存状态依赖，所有信息从数据库获取。
    如果 stage 已被处理（并发重复响应），complete_and_next 会抛异常，
    catch 后忽略即可（幂等安全）。
    """
    logger.info(
        f"收到工具调用异步响应: task_id={task_id}, stage_seq={stage_seq}, "
        f"session={session_id[:8]}"
    )

    next_stage = None
    try:
        async with task_lock.locked(task_id):
            # 1. 从数据库查 stage
            stage = await diagnosis_repo.get_stage_by_task_and_seq(task_id, stage_seq)
            if stage is None:
                logger.warning(
                    f"未找到对应的 stage: task_id={task_id}, stage_seq={stage_seq}"
                )
                return

            stage_id = stage.id
            tool_name = stage.tool_name or ""
            tool_arguments = stage.tool_arguments or {}

            # 2. 检查 stage 状态，如果已经不是 PENDING 则跳过（幂等保护）
            if stage.status != StageStatus.PENDING.value:
                logger.info(
                    f"stage 已被处理，跳过: task_id={task_id}, stage_seq={stage_seq}, "
                    f"status={stage.status}"
                )
                return

            # 3. 检查响应是否有错误
            if "error" in response:
                error_info = response["error"]
                error_msg = f"工具调用返回错误: {error_info.get('message', str(error_info))}"
                logger.warning(error_msg)
                is_final = await diagnosis_repo.mark_failed(stage_id, error_msg)
                if is_final:
                    await diagnosis_repo.fail_task(task_id)
                return

            # 4. 提取工具执行结果
            result = response.get("result", {})
            tool_result_text = ToolCallHandler.extract_tool_result(result)

            # 4.1 过滤 Java 端 TaskStageTracker 的重复执行错误
            #     当调度器重发 TOOL_CALL 请求时，Java 端幂等保护会返回
            #     "Error: Stage is already executing" 错误（isError=true）。
            #     这不是真正的工具执行失败，应忽略此响应，等待真正的执行结果。
            is_error = result.get("isError", False)
            if is_error and "Stage is already executing" in tool_result_text:
                logger.info(
                    f"忽略重复执行错误响应: task_id={task_id}, "
                    f"stage_seq={stage_seq}"
                )
                return

            # 5. 完成 TOOL_CALL stage，创建 TOOL_RESULT stage
            next_stage = await diagnosis_repo.complete_and_next(
                stage_id=stage_id,
                output_data={
                    "tool_name": tool_name,
                    "tool_arguments": tool_arguments,
                    "tool_result": tool_result_text,
                    "raw_response": result,
                },
                next_stage_type=StageType.TOOL_RESULT.value,
                next_input_data={
                    "tool_name": tool_name,
                    "tool_arguments": tool_arguments,
                    "tool_result": tool_result_text,
                },
                tool_result=tool_result_text,
            )

            logger.info(
                f"工具调用完成，已创建 TOOL_RESULT stage: task_id={task_id}, "
                f"tool={tool_name}, result_length={len(tool_result_text)}"
            )

    except TaskLockNotAcquired:
        logger.info(
            f"⏭️ 任务锁已被占用，跳过 WS 回调处理: task_id={task_id}, "
            f"stage_seq={stage_seq}，将由定时轮询兜底"
        )
        return
    except ValueError as e:
        # stage 已经被处理过（幂等保护），忽略
        logger.info(
            f"工具调用响应被忽略（stage 可能已被处理）: task_id={task_id}, "
            f"stage_seq={stage_seq}, error={e}"
        )
    except Exception as e:
        logger.error(
            f"处理工具调用异步响应失败: task_id={task_id}, "
            f"stage_seq={stage_seq}, error={e}",
            exc_info=True,
        )
        try:
            # 兜底标记失败，需要在锁保护下操作
            async with task_lock.locked(task_id):
                stage = await diagnosis_repo.get_stage_by_task_and_seq(task_id, stage_seq)
                if stage and stage.status == StageStatus.PENDING.value:
                    is_final = await diagnosis_repo.mark_failed(stage.id, str(e))
                    if is_final:
                        await diagnosis_repo.fail_task(task_id)
        except TaskLockNotAcquired:
            logger.info(
                f"⏭️ 兜底标记失败时锁被占用，跳过: task_id={task_id}, "
                f"stage_seq={stage_seq}，将由定时轮询兜底"
            )
        except Exception as inner_e:
            logger.error(f"兜底标记失败也失败了: {inner_e}", exc_info=True)

    # 锁已在 async with 退出时释放，如果有 next_stage 则投递到执行池
    if next_stage:
        task = await diagnosis_repo.get_task(task_id)
        if task:
            await task_pool.submit(task, next_stage)
            logger.info(
                f"🚀 TOOL_RESULT 已投递到执行池: task_id={task_id}"
            )


# ========== FastAPI 应用 ==========
app = FastAPI(
    title="Arthas Control Platform",
    version="0.2.0",
    description="Arthas 管控平台 - 基于 FastAPI + WebSocket + 事件驱动状态机",
    lifespan=lifespan,
)


# ========== WebSocket 端点 ==========

@app.websocket("/mcp")
async def websocket_mcp_endpoint(
    websocket: WebSocket,
    sessionId: Optional[str] = Query(None, alias="sessionId"),
):
    """
    MCP WebSocket 端点

    处理 Arthas MCP Client 的反向 WebSocket 连接。
    """
    session_id = sessionId
    if not session_id:
        session_id = websocket.headers.get("mcp-session-id", "")
    if not session_id:
        logger.warning("未提供 sessionId，拒绝连接")
        await websocket.close(code=4000, reason="Missing sessionId")
        return

    if settings.auth_token:
        auth_header = websocket.headers.get("authorization", "")
        if not auth_header.startswith("Bearer ") or auth_header[7:] != settings.auth_token:
            logger.warning(f"认证失败: session={session_id[:8]}")
            await websocket.close(code=4001, reason="Unauthorized")
            return

    await websocket.accept()
    logger.info(f"✅ WebSocket 连接建立: session={session_id[:8]}")
    session = await session_manager.register(session_id, websocket)

    try:
        while True:
            raw_data = await websocket.receive_text()
            try:
                msg = parse_message(raw_data)
            except ValueError as e:
                logger.warning(f"无效消息: {e} (session={session_id[:8]})")
                continue

            if msg.is_request:
                response = mcp_handler.handle_request(msg, session_id)
                if response:
                    if msg.method == "initialize":
                        session.client_info = (msg.params or {}).get("clientInfo", {})
                    await session.send_message(response)
            elif msg.is_notification:
                mcp_handler.handle_notification(msg, session_id)
            elif msg.is_response:
                # 优先走 pending_futures 匹配（tools/list、ping 等有 future 等待的请求）
                matched = session.resolve_response(msg.request_id, msg.raw)
                if not matched:
                    # 未匹配到 future，尝试从响应 _meta 中提取 taskId + stageId
                    # 如果有则是异步工具调用的响应，由 WS 回调驱动后续流程
                    result_data = msg.raw.get("result", {})
                    task_id, stage_seq_str = McpHandler.extract_task_stage_from_response(result_data)
                    if task_id and stage_seq_str:
                        asyncio.ensure_future(
                            _handle_tool_call_response(
                                response=msg.raw,
                                task_id=task_id,
                                stage_seq=int(stage_seq_str),
                                session_id=session_id,
                            )
                        )
                    else:
                        logger.debug(f"未匹配的响应: id={msg.request_id} (session={session_id[:8]})")

    except WebSocketDisconnect:
        logger.info(f"📪 WebSocket 断开: session={session_id[:8]}")
    except Exception as e:
        logger.error(f"WebSocket 错误: {e} (session={session_id[:8]})", exc_info=True)
    finally:
        await session_manager.unregister(session_id)


# ========== 会话管理 REST API ==========

@app.get("/api/sessions", summary="获取活跃会话列表")
async def get_sessions():
    """返回所有活跃且已初始化的会话信息"""
    sessions = await session_manager.get_all_active_sessions()
    return {
        "total": len(sessions),
        "sessions": [s.to_dict() for s in sessions],
    }


@app.get("/api/status", summary="获取平台状态")
async def get_status():
    """返回管控平台的运行状态"""
    return {
        "status": "running",
        "sessions": {
            "total": session_manager.session_count,
            "active": session_manager.active_session_count,
        },
        "pools": {
            "task_pool": {
                "running": task_pool.running_count,
                "max_concurrency": task_pool.max_concurrency,
            },
        },
        "locks": {
            "total": task_lock.lock_count,
            "held": task_lock.held_lock_count,
        },
        "event_scheduler": {
            "running": event_scheduler.is_running,
        },
        "database": {
            "url": settings.db_url,
        },
    }


@app.get("/api/health", summary="健康检查")
async def health_check():
    """健康检查端点"""
    return {"status": "ok"}


# ========== 诊断任务 REST API ==========

@app.post("/api/diagnosis", summary="创建诊断任务")
async def create_diagnosis(req: CreateDiagnosisRequest):
    """
    创建一个新的诊断任务。

    在数据库中创建 task + 初始 USER_QUERY stage（status=pending），
    等待定时轮询拾取执行。

    注意：创建任务不要求对应的 session 当前在线，
    任务会在该 session 上线后被轮询拾取执行。
    """
    # 宽松校验：只校验 session_id 格式合法性（非空）
    if not req.session_id or not req.session_id.strip():
        raise HTTPException(
            status_code=400,
            detail="session_id 不能为空",
        )

    # 如果当前 session 在线，尝试拉取客户端工具列表
    target_session = await session_manager.get_session(req.session_id)
    if target_session:
        try:
            await _fetch_tools_if_needed(target_session, req.session_id)
        except Exception as e:
            logger.warning(f"拉取工具列表失败（不影响任务创建）: {e}")

    task = await diagnosis_repo.create_task(
        session_id=req.session_id,
        user_query=req.user_query,
        metadata=req.metadata,
    )

    return {
        "task_id": task.task_id,
        "session_id": task.session_id,
        "status": task.status,
        "message": "诊断任务已创建，等待轮询处理",
    }


@app.get("/api/diagnosis", summary="查询诊断任务列表")
async def list_diagnosis(
    session_id: Optional[str] = Query(None, description="按 session_id 筛选"),
    status: Optional[str] = Query(None, description="按状态筛选"),
    start_time: Optional[str] = Query(None, description="创建时间起始 (ISO 格式)"),
    end_time: Optional[str] = Query(None, description="创建时间截止 (ISO 格式)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """查询诊断任务列表，支持多条件筛选"""
    parsed_start = datetime.fromisoformat(start_time) if start_time else None
    parsed_end = datetime.fromisoformat(end_time) if end_time else None

    tasks = await diagnosis_repo.get_tasks(
        session_id=session_id,
        status=status,
        start_time=parsed_start,
        end_time=parsed_end,
        limit=limit,
        offset=offset,
    )

    return {
        "total": len(tasks),
        "tasks": [DiagnosisTaskSummarySchema.model_validate(t) for t in tasks],
    }


@app.delete("/api/diagnosis/{task_id}", summary="删除诊断任务")
async def delete_diagnosis(task_id: str):
    """删除诊断任务及其所有关联数据（stages、prompt 日志）。需要先获取任务锁，防止删除正在执行的任务。"""
    try:
        async with task_lock.locked(task_id):
            deleted = await diagnosis_repo.delete_task(task_id)
            if not deleted:
                raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
            return {"message": "任务已删除", "task_id": task_id}
    except TaskLockNotAcquired:
        raise HTTPException(
            status_code=409,
            detail=f"任务正在执行中，无法删除: {task_id}",
        )


@app.get("/api/diagnosis/{task_id}", summary="获取诊断任务详情")
async def get_diagnosis(task_id: str):
    """
    查询诊断任务详情，包含完整的 stage 时间线。

    返回的 stages 按 stage_seq 排序，每个 stage 格式化为时间线视图。
    """
    task = await diagnosis_repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    stages = await diagnosis_repo.get_task_stages(task_id)

    # 格式化 stage 时间线
    timeline = _render_timeline(stages)

    return {
        "task": DiagnosisTaskSummarySchema.model_validate(task),
        "stages": [DiagnosisStageSchema.model_validate(s) for s in stages],
        "timeline": timeline,
    }


@app.get("/api/diagnosis/{task_id}/progress", summary="查询诊断进度")
async def get_diagnosis_progress(task_id: str):
    """查询诊断任务的实时进度"""
    task = await diagnosis_repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    stages = await diagnosis_repo.get_task_stages(task_id)
    completed_count = sum(
        1 for s in stages if s.status == StageStatus.COMPLETED.value
    )

    # 获取当前 stage 信息
    latest = stages[-1] if stages else None

    return DiagnosisProgressSchema(
        task_id=task_id,
        status=task.status,
        total_stages=len(stages),
        completed_stages=completed_count,
        current_stage_seq=task.current_stage_seq,
        current_stage_type=latest.stage_type if latest else None,
        current_stage_status=latest.status if latest else None,
    )


@app.get("/api/diagnosis/{task_id}/conversation", summary="查询诊断完整对话文本")
async def get_diagnosis_conversation(task_id: str):
    """
    返回该诊断任务从输入到结论的完整对话文本。

    从数据库加载所有已完成的 stages，利用 ContextBuilder + build_system_prompt
    复现实际发送给 LLM 的完整 OpenAI messages，然后渲染为人类可读的纯文本。

    不依赖 llm_prompt_log 表，随时可用。
    """
    task = await diagnosis_repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    # 1. 构建 DecisionContext（复用已有逻辑）
    ctx = await context_builder.build_context(task_id, diagnosis_repo)

    # 2. 构建 system prompt
    system_prompt = build_system_prompt(ctx.available_tools)

    # 3. 构建 OpenAI chat messages（和实际发给 LLM 的一模一样）
    #    注意：decision_engine 可能是 Mock，直接用 OpenAIDecisionEngine 的静态方法
    chat_messages = _build_chat_messages_for_display(ctx, system_prompt)

    # 4. 渲染为人类可读的完整对话文本
    conversation_text = _render_conversation_text(chat_messages, task)

    return {
        "task_id": task_id,
        "status": task.status,
        "session_id": task.session_id,
        "user_query": task.user_query,
        "conversation_text": conversation_text,
    }


def _build_chat_messages_for_display(
    context: DecisionContext, system_prompt: str,
) -> List[Dict[str, Any]]:
    """
    将 DecisionContext.messages 转换为 OpenAI chat messages 格式（用于展示）。

    逻辑与 OpenAIDecisionEngine._build_chat_messages 完全一致，
    独立实现是为了不依赖具体的引擎实例。
    """
    chat_messages: List[Dict[str, Any]] = []

    # 系统提示词
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
            pass

        elif role == "function_result":
            tool_name = msg.get("tool_name", "unknown")
            # 反向查找匹配的 tool_call_id
            tool_call_id = f"call_{tool_name}"
            for m in reversed(chat_messages):
                for tc in m.get("tool_calls", []):
                    if tc.get("function", {}).get("name") == tool_name:
                        tool_call_id = tc.get("id", tool_call_id)
                        break

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


def _render_conversation_text(
    chat_messages: List[Dict[str, Any]],
    task: Any,
) -> str:
    """
    将 OpenAI chat messages 渲染为人类可读的完整对话文本。

    格式类似于：
    ================ SYSTEM ================
    <系统提示词>

    ================ USER ================
    <用户提问>

    ================ ASSISTANT ================
    <LLM 思考 + tool_calls>

    ================ TOOL (thread) ================
    <工具返回结果>

    ================ ASSISTANT ================
    <最终结论>
    """
    lines = []
    separator = "=" * 50

    lines.append(f"诊断任务: {task.task_id}")
    lines.append(f"会话: {task.session_id}")
    lines.append(f"状态: {task.status}")
    lines.append(f"用户问题: {task.user_query}")
    lines.append(separator)
    lines.append("")

    for msg in chat_messages:
        role = msg.get("role", "unknown").upper()
        content = msg.get("content", "") or ""
        tool_calls = msg.get("tool_calls", [])
        tool_call_id = msg.get("tool_call_id", "")

        if role == "TOOL":
            tool_name = ""
            # 从 tool_call_id 提取工具名（格式: call_N 或 call_toolName）
            if tool_call_id:
                tool_name = tool_call_id.replace("call_", "", 1)
            lines.append(f"[TOOL 返回结果: {tool_name}]")
        elif role == "ASSISTANT" and tool_calls:
            lines.append(f"[{role}]")
            if content:
                lines.append(content)
            for tc in tool_calls:
                func = tc.get("function", {})
                lines.append(f"  → 调用工具: {func.get('name', '?')}")
                lines.append(f"    参数: {func.get('arguments', '{}')}")
        else:
            lines.append(f"[{role}]")

        # 输出内容（ASSISTANT 带 tool_calls 时内容已在上方输出）
        if content and not (role == "ASSISTANT" and tool_calls):
            lines.append(content)

        lines.append("")  # 空行分隔

    return "\n".join(lines)


# ========== 审核管理 REST API ==========

@app.get("/api/stages/pending-approval", summary="查询待审核阶段")
async def get_pending_approval_stages():
    """查询所有等待人工审核的阶段"""
    stages = await diagnosis_repo.get_pending_approval_stages()
    return {
        "total": len(stages),
        "stages": [DiagnosisStageSchema.model_validate(s) for s in stages],
    }


class ApprovalRequest(BaseModel):
    """审核请求"""
    approved_by: str = "admin"


@app.post("/api/stages/{stage_id}/approve", summary="审核通过")
async def approve_stage(stage_id: int, req: ApprovalRequest = ApprovalRequest()):
    """
    审核通过指定的 stage。

    将 approval_status 设为 approved，status 改回 pending，
    下次轮询将自动拾取继续执行。
    """
    stage = await diagnosis_repo.get_stage(stage_id)
    if not stage:
        raise HTTPException(status_code=404, detail=f"Stage 不存在: {stage_id}")

    if stage.status != StageStatus.WAITING_APPROVAL.value:
        raise HTTPException(
            status_code=400,
            detail=f"Stage 状态不是 waiting_approval，当前状态: {stage.status}",
        )

    # 使用统一的 locked() 上下文管理器获取锁
    task_id = stage.task_id
    try:
        async with task_lock.locked(task_id):
            # 在锁保护下重新检查状态（double-check）
            stage = await diagnosis_repo.get_stage(stage_id)
            if not stage or stage.status != StageStatus.WAITING_APPROVAL.value:
                raise HTTPException(
                    status_code=400,
                    detail=f"Stage 状态已变更，无法审核",
                )
            await diagnosis_repo.approve_stage(stage_id, approved_by=req.approved_by)
    except TaskLockNotAcquired:
        raise HTTPException(
            status_code=409,
            detail=f"任务正在处理中，请稍后重试: task_id={task_id}",
        )

    # 锁已释放，投递到执行池加速执行（审核通过后 stage 变回 pending，可直接拾取）
    task = await diagnosis_repo.get_task(task_id)
    if task:
        # 重新从数据库读取最新 stage 状态
        stage = await diagnosis_repo.get_stage(stage_id)
        if stage and stage.status == StageStatus.PENDING.value:
            await task_pool.submit(task, stage)

    return {"stage_id": stage_id, "status": "approved", "message": "审核通过，已投递加速执行"}


@app.post("/api/stages/{stage_id}/reject", summary="审核拒绝")
async def reject_stage(stage_id: int, req: ApprovalRequest = ApprovalRequest()):
    """
    审核拒绝指定的 stage。

    将 stage 标记为 failed，并创建新的 LLM_THINKING stage
    让 LLM 知道命令被拒绝并重新决策。
    """
    stage = await diagnosis_repo.get_stage(stage_id)
    if not stage:
        raise HTTPException(status_code=404, detail=f"Stage 不存在: {stage_id}")

    if stage.status != StageStatus.WAITING_APPROVAL.value:
        raise HTTPException(
            status_code=400,
            detail=f"Stage 状态不是 waiting_approval，当前状态: {stage.status}",
        )

    # 使用统一的 locked() 上下文管理器获取锁
    task_id = stage.task_id
    next_stage = None
    try:
        async with task_lock.locked(task_id):
            # 在锁保护下重新检查状态（double-check）
            stage = await diagnosis_repo.get_stage(stage_id)
            if not stage or stage.status != StageStatus.WAITING_APPROVAL.value:
                raise HTTPException(
                    status_code=400,
                    detail=f"Stage 状态已变更，无法审核",
                )
            next_stage = await diagnosis_repo.reject_stage(stage_id, rejected_by=req.approved_by)
    except TaskLockNotAcquired:
        raise HTTPException(
            status_code=409,
            detail=f"任务正在处理中，请稍后重试: task_id={task_id}",
        )

    # 锁已释放，如果有 next_stage 则投递到执行池加速执行
    if next_stage:
        task = await diagnosis_repo.get_task(task_id)
        if task:
            await task_pool.submit(task, next_stage)

    return {
        "stage_id": stage_id,
        "status": "rejected",
        "next_stage_id": next_stage.id if next_stage else None,
        "message": "审核拒绝，已创建新的 LLM_THINKING stage 进行重新决策",
    }


# ========== 工具列表拉取 ==========

async def _fetch_tools_if_needed(client_session, session_id: str) -> None:
    """
    拉取客户端的可用工具列表，并更新到 ContextBuilder。

    每个 session 独立存储工具列表，避免多 session 互相覆盖。

    工具列表用于：
    1. ContextBuilder 构建 DecisionContext.available_tools
    2. OpenAI 决策引擎的 function calling 参数
    """
    # 如果该 session 已经有工具列表，跳过
    if context_builder.has_tools(session_id):
        return

    request_id = client_session.next_request_id()
    request_msg = McpHandler.build_tools_list_request(request_id=request_id)

    response = await client_session.send_and_wait(
        request_msg,
        timeout=settings.default_tool_timeout,
    )

    if response and "result" in response:
        tools = response["result"].get("tools", [])
        context_builder.set_available_tools(session_id, tools)
        logger.info(f"✅ 已获取客户端工具列表: session={session_id[:8]}, {len(tools)} 个工具")
    else:
        logger.warning(f"拉取工具列表失败：未收到有效响应，session={session_id[:8]}")


# ========== 时间线渲染 ==========

def _render_timeline(stages) -> List[Dict[str, Any]]:
    """
    将 stage 列表渲染为时间线视图。

    类似 MCP 中渲染模型调用工具的过程展示，
    每种 stage_type 有不同的展示格式。
    """
    timeline = []
    for stage in stages:
        entry = {
            "stage_seq": stage.stage_seq,
            "stage_type": stage.stage_type,
            "status": stage.status,
            "created_at": stage.created_at.isoformat() if stage.created_at else None,
            "updated_at": stage.updated_at.isoformat() if stage.updated_at else None,
        }

        input_data = stage.input_data or {}
        output_data = stage.output_data or {}

        if stage.stage_type == StageType.USER_QUERY.value:
            entry["display"] = {
                "icon": "💬",
                "title": "用户提问",
                "content": input_data.get("user_query", ""),
            }

        elif stage.stage_type == StageType.LLM_THINKING.value:
            thinking_text = output_data.get("thinking", "")
            action_type = output_data.get("action_type", "")
            # 当 LLM 直接给出结论（action_type=conclude）但 thinking 为空时，
            # 设置默认文案，避免前端展示空白的推理阶段
            if not thinking_text and action_type == ActionType.CONCLUDE.value:
                thinking_text = "正在整理诊断结论..."
            entry["display"] = {
                "icon": "🤔",
                "title": "AI 推理",
                "thinking": thinking_text,
                "action_type": action_type,
                "tool_name": output_data.get("tool_name"),
            }

        elif stage.stage_type == StageType.TOOL_CALL.value:
            # TOOL_CALL display 只展示工具名和参数，不展示 tool_result
            # 工具执行结果统一在 TOOL_RESULT stage 的 display 中展示，避免重复
            entry["display"] = {
                "icon": "🔧",
                "title": f"执行命令: {stage.tool_name or ''}",
                "tool_name": stage.tool_name,
                "tool_arguments": stage.tool_arguments,
                "approval_status": stage.approval_status,
                "approved_by": stage.approved_by,
            }

        elif stage.stage_type == StageType.TOOL_RESULT.value:
            entry["display"] = {
                "icon": "📋",
                "title": f"命令结果: {input_data.get('tool_name', '')}",
                "tool_name": input_data.get("tool_name", ""),
                "content": input_data.get("tool_result", ""),
            }

        elif stage.stage_type == StageType.LLM_CONCLUSION.value:
            entry["display"] = {
                "icon": "✅",
                "title": "诊断结论",
                "conclusion": output_data.get("conclusion", ""),
                "thinking": output_data.get("thinking", ""),
            }

        # 错误信息
        if stage.error_message:
            entry["error"] = {
                "message": stage.error_message,
                "retry_count": stage.retry_count,
                "max_retries": stage.max_retries,
            }

        timeline.append(entry)

    return timeline


# ========== 入口 ==========

def main():
    """管控平台启动入口"""
    uvicorn.run(
        "control_platform.main:app",
        host=settings.host,
        port=settings.port,
        log_level="debug" if settings.debug else "info",
        ws_ping_interval=settings.ws_ping_interval,
        ws_ping_timeout=settings.ws_ping_timeout,
    )


if __name__ == "__main__":
    main()
