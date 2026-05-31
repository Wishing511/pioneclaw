import { api } from './index'

// ==================== Types ====================

export interface AutoDreamConfig {
  id: number
  enabled: boolean
  cron_expression: string
  batch_size: number
  max_consolidated_per_run: number
  archive_after_days: number
  delete_after_days: number | null
  enable_dedup: boolean
  enable_consolidation: boolean
  enable_archival: boolean
  created_at: string
  updated_at: string
}

export interface AutoDreamConfigUpdate {
  enabled?: boolean
  cron_expression?: string
  batch_size?: number
  max_consolidated_per_run?: number
  archive_after_days?: number
  delete_after_days?: number | null
  enable_dedup?: boolean
  enable_consolidation?: boolean
  enable_archival?: boolean
}

export interface AutoDreamLog {
  id: number
  triggered_at: string
  triggered_by: string
  status: string
  total_memories: number
  duplicates_found: number
  merged: number
  consolidated: number
  archived: number
  deleted: number
  llm_calls: number
  llm_tokens_in: number
  llm_tokens_out: number
  duration_seconds: number
  error_message: string | null
  details: string | null
}

export interface AutoDreamLogList {
  items: AutoDreamLog[]
  total: number
}

export interface AutoDreamStatus {
  is_running: boolean
  last_run: AutoDreamLog | null
  config: AutoDreamConfig
}

export interface AutoDreamTriggerResponse {
  log_id: number
  status: string
  message: string
}

// ==================== API ====================

export async function getAutoDreamStatus(): Promise<AutoDreamStatus> {
  const res = await api.get('/autodream/status')
  return res.data
}

export async function getAutoDreamConfig(): Promise<AutoDreamConfig> {
  const res = await api.get('/autodream/config')
  return res.data
}

export async function updateAutoDreamConfig(data: AutoDreamConfigUpdate): Promise<AutoDreamConfig> {
  const res = await api.put('/autodream/config', data)
  return res.data
}

export async function triggerAutoDream(): Promise<AutoDreamTriggerResponse> {
  const res = await api.post('/autodream/trigger')
  return res.data
}

export async function getAutoDreamLogs(page = 1, pageSize = 20): Promise<AutoDreamLogList> {
  const res = await api.get('/autodream/logs', {
    params: { page, page_size: pageSize },
  })
  return res.data
}

export async function getAutoDreamLogDetail(id: number): Promise<AutoDreamLog> {
  const res = await api.get(`/autodream/logs/${id}`)
  return res.data
}
