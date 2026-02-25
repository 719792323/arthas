/**
 * 待审核管理页面
 * 显示所有等待人工审核的阶段，支持审核通过/拒绝
 */

import { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import type { DiagnosisStage } from '../types';
import { fetchPendingApproval, approveStage, rejectStage } from '../api/diagnosis';

function formatTime(dateStr: string): string {
  const date = new Date(dateStr);
  return date.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

export default function PendingApproval() {
  const navigate = useNavigate();
  const [stages, setStages] = useState<DiagnosisStage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<number | null>(null);

  const loadStages = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const res = await fetchPendingApproval();
      setStages(res.stages);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadStages();
    const interval = setInterval(loadStages, 5000);
    return () => clearInterval(interval);
  }, [loadStages]);

  const handleApprove = async (stageId: number) => {
    try {
      setActionLoading(stageId);
      await approveStage(stageId);
      loadStages();
    } catch (err) {
      alert(err instanceof Error ? err.message : '审核失败');
    } finally {
      setActionLoading(null);
    }
  };

  const handleReject = async (stageId: number) => {
    const reason = prompt('请输入拒绝原因（可选）');
    try {
      setActionLoading(stageId);
      await rejectStage(stageId, reason || undefined);
      loadStages();
    } catch (err) {
      alert(err instanceof Error ? err.message : '审核失败');
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-amber-50">
      {/* 头部 */}
      <header className="bg-white/80 backdrop-blur-sm border-b border-slate-200 sticky top-0 z-10">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-4">
              <button onClick={() => navigate('/')} className="p-2 hover:bg-slate-100 rounded-lg transition-colors">
                <span className="text-xl">←</span>
              </button>
              <div>
                <h1 className="text-lg font-bold text-slate-800">🔔 审核管理</h1>
                <p className="text-sm text-slate-500">管理待审核的高危命令</p>
              </div>
            </div>
            <button
              onClick={loadStages}
              className="px-4 py-2 bg-amber-500 hover:bg-amber-600 text-white rounded-lg transition-colors flex items-center space-x-2 shadow-md"
            >
              <span>🔄</span>
              <span>刷新</span>
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* 加载状态 */}
        {loading && stages.length === 0 && (
          <div className="flex justify-center items-center py-12">
            <div className="animate-spin rounded-full h-10 w-10 border-4 border-amber-500 border-t-transparent"></div>
            <span className="ml-3 text-slate-500">加载中...</span>
          </div>
        )}

        {/* 错误 */}
        {error && (
          <div className="bg-red-50 border border-red-200 rounded-xl p-4 mb-6">
            <span className="text-red-500 mr-2">❌</span>
            <span className="text-red-700">{error}</span>
          </div>
        )}

        {/* 无待审核 */}
        {!loading && stages.length === 0 && (
          <div className="text-center py-16">
            <div className="text-6xl mb-4">✅</div>
            <p className="text-slate-500 text-lg">当前没有待审核的命令</p>
            <p className="text-slate-400 text-sm mt-2">高危命令（如 heapdump、redefine 等）执行前需要人工审核</p>
          </div>
        )}

        {/* 待审核列表 */}
        <div className="space-y-4">
          {stages.map((stage) => (
            <div
              key={stage.id}
              className="bg-white rounded-xl shadow-md border-2 border-amber-200 overflow-hidden hover:shadow-lg transition-all"
            >
              {/* 头部 */}
              <div className="p-4 bg-amber-50 border-b border-amber-200 flex items-center justify-between flex-wrap gap-2">
                <div className="flex items-center space-x-3">
                  <span className="text-2xl">⚠️</span>
                  <div>
                    <span className="font-semibold text-amber-800">高危命令审核</span>
                    <span className="text-sm text-amber-600 ml-2">Stage #{stage.stage_seq}</span>
                  </div>
                </div>
                <div className="flex items-center space-x-2 text-sm text-amber-600">
                  <span>创建于 {formatTime(stage.created_at)}</span>
                </div>
              </div>

              {/* 命令详情 */}
              <div className="p-4 space-y-3">
                {/* 关联任务 */}
                <div className="flex items-center text-sm">
                  <span className="text-slate-500 w-20 shrink-0">任务 ID:</span>
                  <code
                    className="text-xs bg-slate-100 px-2 py-1 rounded cursor-pointer hover:bg-blue-100 hover:text-blue-600 transition-colors"
                    onClick={() => navigate(`/task/${stage.task_id}`)}
                  >
                    {stage.task_id}
                  </code>
                </div>

                {/* 工具名称 */}
                <div className="flex items-center text-sm">
                  <span className="text-slate-500 w-20 shrink-0">命令名称:</span>
                  <span className="px-3 py-1 bg-red-100 text-red-700 rounded-lg font-mono font-semibold">
                    🔧 {stage.tool_name || '未知'}
                  </span>
                </div>

                {/* 工具参数 */}
                {stage.tool_arguments && (
                  <div>
                    <span className="text-sm text-slate-500 block mb-1">调用参数:</span>
                    <pre className="text-xs bg-slate-900 text-slate-100 p-3 rounded-lg overflow-x-auto max-h-40 font-mono whitespace-pre-wrap break-words">
                      {JSON.stringify(stage.tool_arguments, null, 2)}
                    </pre>
                  </div>
                )}
              </div>

              {/* 操作按钮 */}
              <div className="p-4 border-t border-slate-100 bg-slate-50 flex items-center justify-end space-x-3">
                <button
                  onClick={() => handleReject(stage.id)}
                  disabled={actionLoading === stage.id}
                  className="px-5 py-2.5 bg-red-100 hover:bg-red-200 disabled:bg-slate-200 text-red-600 rounded-lg transition-colors text-sm font-medium flex items-center space-x-2"
                >
                  <span>❌</span>
                  <span>拒绝执行</span>
                </button>
                <button
                  onClick={() => handleApprove(stage.id)}
                  disabled={actionLoading === stage.id}
                  className="px-5 py-2.5 bg-green-500 hover:bg-green-600 disabled:bg-slate-300 text-white rounded-lg transition-colors text-sm font-medium flex items-center space-x-2"
                >
                  {actionLoading === stage.id ? (
                    <>
                      <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent"></div>
                      <span>处理中...</span>
                    </>
                  ) : (
                    <>
                      <span>✅</span>
                      <span>批准执行</span>
                    </>
                  )}
                </button>
              </div>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}
