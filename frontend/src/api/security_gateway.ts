import axios from 'axios'

const sgApi = axios.create({
  baseURL: import.meta.env.VITE_SECURITY_GATEWAY_URL || '/sg-api',
  timeout: 10000,
})

// 自动附加 X-API-Key（如配置了安全网关管理密钥）
sgApi.interceptors.request.use((config) => {
  const key = import.meta.env.VITE_SG_ADMIN_API_KEY
  if (key) {
    config.headers['X-API-Key'] = key
  }
  return config
})

export interface FilterResult {
  action: string
  content?: string
  reason?: string
  risk_level: string
  matched_rules?: any[]
  model_result?: {
    category: string
    severity: number
    description: string
    source: string
  }
}

export interface WordItem {
  id: number
  word: string
  word_type: string
  category?: string
  severity: number
  description?: string
  is_active: boolean
  scope: string
  organization_id?: string
  creator_id?: number
  version: number
  created_at: string
  updated_at: string
}

export interface AuditLogItem {
  id: number
  check_point: string
  event_type: string
  risk_level: string
  user_id?: number
  username?: string
  session_id?: string
  agent_id?: string
  content_preview?: string
  action: string
  reason?: string
  matched_rules?: any
  request_trace_id?: string
  created_at: string
}

export interface AuditLogListResponse {
  items: AuditLogItem[]
  total: number
}

export const securityGatewayApi = {
  // 检测测试
  testFilter(text: string) {
    return sgApi.post<FilterResult>('/api/v1/filter/input', { text, context: {} })
  },

  // 词库列表
  listWords(params?: { word_type?: string; is_active?: boolean; skip?: number; limit?: number }) {
    return sgApi.get<{ items: WordItem[]; total: number }>('/api/v1/admin/words', { params })
  },

  // 创建词
  createWord(data: Partial<WordItem>) {
    return sgApi.post<WordItem>('/api/v1/admin/words', data)
  },

  // 更新词
  updateWord(id: number, data: Partial<WordItem>) {
    return sgApi.put<WordItem>(`/api/v1/admin/words/${id}`, data)
  },

  // 删除词
  deleteWord(id: number) {
    return sgApi.delete(`/api/v1/admin/words/${id}`)
  },

  // 批量导入
  batchImport(words: Partial<WordItem>[]) {
    return sgApi.post<WordItem[]>('/api/v1/admin/words/batch', words)
  },

  // 审计日志
  listAuditLogs(params?: {
    check_point?: string
    risk_level?: string
    user_id?: number
    skip?: number
    limit?: number
  }) {
    return sgApi.get<AuditLogListResponse>('/api/v1/admin/audit-logs', { params })
  },

  // 获取配置
  getConfig() {
    return sgApi.get('/api/v1/admin/config')
  },

  // 更新配置
  updateConfig(data: any) {
    return sgApi.put('/api/v1/admin/config', data)
  },

  // 测试 LLM 连接
  testLlmConnection(data: { url: string; model: string; api_key: string; timeout: number }) {
    return sgApi.post('/api/v1/admin/config/test-llm', data)
  },

  // 看板统计
  getDashboardStats(days?: number) {
    return sgApi.get('/api/v1/admin/dashboard/stats', { params: { days } })
  },
}
