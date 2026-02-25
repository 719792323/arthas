/**
 * 任务详情页面
 * 显示诊断任务的详细过程和时间线
 */

import { useEffect, useState, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import type { DiagnosisStage, DiagnosisTaskSummary, StageType, StageStatus } from '../types';
import { fetchTaskDetail, approveStage, rejectStage, fetchConversation } from '../api/diagnosis';
import type { ConversationResponse } from '../api/diagnosis';
import MarkdownRenderer from '../components/MarkdownRenderer';

// 阶段类型配置 - 更丰富的渐变色
const stageTypeConfig: Record<StageType, {
  label: string; icon: string; color: string; bgColor: string;
  gradientFrom: string; gradientTo: string; borderColor: string;
  dotColor: string;
}> = {
  USER_QUERY: {
    label: '用户提问', icon: '💬',
    color: 'text-violet-700', bgColor: 'bg-violet-50',
    gradientFrom: 'from-violet-500', gradientTo: 'to-purple-600',
    borderColor: 'border-violet-200', dotColor: 'bg-violet-500',
  },
  LLM_THINKING: {
    label: 'AI 推理', icon: '🧠',
    color: 'text-blue-700', bgColor: 'bg-blue-50',
    gradientFrom: 'from-blue-500', gradientTo: 'to-cyan-500',
    borderColor: 'border-blue-200', dotColor: 'bg-blue-500',
  },
  TOOL_CALL: {
    label: '工具调用', icon: '🔧',
    color: 'text-amber-700', bgColor: 'bg-amber-50',
    gradientFrom: 'from-amber-500', gradientTo: 'to-orange-500',
    borderColor: 'border-amber-200', dotColor: 'bg-amber-500',
  },
  TOOL_RESULT: {
    label: '执行结果', icon: '📋',
    color: 'text-teal-700', bgColor: 'bg-teal-50',
    gradientFrom: 'from-teal-500', gradientTo: 'to-emerald-500',
    borderColor: 'border-teal-200', dotColor: 'bg-teal-500',
  },
  LLM_CONCLUSION: {
    label: 'AI 结论', icon: '✨',
    color: 'text-emerald-700', bgColor: 'bg-emerald-50',
    gradientFrom: 'from-emerald-500', gradientTo: 'to-green-600',
    borderColor: 'border-emerald-200', dotColor: 'bg-emerald-500',
  },
  CONTEXT_SUMMARY: {
    label: '上下文摘要', icon: '📦',
    color: 'text-indigo-700', bgColor: 'bg-indigo-50',
    gradientFrom: 'from-indigo-500', gradientTo: 'to-violet-500',
    borderColor: 'border-indigo-200', dotColor: 'bg-indigo-500',
  },
};

// 阶段状态配置
const stageStatusConfig: Record<StageStatus, { label: string; color: string; bgColor: string; ring: string }> = {
  pending: { label: '待处理', color: 'text-yellow-700', bgColor: 'bg-yellow-50', ring: 'ring-yellow-200' },
  waiting_approval: { label: '待审核', color: 'text-amber-700', bgColor: 'bg-amber-50', ring: 'ring-amber-300' },
  completed: { label: '已完成', color: 'text-emerald-700', bgColor: 'bg-emerald-50', ring: 'ring-emerald-200' },
  failed: { label: '失败', color: 'text-red-700', bgColor: 'bg-red-50', ring: 'ring-red-200' },
};

function formatTime(dateStr: string): string {
  const date = new Date(dateStr);
  return date.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

function formatDuration(startStr: string, endStr: string): string {
  const start = new Date(startStr);
  const end = new Date(endStr);
  const diff = end.getTime() - start.getTime();
  if (diff < 1000) return `${diff}ms`;
  if (diff < 60000) return `${(diff / 1000).toFixed(1)}s`;
  return `${Math.floor(diff / 60000)}m ${Math.floor((diff % 60000) / 1000)}s`;
}

// JSON 查看器
function JsonViewer({ data, maxHeight = '400px' }: { data: unknown; maxHeight?: string }) {
  const [expanded, setExpanded] = useState(false);

  if (data === null || data === undefined) {
    return <span className="text-slate-400 italic text-xs">无数据</span>;
  }

  const formatted = JSON.stringify(data, null, 2);
  const lines = formatted.split('\n');
  const isLong = lines.length > 8;

  return (
    <div className="relative w-full min-w-0">
      <pre
        className={`text-xs bg-[#1e1e2e] text-[#cdd6f4] p-4 rounded-xl font-mono w-full min-w-0 overflow-x-auto overflow-y-auto whitespace-pre leading-relaxed ${
          !expanded && isLong ? 'max-h-36' : ''
        }`}
        style={{ maxHeight: expanded ? maxHeight : undefined }}
      >
        <code>{formatted}</code>
      </pre>
      {isLong && (
        <button
          onClick={(e) => { e.stopPropagation(); setExpanded(!expanded); }}
          className="absolute bottom-2 right-2 px-3 py-1 bg-white/10 hover:bg-white/20 backdrop-blur text-xs text-white/80 rounded-lg transition-all"
        >
          {expanded ? '▲ 收起' : `▼ 展开 (${lines.length} 行)`}
        </button>
      )}
    </div>
  );
}

// 可折叠区块
function CollapsibleSection({
  title, icon, children, defaultOpen = false, badge,
}: {
  title: string; icon: string; children: React.ReactNode; defaultOpen?: boolean; badge?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border border-slate-100 rounded-xl overflow-hidden bg-white/50">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-slate-50/80 transition-colors text-left"
      >
        <div className="flex items-center space-x-2">
          <span className="text-sm">{icon}</span>
          <span className="text-xs font-semibold text-slate-600 uppercase tracking-wide">{title}</span>
          {badge && (
            <span className="px-2 py-0.5 bg-slate-100 text-slate-500 text-xs rounded-full">{badge}</span>
          )}
        </div>
        <span className={`text-slate-400 text-xs transition-transform duration-200 ${open ? 'rotate-180' : ''}`}>
          ▼
        </span>
      </button>
      {open && <div className="px-4 pb-4 border-t border-slate-100">{children}</div>}
    </div>
  );
}

// ============== 摘要对比弹窗组件 ==============
function SummaryCompareModal({
  stage, onClose,
}: {
  stage: DiagnosisStage; onClose: () => void;
}) {
  const [activeTab, setActiveTab] = useState<'side' | 'summary' | 'original'>('side');

  const originalContent = stage.tool_result || '';
  const summaryContent = stage.summarized_content || '';
  const compressionRatio = stage.original_tokens && stage.summary_tokens
    ? ((1 - stage.summary_tokens / stage.original_tokens) * 100).toFixed(1)
    : '0';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-md animate-fadeIn" onClick={onClose}>
      <div
        className="bg-white rounded-2xl shadow-2xl w-[95vw] max-w-6xl max-h-[85vh] flex flex-col overflow-hidden animate-slideUp"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100 bg-gradient-to-r from-violet-50 via-indigo-50 to-blue-50 shrink-0">
          <div className="flex items-center space-x-3">
            <div className="w-10 h-10 bg-gradient-to-br from-violet-500 to-indigo-600 rounded-xl flex items-center justify-center shadow-lg">
              <span className="text-white text-lg">🔍</span>
            </div>
            <div>
              <h2 className="text-lg font-bold text-slate-800">上下文压缩对比</h2>
              <p className="text-xs text-slate-500">
                Stage #{stage.stage_seq} · {stage.tool_name || stage.stage_type} ·
                <span className="font-medium ml-1">{stage.summary_type === 'llm' ? 'LLM 智能摘要' : stage.summary_type === 'rule' ? '规则裁剪' : '未知'}</span>
              </p>
            </div>
          </div>
          <button onClick={onClose} className="w-9 h-9 flex items-center justify-center rounded-xl hover:bg-slate-200/80 transition-colors text-slate-400 hover:text-slate-600">✕</button>
        </div>

        <div className="px-6 py-3 bg-slate-50/80 border-b border-slate-100 flex items-center space-x-8 shrink-0">
          <div className="flex items-center space-x-2">
            <div className="w-2.5 h-2.5 rounded-full bg-red-400"></div>
            <span className="text-sm text-slate-600">原始: <strong className="text-red-600">{stage.original_tokens?.toLocaleString()}</strong> tokens</span>
          </div>
          <div className="flex items-center space-x-2">
            <div className="w-2.5 h-2.5 rounded-full bg-emerald-400"></div>
            <span className="text-sm text-slate-600">摘要后: <strong className="text-emerald-600">{stage.summary_tokens?.toLocaleString()}</strong> tokens</span>
          </div>
          <div className="flex items-center space-x-2">
            <div className="w-2.5 h-2.5 rounded-full bg-blue-400"></div>
            <span className="text-sm text-slate-600">节省: <strong className="text-blue-600">{compressionRatio}%</strong></span>
          </div>
        </div>

        <div className="px-6 py-2 border-b border-slate-100 flex items-center space-x-1 shrink-0">
          {(['side', 'summary', 'original'] as const).map((tab) => (
            <button key={tab} onClick={() => setActiveTab(tab)}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                activeTab === tab
                  ? 'bg-gradient-to-r from-blue-500 to-indigo-500 text-white shadow-md'
                  : 'text-slate-500 hover:bg-slate-100'
              }`}
            >
              {tab === 'side' ? '📊 左右对比' : tab === 'summary' ? '📝 仅摘要' : '📄 仅原始'}
            </button>
          ))}
        </div>

        <div className="flex-1 overflow-hidden p-4">
          {activeTab === 'side' && (
            <div className="grid grid-cols-2 gap-4 h-full">
              <div className="flex flex-col min-h-0 border border-red-200 rounded-xl overflow-hidden">
                <div className="px-4 py-2 bg-red-50 border-b border-red-200 shrink-0">
                  <span className="text-sm font-semibold text-red-700">📄 原始内容</span>
                  <span className="text-xs text-red-400 ml-2">({stage.original_tokens?.toLocaleString()} tokens)</span>
                </div>
                <pre className="flex-1 p-3 text-xs font-mono text-slate-700 bg-white overflow-auto whitespace-pre-wrap break-words">{originalContent}</pre>
              </div>
              <div className="flex flex-col min-h-0 border border-emerald-200 rounded-xl overflow-hidden">
                <div className="px-4 py-2 bg-emerald-50 border-b border-emerald-200 shrink-0">
                  <span className="text-sm font-semibold text-emerald-700">📝 摘要内容</span>
                  <span className="text-xs text-emerald-400 ml-2">({stage.summary_tokens?.toLocaleString()} tokens)</span>
                </div>
                <div className="flex-1 p-3 bg-white overflow-auto">
                  <MarkdownRenderer content={summaryContent} />
                </div>
              </div>
            </div>
          )}
          {activeTab === 'summary' && (
            <div className="h-full overflow-auto">
              <div className="bg-emerald-50/50 border border-emerald-200 rounded-xl overflow-hidden">
                <div className="px-4 py-2 border-b border-emerald-200">
                  <span className="text-sm font-semibold text-emerald-700">📝 摘要内容</span>
                </div>
                <div className="p-4"><MarkdownRenderer content={summaryContent} /></div>
              </div>
            </div>
          )}
          {activeTab === 'original' && (
            <div className="h-full overflow-auto">
              <pre className="text-xs font-mono text-slate-700 bg-slate-50 border border-slate-200 rounded-xl p-4 whitespace-pre-wrap break-words">{originalContent}</pre>
            </div>
          )}
        </div>

        <div className="px-6 py-3 border-t border-slate-100 bg-slate-50/80 flex justify-end shrink-0">
          <button onClick={onClose} className="px-5 py-2 bg-slate-200 hover:bg-slate-300 text-slate-700 rounded-xl transition-colors text-sm font-medium">关闭</button>
        </div>
      </div>
    </div>
  );
}

// ============== 完整 Prompt 对话弹窗组件 ==============
function ConversationModal({
  taskId, onClose,
}: {
  taskId: string; onClose: () => void;
}) {
  const [data, setData] = useState<ConversationResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setLoading(true);
        const res = await fetchConversation(taskId);
        if (!cancelled) setData(res);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : '加载失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [taskId]);

  const parseConversation = (text: string) => {
    const blocks: { role: string; content: string }[] = [];
    const lines = text.split('\n');
    let currentRole = '';
    let currentContent: string[] = [];
    let headerPassed = false;

    for (const line of lines) {
      if (!headerPassed) {
        if (line.startsWith('==')) { headerPassed = true; continue; }
        continue;
      }
      const roleMatch = line.match(/^\[(SYSTEM|USER|ASSISTANT|TOOL[^\]]*)\]$/);
      if (roleMatch) {
        if (currentRole && currentContent.length > 0) {
          blocks.push({ role: currentRole, content: currentContent.join('\n').trim() });
        }
        currentRole = roleMatch[1];
        currentContent = [];
      } else {
        currentContent.push(line);
      }
    }
    if (currentRole && currentContent.length > 0) {
      blocks.push({ role: currentRole, content: currentContent.join('\n').trim() });
    }
    return blocks;
  };

  const getRoleConfig = (role: string) => {
    if (role === 'SYSTEM') return { icon: '⚙️', label: 'System Prompt', gradient: 'from-slate-500 to-gray-600', bgColor: 'bg-slate-50', borderColor: 'border-slate-200' };
    if (role === 'USER') return { icon: '👤', label: 'User', gradient: 'from-violet-500 to-purple-600', bgColor: 'bg-violet-50', borderColor: 'border-violet-200' };
    if (role === 'ASSISTANT') return { icon: '🤖', label: 'Assistant', gradient: 'from-blue-500 to-cyan-500', bgColor: 'bg-blue-50', borderColor: 'border-blue-200' };
    if (role.startsWith('TOOL')) return { icon: '🔧', label: role, gradient: 'from-amber-500 to-orange-500', bgColor: 'bg-amber-50', borderColor: 'border-amber-200' };
    return { icon: '❓', label: role, gradient: 'from-gray-400 to-gray-500', bgColor: 'bg-gray-50', borderColor: 'border-gray-200' };
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-md animate-fadeIn" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-2xl w-[90vw] max-w-4xl max-h-[85vh] flex flex-col overflow-hidden animate-slideUp" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100 bg-gradient-to-r from-indigo-50 via-blue-50 to-cyan-50 shrink-0">
          <div className="flex items-center space-x-3">
            <div className="w-10 h-10 bg-gradient-to-br from-indigo-500 to-blue-600 rounded-xl flex items-center justify-center shadow-lg">
              <span className="text-white text-lg">📜</span>
            </div>
            <div>
              <h2 className="text-lg font-bold text-slate-800">完整 Prompt 过程</h2>
              <p className="text-xs text-slate-500">查看发送给 LLM 的完整对话上下文</p>
            </div>
          </div>
          <button onClick={onClose} className="w-9 h-9 flex items-center justify-center rounded-xl hover:bg-slate-200/80 transition-colors text-slate-400 hover:text-slate-600">✕</button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
          {loading && (
            <div className="flex justify-center items-center py-12">
              <div className="animate-spin rounded-full h-8 w-8 border-4 border-blue-500 border-t-transparent"></div>
              <span className="ml-3 text-slate-500">加载完整对话中...</span>
            </div>
          )}
          {error && (
            <div className="bg-red-50 border border-red-200 rounded-xl p-4">
              <span className="text-red-500 mr-2">❌</span>
              <span className="text-red-700">{error}</span>
            </div>
          )}
          {data && (() => {
            const blocks = parseConversation(data.conversation_text);
            return blocks.length > 0 ? (
              blocks.map((block, idx) => {
                const config = getRoleConfig(block.role);
                return (
                  <div key={idx} className={`rounded-xl border ${config.borderColor} ${config.bgColor} overflow-hidden`}>
                    <div className={`flex items-center px-4 py-2.5 border-b ${config.borderColor}`}>
                      <div className={`w-7 h-7 rounded-lg bg-gradient-to-br ${config.gradient} flex items-center justify-center mr-3 shadow-sm`}>
                        <span className="text-white text-sm">{config.icon}</span>
                      </div>
                      <span className="text-sm font-semibold text-slate-700">{config.label}</span>
                      <span className="ml-auto text-xs text-slate-400">#{idx + 1}</span>
                    </div>
                    <div className="px-4 py-3 overflow-x-auto">
                      {block.role === 'SYSTEM' ? (
                        <pre className="text-xs bg-[#1e1e2e] text-[#cdd6f4] p-4 rounded-xl overflow-auto max-h-80 font-mono whitespace-pre-wrap break-words leading-relaxed">{block.content}</pre>
                      ) : (
                        <MarkdownRenderer content={block.content} />
                      )}
                    </div>
                  </div>
                );
              })
            ) : (
              <pre className="text-sm bg-slate-50 text-slate-700 p-4 rounded-xl overflow-auto max-h-[60vh] font-mono whitespace-pre-wrap break-words">{data.conversation_text}</pre>
            );
          })()}
        </div>

        <div className="flex items-center justify-between px-6 py-3 border-t border-slate-100 bg-slate-50/80 shrink-0">
          <span className="text-xs text-slate-400">{data ? `任务: ${data.task_id} | 状态: ${data.status}` : ''}</span>
          <button onClick={onClose} className="px-5 py-2 bg-slate-200 hover:bg-slate-300 text-slate-700 rounded-xl transition-colors text-sm font-medium">关闭</button>
        </div>
      </div>
    </div>
  );
}

// ============== 阶段卡片组件 ==============
function StageCard({
  stage, isLast, onApprove, onReject,
}: {
  stage: DiagnosisStage; isLast: boolean;
  onApprove?: () => void; onReject?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [queryExpanded, setQueryExpanded] = useState(false);
  const [showCompare, setShowCompare] = useState(false);
  const typeConfig = stageTypeConfig[stage.stage_type] || {
    label: stage.stage_type, icon: '❓', color: 'text-slate-600', bgColor: 'bg-slate-100',
    gradientFrom: 'from-slate-400', gradientTo: 'to-slate-500', borderColor: 'border-slate-200', dotColor: 'bg-slate-400',
  } as typeof stageTypeConfig[StageType];
  const statusConfig = stageStatusConfig[stage.status];

  // 提取显示内容 - USER_QUERY 现在也使用 Markdown
  const getDisplayContent = (): { text: string; useMarkdown: boolean } => {
    switch (stage.stage_type) {
      case 'USER_QUERY':
        return { text: (stage.input_data?.user_query as string) || '无问题内容', useMarkdown: true };
      case 'LLM_THINKING':
        return {
          text: (stage.output_data?.thinking as string) || (stage.output_data?.reasoning as string) || '正在思考中...',
          useMarkdown: true,
        };
      case 'TOOL_CALL':
        return { text: '', useMarkdown: false }; // TOOL_CALL 使用自定义渲染
      case 'TOOL_RESULT':
        return { text: stage.tool_result?.slice(0, 200) || '无结果', useMarkdown: false };
      case 'LLM_CONCLUSION':
        return {
          text: (stage.output_data?.conclusion as string) || (stage.output_data?.answer as string) || '生成结论中...',
          useMarkdown: true,
        };
      case 'CONTEXT_SUMMARY':
        return {
          text: (stage.output_data?.summary as string) || '全文上下文摘要',
          useMarkdown: true,
        };
      default:
        return { text: '未知阶段类型', useMarkdown: false };
    }
  };

  const displayContent = getDisplayContent();

  // TOOL_CALL 专属渲染
  const renderToolCall = () => {
    return (
      <div className="space-y-3">
        {/* 工具名称 */}
        <div className="flex items-center space-x-2">
          <span className="text-xs font-semibold text-amber-600 uppercase tracking-wide">调用工具</span>
          <code className="px-3 py-1 bg-amber-100 text-amber-800 rounded-lg text-sm font-bold font-mono">
            {stage.tool_name || '未知'}
          </code>
        </div>

        {/* 工具参数 */}
        {stage.tool_arguments && (
          <div>
            <p className="text-xs text-slate-500 mb-1.5 font-medium">📥 参数</p>
            <JsonViewer data={stage.tool_arguments} />
          </div>
        )}

        {/* 执行结果 */}
        {stage.tool_result && (
          <div>
            <p className="text-xs text-slate-500 mb-1.5 font-medium">📤 执行结果</p>
            <ToolResultViewer result={stage.tool_result} />
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="relative flex min-w-0 group">
      {/* 时间线 */}
      <div className="flex flex-col items-center mr-5 shrink-0 relative">
        {/* 连接线（上） */}
<div className={`w-11 h-11 rounded-2xl bg-gradient-to-br ${typeConfig.gradientFrom} ${typeConfig.gradientTo} flex items-center justify-center shadow-lg group-hover:scale-110 transition-transform duration-200 z-10`}>
          <span className="text-lg text-white drop-shadow">{typeConfig.icon}</span>
        </div>
        {!isLast && (
          <div className="flex-1 w-0.5 my-2 relative">
            <div className={`absolute inset-0 ${typeConfig.dotColor} opacity-20 rounded-full`}></div>
            <div className="absolute inset-0 bg-gradient-to-b from-slate-300 to-slate-200 rounded-full"></div>
          </div>
        )}
      </div>

      {/* 内容卡片 */}
      <div className="flex-1 pb-8 min-w-0">
        <div className={`bg-white rounded-2xl shadow-sm border overflow-hidden transition-all duration-200 hover:shadow-lg ${
          stage.status === 'waiting_approval'
            ? 'border-amber-300 ring-2 ring-amber-100 shadow-amber-100/50'
            : `${typeConfig.borderColor} hover:border-slate-200`
        }`}>
          {/* 卡片顶部彩色条 */}
          <div className={`h-1 bg-gradient-to-r ${typeConfig.gradientFrom} ${typeConfig.gradientTo}`}></div>

          {/* 头部 */}
          <div className="flex items-center justify-between px-5 py-3 border-b border-slate-50 flex-wrap gap-2">
            <div className="flex items-center space-x-2.5 flex-wrap gap-1.5">
              <span className={`px-2.5 py-1 rounded-lg text-xs font-bold ${typeConfig.bgColor} ${typeConfig.color}`}>
                {typeConfig.label}
              </span>
              <span className={`px-2.5 py-1 rounded-lg text-xs font-medium ${statusConfig.bgColor} ${statusConfig.color} ring-1 ${statusConfig.ring}`}>
                {statusConfig.label}
              </span>
              {stage.tool_name && stage.stage_type !== 'TOOL_CALL' && (
                <code className="px-2 py-1 rounded-lg text-xs bg-slate-100 text-slate-600 font-mono">
                  {stage.tool_name}
                </code>
              )}
            </div>
            <div className="flex items-center space-x-2 text-xs text-slate-400">
              <span className="font-mono font-medium">#{stage.stage_seq}</span>
              <span className="text-slate-300">·</span>
              <span>{formatTime(stage.created_at)}</span>
              {stage.updated_at !== stage.created_at && (
                <>
                  <span className="text-slate-300">·</span>
                  <span className="text-emerald-500 font-medium">
                    ⏱ {formatDuration(stage.created_at, stage.updated_at)}
                  </span>
                </>
              )}
            </div>
          </div>

          {/* 主体内容 */}
          <div className="p-5 min-w-0 overflow-hidden">
            <div className="text-sm text-slate-700 min-w-0 overflow-hidden leading-relaxed">
              {/* CONTEXT_SUMMARY 特殊渲染 */}
              {stage.stage_type === 'CONTEXT_SUMMARY' ? (
                <div className="bg-gradient-to-br from-indigo-50 to-violet-50 border border-indigo-200 rounded-xl p-5">
                  <div className="flex items-center mb-4">
                    <div className="w-8 h-8 bg-gradient-to-br from-indigo-500 to-violet-500 rounded-lg flex items-center justify-center mr-3 shadow">
                      <span className="text-white">📦</span>
                    </div>
                    <span className="font-bold text-indigo-700 text-base">全文上下文摘要</span>
                  </div>
                  {stage.input_data && (
                    <div className="grid grid-cols-2 gap-3 mb-4">
                      <div className="bg-white/80 rounded-xl p-3 text-center shadow-sm">
                        <p className="text-indigo-600 font-bold text-sm">
                          Stage #{(stage.input_data.from_stage_seq as number)} → #{(stage.input_data.to_stage_seq as number)}
                        </p>
                        <p className="text-xs text-slate-500 mt-0.5">覆盖范围</p>
                      </div>
                      <div className="bg-white/80 rounded-xl p-3 text-center shadow-sm">
                        <p className="text-indigo-600 font-bold text-sm">
                          {(stage.input_data.original_tokens as number)?.toLocaleString()} → {(stage.output_data?.summary_tokens as number)?.toLocaleString()}
                        </p>
                        <p className="text-xs text-slate-500 mt-0.5">tokens 压缩</p>
                      </div>
                    </div>
                  )}
                  {!!stage.output_data?.summary && (
                    <div className="bg-white/60 rounded-xl p-4">
                      <MarkdownRenderer content={String(stage.output_data.summary)} />
                    </div>
                  )}
                </div>
              ) : stage.stage_type === 'TOOL_CALL' && stage.summarized_content ? (
                // TOOL_CALL 有摘要时：显示工具基本信息 + 摘要内容
                <div>
                  {/* 工具名称 */}
                  <div className="flex items-center space-x-2 mb-3">
                    <span className="text-xs font-semibold text-amber-600 uppercase tracking-wide">调用工具</span>
                    <code className="px-3 py-1 bg-amber-100 text-amber-800 rounded-lg text-sm font-bold font-mono">
                      {stage.tool_name || '未知'}
                    </code>
                  </div>
                  {/* 工具参数 */}
                  {stage.tool_arguments && (
                    <div className="mb-3">
                      <p className="text-xs text-slate-500 mb-1.5 font-medium">📥 参数</p>
                      <JsonViewer data={stage.tool_arguments} />
                    </div>
                  )}
                  {/* 摘要标签 + 内容 */}
                  <div className="flex items-center mb-3 px-3 py-2 bg-blue-50/80 rounded-lg">
                    <span className="mr-1.5">📝</span>
                    <span className="text-xs text-blue-600 font-medium">
                      执行结果已摘要 ({stage.original_tokens?.toLocaleString()} → {stage.summary_tokens?.toLocaleString()} tokens
                      · {stage.summary_type === 'llm' ? 'LLM 智能摘要' : '规则裁剪'})
                    </span>
                    <button
                      onClick={(e) => { e.stopPropagation(); setShowCompare(true); }}
                      className="ml-auto px-3 py-1 bg-violet-100 hover:bg-violet-200 text-violet-700 rounded-lg text-xs font-bold transition-colors"
                    >
                      🔍 查看对比
                    </button>
                  </div>
                  <MarkdownRenderer content={stage.summarized_content} />
                </div>
              ) : stage.stage_type === 'TOOL_CALL' ? (
                renderToolCall()
              ) : stage.summarized_content ? (
                <div>
                  <div className="flex items-center mb-3 px-3 py-2 bg-blue-50/80 rounded-lg">
                    <span className="mr-1.5">📝</span>
                    <span className="text-xs text-blue-600 font-medium">
                      已摘要 ({stage.original_tokens?.toLocaleString()} → {stage.summary_tokens?.toLocaleString()} tokens
                      · {stage.summary_type === 'llm' ? 'LLM 智能摘要' : '规则裁剪'})
                    </span>
                    <button
                      onClick={(e) => { e.stopPropagation(); setShowCompare(true); }}
                      className="ml-auto px-3 py-1 bg-violet-100 hover:bg-violet-200 text-violet-700 rounded-lg text-xs font-bold transition-colors"
                    >
                      🔍 查看对比
                    </button>
                  </div>
                  <MarkdownRenderer content={stage.summarized_content} />
                </div>
              ) : stage.stage_type === 'USER_QUERY' && displayContent.text.length > 150 ? (
                <div>
                  <div className={`relative ${!queryExpanded ? 'max-h-[4.5rem] overflow-hidden' : ''}`}>
                    <MarkdownRenderer content={displayContent.text} className="text-xs !text-xs" />
                    {!queryExpanded && (
                      <div className="absolute bottom-0 left-0 right-0 h-10 bg-gradient-to-t from-white to-transparent pointer-events-none"></div>
                    )}
                  </div>
                  <button
                    onClick={() => setQueryExpanded(!queryExpanded)}
                    className="mt-2 text-xs text-violet-500 hover:text-violet-600 flex items-center font-medium py-1 rounded-lg hover:bg-violet-50/50 transition-colors"
                  >
                    <span className={`mr-1 transition-transform duration-200 ${queryExpanded ? 'rotate-90' : ''}`}>▶</span>
                    <span>{queryExpanded ? '收起' : '展开完整内容'}</span>
                  </button>
                </div>
              ) : displayContent.useMarkdown ? (
                <MarkdownRenderer content={displayContent.text} className={stage.stage_type === 'USER_QUERY' ? 'text-xs !text-xs' : ''} />
              ) : (
                <div className="whitespace-pre-wrap break-words">{displayContent.text}</div>
              )}
            </div>

            {/* 错误信息 */}
            {stage.error_message && (
              <div className="mt-4 p-4 bg-red-50 border border-red-200 rounded-xl">
                <div className="flex items-center text-red-600 mb-2">
                  <span className="mr-2 text-lg">❌</span>
                  <span className="font-bold text-sm">错误信息</span>
                </div>
                <p className="text-sm text-red-700 break-words font-mono">{stage.error_message}</p>
              </div>
            )}

            {/* 审核按钮 */}
            {stage.status === 'waiting_approval' && (
              <div className="mt-5 flex items-center justify-end space-x-3 pt-4 border-t border-amber-100">
                <button onClick={onReject}
                  className="px-5 py-2.5 bg-red-50 hover:bg-red-100 text-red-600 rounded-xl transition-colors text-sm font-bold border border-red-200">
                  ❌ 拒绝
                </button>
                <button onClick={onApprove}
                  className="px-5 py-2.5 bg-gradient-to-r from-emerald-500 to-green-600 hover:from-emerald-600 hover:to-green-700 text-white rounded-xl transition-all text-sm font-bold shadow-lg shadow-emerald-200">
                  ✅ 通过执行
                </button>
              </div>
            )}

            {/* 展开/收起详情 - 仅对非 TOOL_CALL 类型显示（TOOL_CALL 已内联展示所有数据） */}
            {stage.stage_type !== 'TOOL_CALL' && (stage.input_data || stage.output_data || stage.tool_result) && (
              <>
                <button
                  onClick={() => setExpanded(!expanded)}
                  className="mt-4 text-xs text-blue-500 hover:text-blue-600 flex items-center font-medium py-2 rounded-lg hover:bg-blue-50/50 transition-colors"
                >
                  <span className={`mr-1.5 transition-transform duration-200 ${expanded ? 'rotate-90' : ''}`}>▶</span>
                  <span>{expanded ? '收起原始数据' : '查看原始数据'}</span>
                </button>

                {expanded && (
                  <div className="mt-3 space-y-3 min-w-0">
                    {stage.input_data && (
                      <CollapsibleSection title="输入数据" icon="📥" defaultOpen>
                        <div className="mt-2"><JsonViewer data={stage.input_data} /></div>
                      </CollapsibleSection>
                    )}
                    {stage.output_data && (
                      <CollapsibleSection title="输出数据" icon="📤" defaultOpen>
                        <div className="mt-2"><JsonViewer data={stage.output_data} /></div>
                      </CollapsibleSection>
                    )}
                    {stage.tool_result && (
                      <CollapsibleSection title="工具结果" icon="📋">
                        <div className="mt-2"><ToolResultViewer result={stage.tool_result} /></div>
                      </CollapsibleSection>
                    )}
                    {stage.retry_count > 0 && (
                      <div className="text-xs text-slate-500 px-1">
                        🔄 已重试 {stage.retry_count}/{stage.max_retries} 次
                      </div>
                    )}
                    {stage.approved_by && (
                      <div className="text-xs text-slate-500 px-1">
                        👤 由 {stage.approved_by} 于 {stage.approved_at ? formatTime(stage.approved_at) : '未知时间'} 审核
                      </div>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>

      {showCompare && stage.summarized_content && (
        <SummaryCompareModal stage={stage} onClose={() => setShowCompare(false)} />
      )}
    </div>
  );
}

// 工具结果查看器（智能判断是否为 JSON）
function ToolResultViewer({ result }: { result: string }) {
  const [expanded, setExpanded] = useState(false);
  const lines = result.split('\n');
  const isLong = lines.length > 8 || result.length > 600;

  // 尝试解析为 JSON
  let isJson = false;
  try {
    const trimmed = result.trim();
    if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
      JSON.parse(trimmed);
      isJson = true;
    }
  } catch {
    // 不是 JSON
  }

  if (isJson) {
    return <JsonViewer data={JSON.parse(result.trim())} />;
  }

  return (
    <div className="relative w-full min-w-0">
      <pre
        className={`text-xs bg-[#1e1e2e] text-[#a6e3a1] p-4 rounded-xl font-mono w-full min-w-0 overflow-x-auto overflow-y-auto whitespace-pre-wrap break-words leading-relaxed ${
          !expanded && isLong ? 'max-h-36' : 'max-h-96'
        }`}
      >
        {result}
      </pre>
      {isLong && (
        <button
          onClick={(e) => { e.stopPropagation(); setExpanded(!expanded); }}
          className="absolute bottom-2 right-2 px-3 py-1 bg-white/10 hover:bg-white/20 backdrop-blur text-xs text-white/80 rounded-lg transition-all"
        >
          {expanded ? '▲ 收起' : `▼ 展开 (${lines.length} 行)`}
        </button>
      )}
    </div>
  );
}


// ============== 主页面组件 ==============
export default function TaskDetail() {
  const { taskId } = useParams<{ taskId: string }>();
  const navigate = useNavigate();
  const [task, setTask] = useState<DiagnosisTaskSummary | null>(null);
  const [stages, setStages] = useState<DiagnosisStage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showConversation, setShowConversation] = useState(false);

  const loadTaskDetail = useCallback(async () => {
    if (!taskId) return;
    try {
      setLoading(true);
      setError(null);
      const response = await fetchTaskDetail(taskId);
      setTask(response.task);
      setStages(response.stages);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    loadTaskDetail();
    const interval = setInterval(() => {
      if (task?.status === 'running') {
        loadTaskDetail();
      }
    }, 3000);
    return () => clearInterval(interval);
  }, [loadTaskDetail, task?.status]);

  const handleApprove = async (stageId: number) => {
    try {
      await approveStage(stageId);
      loadTaskDetail();
    } catch (err) {
      alert(err instanceof Error ? err.message : '审核失败');
    }
  };

  const handleReject = async (stageId: number) => {
    const reason = prompt('请输入拒绝原因（可选）');
    try {
      await rejectStage(stageId, reason || undefined);
      loadTaskDetail();
    } catch (err) {
      alert(err instanceof Error ? err.message : '审核失败');
    }
  };

  const taskStatusConfig: Record<string, { label: string; color: string; bgColor: string; icon: string; gradient: string }> = {
    running: { label: '运行中', color: 'text-blue-700', bgColor: 'bg-blue-50', icon: '🔄', gradient: 'from-blue-500 to-cyan-500' },
    completed: { label: '已完成', color: 'text-emerald-700', bgColor: 'bg-emerald-50', icon: '✅', gradient: 'from-emerald-500 to-green-600' },
    failed: { label: '已失败', color: 'text-red-700', bgColor: 'bg-red-50', icon: '❌', gradient: 'from-red-500 to-rose-600' },
    cancelled: { label: '已取消', color: 'text-slate-600', bgColor: 'bg-slate-100', icon: '⏹️', gradient: 'from-slate-400 to-gray-500' },
  };

  const currentStatusConfig = task ? taskStatusConfig[task.status] || taskStatusConfig.running : taskStatusConfig.running;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50/30 to-indigo-50/20">
      {/* 头部 */}
      <header className="bg-white/80 backdrop-blur-xl border-b border-slate-200/80 sticky top-0 z-10">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center space-x-4">
              <button
                onClick={() => navigate('/')}
                className="w-9 h-9 flex items-center justify-center rounded-xl hover:bg-slate-100 transition-colors text-slate-400 hover:text-slate-600"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                </svg>
              </button>
              <div className="flex items-center space-x-3">
                <div className={`w-9 h-9 rounded-xl bg-gradient-to-br ${currentStatusConfig.gradient} flex items-center justify-center shadow-lg`}>
                  <span className="text-white text-sm">{currentStatusConfig.icon}</span>
                </div>
                <div>
                  <h1 className="text-base font-bold text-slate-800">诊断详情</h1>
                  <p className="text-xs text-slate-400 font-mono">{taskId?.slice(0, 12)}...</p>
                </div>
              </div>
            </div>
            <div className="flex items-center space-x-2.5">
              <button
                onClick={() => setShowConversation(true)}
                className="h-9 px-4 bg-gradient-to-r from-indigo-500 to-blue-600 hover:from-indigo-600 hover:to-blue-700 text-white rounded-xl transition-all flex items-center space-x-2 shadow-lg shadow-indigo-200/50 text-sm font-medium"
              >
                <span>📜</span><span>完整 Prompt</span>
              </button>
              <button
                onClick={loadTaskDetail}
                className="h-9 px-4 bg-white hover:bg-slate-50 text-slate-600 rounded-xl transition-all flex items-center space-x-2 border border-slate-200 text-sm font-medium"
              >
                <span>🔄</span><span>刷新</span>
              </button>
            </div>
          </div>
        </div>
      </header>

      {/* 主内容区 */}
      <main className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* 加载状态 */}
        {loading && !task && (
          <div className="flex flex-col items-center justify-center py-20">
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center shadow-lg mb-4 animate-pulse">
              <span className="text-white text-xl">🔍</span>
            </div>
            <div className="animate-spin rounded-full h-8 w-8 border-4 border-blue-500 border-t-transparent mb-3"></div>
            <span className="text-slate-500 text-sm">加载诊断详情...</span>
          </div>
        )}

        {/* 错误提示 */}
        {error && (
          <div className="bg-red-50 border border-red-200 rounded-2xl p-5 mb-6 flex items-start space-x-3">
            <span className="text-red-500 text-lg">❌</span>
            <div>
              <p className="text-red-700 font-medium">加载失败</p>
              <p className="text-red-600 text-sm mt-1">{error}</p>
            </div>
          </div>
        )}

        {/* 任务信息卡片 */}
        {task && (
          <div className="bg-white rounded-2xl shadow-lg shadow-slate-200/50 border border-slate-100 mb-8 overflow-hidden">
            {/* 顶部渐变条 */}
            <div className={`h-1.5 bg-gradient-to-r ${currentStatusConfig.gradient}`}></div>

            {/* 任务头部 */}
            <div className="p-6">
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className={`inline-flex items-center px-3 py-1.5 rounded-full ${currentStatusConfig.bgColor} mb-3`}>
                    <span className="mr-1.5 text-sm">{currentStatusConfig.icon}</span>
                    <span className={`text-xs font-bold ${currentStatusConfig.color}`}>{currentStatusConfig.label}</span>
                  </div>
                  {task.user_query.length <= 100 ? (
                    <h2 className="text-base font-bold text-slate-800 mb-3 leading-relaxed break-words">
                      {task.user_query}
                    </h2>
                  ) : (
                    <div className="mb-3">
                      <p className="text-sm text-slate-700 leading-relaxed break-words line-clamp-3">
                        {task.user_query}
                      </p>
                    </div>
                  )}
                  <div className="flex flex-wrap items-center gap-3 text-sm">
                    <div className="flex items-center px-2.5 py-1 bg-slate-50 rounded-lg">
                      <span className="mr-1.5 text-xs">🔑</span>
                      <code className="text-xs text-slate-500 font-mono">{task.task_id.slice(0, 12)}...</code>
                    </div>
                    <div className="flex items-center px-2.5 py-1 bg-slate-50 rounded-lg">
                      <span className="mr-1.5 text-xs">📡</span>
                      <span className="text-xs text-slate-500">{task.session_id.length > 20 ? task.session_id.slice(0, 20) + '...' : task.session_id}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* 进度统计 */}
            <div className="px-6 pb-6">
              <div className="grid grid-cols-4 gap-3">
                <div className="bg-gradient-to-br from-blue-50 to-indigo-50 rounded-xl p-4 text-center border border-blue-100/50">
                  <p className="text-2xl font-black text-blue-600">{stages.filter(s => s.stage_type !== 'TOOL_RESULT').length}</p>
                  <p className="text-xs text-blue-500 font-medium mt-0.5">总阶段数</p>
                </div>
                <div className="bg-gradient-to-br from-emerald-50 to-green-50 rounded-xl p-4 text-center border border-emerald-100/50">
                  <p className="text-2xl font-black text-emerald-600">{stages.filter(s => s.status === 'completed').length}</p>
                  <p className="text-xs text-emerald-500 font-medium mt-0.5">已完成</p>
                </div>
                <div className="bg-gradient-to-br from-amber-50 to-orange-50 rounded-xl p-4 text-center border border-amber-100/50">
                  <p className="text-2xl font-black text-amber-600">{stages.filter(s => s.status === 'waiting_approval').length}</p>
                  <p className="text-xs text-amber-500 font-medium mt-0.5">待审核</p>
                </div>
                <div className="bg-gradient-to-br from-slate-50 to-gray-50 rounded-xl p-4 text-center border border-slate-100">
                  <p className="text-2xl font-black text-slate-600">{task.current_stage_seq}</p>
                  <p className="text-xs text-slate-500 font-medium mt-0.5">当前阶段</p>
                </div>
              </div>
            </div>

            {/* 结论 */}
            {task.conclusion && (
              <div className="border-t border-slate-100 bg-gradient-to-br from-emerald-50/50 to-green-50/30 p-6">
                <div className="flex items-start">
                  <div className="w-10 h-10 bg-gradient-to-br from-emerald-500 to-green-600 rounded-xl flex items-center justify-center shadow-lg shadow-emerald-200/50 shrink-0 mr-4">
                    <span className="text-white text-lg">✨</span>
                  </div>
                  <div className="min-w-0 flex-1">
                    <h3 className="font-bold text-emerald-700 mb-2 text-sm">诊断结论</h3>
                    <MarkdownRenderer content={task.conclusion} />
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* 时间线 */}
        {stages.length > 0 && (() => {
          const visibleStages = stages.filter(s => s.stage_type !== 'TOOL_RESULT');
          return (
            <div>
              <div className="flex items-center mb-6">
                <div className="w-8 h-8 bg-gradient-to-br from-slate-700 to-slate-800 rounded-xl flex items-center justify-center shadow mr-3">
                  <span className="text-white text-sm">📊</span>
                </div>
                <h3 className="text-lg font-bold text-slate-800">诊断过程</h3>
                <span className="ml-3 px-2.5 py-1 bg-slate-100 text-slate-500 text-xs font-medium rounded-lg">
                  {visibleStages.length} 个阶段
                </span>
              </div>
              <div className="space-y-0">
                {visibleStages.map((stage, index) => (
                  <StageCard
                    key={stage.id}
                    stage={stage}
                    isLast={index === visibleStages.length - 1}
                    onApprove={stage.status === 'waiting_approval' ? () => handleApprove(stage.id) : undefined}
                    onReject={stage.status === 'waiting_approval' ? () => handleReject(stage.id) : undefined}
                  />
                ))}
              </div>
            </div>
          );
        })()}

        {/* 无阶段提示 */}
        {!loading && stages.length === 0 && (
          <div className="text-center py-20">
            <div className="w-16 h-16 bg-slate-100 rounded-2xl flex items-center justify-center mx-auto mb-4">
              <span className="text-3xl">📋</span>
            </div>
            <p className="text-slate-500">暂无诊断阶段</p>
          </div>
        )}
      </main>

      {/* 完整 Prompt 弹窗 */}
      {showConversation && taskId && (
        <ConversationModal taskId={taskId} onClose={() => setShowConversation(false)} />
      )}
    </div>
  );
}