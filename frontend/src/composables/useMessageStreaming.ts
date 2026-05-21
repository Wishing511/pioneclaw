import { computed, ref } from 'vue'
import type { ChatMessage, ChatStreamAction, ChatStreamRuntimeState } from '@/types/chat'
import {
  chatStreamReducer,
  createInitialChatStreamState,
  mapRealtimeMessageToAction,
  type StreamRealtimeMessage,
} from '@/modules/chat/runtime/chatStreamReducer'

const SESSION_MESSAGES_CACHE_KEY = (sessionId: string) =>
  `pioneclaw_session_messages_${sessionId}`
const CACHE_MAX_AGE_MS = 5 * 60 * 1000 // 5 分钟

export function useMessageStreaming() {
  const runtime = ref<ChatStreamRuntimeState>(createInitialChatStreamState())

  const messages = computed({
    get: () => runtime.value.messages,
    set: (value: ChatMessage[]) => {
      runtime.value = {
        ...runtime.value,
        messages: value,
      }
    },
  })

  const isStreaming = computed({
    get: () => runtime.value.isStreaming,
    set: (value: boolean) => {
      runtime.value = {
        ...runtime.value,
        isStreaming: value,
      }
    },
  })

  const currentStreamingMessageId = computed(() => runtime.value.currentStreamingMessageId)

  function applyAction(action: ChatStreamAction) {
    runtime.value = chatStreamReducer(runtime.value, action)
  }

  function dispatchStreamEvent(message: StreamRealtimeMessage) {
    const action = mapRealtimeMessageToAction(message)
    if (!action) return
    applyAction(action)
  }

  function replaceMessages(nextMessages: ChatMessage[]) {
    applyAction({ type: 'replace_messages', messages: nextMessages })
  }

  function clearMessages() {
    applyAction({ type: 'clear_messages' })
  }

  function stopStreaming() {
    applyAction({ type: 'stop_streaming' })
  }

  function addMessage(message: ChatMessage) {
    applyAction({ type: 'add_message', message })
  }

  /* ─── 会话缓存 ─── */
  function cacheSessionMessages(sessionId: string) {
    try {
      const data = {
        messages: runtime.value.messages,
        timestamp: Date.now(),
        isStreaming: runtime.value.isStreaming,
        currentStreamingMessageId: runtime.value.currentStreamingMessageId,
      }
      sessionStorage.setItem(SESSION_MESSAGES_CACHE_KEY(sessionId), JSON.stringify(data))
    } catch {
      /* ignore storage failures */
    }
  }

  function readSessionMessages(sessionId: string): { messages: ChatMessage[]; timestamp: number; isStreaming: boolean } | null {
    try {
      const raw = sessionStorage.getItem(SESSION_MESSAGES_CACHE_KEY(sessionId))
      if (!raw) return null
      const data = JSON.parse(raw)
      if (!data.timestamp || Date.now() - data.timestamp > CACHE_MAX_AGE_MS) {
        sessionStorage.removeItem(SESSION_MESSAGES_CACHE_KEY(sessionId))
        return null
      }
      const messages = (data.messages || []).map((m: any) => ({
        ...m,
        timestamp: m.timestamp ? new Date(m.timestamp) : new Date(),
      }))
      return {
        messages,
        timestamp: data.timestamp,
        isStreaming: data.isStreaming || false,
      }
    } catch {
      return null
    }
  }

  function restoreSessionMessages(sessionId: string): ChatMessage[] | null {
    const data = readSessionMessages(sessionId)
    if (!data) return null
    runtime.value = {
      ...createInitialChatStreamState(),
      messages: data.messages,
      isStreaming: false, // 恢复时不再流式
      currentStreamingMessageId: null,
    }
    return data.messages
  }

  return {
    runtime,
    messages,
    isStreaming,
    currentStreamingMessageId,
    applyAction,
    dispatchStreamEvent,
    replaceMessages,
    clearMessages,
    stopStreaming,
    addMessage,
    cacheSessionMessages,
    readSessionMessages,
    restoreSessionMessages,
  }
}
