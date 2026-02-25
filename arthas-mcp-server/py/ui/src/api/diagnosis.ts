/**
 * API 服务层
 * 封装所有与后端通信的接口
 */

import type {
  TaskListResponse,
  TaskDetailResponse,
  TaskProgressResponse,
  HealthResponse,
  StatusResponse,
  SessionsResponse,
  CreateDiagnosisRequest,
  CreateDiagnosisResponse,
  PendingApprovalResponse,
} from '../types';

const API_BASE = '/api';

// ========== 系统接口 ==========

/**
 * 健康检查
 */
export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE}/health`);
  if (!response.ok) {
    throw new Error(`健康检查失败: ${response.statusText}`);
  }
  return response.json();
}

/**
 * 获取平台运行状态
 */
export async function fetchStatus(): Promise<StatusResponse> {
  const response = await fetch(`${API_BASE}/status`);
  if (!response.ok) {
    throw new Error(`获取平台状态失败: ${response.statusText}`);
  }
  return response.json();
}

/**
 * 获取活跃会话列表
 */
export async function fetchSessions(): Promise<SessionsResponse> {
  const response = await fetch(`${API_BASE}/sessions`);
  if (!response.ok) {
    throw new Error(`获取会话列表失败: ${response.statusText}`);
  }
  return response.json();
}

// ========== 诊断任务接口 ==========

/**
 * 创建诊断任务
 */
export async function createDiagnosis(req: CreateDiagnosisRequest): Promise<CreateDiagnosisResponse> {
  const response = await fetch(`${API_BASE}/diagnosis`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  
  if (!response.ok) {
    if (response.status === 404) {
      const data = await response.json();
      throw new Error(data.detail || '会话不存在或未初始化');
    }
    throw new Error(`创建诊断任务失败: ${response.statusText}`);
  }
  return response.json();
}

/**
 * 获取诊断任务列表
 */
export async function fetchTaskList(params?: {
  session_id?: string;
  status?: string;
  start_time?: string;
  end_time?: string;
  limit?: number;
  offset?: number;
}): Promise<TaskListResponse> {
  const searchParams = new URLSearchParams();
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null) {
        searchParams.append(key, String(value));
      }
    });
  }
  
  const url = `${API_BASE}/diagnosis${searchParams.toString() ? '?' + searchParams.toString() : ''}`;
  const response = await fetch(url);
  
  if (!response.ok) {
    throw new Error(`获取任务列表失败: ${response.statusText}`);
  }
  
  return response.json();
}

/**
 * 获取诊断任务详情
 */
export async function fetchTaskDetail(taskId: string): Promise<TaskDetailResponse> {
  const response = await fetch(`${API_BASE}/diagnosis/${taskId}`);
  
  if (!response.ok) {
    if (response.status === 404) {
      throw new Error(`任务不存在: ${taskId}`);
    }
    throw new Error(`获取任务详情失败: ${response.statusText}`);
  }
  
  return response.json();
}

/**
 * 删除诊断任务
 */
export async function deleteTask(taskId: string): Promise<{ message: string; task_id: string }> {
  const response = await fetch(`${API_BASE}/diagnosis/${taskId}`, {
    method: 'DELETE',
  });
  
  if (!response.ok) {
    if (response.status === 404) {
      throw new Error(`任务不存在: ${taskId}`);
    }
    if (response.status === 409) {
      throw new Error(`任务正在执行中，无法删除`);
    }
    throw new Error(`删除任务失败: ${response.statusText}`);
  }
  
  return response.json();
}

/**
 * 获取任务进度
 */
export async function fetchTaskProgress(taskId: string): Promise<TaskProgressResponse> {
  const response = await fetch(`${API_BASE}/diagnosis/${taskId}/progress`);
  
  if (!response.ok) {
    throw new Error(`获取任务进度失败: ${response.statusText}`);
  }
  
  return response.json();
}

/**
 * 获取完整对话/Prompt 过程
 */
export interface ConversationResponse {
  task_id: string;
  status: string;
  session_id: string;
  user_query: string;
  conversation_text: string;
}

export async function fetchConversation(taskId: string): Promise<ConversationResponse> {
  const response = await fetch(`${API_BASE}/diagnosis/${taskId}/conversation`);
  
  if (!response.ok) {
    if (response.status === 404) {
      throw new Error(`任务不存在: ${taskId}`);
    }
    throw new Error(`获取对话过程失败: ${response.statusText}`);
  }
  
  return response.json();
}

// ========== 审核管理接口 ==========

/**
 * 获取待审核阶段列表
 */
export async function fetchPendingApproval(): Promise<PendingApprovalResponse> {
  const response = await fetch(`${API_BASE}/stages/pending-approval`);
  
  if (!response.ok) {
    throw new Error(`获取待审核列表失败: ${response.statusText}`);
  }
  
  return response.json();
}

/**
 * 审核通过
 */
export async function approveStage(stageId: number, approvedBy?: string): Promise<void> {
  const response = await fetch(`${API_BASE}/stages/${stageId}/approve`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ approved_by: approvedBy }),
  });
  
  if (!response.ok) {
    throw new Error(`审核通过失败: ${response.statusText}`);
  }
}

/**
 * 审核拒绝
 */
export async function rejectStage(stageId: number, reason?: string): Promise<void> {
  const response = await fetch(`${API_BASE}/stages/${stageId}/reject`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ reason }),
  });
  
  if (!response.ok) {
    throw new Error(`审核拒绝失败: ${response.statusText}`);
  }
}

