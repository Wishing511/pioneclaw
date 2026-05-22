<template>
  <div class="file-memory-panel">
    <!-- Stats Row -->
    <div class="file-memory-stats">
      <el-card class="stat-card">
        <div class="stat-icon primary"><el-icon><Collection /></el-icon></div>
        <div class="stat-info">
          <div class="stat-value">{{ stats.total_entries }}</div>
          <div class="stat-label">{{ $t('memory.totalEntries') }}</div>
        </div>
      </el-card>
      <el-card class="stat-card">
        <div class="stat-icon success"><el-icon><FolderOpened /></el-icon></div>
        <div class="stat-info">
          <div class="stat-value">{{ sourceCount }}</div>
          <div class="stat-label">{{ $t('memory.sourcesCount') }}</div>
        </div>
      </el-card>
      <el-card class="stat-card">
        <div class="stat-icon purple"><el-icon><Document /></el-icon></div>
        <div class="stat-info">
          <div class="stat-value">{{ totalChars }}</div>
          <div class="stat-label">{{ $t('memory.totalChars') }}</div>
        </div>
      </el-card>
    </div>

    <!-- Filter Bar -->
    <el-card class="filter-card">
      <div class="filter-inner">
        <div class="filter-left">
          <el-select v-model="sourceFilter" :placeholder="$t('memory.filterBySource')" style="width: 160px" clearable @change="loadEntries">
            <el-option v-for="s in sources" :key="s" :label="s" :value="s" />
          </el-select>
          <el-input
            v-model="search"
            :placeholder="$t('memory.searchKeywords')"
            clearable
            @keyup.enter="searchEntries"
          >
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
        </div>
        <div class="filter-right">
          <button class="pc-glow-btn secondary" @click="refresh">
            <el-icon><Refresh /></el-icon> {{ $t('common.refresh') }}
          </button>
          <button class="pc-glow-btn secondary" @click="exportMemory">
            <el-icon><Download /></el-icon> {{ $t('common.export') }}
          </button>
          <button class="pc-glow-btn secondary" @click="showTableView = !showTableView">
            <el-icon><List /></el-icon> {{ showTableView ? $t('memory.viewFull') : $t('memory.entryList') }}
          </button>
          <button class="pc-glow-btn" @click="openEditor()">
            <el-icon><Edit /></el-icon> {{ $t('common.edit') }}
          </button>
          <button class="pc-glow-btn" @click="showAppendDialog = true">
            <el-icon><Plus /></el-icon> {{ $t('memory.addEntry') }}
          </button>
          <button class="pc-glow-btn danger" @click="clearAll">
            <el-icon><Delete /></el-icon> {{ $t('memory.clearAll') }}
          </button>
        </div>
      </div>
    </el-card>

    <!-- Main Content: Rendered Markdown (default) or Entries Table -->
    <el-card v-if="!showTableView" class="file-memory-card" v-loading="loading">
      <div v-if="content" class="file-memory-rendered" v-html="contentHtml" />
      <el-empty v-else :description="$t('memory.emptyLongTerm')" />
    </el-card>

    <el-card v-else class="file-memory-card" v-loading="loading">
      <el-table :data="entries" stripe style="width: 100%" class="pc-data-table">
        <template #empty>
          <el-empty :description="$t('common.noData')" />
        </template>
        <el-table-column type="index" :label="'#'" width="55" />
        <el-table-column prop="source" :label="$t('wiki.source')" width="120">
          <template #default="{ row }">
            <el-tag size="small" type="info">{{ row.source }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="$t('common.description')" min-width="400">
          <template #default="{ row }">
            <span class="content-cell" @click="openViewer(row)" style="cursor: pointer;">
              {{ headingText(row.content) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="date" :label="$t('common.time')" width="130" />
        <el-table-column :label="$t('common.actions')" width="120" align="center">
          <template #default="{ row }">
            <div class="pc-action-group">
              <el-button size="small" text type="primary" @click="openViewer(row)">
                <el-icon><View /></el-icon>
              </el-button>
              <el-button size="small" text type="danger" @click="deleteLine(row)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- Append Dialog -->
    <el-dialog v-model="showAppendDialog" :title="$t('memory.addEntry')" width="500px" destroy-on-close class="cyber-dialog">
      <el-form :model="appendForm" label-width="80px">
        <el-form-item :label="$t('wiki.source')" required>
          <el-input v-model="appendForm.source" placeholder="manual" />
        </el-form-item>
        <el-form-item :label="$t('common.description')" required>
          <el-input v-model="appendForm.content" type="textarea" :rows="5" :placeholder="$t('common.description')" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAppendDialog = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="appending" @click="appendEntry">{{ $t('common.submit') }}</el-button>
      </template>
    </el-dialog>

    <!-- View Entry Dialog -->
    <el-dialog v-model="showViewDialog" :title="$t('memory.viewEntry')" width="700px" class="cyber-dialog">
      <div class="memory-rendered" v-html="viewerHtml" />
    </el-dialog>

    <!-- Full Content Editor Dialog -->
    <el-dialog v-model="showEditDialog" :title="$t('memory.editMemory')" width="900px" destroy-on-close class="cyber-dialog">
      <div class="markdown-tools">
        <button class="tool-btn" title="Bold" @click="insertMarkdown('**', '**')"><strong>B</strong></button>
        <button class="tool-btn" title="Italic" @click="insertMarkdown('*', '*')"><em>I</em></button>
        <button class="tool-btn" title="Code" @click="insertMarkdown('`', '`')"><span class="tool-code">&lt;/&gt;</span></button>
        <button class="tool-btn" title="Heading" @click="insertMarkdown('## ', '')"><strong>H</strong></button>
        <button class="tool-btn" title="List" @click="insertMarkdown('- ', '')"><el-icon><List /></el-icon></button>
      </div>
      <div v-if="saveError" class="error-bar">
        <el-icon><WarningFilled /></el-icon>
        <span>{{ saveError }}</span>
      </div>
      <div class="editor-panes">
        <div class="editor-pane">
          <div class="pane-header">{{ $t('memory.editor') }}</div>
          <textarea ref="textareaRef" v-model="editContent" class="editor-textarea" :placeholder="$t('memory.editPlaceholder')" @keydown.ctrl.s.prevent="saveContent" />
        </div>
        <div class="preview-pane">
          <div class="pane-header">{{ $t('memory.preview') }}</div>
          <div class="preview-content" v-html="previewHtml" />
        </div>
      </div>
      <template #footer>
        <el-button @click="showEditDialog = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" @click="saveContent">{{ $t('common.save') }}</el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { marked } from 'marked'
import {
  Collection, FolderOpened, Document,
  Plus, Download, Delete, Refresh, Edit, View,
  Search, List, WarningFilled,
} from '@element-plus/icons-vue'
import { memoryApi, type MemoryEntry, type MemoryStats } from '@/api/memory'

const { t: $t } = useI18n()

// ==================== State ====================
const stats = ref<MemoryStats>({ total_entries: 0, sources: {}, date_range: null, total_chars: 0 })
const content = ref('')
const editContent = ref('')
const entries = ref<MemoryEntry[]>([])
const loading = ref(false)
const saving = ref(false)
const saveError = ref<string | null>(null)
const textareaRef = ref<HTMLTextAreaElement | null>(null)
const search = ref('')
const sourceFilter = ref('')
const showAppendDialog = ref(false)
const showEditDialog = ref(false)
const showViewDialog = ref(false)
const showTableView = ref(false)
const appending = ref(false)
const viewerHtml = ref('')
const appendForm = ref({ source: 'manual', content: '' })

function stripLinePrefix(line: string): string {
  const idx = line.indexOf('|')
  if (idx === -1) return line
  const idx2 = line.indexOf('|', idx + 1)
  if (idx2 === -1) return line
  return line.substring(idx2 + 1)
}

const sourceCount = computed(() => Object.keys(stats.value.sources || {}).length)
const totalChars = computed(() => stats.value.total_chars.toLocaleString())
const sources = computed(() => Object.keys(stats.value.sources || {}))

const previewHtml = computed(() => {
  if (!editContent.value) return `<div class="empty-preview">${$t('memory.emptyPreview')}</div>`
  const cleaned = editContent.value.split('\n').map(stripLinePrefix).join('\n')
  return marked.parse(cleaned) as string
})

const contentHtml = computed(() => renderMarkdown(content.value))

function renderMarkdown(text: string): string {
  if (!text) return ''
  const cleaned = text.split('\n').map(stripLinePrefix).join('\n')
  return marked.parse(cleaned) as string
}

// ==================== Data Loading ====================
async function loadStats() {
  try {
    const { data } = await memoryApi.info()
    if (data) stats.value = data
  } catch { /* ignore */ }
}

async function loadContent() {
  try {
    const { data } = await memoryApi.exportMemory()
    if (data) content.value = data.content
  } catch { /* ignore */ }
}

async function loadEntries() {
  loading.value = true
  try {
    const { data } = await memoryApi.recent(100)
    if (data?.entries) {
      let list = data.entries
      if (sourceFilter.value) {
        list = list.filter((e: MemoryEntry) => e.source === sourceFilter.value)
      }
      if (search.value) {
        const kw = search.value.toLowerCase()
        list = list.filter((e: MemoryEntry) => e.content.toLowerCase().includes(kw) || e.source.toLowerCase().includes(kw))
      }
      entries.value = list
    }
  } catch { /* ignore */ }
  loading.value = false
}

async function refresh() {
  await Promise.all([loadStats(), loadContent(), loadEntries()])
}

async function searchEntries() {
  await loadEntries()
}

// ==================== Editor ====================
function openEditor() {
  editContent.value = content.value
  saveError.value = null
  showEditDialog.value = true
  nextTick(() => {
    textareaRef.value?.focus()
  })
}

async function saveContent() {
  if (saving.value) return
  saving.value = true
  saveError.value = null
  try {
    await memoryApi.saveContent(editContent.value)
    content.value = editContent.value
    showEditDialog.value = false
    ElMessage.success($t('common.success'))
    await loadStats()
  } catch (e: any) {
    saveError.value = e?.message || $t('memory.saveError')
  }
  saving.value = false
}

function insertMarkdown(before: string, after: string) {
  const ta = textareaRef.value
  if (!ta) return
  const start = ta.selectionStart
  const end = ta.selectionEnd
  const sel = editContent.value.substring(start, end)
  editContent.value = editContent.value.substring(0, start) + before + sel + after + editContent.value.substring(end)
  nextTick(() => {
    ta.focus()
    const pos = start + before.length + sel.length
    ta.setSelectionRange(pos, pos)
  })
}

// ==================== Append ====================
async function appendEntry() {
  if (!appendForm.value.content) return
  appending.value = true
  try {
    await memoryApi.append(appendForm.value.source, appendForm.value.content)
    ElMessage.success($t('common.success'))
    showAppendDialog.value = false
    appendForm.value = { source: 'manual', content: '' }
    await Promise.all([loadStats(), loadContent(), loadEntries()])
  } catch { /* ignore */ }
  appending.value = false
}

// ==================== Entry Actions ====================
function headingText(text: string): string {
  const firstLine = text.split('\n')[0]
  return firstLine.replace(/^#+\s*/, '')
}

function openViewer(row: MemoryEntry) {
  viewerHtml.value = marked.parse(row.content) as string
  showViewDialog.value = true
}

async function deleteLine(row: MemoryEntry) {
  try {
    await memoryApi.deleteLine(row.line_number)
    ElMessage.success($t('common.success'))
    await Promise.all([loadStats(), loadContent(), loadEntries()])
  } catch { /* ignore */ }
}

async function clearAll() {
  try {
    await memoryApi.clear()
    content.value = ''
    entries.value = []
    ElMessage.success($t('common.success'))
    await loadStats()
  } catch { /* ignore */ }
}

async function exportMemory() {
  try {
    const { data } = await memoryApi.exportMemory()
    if (data) {
      const blob = new Blob([data.content], { type: 'text/plain' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'MEMORY.md'
      a.click()
      URL.revokeObjectURL(url)
    }
  } catch { /* ignore */ }
}

// ==================== Lifecycle ====================
onMounted(() => {
  loadStats()
  loadContent()
  loadEntries()
})
</script>

<style scoped lang="scss">
.file-memory-panel {
  padding: 0;
}

// ── Stat Cards ──
.file-memory-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
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
      flex-shrink: 0;

      &.primary {
        background: var(--pc-primary);
        box-shadow: 0 0 14px rgba(var(--pc-primary-rgb), 0.35);
      }
      &.success {
        background: var(--pc-accent-green);
        box-shadow: 0 0 14px rgba(var(--pc-accent-green-rgb), 0.3);
      }
      &.purple {
        background: var(--pc-accent-purple);
        box-shadow: 0 0 14px rgba(var(--pc-accent-purple-rgb), 0.3);
      }
    }

    .stat-info {
      .stat-value {
        font-size: 26px;
        font-weight: 700;
        color: var(--pc-text-primary);
        letter-spacing: 0.5px;
        line-height: 1.2;
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

// ── Table Content Cell ──
.content-cell {
  color: var(--pc-text-primary);
  line-height: 1.6;
  &:hover { color: var(--pc-primary); }
}

// ── Viewer Dialog ──
.memory-rendered {
  padding: 24px;
  background: var(--pc-glass-bg);
  border: 1px solid var(--pc-glass-border);
  border-radius: var(--pc-radius-lg);
  color: var(--pc-text-primary);
  line-height: 1.7;
  font-size: 14px;
  max-height: 60vh;
  overflow-y: auto;

  :deep(h1), :deep(h2), :deep(h3) {
    margin-top: 1.5em;
    margin-bottom: 0.5em;
    font-weight: 600;
    color: var(--pc-text-primary);
  }
  :deep(h1:first-child), :deep(h2:first-child), :deep(h3:first-child) {
    margin-top: 0;
  }
  :deep(h1) { font-size: 1.5rem; }
  :deep(h2) { font-size: 1.25rem; }
  :deep(h3) { font-size: 1.1rem; }
  :deep(p) { margin: 0.75em 0; }
  :deep(ul), :deep(ol) { margin: 0.5em 0; padding-left: 1.5em; }
  :deep(code) {
    padding: 2px 6px;
    background: rgba(var(--pc-primary-rgb), 0.08);
    border-radius: 4px;
    font-family: 'SF Mono', 'Consolas', monospace;
    font-size: 0.9em;
  }
  :deep(pre) {
    margin: 1em 0;
    padding: 16px;
    background: rgba(0, 0, 0, 0.2);
    border: 1px solid var(--pc-glass-border);
    border-radius: 8px;
    overflow-x: auto;
  }
  :deep(pre code) { padding: 0; background: none; }
  :deep(blockquote) {
    margin: 1em 0;
    padding-left: 1em;
    border-left: 3px solid var(--pc-primary);
    color: var(--pc-text-secondary);
  }
}

// ── Content Card (rendered markdown / table) ──
.file-memory-card {
  background: var(--pc-glass-bg) !important;
  border: 1px solid var(--pc-glass-border) !important;
  border-radius: var(--pc-radius-lg) !important;
  backdrop-filter: var(--pc-glass-blur);

  :deep(.el-card__body) {
    background: transparent;
  }
}

.file-memory-rendered {
  padding: 24px;
  color: var(--pc-text-primary);
  line-height: 1.7;
  font-size: 14px;

  :deep(h1), :deep(h2), :deep(h3) {
    margin-top: 1.5em;
    margin-bottom: 0.5em;
    font-weight: 600;
    color: var(--pc-text-primary);
  }
  :deep(h1:first-child), :deep(h2:first-child), :deep(h3:first-child) {
    margin-top: 0;
  }
  :deep(h1) { font-size: 1.5rem; }
  :deep(h2) { font-size: 1.25rem; }
  :deep(h3) { font-size: 1.1rem; }
  :deep(p) { margin: 0.75em 0; }
  :deep(ul), :deep(ol) { margin: 0.5em 0; padding-left: 1.5em; }
  :deep(code) {
    padding: 2px 6px;
    background: rgba(var(--pc-primary-rgb), 0.08);
    border-radius: 4px;
    font-family: 'SF Mono', 'Consolas', monospace;
    font-size: 0.9em;
  }
  :deep(pre) {
    margin: 1em 0;
    padding: 16px;
    background: rgba(0, 0, 0, 0.2);
    border: 1px solid var(--pc-glass-border);
    border-radius: 8px;
    overflow-x: auto;
  }
  :deep(pre code) { padding: 0; background: none; }
  :deep(blockquote) {
    margin: 1em 0;
    padding-left: 1em;
    border-left: 3px solid var(--pc-primary);
    color: var(--pc-text-secondary);
  }
}

/* Danger button */
.pc-glow-btn.danger {
  background: rgba(239, 68, 68, 0.08);
  color: var(--pc-accent-red);
  border: 1px solid rgba(239, 68, 68, 0.2);
}
.pc-glow-btn.danger:hover {
  background: rgba(239, 68, 68, 0.15);
  box-shadow: 0 0 12px rgba(239, 68, 68, 0.2);
}

/* Markdown tools */
.markdown-tools {
  display: flex;
  gap: 4px;
  padding: 6px 8px;
  margin-bottom: 12px;
  background: var(--pc-glass-bg);
  border: 1px solid var(--pc-glass-border);
  border-radius: 8px;
}

.tool-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--pc-text-secondary);
  cursor: pointer;
  transition: all 0.2s;
  font-size: 14px;
}
.tool-btn:hover {
  background: rgba(var(--pc-primary-rgb), 0.1);
  color: var(--pc-primary);
}
.tool-code {
  font-family: monospace;
  font-size: 14px;
}

.error-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  margin-bottom: 12px;
  background: rgba(239, 68, 68, 0.1);
  border: 1px solid rgba(239, 68, 68, 0.25);
  border-radius: 8px;
  color: var(--pc-accent-red);
  font-size: 13px;
}

.editor-panes {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1px;
  border: 1px solid var(--pc-glass-border);
  border-radius: var(--pc-radius-lg);
  overflow: hidden;
  background: var(--pc-glass-border);
  min-height: 400px;
  max-height: 55vh;
}

.editor-pane, .preview-pane {
  display: flex;
  flex-direction: column;
  background: var(--pc-glass-bg);
  overflow: hidden;
}

.pane-header {
  padding: 10px 16px;
  font-size: 13px;
  font-weight: 600;
  color: var(--pc-text-secondary);
  border-bottom: 1px solid var(--pc-glass-border);
  background: rgba(0, 0, 0, 0.1);
}

.editor-textarea {
  flex: 1;
  padding: 16px;
  border: none;
  background: transparent;
  color: var(--pc-text-primary);
  font-family: 'SF Mono', 'Consolas', monospace;
  font-size: 14px;
  line-height: 1.7;
  resize: none;
  outline: none;
}
.editor-textarea::placeholder {
  color: var(--pc-text-muted);
}

.preview-content {
  flex: 1;
  padding: 16px;
  overflow-y: auto;
  color: var(--pc-text-primary);
  line-height: 1.7;
  font-size: 14px;

  :deep(h1), :deep(h2), :deep(h3) {
    margin-top: 1.2em;
    margin-bottom: 0.4em;
    font-weight: 600;
  }
  :deep(h1) { font-size: 1.4rem; }
  :deep(h2) { font-size: 1.15rem; }
  :deep(h3) { font-size: 1.05rem; }
  :deep(p) { margin: 0.6em 0; }
  :deep(ul), :deep(ol) { padding-left: 1.5em; }
  :deep(code) {
    padding: 1px 5px;
    background: rgba(var(--pc-primary-rgb), 0.08);
    border-radius: 4px;
    font-family: 'SF Mono', 'Consolas', monospace;
    font-size: 0.88em;
  }
  :deep(pre) {
    margin: 0.8em 0;
    padding: 12px;
    background: rgba(0, 0, 0, 0.2);
    border: 1px solid var(--pc-glass-border);
    border-radius: 6px;
    overflow-x: auto;
  }
  :deep(pre code) { padding: 0; background: none; }
  :deep(blockquote) {
    margin: 0.8em 0;
    padding-left: 0.8em;
    border-left: 3px solid var(--pc-primary);
    color: var(--pc-text-secondary);
  }
}

.empty-preview {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--pc-text-muted);
  font-style: italic;
}

</style>
