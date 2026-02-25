/**
 * 任务列表页面
 * 显示所有诊断任务 + 系统状态 + 创建任务
 */

import { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import type { DiagnosisTaskSummary, TaskStatus, StatusResponse, SessionInfo } from '../types';
import { fetchTaskList, fetchStatus, fetchSessions, createDiagnosis, fetchPendingApproval, deleteTask } from '../api/diagnosis';

// 状态标签配置
const statusConfig: Record<TaskStatus, { label: string; color: string; bgColor: string; icon: string }> = {
  running: { label: '运行中', color: 'text-blue-600', bgColor: 'bg-blue-100', icon: '🔄' },
  completed: { label: '已完成', color: 'text-green-600', bgColor: 'bg-green-100', icon: '✅' },
  failed: { label: '已失败', color: 'text-red-600', bgColor: 'bg-red-100', icon: '❌' },
  cancelled: { label: '已取消', color: 'text-gray-600', bgColor: 'bg-gray-100', icon: '⏹️' },
};

function formatTime(dateStr: string): string {
  const date = new Date(dateStr);
  return date.toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diff = now.getTime() - date.getTime();
  const minutes = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days = Math.floor(diff / 86400000);
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return `${minutes} 分钟前`;
  if (hours < 24) return `${hours} 小时前`;
  return `${days} 天前`;
}

// ============== 系统状态栏组件 ==============
function SystemStatusBar({ onPendingClick }: { onPendingClick: () => void }) {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [pendingCount, setPendingCount] = useState(0);
  const [loading, setLoading] = useState(true);

  const loadStatus = useCallback(async () => {
    try {
      // 使用 Promise.allSettled 替代 Promise.all，避免单个请求失败导致全部失败
      const [statusResult, sessionsResult, pendingResult] = await Promise.allSettled([
        fetchStatus(),
        fetchSessions(),
        fetchPendingApproval(),
      ]);
      if (statusResult.status === 'fulfilled') setStatus(statusResult.value);
      if (sessionsResult.status === 'fulfilled') setSessions(sessionsResult.value.sessions);
      if (pendingResult.status === 'fulfilled') setPendingCount(pendingResult.value.total);
    } catch {
      // 静默失败
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadStatus();
    const interval = setInterval(loadStatus, 15000);
    return () => clearInterval(interval);
  }, [loadStatus]);

  if (loading) return null;

  return (
    <div className="bg-white/60 backdrop-blur-sm border border-slate-200 rounded-xl p-4 mb-6">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-slate-600 flex items-center">
          <span className="mr-2">📡</span>系统状态
        </h3>
        {status?.status && (
          <span className={`px-2 py-1 rounded-full text-xs font-medium ${
            status.status === 'running' ? 'bg-green-100 text-green-600' : 'bg-red-100 text-red-600'
          }`}>
            {status.status === 'running' ? '● 运行中' : '● 异常'}
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        {/* 活跃会话 */}
        <div className="bg-indigo-50 rounded-lg p-3 text-center">
          <p className="text-xl font-bold text-indigo-600">{sessions.length}</p>
          <p className="text-xs text-indigo-500">活跃会话</p>
        </div>
        {/* 调度池 */}
        {status?.pools?.scheduled && (
          <div className="bg-blue-50 rounded-lg p-3 text-center">
            <p className="text-xl font-bold text-blue-600">
              {status.pools.scheduled.running}/{status.pools.scheduled.max_concurrency}
            </p>
            <p className="text-xs text-blue-500">调度池</p>
          </div>
        )}
        {/* 即时池 */}
        {status?.pools?.immediate && (
          <div className="bg-cyan-50 rounded-lg p-3 text-center">
            <p className="text-xl font-bold text-cyan-600">
              {status.pools.immediate.running}/{status.pools.immediate.max_concurrency}
            </p>
            <p className="text-xs text-cyan-500">即时池</p>
          </div>
        )}
        {/* 锁 */}
        {status?.locks && (
          <div className="bg-slate-50 rounded-lg p-3 text-center" title={`当前有 ${status.locks.held} 个任务锁处于占用状态，共管理 ${status.locks.total} 个任务锁`}>
            <p className="text-xl font-bold text-slate-600">
              <span className={status.locks.held > 0 ? 'text-orange-500' : 'text-slate-400'}>{status.locks.held}</span>
              <span className="text-slate-300 text-base mx-0.5">/</span>
              <span>{status.locks.total}</span>
            </p>
            <p className="text-xs text-slate-500">任务锁 <span className="text-slate-400">(占用/总数)</span></p>
          </div>
        )}
        {/* 待审核 */}
        <div
          className={`rounded-lg p-3 text-center cursor-pointer transition-all hover:shadow-md ${
            pendingCount > 0 ? 'bg-amber-50 ring-2 ring-amber-200' : 'bg-slate-50'
          }`}
          onClick={onPendingClick}
        >
          <p className={`text-xl font-bold ${pendingCount > 0 ? 'text-amber-600 animate-pulse' : 'text-slate-400'}`}>
            {pendingCount}
          </p>
          <p className={`text-xs ${pendingCount > 0 ? 'text-amber-500' : 'text-slate-500'}`}>待审核</p>
        </div>
      </div>
      {/* 会话列表（折叠） */}
      {sessions.length > 0 && (
        <div className="mt-3 pt-3 border-t border-slate-100">
          <p className="text-xs text-slate-400 mb-2">活跃会话：</p>
          <div className="flex flex-wrap gap-2">
            {sessions.map((s) => (
              <span
                key={s.session_id}
                className="px-2 py-1 bg-indigo-50 text-indigo-600 rounded-md text-xs font-mono"
                title={`连接于 ${s.connected_at}`}
              >
                {s.session_id.length > 16 ? s.session_id.slice(0, 16) + '...' : s.session_id}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ============== 创建诊断任务弹窗 ==============
function CreateDiagnosisModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (taskId: string) => void;
}) {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [selectedSession, setSelectedSession] = useState('');
  const [userQuery, setUserQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetchSessions();
        setSessions(res.sessions);
        if (res.sessions.length > 0) {
          setSelectedSession(res.sessions[0].session_id);
        }
      } catch {
        setError('无法获取会话列表');
      } finally {
        setLoadingSessions(false);
      }
    })();
  }, []);

  const handleSubmit = async () => {
    if (!selectedSession || !userQuery.trim()) return;
    try {
      setLoading(true);
      setError(null);
      const res = await createDiagnosis({
        session_id: selectedSession,
        user_query: userQuery.trim(),
      });
      onCreated(res.task_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-2xl w-[90vw] max-w-lg overflow-hidden" onClick={(e) => e.stopPropagation()}>
        {/* 头部 */}
        <div className="px-6 py-4 border-b border-slate-200 bg-gradient-to-r from-blue-50 to-indigo-50">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-3">
              <span className="text-2xl">🆕</span>
              <h2 className="text-lg font-bold text-slate-800">创建诊断任务</h2>
            </div>
            <button onClick={onClose} className="w-8 h-8 flex items-center justify-center rounded-full hover:bg-slate-200 transition-colors text-slate-500">✕</button>
          </div>
        </div>
        {/* 内容 */}
        <div className="p-6 space-y-4">
          {/* 选择会话 */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">目标会话</label>
            {loadingSessions ? (
              <div className="text-sm text-slate-400">加载会话中...</div>
            ) : sessions.length === 0 ? (
              <div className="text-sm text-red-500">⚠️ 无可用会话，请确保 Arthas 客户端已连接</div>
            ) : (
              <select
                value={selectedSession}
                onChange={(e) => setSelectedSession(e.target.value)}
                className="w-full px-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
              >
                {sessions.map((s) => (
                  <option key={s.session_id} value={s.session_id}>
                    {s.session_id}
                  </option>
                ))}
              </select>
            )}
          </div>
          {/* 问题输入 */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">问题描述</label>
            <textarea
              value={userQuery}
              onChange={(e) => setUserQuery(e.target.value)}
              placeholder="请描述你要诊断的问题，例如：我的 Java 应用 CPU 占用很高，帮我排查一下"
              rows={4}
              className="w-full px-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm resize-none"
            />
          </div>
          {/* 错误 */}
          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-600">
              ❌ {error}
            </div>
          )}
        </div>
        {/* 底部 */}
        <div className="px-6 py-4 border-t border-slate-200 bg-slate-50 flex justify-end space-x-3">
          <button onClick={onClose} className="px-4 py-2 bg-slate-200 hover:bg-slate-300 text-slate-700 rounded-lg text-sm transition-colors">
            取消
          </button>
          <button
            onClick={handleSubmit}
            disabled={loading || !selectedSession || !userQuery.trim()}
            className="px-4 py-2 bg-blue-500 hover:bg-blue-600 disabled:bg-slate-300 text-white rounded-lg text-sm transition-colors flex items-center space-x-2"
          >
            {loading ? (
              <>
                <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent"></div>
                <span>创建中...</span>
              </>
            ) : (
              <span>🚀 创建任务</span>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

// ============== 主页面组件 ==============
export default function TaskList() {
  const navigate = useNavigate();
  const [tasks, setTasks] = useState<DiagnosisTaskSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [searchQuery, setSearchQuery] = useState('');
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [deletingTaskId, setDeletingTaskId] = useState<string | null>(null);

  const loadTasks = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const params: Record<string, string | number> = { limit: 100 };
      if (statusFilter) {
        params.status = statusFilter;
      }
      const response = await fetchTaskList(params);
      setTasks(response.tasks || []);
    } catch (err) {
      console.error('[TaskList] 加载任务失败:', err);
      setError(err instanceof Error ? err.message : '加载失败，请确保后端服务已启动');
      setTasks([]);
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    loadTasks();
    const interval = setInterval(loadTasks, 10000);
    return () => clearInterval(interval);
  }, [loadTasks]);

  const handleDeleteTask = useCallback(async (taskId: string, e: React.MouseEvent) => {
    e.stopPropagation(); // 阻止点击事件冒泡到任务卡片
    if (!window.confirm('确定要删除此诊断任务吗？删除后将无法恢复。')) return;
    try {
      setDeletingTaskId(taskId);
      await deleteTask(taskId);
      setTasks((prev) => prev.filter((t) => t.task_id !== taskId));
    } catch (err) {
      alert(err instanceof Error ? err.message : '删除失败');
    } finally {
      setDeletingTaskId(null);
    }
  }, []);

  const filteredTasks = tasks.filter((task) => {
    if (!searchQuery) return true;
    return (
      task.user_query.toLowerCase().includes(searchQuery.toLowerCase()) ||
      task.task_id.toLowerCase().includes(searchQuery.toLowerCase()) ||
      task.session_id.toLowerCase().includes(searchQuery.toLowerCase())
    );
  });

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50">
      {/* 头部 */}
      <header className="bg-white/80 backdrop-blur-sm border-b border-slate-200 sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-3">
              <div className="w-10 h-10 bg-gradient-to-br from-blue-500 to-indigo-600 rounded-xl flex items-center justify-center shadow-lg">
                <span className="text-white text-xl">🔍</span>
              </div>
              <div>
                <h1 className="text-xl font-bold text-slate-800">Arthas 诊断中心</h1>
                <p className="text-sm text-slate-500">诊断任务管理与监控</p>
              </div>
            </div>
            <div className="flex items-center space-x-3">
              <button
                onClick={() => navigate('/pending-approval')}
                className="px-4 py-2 bg-amber-500 hover:bg-amber-600 text-white rounded-lg transition-colors flex items-center space-x-2 shadow-md hover:shadow-lg"
              >
                <span>🔔</span>
                <span>审核管理</span>
              </button>
              <button
                onClick={() => setShowCreateModal(true)}
                className="px-4 py-2 bg-green-500 hover:bg-green-600 text-white rounded-lg transition-colors flex items-center space-x-2 shadow-md hover:shadow-lg"
              >
                <span>🆕</span>
                <span>创建任务</span>
              </button>
              <button
                onClick={loadTasks}
                className="px-4 py-2 bg-blue-500 hover:bg-blue-600 text-white rounded-lg transition-colors flex items-center space-x-2 shadow-md hover:shadow-lg"
              >
                <span>🔄</span>
                <span>刷新</span>
              </button>
            </div>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* 系统状态栏 */}
        <SystemStatusBar onPendingClick={() => navigate('/pending-approval')} />

        {/* 筛选区 */}
        <div className="mb-6 flex flex-col sm:flex-row gap-4">
          <div className="flex-1">
            <div className="relative">
              <input
                type="text"
                placeholder="搜索任务（ID、会话ID、问题内容）..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full px-4 py-3 pl-10 bg-white border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent shadow-sm"
              />
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400">🔍</span>
            </div>
          </div>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="pl-4 pr-10 py-3 bg-white border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-500 cursor-pointer shadow-sm appearance-none bg-[url('data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%2216%22%20height%3D%2216%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22none%22%20stroke%3D%22%2394a3b8%22%20stroke-width%3D%222%22%3E%3Cpath%20d%3D%22M6%209l6%206%206-6%22%2F%3E%3C%2Fsvg%3E')] bg-no-repeat bg-[right_0.75rem_center]"
          >
            <option value="">全部状态</option>
            <option value="running">🔄 运行中</option>
            <option value="completed">✅ 已完成</option>
            <option value="failed">❌ 已失败</option>
            <option value="cancelled">⏹️ 已取消</option>
          </select>
        </div>

        {/* 统计卡片 */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
          {(['running', 'completed', 'failed', 'cancelled'] as TaskStatus[]).map((status) => {
            const config = statusConfig[status];
            const count = tasks.filter((t) => t.status === status).length;
            return (
              <div
                key={status}
                onClick={() => setStatusFilter(statusFilter === status ? '' : status)}
                className={`p-4 rounded-xl cursor-pointer transition-all ${
                  statusFilter === status ? 'ring-2 ring-blue-500 shadow-lg' : 'hover:shadow-md'
                } ${config.bgColor}`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-2xl">{config.icon}</span>
                  <span className={`text-2xl font-bold ${config.color}`}>{count}</span>
                </div>
                <p className={`mt-1 text-sm ${config.color}`}>{config.label}</p>
              </div>
            );
          })}
        </div>

        {/* 加载状态 */}
        {loading && (
          <div className="flex justify-center items-center py-12">
            <div className="animate-spin rounded-full h-10 w-10 border-4 border-blue-500 border-t-transparent"></div>
            <span className="ml-3 text-slate-500">加载中...</span>
          </div>
        )}

        {/* 错误提示 */}
        {error && (
          <div className="bg-red-50 border border-red-200 rounded-xl p-4 mb-6">
            <div className="flex items-center">
              <span className="text-red-500 mr-2">❌</span>
              <span className="text-red-700">{error}</span>
            </div>
          </div>
        )}

        {/* 任务列表 */}
        {!loading && !error && (
          <div className="space-y-4">
            {filteredTasks.length === 0 ? (
              <div className="text-center py-12">
                <div className="text-6xl mb-4">📋</div>
                <p className="text-slate-500 mb-4">暂无诊断任务</p>
                <button
                  onClick={() => setShowCreateModal(true)}
                  className="px-6 py-3 bg-blue-500 hover:bg-blue-600 text-white rounded-xl transition-colors shadow-md"
                >
                  🆕 创建第一个诊断任务
                </button>
              </div>
            ) : (
              filteredTasks.map((task) => {
                const config = statusConfig[task.status];
                return (
                  <div
                    key={task.task_id}
                    onClick={() => navigate(`/task/${task.task_id}`)}
                    className="bg-white rounded-xl p-5 shadow-sm hover:shadow-lg transition-all cursor-pointer border border-slate-100 hover:border-blue-200"
                  >
                    <div className="flex items-start justify-between">
                      <div className="flex-1 min-w-0">
                        <h3 className="text-lg font-medium text-slate-800 truncate pr-4">
                          {task.user_query}
                        </h3>
                        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-slate-500">
                          <span className="flex items-center">
                            <span className="mr-1">🔑</span>
                            <code className="text-xs bg-slate-100 px-2 py-0.5 rounded">
                              {task.task_id.slice(0, 8)}...
                            </code>
                          </span>
                          <span className="flex items-center">
                            <span className="mr-1">📡</span>
                            {task.session_id.length > 16 ? task.session_id.slice(0, 16) + '...' : task.session_id}
                          </span>
                          <span className="flex items-center">
                            <span className="mr-1">📊</span>
                            阶段 {task.current_stage_seq}
                          </span>
                          <span className="flex items-center">
                            <span className="mr-1">🕐</span>
                            {formatRelativeTime(task.updated_at)}
                          </span>
                        </div>
                        {task.conclusion && (
                          <p className="mt-2 text-sm text-slate-600 line-clamp-2">
                            <span className="font-medium text-green-600">结论：</span>
                            {task.conclusion.slice(0, 100)}...
                          </p>
                        )}
                      </div>
                      <div className="flex items-center space-x-2">
                        <div className={`flex items-center px-3 py-1.5 rounded-full ${config.bgColor}`}>
                          <span className="mr-1.5">{config.icon}</span>
                          <span className={`text-sm font-medium ${config.color}`}>{config.label}</span>
                        </div>
                        <button
                          onClick={(e) => handleDeleteTask(task.task_id, e)}
                          disabled={deletingTaskId === task.task_id}
                          className="w-8 h-8 flex items-center justify-center rounded-full hover:bg-red-100 text-slate-400 hover:text-red-500 transition-colors"
                          title="删除任务"
                        >
                          {deletingTaskId === task.task_id ? (
                            <div className="animate-spin rounded-full h-4 w-4 border-2 border-red-400 border-t-transparent"></div>
                          ) : (
                            <span className="text-sm">🗑️</span>
                          )}
                        </button>
                      </div>
                    </div>
                    <div className="mt-3 pt-3 border-t border-slate-100 flex items-center justify-between text-xs text-slate-400">
                      <span>创建于 {formatTime(task.created_at)}</span>
                      <span>更新于 {formatTime(task.updated_at)}</span>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        )}
      </main>

      {/* 创建任务弹窗 */}
      {showCreateModal && (
        <CreateDiagnosisModal
          onClose={() => setShowCreateModal(false)}
          onCreated={(taskId) => {
            setShowCreateModal(false);
            navigate(`/task/${taskId}`);
          }}
        />
      )}
    </div>
  );
}
