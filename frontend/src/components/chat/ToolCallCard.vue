<template>
  <div
    class="tool-call-card"
    :class="[`status-${normalizedStatus}`, { collapsed: isCollapsed }]"
  >
    <div class="tool-header" @click="toggleCollapse">
      <div class="tool-header-main">
        <div class="status-dot" :class="`dot-${normalizedStatus}`">
          <el-icon v-if="normalizedStatus === 'running'" class="spin"><Loading /></el-icon>
        </div>

        <div class="tool-icon-shell">
          <el-icon :size="15" class="tool-type-icon">
            <component :is="getToolIcon(toolName)" />
          </el-icon>
        </div>

        <div class="tool-summary">
          <div class="tool-name-row">
            <span class="tool-name">{{ formatToolName(toolName) }}</span>
            <span v-if="argumentPreview" class="tool-preview">{{ argumentPreview }}</span>
          </div>
        </div>
      </div>

      <div class="tool-header-meta">
        <span class="tool-status-chip" :class="`chip-${normalizedStatus}`">{{ statusLabel }}</span>
        <span v-if="effectiveDuration != null" class="tool-duration">{{ formatDuration(effectiveDuration) }}</span>
        <el-icon :size="14" class="chevron-icon" :class="{ expanded: !isCollapsed }">
          <ArrowRight />
        </el-icon>
      </div>
    </div>

    <!-- 进度条 -->
    <div v-if="showProgressStrip" class="tool-progress-strip">
      <div class="tool-progress-bar">
        <div
          class="tool-progress-fill"
          :class="{ indeterminate: isProgressIndeterminate }"
          :style="progressFillStyle"
        />
      </div>
      <div v-if="progressMessage" class="tool-progress-message">{{ progressMessage }}</div>
    </div>

    <!-- 展开内容 -->
    <transition name="slide">
      <div v-if="!isCollapsed" class="tool-body">
        <div v-if="hasArguments" class="tool-section">
          <div class="section-label">参数</div>
          <div class="tool-arguments">
            <div
              v-for="(value, key) in effectiveArguments"
              :key="key"
              class="arg-item"
            >
              <span class="arg-key">{{ key }}</span>
              <span class="arg-value">{{ formatArgValue(value) }}</span>
            </div>
          </div>
        </div>

        <div v-if="result != null" class="tool-section">
          <div class="section-label">结果</div>
          <pre class="tool-result">{{ result }}</pre>
        </div>

        <div v-if="error != null" class="tool-section">
          <div class="section-label">错误</div>
          <pre class="tool-error">{{ error }}</pre>
        </div>
      </div>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  ArrowRight,
  Loading,
  FolderOpened,
  Search,
  Promotion,
  Setting,
  Link,
  Document,
  Tools,
} from '@element-plus/icons-vue'
import type { ToolCallStatus } from '@/types/chat'

interface Props {
  toolId?: string | null
  toolName: string
  arguments?: Record<string, any>
  status?: ToolCallStatus
  result?: string | null
  error?: string | null
  duration?: number | null
  progress?: number | null
  progressMessage?: string | null
}

const props = withDefaults(defineProps<Props>(), {
  status: 'pending',
  result: null,
  error: null,
})

const isCollapsed = ref(true)

function toggleCollapse() {
  isCollapsed.value = !isCollapsed.value
}

/* ─── 状态 ─── */
const normalizedStatus = computed(() => props.status)

const statusLabel = computed(() => {
  const map: Record<ToolCallStatus, string> = {
    pending: '等待中',
    running: '执行中',
    success: '已完成',
    error: '失败',
    cancelled: '已取消',
  }
  return map[props.status] || props.status
})

/* ─── 进度 ─── */
const showProgressStrip = computed(() => {
  return (
    props.status === 'running' &&
    (props.progress != null || Boolean(props.progressMessage))
  )
})

const isProgressIndeterminate = computed(() => props.progress == null)

const progressFillStyle = computed(() => {
  if (props.progress == null) return {}
  return { width: `${Math.max(4, Math.min(100, props.progress))}%` }
})

/* ─── 参数 ─── */
const hasArguments = computed(() => {
  return props.arguments != null && Object.keys(props.arguments).length > 0
})

const effectiveArguments = computed(() => props.arguments || {})

const argumentPreview = computed(() => {
  const args = props.arguments
  if (!args) return ''
  const keys = Object.keys(args)
  if (keys.length === 0) return ''
  return `${keys[0]}=${formatArgValue(args[keys[0]], 20)}`
})

function formatArgValue(value: any, maxLen = 100): string {
  if (value == null) return 'null'
  if (typeof value === 'string') {
    return value.length > maxLen ? value.slice(0, maxLen) + '…' : value
  }
  if (typeof value === 'object') {
    const str = JSON.stringify(value)
    return str.length > maxLen ? str.slice(0, maxLen) + '…' : str
  }
  return String(value)
}

/* ─── 耗时 ─── */
const effectiveDuration = computed(() => props.duration)

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

/* ─── 图标 ─── */
function getToolIcon(name: string) {
  const lower = name.toLowerCase()
  if (lower.includes('read') || lower.includes('write') || lower.includes('list') || lower.includes('file')) {
    return FolderOpened
  }
  if (lower.includes('search') || lower.includes('grep') || lower.includes('find')) {
    return Search
  }
  if (lower.includes('exec') || lower.includes('bash') || lower.includes('run') || lower.includes('shell')) {
    return Promotion
  }
  if (lower.includes('config') || lower.includes('setting')) {
    return Setting
  }
  if (lower.includes('browser') || lower.includes('fetch') || lower.includes('web')) {
    return Link
  }
  if (lower.includes('code') || lower.includes('review') || lower.includes('analyze')) {
    return Document
  }
  return Tools
}

function formatToolName(name: string): string {
  const map: Record<string, string> = {
    read_file: '读取文件',
    write_file: '写入文件',
    list_dir: '列出目录',
    file_search: '文件搜索',
    grep: '文本搜索',
    web_search: '网页搜索',
    exec: '执行命令',
    bash: 'Bash 命令',
    browser: '浏览器',
    fetch_url: '获取 URL',
    skill: '技能',
    code_review: '代码审查',
  }
  return map[name] || name
}
</script>

<style scoped lang="scss">
.tool-call-card {
  background: var(--pc-bg-elevated);
  border-radius: 8px;
  border: 1px solid var(--pc-border);
  overflow: hidden;
  transition: border-color 0.2s;

  &.status-running {
    border-color: var(--el-color-primary);
  }
  &.status-success {
    border-color: var(--el-color-success);
  }
  &.status-error {
    border-color: var(--el-color-danger);
  }
  &.status-cancelled {
    border-color: var(--el-color-warning);
  }
}

.tool-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  cursor: pointer;
  user-select: none;

  &:hover {
    background: rgba(var(--pc-primary-rgb), 0.04);
  }
}

.tool-header-main {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;

  &.dot-pending {
    background: var(--el-text-color-placeholder);
  }
  &.dot-running {
    background: var(--el-color-primary);
    .spin {
      animation: rotate 1s linear infinite;
    }
  }
  &.dot-success {
    background: var(--el-color-success);
  }
  &.dot-error {
    background: var(--el-color-danger);
  }
  &.dot-cancelled {
    background: var(--el-color-warning);
  }
}

@keyframes rotate {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.tool-icon-shell {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  background: rgba(var(--pc-primary-rgb), 0.08);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.tool-summary {
  flex: 1;
  min-width: 0;
}

.tool-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tool-name {
  font-weight: 500;
  font-size: 14px;
  color: var(--pc-text);
}

.tool-preview {
  font-size: 12px;
  color: var(--pc-text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tool-header-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.tool-status-chip {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 4px;
  font-weight: 500;

  &.chip-pending {
    background: var(--el-fill-color);
    color: var(--el-text-color-placeholder);
  }
  &.chip-running {
    background: var(--el-color-primary-light-9);
    color: var(--el-color-primary);
  }
  &.chip-success {
    background: var(--el-color-success-light-9);
    color: var(--el-color-success);
  }
  &.chip-error {
    background: var(--el-color-danger-light-9);
    color: var(--el-color-danger);
  }
  &.chip-cancelled {
    background: var(--el-color-warning-light-9);
    color: var(--el-color-warning);
  }
}

.tool-duration {
  font-size: 11px;
  color: var(--pc-text-muted);
}

.chevron-icon {
  color: var(--pc-text-muted);
  transition: transform 0.2s;

  &.expanded {
    transform: rotate(90deg);
  }
}

/* ─── 进度条 ─── */
.tool-progress-strip {
  padding: 0 14px 10px;
}

.tool-progress-bar {
  height: 4px;
  background: var(--el-fill-color);
  border-radius: 2px;
  overflow: hidden;
}

.tool-progress-fill {
  height: 100%;
  background: var(--el-color-primary);
  border-radius: 2px;
  transition: width 0.3s ease;

  &.indeterminate {
    width: 30%;
    animation: indeterminate-slide 1.4s ease-in-out infinite;
  }
}

@keyframes indeterminate-slide {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(350%); }
}

.tool-progress-message {
  margin-top: 6px;
  font-size: 12px;
  color: var(--pc-text-muted);
}

/* ─── body ─── */
.tool-body {
  padding: 0 14px 14px;
}

.tool-section {
  margin-top: 12px;

  &:first-child {
    margin-top: 0;
  }
}

.section-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--pc-text-muted);
  margin-bottom: 6px;
}

.tool-arguments {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.arg-item {
  display: flex;
  gap: 8px;
  font-size: 13px;
}

.arg-key {
  color: var(--el-color-primary);
  font-weight: 500;
  flex-shrink: 0;
}

.arg-value {
  color: var(--pc-text);
  word-break: break-word;
}

.tool-result,
.tool-error {
  margin: 0;
  padding: 10px;
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 340px;
  overflow-y: auto;
}

.tool-result {
  background: var(--el-fill-color);
  color: var(--pc-text);
}

.tool-error {
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
}

/* ─── transition ─── */
.slide-enter-active,
.slide-leave-active {
  transition: all 0.2s ease;
}

.slide-enter-from,
.slide-leave-to {
  opacity: 0;
  max-height: 0;
  overflow: hidden;
}
</style>
