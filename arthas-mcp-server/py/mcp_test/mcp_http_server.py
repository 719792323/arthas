#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Arthas MCP Client 测试服务端 - HTTP/SSE 模式

这个脚本模拟一个管控平台，接收 Arthas MCP Client 的反向连接。

架构说明：
┌─────────────────────────────────────────────────────────────────────────┐
│                         本脚本（模拟管控平台）                            │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐     │
│  │  SSE Endpoint   │    │  POST Handler   │    │  命令行交互       │     │
│  │  GET /mcp       │    │  POST /mcp      │    │  调用工具        │     │
│  └────────▲────────┘    └────────▲────────┘    └────────┬────────┘     │
│           │                      │                      │              │
└───────────┼──────────────────────┼──────────────────────┼──────────────┘
            │ SSE 事件流            │ JSON-RPC 响应        │ 发送请求
            │ (发送请求到Arthas)    │ (接收Arthas响应)     │
            │                      │                      ▼
┌───────────┴──────────────────────┴──────────────────────────────────────┐
│                         Arthas MCP Client                               │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐     │
│  │  SSE Client     │    │  HTTP POST      │    │  工具执行        │     │
│  │  连接到管控平台   │    │  发送响应       │    │  jvm/thread等    │     │
│  └─────────────────┘    └─────────────────┘    └─────────────────┘     │
└─────────────────────────────────────────────────────────────────────────┘

使用方式：
1. 设置环境变量（可选）：
   export MCP_TEST_SERVER_PORT=8080
   export MCP_TEST_AUTH_TOKEN=your-token

2. 启动服务端：
   python test_mcp_http_server.py

3. 启动 Arthas MCP Client 连接到此服务端

交互命令：
- list       : 获取工具列表
- jvm        : 调用 jvm 工具
- thread     : 调用 thread 工具
- memory     : 调用 memory 工具
- call <name> [json_args] : 调用指定工具
- help       : 显示帮助
- quit       : 退出

作者：Arthas Team
"""

import os
import sys
import json
import time
import uuid
import threading
import queue
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Optional, Dict, Any, List


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """多线程 HTTP 服务器，每个请求在独立线程中处理"""
    daemon_threads = True


# 配置
PORT = int(os.getenv('MCP_TEST_SERVER_PORT', '8080'))
AUTH_TOKEN = os.getenv('MCP_TEST_AUTH_TOKEN', '')

# 流式/异步命令列表（需要更长超时时间）
STREAMABLE_TOOLS = {'trace', 'watch', 'stack', 'tt', 'monitor', 'dashboard', 'profiler'}

# 流式命令的默认超时时间（秒）
STREAMABLE_TIMEOUT = 60.0

# 普通命令的默认超时时间（秒）
DEFAULT_TIMEOUT = 30.0

# 调试模式开关（设置为 True 显示详细日志）
DEBUG = os.getenv('MCP_DEBUG', '').lower() in ('1', 'true', 'yes')

def debug_print(*args, **kwargs):
    """仅在调试模式下打印"""
    if DEBUG:
        print(*args, **kwargs)


# 全局状态
class ServerState:
    def __init__(self):
        self.sessions: Dict[str, 'ClientSession'] = {}
        self.request_id_counter = 1
        self.lock = threading.Lock()

    def new_session(self) -> str:
        session_id = str(uuid.uuid4())
        with self.lock:
            self.sessions[session_id] = ClientSession(session_id)
        return session_id

    def get_session(self, session_id: str) -> Optional['ClientSession']:
        return self.sessions.get(session_id)

    def remove_session(self, session_id: str):
        with self.lock:
            if session_id in self.sessions:
                self.sessions[session_id].close()
                del self.sessions[session_id]

    def next_request_id(self) -> int:
        with self.lock:
            self.request_id_counter += 1
            return self.request_id_counter


# 响应回调函数类型
ResponseCallback = Any  # Callable[[Dict], None]


class ClientSession:
    """客户端会话"""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.created_at = time.time()
        self.sse_queue: queue.Queue = queue.Queue()
        # 异步模式：存储回调函数而不是 Event
        self.pending_callbacks: Dict[int, ResponseCallback] = {}
        # 存储请求的元信息（用于打印）
        self.pending_request_info: Dict[int, Dict] = {}
        self.tools: List[Dict] = []
        self.initialized = False
        self.client_info: Dict = {}
        self.active = True

    def close(self):
        self.active = False
        # 通知所有等待的回调请求已取消
        for request_id, callback in list(self.pending_callbacks.items()):
            try:
                callback({'error': {'message': 'Session closed'}})
            except:
                pass
        self.pending_callbacks.clear()
        self.pending_request_info.clear()

    def send_sse_event(self, event_type: str, data: Any):
        """发送 SSE 事件"""
        if self.active:
            self.sse_queue.put((event_type, data))

    def register_callback(self, request_id: int, callback: ResponseCallback, info: Dict = None):
        """注册异步回调"""
        self.pending_callbacks[request_id] = callback
        if info:
            self.pending_request_info[request_id] = info

    def handle_response(self, request_id: int, response: Any):
        """处理响应，调用回调"""
        callback = self.pending_callbacks.pop(request_id, None)
        info = self.pending_request_info.pop(request_id, {})
        if callback:
            try:
                callback(response, info)
            except Exception as e:
                print(f"❌ Callback error: {e}")
            return True
        return False


# 全局服务器状态
server_state = ServerState()


class McpHttpHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器"""

    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):
        """自定义日志格式"""
        print(f"[{time.strftime('%H:%M:%S')}] {self.address_string()} - {format % args}")

    def check_auth(self) -> bool:
        """检查认证"""
        if not AUTH_TOKEN:
            return True

        auth_header = self.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header[7:]
            return token == AUTH_TOKEN
        return False

    def send_json_response(self, status: int, data: Any):
        """发送 JSON 响应"""
        body = json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def send_error_response(self, status: int, message: str):
        """发送错误响应"""
        error = {
            'jsonrpc': '2.0',
            'error': {
                'code': -32600,
                'message': message
            }
        }
        self.send_json_response(status, error)

    def do_OPTIONS(self):
        """处理 CORS 预检请求"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, Accept, Mcp-Session-Id')
        self.send_header('Access-Control-Expose-Headers', 'Mcp-Session-Id')
        self.end_headers()

    def do_GET(self):
        """处理 GET 请求 - SSE 连接"""
        if not self.path.startswith('/mcp'):
            self.send_error(404)
            return

        if not self.check_auth():
            self.send_error_response(401, 'Unauthorized')
            return

        # 检查 Accept 头
        accept = self.headers.get('Accept', '')
        if 'text/event-stream' not in accept:
            self.send_error_response(400, 'Expected Accept: text/event-stream')
            return

        # 获取或创建会话
        session_id = self.headers.get('Mcp-Session-Id')
        if not session_id:
            # 从查询参数获取
            if '?' in self.path:
                params = dict(p.split('=') for p in self.path.split('?')[1].split('&') if '=' in p)
                session_id = params.get('sessionId')

        session = None  # 先初始化为 None
        if session_id:
            session = server_state.get_session(session_id)

        # 如果没有会话ID或会话不存在，创建新会话
        if not session:
            session_id = server_state.new_session()
            session = server_state.get_session(session_id)

        print(f"\n✅ SSE connection established, session: {session_id}")

        # 发送 SSE 响应头
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Mcp-Session-Id', session_id)
        self.end_headers()

        # 发送初始连接确认
        self.wfile.write(b': connected\n\n')
        self.wfile.flush()

        # SSE 事件循环
        try:
            while session.active:
                try:
                    event_type, data = session.sse_queue.get(timeout=15)  # 15秒心跳间隔

                    # 发送 SSE 事件
                    event_data = json.dumps(data)
                    self.wfile.write(f'event: {event_type}\n'.encode('utf-8'))
                    self.wfile.write(f'data: {event_data}\n\n'.encode('utf-8'))
                    self.wfile.flush()

                    debug_print(f"📤 Sent SSE event: {event_type}")

                except queue.Empty:
                    # 发送心跳保持连接
                    self.wfile.write(b': heartbeat\n\n')
                    self.wfile.flush()

        except (BrokenPipeError, ConnectionResetError):
            print(f"\n❌ SSE connection closed, session: {session_id}")
        finally:
            # 注意：不要立即删除 session，因为客户端可能还需要发送 POST 请求
            # 只是标记 session 的 SSE 连接已断开
            if session:
                session.active = False
            debug_print(f"SSE stream ended for session: {session_id}")

    def do_POST(self):
        """处理 POST 请求 - JSON-RPC 消息"""
        debug_print(f"\n📨 Received POST request: {self.path}")
        debug_print(f"   Headers: {dict(self.headers)}")

        if not self.path.startswith('/mcp'):
            self.send_error(404)
            return

        if not self.check_auth():
            self.send_error_response(401, 'Unauthorized')
            return

        # 获取会话
        session_id = self.headers.get('Mcp-Session-Id')
        if not session_id:
            # 从查询参数获取
            if '?' in self.path:
                params = dict(p.split('=') for p in self.path.split('?')[1].split('&') if '=' in p)
                session_id = params.get('sessionId')

        if not session_id:
            # 没有会话ID，创建新会话
            session_id = server_state.new_session()

        session = server_state.get_session(session_id)
        if not session:
            # 尝试获取一个活跃的 session（处理 Python 重启后 session 不匹配的情况）
            active_session = get_active_session()
            if active_session:
                debug_print(f"  ℹ️ Unknown session {session_id[:8]}..., routing to active session {active_session.session_id[:8]}...")
                session = active_session
            else:
                session_id = server_state.new_session()
                session = server_state.get_session(session_id)

        # 读取请求体
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')

        try:
            message = json.loads(body)
        except json.JSONDecodeError:
            self.send_error_response(400, 'Invalid JSON')
            return

        # 打印消息
        method = message.get('method', 'response')
        if method != "ping":
            debug_print(f"\n📥 Received message: {message.get('method', 'response')}")

        # 处理消息
        if 'method' in message:
            if 'id' in message:
                # 请求
                self.handle_request(session, message)
            else:
                # 通知
                self.handle_notification(session, message)
        elif 'result' in message or 'error' in message:
            # 响应
            self.handle_response(session, message)
        else:
            self.send_error_response(400, 'Invalid message format')

    def handle_request(self, session: ClientSession, request: Dict):
        """处理客户端请求"""
        method = request.get('method')
        params = request.get('params', {})
        request_id = request.get('id')

        debug_print(f"  Method: {method}")

        if method == 'initialize':
            # 处理初始化请求
            session.client_info = params.get('clientInfo', {})
            session.initialized = True

            debug_print(
                f"  Client: {session.client_info.get('name', 'unknown')} v{session.client_info.get('version', 'unknown')}")

            response = {
                'jsonrpc': '2.0',
                'id': request_id,
                'result': {
                    'protocolVersion': '2025-03-26',
                    'capabilities': {
                        'tools': {'listChanged': True}
                    },
                    'serverInfo': {
                        'name': 'MCP Test Server',
                        'version': '1.0.0'
                    }
                }
            }
            self.send_json_response(200, response)

        elif method == 'ping':
            response = {
                'jsonrpc': '2.0',
                'id': request_id,
                'result': {}
            }
            self.send_json_response(200, response)

        else:
            # 未知方法
            response = {
                'jsonrpc': '2.0',
                'id': request_id,
                'error': {
                    'code': -32601,
                    'message': f'Method not found: {method}'
                }
            }
            self.send_json_response(200, response)

    def handle_notification(self, session: ClientSession, notification: Dict):
        """处理客户端通知"""
        method = notification.get('method')
        debug_print(f"  Notification: {method}")

        if method == 'notifications/initialized':
            print(f"\n✅ Client initialized successfully!")
            print(f"   Session: {session.session_id}")
            print(f"   Client: {session.client_info.get('name', 'unknown')}")
            
            # 清理其他非活跃或未初始化的旧会话
            sessions_to_remove = []
            for sid, s in server_state.sessions.items():
                if sid != session.session_id and (not s.active or not s.initialized):
                    sessions_to_remove.append(sid)
            
            for sid in sessions_to_remove:
                debug_print(f"   🗑️ Removing stale session: {sid[:8]}...")
                server_state.remove_session(sid)

        # 通知不需要响应，返回 202 Accepted
        self.send_response(202)
        self.send_header('Content-Length', 0)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Mcp-Session-Id', session.session_id)
        self.end_headers()

    def handle_response(self, session: ClientSession, response: Dict):
        """处理客户端响应（对我们发送的请求的响应）- 异步回调模式"""
        response_id = response.get('id')
        if response_id:
            # 首先尝试在当前 session 中处理响应
            if session.handle_response(response_id, response):
                pass  # 成功处理
            else:
                # 如果当前 session 没有等待这个 response_id，
                # 尝试在所有活跃 session 中查找（处理重启后 session 不匹配的情况）
                matched = False
                for s in server_state.sessions.values():
                    if response_id in s.pending_callbacks:
                        debug_print(f"  ℹ️ Routing response {response_id} to session {s.session_id[:8]}...")
                        s.handle_response(response_id, response)
                        matched = True
                        break
                
                if not matched:
                    debug_print(f"  ⚠️ No pending request found for response id: {response_id}")

        # 响应不需要回复
        self.send_response(202)
        self.send_header('Content-Length', 0)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Mcp-Session-Id', session.session_id)
        self.end_headers()


def send_tool_request_async(session: ClientSession, method: str, params: Optional[Dict] = None, 
                            callback: ResponseCallback = None, info: Dict = None) -> int:
    """异步发送工具请求到客户端，响应通过回调处理"""
    request_id = server_state.next_request_id()

    request = {
        'jsonrpc': '2.0',
        'id': request_id,
        'method': method
    }
    if params:
        request['params'] = params

    # 注册回调
    if callback:
        session.register_callback(request_id, callback, info)

    # 通过 SSE 发送请求
    session.send_sse_event('message', request)

    return request_id


def send_tool_request(session: ClientSession, method: str, params: Optional[Dict] = None, timeout: float = DEFAULT_TIMEOUT) -> Optional[Dict]:
    """同步发送工具请求到客户端（兼容旧代码，仅用于 list 等简单命令）"""
    result_holder = {'response': None, 'event': threading.Event()}
    
    def sync_callback(response, info=None):
        result_holder['response'] = response
        result_holder['event'].set()
    
    request_id = send_tool_request_async(session, method, params, sync_callback)
    
    # 等待响应
    if result_holder['event'].wait(timeout):
        return result_holder['response']
    else:
        # 超时，清理回调
        session.pending_callbacks.pop(request_id, None)
        session.pending_request_info.pop(request_id, None)
        return None


def parse_cli_args(args_list: List[str]) -> Dict:
    """
    解析命令行风格的参数
    
    支持格式:
    - --key value
    - --key=value
    - -key value
    - --flag (布尔值，设为 true)
    
    示例:
    parse_cli_args(['--action', 'heapAnalyze', '--classNum', '5'])
    => {'action': 'heapAnalyze', 'classNum': 5}
    """
    result = {}
    i = 0
    while i < len(args_list):
        arg = args_list[i]
        
        # 跳过非参数项
        if not arg.startswith('-'):
            i += 1
            continue
        
        # 去掉前缀 -- 或 -
        if arg.startswith('--'):
            key = arg[2:]
        else:
            key = arg[1:]
        
        # 检查是否是 --key=value 格式
        if '=' in key:
            key, value = key.split('=', 1)
            result[key] = try_parse_value(value)
            i += 1
            continue
        
        # 检查下一个参数是否是值
        if i + 1 < len(args_list) and not args_list[i + 1].startswith('-'):
            value = args_list[i + 1]
            result[key] = try_parse_value(value)
            i += 2
        else:
            # 没有值，作为布尔标志
            result[key] = True
            i += 1
    
    return result


def try_parse_value(value: str):
    """
    尝试将字符串值解析为适当的类型
    """
    # 尝试解析为整数
    try:
        return int(value)
    except ValueError:
        pass
    
    # 尝试解析为浮点数
    try:
        return float(value)
    except ValueError:
        pass
    
    # 尝试解析为布尔值
    if value.lower() in ('true', 'yes', 'on', '1'):
        return True
    if value.lower() in ('false', 'no', 'off', '0'):
        return False
    
    # 返回原始字符串
    return value


def print_tool_response(response: Dict, info: Dict = None):
    """打印工具响应结果（回调函数）"""
    tool_name = info.get('tool_name', 'unknown') if info else 'unknown'
    
    if response:
        if 'result' in response:
            result = response['result']
            content = result.get('content', [])
            print(f"\n✅ [{tool_name}] Tool result:")
            for item in content:
                if item.get('type') == 'text':
                    text = item.get('text', '')
                    # 尝试格式化 JSON
                    try:
                        data = json.loads(text)
                        print(json.dumps(data, indent=2, ensure_ascii=False))
                    except:
                        # 直接打印源代码文本
                        print(text)
        elif 'error' in response:
            print(f"\n❌ [{tool_name}] Error: {response['error'].get('message', 'Unknown error')}")
    else:
        print(f"\n❌ [{tool_name}] No response")
    
    # 打印提示符，让用户知道可以继续输入
    print("\n>>> ", end='', flush=True)


def call_tool(session: ClientSession, tool_name: str, args: Dict = None):
    """调用工具（异步模式，立即返回）"""
    if args is None:
        args = {}

    print(f"📤 Calling {tool_name}...")
    debug_print(f"   Arguments: {json.dumps(args, ensure_ascii=False)}")
    
    params = {'name': tool_name, 'arguments': args}
    info = {'tool_name': tool_name, 'args': args, 'start_time': time.time()}
    
    # 异步发送请求，响应到达时通过回调打印
    request_id = send_tool_request_async(session, 'tools/call', params, print_tool_response, info)
    print(f"   Request sent (id: {request_id}), waiting for response...")


def get_active_session() -> Optional[ClientSession]:
    """获取一个活跃且已初始化的会话"""
    sessions = list(server_state.sessions.values())
    # 过滤：只选择 active=True 且 initialized=True 的会话
    active_sessions = [s for s in sessions if s.active and s.initialized]
    if active_sessions:
        return active_sessions[0]
    return None


def interactive_cli(server_state: ServerState):
    """交互式命令行"""
    print("\n" + "=" * 60)
    print("MCP Test Server CLI")
    print("=" * 60)
    print("Commands:")
    print("  list                    - List available tools")
    print("  call <name> [json_args] - Call a specific tool")
    print("  <tool_name>             - Directly call a tool (e.g. thread_count)")
    print("  sessions                - List active sessions")
    print("  help                    - Show this help")
    print("  quit                    - Exit")
    print("=" * 60)

    while True:
        try:
            cmd = input("\n>>> ").strip()
            if not cmd:
                continue

            parts = cmd.split(None, 2)
            command = parts[0].lower()

            if command == 'quit' or command == 'exit':
                print("Goodbye!")
                os._exit(0)

            elif command == 'help':
                print("Commands: list, call <name> [args], <tool_name>, sessions, quit")
                print("Example: thread_count, echo {\"message\": \"hello\"}")

            elif command == 'sessions':
                sessions = list(server_state.sessions.values())
                if not sessions:
                    print("No sessions")
                else:
                    print(f"Total sessions: {len(sessions)}")
                    for s in sessions:
                        status = "✅ active" if (s.active and s.initialized) else "⏳ pending" if s.active else "❌ closed"
                        print(f"  - {s.session_id[:8]}... ({s.client_info.get('name', 'unknown')}) [{status}]")

            elif command in ['list', 'call']:
                # 需要选择一个已初始化且活跃的会话
                session = get_active_session()
                if not session:
                    print("❌ No active client sessions (waiting for initialized connection)")
                    continue

                if command == 'list':
                    print(f"📤 Requesting tools list...")
                    response = send_tool_request(session, 'tools/list')
                    if response:
                        if 'result' in response:
                            tools = response['result'].get('tools', [])
                            session.tools = tools  # 保存工具列表
                            print(f"\n✅ Found {len(tools)} tools:")
                            for tool in tools:
                                print(f"  - {tool['name']}: {tool.get('description', 'No description')}")
                        elif 'error' in response:
                            print(f"❌ Error: {response['error'].get('message', 'Unknown error')}")
                    else:
                        print("❌ Request timeout")

                elif command == 'call':
                    if len(parts) < 2:
                        print("Usage: call <tool_name> [json_arguments]")
                        continue

                    tool_name = parts[1]
                    args = {}
                    if len(parts) > 2:
                        try:
                            args = json.loads(parts[2])
                        except json.JSONDecodeError:
                            print("❌ Invalid JSON arguments")
                            continue

                    call_tool(session, tool_name, args)

            else:
                # 尝试将未知命令当作工具名来调用
                # 验证工具名：必须是字母、数字、下划线组成的有效标识符
                if not command.replace('_', '').isalnum() or not command[0].isalpha():
                    print(f"Unknown command: {command}")
                    print("Type 'help' for available commands")
                    continue

                session = get_active_session()
                if not session:
                    print(f"❌ No active client sessions")
                    print("Please wait for an initialized client connection first")
                    continue
                tool_name = command
                args = {}

                # 检查是否有参数
                if len(parts) > 1:
                    arg_str = parts[1]
                    # 如果参数以 { 或 [ 开头，尝试解析为 JSON
                    if arg_str.startswith('{') or arg_str.startswith('['):
                        # JSON 参数可能包含空格，需要合并 parts[1] 和 parts[2]（如果有）
                        full_json = ' '.join(parts[1:])
                        try:
                            args = json.loads(full_json)
                        except json.JSONDecodeError:
                            print(f"❌ Invalid JSON arguments: {full_json}")
                            continue
                    # 检查是否是命令行风格参数 (--key value 或 -key value)
                    elif arg_str.startswith('-'):
                        # 重新分割完整命令以获取所有参数（不限制分割数量）
                        # 例如: vmtool --action heapAnalyze --classNum 5 --objectNum 3
                        # 变为: ['vmtool', '--action', 'heapAnalyze', '--classNum', '5', '--objectNum', '3']
                        all_parts = cmd.split()
                        args = parse_cli_args(all_parts[1:])
                    else:
                        # 否则作为普通字符串参数，根据工具名选择合适的参数名
                        # 需要两个参数的工具 (classPattern + methodPattern)
                        two_param_tools = {'trace', 'watch', 'stack', 'monitor', 'tt'}
                        
                        if tool_name in two_param_tools and len(parts) > 2:
                            # 支持：trace classPattern methodPattern
                            args = {
                                'classPattern': parts[1],
                                'methodPattern': parts[2]
                            }
                        else:
                            # 工具参数名映射表
                            tool_param_map = {
                                'jad': 'classPattern',
                                'sc': 'classPattern',
                                'sm': 'classPattern',
                                'getstatic': 'classPattern',
                                'classloader': 'hashcode',
                                'watch': 'classPattern',
                                'trace': 'classPattern',
                                'stack': 'classPattern',
                                'tt': 'classPattern',
                                'monitor': 'classPattern',
                                'echo': 'message',
                            }
                            # 使用映射的参数名，默认为 className
                            param_name = tool_param_map.get(tool_name, 'className')
                            args = {param_name: arg_str}

                call_tool(session, tool_name, args)

        except KeyboardInterrupt:
            print("\nGoodbye!")
            os._exit(0)
        except Exception as e:
            print(f"Error: {e}")


def main():
    print("=" * 60)
    print("  Arthas MCP Test Server - HTTP/SSE Mode")
    print("=" * 60)
    print(f"  Port: {PORT}")
    print(f"  Auth: {'Enabled' if AUTH_TOKEN else 'Disabled'}")
    print("=" * 60)

    # 启动 HTTP 服务器（使用多线程版本，以便同时处理 SSE 和 POST 请求）
    server = ThreadingHTTPServer(('0.0.0.0', PORT), McpHttpHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    print(f"\n🚀 Server listening on http://0.0.0.0:{PORT}/mcp")
    print("\nWaiting for Arthas MCP Client to connect...")
    print("Set environment variable to connect:")
    print(f"  export ARTHAS_MCP_CLIENT_SERVER_URL=http://localhost:{PORT}/mcp")
    if AUTH_TOKEN:
        print(f"  export ARTHAS_MCP_CLIENT_AUTH_TOKEN={AUTH_TOKEN}")

    # 启动交互式 CLI
    interactive_cli(server_state)


if __name__ == '__main__':
    main()
