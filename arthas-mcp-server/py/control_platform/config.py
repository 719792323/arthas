"""
管控平台配置模块

使用 Pydantic BaseSettings 管理配置项，支持环境变量覆盖。
环境变量前缀: CP_ (Control Platform)
"""
import os

from pydantic_settings import BaseSettings
from pydantic import Field, field_validator


class Settings(BaseSettings):
    """管控平台全局配置"""

    # ========== 服务基础配置 ==========
    # 服务监听端口
    port: int = Field(default=8080, description="服务监听端口")
    # 服务监听地址
    host: str = Field(default="0.0.0.0", description="服务监听地址")

    # ========== 认证配置 ==========
    # 认证 Token，为空则不启用认证
    auth_token: str = Field(default="", description="Bearer Token 认证密钥，为空则不启用")

    # ========== WebSocket 心跳配置 ==========
    # WebSocket 传输层 ping 间隔（秒）
    ws_ping_interval: int = Field(default=30, description="WebSocket 传输层 ping 间隔（秒）")
    # WebSocket 传输层 ping 超时（秒）
    ws_ping_timeout: int = Field(default=10, description="WebSocket 传输层 ping 超时（秒）")
    # MCP 层心跳检测间隔（秒）
    mcp_heartbeat_interval: int = Field(default=30, description="MCP 层心跳检测间隔（秒）")
    # MCP 层心跳超时（秒）
    mcp_heartbeat_timeout: int = Field(default=10, description="MCP 层心跳超时（秒）")

    # ========== 事件调度配置 ==========
    # 事件轮询间隔（秒）
    event_poll_interval: float = Field(default=10.0, description="事件轮询间隔（秒）")

    # ========== 执行池配置 ==========
    # 任务执行池最大并发数
    task_pool_max_concurrency: int = Field(default=20, description="任务执行池最大并发数")

    # ========== 工具调用配置 ==========
    # 默认工具调用超时（秒）
    default_tool_timeout: float = Field(default=30.0, description="默认工具调用超时（秒）")
    # 流式/异步工具调用超时（秒）
    streamable_tool_timeout: float = Field(default=60.0, description="流式/异步工具调用超时（秒）")

    # ========== 工具调用冷却配置 ==========
    # TOOL_CALL stage 冷却时间（秒）：冷却期间调度器不会重复发送工具调用请求
    tool_call_cooldown: float = Field(default=60.0, description="TOOL_CALL stage 冷却时间（秒），防止重复发送")

    # ========== 命令审核配置 ==========
    # 需要人工审核的高危命令列表
    commands_requiring_approval: list = Field(
        default=["heapdump", "redefine", "retransform", "reset", "stop", "shutdown"],
        description="需要人工审核才能执行的高危 Arthas 命令列表"
    )

    # ========== LLM 决策引擎配置 ==========
    # 决策引擎类型: mock（无需 LLM）或 openai（调用 LLM API）
    llm_engine: str = Field(default="openai", description="决策引擎类型: mock 或 openai")
    # LLM API 密钥
    llm_api_key: str = Field(default=os.getenv("API_KEY") or "", description="LLM API 密钥（CP_LLM_API_KEY 环境变量）")
    # LLM API 基础 URL
    llm_base_url: str = Field(
        default="https://api.lkeap.cloud.tencent.com/v1",
        description="LLM API 基础 URL",
    )
    # LLM 模型名称
    llm_model: str = Field(default="deepseek-v3-0324", description="LLM 模型名称")
    # LLM 最大生成 token 数
    llm_max_tokens: int = Field(default=8192, description="LLM 最大生成 token 数")
    # LLM 采样温度
    llm_temperature: float = Field(default=0.1, description="LLM 采样温度")

    # ========== 数据库配置 ==========
    # 数据库连接 URL（默认使用 SQLite）
    db_url: str = Field(
        default="sqlite+aiosqlite:///diagnosis.db",
        description="数据库连接 URL，支持 SQLite（默认）和其他 SQLAlchemy 兼容的数据库"
    )

    # ========== 上下文管理配置 ==========
    # 输入上下文 token 预算上限（应大于 context_reserved_tokens，否则可用预算为 0）
    context_max_tokens: int = Field(default=128000, description="输入上下文 token 预算上限")
    # system prompt + tools schema 预留开销（动态计算失败时的降级默认值）
    context_reserved_tokens: int = Field(default=8192, description="system prompt 预留 token，动态计算失败时的降级默认值")
    # 单条工具结果触发摘要的 token 阈值
    tool_result_summary_threshold: int = Field(default=4096, description="单条工具结果触发摘要的 token 阈值")
    # 滑动窗口保留的最近消息数
    sliding_window_keep_recent: int = Field(default=6, description="滑动窗口保留的最近消息数")
    # 摘要专用模型，空则使用主模型
    summary_model: str = Field(default="", description="摘要专用 LLM 模型名称，为空则使用主模型(llm_model)")
    # LLM 摘要调用超时秒数
    summary_timeout: float = Field(default=60.0, description="LLM 摘要调用超时（秒）")
    # 是否启用工具结果即时摘要
    enable_tool_result_summary: bool = Field(default=True, description="是否启用工具结果即时摘要")

    # ========== RAG 配置 ==========
    # 是否启用 RAG 知识检索增强
    rag_enabled: bool = Field(default=True, description="是否启用 RAG 知识检索增强")
    # 知识库文档目录路径
    rag_knowledge_dir: str = Field(default="knowledge/", description="知识库文档目录路径")
    # 向量数据库类型（chroma / qdrant / milvus 等）
    rag_store_type: str = Field(default="chroma", description="向量数据库类型")
    # 向量数据库本地持久化路径
    rag_store_path: str = Field(default="data/vector_db/", description="向量数据库本地持久化路径")
    # 向量数据库远程地址（仅远程模式时使用）
    rag_store_url: str = Field(default="", description="向量数据库远程地址，仅远程模式时使用")
    # 检索返回的最大知识片段数
    rag_top_k: int = Field(default=3, description="检索返回的最大知识片段数")
    # 相似度过滤阈值（低于此值的结果将被丢弃），混合检索启用时仅对纯向量路径生效
    rag_similarity_threshold: float = Field(default=0.5, description="相似度过滤阈值")
    # RAG 知识的 token 预算上限
    rag_max_tokens: int = Field(default=2048, description="RAG 知识的 token 预算上限")
    # Embedding 提供者类型：local（本地 sentence-transformers）或 api（OpenAI 兼容 API）
    rag_embedding_provider: str = Field(default="local", description="Embedding 提供者: local 或 api")
    # Embedding 模型名称（local 模式默认 BAAI/bge-m3，api 模式默认 text-embedding-3-small）
    rag_embedding_model: str = Field(default="BAAI/bge-m3", description="Embedding 模型名称")
    # Parent chunk 动态边界算法的 token 上限
    rag_max_parent_size: int = Field(default=2048, description="Parent chunk 动态边界的 token 上限")
    # 是否启用混合检索（BM25 + 向量融合），设为 False 时退化为纯向量检索
    rag_hybrid_search_enabled: bool = Field(default=True, description="是否启用 BM25 + 向量混合检索")
    # RRF 融合分数过滤阈值，低于此值的结果将被丢弃（仅混合检索时生效）
    rag_rrf_score_threshold: float = Field(default=0.01, description="RRF 融合分数过滤阈值")

    # ========== 分布式锁配置 ==========
    # 锁实现类型：local（本地 asyncio 锁）或 redis（Redis 分布式锁）
    lock_type: str = Field(default="local", description="锁实现类型: local 或 redis")
    # Redis 连接 URL（仅 lock_type=redis 时使用）
    redis_url: str = Field(default="redis://localhost:6379/0", description="Redis 连接 URL")
    # 锁的 TTL 秒数
    lock_ttl: int = Field(default=300, description="锁的 TTL 秒数，默认 300（5 分钟）")
    # 锁键前缀，用于 Redis 键命名空间隔离
    lock_key_prefix: str = Field(default="arthas:lock:", description="Redis 锁键前缀")
    # 是否启用看门狗自动续期机制
    lock_watchdog_enabled: bool = Field(default=True, description="是否启用看门狗自动续期机制")

    # ========== 调试配置 ==========
    # 是否启用调试模式
    debug: bool = Field(default=False, description="是否启用调试模式")
    # 是否启用 LLM Prompt 日志记录（记录每次发送给 LLM 的完整 prompt 内容）
    enable_prompt_logging: bool = Field(default=True, description="是否启用 LLM Prompt 日志记录到数据库")

    @field_validator("context_max_tokens")
    @classmethod
    def validate_context_max_tokens(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("context_max_tokens 必须大于 0")
        return v

    @field_validator("context_reserved_tokens")
    @classmethod
    def validate_context_reserved_tokens(cls, v: int) -> int:
        if v < 0:
            raise ValueError("context_reserved_tokens 不能为负数")
        return v

    @field_validator("tool_result_summary_threshold")
    @classmethod
    def validate_tool_result_summary_threshold(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("tool_result_summary_threshold 必须大于 0")
        return v

    @field_validator("sliding_window_keep_recent")
    @classmethod
    def validate_sliding_window_keep_recent(cls, v: int) -> int:
        if v < 2:
            raise ValueError("sliding_window_keep_recent 至少为 2")
        return v

    @field_validator("lock_type")
    @classmethod
    def validate_lock_type(cls, v: str) -> str:
        allowed = ["local", "redis"]
        if v not in allowed:
            raise ValueError(f"lock_type 必须为 {allowed} 之一，当前值: {v}")
        return v

    @field_validator("summary_timeout")
    @classmethod
    def validate_summary_timeout(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("summary_timeout 必须大于 0")
        return v

    model_config = {
        "env_prefix": "CP_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


# 全局配置单例
settings = Settings()
