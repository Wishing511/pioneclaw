/**
 * 聊天相关类型定义
 *
 * 借鉴 CountBot 的 ChatToolCall 设计，为工具调用添加独立 ID、status、progress 等字段
 */

export type ToolCallStatus = 'pending' | 'running' | 'success' | 'error' | 'cancelled'

export interface ChatToolCall {
  /** 工具调用唯一标识 */
  id: string
  /** 工具名称 */
  name: string
  /** 调用参数 */
  arguments?: Record<string, any>
  /** 执行结果 */
  result?: string | null
  /** 错误信息 */
  error?: string | null
  /** 执行状态 */
  status?: ToolCallStatus
  /** 执行耗时(ms) */
  duration?: number | null
  /** 进度百分比(0-100) */
  progress?: number | null
  /** 进度描述文字 */
  progressMessage?: string | null
  /** 关联的消息ID */
  messageId?: string | null
}

export interface ChatMessage {
  /** 消息唯一标识 */
  id: string
  /** 角色 */
  role: 'user' | 'assistant' | 'system'
  /** 消息内容 */
  content: string
  /** 推理/思考内容 */
  reasoningContent?: string
  /** 是否正在思考中 */
  isThinking?: boolean
  /** 工具调用列表 */
  toolCalls?: ChatToolCall[]
  /** 延迟(ms) */
  latency?: number
  /** 输入 token 数 */
  input_tokens?: number
  /** 输出 token 数 */
  output_tokens?: number
  /** 时间戳 */
  timestamp?: Date
  /** 是否正在流式接收 */
  isStreaming?: boolean
}

export interface ChatConversation {
  /** 会话唯一标识 */
  id: string
  /** 会话标题 */
  title: string
  /** 消息列表 */
  messages: ChatMessage[]
  /** 创建时间 */
  createdAt: Date
  /** 更新时间 */
  updatedAt?: Date
  /** WebSocket 会话 ID */
  sessionId?: string
  /** 切换会话时保留输入草稿 */
  draftText?: string
}

export interface ChatStreamRuntimeState {
  /** 消息列表 */
  messages: ChatMessage[]
  /** 是否正在流式接收 */
  isStreaming: boolean
  /** 当前正在流式填充的消息ID */
  currentStreamingMessageId: string | null
  /** 是否正在停止流式输出 */
  isStopping: boolean
}

export type StreamRealtimeMessage = Record<string, any> & {
  type: string
}

export type ChatStreamAction =
  | { type: 'replace_messages'; messages: ChatMessage[] }
  | { type: 'add_message'; message: ChatMessage }
  | { type: 'message_chunk'; payload: StreamRealtimeMessage; now: number }
  | { type: 'reasoning_chunk'; payload: StreamRealtimeMessage; now: number }
  | { type: 'tool_call'; payload: StreamRealtimeMessage; now: number }
  | { type: 'tool_progress'; payload: StreamRealtimeMessage }
  | { type: 'tool_result'; payload: StreamRealtimeMessage }
  | { type: 'tool_error'; payload: StreamRealtimeMessage }
  | { type: 'iteration_boundary'; now: number }
  | { type: 'message_complete'; payload?: StreamRealtimeMessage }
  | { type: 'stop_streaming' }
  | { type: 'clear_messages' }
