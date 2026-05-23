<template>
  <div class="tracing-page">
    <div class="pc-page-header">
      <h2 class="pc-page-title">{{ $t('tracing.title') }}</h2>
      <div class="header-actions">
        <button class="pc-glow-btn secondary" @click="loadTraces">
          <el-icon><Refresh /></el-icon>
          {{ $t('common.search') }}
        </button>
        <el-popconfirm
          v-if="isSuperAdmin"
          :title="$t('tracing.clearAllConfirm')"
          @confirm="clearAllTraces"
        >
          <template #reference>
            <button class="pc-glow-btn danger">
              <el-icon><Delete /></el-icon>
              {{ $t('tracing.clearAll') }}
            </button>
          </template>
        </el-popconfirm>
      </div>
    </div>

    <!-- Stats Row -->
    <div v-if="stats" class="tracing-stats">
      <el-card v-for="card in statCards" :key="card.key" class="stat-card">
        <div class="stat-icon" :class="card.colorClass">
          <el-icon><component :is="card.icon" /></el-icon>
        </div>
        <div class="stat-info">
          <div class="stat-value">{{ card.value }}</div>
          <div class="stat-label">{{ card.label }}</div>
        </div>
      </el-card>
    </div>

    <!-- Filter Bar -->
    <el-card class="filter-card">
      <div class="filter-inner">
        <div class="filter-left">
          <el-input
            v-model="filters.agent_id"
            :placeholder="$t('tracing.filterByAgent')"
            clearable
            @clear="loadTraces"
            @keyup.enter="loadTraces"
          >
            <template #prefix>
              <el-icon><Cpu /></el-icon>
            </template>
          </el-input>
          <el-input
            v-model="filters.session_id"
            :placeholder="$t('tracing.filterBySession')"
            clearable
            @clear="loadTraces"
            @keyup.enter="loadTraces"
          >
            <template #prefix>
              <el-icon><ChatDotRound /></el-icon>
            </template>
          </el-input>
        </div>
        <div class="filter-right">
          <button class="pc-glow-btn" @click="loadTraces">
            <el-icon><Search /></el-icon>
            {{ $t('common.search') }}
          </button>
          <button class="pc-glow-btn secondary" @click="resetFilters">
            <el-icon><Refresh /></el-icon>
            {{ $t('common.reset') }}
          </button>
        </div>
      </div>
    </el-card>

    <!-- Main Tabs -->
    <el-card class="tracing-card">
      <el-tabs v-model="activeTab" @tab-change="onTabChange">
        <!-- Tab 1: Trace List -->
        <el-tab-pane :label="$t('tracing.traceList')" name="list">
          <el-table
            :data="pagedTraces"
            v-loading="loading"
            style="width: 100%"
            class="pc-data-table"
            highlight-current-row
            @row-click="selectTrace"
            :row-class-name="traceRowClass"
          >
            <template #empty>
              <el-empty :description="$t('tracing.noTraces')" />
            </template>
            <el-table-column
              :label="$t('tracing.traceName')"
              prop="name"
              min-width="200"
              show-overflow-tooltip
            >
              <template #default="{ row }">
                <div class="trace-name-cell">
                  <span class="trace-kind-dot" :class="kindClass(row.root_span?.kind)"></span>
                  <span class="trace-name-text">{{ row.name }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column :label="$t('tracing.agent')" width="140">
              <template #default="{ row }">
                <el-tag size="small" type="info" effect="plain">{{ row.agent_name || '-' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="$t('tracing.session')" width="150">
              <template #default="{ row }">
                <span class="mono-text">{{ row.session_id ? row.session_id.slice(0, 12) + '...' : '-' }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="$t('tracing.spanCount')" width="100" align="center">
              <template #default="{ row }">
                <span class="metric-chip">{{ row.span_count }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="$t('tracing.tokens')" width="110" align="right">
              <template #default="{ row }">
                <span class="metric-value">{{ formatNumber(row.total_tokens) }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="$t('tracing.errors')" width="90" align="center">
              <template #default="{ row }">
                <span v-if="row.error_count > 0" class="error-badge">{{ row.error_count }}</span>
                <span v-else class="text-muted-sm">0</span>
              </template>
            </el-table-column>
            <el-table-column :label="$t('tracing.duration')" width="120" align="right">
              <template #default="{ row }">
                <span class="duration-text" :class="{ 'duration-slow': row.duration_ms > 30000 }">
                  {{ formatDuration(row.duration_ms) }}
                </span>
              </template>
            </el-table-column>
            <el-table-column :label="$t('tracing.startTime')" width="170">
              <template #default="{ row }">
                <span class="time-text">{{ formatTime(row.start_time) }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="$t('common.actions')" width="160" align="center">
              <template #default="{ row }">
                <div class="pc-action-group">
                  <el-button size="small" @click.stop="selectTrace(row)">
                    <el-icon><View /></el-icon>
                    {{ $t('tracing.viewDetail') }}
                  </el-button>
                  <el-popconfirm
                    :title="$t('tracing.deleteTraceConfirm')"
                    @confirm="deleteTrace(row.id)"
                  >
                    <template #reference>
                      <el-button size="small" type="danger" @click.stop>
                        <el-icon><Delete /></el-icon>
                        {{ $t('common.delete') }}
                      </el-button>
                    </template>
                  </el-popconfirm>
                </div>
              </template>
            </el-table-column>
          </el-table>

          <!-- Pagination -->
          <div v-if="traces.length > 0" class="pagination-wrapper">
            <el-pagination
              v-model:current-page="pagination.page"
              v-model:page-size="pagination.pageSize"
              :page-sizes="[20, 50, 100]"
              :total="traces.length"
              layout="total, sizes, prev, pager, next"
              background
              small
            />
          </div>
        </el-tab-pane>

        <!-- Tab 2: Trace Detail -->
        <el-tab-pane :label="$t('tracing.traceDetail')" name="detail" :disabled="!selectedTrace">
          <div v-if="!selectedTrace" class="pc-empty-state">
            <el-empty :description="$t('tracing.noTraceSelected')" />
          </div>
          <template v-else>
            <!-- Trace Info Card -->
            <div class="detail-header">
              <div class="detail-title-row">
                <h3 class="detail-title">{{ selectedTrace.name }}</h3>
                <button class="pc-glow-btn secondary" @click="activeTab = 'list'">
                  <el-icon><ArrowLeft /></el-icon>
                  {{ $t('common.back') }}
                </button>
              </div>
              <div class="detail-meta-chips">
                <div class="meta-chip">
                  <el-icon><Cpu /></el-icon>
                  <span>{{ selectedTrace.agent_name || '-' }}</span>
                </div>
                <div class="meta-chip">
                  <el-icon><ChatDotRound /></el-icon>
                  <span class="mono-text">{{ selectedTrace.session_id ? selectedTrace.session_id.slice(0, 12) + '...' : '-' }}</span>
                </div>
                <div class="meta-chip">
                  <el-icon><Timer /></el-icon>
                  <span>{{ formatDuration(selectedTrace.duration_ms) }}</span>
                </div>
                <div class="meta-chip">
                  <el-icon><Coin /></el-icon>
                  <span>{{ formatNumber(selectedTrace.total_tokens) }} tokens</span>
                </div>
                <div class="meta-chip">
                  <el-icon><List /></el-icon>
                  <span>{{ selectedTrace.span_count }} spans</span>
                </div>
                <div v-if="selectedTrace.error_count > 0" class="meta-chip error-chip">
                  <el-icon><WarningFilled /></el-icon>
                  <span>{{ selectedTrace.error_count }} errors</span>
                </div>
              </div>
              <div class="detail-time-row">
                <span>{{ formatTime(selectedTrace.start_time) }}</span>
                <span class="time-arrow">&rarr;</span>
                <span>{{ selectedTrace.end_time ? formatTime(selectedTrace.end_time) : '...' }}</span>
              </div>
            </div>

            <!-- Span Tree -->
            <h4 class="section-title">{{ $t('tracing.spanTree') }}</h4>
            <el-table
              :data="spanTreeData"
              v-loading="detailLoading"
              style="width: 100%"
              class="pc-data-table"
              row-key="id"
              :tree-props="{ children: 'children', hasChildren: 'hasChildren' }"
              default-expand-all
              :row-class-name="spanRowClass"
            >
              <template #empty>
                <el-empty :description="$t('tracing.noTraces')" />
              </template>
              <el-table-column :label="$t('tracing.spanName')" min-width="240" show-overflow-tooltip>
                <template #default="{ row }">
                  <div class="span-name-cell">
                    <span class="kind-dot" :class="kindClass(row.kind)"></span>
                    <span>{{ row.name }}</span>
                  </div>
                </template>
              </el-table-column>
              <el-table-column :label="$t('tracing.spanKind')" width="110">
                <template #default="{ row }">
                  <span class="kind-badge" :class="kindClass(row.kind)">
                    {{ $t(kindI18nKey(row.kind)) }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('tracing.spanStatus')" width="100">
                <template #default="{ row }">
                  <span class="status-badge" :class="'status-' + row.status">
                    <span class="status-dot"></span>
                    {{ $t(statusI18nKey(row.status)) }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('tracing.spanDuration')" width="110" align="right">
                <template #default="{ row }">
                  <span class="duration-text" :class="{ 'duration-slow': row.duration_ms > 10000 }">
                    {{ row.duration_ms }} ms
                  </span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('tracing.spanTokens')" width="170">
                <template #default="{ row }">
                  <span v-if="row.tokens" class="token-bars">
                    <span class="token-bar-item">
                      <span class="token-bar-label">P</span>
                      <span class="token-bar-track"><span class="token-bar-fill prompt" :style="tokenBarStyle(row.tokens.prompt, row.tokens.total)"></span></span>
                      <span class="token-bar-num">{{ formatNumber(row.tokens.prompt) }}</span>
                    </span>
                    <span class="token-bar-item">
                      <span class="token-bar-label">C</span>
                      <span class="token-bar-track"><span class="token-bar-fill completion" :style="tokenBarStyle(row.tokens.completion, row.tokens.total)"></span></span>
                      <span class="token-bar-num">{{ formatNumber(row.tokens.completion) }}</span>
                    </span>
                  </span>
                  <span v-else class="text-muted-sm">-</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('tracing.spanError')" min-width="200" show-overflow-tooltip>
                <template #default="{ row }">
                  <span v-if="row.error" class="error-text-inline">{{ row.error }}</span>
                  <span v-else class="text-muted-sm">-</span>
                </template>
              </el-table-column>
            </el-table>
          </template>
        </el-tab-pane>

        <!-- Tab 3: Timeline -->
        <el-tab-pane :label="$t('tracing.timeline')" name="timeline" :disabled="!selectedTrace">
          <div v-if="!timelineData" class="pc-empty-state">
            <el-empty :description="$t('tracing.noTraceSelected')" />
          </div>
          <template v-else>
            <div class="timeline-section">
              <div class="timeline-header">
                <div class="timeline-header-left">
                  <h4 class="section-title">{{ timelineData.trace_name }}</h4>
                  <span class="timeline-total-dur">
                    <el-icon><Timer /></el-icon>
                    {{ timelineData.total_duration_ms }} ms total
                  </span>
                </div>
                <div class="timeline-legend">
                  <span v-for="(_color, kind) in kindColors" :key="kind" class="legend-chip">
                    <span class="legend-dot" :class="kindClass(kind)"></span>
                    {{ $t(kindI18nKey(kind)) }}
                  </span>
                </div>
              </div>

              <div class="timeline-chart">
                <div
                  v-for="item in timelineData.items"
                  :key="item.id"
                  class="timeline-row"
                  :style="{ paddingLeft: item.depth * 16 + 'px' }"
                >
                  <div class="timeline-row-inner">
                    <div class="tl-label">
                      <span class="kind-dot" :class="kindClass(item.kind)"></span>
                      <span class="tl-name" :title="item.name">{{ item.name }}</span>
                      <span class="tl-dur">{{ item.duration_ms }}ms</span>
                    </div>
                    <div class="tl-bar-track">
                      <div
                        class="tl-bar"
                        :class="kindClass(item.kind)"
                        :style="barStyle(item)"
                      >
                        <el-tooltip
                          :content="`${item.name}\n${item.duration_ms}ms (offset: ${item.start_offset_ms}ms)`"
                          placement="top"
                          :show-after="200"
                        >
                          <span class="tl-bar-inner"></span>
                        </el-tooltip>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </template>
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/stores/user'
import { tracingApi, type TraceItem, type TraceStats, type TimelineResponse } from '@/api/tracing'
import { ElMessage } from 'element-plus'
import {
  List, Connection, Coin, WarningFilled, Timer,
  Search, Refresh, Delete, View, ArrowLeft, Cpu, ChatDotRound
} from '@element-plus/icons-vue'

const { t } = useI18n()
const userStore = useUserStore()

// ==================== State ====================
const loading = ref(false)
const detailLoading = ref(false)
const traces = ref<TraceItem[]>([])
const stats = ref<TraceStats | null>(null)
const activeTab = ref('list')
const selectedTrace = ref<TraceItem | null>(null)
const timelineData = ref<TimelineResponse | null>(null)

const filters = reactive({
  agent_id: '',
  session_id: '',
})

const pagination = reactive({
  page: 1,
  pageSize: 20,
})

const pagedTraces = computed(() => {
  const start = (pagination.page - 1) * pagination.pageSize
  return traces.value.slice(start, start + pagination.pageSize)
})

const isSuperAdmin = computed(() => userStore.user?.role === 'super_admin')

// ==================== Stat Cards ====================
const statCards = computed(() => {
  if (!stats.value) return []
  return [
    {
      key: 'total',
      icon: List,
      colorClass: 'total',
      value: stats.value.total_traces,
      label: t('tracing.totalTraces'),
    },
    {
      key: 'spans',
      icon: Connection,
      colorClass: 'configured',
      value: stats.value.total_spans,
      label: t('tracing.totalSpans'),
    },
    {
      key: 'tokens',
      icon: Coin,
      colorClass: 'active',
      value: formatNumber(stats.value.total_tokens),
      label: t('tracing.totalTokens'),
    },
    {
      key: 'errors',
      icon: WarningFilled,
      colorClass: stats.value.total_errors > 0 ? 'system' : 'active',
      value: stats.value.total_errors,
      label: t('tracing.totalErrors'),
    },
    {
      key: 'duration',
      icon: Timer,
      colorClass: 'configured',
      value: formatDuration(stats.value.avg_duration_ms),
      label: t('tracing.avgDuration'),
    },
  ]
})

// ==================== Span Tree ====================
const spanTreeData = computed(() => {
  if (!selectedTrace.value?.root_span) return []
  return [selectedTrace.value.root_span]
})

// ==================== Kind & Color Helpers ====================
const kindColors: Record<string, string> = {
  agent: '#409eff',
  llm: '#67c23a',
  tool: '#e6a23c',
  handoff: '#a855f7',
  guardrail: '#f56c6c',
  hook: '#0ea5a5',
  retrieval: '#d946ef',
  embedding: '#6366f1',
  trace: '#909399',
}

function kindClass(kind: string | undefined): string {
  if (!kind) return 'kind-trace'
  return 'kind-' + kind
}

function kindI18nKey(kind: string): string {
  const map: Record<string, string> = {
    trace: 'tracing.kindTrace',
    agent: 'tracing.kindAgent',
    llm: 'tracing.kindLlm',
    tool: 'tracing.kindTool',
    handoff: 'tracing.kindHandoff',
    guardrail: 'tracing.kindGuardrail',
    hook: 'tracing.kindHook',
    retrieval: 'tracing.kindRetrieval',
    embedding: 'tracing.kindEmbedding',
  }
  return map[kind] || 'tracing.kindTrace'
}

function statusI18nKey(status: string): string {
  const map: Record<string, string> = {
    running: 'tracing.statusRunning',
    success: 'tracing.statusSuccess',
    error: 'tracing.statusError',
    cancelled: 'tracing.statusCancelled',
  }
  return map[status] || 'tracing.statusRunning'
}

// ==================== Table Row Classes ====================
function traceRowClass({ row }: { row: TraceItem }): string {
  return row.error_count > 0 ? 'trace-row-error' : ''
}

function spanRowClass({ row }: { row: any }): string {
  if (row.status === 'error') return 'span-row-error'
  if (row.status === 'running') return 'span-row-running'
  return ''
}

// ==================== Formatting ====================
function formatNumber(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K'
  return n.toLocaleString()
}

function formatDuration(ms: number): string {
  if (ms >= 60_000) return (ms / 60_000).toFixed(1) + ' min'
  if (ms >= 1_000) return (ms / 1_000).toFixed(2) + ' s'
  return ms + ' ms'
}

function formatTime(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleString()
}

// ==================== Token Bar ====================
function tokenBarStyle(count: number, total: number) {
  const pct = total > 0 ? Math.max((count / total) * 100, 2) : 0
  return { width: pct + '%' }
}

// ==================== Timeline Bar ====================
function barStyle(item: { duration_ms: number; start_offset_ms: number }) {
  if (!timelineData.value || timelineData.value.total_duration_ms === 0) {
    return { width: '0%', marginLeft: '0%' }
  }
  const total = timelineData.value.total_duration_ms
  const width = Math.max((item.duration_ms / total) * 100, 0.3)
  const left = (item.start_offset_ms / total) * 100
  return {
    width: width + '%',
    marginLeft: left + '%',
  }
}

// ==================== Data Loading ====================
async function loadStats() {
  try {
    const res = await tracingApi.stats()
    stats.value = res.data
  } catch {
    // stats failure is non-blocking
  }
}

async function loadTraces() {
  loading.value = true
  try {
    const params: Record<string, string | number> = { limit: 200 }
    if (filters.agent_id) params.agent_id = filters.agent_id
    if (filters.session_id) params.session_id = filters.session_id
    const res = await tracingApi.list(params as any)
    traces.value = res.data.items || []
    pagination.page = 1
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || t('common.failed'))
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  filters.agent_id = ''
  filters.session_id = ''
  loadTraces()
}

// ==================== Trace Actions ====================
async function selectTrace(trace: TraceItem) {
  selectedTrace.value = trace
  activeTab.value = 'detail'

  detailLoading.value = true
  try {
    const [detailRes, timelineRes] = await Promise.all([
      tracingApi.get(trace.id),
      tracingApi.getTimeline(trace.id),
    ])
    selectedTrace.value = detailRes.data
    timelineData.value = timelineRes.data
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || t('common.failed'))
  } finally {
    detailLoading.value = false
  }
}

function onTabChange(name: string) {
  if (name === 'timeline' && selectedTrace.value && !timelineData.value) {
    loadTimeline(selectedTrace.value.id)
  }
}

async function loadTimeline(traceId: string) {
  try {
    const res = await tracingApi.getTimeline(traceId)
    timelineData.value = res.data
  } catch {
    // non-blocking
  }
}

async function deleteTrace(traceId: string) {
  try {
    await tracingApi.delete(traceId)
    ElMessage.success(t('common.success'))
    if (selectedTrace.value?.id === traceId) {
      selectedTrace.value = null
      timelineData.value = null
      activeTab.value = 'list'
    }
    await loadTraces()
    await loadStats()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || t('common.failed'))
  }
}

async function clearAllTraces() {
  try {
    await tracingApi.clearAll(100)
    ElMessage.success(t('common.success'))
    selectedTrace.value = null
    timelineData.value = null
    activeTab.value = 'list'
    await loadTraces()
    await loadStats()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || t('common.failed'))
  }
}

// ==================== Lifecycle ====================
onMounted(() => {
  loadTraces()
  loadStats()
})
</script>

<style scoped lang="scss">
@use "sass:list";

.tracing-page {
  padding: 0;
}

// ── Stat Cards ──
.tracing-stats {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 16px;
  margin-bottom: 24px;

  .stat-card {
    background: var(--pc-glass-bg);
    border: 1px solid var(--pc-glass-border);
    border-radius: var(--pc-radius-lg);
    backdrop-filter: var(--pc-glass-blur);
    transition: all 0.25s ease;

    &:hover {
      border-color: rgba(var(--pc-primary-rgb), 0.3);
      box-shadow: 0 0 20px rgba(var(--pc-primary-rgb), 0.1);
    }

    :deep(.el-card__body) {
      display: flex;
      align-items: center;
      gap: 16px;
      padding: 18px;
      background: transparent;
    }

    .stat-icon {
      width: 48px;
      height: 48px;
      border-radius: 12px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: #fff;
      font-size: 22px;

      &.total {
        background: var(--pc-primary);
        box-shadow: 0 0 14px rgba(var(--pc-primary-rgb), 0.35);
      }
      &.active {
        background: var(--pc-accent-green);
        box-shadow: 0 0 14px rgba(var(--pc-accent-green-rgb), 0.3);
      }
      &.configured {
        background: var(--pc-accent-purple);
        box-shadow: 0 0 14px rgba(var(--pc-accent-purple-rgb), 0.3);
      }
      &.system {
        background: var(--pc-accent-orange);
        box-shadow: 0 0 14px rgba(var(--pc-accent-orange-rgb), 0.3);
      }
    }

    .stat-info {
      .stat-value {
        font-size: 26px;
        font-weight: 700;
        color: var(--pc-text-primary);
        letter-spacing: 0.5px;
      }
      .stat-label {
        font-size: 12px;
        color: var(--pc-text-muted);
        text-transform: uppercase;
        letter-spacing: 1px;
        margin-top: 2px;
      }
    }
  }
}

// ── Filter Card ──
.filter-card {
  background: var(--pc-glass-bg) !important;
  border: 1px solid var(--pc-glass-border) !important;
  border-radius: var(--pc-radius-lg) !important;
  backdrop-filter: var(--pc-glass-blur);
  margin-bottom: 16px;

  :deep(.el-card__body) {
    background: transparent;
    padding: 16px 20px;
  }
}

.filter-inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.filter-left {
  display: flex;
  gap: 10px;
  flex: 1;
  min-width: 0;

  .el-input {
    max-width: 260px;
  }
}

.filter-right {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-shrink: 0;
}

// ── Main Tracing Card ──
.tracing-card {
  background: var(--pc-glass-bg) !important;
  border: 1px solid var(--pc-glass-border) !important;
  border-radius: var(--pc-radius-lg) !important;
  backdrop-filter: var(--pc-glass-blur);

  :deep(.el-card__body) {
    background: transparent;
  }

  :deep(.el-tabs__header) {
    margin-bottom: 16px;
    padding: 0 20px;
    padding-top: 8px;
  }

  :deep(.el-tabs__content) {
    padding: 0 20px 16px;
  }

  :deep(.el-tabs__item) {
    font-weight: 500;
    font-size: 14px;
  }
}

// ── Trace Table ──
:deep(.trace-row-error) {
  background: rgba(239, 68, 68, 0.03) !important;
}
:deep(.trace-row-error:hover > td) {
  background: rgba(239, 68, 68, 0.06) !important;
}

.trace-name-cell {
  display: flex;
  align-items: center;
  gap: 8px;

  .trace-name-text {
    font-weight: 600;
    color: var(--pc-text-primary);
  }
}

.trace-kind-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.metric-chip {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 600;
  color: var(--pc-text-secondary);
  background: rgba(var(--pc-primary-rgb), 0.06);
  min-width: 28px;
  text-align: center;
}

.metric-value {
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}

.error-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 700;
  color: var(--pc-accent-red);
  background: rgba(239, 68, 68, 0.1);
}

.duration-text.duration-slow {
  color: var(--pc-accent-orange);
  font-weight: 500;
}

.time-text {
  font-size: 12px;
  color: var(--pc-text-muted);
}

.text-muted-sm {
  color: var(--pc-text-muted);
  font-size: 12px;
}

.mono-text {
  font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
  font-size: 12px;
}

// ── Detail Header ──
.detail-header {
  padding: 20px;
  background: var(--pc-gradient-surface);
  border: 1px solid var(--pc-glass-border);
  border-radius: var(--pc-radius-lg);
  margin-bottom: 16px;
}

.detail-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.detail-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--pc-text-primary);
}

.detail-meta-chips {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.meta-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 12px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 500;
  color: var(--pc-text-secondary);
  background: var(--pc-glass-bg);
  border: 1px solid var(--pc-glass-border);

  .el-icon {
    font-size: 14px;
    color: var(--pc-text-muted);
  }

  &.error-chip {
    color: var(--pc-accent-red);
    border-color: rgba(239, 68, 68, 0.25);
    background: rgba(239, 68, 68, 0.06);

    .el-icon {
      color: var(--pc-accent-red);
    }
  }
}

.detail-time-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--pc-text-muted);
  font-variant-numeric: tabular-nums;

  .time-arrow {
    font-size: 14px;
  }
}

// ── Section Title ──
.section-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--pc-text-primary);
  margin: 0 0 12px;
}

// ── Span Name ──
.span-name-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

// ── Kind Dots ──
.kind-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

// ── Kind Tags (dots + badges use same color palette) ──
$kinds: (
  trace: (var(--el-color-info), var(--el-color-info)),
  agent: (#409eff, #409eff),
  llm: (#67c23a, #67c23a),
  tool: (#e6a23c, #e6a23c),
  handoff: (#a855f7, #a855f7),
  guardrail: (#f56c6c, #f56c6c),
  hook: (#0ea5a5, #0ea5a5),
  retrieval: (#d946ef, #d946ef),
  embedding: (#6366f1, #6366f1),
);

@each $kind, $color in $kinds {
  .trace-kind-dot.kind-#{$kind},
  .kind-dot.kind-#{$kind} {
    background: list.nth($color, 1);
  }

  .kind-badge.kind-#{$kind} {
    color: nth($color, 1);
    border-color: currentColor;
  }
}

.kind-badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.3px;
  text-transform: uppercase;
  background: none !important;
  border: 1px solid currentColor;
}

// ── Status Badges ──
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  font-weight: 500;

  .status-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  &.status-success {
    color: var(--pc-accent-green);
    .status-dot {
      background: var(--pc-accent-green);
      box-shadow: 0 0 6px rgba(var(--pc-accent-green-rgb), 0.5);
    }
  }
  &.status-error {
    color: var(--pc-accent-red);
    .status-dot {
      background: var(--pc-accent-red);
      box-shadow: 0 0 6px rgba(239, 68, 68, 0.5);
    }
  }
  &.status-running {
    color: var(--pc-primary);
    .status-dot {
      background: var(--pc-primary);
      box-shadow: 0 0 6px rgba(var(--pc-primary-rgb), 0.5);
      animation: pc-pulse 1.5s ease infinite;
    }
  }
  &.status-cancelled {
    color: var(--pc-accent-orange);
    .status-dot {
      background: var(--pc-accent-orange);
      box-shadow: 0 0 6px rgba(var(--pc-accent-orange-rgb), 0.5);
    }
  }
}

.error-text-inline {
  color: var(--pc-accent-red);
  font-size: 12px;
  font-weight: 500;
}

// ── Token Bars ──
.token-bars {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.token-bar-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.token-bar-label {
  font-size: 10px;
  font-weight: 700;
  color: var(--pc-text-muted);
  width: 12px;
  text-align: center;
}

.token-bar-track {
  width: 40px;
  height: 4px;
  border-radius: 2px;
  background: rgba(var(--pc-primary-rgb), 0.08);
  overflow: hidden;
}

.token-bar-fill {
  height: 100%;
  border-radius: 2px;

  &.prompt { background: var(--pc-accent-purple); }
  &.completion { background: var(--pc-accent-green); }
}

.token-bar-num {
  font-size: 11px;
  color: var(--pc-text-secondary);
  font-variant-numeric: tabular-nums;
}

// ── Timeline ──
.timeline-section {
  padding: 4px 0;
}

.timeline-header {
  margin-bottom: 16px;
}

.timeline-header-left {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}

.timeline-total-dur {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--pc-text-muted);
}

.timeline-legend {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.legend-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: var(--pc-text-muted);
}

.legend-dot {
  width: 8px;
  height: 8px;
  border-radius: 2px;
  flex-shrink: 0;
}

.timeline-chart {
  overflow-x: auto;
  padding-bottom: 8px;
}

.timeline-row {
  margin-bottom: 2px;
}

.timeline-row-inner {
  display: flex;
  align-items: center;
  min-height: 30px;
}

.tl-label {
  width: 320px;
  min-width: 320px;
  display: flex;
  align-items: center;
  padding: 2px 6px;
  gap: 6px;
}

.tl-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--pc-text-primary);
}

.tl-dur {
  font-size: 11px;
  color: var(--pc-text-muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.tl-bar-track {
  flex: 1;
  height: 22px;
  position: relative;
  background: rgba(255, 255, 255, 0.02);
  border-radius: 4px;
  overflow: hidden;
}

.tl-bar {
  height: 100%;
  border-radius: 4px;
  min-width: 3px;
  transition: opacity 0.15s;
  cursor: pointer;
  display: flex;
  align-items: center;

  &:hover { opacity: 0.75; }

  &.kind-agent { background: linear-gradient(90deg, #2563eb, #409eff); }
  &.kind-llm { background: linear-gradient(90deg, #059669, #67c23a); }
  &.kind-tool { background: linear-gradient(90deg, #d97706, #e6a23c); }
  &.kind-handoff { background: linear-gradient(90deg, #7c3aed, #a855f7); }
  &.kind-guardrail { background: linear-gradient(90deg, #dc2626, #f56c6c); }
  &.kind-hook { background: linear-gradient(90deg, #0891b2, #0ea5a5); }
  &.kind-retrieval { background: linear-gradient(90deg, #c026d3, #d946ef); }
  &.kind-embedding { background: linear-gradient(90deg, #4f46e5, #6366f1); }
  &.kind-trace { background: linear-gradient(90deg, #6b7280, #909399); }
}

.tl-bar-inner {
  display: block;
  width: 100%;
  height: 100%;
}

// ── Pagination ──
.pagination-wrapper {
  display: flex;
  justify-content: center;
  margin-top: 16px;
  padding: 8px 0;
}

// ── Secondary glow button ──
:deep(.pc-glow-btn.secondary) {
  background: transparent !important;
  color: var(--pc-text-secondary) !important;
  border: 1px solid var(--pc-border) !important;

  &:hover {
    border-color: var(--pc-primary) !important;
    color: var(--pc-primary) !important;
    box-shadow: 0 0 10px rgba(var(--pc-primary-rgb), 0.15);
  }
}

:deep(.pc-glow-btn.danger) {
  background: transparent !important;
  color: var(--pc-accent-red) !important;
  border: 1px solid rgba(239, 68, 68, 0.3) !important;

  &:hover {
    border-color: var(--pc-accent-red) !important;
    box-shadow: 0 0 10px rgba(239, 68, 68, 0.2);
  }
}

// ── Span row overrides ──
:deep(.span-row-error) {
  background: rgba(239, 68, 68, 0.02) !important;
}
:deep(.span-row-error:hover > td) {
  background: rgba(239, 68, 68, 0.05) !important;
}
:deep(.span-row-running) {
  background: rgba(var(--pc-primary-rgb), 0.02) !important;
}

// ── Responsive ──
@media (max-width: 1200px) {
  .tracing-stats {
    grid-template-columns: repeat(3, 1fr);
  }
}

@media (max-width: 768px) {
  .tracing-stats {
    grid-template-columns: repeat(2, 1fr);
  }

  .filter-inner {
    flex-direction: column;
    align-items: stretch;
  }

  .filter-left {
    flex-direction: column;

    .el-input {
      max-width: none;
    }
  }

  .filter-right {
    justify-content: flex-start;
  }

  .tl-label {
    width: 200px;
    min-width: 200px;
  }
}

@media (max-width: 480px) {
  .tracing-stats {
    grid-template-columns: 1fr;
  }
}
</style>
