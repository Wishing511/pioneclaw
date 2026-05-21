import type { StreamRealtimeMessage } from '@/types/chat'

interface UseChatRealtimeOptions {
  dispatchStreamEvent: (message: StreamRealtimeMessage) => void
  getCurrentSessionId: () => string | null
  cacheSessionMessages: (sessionId: string) => void
  onError?: (message: string) => void
}

/**
 * 高频事件类型集合 —— 这些事件会触发防抖缓存
 */
const highFrequencyCacheEventTypes = new Set([
  'content',
  'reasoning_chunk',
  'thinking',
  'tool_progress',
])

export function useChatRealtime(options: UseChatRealtimeOptions) {
  const cacheDebounceMs = 250
  let cacheTimer: number | null = null
  let pendingCacheSessionId: string | null = null
  let activeStreamSessionId: string | null = null

  const clearPendingCacheTimer = () => {
    if (cacheTimer !== null) {
      window.clearTimeout(cacheTimer)
      cacheTimer = null
    }
  }

  const flushCachedSession = () => {
    if (!pendingCacheSessionId) return
    options.cacheSessionMessages(pendingCacheSessionId)
    pendingCacheSessionId = null
  }

  const scheduleSessionCache = (sessionId: string) => {
    pendingCacheSessionId = sessionId
    if (cacheTimer !== null) return
    cacheTimer = window.setTimeout(() => {
      cacheTimer = null
      flushCachedSession()
    }, cacheDebounceMs)
  }

  const persistSessionCache = (sessionId: string, immediate = false) => {
    if (immediate) {
      pendingCacheSessionId = sessionId
      clearPendingCacheTimer()
      flushCachedSession()
      return
    }
    scheduleSessionCache(sessionId)
  }

  /**
   * 处理单个 SSE 事件消息
   */
  const handleStreamEvent = (data: StreamRealtimeMessage) => {
    // DEBUG: 记录所有收到的 SSE 事件
    // eslint-disable-next-line no-console
    console.log('[SSE] event type:', data.type, 'data:', JSON.stringify(data).slice(0, 200))

    // 跨会话保护：如果当前选中的会话已不是本流的目标会话，丢弃事件
    const currentSessionId = options.getCurrentSessionId()
    if (activeStreamSessionId && currentSessionId && activeStreamSessionId !== currentSessionId) {
      return
    }

    if (data.type === 'error') {
      // 错误事件也要停止 streaming，避免 UI 一直卡在 loading
      options.dispatchStreamEvent({ type: 'stop_streaming' })
      options.onError?.(data.message || '发生错误')
      // 立即 flush 缓存，保证持久化状态结束在非 streaming
      if (currentSessionId || activeStreamSessionId) {
        persistSessionCache((currentSessionId || activeStreamSessionId)!, true)
      }
      return
    }

    // 所有非错误事件都分发给 reducer
    options.dispatchStreamEvent(data)

    // 会话缓存（高频事件防抖）
    if (currentSessionId) {
      const isHighFreq = highFrequencyCacheEventTypes.has(data.type)
      persistSessionCache(currentSessionId, !isHighFreq)
    }
  }

  /**
   * 启动 SSE 流并逐事件处理
   */
  async function startStream(
    url: string,
    body: Record<string, any>,
    token: string,
    onComplete?: () => void
  ): Promise<void> {
    // 绑定本次流式请求的目标会话，防止切换会话后旧请求串流
    activeStreamSessionId = body.session_id || null
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(body),
    })

    if (!response.ok) {
      const err = await response.text()
      throw new Error(`请求失败 (${response.status}): ${err}`)
    }

    const reader = response.body?.getReader()
    if (!reader) {
      throw new Error('无法读取响应流')
    }

    const decoder = new TextDecoder()
    let sseBuffer = ''

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        sseBuffer += decoder.decode(value, { stream: true })
        const lines = sseBuffer.split('\n')
        sseBuffer = lines.pop() || ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const dataStr = line.slice(6)
          if (dataStr === '[DONE]') continue

          try {
            const data = JSON.parse(dataStr)
            handleStreamEvent(data)
          } catch {
            // 忽略 JSON 解析错误
          }
        }
      }
    } finally {
      reader.releaseLock()
      // 确保最终缓存被写入
      const sessionId = options.getCurrentSessionId()
      if (sessionId) {
        persistSessionCache(sessionId, true)
      }
      activeStreamSessionId = null
      onComplete?.()
    }
  }

  return {
    startStream,
    handleStreamEvent,
  }
}
