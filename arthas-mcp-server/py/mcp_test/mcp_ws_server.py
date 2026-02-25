#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Arthas MCP Client 测试服务端 - WebSocket 模式

这个脚本模拟一个管控平台，接收 Arthas MCP Client 的 WebSocket 反向连接。

架构说明：
┌─────────────────────────────────────────────────────────────────────────┐
│                         本脚本（模拟管控平台）                            │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  WebSocket Server (/mcp)                                        │   │
│  │  双向通信：收发 JSON-RPC 消息                                     │   │
│  └────────────────────────────▲────────────────────────────────────┘   │
│                               │                                        │
│  ┌─────────────────────────── │ ──────────────────────────────────┐   │
│  │  命令行交互                │                                    │   │
│  │  list / call / <tool_name> │                                    │   │
│  └────────────────────────────┴────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────────────┘
                                │
                         WebSocket 连接
                        （双向全双工）
                                │
┌───────────────────────────────┴───────────────────────────────────────┐
│                         Arthas MCP Client                              │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │  WebSocket Client                                                │  │
│  │  收发 JSON-RPC 消息 + 工具执行 (jvm/thread/...)                  │  │
│  └─────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────┘

依赖版本要求：
  websockets >= 14.0 （推荐 >= 14.0，旧版 API 已废弃）
  安装/升级: pip install "websockets>=14.0"

使用方式：
1. 安装依赖：pip install "websockets>=14.0"
2. 启动服务端：python mcp_ws_server.py
3. 设置环境变量并启动 Arthas MCP Client 连接到此服务端：
   export ARTHAS_MCP_CLIENT_SERVER_URL=ws://localhost:8080/mcp
   export ARTHAS_MCP_CLIENT_TRANSPORT_TYPE=WEBSOCKET

交互命令：
- list                              : 获取工具列表
- jvm / thread / memory             : 直接调用工具
- call <name> [json_args]           : 调用指定工具
- <tool_name> --arg1 val1 --arg2 v2 : 命令行风格参数
- sessions                          : 列出活跃会话
- help                              : 显示帮助
- quit                              : 退出

作者：Arthas Team
"""

import os
import sys
import json
import time
import uuid
import asyncio
import threading
from typing import Optional, Dict, Any, List

try:
    import websockets
    # websockets >= 14.0: 使用顶层 websockets.serve()
    # websockets < 14.0 (legacy): 使用 websockets.server.serve
    if hasattr(websockets, 'serve'):
        serve = websockets.serve
    else:
        from websockets.server import serve
except ImportError:
    print("❌ 需要安装 websockets 库：pip install 'websockets>=14.0'")
    sys.exit(1)

# 版本检查
_ws_version = tuple(int(x) for x in websockets.__version__.split('.')[:2])
if _ws_version < (14, 0):
    print(f"⚠️  当前 websockets 版本 {websockets.__version__}，建议升级到 >= 14.0")
    print(f"   运行: pip install 'websockets>=14.0'")


# 配置
PORT = int(os.getenv('MCP_TEST_SERVER_PORT', '8080'))
AUTH_TOKEN = os.getenv('MCP_TEST_AUTH_TOKEN', '')
DEBUG = os.getenv('MCP_DEBUG', '').lower() in ('1', 'true', 'yes')

# 流式/异步命令列表
STREAMABLE_TOOLS = {'trace', 'watch', 'stack', 'tt', 'monitor', 'dashboard', 'profiler'}
STREAMABLE_TIMEOUT = 60.0
DEFAULT_TIMEOUT = 30.0


def debug_print(*args, **kwargs):
    """仅在调试模式下打印"""
    if DEBUG:
        print(*args, **kwargs)


class ClientSession:
    """WebSocket 客户端会话"""

    def __init__(self, session_id: str, websocket):
        self.session_id = session_id
        self.websocket = websocket
        self.created_at = time.time()
        self.tools: List[Dict] = []
        self.initialized = False
        self.client_info: Dict = {}
        self.active = True
        self.request_id_counter = 1
        # 异步回调
        self.pending_futures: Dict[int, asyncio.Future] = {}
        self.pending_request_info: Dict[int, Dict] = {}

    def next_request_id(self) -> int:
        self.request_id_counter += 1
        return self.request_id_counter

    async def send_message(self, message: Dict):
        """通过 WebSocket 发送消息"""
        if self.active and self.websocket:
            try:
                data = json.dumps(message)
                await self.websocket.send(data)
                debug_print(f"📤 发送: {message.get('method', 'response')}")
            except Exception as e:
                print(f"❌ 发送失败: {e}")
                self.active = False

    def close(self):
        self.active = False
        # 取消所有 pending future
        for future in self.pending_futures.values():
            if not future.done():
                future.set_exception(Exception("Session closed"))
        self.pending_futures.clear()
        self.pending_request_info.clear()


class ServerState:
    """服务器全局状态"""

    def __init__(self):
        self.sessions: Dict[str, ClientSession] = {}
        self.lock = threading.Lock()

    def new_session(self, websocket) -> ClientSession:
        session_id = str(uuid.uuid4())
        session = ClientSession(session_id, websocket)
        with self.lock:
            self.sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[ClientSession]:
        return self.sessions.get(session_id)

    def remove_session(self, session_id: str):
        with self.lock:
            session = self.sessions.pop(session_id, None)
            if session:
                session.close()

    def get_active_session(self) -> Optional[ClientSession]:
        """获取一个活跃且已初始化的会话"""
        for s in self.sessions.values():
            if s.active and s.initialized:
                return s
        return None


server_state = ServerState()
# asyncio 事件循环引用（供 CLI 线程使用）
main_loop: Optional[asyncio.AbstractEventLoop] = None


def handle_request(session: ClientSession, message: Dict) -> Optional[Dict]:
    """处理客户端请求，返回响应（如果需要）"""
    method = message.get('method')
    params = message.get('params', {})
    request_id = message.get('id')

    if method == 'initialize':
        session.client_info = params.get('clientInfo', {})
        session.initialized = True
        debug_print(f"  客户端: {session.client_info.get('name', 'unknown')} "
                    f"v{session.client_info.get('version', 'unknown')}")
        return {
            'jsonrpc': '2.0',
            'id': request_id,
            'result': {
                'protocolVersion': '2025-03-26',
                'capabilities': {
                    'tools': {'listChanged': True}
                },
                'serverInfo': {
                    'name': 'MCP WS Test Server',
                    'version': '1.0.0'
                }
            }
        }

    elif method == 'ping':
        return {
            'jsonrpc': '2.0',
            'id': request_id,
            'result': {}
        }

    else:
        return {
            'jsonrpc': '2.0',
            'id': request_id,
            'error': {
                'code': -32601,
                'message': f'Method not found: {method}'
            }
        }


def handle_notification(session: ClientSession, message: Dict):
    """处理客户端通知"""
    method = message.get('method')
    if method == 'notifications/initialized':
        print(f"\n✅ 客户端初始化成功!")
        print(f"   Session: {session.session_id}")
        print(f"   Client: {session.client_info.get('name', 'unknown')}")
        # 清理旧的非活跃会话
        stale = [sid for sid, s in server_state.sessions.items()
                 if sid != session.session_id and (not s.active or not s.initialized)]
        for sid in stale:
            debug_print(f"   🗑️ 清理旧会话: {sid[:8]}...")
            server_state.remove_session(sid)


def handle_response(session: ClientSession, message: Dict):
    """处理客户端响应（对我们发送请求的响应）"""
    response_id = message.get('id')
    if response_id is None:
        return

    future = session.pending_futures.pop(response_id, None)
    info = session.pending_request_info.pop(response_id, {})

    if future and not future.done():
        future.set_result((message, info))
    else:
        # 尝试在其他会话中查找
        for s in server_state.sessions.values():
            f = s.pending_futures.pop(response_id, None)
            i = s.pending_request_info.pop(response_id, {})
            if f and not f.done():
                f.set_result((message, i))
                return
        debug_print(f"  ⚠️ 未找到 response id: {response_id} 的 pending 请求")


def _get_ws_path(websocket) -> str:
    """兼容不同版本 websockets 获取请求路径"""
    # websockets >= 13.0 新 API: websocket.request.path
    if hasattr(websocket, 'request') and websocket.request is not None:
        req = websocket.request
        if hasattr(req, 'path'):
            return req.path
        # 某些版本 request 可能只有 url
        if hasattr(req, 'url'):
            return req.url
    # legacy API: websocket.path
    if hasattr(websocket, 'path'):
        return websocket.path
    return '/mcp'


def _get_ws_headers(websocket) -> dict:
    """兼容不同版本 websockets 获取请求头"""
    # websockets >= 13.0 新 API
    if hasattr(websocket, 'request') and websocket.request is not None:
        req = websocket.request
        if hasattr(req, 'headers'):
            return req.headers
    # legacy API
    if hasattr(websocket, 'request_headers'):
        return websocket.request_headers
    return {}


async def handle_websocket(websocket):
    """处理 WebSocket 连接"""
    # 认证检查
    if AUTH_TOKEN:
        headers = _get_ws_headers(websocket)
        auth_header = headers.get('Authorization', '')
        if not auth_header.startswith('Bearer ') or auth_header[7:] != AUTH_TOKEN:
            print("❌ 认证失败，关闭连接")
            await websocket.close(4001, "Unauthorized")
            return

    # 提取 sessionId（从查询参数）
    path = _get_ws_path(websocket)
    session_id = None
    if '?' in path:
        params = dict(p.split('=', 1) for p in path.split('?', 1)[1].split('&') if '=' in p)
        session_id = params.get('sessionId')

    # 获取或创建会话
    session = None
    if session_id:
        session = server_state.get_session(session_id)
        if session:
            session.websocket = websocket
            session.active = True
            print(f"\n🔄 客户端重连，复用会话: {session_id[:8]}...")

    if not session:
        session = server_state.new_session(websocket)
        print(f"\n✅ WebSocket 连接建立，新会话: {session.session_id[:8]}...")

    try:
        async for raw_message in websocket:
            try:
                message = json.loads(raw_message)
            except json.JSONDecodeError:
                print(f"❌ 无效 JSON: {raw_message[:100]}")
                continue

            method = message.get('method', '')
            if method and method != 'ping':
                debug_print(f"\n📥 收到: {method}")

            # 分类处理
            if 'method' in message:
                if 'id' in message:
                    # 请求 → 需要响应
                    response = handle_request(session, message)
                    if response:
                        await session.send_message(response)
                else:
                    # 通知
                    handle_notification(session, message)
            elif 'result' in message or 'error' in message:
                # 响应
                handle_response(session, message)
            else:
                debug_print(f"  ⚠️ 未知消息格式: {message}")

    except websockets.exceptions.ConnectionClosedOK:
        print(f"\n📪 WebSocket 正常关闭: {session.session_id[:8]}...")
    except websockets.exceptions.ConnectionClosedError as e:
        print(f"\n❌ WebSocket 异常关闭: {session.session_id[:8]}... ({e})")
    except Exception as e:
        print(f"\n❌ WebSocket 错误: {e}")
    finally:
        session.active = False
        debug_print(f"WebSocket 连接结束: {session.session_id[:8]}...")


def print_tool_response(response: Dict, info: Dict = None):
    """打印工具响应结果"""
    tool_name = info.get('tool_name', 'unknown') if info else 'unknown'

    if response:
        if 'result' in response:
            result = response['result']
            content = result.get('content', [])
            print(f"\n✅ [{tool_name}] 工具结果:")
            for item in content:
                if item.get('type') == 'text':
                    text = item.get('text', '')
                    try:
                        data = json.loads(text)
                        print(json.dumps(data, indent=2, ensure_ascii=False))
                    except:
                        print(text)
        elif 'error' in response:
            print(f"\n❌ [{tool_name}] 错误: {response['error'].get('message', 'Unknown error')}")
    else:
        print(f"\n❌ [{tool_name}] 无响应")

    print("\n>>> ", end='', flush=True)


async def send_tool_request(session: ClientSession, method: str,
                            params: Optional[Dict] = None,
                            timeout: float = DEFAULT_TIMEOUT) -> Optional[Dict]:
    """异步发送请求到客户端并等待响应"""
    request_id = session.next_request_id()
    request = {
        'jsonrpc': '2.0',
        'id': request_id,
        'method': method
    }
    if params:
        request['params'] = params

    # 创建 Future
    loop = asyncio.get_event_loop()
    future = loop.create_future()
    session.pending_futures[request_id] = future
    session.pending_request_info[request_id] = params or {}

    # 发送请求
    await session.send_message(request)

    try:
        result = await asyncio.wait_for(future, timeout=timeout)
        return result  # (response, info) tuple
    except asyncio.TimeoutError:
        session.pending_futures.pop(request_id, None)
        session.pending_request_info.pop(request_id, None)
        return None


async def call_tool_async(session: ClientSession, tool_name: str, args: Dict = None):
    """异步调用工具"""
    if args is None:
        args = {}

    print(f"📤 调用 {tool_name}...")
    debug_print(f"   参数: {json.dumps(args, ensure_ascii=False)}")

    params = {'name': tool_name, 'arguments': args}
    timeout = STREAMABLE_TIMEOUT if tool_name in STREAMABLE_TOOLS else DEFAULT_TIMEOUT

    result = await send_tool_request(session, 'tools/call', params, timeout)
    if result:
        response, info = result
        info['tool_name'] = tool_name
        print_tool_response(response, info)
    else:
        print(f"\n❌ [{tool_name}] 请求超时 ({timeout}s)")
        print("\n>>> ", end='', flush=True)


def parse_cli_args(args_list: List[str]) -> Dict:
    """解析命令行风格的参数（--key value / --key=value / -key value）"""
    result = {}
    i = 0
    while i < len(args_list):
        arg = args_list[i]
        if not arg.startswith('-'):
            i += 1
            continue
        key = arg.lstrip('-')
        if '=' in key:
            key, value = key.split('=', 1)
            result[key] = try_parse_value(value)
            i += 1
            continue
        if i + 1 < len(args_list) and not args_list[i + 1].startswith('-'):
            result[key] = try_parse_value(args_list[i + 1])
            i += 2
        else:
            result[key] = True
            i += 1
    return result


def try_parse_value(value: str):
    """尝试将字符串值解析为适当类型"""
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    if value.lower() in ('true', 'yes', 'on', '1'):
        return True
    if value.lower() in ('false', 'no', 'off', '0'):
        return False
    return value


async def process_command(cmd: str):
    """处理 CLI 命令（在 asyncio 事件循环中执行）"""
    if not cmd:
        return

    parts = cmd.split(None, 2)
    command = parts[0].lower()

    if command in ('quit', 'exit'):
        print("再见!")
        os._exit(0)

    elif command == 'help':
        print("命令:")
        print("  list                              - 获取工具列表")
        print("  call <name> [json_args]           - 调用指定工具")
        print("  <tool_name>                       - 直接调用工具")
        print("  <tool_name> --arg1 val1 --arg2 v2 - 命令行风格参数")
        print("  sessions                          - 列出活跃会话")
        print("  quit                              - 退出")

    elif command == 'sessions':
        sessions = list(server_state.sessions.values())
        if not sessions:
            print("无活跃会话")
        else:
            print(f"总会话数: {len(sessions)}")
            for s in sessions:
                status = "✅ 活跃" if (s.active and s.initialized) else "⏳ 等待" if s.active else "❌ 关闭"
                print(f"  - {s.session_id[:8]}... ({s.client_info.get('name', 'unknown')}) [{status}]")

    elif command == 'list':
        session = server_state.get_active_session()
        if not session:
            print("❌ 无活跃会话（等待客户端连接...）")
            return
        print("📤 请求工具列表...")
        result = await send_tool_request(session, 'tools/list')
        if result:
            response, _ = result
            if 'result' in response:
                tools = response['result'].get('tools', [])
                session.tools = tools
                print(f"\n✅ 找到 {len(tools)} 个工具:")
                for tool in tools:
                    print(f"  - {tool['name']}: {tool.get('description', '无描述')}")
            elif 'error' in response:
                print(f"❌ 错误: {response['error'].get('message')}")
        else:
            print("❌ 请求超时")

    elif command == 'call':
        session = server_state.get_active_session()
        if not session:
            print("❌ 无活跃会话")
            return
        if len(parts) < 2:
            print("用法: call <tool_name> [json_arguments]")
            return
        tool_name = parts[1]
        args = {}
        if len(parts) > 2:
            try:
                args = json.loads(parts[2])
            except json.JSONDecodeError:
                print("❌ 无效 JSON 参数")
                return
        await call_tool_async(session, tool_name, args)

    else:
        # 尝试当作工具名调用
        if not command.replace('_', '').isalnum() or not command[0].isalpha():
            print(f"未知命令: {command}")
            print("输入 'help' 查看可用命令")
            return

        session = server_state.get_active_session()
        if not session:
            print("❌ 无活跃会话（等待客户端连接...）")
            return

        tool_name = command
        args = {}

        if len(parts) > 1:
            arg_str = parts[1]
            if arg_str.startswith('{') or arg_str.startswith('['):
                full_json = ' '.join(parts[1:])
                try:
                    args = json.loads(full_json)
                except json.JSONDecodeError:
                    print(f"❌ 无效 JSON 参数: {full_json}")
                    return
            elif arg_str.startswith('-'):
                all_parts = cmd.split()
                args = parse_cli_args(all_parts[1:])
            else:
                two_param_tools = {'trace', 'watch', 'stack', 'monitor', 'tt'}
                if tool_name in two_param_tools and len(parts) > 2:
                    args = {'classPattern': parts[1], 'methodPattern': parts[2]}
                else:
                    tool_param_map = {
                        'jad': 'classPattern', 'sc': 'classPattern', 'sm': 'classPattern',
                        'getstatic': 'classPattern', 'classloader': 'hashcode',
                        'watch': 'classPattern', 'trace': 'classPattern',
                        'stack': 'classPattern', 'tt': 'classPattern',
                        'monitor': 'classPattern', 'echo': 'message',
                    }
                    param_name = tool_param_map.get(tool_name, 'className')
                    args = {param_name: arg_str}

        await call_tool_async(session, tool_name, args)


def cli_thread_func():
    """CLI 线程：读取用户输入并调度到 asyncio 循环"""
    global main_loop

    print("\n" + "=" * 60)
    print("MCP WebSocket Test Server CLI")
    print("=" * 60)
    print("命令:")
    print("  list                    - 获取工具列表")
    print("  call <name> [json_args] - 调用指定工具")
    print("  <tool_name>             - 直接调用工具 (如 jvm, thread)")
    print("  sessions                - 列出活跃会话")
    print("  help                    - 显示帮助")
    print("  quit                    - 退出")
    print("=" * 60)

    while True:
        try:
            cmd = input("\n>>> ").strip()
            if not cmd:
                continue
            if main_loop:
                asyncio.run_coroutine_threadsafe(process_command(cmd), main_loop)
        except KeyboardInterrupt:
            print("\n再见!")
            os._exit(0)
        except EOFError:
            break
        except Exception as e:
            print(f"错误: {e}")


async def main():
    global main_loop
    main_loop = asyncio.get_event_loop()

    print("=" * 60)
    print("  Arthas MCP Test Server - WebSocket 模式")
    print("=" * 60)
    print(f"  端口: {PORT}")
    print(f"  认证: {'已启用' if AUTH_TOKEN else '未启用'}")
    print(f"  调试: {'已启用' if DEBUG else '未启用'}")
    print("=" * 60)

    # 启动 CLI 线程
    cli = threading.Thread(target=cli_thread_func, daemon=True)
    cli.start()

    print(f"\n🚀 WebSocket 服务端监听 ws://0.0.0.0:{PORT}/mcp")
    print("\n等待 Arthas MCP Client 连接...")
    print("配置环境变量以连接:")
    print(f"  export ARTHAS_MCP_CLIENT_SERVER_URL=ws://localhost:{PORT}/mcp")
    print(f"  export ARTHAS_MCP_CLIENT_TRANSPORT_TYPE=WEBSOCKET")
    if AUTH_TOKEN:
        print(f"  export ARTHAS_MCP_CLIENT_AUTH_TOKEN={AUTH_TOKEN}")

    async with serve(handle_websocket, "0.0.0.0", PORT,
                     ping_interval=30, ping_timeout=10) as server:
        await asyncio.Future()  # 永久运行


if __name__ == '__main__':
    asyncio.run(main())
