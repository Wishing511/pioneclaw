<template>
  <div class="autodream-page">
    <div class="pc-page-header">
      <h2 class="pc-page-title">{{ $t('autoDream.title') }}</h2>
    </div>

    <el-tabs v-model="activeTab" type="border-card">
      <!-- Status Tab -->
      <el-tab-pane :label="$t('autoDream.status')" name="status">
        <div v-loading="loadingStatus">
          <!-- Top Action Bar -->
          <div class="status-actions">
            <el-button
              type="primary"
              :loading="triggering"
              :disabled="status?.is_running"
              @click="handleTrigger"
            >
              <el-icon><VideoPlay /></el-icon>
              {{ $t('autoDream.trigger') }}
            </el-button>
            <el-button @click="loadStatus">
              <el-icon><Refresh /></el-icon>
              {{ $t('common.refresh') }}
            </el-button>
          </div>

          <!-- Last Run Stats -->
          <template v-if="status?.last_run">
            <el-row :gutter="16" class="stat-row">
              <el-col :xs="12" :sm="8" :md="6" :lg="4">
                <el-card shadow="hover" class="stat-card">
                  <div class="stat-label">{{ $t('common.status') }}</div>
                  <el-tag
                    :type="statusTagType(status.last_run.status)"
                    size="large"
                    class="stat-value-tag"
                  >
                    {{ statusText(status.last_run.status) }}
                  </el-tag>
                </el-card>
              </el-col>
              <el-col :xs="12" :sm="8" :md="6" :lg="4">
                <el-card shadow="hover" class="stat-card">
                  <div class="stat-label">{{ $t('autoDream.totalMemories') }}</div>
                  <div class="stat-value">{{ status.last_run.total_memories }}</div>
                </el-card>
              </el-col>
              <el-col :xs="12" :sm="8" :md="6" :lg="4">
                <el-card shadow="hover" class="stat-card">
                  <div class="stat-label">{{ $t('autoDream.duration') }}</div>
                  <div class="stat-value">{{ status.last_run.duration_seconds.toFixed(1) }}{{ $t('autoDream.seconds') }}</div>
                </el-card>
              </el-col>
              <el-col :xs="12" :sm="8" :md="6" :lg="4">
                <el-card shadow="hover" class="stat-card">
                  <div class="stat-label">{{ $t('autoDream.duplicates') }}</div>
                  <div class="stat-value">{{ status.last_run.duplicates_found }}</div>
                </el-card>
              </el-col>
              <el-col :xs="12" :sm="8" :md="6" :lg="4">
                <el-card shadow="hover" class="stat-card">
                  <div class="stat-label">{{ $t('autoDream.merged') }}</div>
                  <div class="stat-value">{{ status.last_run.merged }}</div>
                </el-card>
              </el-col>
              <el-col :xs="12" :sm="8" :md="6" :lg="4">
                <el-card shadow="hover" class="stat-card">
                  <div class="stat-label">{{ $t('autoDream.consolidated') }}</div>
                  <div class="stat-value">{{ status.last_run.consolidated }}</div>
                </el-card>
              </el-col>
            </el-row>

            <el-row :gutter="16" class="stat-row">
              <el-col :xs="12" :sm="8" :md="6" :lg="4">
                <el-card shadow="hover" class="stat-card">
                  <div class="stat-label">{{ $t('autoDream.archived') }}</div>
                  <div class="stat-value">{{ status.last_run.archived }}</div>
                </el-card>
              </el-col>
              <el-col :xs="12" :sm="8" :md="6" :lg="4">
                <el-card shadow="hover" class="stat-card">
                  <div class="stat-label">{{ $t('autoDream.deleted') }}</div>
                  <div class="stat-value">{{ status.last_run.deleted }}</div>
                </el-card>
              </el-col>
              <el-col :xs="12" :sm="8" :md="6" :lg="4">
                <el-card shadow="hover" class="stat-card">
                  <div class="stat-label">{{ $t('autoDream.llmCalls') }}</div>
                  <div class="stat-value">{{ status.last_run.llm_calls }}</div>
                </el-card>
              </el-col>
              <el-col :xs="12" :sm="8" :md="6" :lg="4">
                <el-card shadow="hover" class="stat-card">
                  <div class="stat-label">{{ $t('autoDream.tokensIn') }}</div>
                  <div class="stat-value">{{ status.last_run.llm_tokens_in }}</div>
                </el-card>
              </el-col>
              <el-col :xs="12" :sm="8" :md="6" :lg="4">
                <el-card shadow="hover" class="stat-card">
                  <div class="stat-label">{{ $t('autoDream.tokensOut') }}</div>
                  <div class="stat-value">{{ status.last_run.llm_tokens_out }}</div>
                </el-card>
              </el-col>
              <el-col :xs="12" :sm="8" :md="6" :lg="4">
                <el-card shadow="hover" class="stat-card">
                  <div class="stat-label">{{ $t('autoDream.lastRun') }}</div>
                  <div class="stat-value-small">{{ formatDate(status.last_run.triggered_at) }}</div>
                </el-card>
              </el-col>
            </el-row>

            <el-card v-if="status.last_run.error_message" shadow="never" class="error-card">
              <template #header>
                <span class="error-header">
                  <el-icon><Warning /></el-icon>
                  {{ $t('autoDream.errorMessage') }}
                </span>
              </template>
              <pre class="error-message">{{ status.last_run.error_message }}</pre>
            </el-card>
          </template>

          <el-empty v-else :description="$t('autoDream.neverRun')" />
        </div>
      </el-tab-pane>

      <!-- Config Tab -->
      <el-tab-pane :label="$t('autoDream.config')" name="config">
        <el-form
          :model="configForm"
          label-width="180px"
          v-loading="loadingConfig"
          class="config-form"
        >
          <el-form-item :label="$t('common.status')">
            <el-switch
              v-model="configForm.enabled"
              :active-text="$t('common.active')"
              :inactive-text="$t('common.inactive')"
            />
          </el-form-item>

          <el-form-item :label="$t('autoDream.cronExpression')">
            <el-input v-model="configForm.cron_expression" style="width: 240px" />
          </el-form-item>

          <el-form-item :label="$t('autoDream.batchSize')">
            <el-input-number
              v-model="configForm.batch_size"
              :min="1"
              :max="500"
              style="width: 180px"
            />
          </el-form-item>

          <el-form-item :label="$t('autoDream.maxConsolidated')">
            <el-input-number
              v-model="configForm.max_consolidated_per_run"
              :min="0"
              :max="100"
              style="width: 180px"
            />
          </el-form-item>

          <el-form-item :label="$t('autoDream.archiveAfterDays')">
            <el-input-number
              v-model="configForm.archive_after_days"
              :min="1"
              style="width: 180px"
            />
          </el-form-item>

          <el-form-item :label="$t('autoDream.deleteAfterDays')">
            <el-input-number
              v-model="configForm.delete_after_days"
              :min="0"
              :placeholder="$t('common.disabled')"
              style="width: 180px"
            />
            <span class="form-hint">{{ $t('common.disabled') }} = 0</span>
          </el-form-item>

          <el-form-item :label="$t('autoDream.enableDedup')">
            <el-switch v-model="configForm.enable_dedup" />
          </el-form-item>

          <el-form-item :label="$t('autoDream.enableConsolidation')">
            <el-switch v-model="configForm.enable_consolidation" />
          </el-form-item>

          <el-form-item :label="$t('autoDream.enableArchival')">
            <el-switch v-model="configForm.enable_archival" />
          </el-form-item>

          <el-form-item>
            <el-button type="primary" :loading="savingConfig" @click="saveConfig">
              {{ $t('common.save') }}
            </el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- Logs Tab -->
      <el-tab-pane :label="$t('autoDream.logs')" name="logs">
        <div v-loading="loadingLogs">
          <div class="table-scroll">
            <el-table
              :data="logs.items"
              stripe
              v-if="logs.items.length > 0"
              @row-click="showLogDetail"
              class="clickable-table"
            >
              <el-table-column prop="id" :label="'ID'" width="60">
                <template #default="{ row }">
                  <el-tag type="info" size="small">#{{ row.id }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column :label="$t('common.status')" width="100">
                <template #default="{ row }">
                  <el-tag :type="statusTagType(row.status)" size="small">
                    {{ statusText(row.status) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="total_memories" :label="$t('autoDream.totalMemories')" width="110" />
              <el-table-column prop="duplicates_found" :label="$t('autoDream.duplicates')" width="90" />
              <el-table-column prop="merged" :label="$t('autoDream.merged')" width="80" />
              <el-table-column prop="consolidated" :label="$t('autoDream.consolidated')" width="80" />
              <el-table-column prop="archived" :label="$t('autoDream.archived')" width="80" />
              <el-table-column prop="deleted" :label="$t('autoDream.deleted')" width="80" />
              <el-table-column prop="llm_calls" :label="$t('autoDream.llmCalls')" width="100" />
              <el-table-column :label="$t('autoDream.duration')" width="100">
                <template #default="{ row }">
                  {{ row.duration_seconds.toFixed(1) }}{{ $t('autoDream.seconds') }}
                </template>
              </el-table-column>
              <el-table-column :label="$t('common.time')" width="160">
                <template #default="{ row }">
                  {{ formatDate(row.triggered_at) }}
                </template>
              </el-table-column>
              <el-table-column :label="$t('common.actions')" width="100">
                <template #default="{ row }">
                  <el-button size="small" @click.stop="showLogDetail(row)">
                    {{ $t('autoDream.details') }}
                  </el-button>
                </template>
              </el-table-column>
            </el-table>

            <el-empty v-else :description="$t('autoDream.noLogs')" />
          </div>

          <el-pagination
            v-if="logs.total > 0"
            v-model:current-page="logPage"
            v-model:page-size="logPageSize"
            :total="logs.total"
            :page-sizes="[10, 20, 50]"
            layout="total, sizes, prev, pager, next"
            @change="loadLogs"
            class="pagination"
          />
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- Log Detail Dialog -->
    <el-dialog
      v-model="detailVisible"
      :title="$t('autoDream.logDetail')"
      width="700px"
    >
      <div v-if="selectedLog" class="log-detail">
        <el-descriptions :column="2" border>
          <el-descriptions-item :label="'ID'">{{ selectedLog.id }}</el-descriptions-item>
          <el-descriptions-item :label="$t('common.status')">
            <el-tag :type="statusTagType(selectedLog.status)">
              {{ statusText(selectedLog.status) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item :label="$t('autoDream.totalMemories')">{{ selectedLog.total_memories }}</el-descriptions-item>
          <el-descriptions-item :label="$t('autoDream.duration')">
            {{ selectedLog.duration_seconds.toFixed(1) }}{{ $t('autoDream.seconds') }}
          </el-descriptions-item>
          <el-descriptions-item :label="$t('autoDream.duplicates')">{{ selectedLog.duplicates_found }}</el-descriptions-item>
          <el-descriptions-item :label="$t('autoDream.merged')">{{ selectedLog.merged }}</el-descriptions-item>
          <el-descriptions-item :label="$t('autoDream.consolidated')">{{ selectedLog.consolidated }}</el-descriptions-item>
          <el-descriptions-item :label="$t('autoDream.archived')">{{ selectedLog.archived }}</el-descriptions-item>
          <el-descriptions-item :label="$t('autoDream.deleted')">{{ selectedLog.deleted }}</el-descriptions-item>
          <el-descriptions-item :label="$t('autoDream.llmCalls')">{{ selectedLog.llm_calls }}</el-descriptions-item>
          <el-descriptions-item :label="$t('autoDream.tokensIn')">{{ selectedLog.llm_tokens_in }}</el-descriptions-item>
          <el-descriptions-item :label="$t('autoDream.tokensOut')">{{ selectedLog.llm_tokens_out }}</el-descriptions-item>
          <el-descriptions-item :label="$t('common.time')" :span="2">
            {{ formatDate(selectedLog.triggered_at) }}
          </el-descriptions-item>
          <el-descriptions-item v-if="selectedLog.error_message" :label="$t('autoDream.errorMessage')" :span="2">
            <pre class="error-pre">{{ selectedLog.error_message }}</pre>
          </el-descriptions-item>
        </el-descriptions>

        <div v-if="selectedLog.details" class="detail-json">
          <div class="detail-label">{{ $t('autoDream.details') }}</div>
          <pre>{{ formatJson(selectedLog.details) }}</pre>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { VideoPlay, Refresh, Warning } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
import {
  getAutoDreamStatus,
  getAutoDreamConfig,
  updateAutoDreamConfig,
  triggerAutoDream,
  getAutoDreamLogs,
} from '@/api/autodream'
import type {
  AutoDreamStatus,
  AutoDreamConfig,
  AutoDreamConfigUpdate,
  AutoDreamLog,
  AutoDreamLogList,
} from '@/api/autodream'

const { t } = useI18n()

const activeTab = ref('status')

// Status
const status = ref<AutoDreamStatus | null>(null)
const loadingStatus = ref(false)
const triggering = ref(false)

// Config
const configForm = reactive<AutoDreamConfigUpdate>({
  enabled: true,
  cron_expression: '0 3 * * *',
  batch_size: 50,
  max_consolidated_per_run: 5,
  archive_after_days: 30,
  delete_after_days: null,
  enable_dedup: true,
  enable_consolidation: true,
  enable_archival: true,
})
const loadingConfig = ref(false)
const savingConfig = ref(false)

// Logs
const logs = reactive<AutoDreamLogList>({ items: [], total: 0 })
const loadingLogs = ref(false)
const logPage = ref(1)
const logPageSize = ref(20)

// Detail dialog
const detailVisible = ref(false)
const selectedLog = ref<AutoDreamLog | null>(null)

function statusTagType(status: string): string {
  switch (status) {
    case 'success': return 'success'
    case 'failed': return 'danger'
    case 'skipped': return 'info'
    case 'running': return 'warning'
    default: return 'info'
  }
}

function statusText(status: string): string {
  switch (status) {
    case 'success': return t('autoDream.statusSuccess')
    case 'failed': return t('autoDream.statusFailed')
    case 'skipped': return t('autoDream.statusSkipped')
    case 'running': return t('autoDream.running')
    default: return status
  }
}

function formatDate(dateStr: string): string {
  if (!dateStr) return '-'
  const d = new Date(dateStr)
  return d.toLocaleString()
}

function formatJson(jsonStr: string): string {
  try {
    return JSON.stringify(JSON.parse(jsonStr), null, 2)
  } catch {
    return jsonStr
  }
}

async function loadStatus() {
  loadingStatus.value = true
  try {
    const data = await getAutoDreamStatus()
    status.value = data
  } catch (e: any) {
    ElMessage.error(e.detail || t('common.failed'))
  } finally {
    loadingStatus.value = false
  }
}

async function handleTrigger() {
  if (status.value?.is_running) {
    ElMessage.warning(t('autoDream.triggerRunning'))
    return
  }
  triggering.value = true
  try {
    const res = await triggerAutoDream()
    ElMessage.success(res.message || t('autoDream.triggerSuccess'))
    await loadStatus()
  } catch (e: any) {
    ElMessage.error(e.detail || t('common.failed'))
  } finally {
    triggering.value = false
  }
}

async function loadConfig() {
  loadingConfig.value = true
  try {
    const data = await getAutoDreamConfig()
    Object.assign(configForm, {
      enabled: data.enabled,
      cron_expression: data.cron_expression,
      batch_size: data.batch_size,
      max_consolidated_per_run: data.max_consolidated_per_run,
      archive_after_days: data.archive_after_days,
      delete_after_days: data.delete_after_days ?? 0,
      enable_dedup: data.enable_dedup,
      enable_consolidation: data.enable_consolidation,
      enable_archival: data.enable_archival,
    })
  } catch (e: any) {
    ElMessage.error(e.detail || t('common.failed'))
  } finally {
    loadingConfig.value = false
  }
}

async function saveConfig() {
  savingConfig.value = true
  try {
    const payload: AutoDreamConfigUpdate = {
      ...configForm,
      delete_after_days: configForm.delete_after_days === 0 ? null : configForm.delete_after_days,
    }
    await updateAutoDreamConfig(payload)
    ElMessage.success(t('autoDream.configSaved'))
  } catch (e: any) {
    ElMessage.error(e.detail || t('autoDream.saveFailed'))
  } finally {
    savingConfig.value = false
  }
}

async function loadLogs() {
  loadingLogs.value = true
  try {
    const data = await getAutoDreamLogs(logPage.value, logPageSize.value)
    logs.items = data.items
    logs.total = data.total
  } catch (e: any) {
    ElMessage.error(e.detail || t('common.failed'))
  } finally {
    loadingLogs.value = false
  }
}

function showLogDetail(row: AutoDreamLog) {
  selectedLog.value = row
  detailVisible.value = true
}

onMounted(() => {
  loadStatus()
  loadConfig()
  loadLogs()
})
</script>

<style scoped lang="scss">
.autodream-page {
  .status-actions {
    margin-bottom: 16px;
    display: flex;
    gap: 8px;
  }

  .stat-row {
    margin-bottom: 16px;
  }

  .stat-card {
    text-align: center;
    margin-bottom: 8px;

    .stat-label {
      font-size: 12px;
      color: var(--pc-text-muted);
      margin-bottom: 8px;
    }

    .stat-value {
      font-size: 24px;
      font-weight: 600;
      color: var(--pc-text-primary);
    }

    .stat-value-small {
      font-size: 13px;
      color: var(--pc-text-secondary);
      word-break: break-all;
    }

    .stat-value-tag {
      font-size: 14px;
    }
  }

  .error-card {
    margin-top: 16px;

    .error-header {
      display: flex;
      align-items: center;
      gap: 6px;
      color: var(--pc-accent-red);
      font-weight: 500;
    }

    .error-message {
      margin: 0;
      padding: 12px;
      background: var(--pc-bg-elevated);
      border-radius: var(--pc-radius-sm);
      font-size: 13px;
      color: var(--pc-text-secondary);
      white-space: pre-wrap;
      word-break: break-all;
    }
  }

  .config-form {
    max-width: 600px;
    padding-top: 16px;
  }

  .form-hint {
    margin-left: 8px;
    font-size: 12px;
    color: var(--pc-text-muted);
  }

  .clickable-table {
    :deep(tr) {
      cursor: pointer;
    }
  }

  .pagination {
    margin-top: 16px;
    justify-content: flex-end;
  }

  .log-detail {
    .detail-json {
      margin-top: 16px;

      .detail-label {
        font-size: 13px;
        font-weight: 500;
        color: var(--pc-text-primary);
        margin-bottom: 8px;
      }

      pre {
        margin: 0;
        padding: 12px;
        background: var(--pc-bg-elevated);
        border-radius: var(--pc-radius-sm);
        font-size: 12px;
        max-height: 400px;
        overflow: auto;
      }
    }

    .error-pre {
      margin: 0;
      color: var(--pc-accent-red);
      white-space: pre-wrap;
      word-break: break-all;
    }
  }
}
</style>
