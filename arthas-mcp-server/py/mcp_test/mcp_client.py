#!/usr/bin/env python3
"""
Arthas MCP Client 测试脚本

这个脚本演示如何使用 Python 连接到 Arthas MCP Server 并调用诊断工具。

使用方法:
    python test_mcp_client.py

要求:
    pip install requests

注意:
    Arthas MCP Server 使用 SSE (Server-Sent Events) 流式响应
"""

import json
import sys
import time
import uuid
from typing import Dict, Any, Optional, List
import requests


class ArthasMcpClient:
    """Arthas MCP 客户端"""
    
    def __init__(self, base_url: str = "http://localhost:8563", 
                 mcp_endpoint: str = "/mcp",
                 auth_token: Optional[str] = None):
        """
        初始化 MCP 客户端
        
        Args:
            base_url: Arthas 服务器地址
            mcp_endpoint: MCP 端点路径
            auth_token: 认证 Token（可选）
        """
        self.base_url = base_url
        self.mcp_endpoint = mcp_endpoint
        self.url = f"{base_url}{mcp_endpoint}"
        self.session_id = None
        self.auth_token = auth_token
        self.request_id = 0
        
        # 设置请求头
        # 注意: Arthas MCP Server 要求 Accept 同时包含两个值
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json"
        }
        
        if auth_token:
            self.headers["Authorization"] = f"Bearer {auth_token}"
    
    def _next_request_id(self) -> int:
        """生成下一个请求 ID"""
        self.request_id += 1
        return self.request_id
    
    def _create_request(self, method: str, params: Optional[Dict] = None) -> Dict:
        """创建 JSON-RPC 请求"""
        request = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": method
        }
        if params:
            request["params"] = params
        return request
    
    def _send_request(self, request: Dict) -> Any:
        """
        发送请求并接收 SSE 响应
        
        Args:
            request: JSON-RPC 请求
            
        Returns:
            响应结果
        """
        print(f"\n📤 发送请求: {request['method']}")
        print(f"   请求 ID: {request['id']}")
        if 'params' in request:
            print(f"   参数: {json.dumps(request['params'], ensure_ascii=False, indent=2)}")
        
        try:
            # 发送 POST 请求
            response = requests.post(
                self.url,
                json=request,
                headers=self.headers,
                stream=True,
                timeout=30
            )
            
            if response.status_code != 200:
                print(f"❌ HTTP 错误: {response.status_code}")
                print(f"   响应: {response.text}")
                return None
            
            # 保存 session ID 并更新后续请求头
            if 'mcp-session-id' in response.headers:
                self.session_id = response.headers['mcp-session-id']
                # 重要：后续请求必须带上 session ID
                self.headers['mcp-session-id'] = self.session_id
                print(f"   Session ID: {self.session_id}")
            
            # 读取完整响应内容（避免按行分割导致 UTF-8 编码被截断）
            content = response.content.decode('utf-8')
            
            # 解析 SSE 格式响应
            # SSE 事件由空行分隔，每个事件包含 id/event/data 字段
            events = content.split('\n\n')
            
            for event_block in events:
                if not event_block.strip():
                    continue
                
                current_event = None
                current_data = None
                
                for line in event_block.split('\n'):
                    if line.startswith('event:'):
                        current_event = line[6:].strip()
                    elif line.startswith('data:'):
                        current_data = line[5:].strip()
                    elif line.startswith('id:'):
                        pass  # 忽略 SSE id 字段
                    elif line.startswith('{'):
                        # 纯 JSON 响应（不是 SSE 格式）
                        try:
                            data = json.loads(line)
                            if 'id' in data and data['id'] == request['id']:
                                if 'result' in data:
                                    print(f"✅ 收到响应")
                                    return data['result']
                                elif 'error' in data:
                                    print(f"❌ 错误响应: {data['error']}")
                                    return None
                        except json.JSONDecodeError:
                            pass
                
                # 处理 SSE message 事件
                if current_data and current_event == 'message':
                    try:
                        data = json.loads(current_data)
                        if 'id' in data and data['id'] == request['id']:
                            if 'result' in data:
                                print(f"✅ 收到响应")
                                return data['result']
                            elif 'error' in data:
                                print(f"❌ 错误响应: {data['error']}")
                                return None
                    except json.JSONDecodeError:
                        pass
            
            print("⚠️  未收到响应")
            return None
            
        except requests.exceptions.RequestException as e:
            print(f"❌ 请求失败: {e}")
            return None
        except Exception as e:
            print(f"❌ 解析响应失败: {e}")
            return None
    
    def initialize(self) -> bool:
        """
        初始化 MCP 连接
        
        Returns:
            是否初始化成功
        """
        print("\n" + "="*60)
        print("🚀 初始化 MCP 连接")
        print("="*60)
        
        request = self._create_request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {
                "name": "arthas-mcp-test-client",
                "version": "1.0.0"
            }
        })
        
        result = self._send_request(request)
        
        if result:
            print(f"\n✅ 初始化成功!")
            print(f"   协议版本: {result.get('protocolVersion')}")
            print(f"   服务端: {result.get('serverInfo', {}).get('name')} "
                  f"v{result.get('serverInfo', {}).get('version')}")
            return True
        else:
            print("\n❌ 初始化失败")
            return False
    
    def list_tools(self) -> Optional[List[Dict]]:
        """
        获取可用工具列表
        
        Returns:
            工具列表
        """
        print("\n" + "="*60)
        print("🔧 获取工具列表")
        print("="*60)
        
        request = self._create_request("tools/list")
        result = self._send_request(request)
        
        if result and 'tools' in result:
            tools = result['tools']
            print(f"\n✅ 共有 {len(tools)} 个工具:")
            print()
            
            # 按类别分组
            categories = {}
            for tool in tools:
                desc = tool.get('description', '')
                # 简单分类
                if any(k in desc.lower() for k in ['jvm', 'heap', 'memory', 'thread']):
                    category = 'JVM 相关'
                elif any(k in desc.lower() for k in ['class', 'classloader', 'jad', 'dump']):
                    category = 'Class/ClassLoader 相关'
                elif any(k in desc.lower() for k in ['monitor', 'watch', 'trace', 'stack']):
                    category = '监控诊断'
                else:
                    category = '其他'
                
                if category not in categories:
                    categories[category] = []
                categories[category].append(tool)
            
            # 打印分类工具
            for category, tools_in_cat in categories.items():
                print(f"📦 {category} ({len(tools_in_cat)} 个)")
                for tool in tools_in_cat:
                    name = tool.get('name', 'unknown')
                    desc = tool.get('description', '').split('\n')[0][:60]
                    print(f"   • {name:20s} - {desc}")
                print()
            
            return tools
        else:
            print("\n❌ 获取工具列表失败")
            return None
    
    def call_tool(self, tool_name: str, arguments: Optional[Dict] = None) -> Any:
        """
        调用工具
        
        Args:
            tool_name: 工具名称
            arguments: 工具参数
            
        Returns:
            工具执行结果
        """
        print("\n" + "="*60)
        print(f"🛠️  调用工具: {tool_name}")
        print("="*60)
        
        params = {
            "name": tool_name
        }
        if arguments:
            params["arguments"] = arguments
        
        request = self._create_request("tools/call", params)
        result = self._send_request(request)
        
        if result:
            print(f"\n✅ 工具执行成功")
            
            # 解析结果
            if 'content' in result:
                for item in result['content']:
                    if item.get('type') == 'text':
                        text = item.get('text', '')
                        print("\n" + "-"*60)
                        print("📄 执行结果:")
                        print("-"*60)
                        print(text)
                        print("-"*60)
            
            return result
        else:
            print(f"\n❌ 工具执行失败")
            return None


def print_banner():
    """打印欢迎横幅"""
    banner = """
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║        🎯 Arthas MCP Client 测试脚本                         ║
║                                                              ║
║        测试 Arthas MCP Server 的原生功能                     ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
"""
    print(banner)


def main():
    """主函数"""
    print_banner()
    
    # 检查依赖
    try:
        import requests
    except ImportError:
        print("❌ 缺少依赖库，请先安装:")
        print("   pip install requests")
        sys.exit(1)
    
    # 创建客户端
    print("📡 连接到 Arthas MCP Server...")
    print("   地址: http://localhost:8563/mcp")
    print()
    
    client = ArthasMcpClient(
        base_url="http://localhost:8563",
        mcp_endpoint="/mcp",
        # auth_token="password"  # 如果启用了认证，取消注释
    )
    
    # 1. 初始化连接
    if not client.initialize():
        print("\n❌ 无法连接到 Arthas MCP Server")
        print("\n💡 请确保:")
        print("   1. Arthas 已启动")
        print("   2. MCP Server 已启用 (arthas.mcpEndpoint=/mcp)")
        print("   3. 端口 8563 可访问")
        sys.exit(1)
    
    # 2. 获取工具列表
    tools = client.list_tools()
    if not tools:
        print("\n❌ 无法获取工具列表")
        sys.exit(1)
    
    # 3. 演示调用几个工具
    print("\n" + "="*60)
    print("🎬 开始演示工具调用")
    print("="*60)
    
    # 示例 1: 查看 JVM 信息
    print("\n\n【示例 1】查看 JVM 信息")
    client.call_tool("jvm", {})
    
    time.sleep(1)
    
    # 示例 2: 查看线程信息
    print("\n\n【示例 2】查看线程信息（前 5 个）")
    client.call_tool("thread", {"n": 5})
    
    time.sleep(1)
    
    # 示例 3: 查看内存信息
    print("\n\n【示例 3】查看内存信息")
    client.call_tool("memory", {})
    
    time.sleep(1)
    
    # 示例 4: 查看系统属性
    print("\n\n【示例 4】查看系统属性（java.version）")
    client.call_tool("sysprop", {"property-name": "java.version"})
    
    time.sleep(1)
    
    # 示例 5: 搜索类
    print("\n\n【示例 5】搜索类（java.lang.String）")
    client.call_tool("sc", {"classPattern": "java.lang.String"})
    
    # 完成
    print("\n\n" + "="*60)
    print("✅ 测试完成!")
    print("="*60)
    print()
    print("💡 你可以修改脚本来测试其他工具，例如:")
    print("   • dashboard - 查看实时面板")
    print("   • jad - 反编译类")
    print("   • watch - 观察方法调用")
    print("   • trace - 追踪方法调用")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断")
        sys.exit(0)
    except Exception as e:
        print(f"\n\n❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
