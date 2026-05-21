<template>
  <div ref="listRef" class="message-list" @scroll="handleScroll">
    <div
      v-for="(msg, index) in messages"
      :key="msg.id || `msg-${index}`"
      class="message-row"
      :class="msg.role"
    >
      <!-- 时间分隔线 -->
      <div v-if="shouldShowTime(index)" class="time-divider">
        {{ formatFullTime(msg.timestamp) }}
      </div>

      <!-- AI 消息 -->
      <template v-if="msg.role === 'assistant'">
        <el-avatar :size="36" class="msg-avatar ai-avatar">
          <el-icon><Cpu /></el-icon>
        </el-avatar>
        <div class="message-body">
          <!-- 思考内容 -->
          <div
            v-if="msg.reasoningContent && showThinking"
            class="thinking-collapse"
          >
            <details class="thinking-details" :open="msg.isStreaming">
              <summary class="thinking-summary">
                <span class="thinking-icon">💭</span>
                <span>思考过程</span>
                <span class="thinking-chars">({{ msg.reasoningContent.length }} 字)</span>
              </summary>
              <div class="thinking-content">{{ msg.reasoningContent }}</div>
            </details>
          </div>

          <!-- 工具调用 -->
          <ToolCallsList
            v-if="msg.toolCalls && msg.toolCalls.length > 0"
            :tool-calls="msg.toolCalls"
          />

          <!-- 消息内容 -->
          <div
            v-if="!msg.isStreaming || msg.content"
            class="message-bubble assistant"
          >
            <MessageContent :content="msg.content" />
            <!-- 元数据 -->
            <div
              v-if="msg.latency || msg.input_tokens || msg.output_tokens"
              class="message-meta"
            >
              <span v-if="msg.latency" class="meta-item">⏱ {{ msg.latency }}ms</span>
              <span v-if="msg.input_tokens" class="meta-item">↑ {{ msg.input_tokens }}</span>
              <span v-if="msg.output_tokens" class="meta-item">↓ {{ msg.output_tokens }}</span>
            </div>
            <!-- 操作按钮 -->
            <div v-if="!msg.isStreaming" class="bubble-actions">
              <el-button size="small" text @click="$emit('copy', msg.content)">
                <el-icon><CopyDocument /></el-icon>
              </el-button>
              <el-button size="small" text @click="$emit('regenerate', index)">
                <el-icon><Refresh /></el-icon>
              </el-button>
            </div>
          </div>

          <!-- 流式 loading -->
          <div v-else-if="msg.isStreaming" class="message-bubble assistant loading">
            <div class="typing-dots">
              <span /><span /><span />
            </div>
          </div>
        </div>
      </template>

      <!-- 用户消息 -->
      <template v-else>
        <div class="message-bubble user">
          <div class="message-content-text">{{ msg.content }}</div>
          <div v-if="msg.timestamp" class="message-time">
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

<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import { Cpu, User, CopyDocument, Refresh } from '@element-plus/icons-vue'
import type { ChatMessage } from '@/types/chat'
import ToolCallsList from './ToolCallsList.vue'
import MessageContent from './MessageContent.vue'

interface Props {
  messages: ChatMessage[]
  autoScroll?: boolean
  showThinking?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  autoScroll: true,
  showThinking: true,
})

defineEmits<{
  copy: [content: string]
  regenerate: [index: number]
}>()

const listRef = ref<HTMLElement>()
const isUserScrolling = ref(false)
let scrollTimeout: ReturnType<typeof setTimeout> | null = null

/* ─── 自动滚动 ─── */
watch(
  () => props.messages.length,
  () => {
    if (props.autoScroll && !isUserScrolling.value) {
      scrollToBottom()
    }
  }
)

watch(
  () => props.messages[props.messages.length - 1]?.content,
  () => {
    if (props.autoScroll && !isUserScrolling.value) {
      scrollToBottom()
    }
  }
)

function scrollToBottom() {
  nextTick(() => {
    if (listRef.value) {
      listRef.value.scrollTop = listRef.value.scrollHeight
    }
  })
}

/* ─── 滚动检测 ─── */
function handleScroll() {
  if (!listRef.value) return
  const { scrollTop, scrollHeight, clientHeight } = listRef.value
  const isAtBottom = scrollHeight - scrollTop - clientHeight < 50
  isUserScrolling.value = !isAtBottom

  if (scrollTimeout) clearTimeout(scrollTimeout)
  scrollTimeout = setTimeout(() => {
    if (isAtBottom) isUserScrolling.value = false
  }, 1000)
}

/* ─── 时间格式化 ─── */
function formatTime(date?: Date | string): string {
  if (!date) return ''
  const d = new Date(date)
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function formatFullTime(date?: Date | string): string {
  if (!date) return ''
  const d = new Date(date)
  return d.toLocaleString('zh-CN', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/* ─── 时间分隔线 ─── */
const TIME_GAP_MS = 5 * 60 * 1000 // 5 分钟

function shouldShowTime(index: number): boolean {
  if (index === 0) return true
  const prev = props.messages[index - 1]
  const curr = props.messages[index]
  if (!prev?.timestamp || !curr?.timestamp) return false
  const prevTime = new Date(prev.timestamp).getTime()
  const currTime = new Date(curr.timestamp).getTime()
  return currTime - prevTime > TIME_GAP_MS
}

/* ─── 暴露方法 ─── */
defineExpose({ scrollToBottom })
</script>

<style scoped lang="scss">
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.message-row {
  display: flex;
  gap: 12px;
  align-items: flex-start;

  &.user {
    flex-direction: row-reverse;
  }
}

.msg-avatar {
  flex-shrink: 0;
}

.message-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 85%;
}

/* ─── 思考内容 ─── */
.thinking-collapse {
  .thinking-details {
    background: var(--pc-bg-elevated);
    border-radius: 8px;
    border: 1px solid var(--pc-border);
    padding: 10px 14px;
  }

  .thinking-summary {
    display: flex;
    align-items: center;
    gap: 6px;
    cursor: pointer;
    font-size: 13px;
    color: var(--pc-text-muted);
    list-style: none;

    &::-webkit-details-marker {
      display: none;
    }
  }

  .thinking-icon {
    font-size: 14px;
  }

  .thinking-chars {
    font-size: 11px;
    opacity: 0.7;
  }

  .thinking-content {
    margin-top: 8px;
    padding-top: 8px;
    border-top: 1px solid var(--pc-border);
    font-size: 13px;
    line-height: 1.6;
    color: var(--pc-text-secondary);
    white-space: pre-wrap;
  }
}

/* ─── 消息气泡 ─── */
.message-bubble {
  border-radius: 12px;
  padding: 12px 16px;

  &.assistant {
    background: var(--pc-bg-elevated);
    border: 1px solid var(--pc-border);
  }

  &.user {
    background: var(--el-color-primary);
    color: #fff;
  }

  &.loading {
    min-height: 40px;
    display: flex;
    align-items: center;
    justify-content: center;
  }
}

.message-content-text {
  line-height: 1.6;
  word-break: break-word;
}

/* ─── 元数据 ─── */
.message-meta {
  display: flex;
  gap: 12px;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--pc-border);

  .meta-item {
    font-size: 11px;
    color: var(--pc-text-muted);
  }
}

/* ─── 操作按钮 ─── */
.bubble-actions {
  display: flex;
  gap: 4px;
  margin-top: 8px;
  opacity: 0;
  transition: opacity 0.2s;

  .message-bubble:hover & {
    opacity: 1;
  }
}

/* ─── loading 动画 ─── */
.typing-dots {
  display: flex;
  gap: 4px;

  span {
    width: 8px;
    height: 8px;
    background: var(--el-color-primary);
    border-radius: 50%;
    animation: typing-bounce 1.4s ease-in-out infinite both;

    &:nth-child(1) { animation-delay: -0.32s; }
    &:nth-child(2) { animation-delay: -0.16s; }
  }
}

@keyframes typing-bounce {
  0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
  40% { transform: scale(1); opacity: 1; }
}

/* ─── 时间分隔线 ─── */
.time-divider {
  text-align: center;
  font-size: 12px;
  color: var(--pc-text-muted);
  margin: 8px 0;
  position: relative;

  &::before,
  &::after {
    content: '';
    position: absolute;
    top: 50%;
    width: 30%;
    height: 1px;
    background: var(--pc-border);
  }

  &::before { left: 0; }
  &::after { right: 0; }
}

.message-time {
  font-size: 11px;
  opacity: 0.7;
  margin-top: 4px;
  text-align: right;
}
</style>
