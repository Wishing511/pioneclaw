import type {
  ChatMessage,
  ChatStreamAction,
  ChatStreamRuntimeState,
  ChatToolCall,
  StreamRealtimeMessage,
  ToolCallStatus,
} from '@/types/chat'

export function createInitialChatStreamState(): ChatStreamRuntimeState {
  return {
    messages: [],
    isStreaming: false,
    currentStreamingMessageId: null,
    isStopping: false,
  }
}

export type { StreamRealtimeMessage }

export function mapRealtimeMessageToAction(
  message: StreamRealtimeMessage,
  now: number = Date.now()
): ChatStreamAction | null {
  // DEBUG: 记录消息映射
  // eslint-disable-next-line no-console
  console.log('[REDUCER] mapRealtimeMessageToAction type:', message.type)
  switch (message.type) {
    case 'reasoning_chunk':
    case 'thinking':
      return { type: 'reasoning_chunk', payload: message, now }
    case 'content':
      return { type: 'message_chunk', payload: message, now }
    case 'tool_call':
    case 'tool_start':
      return { type: 'tool_call', payload: message, now }
    case 'tool_progress':
      return { type: 'tool_progress', payload: message }
    case 'tool_result':
      return { type: 'tool_result', payload: message }
    case 'tool_error':
      return { type: 'tool_error', payload: message }
    case 'new_iteration':
      return { type: 'iteration_boundary', now }
    case 'done':
      return { type: 'message_complete', payload: message }
    case 'message_complete':
      return { type: 'message_complete' }
    case 'error':
    case 'stop_streaming':
      return { type: 'stop_streaming' }
    default:
      return null
  }
}

export function chatStreamReducer(
  state: ChatStreamRuntimeState,
  action: ChatStreamAction
): ChatStreamRuntimeState {
  // DEBUG: 记录 reducer 调用
  // eslint-disable-next-line no-console
  console.log('[REDUCER] action:', action.type, 'msgs:', state.messages.length, 'currentId:', state.currentStreamingMessageId)
  switch (action.type) {
    case 'replace_messages':
      return {
        ...createInitialChatStreamState(),
        messages: action.messages,
      }
    case 'add_message':
      return {
        ...state,
        messages: [...state.messages, action.message],
      }
    case 'message_chunk':
      return reduceMessageChunk(state, action.payload, action.now)
    case 'reasoning_chunk':
      return reduceReasoningChunk(state, action.payload, action.now)
    case 'tool_call':
      return reduceToolCall(state, action.payload, action.now)
    case 'tool_progress':
      return reduceToolProgress(state, action.payload)
    case 'tool_result':
      return reduceToolResult(state, action.payload)
    case 'tool_error':
      return reduceToolError(state, action.payload)
    case 'iteration_boundary':
      return reduceIterationBoundary(state, action.now)
    case 'message_complete':
      return reduceMessageComplete(state, action.payload)
    case 'stop_streaming':
      return reduceStopStreaming(state)
    case 'clear_messages':
      return createInitialChatStreamState()
    default:
      return state
  }
}

/* ─── message chunk ─── */
function reduceMessageChunk(
  state: ChatStreamRuntimeState,
  message: StreamRealtimeMessage,
  now: number
): ChatStreamRuntimeState {
  if (state.isStopping) return state

  const content = message.content || ''
  const currentIndex = findMessageIndex(state.messages, state.currentStreamingMessageId)

  if (currentIndex === -1) {
    const newMessage: ChatMessage = {
      id: `msg-${now}-${Math.random().toString(36).slice(2, 7)}`,
      role: 'assistant',
      content,
      timestamp: new Date(),
      toolCalls: [],
      isStreaming: true,
    }
    return {
      ...state,
      messages: [...state.messages, newMessage],
      isStreaming: true,
      currentStreamingMessageId: newMessage.id,
    }
  }

  const currentMessage = state.messages[currentIndex]
  const updatedMessage: ChatMessage = currentMessage.isThinking
    ? { ...currentMessage, isThinking: false, content }
    : { ...currentMessage, content: `${currentMessage.content || ''}${content}` }

  return {
    ...state,
    messages: replaceMessage(state.messages, currentIndex, updatedMessage),
    isStreaming: true,
    currentStreamingMessageId: updatedMessage.id,
  }
}

/* ─── reasoning chunk ─── */
function reduceReasoningChunk(
  state: ChatStreamRuntimeState,
  message: StreamRealtimeMessage,
  now: number
): ChatStreamRuntimeState {
  if (state.isStopping) return state

  const content = message.content || ''
  const currentIndex = findMessageIndex(state.messages, state.currentStreamingMessageId)

  if (currentIndex === -1) {
    const newMessage: ChatMessage = {
      id: `msg-${now}-${Math.random().toString(36).slice(2, 7)}`,
      role: 'assistant',
      content: '',
      reasoningContent: content,
      timestamp: new Date(),
      toolCalls: [],
      isThinking: true,
      isStreaming: true,
    }
    return {
      ...state,
      messages: [...state.messages, newMessage],
      isStreaming: true,
      currentStreamingMessageId: newMessage.id,
    }
  }

  const currentMessage = state.messages[currentIndex]
  const updatedMessage: ChatMessage = {
    ...currentMessage,
    reasoningContent: `${currentMessage.reasoningContent || ''}${content}`,
    isThinking: !Boolean(currentMessage.content),
  }

  return {
    ...state,
    messages: replaceMessage(state.messages, currentIndex, updatedMessage),
    isStreaming: true,
    currentStreamingMessageId: updatedMessage.id,
  }
}

/* ─── tool call ─── */
function reduceToolCall(
  state: ChatStreamRuntimeState,
  message: StreamRealtimeMessage,
  now: number
): ChatStreamRuntimeState {
  const toolName = message.tool || message.tool_name || message.name
  // DEBUG
  // eslint-disable-next-line no-console
  console.log('[REDUCER] reduceToolCall toolName:', toolName, 'msg:', JSON.stringify(message).slice(0, 150))
  if (!toolName) return state

  const incomingId = message.tool_call_id
  const toolCallId = incomingId || `tc-${now}-${toolName}-${Math.random().toString(36).slice(2, 5)}`
  const initialToolCall: ChatToolCall = {
    id: toolCallId,
    name: toolName,
    arguments: message.arguments || {},
    status: 'running',
    progress: 0,
    progressMessage: null,
    messageId: message.messageId || null,
  }

  const currentIndex = findMessageIndex(state.messages, state.currentStreamingMessageId)
  const targetIndex = currentIndex === -1 ? state.messages.length : currentIndex
  const targetMessage: ChatMessage =
    currentIndex === -1
      ? {
          id: `msg-${now}-${Math.random().toString(36).slice(2, 7)}`,
          role: 'assistant',
          content: '',
          timestamp: new Date(),
          toolCalls: [],
          isStreaming: true,
        }
      : state.messages[currentIndex]

  const toolCalls = [...(targetMessage.toolCalls || [])]
  const exists = toolCalls.some((tc) => {
    if (tc.status !== 'running') return false
    // 有唯一ID时精确匹配
    if (incomingId && tc.id === incomingId) return true
    // 无ID时，同名+同参数才认为是重复（避免并行同类型工具被误判）
    if (tc.name !== toolName) return false
    const existingArgs = JSON.stringify(tc.arguments || {})
    const newArgs = JSON.stringify(message.arguments || {})
    return existingArgs === newArgs
  })
  if (exists) {
    return { ...state, isStreaming: true }
  }

  const nextMessage: ChatMessage = {
    ...targetMessage,
    isThinking: false,
    toolCalls: [...toolCalls, initialToolCall],
  }

  const nextMessages =
    currentIndex === -1
      ? [...state.messages, nextMessage]
      : replaceMessage(state.messages, targetIndex, nextMessage)

  return {
    ...state,
    messages: nextMessages,
    isStreaming: true,
    currentStreamingMessageId: nextMessage.id,
  }
}

/* ─── tool progress ─── */
function reduceToolProgress(
  state: ChatStreamRuntimeState,
  message: StreamRealtimeMessage
): ChatStreamRuntimeState {
  const toolName = message.tool || message.tool_name || message.name
  if (!toolName) return state

  const match = findLatestRunningToolCall(state.messages, toolName, state.currentStreamingMessageId, message.tool_call_id)
  if (!match) return state

  const targetMessage = state.messages[match.messageIndex]
  const toolCalls = [...(targetMessage.toolCalls || [])]
  const toolCall = toolCalls[match.toolCallIndex]

  toolCalls[match.toolCallIndex] = {
    ...toolCall,
    progress:
      message.progress === undefined || message.progress === null
        ? toolCall.progress ?? null
        : Number(message.progress),
    progressMessage:
      message.message === undefined
        ? toolCall.progressMessage ?? null
        : message.message,
  }

  return {
    ...state,
    messages: replaceMessage(state.messages, match.messageIndex, {
      ...targetMessage,
      toolCalls,
    }),
  }
}

/* ─── tool result ─── */
function reduceToolResult(
  state: ChatStreamRuntimeState,
  message: StreamRealtimeMessage
): ChatStreamRuntimeState {
  const toolName = message.tool || message.tool_name || message.name
  // DEBUG
  // eslint-disable-next-line no-console
  console.log('[REDUCER] reduceToolResult toolName:', toolName, 'msg:', JSON.stringify(message).slice(0, 150))
  if (!toolName) return state

  const match = findLatestRunningToolCall(state.messages, toolName, state.currentStreamingMessageId, message.tool_call_id)
  if (!match) return state

  const targetMessage = state.messages[match.messageIndex]
  const toolCalls = [...(targetMessage.toolCalls || [])]
  const toolCall = toolCalls[match.toolCallIndex]

  toolCalls[match.toolCallIndex] = {
    ...toolCall,
    status: 'success',
    result: message.result,
    duration: message.duration_ms ?? message.duration ?? null,
    progress: 100,
    progressMessage: null,
  }

  return {
    ...state,
    messages: replaceMessage(state.messages, match.messageIndex, {
      ...targetMessage,
      toolCalls,
    }),
  }
}

/* ─── tool error ─── */
function reduceToolError(
  state: ChatStreamRuntimeState,
  message: StreamRealtimeMessage
): ChatStreamRuntimeState {
  const toolName = message.tool || message.tool_name || message.name
  if (!toolName) return state

  const match = findLatestRunningToolCall(state.messages, toolName, state.currentStreamingMessageId, message.tool_call_id)
  if (!match) return state

  const targetMessage = state.messages[match.messageIndex]
  const toolCalls = [...(targetMessage.toolCalls || [])]
  const toolCall = toolCalls[match.toolCallIndex]

  toolCalls[match.toolCallIndex] = {
    ...toolCall,
    status: 'error',
    error: message.error,
    duration: message.duration_ms ?? message.duration ?? null,
    progress: 100,
    progressMessage: null,
  }

  return {
    ...state,
    messages: replaceMessage(state.messages, match.messageIndex, {
      ...targetMessage,
      toolCalls,
    }),
  }
}

/* ─── iteration boundary ─── */
function reduceIterationBoundary(
  state: ChatStreamRuntimeState,
  now: number
): ChatStreamRuntimeState {
  const currentIndex = findMessageIndex(state.messages, state.currentStreamingMessageId)
  if (currentIndex === -1) return state

  // 把当前消息标记为非流式（finalize），避免已展示内容被清空
  const currentMessage = state.messages[currentIndex]
  const finalizedMessage: ChatMessage = {
    ...currentMessage,
    isStreaming: false,
    isThinking: false,
  }

  // 创建新的 assistant 消息作为新一轮流式目标
  const newMessage: ChatMessage = {
    id: `msg-${now}-${Math.random().toString(36).slice(2, 7)}`,
    role: 'assistant',
    content: '',
    timestamp: new Date(),
    toolCalls: [],
    isStreaming: true,
  }

  const nextMessages = [...state.messages]
  nextMessages[currentIndex] = finalizedMessage
  nextMessages.push(newMessage)

  return {
    ...state,
    messages: nextMessages,
    currentStreamingMessageId: newMessage.id,
  }
}

/* ─── message complete ─── */
function reduceMessageComplete(
  state: ChatStreamRuntimeState,
  payload?: StreamRealtimeMessage
): ChatStreamRuntimeState {
  const messageIndex = findMessageIndex(state.messages, state.currentStreamingMessageId)
  let nextMessages = state.messages

  if (messageIndex !== -1) {
    const currentMessage = state.messages[messageIndex]
    const updates: Partial<ChatMessage> = { isStreaming: false }

    if (currentMessage.isThinking) {
      updates.isThinking = false
      updates.content = currentMessage.content || ''
    }

    if (payload) {
      if (payload.response && !currentMessage.content) {
        updates.content = String(payload.response)
          .replace(/^\n{2,}/, '\n')
          .replace(/^\s{2,}/, '')
      }
      if (payload.thinking_content && !currentMessage.reasoningContent) {
        updates.reasoningContent = payload.thinking_content
      }
      if (payload.latency_ms != null) updates.latency = payload.latency_ms
      if (payload.input_tokens != null) updates.input_tokens = payload.input_tokens
      if (payload.output_tokens != null) updates.output_tokens = payload.output_tokens
    }

    nextMessages = replaceMessage(state.messages, messageIndex, {
      ...currentMessage,
      ...updates,
    })
  }

  return {
    ...state,
    messages: nextMessages,
    currentStreamingMessageId: null,
    isStreaming: false,
    isStopping: false,
  }
}

/* ─── stop streaming ─── */
function reduceStopStreaming(state: ChatStreamRuntimeState): ChatStreamRuntimeState {
  const messageIndex = findMessageIndex(state.messages, state.currentStreamingMessageId)
  let nextMessages = state.messages

  if (messageIndex !== -1) {
    const currentMessage = state.messages[messageIndex]
    const toolCalls = (currentMessage.toolCalls || []).map((tc) =>
      tc.status === 'running' ? { ...tc, status: 'cancelled' as ToolCallStatus } : tc
    )
    nextMessages = replaceMessage(state.messages, messageIndex, {
      ...currentMessage,
      toolCalls,
      isStreaming: false,
    })
  }

  return {
    ...state,
    messages: nextMessages,
    currentStreamingMessageId: null,
    isStreaming: false,
    isStopping: false,
  }
}

/* ─── helpers ─── */
function findMessageIndex(messages: ChatMessage[], messageId: string | null): number {
  if (!messageId) return -1
  return messages.findIndex((m) => m.id === messageId)
}

function findLatestRunningToolCall(
  messages: ChatMessage[],
  toolName: string,
  preferredMessageId: string | null,
  toolCallId?: string | null
): { messageIndex: number; toolCallIndex: number } | null {
  const preferredMessageIndex = findMessageIndex(messages, preferredMessageId)

  if (preferredMessageIndex !== -1) {
    const toolCallIndex = findRunningToolCallIndex(messages[preferredMessageIndex], toolName, toolCallId)
    if (toolCallIndex !== -1) {
      return { messageIndex: preferredMessageIndex, toolCallIndex }
    }
  }

  for (let messageIndex = messages.length - 1; messageIndex >= 0; messageIndex -= 1) {
    if (messageIndex === preferredMessageIndex) continue
    const toolCallIndex = findRunningToolCallIndex(messages[messageIndex], toolName, toolCallId)
    if (toolCallIndex !== -1) {
      return { messageIndex, toolCallIndex }
    }
  }

  return null
}

function findRunningToolCallIndex(message: ChatMessage, toolName: string, toolCallId?: string | null): number {
  const toolCalls = message.toolCalls || []
  for (let index = toolCalls.length - 1; index >= 0; index -= 1) {
    if (toolCalls[index].status !== 'running') continue
    if (toolCallId) {
      if (toolCalls[index].id === toolCallId) return index
    } else {
      if (toolCalls[index].name === toolName) return index
    }
  }
  return -1
}

function replaceMessage(
  messages: ChatMessage[],
  index: number,
  nextMessage: ChatMessage
): ChatMessage[] {
  const nextMessages = [...messages]
  nextMessages[index] = nextMessage
  return nextMessages
}
