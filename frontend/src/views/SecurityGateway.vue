<template>
  <div class="security-gateway">
    <el-page-header title="安全网关" @back="$router.back()">
      <template #content>
        <span class="text-large font-600 mr-3">安全网关管理</span>
      </template>
    </el-page-header>

    <el-tabs v-model="activeTab" class="mt-4">
      <!-- 安全看板 -->
      <el-tab-pane label="安全看板" name="dashboard">
        <div v-loading="dashboardLoading">
          <!-- 今日概览卡片 -->
          <el-row :gutter="16" class="mb-4">
            <el-col :span="6">
              <el-card>
                <div class="stat-card">
                  <div class="stat-value">{{ dashboardData.summary.total_checks_today }}</div>
                  <div class="stat-label">今日总检测</div>
                </div>
              </el-card>
            </el-col>
            <el-col :span="6">
              <el-card>
                <div class="stat-card">
                  <div class="stat-value text-danger">{{ dashboardData.summary.block_count_today }}</div>
                  <div class="stat-label">今日拦截</div>
                </div>
              </el-card>
            </el-col>
            <el-col :span="6">
              <el-card>
                <div class="stat-card">
                  <div class="stat-value text-warning">{{ dashboardData.summary.critical_count_today }}</div>
                  <div class="stat-label">今日严重事件</div>
                </div>
              </el-card>
            </el-col>
            <el-col :span="6">
              <el-card>
                <div class="stat-card">
                  <div class="stat-value">{{ dashboardData.summary.avg_response_ms }}ms</div>
                  <div class="stat-label">平均响应时间</div>
                </div>
              </el-card>
            </el-col>
          </el-row>

          <!-- 图表区域 -->
          <el-row :gutter="16" class="mb-4">
            <el-col :span="16">
              <el-card>
                <template #header>
                  <span>风险趋势（近7天）</span>
                </template>
                <v-chart :option="trendChartOption" style="height: 300px" autoresize />
              </el-card>
            </el-col>
            <el-col :span="8">
              <el-card>
                <template #header>
                  <span>高频敏感词 TOP 10</span>
                </template>
                <v-chart :option="topWordsChartOption" style="height: 300px" autoresize />
              </el-card>
            </el-col>
          </el-row>

          <!-- 用户风险排名 -->
          <el-card>
            <template #header>
              <span>用户风险排名 TOP 10</span>
            </template>
            <el-table :data="dashboardData.top_users" stripe size="small">
              <el-table-column type="index" label="排名" width="60" />
              <el-table-column prop="username" label="用户" />
              <el-table-column prop="block_count" label="拦截次数" width="120">
                <template #default="{ row }">
                  <el-tag type="danger">{{ row.block_count }}</el-tag>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </div>
      </el-tab-pane>

      <!-- 检测测试 -->
      <el-tab-pane label="检测测试" name="test">
        <el-card>
          <template #header>
            <span>输入内容安全检测</span>
          </template>
          <el-input
            v-model="testText"
            type="textarea"
            :rows="6"
            placeholder="输入要检测的文本，例如：身份证号 51012319900101001X，手机号 13800138000"
          />
          <el-button type="primary" class="mt-3" @click="runTest" :loading="testing">
            检测
          </el-button>

          <div v-if="testResult" class="mt-4">
            <el-alert
              :title="`检测结果: ${actionLabel(testResult.action)}`"
              :type="alertType(testResult.action)"
              :description="testResult.reason"
              show-icon
              :closable="false"
            />
            <div v-if="testResult.matched_rules?.length" class="mt-3">
              <el-text type="info">匹配规则:</el-text>
              <el-tag
                v-for="(rule, idx) in testResult.matched_rules"
                :key="idx"
                :type="tagType(rule.severity)"
                class="ml-2 mt-2"
              >
                {{ rule.type }}: {{ rule.match || rule.word }} (severity={{ rule.severity }})
              </el-tag>
            </div>
            <div v-if="testResult.model_result" class="mt-3">
              <el-text type="info">模型检测:</el-text>
              <el-tag :type="tagType(testResult.model_result.severity)" class="ml-2">
                {{ testResult.model_result.category }} (severity={{ testResult.model_result.severity }})
              </el-tag>
              <el-text type="info" class="ml-2">{{ testResult.model_result.description }}</el-text>
              <el-tag v-if="testResult.model_result.source === 'llm'" type="primary" size="small" class="ml-2">LLM</el-tag>
            </div>
            <div v-if="testResult.content" class="mt-3">
              <el-text type="info">脱敏结果:</el-text>
              <el-input v-model="testResult.content" type="textarea" :rows="3" readonly class="mt-2" />
            </div>
          </div>
        </el-card>
      </el-tab-pane>

      <!-- 词库管理 -->
      <el-tab-pane label="词库管理" name="words">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>词库管理</span>
              <div>
                <el-button type="primary" @click="showCreateDialog = true">
                  <el-icon><Plus /></el-icon> 新增
                </el-button>
                <el-button @click="loadWords">
                  <el-icon><Refresh /></el-icon> 刷新
                </el-button>
              </div>
            </div>
          </template>

          <el-table :data="wordList" v-loading="wordLoading" stripe>
            <el-table-column prop="word" label="词汇" width="200" />
            <el-table-column prop="word_type" label="类型" width="100">
              <template #default="{ row }">
                <el-tag :type="wordTypeTag(row.word_type)">{{ wordTypeLabel(row.word_type) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="category" label="分类" width="120" />
            <el-table-column prop="severity" label="严重度" width="100">
              <template #default="{ row }">
                <el-tag :type="severityTag(row.severity)">{{ row.severity }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="description" label="描述" show-overflow-tooltip />
            <el-table-column prop="is_active" label="状态" width="80">
              <template #default="{ row }">
                <el-tag :type="row.is_active ? 'success' : 'info'">
                  {{ row.is_active ? '启用' : '禁用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="150" fixed="right">
              <template #default="{ row }">
                <el-button size="small" @click="openEditDialog(row)">编辑</el-button>
                <el-button size="small" type="danger" @click="deleteWord(row.id)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>

          <el-pagination
            v-model:current-page="wordPage"
            v-model:page-size="wordPageSize"
            :total="wordTotal"
            layout="total, prev, pager, next"
            class="mt-4"
            @change="loadWords"
          />
        </el-card>
      </el-tab-pane>

      <!-- 审计日志 -->
      <el-tab-pane label="审计日志" name="audit">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>安全审计日志</span>
              <el-button @click="loadAuditLogs">
                <el-icon><Refresh /></el-icon> 刷新
              </el-button>
            </div>
          </template>

          <div class="filter-bar mb-4">
            <el-select v-model="auditFilter.risk_level" placeholder="风险级别" clearable class="mr-2">
              <el-option label="低" value="low" />
              <el-option label="中" value="medium" />
              <el-option label="高" value="high" />
              <el-option label="严重" value="critical" />
            </el-select>
            <el-select v-model="auditFilter.check_point" placeholder="检查点" clearable class="mr-2">
              <el-option label="输入过滤" value="filter_input" />
              <el-option label="输出过滤" value="filter_output" />
              <el-option label="工具检查" value="check_tool" />
            </el-select>
            <el-button type="primary" @click="loadAuditLogs">查询</el-button>
          </div>

          <el-table :data="auditList" v-loading="auditLoading" stripe>
            <el-table-column prop="created_at" label="时间" width="180">
              <template #default="{ row }">
                {{ formatTime(row.created_at) }}
              </template>
            </el-table-column>
            <el-table-column prop="check_point" label="检查点" width="120">
              <template #default="{ row }">
                {{ checkpointLabel(row.check_point) }}
              </template>
            </el-table-column>
            <el-table-column prop="action" label="操作" width="100">
              <template #default="{ row }">
                <el-tag :type="actionTag(row.action)">{{ actionLabel(row.action) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="risk_level" label="风险" width="100">
              <template #default="{ row }">
                <el-tag :type="riskTag(row.risk_level)">{{ row.risk_level }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="content_preview" label="内容预览" show-overflow-tooltip />
            <el-table-column prop="reason" label="原因" show-overflow-tooltip />
            <el-table-column prop="username" label="用户" width="100" />
            <el-table-column prop="session_id" label="会话" width="120" show-overflow-tooltip />
          </el-table>

          <el-pagination
            v-model:current-page="auditPage"
            v-model:page-size="auditPageSize"
            :total="auditTotal"
            layout="total, prev, pager, next"
            class="mt-4"
            @change="loadAuditLogs"
          />
        </el-card>
      </el-tab-pane>

      <!-- 配置管理 -->
      <el-tab-pane label="配置管理" name="config">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>引擎与模型配置</span>
              <el-button type="primary" @click="saveConfig">
                <el-icon><Check /></el-icon> 保存配置
              </el-button>
            </div>
          </template>

          <el-form :model="configForm" label-width="160px" style="max-width: 600px">
            <el-divider>引擎开关</el-divider>
            <el-form-item label="启用词引擎">
              <el-switch v-model="configForm.enable_word_engine" />
            </el-form-item>
            <el-form-item label="启用正则引擎">
              <el-switch v-model="configForm.enable_regex_engine" />
            </el-form-item>
            <el-form-item label="启用模型引擎">
              <el-switch v-model="configForm.enable_model_engine" />
            </el-form-item>

            <el-divider>模型引擎 LLM 增强（可选）</el-divider>
            <el-form-item label="启用 LLM 增强">
              <el-switch v-model="configForm.enable_model_llm" />
            </el-form-item>

            <el-form-item label="平台 AI 配置" v-show="configForm.enable_model_llm">
              <el-select
                v-model="configForm.ai_config_id"
                @change="onAiConfigChange"
                clearable
                placeholder="手动输入（不引用平台配置）"
                style="width: 100%"
                :loading="aiConfigLoading"
              >
                <el-option
                  v-for="cfg in aiConfigList"
                  :key="cfg.id"
                  :label="cfg.display_name || cfg.name"
                  :value="cfg.id"
                />
              </el-select>
              <div class="form-tip">选择平台已有配置可自动填充 URL/模型/Key，也可留空手动输入</div>
            </el-form-item>

            <el-form-item label="Base URL" v-show="configForm.enable_model_llm">
              <el-input
                v-model="configForm.model_engine_llm_url"
                placeholder="https://your-api.com/v1/chat/completions"
              />
            </el-form-item>
            <el-form-item label="模型 ID" v-show="configForm.enable_model_llm">
              <el-input
                v-model="configForm.model_engine_llm_model"
                placeholder="模型 ID"
              />
            </el-form-item>
            <el-form-item label="API Key" v-show="configForm.enable_model_llm">
              <el-input
                v-model="configForm.model_engine_llm_api_key"
                placeholder="无认证可留空"
                type="password"
                show-password
              />
            </el-form-item>
            <el-form-item label="超时（秒）" v-show="configForm.enable_model_llm">
              <el-input-number v-model="configForm.model_engine_llm_timeout" :min="1" :max="30" />
            </el-form-item>
            <el-form-item v-show="configForm.enable_model_llm">
              <el-button
                type="info"
                :loading="testingLlm"
                @click="testLlmConnection"
                :disabled="!configForm.model_engine_llm_url"
              >
                <el-icon><Connection /></el-icon> 测试连接
              </el-button>
              <el-text v-if="llmTestResult" :type="llmTestResult.success ? 'success' : 'danger'" class="ml-3">
                {{ llmTestResult.message }}
                <span v-if="llmTestResult.latency_ms">({{ llmTestResult.latency_ms }}ms)</span>
              </el-text>
            </el-form-item>

            <el-divider>其他</el-divider>
            <el-form-item label="降级放行（Fail Open）">
              <el-switch v-model="configForm.fail_open" />
              <template #append>
                <el-text type="info">安全网关异常时放行请求</el-text>
              </template>
            </el-form-item>
            <el-form-item label="日志保留天数">
              <el-input-number v-model="configForm.log_retention_days" :min="1" :max="3650" />
            </el-form-item>
          </el-form>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 创建/编辑词对话框 -->
    <el-dialog v-model="showCreateDialog" :title="isEditing ? '编辑词汇' : '新增词汇'" width="500px">
      <el-form :model="wordForm" label-width="80px">
        <el-form-item label="词汇" required>
          <el-input v-model="wordForm.word" placeholder="输入敏感词/风控词/放通词" />
        </el-form-item>
        <el-form-item label="类型" required>
          <el-select v-model="wordForm.word_type" placeholder="选择类型">
            <el-option label="敏感词" value="sensitive" />
            <el-option label="风险词" value="risk" />
            <el-option label="放通词" value="allow" />
          </el-select>
        </el-form-item>
        <el-form-item label="分类">
          <el-input v-model="wordForm.category" placeholder="例如：个人信息/警务/系统" />
        </el-form-item>
        <el-form-item label="严重度">
          <el-slider v-model="wordForm.severity" :min="1" :max="5" show-stops />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="wordForm.description" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" @click="submitWord">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Check, Connection } from '@element-plus/icons-vue'
import { use } from 'echarts/core'
import { LineChart, BarChart } from 'echarts/charts'
import { TitleComponent, TooltipComponent, LegendComponent, GridComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import VChart from 'vue-echarts'
import { securityGatewayApi, type FilterResult, type WordItem, type AuditLogItem } from '@/api/security_gateway'
import { api } from '@/api'

use([LineChart, BarChart, TitleComponent, TooltipComponent, LegendComponent, GridComponent, CanvasRenderer])

const activeTab = ref('dashboard')

// 安全看板
const dashboardLoading = ref(false)
const dashboardData = reactive({
  risk_trend: [] as any[],
  top_words: [] as any[],
  top_users: [] as any[],
  summary: {
    total_checks_today: 0,
    block_count_today: 0,
    critical_count_today: 0,
    avg_response_ms: 0,
  },
})

const trendChartOption = computed(() => {
  const dates = dashboardData.risk_trend.map((d: any) => d.date)
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['拦截', '审批', '脱敏', '放行'] },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: { type: 'category', boundaryGap: false, data: dates },
    yAxis: { type: 'value' },
    series: [
      { name: '拦截', type: 'line', data: dashboardData.risk_trend.map((d: any) => d.block), smooth: true, itemStyle: { color: '#f56c6c' } },
      { name: '审批', type: 'line', data: dashboardData.risk_trend.map((d: any) => d.approve), smooth: true, itemStyle: { color: '#e6a23c' } },
      { name: '脱敏', type: 'line', data: dashboardData.risk_trend.map((d: any) => d.sanitize), smooth: true, itemStyle: { color: '#409eff' } },
      { name: '放行', type: 'line', data: dashboardData.risk_trend.map((d: any) => d.allow), smooth: true, itemStyle: { color: '#67c23a' } },
    ],
  }
})

const topWordsChartOption = computed(() => {
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: '3%', right: '8%', bottom: '3%', containLabel: true },
    xAxis: { type: 'value' },
    yAxis: {
      type: 'category',
      data: [...dashboardData.top_words].reverse().map((w: any) => w.word),
    },
    series: [
      {
        type: 'bar',
        data: [...dashboardData.top_words].reverse().map((w: any) => w.count),
        itemStyle: { color: '#409eff' },
      },
    ],
  }
})

const loadDashboard = async () => {
  dashboardLoading.value = true
  try {
    const { data } = await securityGatewayApi.getDashboardStats(7)
    dashboardData.risk_trend = data.risk_trend || []
    dashboardData.top_words = data.top_words || []
    dashboardData.top_users = data.top_users || []
    dashboardData.summary = data.summary || dashboardData.summary
  } catch (e: any) {
    ElMessage.error('加载看板数据失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    dashboardLoading.value = false
  }
}

// 检测测试
const testText = ref('')
const testing = ref(false)
const testResult = ref<FilterResult | null>(null)

const runTest = async () => {
  if (!testText.value.trim()) {
    ElMessage.warning('请输入要检测的文本')
    return
  }
  testing.value = true
  try {
    const { data } = await securityGatewayApi.testFilter(testText.value)
    testResult.value = data
  } catch (e: any) {
    ElMessage.error('检测失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    testing.value = false
  }
}

// 词库管理
const wordList = ref<WordItem[]>([])
const wordTotal = ref(0)
const wordPage = ref(1)
const wordPageSize = ref(20)
const wordLoading = ref(false)
const showCreateDialog = ref(false)
const isEditing = ref(false)
const editingId = ref<number | null>(null)
const wordForm = reactive<Partial<WordItem>>({
  word: '',
  word_type: 'sensitive',
  category: '',
  severity: 3,
  description: '',
  is_active: true,
})

const resetForm = () => {
  isEditing.value = false
  editingId.value = null
  wordForm.word = ''
  wordForm.word_type = 'sensitive'
  wordForm.category = ''
  wordForm.severity = 3
  wordForm.description = ''
  wordForm.is_active = true
}

const loadWords = async () => {
  wordLoading.value = true
  try {
    const { data } = await securityGatewayApi.listWords({
      skip: (wordPage.value - 1) * wordPageSize.value,
      limit: wordPageSize.value,
    })
    wordList.value = data.items || []
    wordTotal.value = data.total || 0
  } catch (e: any) {
    ElMessage.error('加载词库失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    wordLoading.value = false
  }
}

const openEditDialog = (row: WordItem) => {
  isEditing.value = true
  editingId.value = row.id
  wordForm.word = row.word
  wordForm.word_type = row.word_type
  wordForm.category = row.category || ''
  wordForm.severity = row.severity
  wordForm.description = row.description || ''
  wordForm.is_active = row.is_active
  showCreateDialog.value = true
}

const submitWord = async () => {
  if (!wordForm.word || !wordForm.word_type) {
    ElMessage.warning('请填写完整信息')
    return
  }
  try {
    if (isEditing.value && editingId.value !== null) {
      await securityGatewayApi.updateWord(editingId.value, wordForm)
      ElMessage.success('更新成功')
    } else {
      await securityGatewayApi.createWord(wordForm)
      ElMessage.success('创建成功')
    }
    showCreateDialog.value = false
    resetForm()
    loadWords()
  } catch (e: any) {
    ElMessage.error('保存失败: ' + (e.response?.data?.detail || e.message))
  }
}

const deleteWord = async (id: number) => {
  try {
    await ElMessageBox.confirm('确认删除该词汇?', '提示', { type: 'warning' })
    await securityGatewayApi.deleteWord(id)
    ElMessage.success('删除成功')
    loadWords()
  } catch (e: any) {
    if (e !== 'cancel') {
      ElMessage.error('删除失败: ' + (e.response?.data?.detail || e.message))
    }
  }
}

// 审计日志
const auditList = ref<AuditLogItem[]>([])
const auditTotal = ref(0)
const auditPage = ref(1)
const auditPageSize = ref(20)
const auditLoading = ref(false)
const auditFilter = reactive({
  risk_level: '',
  check_point: '',
})

const loadAuditLogs = async () => {
  auditLoading.value = true
  try {
    const { data } = await securityGatewayApi.listAuditLogs({
      risk_level: auditFilter.risk_level || undefined,
      check_point: auditFilter.check_point || undefined,
      skip: (auditPage.value - 1) * auditPageSize.value,
      limit: auditPageSize.value,
    })
    auditList.value = data.items || []
    auditTotal.value = data.total || 0
  } catch (e: any) {
    ElMessage.error('加载审计日志失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    auditLoading.value = false
  }
}

// 配置管理
const configForm = reactive({
  enable_word_engine: true,
  enable_regex_engine: true,
  enable_model_engine: true,
  enable_model_llm: false,
  ai_config_id: null as number | null,
  model_engine_llm_url: '',
  model_engine_llm_model: 'qwen2.5:1.5b',
  model_engine_llm_api_key: '',
  model_engine_llm_timeout: 3,
  fail_open: true,
  log_retention_days: 180,
})
const configLoading = ref(false)

// 平台 AI 配置列表
const aiConfigList = ref<any[]>([])
const aiConfigLoading = ref(false)
const aiConfigMap = ref<Record<number, any>>({})

const loadAiConfigs = async () => {
  aiConfigLoading.value = true
  try {
    const { data } = await api.get('/ai-configs')
    aiConfigList.value = data || []
    // 构建 id -> config 映射
    const map: Record<number, any> = {}
    for (const cfg of aiConfigList.value) {
      map[cfg.id] = cfg
    }
    aiConfigMap.value = map
  } catch (e: any) {
    ElMessage.error('加载平台 AI 配置失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    aiConfigLoading.value = false
  }
}

const loadConfig = async () => {
  try {
    const { data } = await securityGatewayApi.getConfig()
    Object.assign(configForm, data)
  } catch (e: any) {
    ElMessage.error('加载配置失败: ' + (e.response?.data?.detail || e.message))
  }
}

const saveConfig = async () => {
  configLoading.value = true
  try {
    await securityGatewayApi.updateConfig(configForm)
    ElMessage.success('配置已保存')
  } catch (e: any) {
    ElMessage.error('保存配置失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    configLoading.value = false
  }
}

const onAiConfigChange = (configId: number | null) => {
  llmTestResult.value = null
  if (!configId) return
  const cfg = aiConfigMap.value[configId]
  if (!cfg) return
  // 自动填充 URL/模型
  if (cfg.base_url) {
    configForm.model_engine_llm_url = cfg.base_url
  }
  if (cfg.model_name) {
    configForm.model_engine_llm_model = cfg.model_name
  }
  // API Key 从平台获取明文（需要管理员权限）
  loadAiConfigApiKey(configId)
}

const loadAiConfigApiKey = async (configId: number) => {
  try {
    const { data } = await api.get(`/ai-configs/${configId}/api-key`)
    if (data.api_key) {
      configForm.model_engine_llm_api_key = data.api_key
    }
  } catch (e: any) {
    // 非管理员可能无权查看，静默失败
    console.warn('无法获取平台配置 API Key:', e.message)
  }
}

// LLM 测试
const testingLlm = ref(false)
const llmTestResult = ref<any>(null)

const testLlmConnection = async () => {
  testingLlm.value = true
  llmTestResult.value = null
  try {
    const { data } = await securityGatewayApi.testLlmConnection({
      url: configForm.model_engine_llm_url,
      model: configForm.model_engine_llm_model,
      api_key: configForm.model_engine_llm_api_key,
      timeout: configForm.model_engine_llm_timeout,
    })
    llmTestResult.value = data
    if (data.success) {
      ElMessage.success(data.message)
    } else {
      ElMessage.error(data.message)
    }
  } catch (e: any) {
    ElMessage.error('测试失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    testingLlm.value = false
  }
}

// 辅助函数
const actionLabel = (action: string) => {
  const map: Record<string, string> = {
    allow: '放行',
    block: '拦截',
    sanitize: '脱敏',
    approve: '审批',
  }
  return map[action] || action
}

const actionTag = (action: string) => {
  const map: Record<string, any> = {
    allow: 'success',
    block: 'danger',
    sanitize: 'warning',
    approve: 'info',
  }
  return map[action] || 'info'
}

const alertType = (action: string) => {
  const map: Record<string, any> = {
    allow: 'success',
    block: 'error',
    sanitize: 'warning',
    approve: 'info',
  }
  return map[action] || 'info'
}

const riskTag = (level: string) => {
  const map: Record<string, any> = {
    low: 'success',
    medium: 'warning',
    high: 'danger',
    critical: 'danger',
  }
  return map[level] || 'info'
}

const tagType = (severity: number) => {
  if (severity >= 4) return 'danger'
  if (severity >= 3) return 'warning'
  return 'info'
}

const wordTypeTag = (type: string) => {
  const map: Record<string, any> = {
    sensitive: 'danger',
    risk: 'warning',
    allow: 'success',
  }
  return map[type] || 'info'
}

const wordTypeLabel = (type: string) => {
  const map: Record<string, string> = {
    sensitive: '敏感词',
    risk: '风险词',
    allow: '放通词',
  }
  return map[type] || type
}

const severityTag = (severity: number) => {
  if (severity >= 4) return 'danger'
  if (severity >= 3) return 'warning'
  return 'info'
}

const checkpointLabel = (cp: string) => {
  const map: Record<string, string> = {
    filter_input: '输入过滤',
    filter_output: '输出过滤',
    check_tool: '工具检查',
  }
  return map[cp] || cp
}

const formatTime = (time: string) => {
  return new Date(time).toLocaleString('zh-CN')
}

onMounted(() => {
  loadDashboard()
  loadWords()
  loadAuditLogs()
  loadAiConfigs()
  loadConfig()
})
</script>

<style scoped>
.security-gateway {
  padding: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.filter-bar {
  display: flex;
  align-items: center;
}

.mt-3 {
  margin-top: 12px;
}

.mt-4 {
  margin-top: 16px;
}

.mb-4 {
  margin-bottom: 16px;
}

.ml-2 {
  margin-left: 8px;
}

.form-tip {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}

.stat-card {
  text-align: center;
  padding: 12px 0;
}

.stat-value {
  font-size: 28px;
  font-weight: bold;
  color: #303133;
}

.stat-value.text-danger {
  color: #f56c6c;
}

.stat-value.text-warning {
  color: #e6a23c;
}

.stat-label {
  margin-top: 8px;
  font-size: 14px;
  color: #909399;
}
</style>
