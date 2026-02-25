/**
 * 诊断任务与阶段的类型定义
 */

// 任务状态枚举
export type TaskStatus = 'running' | 'completed' | 'failed' | 'cancelled';

// 阶段类型枚举（含 CONTEXT_SUMMARY）
export type StageType = 
  | 'USER_QUERY'        // 用户提问
  | 'LLM_THINKING'      // LLM 推理
  | 'TOOL_CALL'         // 工具调用
  | 'TOOL_RESULT'       // 工具结果
  | 'LLM_CONCLUSION'    // LLM 结论
  | 'CONTEXT_SUMMARY';  // 上下文摘要事件

// 阶段状态枚举
export type StageStatus = 'pending' | 'waiting_approval' | 'completed' | 'failed';

// 审核状态枚举
export type ApprovalStatus = 'not_required' | 'pending' | 'approved' | 'rejected';

// 诊断阶段接口
export interface DiagnosisStage {
  id: number;
  task_id: string;
  stage_seq: number;
  stage_type: StageType;
  status: StageStatus;
  input_data: Record<string, unknown> | null;
  output_data: Record<string, unknown> | null;
  error_message: string | null;
  retry_count: number;
  max_retries: number;
  tool_name: string | null;
  tool_arguments: Record<string, unknown> | null;
  tool_result: string | null;
  approval_status: ApprovalStatus | null;
  approved_by: string | null;
  approved_at: string | null;
  summarized_content: string | null;
  summary_tokens: number | null;
  original_tokens: number | null;
  summary_type: string | null;
  created_at: string;
  updated_at: string;
}

// 诊断任务摘要接口（列表用）
export interface DiagnosisTaskSummary {
  task_id: string;
  session_id: string;
  user_query: string;
  status: TaskStatus;
  current_stage_seq: number;
  created_at: string;
  updated_at: string;
  conclusion: string | null;
}

// 诊断任务详情接口
export interface DiagnosisTask extends DiagnosisTaskSummary {
  stages: DiagnosisStage[];
}

// 任务列表响应
export interface TaskListResponse {
  total: number;
  tasks: DiagnosisTaskSummary[];
}

// 任务详情响应
export interface TaskDetailResponse {
  task: DiagnosisTaskSummary;
  stages: DiagnosisStage[];
}

// 任务进度响应
export interface TaskProgressResponse {
  task_id: string;
  status: TaskStatus;
  total_stages: number;
  completed_stages: number;
  current_stage_seq: number;
  current_stage_type: StageType | null;
  current_stage_status: StageStatus | null;
}

// ========== 系统状态相关类型 ==========

// 健康检查响应
export interface HealthResponse {
  status: string;
}

// 平台运行状态响应
export interface StatusResponse {
  status: string;
  sessions: {
    total: number;
    active: number;
  };
  pools: {
    scheduled: { running: number; max_concurrency: number };
    immediate: { running: number; max_concurrency: number };
  };
  locks: {
    total: number;
    held: number;
  };
  event_scheduler: {
    running: boolean;
  };
  database: {
    url: string;
  };
}

// 会话信息
export interface SessionInfo {
  session_id: string;
  client_info: Record<string, unknown>;
  connected_at: string;
  initialized: boolean;
}

// 会话列表响应
export interface SessionsResponse {
  total: number;
  sessions: SessionInfo[];
}

// 创建诊断任务请求
export interface CreateDiagnosisRequest {
  session_id: string;
  user_query: string;
  metadata?: Record<string, unknown>;
}

// 创建诊断任务响应
export interface CreateDiagnosisResponse {
  task_id: string;
  session_id: string;
  status: string;
  message: string;
}

// 待审核阶段列表响应
export interface PendingApprovalResponse {
  total: number;
  stages: DiagnosisStage[];
}
