<template>
  <div class="chat-page">
    <!-- 左侧会话列表（微信风格） -->
    <div class="chat-sidebar" :class="{ collapsed: sidebarCollapsed }">
      <!-- 新对话按钮 -->
      <div class="sidebar-actions" :class="{ collapsed: sidebarCollapsed }">
        <el-button type="primary" @click="newConversation" :circle="sidebarCollapsed">
          <el-icon><Plus /></el-icon>
          <span v-if="!sidebarCollapsed">{{ $t('chat.newChat') }}</span>
        </el-button>
        <!-- 折叠按钮 -->
        <el-button class="sidebar-toggle" @click="sidebarCollapsed = !sidebarCollapsed" :circle="true" size="small">
          <el-icon v-if="sidebarCollapsed"><Expand /></el-icon>
          <el-icon v-else><Fold /></el-icon>
        </el-button>
      </div>

      <!-- 会话列表 -->
      <div class="conversation-list">
        <div
          v-for="conv in filteredConversations"
          :key="conv.id"
          class="conversation-item"
          :class="{ active: currentConversation?.id === conv.id, collapsed: sidebarCollapsed }"
          @click="selectConversation(conv)"
          :title="sidebarCollapsed ? conv.title : ''"
        >
          <div class="conv-avatar">
            <el-icon><ChatLineRound /></el-icon>
          </div>
          <div class="conv-info" v-show="!sidebarCollapsed">
            <div class="conv-title-row">
              <span class="conv-title">{{ conv.title }}</span>
              <span class="conv-time">{{ formatTime(conv.updatedAt || conv.createdAt) }}</span>
            </div>
            <div class="conv-preview">{{ getLastMessage(conv) }}</div>
          </div>
        </div>

        <el-empty v-if="filteredConversations.length === 0 && !sidebarCollapsed" :description="$t('chat.noConversations')" size="small" />
      </div>
    </div>

    <!-- 右侧对话区域（微信风格） -->
    <div class="chat-main">
      <!-- 顶部工具栏 -->
      <div class="chat-header">
        <div class="header-left">
          <span v-if="currentConversation" class="chat-title">{{ currentConversation.title }}</span>
        </div>
        <div class="header-right">
          <el-select v-model="selectedModelId" :placeholder="$t('chat.selectModel')" style="width: 180px" size="small">
            <el-option-group v-for="group in tieredModels" :key="group.tier" :label="group.label">
              <el-option
                v-for="model in group.models"
                :key="model.id"
                :label="model.display_name"
                :value="model.id"
              />
            </el-option-group>
          </el-select>
          <template v-if="currentConversation">
            <el-button
              size="small"
              :title="'压缩上下文'"
              @click="compactContext()"
            >
              <el-icon><Collection /></el-icon>
              <span class="compact-btn-text">压缩</span>
            </el-button>
            <el-dropdown trigger="click">
              <el-button size="small" circle>
                <el-icon><MoreFilled /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item @click="clearCurrentConversation">{{ $t('chat.clearChat') }}</el-dropdown-item>
                  <el-dropdown-item @click="exportConversation">{{ $t('chat.exportChat') }}</el-dropdown-item>
                  <el-dropdown-item divided @click="deleteCurrentConversation">{{ $t('chat.deleteChat') }}</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
        </div>
      </div>

      <!-- 消息区域（微信风格） -->
      <div class="chat-messages" ref="messagesContainer">
        <div v-if="!currentConversation || streamMessages.length === 0" class="empty-chat">
          <div class="empty-icon">
            <svg viewBox="0 0 64 64" fill="none">
              <rect x="8" y="12" width="48" height="40" rx="4" stroke="currentColor" stroke-width="2"/>
              <path d="M8 20h48" stroke="currentColor" stroke-width="2"/>
              <circle cx="16" cy="16" r="2" fill="currentColor"/>
              <circle cx="24" cy="16" r="2" fill="currentColor"/>
              <circle cx="32" cy="16" r="2" fill="currentColor"/>
              <path d="M20 32h24M20 40h16" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
          </div>
          <h3>{{ $t('chat.startNewChat') }}</h3>
          <p>{{ $t('chat.startNewChatTip') }}</p>
          <div class="quick-actions">
            <button class="quick-action-btn" @click="inputMessage = $t('chat.quickQ1')">
              <el-icon><QuestionFilled /></el-icon>
              {{ $t('chat.quickQ1') }}
            </button>
            <button class="quick-action-btn" @click="inputMessage = $t('chat.quickQ2')">
              <el-icon><Cpu /></el-icon>
              {{ $t('chat.quickQ2') }}
            </button>
            <button class="quick-action-btn" @click="inputMessage = $t('chat.quickQ3')">
              <el-icon><Tools /></el-icon>
              {{ $t('chat.quickQ3') }}
            </button>
          </div>
        </div>

        <div v-else class="messages-wrapper">
          <template
            v-for="(msg, index) in displayMessages"
            :key="msg.id || index"
          >
            <div
              v-if="!(msg.isStreaming && !msg.content && !msg.thinkingContent && !(msg.toolCalls && msg.toolCalls.length > 0))"
              class="message-row"
              :class="msg.role"
            >
            <!-- 时间分隔线 -->
            <div v-if="shouldShowTime(index)" class="time-divider">
              {{ formatFullTime(msg.timestamp) }}
            </div>

            <!-- 消息气泡 -->
            <div class="message-bubble-wrapper" :class="msg.role">
              <!-- AI 消息 - 左侧 -->
              <template v-if="msg.role === 'assistant'">
                <el-avatar :size="36" class="msg-avatar ai-avatar">
                  <el-icon><Cpu /></el-icon>
                </el-avatar>
                <div class="message-body">
                  <!-- 思考/推理内容（默认折叠，显示思考时长） -->
                  <div v-if="msg.thinkingContent" class="thinking-collapse">
                    <div class="thinking-toggle" @click="toggleThinking(index)">
                      <span class="thinking-dot"></span>
                      <span class="thinking-label">Thought for {{ formatThinkingDuration(msg.thinkingContent) }}</span>
                      <el-icon class="thinking-arrow" :class="{ expanded: isThinkingExpanded(index) }">
                        <ArrowRight />
                      </el-icon>
                    </div>
                    <div v-show="isThinkingExpanded(index)" class="thinking-content">{{ msg.thinkingContent }}</div>
                  </div>
                  <!-- 工具调用 -->
                  <div v-if="msg.toolCalls && msg.toolCalls.length > 0" class="tool-call-bubble">
                    <div v-for="(tc, tcIndex) in msg.toolCalls" :key="tcIndex" class="tool-call-item" :class="`tool-status-${tc.status}`">
                      <div class="tool-call-header" @click="toggleToolCall(index, tcIndex)">
                        <el-icon class="expand-icon" :class="{ expanded: isToolCallExpanded(index, tcIndex) }">
                          <ArrowRight />
                        </el-icon>
                        <el-icon :class="['tool-icon', `tool-icon-${getToolCategory(tc.name)}`]">
                          <component :is="getToolIcon(tc.name)" />
                        </el-icon>
                        <span class="tool-name">{{ formatToolName(tc.name) }}</span>
                        <span v-if="tc.loading" class="tool-loading">
                          <span class="loading-dots"><span></span><span></span><span></span></span>
                          <span class="loading-text">执行中...</span>
                        </span>
                        <span v-else class="tool-result-summary" :class="{ 'is-error': tc.status === 'error' }">
                          {{ formatToolResultSummary(tc) }}
                        </span>
                        <span v-if="tc.duration != null" class="tool-duration">{{ tc.duration }}ms</span>
                      </div>
                      <div v-show="isToolCallExpanded(index, tcIndex)" class="tool-result">
                        <pre v-if="isJsonString(tc.result)" class="tool-result-json"><code>{{ formatJsonResult(tc.result) }}</code></pre>
                        <template v-else>{{ tc.result }}</template>
                      </div>
                    </div>
                  </div>
                  <!-- 普通回复 - 流式时内容为空则不显示气泡 -->
                  <div v-if="!msg.isStreaming || msg.content" class="message-bubble assistant">
                    <div class="message-content" v-html="formatMessage(msg.content)"></div>
                    <!-- 消息元数据 -->
                    <div class="message-meta" v-if="msg.latency || msg.input_tokens || msg.output_tokens">
                      <span v-if="msg.latency" class="meta-item">⏱ {{ msg.latency }}ms</span>
                      <span v-if="msg.input_tokens" class="meta-item">↑ {{ msg.input_tokens }}</span>
                      <span v-if="msg.output_tokens" class="meta-item">↓ {{ msg.output_tokens }}</span>
                    </div>
                    <div class="bubble-actions">
                      <el-button size="small" text @click="copyMessage(msg.content)">
                        <el-icon><CopyDocument /></el-icon>
                      </el-button>
                      <el-button size="small" text @click="regenerate(index)">
                        <el-icon><Refresh /></el-icon>
                      </el-button>
                    </div>
                  </div>
                  <!-- 流式等待中（无内容无工具调用时） -->
                  <div v-else-if="msg.isStreaming" class="message-bubble assistant loading">
                    <div class="typing-dots"><span></span><span></span><span></span></div>
                  </div>
                </div>
              </template>

              <!-- 用户消息 - 右侧 -->
              <template v-else>
                <div class="message-bubble user">
                  <div class="message-content">{{ msg.content }}</div>
                  <!-- 时间戳 -->
                  <div class="message-time" v-if="msg.timestamp">
                    {{ formatTime(msg.timestamp) }}
                  </div>
                </div>
                <el-avatar :size="36" class="msg-avatar user-avatar">
                  <el-icon><User /></el-icon>
                </el-avatar>
              </template>
            </div>
          </div>
          </template>

          <!-- 加载中 - 只在当前会话显示，流式消息有内容时隐藏 -->
          <div v-if="isCurrentConversationLoading() && !streamingMsgHasContent" class="message-row assistant">
            <div class="message-bubble-wrapper assistant">
              <el-avatar :size="36" class="msg-avatar ai-avatar">
                <el-icon><Cpu /></el-icon>
              </el-avatar>
              <div class="message-bubble assistant loading">
                <div class="typing-dots">
                  <span></span><span></span><span></span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 工具执行状态 -->
        <div v-if="activeTools.size > 0" class="tools-status">
          <div v-for="[id, tool] in activeTools" :key="id" class="tool-status-item" :class="tool.status">
            <div class="tool-header">
              <span class="tool-icon">{{ tool.status === 'running' ? '🔧' : tool.status === 'complete' ? '✅' : '❌' }}</span>
              <span class="tool-name">{{ tool.name }}</span>
              <span v-if="tool.duration_ms" class="tool-duration">{{ tool.duration_ms.toFixed(0) }}ms</span>
            </div>
            <div v-if="tool.status === 'running'" class="tool-progress">
              <el-progress :percentage="tool.progress || 0" :stroke-width="3" :show-text="false" />
            </div>
          </div>
        </div>
      </div>

      <!-- 输入区域（类似 OpenClaw 风格） -->
      <div class="chat-input-area">
        <!-- 上下文使用率提示 -->
        <div v-if="contextUsageStatus && contextUsageStatus.status !== 'normal'" class="context-usage-bar">
          <el-progress
            :percentage="Math.round(contextUsageStatus.percent)"
            :color="contextUsageStatus.color"
            :stroke-width="8"
            :show-text="true"
            class="usage-progress"
          />
          <span class="usage-label" :style="{ color: contextUsageStatus.color }">
            {{ contextUsageStatus.label }}
            ({{ contextUsageStatus.inputTokens.toLocaleString() }} / {{ contextUsageStatus.window.toLocaleString() }} tokens)
          </span>
          <el-button
            v-if="contextUsageStatus.status === 'caution' || contextUsageStatus.status === 'critical'"
            size="small"
            link
            type="warning"
            @click="compactContext()"
          >
            立即压缩
          </el-button>
        </div>

        <!-- 输入框 -->
        <div class="input-main">
          <el-input
            v-model="inputMessage"
            type="textarea"
            :rows="3"
            :placeholder="$t('chat.placeholder')"
            @keydown="handleKeydown"
            @compositionstart="isComposing = true"
            @compositionend="isComposing = false"
            :disabled="isCurrentConversationLoading() || availableModels.length === 0"
            resize="none"
          />
        </div>

        <!-- 底部工具栏 -->
        <div class="input-footer">
          <div class="footer-left">
            <!-- Upload file -->
            <el-button size="small" :title="$t('chat.uploadFile')">
              <el-icon><Paperclip /></el-icon>
            </el-button>

            <!-- Stop response -->
            <el-button
              v-if="isCurrentConversationLoading()"
              size="small"
              type="danger"
              @click="cancelCurrentTask"
              :title="$t('chat.stopResponse')"
            >
              <el-icon><VideoPause /></el-icon>
            </el-button>
          </div>

          <div class="footer-right">
            <!-- 发送按钮 -->
            <el-button type="primary" @click="sendMessage" :loading="isCurrentConversationLoading()" :disabled="!inputMessage.trim()">
              <el-icon><Promotion /></el-icon>
              {{ $t('chat.send') }}
            </el-button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, nextTick, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, ChatLineRound, User, MoreFilled, CopyDocument, Refresh, ArrowRight, Fold, Expand, Paperclip, VideoPause, Promotion, QuestionFilled, Cpu, Tools, FolderOpened, Search, Setting, Document, Link, Collection } from '@element-plus/icons-vue'
import { api, longApi } from '../api'
import { useI18n } from 'vue-i18n'
import { getAccessToken } from '@/stores/user'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { logger } from '../utils/logger'
import { useMessageStreaming } from '@/composables/useMessageStreaming'
import { useChatRealtime } from '@/composables/useChatRealtime'
import type { ChatMessage, StreamRealtimeMessage } from '@/types/chat'

const { locale, t: $t } = useI18n()

// WebSocket 相关
const wsSessionId = ref<string | null>(null)
let ws: WebSocket | null = null
let heartbeatTimer: ReturnType<typeof setInterval> | null = null
const toolCleanupTimers = new Set<ReturnType<typeof setTimeout>>()

// 工具执行状态（实时展示）
interface ToolExecution {
  id: string
  name: string
  status: 'running' | 'complete' | 'error'
  progress?: number
  message?: string
  result?: string
  error?: string
  duration_ms?: number
}
const activeTools = ref<Map<string, ToolExecution>>(new Map())

// ─── 流式消息状态（reducer 架构）───
const {
  messages: streamMessages,
  isStreaming: isStreamActive,
  dispatchStreamEvent,
  replaceMessages,
  clearMessages,
  addMessage,
  cacheSessionMessages,
  readSessionMessages,
  restoreSessionMessages,
} = useMessageStreaming()

// 包装 dispatch：拦截 done 事件保存 context_usage
const wrappedDispatch = (data: StreamRealtimeMessage) => {
  if (data.type === 'done' && data.context_usage) {
    contextUsage.value = data.context_usage
  }
  dispatchStreamEvent(data)
}

// 映射旧字段 → 模板期望的字段（兼容现有模板）
const displayMessages = computed(() => {
  const result = streamMessages.value
    .filter((m) => m.role !== 'system')
    .map((m) => ({
      ...m,
      thinkingContent: m.reasoningContent,
      toolCalls: m.toolCalls?.map((tc) => ({
        ...tc,
        result: tc.result || tc.error || '',
        status: tc.status || (tc.error ? 'error' : tc.result ? 'success' : 'pending'),
        loading: tc.status === 'running',
      })),
    }))
  return result
})

const { startStream } = useChatRealtime({
  dispatchStreamEvent: wrappedDispatch,
  getCurrentSessionId: () => currentConversation.value?.sessionId || null,
  cacheSessionMessages,
  onError: (msg: string) => {
    ElMessage.error(msg)
    // 添加一条 assistant 错误消息，避免 UI 卡在 loading 且无内容
    addMessage({
      id: `msg-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      role: 'assistant',
      content: `⚠️ ${msg}`,
      timestamp: new Date(),
    })
  },
})

// 连接 WebSocket
function connectWebSocket() {
  if (ws) return

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const wsUrl = `${protocol}//localhost:20005/api/ws`

  ws = new WebSocket(wsUrl)

  ws.onopen = () => {
    logger.log('WebSocket connected')
    // 发送心跳
    heartbeatTimer = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping', timestamp: Date.now() }))
      }
    }, 30000)
  }

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      handleWebSocketMessage(data)
    } catch (e) {
      console.error('WebSocket message parse error:', e)
    }
  }

  ws.onerror = (error) => {
    console.error('WebSocket error:', error)
  }

  ws.onclose = () => {
    logger.log('WebSocket disconnected')
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer)
      heartbeatTimer = null
    }
    ws = null
  }
}

// 处理 WebSocket 消息
function handleWebSocketMessage(data: any) {
  switch (data.type) {
    case 'connected':
      wsSessionId.value = data.session_id
      logger.log('WebSocket session:', data.session_id)
      break

    // 工具开始执行
    case 'tool_start':
      activeTools.value.set(data.tool_call_id, {
        id: data.tool_call_id,
        name: data.tool,
        status: 'running',
        progress: 0,
      })
      logger.log('🔧 Tool start:', data.tool)
      break

    // 工具执行进度
    case 'tool_progress':
      const runningTool = activeTools.value.get(data.tool_call_id)
      if (runningTool) {
        runningTool.progress = data.progress
        runningTool.message = data.message
      }
      break

    // 工具执行完成
    case 'tool_complete':
      const completeTool = activeTools.value.get(data.tool_call_id)
      if (completeTool) {
        completeTool.status = 'complete'
        completeTool.result = data.result
        completeTool.duration_ms = data.duration_ms
      }
      logger.log(`✅ Tool complete: ${data.tool} (${data.duration_ms?.toFixed(0)}ms)`)
      // 立即移除，避免与消息内的工具卡片重叠
      activeTools.value.delete(data.tool_call_id)
      break

    // 工具执行错误
    case 'tool_error':
      const errorTool = activeTools.value.get(data.tool_call_id)
      if (errorTool) {
        errorTool.status = 'error'
        errorTool.error = data.error
        errorTool.duration_ms = data.duration_ms
      }
      console.error(`❌ Tool error: ${data.tool} - ${data.error}`)
      // 立即移除
      activeTools.value.delete(data.tool_call_id)
      break

    case 'agent_complete':
      logger.log('Agent complete:', data.total_iterations, 'iterations')
      break

    // 流式响应块 - 需要检查 session_id 是否匹配当前会话
    case 'stream_chunk':
      {
        const currentSessId = currentConversation.value?.sessionId
        if (currentSessId && data.session_id === currentSessId) {
          dispatchStreamEvent({ type: 'content', content: data.chunk })
        }
      }
      break

    case 'stream_end':
      dispatchStreamEvent({ type: 'message_complete' })
      break
  }
}

function disconnectWebSocket() {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }
  if (ws) {
    ws.close()
    ws = null
  }
}

// 取消当前任务
function cancelCurrentTask() {
  const sessionId = currentConversation.value?.sessionId
  if (sessionId && ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({
      type: 'cancel',
      session_id: sessionId
    }))
    ElMessage.warning($t('chat.cancelSent'))
  }
}

// 斜杠命令定义
const slashCommands = [
  { command: '/new', description: $t('chat.newChat'), action: 'new' },
  { command: '/clear', description: $t('chat.slashClear'), action: 'clear' },
  { command: '/compact', description: '压缩上下文', action: 'compact' },
  { command: '/history', description: $t('chat.chatHistoryInSidebar'), action: 'history' },
  { command: '/model', description: $t('chat.slashModels'), action: 'model' },
  { command: '/system', description: $t('chat.slashSystem'), action: 'system' },
  { command: '/export', description: $t('chat.slashExport'), action: 'export' },
  { command: '/help', description: $t('chat.slashHelp'), action: 'help' }
]

// 命令提示（暂时不用）
// const filteredCommands = computed(() => {
//   if (!inputMessage.value.startsWith('/')) return []
//   const query = inputMessage.value.toLowerCase()
//   return slashCommands.filter(cmd => cmd.command.startsWith(query))
// })

interface Conversation {
  id: string
  title: string
  messages: ChatMessage[]
  createdAt: Date
  updatedAt?: Date
  sessionId?: string  // 每个会话独立的 WebSocket session ID
  draftText?: string  // 草稿文本（切换会话时保留）
}

interface ModelConfig {
  id: number
  name: string
  display_name: string
  model_name: string
  provider: string
  tier: string
  is_default: boolean
}

const conversations = ref<Conversation[]>([])
const currentConversation = ref<Conversation | null>(null)
const inputMessage = ref('')
const loadingConversationId = ref<string | null>(null)  // 正在加载的会话 ID
const availableModels = ref<ModelConfig[]>([])
const selectedModelId = ref<number | null>(null)

const tierLabels: Record<string, string> = { opus: 'Opus - 最强', sonnet: 'Sonnet - 均衡', haiku: 'Haiku - 快速', custom: '自定义' }
const tieredModels = computed(() => {
  const groups: Record<string, ModelConfig[]> = {}
  for (const m of availableModels.value) {
    const t = m.tier || 'sonnet'
    if (!groups[t]) groups[t] = []
    groups[t].push(m)
  }
  return ['opus', 'sonnet', 'haiku', 'custom']
    .filter(t => groups[t]?.length)
    .map(t => ({ tier: t, label: tierLabels[t] || t, models: groups[t] }))
})
const messagesContainer = ref<HTMLElement | null>(null)
const sidebarCollapsed = ref(false) // 左侧栏折叠状态
const searchKeyword = ref('')
const contextUsage = ref<any>(null)  // 上下文使用率信息
const expandedToolCalls = ref<Set<string>>(new Set())  // 展开的工具调用
const expandedThinking = ref<Set<number>>(new Set())  // 展开的思考过程

// 切换思考过程展开/收缩
function toggleThinking(msgIndex: number) {
  if (expandedThinking.value.has(msgIndex)) {
    expandedThinking.value.delete(msgIndex)
  } else {
    expandedThinking.value.add(msgIndex)
  }
}

function isThinkingExpanded(msgIndex: number): boolean {
  return expandedThinking.value.has(msgIndex)
}

/** 根据思考内容长度估算思考时长（字符数 / 100 ≈ 秒数） */
function formatThinkingDuration(content: string): string {
  const seconds = Math.max(1, Math.round((content?.length || 0) / 100))
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`
}

// 检查当前会话是否正在加载
function isCurrentConversationLoading(): boolean {
  return isStreamActive.value && loadingConversationId.value === currentConversation.value?.id
}

// 最后一条 AI 消息是否正在流式且有可见内容
const streamingMsgHasContent = computed(() => {
  const msgs = streamMessages.value
  if (!msgs.length) return false
  const last = msgs[msgs.length - 1]
  const hasVisibleContent = !!(last.reasoningContent || last.content)
  const hasToolCalls = !!(last.toolCalls && last.toolCalls.length > 0)
  return last.role === 'assistant' && last.isStreaming && (hasVisibleContent || hasToolCalls)
})

// 上下文使用率状态（用于 UI 提示）
const contextUsageStatus = computed(() => {
  const u = contextUsage.value
  if (!u) return null
  return {
    percent: u.usage_percent || 0,
    status: u.status || 'normal',
    inputTokens: u.input_tokens || 0,
    window: u.context_window || 0,
    label: u.status === 'critical' ? '即将自动压缩' :
           u.status === 'caution' ? '上下文较长，可点击压缩' :
           u.status === 'warning' ? '上下文使用率较高' : '',
    color: u.status === 'critical' || u.status === 'block' ? '#f56c6c' :
           u.status === 'caution' ? '#e6a23c' :
           u.status === 'warning' ? '#409eff' : '#67c23a',
  }
})

// 切换工具调用展开/收缩
// 持久化消息到后端
async function persistMessage(conv: Conversation, role: string, content: string, toolCalls?: any[]) {
  try {
    const params: any = { role, content }
    if (toolCalls && toolCalls.length > 0) {
      params.tool_calls = JSON.stringify(toolCalls)
    }
    await api.post(`/chat/sessions/${conv.id}/messages`, null, { params })
  } catch (e) {
    // 后端不可用时静默降级
  }
}

// 工具分类 & 图标映射
const TOOL_CATEGORY_MAP: Record<string, string> = {
  // 文件操作
  read_file: 'file',
  write_file: 'file',
  list_dir: 'file',
  create_dir: 'file',
  delete_file: 'file',
  copy_file: 'file',
  move_file: 'file',
  // 搜索
  file_search: 'search',
  grep: 'search',
  glob: 'search',
  web_search: 'search',
  web_fetch: 'search',
  // 终端/执行
  exec: 'terminal',
  bash: 'terminal',
  terminal: 'terminal',
  run_command: 'terminal',
  // 配置/系统
  config: 'setting',
  sandbox: 'setting',
  // 网页/链接
  browser: 'link',
  fetch_url: 'link',
  // 代码分析
  code_review: 'code',
  analyze: 'code',
  // 技能
  skill: 'collection',
}

function getToolCategory(name: string): string {
  return TOOL_CATEGORY_MAP[name] || 'default'
}

function getToolIcon(name: string) {
  const cat = getToolCategory(name)
  const iconMap: Record<string, any> = {
    file: FolderOpened,
    search: Search,
    terminal: Promotion,
    setting: Setting,
    link: Link,
    code: Document,
    default: Tools,
  }
  return iconMap[cat] || Tools
}

function formatToolName(name: string): string {
  if (!name) return ''
  // 如果已经是可读格式（包含空格或大写驼峰），直接返回
  if (/\s/.test(name) || /^[A-Z][a-z]+([A-Z][a-z]+)+$/.test(name)) {
    return name
  }
  return name
    .split('_')
    .map(word => {
      if (!word) return ''
      // 纯中文或已是大写的词不处理
      if (/^[一-龥]+$/.test(word) || /^[A-Z]+$/.test(word)) return word
      return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase()
    })
    .join(' ')
}

/** 智能摘要：根据工具状态/结果生成友好的 header 摘要 */
function formatToolResultSummary(tc: any): string {
  if (tc.status === 'error') {
    const err = tc.result || '执行失败'
    return err.length > 30 ? err.substring(0, 30) + '…' : err
  }
  const result = tc.result || ''
  if (!result) return ''
  // JSON 结果：提取关键状态
  const json = tryParseJson(result)
  if (json) {
    if (json.success === true) return '✅ 成功'
    if (json.success === false) return `❌ ${json.message || json.error || '失败'}`
    if (json.error) return `❌ ${json.error}`
    return '✅ 完成'
  }
  // 文本结果：截取前 40 字符
  return result.length > 40 ? result.substring(0, 40) + '…' : result
}

function tryParseJson(str: string): any | null {
  if (!str || typeof str !== 'string') return null
  const trimmed = str.trim()
  if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) return null
  try {
    return JSON.parse(trimmed)
  } catch {
    return null
  }
}

function isJsonString(str: string): boolean {
  return tryParseJson(str) !== null
}

function formatJsonResult(str: string): string {
  try {
    return JSON.stringify(JSON.parse(str), null, 2)
  } catch {
    return str
  }
}

function toggleToolCall(msgIndex: number, tcIndex: number) {
  const key = `${msgIndex}-${tcIndex}`
  if (expandedToolCalls.value.has(key)) {
    expandedToolCalls.value.delete(key)
  } else {
    expandedToolCalls.value.add(key)
  }
}

function isToolCallExpanded(msgIndex: number, tcIndex: number) {
  return expandedToolCalls.value.has(`${msgIndex}-${tcIndex}`)
}

// 过滤对话列表
const filteredConversations = computed(() => {
  if (!searchKeyword.value) return conversations.value
  return conversations.value.filter(c =>
    c.title.toLowerCase().includes(searchKeyword.value.toLowerCase())
  )
})

// 加载可用模型
async function loadModels() {
  try {
    const response = await api.get('/chat/models')
    availableModels.value = response.data
    if (availableModels.value.length > 0) {
      const sonnet = availableModels.value.find(m => m.tier === 'sonnet' && m.is_default)
      const anyDefault = availableModels.value.find(m => m.is_default)
      selectedModelId.value = sonnet?.id || anyDefault?.id || availableModels.value[0].id
    }
  } catch (error) {
    console.error('Failed to load models:', error)
  }
}

// 从后端加载会话列表
async function loadConversations() {
  try {
    const res = await api.get('/chat/sessions')
    const sessions = res.data || []
    conversations.value = sessions.map((s: any) => ({
      id: s.id,
      title: s.title,
      messages: [],
      createdAt: new Date(s.created_at),
      updatedAt: new Date(s.updated_at),
      sessionId: s.id,
    }))
  } catch (e) {
    // 后端不可用时从 localStorage 降级
    const saved = localStorage.getItem('pioneclaw_conversations')
    if (saved) conversations.value = JSON.parse(saved)
  }
}

// 保存对话到后端（在新对话/删除对话后更新列表）
function saveConversations() {
  // 后端已持久化，仅保留 localStorage 作为离线备份
  localStorage.setItem('pioneclaw_conversations', JSON.stringify(conversations.value))
}

// 新建对话
async function newConversation() {
  syncCurrentMessagesToConv()
  clearMessages()
  try {
    const res = await api.post('/chat/sessions', null, { params: { title: $t('chat.newChat') } })
    const s = res.data
    const conv: Conversation = {
      id: s.id,
      title: s.title,
      messages: [],
      createdAt: new Date(s.created_at),
      updatedAt: new Date(),
      sessionId: s.id,
    }
    conversations.value.unshift(conv)
    currentConversation.value = conv
  } catch (e) {
    // 降级：本地创建
    const conv: Conversation = {
      id: Date.now().toString(),
      title: $t('chat.newChat'),
      messages: [],
      createdAt: new Date(),
      updatedAt: new Date(),
      sessionId: crypto.randomUUID(),
    }
    conversations.value.unshift(conv)
    currentConversation.value = conv
  }
  inputMessage.value = ''
  saveConversations()
}

/** 统一切换会话：保存旧会话状态 → 切换 → 恢复新会话消息 */
function syncCurrentMessagesToConv() {
  if (currentConversation.value) {
    currentConversation.value.draftText = inputMessage.value
    currentConversation.value.messages = [...streamMessages.value]
  }
}

// 选择对话
async function selectConversation(conv: Conversation) {
  syncCurrentMessagesToConv()
  currentConversation.value = conv
  inputMessage.value = conv.draftText || ''

  let loadedMessages: ChatMessage[] = []

  // 1. 优先从后端加载（持久化数据为准）
  try {
    const res = await api.get(`/chat/sessions/${conv.id}`)
    if (res.data?.messages && res.data.messages.length > 0) {
      loadedMessages = res.data.messages.map((m: any, i: number) => ({
        id: m.id || `msg-${Date.now()}-${i}`,
        role: m.role,
        content: m.content,
        reasoningContent: m.thinkingContent || m.reasoning_content,
        toolCalls: m.tool_calls?.map((tc: any, j: number) => ({
          id: tc.id || `tc-${Date.now()}-${j}`,
          name: tc.name,
          arguments: tc.arguments,
          result: tc.result,
          status: tc.loading ? 'running' : tc.result ? 'success' : 'pending',
        })),
        timestamp: new Date(m.created_at),
      }))
      conv.messages = loadedMessages
    }
  } catch (e) { /* 静默降级，继续尝试其他来源 */ }

  // 2. 后端无数据时，用本地已保存的消息
  if (loadedMessages.length === 0 && conv.messages.length > 0) {
    loadedMessages = [...conv.messages]
  }

  // 3. 尝试从 sessionStorage 读取流式中断状态（不修改 runtime）
  let cacheMessages: ChatMessage[] = []
  let cacheIsStreaming = false
  if (conv.sessionId) {
    const cache = readSessionMessages(conv.sessionId)
    if (cache && cache.messages.length > 0) {
      cacheMessages = cache.messages
      cacheIsStreaming = cache.isStreaming
    }
  }

  // 4. 选择数据源：只有缓存标记为流式中断时才优先用缓存，否则后端持久化数据为准
  if (cacheIsStreaming && cacheMessages.length > 0) {
    loadedMessages = cacheMessages
  }

  replaceMessages(loadedMessages)
  scrollToBottom()
}

// 删除当前对话
async function deleteCurrentConversation() {
  if (!currentConversation.value) return
  const convId = currentConversation.value.id
  const index = conversations.value.findIndex(c => c.id === convId)
  if (index > -1) {
    conversations.value.splice(index, 1)
    syncCurrentMessagesToConv()
    const nextConv = conversations.value[0] || null
    currentConversation.value = nextConv
    if (nextConv) {
      replaceMessages(nextConv.messages.length > 0 ? [...nextConv.messages] : [])
    } else {
      clearMessages()
    }
    saveConversations()
  }
  try {
    await api.delete(`/chat/sessions/${convId}`)
  } catch (e) { /* 后端不可用时不报错 */ }
}

// 清空当前对话
function clearCurrentConversation() {
  if (currentConversation.value) {
    clearMessages()
    currentConversation.value.messages = []
    currentConversation.value.title = $t('chat.newChat')
    saveConversations()
  }
}

// 导出对话
function exportConversation() {
  if (!currentConversation.value) return
  const content = streamMessages.value
    .map(m => `[${m.role === 'user' ? 'User' : 'AI'}] ${m.content}`)
    .join('\n\n')

  const blob = new Blob([content], { type: 'text/plain' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${currentConversation.value.title}.txt`
  a.click()
  URL.revokeObjectURL(url)
}

// 手动压缩上下文
async function compactContext(instruction?: string) {
  if (!currentConversation.value) {
    ElMessage.warning('请先选择一个会话')
    return
  }
  if (streamMessages.value.length === 0) {
    ElMessage.warning('当前会话没有消息可压缩')
    return
  }

  const loading = ElMessage.info({ message: '正在压缩上下文...', duration: 0 })
  try {
    const messages = streamMessages.value.map(m => ({
      role: m.role,
      content: m.content,
    }))

    const res = await api.post('/chat/compact', {
      messages,
      instruction: instruction || undefined,
      model_config_id: selectedModelId.value,
      session_id: currentConversation.value.sessionId || undefined,
    })

    const data = res.data
    if (data.success) {
      const originalCount = streamMessages.value.length
      const summaryText = data.summary || ''

      // 用后端返回的压缩后消息列表替换当前会话
      let nextMessages: ChatMessage[] = []
      if (data.messages && data.messages.length > 0) {
        nextMessages = data.messages.map((m: any, i: number) => ({
          id: `msg-${Date.now()}-${i}`,
          role: m.role,
          content: m.content,
          timestamp: new Date(),
        }))
      }

      // 追加压缩统计信息作为系统消息
      const infoText = `--- 上下文已压缩 ---\n压缩前: ${data.before_tokens?.toLocaleString() || '?'} tokens (${originalCount} 条消息)\n压缩后: ${data.after_tokens?.toLocaleString() || '?'} tokens (${data.kept_messages} 条消息)\n节省: ${data.saved_tokens?.toLocaleString() || '?'} tokens\n\n[摘要]\n${summaryText}`

      nextMessages.push({
        id: `msg-${Date.now()}-info`,
        role: 'system',
        content: infoText,
        timestamp: new Date(),
      })

      replaceMessages(nextMessages)
      currentConversation.value.messages = [...nextMessages]
      saveConversations()
      ElMessage.success(`上下文已压缩，节省 ${data.saved_tokens?.toLocaleString() || '?'} tokens`)
    } else {
      ElMessage.warning(data.message || '压缩失败')
    }
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '压缩请求失败')
  } finally {
    loading.close()
  }
}

// 处理斜杠命令
function handleSlashCommand(command: string) {
  const parts = command.split(' ')
  const cmdName = parts[0].toLowerCase()
  const args = parts.slice(1).join(' ').trim()
  const cmd = slashCommands.find(c => c.command === cmdName)

  if (!cmd) {
    ElMessage.warning($t('chat.unknownCommand', { cmd: command }))
    inputMessage.value = ''
    return
  }

  switch (cmd.action) {
    case 'new':
      newConversation()
      ElMessage.success($t('chat.newChatCreated'))
      break

    case 'clear':
      if (currentConversation.value) {
        clearMessages()
        currentConversation.value.messages = []
        saveConversations()
        ElMessage.success($t('chat.chatCleared'))
      }
      break

    case 'compact':
      compactContext(args || undefined)
      break

    case 'history':
      ElMessage.info($t('chat.chatHistoryInSidebar'))
      break

    case 'model':
      // 显示模型选择提示
      if (availableModels.value.length > 0) {
        const modelNames = availableModels.value.map(m => m.display_name).join(', ')
        ElMessage.info($t('chat.availableModels', { models: modelNames }))
      }
      break

    case 'system':
      ElMessage.info($t('chat.systemPromptComing'))
      break

    case 'export':
      exportConversation()
      ElMessage.success($t('chat.chatExported'))
      break

    case 'help':
      const helpText = slashCommands
        .map(c => `${c.command} - ${c.description}`)
        .join('\n')
      ElMessage.info({
        message: $t('chat.availableCommands', { commands: helpText }),
        duration: 5000
      })
      break
  }

  inputMessage.value = ''
}

// 选择命令（暂时不用）
// function selectCommand(command: string) {
//   inputMessage.value = command + ' '
// }

// 发送消息
async function sendMessage() {
  if (!inputMessage.value.trim()) return
  if (loadingConversationId.value === currentConversation.value?.id) return

  if (inputMessage.value.startsWith('/')) {
    handleSlashCommand(inputMessage.value.trim())
    return
  }

  if (!currentConversation.value) {
    await newConversation()
  }

  const userMessage = inputMessage.value.trim()
  inputMessage.value = ''
  if (currentConversation.value) currentConversation.value.draftText = ''

  // 添加用户消息到流式状态
  addMessage({
    id: `msg-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    role: 'user',
    content: userMessage,
    timestamp: new Date(),
  })

  // 持久化用户消息到后端
  persistMessage(currentConversation.value!, 'user', userMessage)

  // 更新标题
  if (streamMessages.value.length === 1) {
    currentConversation.value!.title = userMessage.slice(0, 30) + (userMessage.length > 30 ? '...' : '')
  }
  currentConversation.value!.updatedAt = new Date()
  scrollToBottom()

  loadingConversationId.value = currentConversation.value!.id
  const targetConversation = currentConversation.value
  if (!targetConversation) return

  try {
    const sessionId = targetConversation.sessionId || crypto.randomUUID()
    if (!targetConversation.sessionId) {
      targetConversation.sessionId = sessionId
    }

    // 构造 context（排除最后一条 user message）
    const contextMessages = streamMessages.value
      .slice(0, -1)
      .filter((m) => !m.isStreaming && m.role)
      .map((m) => ({ role: m.role, content: m.content }))

    const token = getAccessToken() || localStorage.getItem('token') || ''

    await startStream(
      '/api/chat/react/stream',
      {
        message: userMessage,
        context: contextMessages.length > 0 ? contextMessages : undefined,
        model_config_id: selectedModelId.value,
        enable_tools: true,
        max_iterations: 10,
        session_id: sessionId,
        fast_mode: false,
      },
      token
    )

    // 流式结束后同步回 conversation
    targetConversation.messages = [...streamMessages.value]
    const lastMsg = streamMessages.value[streamMessages.value.length - 1]
    if (lastMsg && lastMsg.role === 'assistant') {
      persistMessage(targetConversation, 'assistant', lastMsg.content || '', lastMsg.toolCalls)
    }
  } catch (error: any) {
    console.error('sendMessage error:', error)
    const msg = error.message || ''
    if (msg.includes('401')) {
      addMessage({
        id: `msg-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        role: 'assistant',
        content: '⚠️ 登录已过期，请重新登录',
        timestamp: new Date(),
      })
      localStorage.removeItem('token')
      setTimeout(() => { window.location.href = '/login' }, 1000)
      return
    }
    const errorMsg = error.response?.data?.detail || error.detail || msg || $t('chat.requestFailed')
    addMessage({
      id: `msg-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      role: 'assistant',
      content: `⚠️ ${errorMsg}`,
      timestamp: new Date(),
    })
    ElMessage.error(errorMsg)
  } finally {
    loadingConversationId.value = null
    targetConversation.updatedAt = new Date()
    saveConversations()
    scrollToBottom()
  }
}

// 重新生成
async function regenerate(index: number) {
  // 找到上一条用户消息
  const messages = streamMessages.value.slice(0, index)
  const lastUserMsg = [...messages].reverse().find(m => m.role === 'user')
  if (!lastUserMsg) return

  // 删除从 index 开始的所有消息
  replaceMessages(messages)
  currentConversation.value!.messages = [...messages]

  // 重新发送
  const targetConversation = currentConversation.value
  loadingConversationId.value = targetConversation!.id
  try {
    const response = await longApi.post('/chat/completions', {
      messages: messages.map(m => ({ role: m.role, content: m.content })),
      model_config_id: selectedModelId.value
    })

    if (response.data.success) {
      // 去除回复开头的多余空行
      let content = response.data.response
      content = content.replace(/^\n{2,}/, '\n').replace(/^\s{2,}/, '')

      const assistantMsg: ChatMessage = {
        id: `msg-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        role: 'assistant',
        content: content,
        latency: response.data.latency_ms,
        input_tokens: response.data.usage?.input_tokens,
        output_tokens: response.data.usage?.output_tokens,
        timestamp: new Date(),
      }
      addMessage(assistantMsg)
      targetConversation!.messages = [...streamMessages.value]
    } else {
      ElMessage.error(response.data.message)
    }
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || $t('chat.requestFailed'))
  } finally {
    loadingConversationId.value = null
    saveConversations()
    scrollToBottom()
  }
}

// 复制消息
function copyMessage(content: string) {
  navigator.clipboard.writeText(content)
  ElMessage.success($t('chat.copiedToClipboard'))
}

// 处理键盘事件（兼容中文输入法）
const isComposing = ref(false)

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !isComposing.value) {
    event.preventDefault()
    sendMessage()
  }
}

// 滚动到底部
function scrollToBottom() {
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
    }
  })
}

// 格式化时间
function formatTime(date?: Date | string): string {
  if (!date) return ''
  const d = new Date(date)
  const lang = locale.value === 'zh-CN' ? 'zh-CN' : 'en-US'
  return d.toLocaleTimeString(lang, { hour: '2-digit', minute: '2-digit' })
}

// 格式化完整时间（用于时间分隔线）
function formatFullTime(date?: Date | string): string {
  if (!date) return ''
  const d = new Date(date)
  const now = new Date()
  const isToday = d.toDateString() === now.toDateString()
  const yesterday = new Date(now)
  yesterday.setDate(yesterday.getDate() - 1)
  const isYesterday = d.toDateString() === yesterday.toDateString()

  const lang = locale.value === 'zh-CN' ? 'zh-CN' : 'en-US'
  const time = d.toLocaleTimeString(lang, { hour: '2-digit', minute: '2-digit' })

  if (isToday) {
    return $t('chat.today') + ' ' + time
  } else if (isYesterday) {
    return $t('chat.yesterday') + ' ' + time
  } else {
    return d.toLocaleDateString(lang, { month: '2-digit', day: '2-digit' }) + ' ' + time
  }
}

// 是否显示时间分隔线（每隔5分钟显示一次）
function shouldShowTime(index: number): boolean {
  if (index === 0) return true
  const messages = streamMessages.value
  if (!messages || index >= messages.length) return false

  const currentMsg = messages[index]
  const prevMsg = messages[index - 1]

  if (!currentMsg.timestamp || !prevMsg.timestamp) return false

  const currentTime = new Date(currentMsg.timestamp).getTime()
  const prevTime = new Date(prevMsg.timestamp).getTime()

  // 如果间隔超过5分钟，显示时间
  return (currentTime - prevTime) > 5 * 60 * 1000
}

// 获取最后一条消息
function getLastMessage(conv: Conversation): string {
  const last = conv.messages[conv.messages.length - 1]
  if (!last) return 'No messages'
  return last.content.slice(0, 30) + (last.content.length > 30 ? '...' : '')
}

// 格式化消息（markdown 渲染，支持流式输出）
function formatMessage(content: string): string {
  let text = content.replace(/^[\s\n]+/, '')

  // 修复 LLM 输出的标题格式问题：##text → ## text（CommonMark 要求空格）
  text = text.replace(/^(#{1,6})([^\s#])/gm, '$1 $2')

  // 行首短横紧跟数字/文字（缺少空格）→ 补空格，使其成为合法列表项
  text = text.replace(/^(-)(\d)/gm, '- $2')
  // 紧跟前文的无序列表项 → 插入空行确保解析为列表
  text = text.replace(/([^\n])\n(- [^\n]+)/g, '$1\n\n$2')
  // 冒号后空格分隔的 dash-items → 拆成真正的 markdown 列表项
  // 例: "计算过程： -100×90=9,000 -18²=324" → "计算过程：\n- 100×90=9,000\n- 18²=324"
  text = text.replace(/([：:])\s*-(.+)$/gm, (_, colon, items) => {
    const parts = items.split(/\s+-(?=\S)/)
    if (parts.length <= 1) return _
    return colon + '\n' + parts.map((s: string) => '- ' + s.replace(/^-?\s*/, '').trim()).join('\n')
  })

  // 保护 code blocks（marked 能处理围栏代码块，但保留自定义 <pre><code> 包装）
  const codeBlocks: string[] = []
  text = text.replace(/```(\w*)\n([\s\S]*?)```/g, (_, _lang, code) => {
    codeBlocks.push(`<pre><code>${code.trim()}</code></pre>`)
    return `\x00CODE${codeBlocks.length - 1}\x00`
  })
  text = text.replace(/`([^`]+)`/g, (_, code) => {
    codeBlocks.push(`<code>${code}</code>`)
    return `\x00CODE${codeBlocks.length - 1}\x00`
  })

  // —— 表格行断行修复（LLM 偶发 bug：表格第一列意外换行）——
  // 模式 A：|cell\n|rest|of|row|  → 直接拼接
  text = text.replace(/^(\|[^|\n]+)\n(\|[^|\n]+\|.+\|)$/gm, '$1$2')
  // 模式 B：|cell\nrest    of    row（可能带尾部 |）→ 空格转 |、补齐
  text = text.replace(/^(\|[^|\n]+)\n([^|\n]+\s{2,}[^|\n]+\|?)$/gm,
    (_, first: string, rest: string) => {
      const clean = rest.replace(/\|$/, '')
      return `${first}|${clean.replace(/\s{2,}/g, '|')}|`
    }
  )
  // 模式 C：||cell → |\n|cell（粘连的列分隔符）
  text = text.replace(/\|\|(?=\-)/g, '|\n|')
  text = text.replace(/\|\|(?=[^\-])/g, '|\n|')

  // 用 marked 渲染 markdown → HTML
  let html = marked.parse(text) as string

  // 表格加上样式类
  html = html.replace(/<table>/g, '<table class="md-table">')

  // 恢复 code blocks
  html = html.replace(/\x00CODE(\d+)\x00/g, (_, i: string) => codeBlocks[parseInt(i)])

  // 清理块级元素周围多余的 <br>
  html = html.replace(/(?:<br>\s*)+(<(?:h[2-5]|table|ul|ol|hr|p|blockquote))/g, '$1')
  html = html.replace(/(<\/(?:table|ul|ol|hr|blockquote)>)(?:<br>\s*)+/g, '$1')

  // XSS 防护：消毒 HTML，移除危险标签和属性
  return DOMPurify.sanitize(html)
}

onMounted(async () => {
  loadModels()
  await loadConversations()
  if (conversations.value.length > 0) {
    const firstConv = conversations.value[0]
    currentConversation.value = firstConv
    let loadedMessages: ChatMessage[] = []

    // 1. 优先从后端加载消息（持久化数据为准）
    try {
      const res = await api.get(`/chat/sessions/${firstConv.id}`)
      if (res.data?.messages && res.data.messages.length > 0) {
        loadedMessages = res.data.messages.map((m: any, i: number) => ({
          id: m.id || `msg-${Date.now()}-${i}`,
          role: m.role,
          content: m.content,
          reasoningContent: m.thinkingContent || m.reasoning_content,
          toolCalls: m.tool_calls?.map((tc: any, j: number) => ({
            id: tc.id || `tc-${Date.now()}-${j}`,
            name: tc.name,
            arguments: tc.arguments,
            result: tc.result,
            status: tc.loading ? 'running' : tc.result ? 'success' : 'pending',
          })),
          timestamp: new Date(m.created_at),
        }))
        firstConv.messages = loadedMessages
      }
    } catch (e) { /* 静默降级 */ }

    // 2. 后端无数据时，用本地已保存的消息
    if (loadedMessages.length === 0 && firstConv.messages.length > 0) {
      loadedMessages = [...firstConv.messages]
    }

    // 3. 尝试从 sessionStorage 读取流式中断状态（不修改 runtime）
    let cacheMessages: ChatMessage[] = []
    let cacheIsStreaming = false
    if (firstConv.sessionId) {
      const cache = readSessionMessages(firstConv.sessionId)
      if (cache && cache.messages.length > 0) {
        cacheMessages = cache.messages
        cacheIsStreaming = cache.isStreaming
      }
    }

    // 4. 选择数据源：只有缓存标记为流式中断时才优先用缓存，否则后端持久化数据为准
    if (cacheIsStreaming && cacheMessages.length > 0) {
      loadedMessages = cacheMessages
    }

    replaceMessages(loadedMessages)
  }
  // 连接 WebSocket
  connectWebSocket()
})

onUnmounted(() => {
  disconnectWebSocket()
  // 清除所有工具清理定时器
  toolCleanupTimers.forEach(timer => clearTimeout(timer))
  toolCleanupTimers.clear()
  // 清除活跃工具状态
  activeTools.value.clear()
})
</script>

<style scoped lang="scss">
// Chat 页面容器
.chat-page {
  display: flex;
  height: calc(100vh - 60px);
  background: var(--pc-bg-deep);
  margin: -24px;
  position: relative;
}

// 左侧会话列表
.chat-sidebar {
  width: 260px;
  background: var(--pc-bg-base);
  border-right: 1px solid var(--pc-border);
  display: flex;
  flex-direction: column;
  position: relative;
  transition: all 0.3s ease;

  .sidebar-actions {
    padding: 12px;
    display: flex;
    align-items: center;
    gap: 8px;

    .sidebar-toggle {
      margin-left: auto;
    }

    &.collapsed {
      flex-direction: column;
      justify-content: center;
      gap: 8px;

      .sidebar-toggle {
        margin-left: 0;
      }
    }
  }

  &.collapsed {
    width: 64px;
  }

  .conversation-list {
    flex: 1;
    overflow-y: auto;

    .conversation-item {
      display: flex;
      align-items: center;
      padding: 12px 16px;
      cursor: pointer;
      border-bottom: 1px solid var(--pc-border);
      gap: 12px;
      transition: background 0.2s;

      &:hover {
        background: rgba(var(--pc-primary-rgb), 0.04);
      }

      &.active {
        background: rgba(var(--pc-primary-rgb), 0.08);
      }

      &.collapsed {
        justify-content: center;
        padding: 12px 8px;
      }

      .conv-avatar {
        width: 40px;
        height: 40px;
        min-width: 40px;
        border-radius: 6px;
        background: var(--pc-primary);
        color: #fff;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 18px;
      }

      .conv-info {
        flex: 1;
        overflow: hidden;

        .conv-title-row {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 4px;
        }

        .conv-title {
          font-size: 14px;
          font-weight: 500;
          color: var(--pc-text-primary);
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .conv-time {
          font-size: 11px;
          color: var(--pc-text-muted);
          flex-shrink: 0;
        }

        .conv-preview {
          font-size: 12px;
          color: var(--pc-text-muted);
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
      }
    }
  }
}

// 右侧对话区域
.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--pc-bg-surface);

  .chat-header {
    padding: 12px 20px;
    background: var(--pc-bg-elevated);
    border-bottom: 1px solid var(--pc-border);
    display: flex;
    justify-content: space-between;
    align-items: center;

    .header-left {
      .chat-title {
        font-size: 15px;
        font-weight: 500;
        color: var(--pc-text-primary);
      }
    }

    .header-right {
      display: flex;
      align-items: center;
      gap: 8px;
    }
  }

  // 消息区域
  .chat-messages {
    flex: 1;
    overflow-y: auto;
    padding: 16px 24px;
    background: var(--pc-bg-deep);

    .empty-chat {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      height: 100%;
      color: var(--pc-text-muted);

      .empty-icon {
        width: 80px;
        height: 80px;
        margin-bottom: 24px;
        color: var(--pc-primary);
        opacity: 0.6;

        svg {
          width: 100%;
          height: 100%;
        }
      }

      h3 {
        margin: 0 0 8px;
        color: var(--pc-text-primary);
        font-size: 18px;
        font-weight: 600;
      }

      p {
        margin: 0 0 24px;
        color: var(--pc-text-muted);
        font-size: 14px;
      }

      .quick-actions {
        display: flex;
        flex-direction: column;
        gap: 10px;
        width: 100%;
        max-width: 360px;

        .quick-action-btn {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 12px 16px;
          background: var(--pc-bg-elevated);
          border: 1px solid var(--pc-border);
          border-radius: var(--pc-radius-md);
          color: var(--pc-text-secondary);
          font-size: 13px;
          cursor: pointer;
          transition: all 0.2s ease;
          text-align: left;

          .el-icon {
            font-size: 16px;
            color: var(--pc-primary);
          }

          &:hover {
            border-color: var(--pc-primary);
            background: rgba(var(--pc-primary-rgb), 0.04);
            color: var(--pc-text-primary);
            transform: translateX(4px);
          }
        }
      }
    }

    .messages-wrapper {
      max-width: 100%;
      margin: 0 auto;
    }

    // 时间分隔线
    .time-divider {
      text-align: center;
      padding: 8px 0 16px;
      font-size: 12px;
      color: var(--pc-text-muted);
    }

    // 消息行
    .message-row {
      margin-bottom: 16px;

      &.user {
        .message-bubble-wrapper {
          justify-content: flex-end;
        }
      }
    }

    // 消息气泡容器
    .message-bubble-wrapper {
      display: flex;
      align-items: flex-start;
      gap: 10px;

      .message-body {
        display: flex;
        flex-direction: column;
        gap: 4px;
        max-width: 85%;
        min-width: 0;
      }

      .msg-avatar {
        flex-shrink: 0;

        &.ai-avatar {
          background: var(--pc-bg-surface);
          color: var(--pc-primary);
        }
        &.user-avatar {
          background: var(--pc-primary);
          color: #fff;
        }
      }

      // AI 消息气泡
      .message-bubble.assistant {
        background: var(--pc-bg-elevated);
        padding: 12px 16px;
        border-radius: 12px;
        max-width: 100%;
        width: fit-content;
        position: relative;
        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
        border: 1px solid var(--pc-border);

        .message-content {
          font-size: 14px;
          line-height: 1.7;
          color: var(--pc-text-primary);
          word-break: break-word;

          :deep(pre) {
            background: var(--pc-bg-deep);
            color: var(--pc-text-primary);
            padding: 12px;
            border-radius: 6px;
            overflow-x: auto;
            margin: 10px 0;
            font-size: 13px;
            border: 1px solid var(--pc-border);
          }

          :deep(ul), :deep(ol) {
            padding-left: 20px;
            margin: 8px 0;
          }
          :deep(li) {
            margin: 3px 0;
            &::marker { color: var(--pc-text-muted); }
          }

          :deep(code) {
            background: rgba(var(--pc-primary-rgb), 0.1);
            color: var(--pc-primary);
            padding: 2px 6px;
            border-radius: 4px;
            font-family: Consolas, Monaco, monospace;
            font-size: 13px;
          }

          :deep(table.md-table) {
            width: 100%;
            border-collapse: collapse;
            margin: 10px 0;
            font-size: 13px;
            th {
              background: var(--pc-bg-deep);
              font-weight: 600;
              padding: 8px 12px;
              text-align: left;
              border: 1px solid var(--pc-border);
            }
            td {
              padding: 8px 12px;
              border: 1px solid var(--pc-border);
            }
          }

          :deep(ul), :deep(ol) {
            margin: 6px 0;
            padding-left: 20px;
            li {
              margin: 2px 0;
            }
          }

          :deep(hr) {
            border: none;
            border-top: 1px solid var(--pc-border);
            margin: 12px 0;
          }

          :deep(h2), :deep(h3), :deep(h4), :deep(h5) {
            margin: 12px 0 6px 0;
            font-weight: 600;
            line-height: 1.4;
          }
          :deep(h2) { font-size: 18px; border-bottom: 1px solid var(--pc-border); padding-bottom: 4px; }
          :deep(h3) { font-size: 16px; }
          :deep(h4) { font-size: 14px; }
          :deep(h5) { font-size: 13px; }
        }

        .bubble-actions {
          position: absolute;
          right: -40px;
          top: 50%;
          transform: translateY(-50%);
          opacity: 0;
          transition: opacity 0.2s;
          display: flex;
          gap: 4px;
        }

        &:hover .bubble-actions {
          opacity: 1;
        }

        // 消息元数据
        .message-meta {
          margin-top: 8px;
          padding-top: 8px;
          border-top: 1px solid var(--pc-border);
          display: flex;
          gap: 12px;
          font-size: 11px;
          color: var(--pc-text-muted);

          .meta-item {
            display: flex;
            align-items: center;
            gap: 2px;
          }
        }

        &.loading {
          .typing-dots {
            display: flex;
            gap: 4px;
            padding: 4px 0;

            span {
              width: 6px;
              height: 6px;
              background: var(--pc-primary);
              border-radius: 50%;
              animation: typing 1.4s infinite ease-in-out;

              &:nth-child(1) { animation-delay: 0s; }
              &:nth-child(2) { animation-delay: 0.2s; }
              &:nth-child(3) { animation-delay: 0.4s; }
            }
          }
        }
      }

      // 用户消息气泡（蓝色）
      .message-bubble.user {
        background: var(--pc-primary);
        padding: 12px 16px;
        border-radius: 12px;
        max-width: 100%;
        width: fit-content;

        .message-content {
          font-size: 14px;
          line-height: 1.7;
          color: #fff;
          word-break: break-word;
        }

        // 时间戳
        .message-time {
          margin-top: 6px;
          font-size: 11px;
          color: rgba(255, 255, 255, 0.7);
          text-align: right;
        }
      }

      // 思考/推理内容（可折叠）
      .thinking-collapse {
        max-width: 100%;
        margin-bottom: 4px;

        .thinking-toggle {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          padding: 6px 12px;
          border-radius: 6px;
          background: var(--pc-bg-surface);
          border: 1px solid var(--pc-border);
          cursor: pointer;
          user-select: none;
          font-size: 12px;
          color: var(--pc-text-secondary);
          transition: background 0.15s;

          &:hover {
            background: rgba(var(--pc-primary-rgb), 0.06);
          }

          .thinking-dot {
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: var(--pc-primary);
            flex-shrink: 0;
          }

          .thinking-label {
            font-size: 12px;
            color: var(--pc-text-secondary);
          }

          .thinking-arrow {
            font-size: 12px;
            color: var(--pc-text-muted);
            transition: transform 0.2s;
            flex-shrink: 0;

            &.expanded {
              transform: rotate(90deg);
            }
          }
        }

        .thinking-content {
          font-size: 12px;
          color: var(--pc-text-secondary);
          padding: 8px 12px;
          background: var(--pc-bg-surface);
          border: 1px solid var(--pc-border);
          border-top: none;
          border-radius: 0 0 6px 6px;
          line-height: 1.5;
          max-height: 300px;
          overflow-y: auto;
          white-space: pre-wrap;
          word-break: break-word;
        }
      }

      // 工具调用气泡
      .tool-call-bubble {
        background: var(--pc-bg-elevated);
        padding: 10px 14px;
        border-radius: 8px;
        max-width: 100%;
        border: 1px solid var(--pc-border);

        .tool-call-item {
            padding: 8px 10px;
            margin-bottom: 4px;
            border-radius: 6px;
            transition: background 0.15s;

            &:last-child {
                margin-bottom: 0;
            }

            &:hover {
                background: rgba(var(--pc-primary-rgb), 0.04);
            }

            &.tool-status-error {
              border-left: 3px solid var(--pc-accent-red, #f56c6c);
              padding-left: 7px;
            }

            .tool-call-header {
                display: flex;
                align-items: center;
                gap: 8px;
                cursor: pointer;
                user-select: none;

                .expand-icon {
                    transition: transform 0.2s;
                    color: var(--pc-text-muted);
                    font-size: 12px;
                    flex-shrink: 0;

                    &.expanded {
                        transform: rotate(90deg);
                    }
                }

                .tool-icon {
                    font-size: 15px;
                    flex-shrink: 0;

                    &.tool-icon-file { color: #409eff; }
                    &.tool-icon-search { color: #e6a23c; }
                    &.tool-icon-terminal { color: #67c23a; }
                    &.tool-icon-setting { color: #909399; }
                    &.tool-icon-link { color: #00bcd4; }
                    &.tool-icon-code { color: #ab47bc; }
                    &.tool-icon-collection { color: #ff5722; }
                    &.tool-icon-default { color: var(--pc-text-muted); }
                }

                .tool-name {
                    font-size: 13px;
                    font-weight: 600;
                    color: var(--pc-text-primary);
                    flex-shrink: 0;
                }

                .tool-result-summary {
                    margin-left: 8px;
                    font-size: 12px;
                    color: var(--pc-text-muted);
                    overflow: hidden;
                    text-overflow: ellipsis;
                    white-space: nowrap;
                    font-family: 'JetBrains Mono', 'Fira Code', monospace;

                    &.is-error {
                      color: var(--pc-accent-red, #f56c6c);
                    }
                }

                .tool-duration {
                    margin-left: auto;
                    font-size: 11px;
                    color: var(--pc-text-muted);
                    flex-shrink: 0;
                }

                .tool-loading {
                    margin-left: 8px;
                    display: flex;
                    align-items: center;
                    gap: 6px;
                    font-size: 12px;
                    color: var(--pc-primary);

                    .loading-text {
                        font-size: 12px;
                    }

                    .loading-dots {
                        display: flex;
                        gap: 3px;

                        span {
                            width: 4px;
                            height: 4px;
                            background: var(--pc-primary);
                            border-radius: 50%;
                            animation: typing 1.4s infinite ease-in-out;

                            &:nth-child(1) { animation-delay: 0s; }
                            &:nth-child(2) { animation-delay: 0.2s; }
                            &:nth-child(3) { animation-delay: 0.4s; }
                        }
                    }
                }
            }

            .tool-result {
                font-size: 12px;
                color: var(--pc-text-secondary);
                padding: 10px 12px;
                background: var(--pc-bg-surface);
                border-radius: 4px;
                margin-top: 8px;
                line-height: 1.6;
                max-height: 300px;
                overflow-y: auto;
                border: 1px solid var(--pc-border);
                white-space: pre-wrap;
                word-break: break-word;
                font-family: 'JetBrains Mono', 'Fira Code', monospace;

                .tool-result-json {
                  margin: 0;
                  padding: 0;
                  background: transparent;
                  border: none;
                  code {
                    background: transparent;
                    color: var(--pc-text-secondary);
                    padding: 0;
                    font-family: 'JetBrains Mono', 'Fira Code', monospace;
                    font-size: 12px;
                    line-height: 1.6;
                  }
                }
            }
        }
      }
    }
  }

  @keyframes typing {
    0%, 60%, 100% { transform: translateY(0); }
    30% { transform: translateY(-4px); }
  }

  // 输入区域（类似 OpenClaw 风格）
  .chat-input-area {
    padding: 16px 24px;
    background: var(--pc-bg-elevated);
    border-top: 1px solid var(--pc-border);

    .context-usage-bar {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 8px;
      padding: 6px 10px;
      background: rgba(var(--pc-warning-rgb), 0.08);
      border-radius: 6px;
      font-size: 12px;

      .usage-progress {
        width: 80px;
        flex-shrink: 0;
      }

      .usage-label {
        flex: 1;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
    }

    .input-main {
      margin-bottom: 12px;

      :deep(.el-textarea__inner) {
        font-size: 14px !important;
        line-height: 1.6;
        border-radius: 8px;
        border: 1px solid var(--pc-border);
        background: var(--pc-bg-surface);
        padding: 12px;
        color: var(--pc-text-primary);

        &:focus {
          border-color: var(--pc-primary);
          background: var(--pc-bg-elevated);
        }
      }
    }

    .input-footer {
      display: flex;
      justify-content: space-between;
      align-items: center;

      .footer-left {
        display: flex;
        align-items: center;
        gap: 8px;

        .el-radio-group {
          :deep(.el-radio-button__inner) {
            padding: 6px 12px;
          }
        }
      }

      .footer-right {
        .el-button--primary {
          padding: 8px 20px;
        }
      }
    }
  }
}

// 工具执行状态
.tools-status {
  position: fixed;
  bottom: 140px;
  right: 30px;
  max-width: 320px;
  z-index: 1000;
  display: flex;
  flex-direction: column;
  gap: 8px;

  .tool-status-item {
    background: var(--pc-glass-bg);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border-radius: 8px;
    padding: 12px 14px;
    box-shadow: var(--pc-shadow-lg);
    border: 1px solid var(--pc-border);
    border-left: 3px solid var(--pc-primary);

    &.running { border-left-color: var(--pc-primary); }
    &.complete { border-left-color: var(--pc-accent-green); }
    &.error { border-left-color: var(--pc-accent-red); }

    .tool-header {
      display: flex;
      align-items: center;
      gap: 8px;

      .tool-icon { font-size: 14px; }
      .tool-name { font-weight: 500; font-size: 13px; color: var(--pc-text-primary); flex: 1; }
      .tool-duration { font-size: 11px; color: var(--pc-text-muted); }
    }

    .tool-progress {
      margin-top: 8px;
    }
  }
}

// 响应式
@media (max-width: 768px) {
  .chat-sidebar {
    width: 64px;

    .conv-info { display: none; }
  }

  .tools-status {
    right: 10px;
    max-width: 260px;
  }
}
</style>
