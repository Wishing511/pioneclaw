<template>
  <div class="chat-input-area">
    <div class="input-wrapper">
      <el-input
        v-model="localInput"
        type="textarea"
        :rows="1"
        :autosize="{ minRows: 1, maxRows: 6 }"
        :placeholder="placeholder"
        :disabled="disabled"
        @keydown.enter.prevent="handleEnter"
        @input="handleInput"
      />
      <el-button
        class="send-btn"
        type="primary"
        :disabled="!canSend || disabled"
        @click="handleSend"
      >
        <el-icon><Promotion /></el-icon>
      </el-button>
    </div>
    <div v-if="showSlashCommands" class="slash-commands">
      <div
        v-for="cmd in filteredCommands"
        :key="cmd.command"
        class="slash-item"
        @click="selectCommand(cmd)"
      >
        <span class="cmd-name">{{ cmd.command }}</span>
        <span class="cmd-desc">{{ cmd.description }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { Promotion } from '@element-plus/icons-vue'

interface SlashCommand {
  command: string
  description: string
}

interface Props {
  modelValue: string
  placeholder?: string
  disabled?: boolean
  canSend?: boolean
  slashCommands?: SlashCommand[]
}

const props = withDefaults(defineProps<Props>(), {
  placeholder: '输入消息...',
  disabled: false,
  canSend: true,
  slashCommands: () => [],
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
  send: []
  command: [command: string]
}>()

const localInput = ref(props.modelValue)
const showSlashCommands = ref(false)

watch(
  () => props.modelValue,
  (val) => {
    localInput.value = val
  }
)

watch(localInput, (val) => {
  emit('update:modelValue', val)
  showSlashCommands.value = val.startsWith('/') && props.slashCommands.length > 0
})

const filteredCommands = computed(() => {
  const text = localInput.value.slice(1).toLowerCase()
  return props.slashCommands.filter((cmd) =>
    cmd.command.toLowerCase().includes(text)
  )
})

function handleEnter(e: KeyboardEvent) {
  if (e.shiftKey) return
  if (!props.canSend || props.disabled) return
  handleSend()
}

function handleSend() {
  if (!localInput.value.trim()) return
  emit('send')
  showSlashCommands.value = false
}

function handleInput() {
  // 可扩展：输入建议、@提及等
}

function selectCommand(cmd: SlashCommand) {
  localInput.value = cmd.command + ' '
  emit('update:modelValue', localInput.value)
  showSlashCommands.value = false
}
</script>

<style scoped lang="scss">
.chat-input-area {
  padding: 12px 16px;
  border-top: 1px solid var(--pc-border);
  background: var(--pc-bg);
}

.input-wrapper {
  display: flex;
  gap: 8px;
  align-items: flex-end;

  :deep(.el-textarea__inner) {
    resize: none;
    border-radius: 12px;
    padding: 10px 14px;
  }
}

.send-btn {
  flex-shrink: 0;
  height: 40px;
  width: 40px;
  border-radius: 12px;
  padding: 0;
}

/* ─── 斜杠命令 ─── */
.slash-commands {
  position: absolute;
  bottom: 100%;
  left: 16px;
  right: 16px;
  background: var(--pc-bg-elevated);
  border: 1px solid var(--pc-border);
  border-radius: 8px;
  padding: 4px;
  margin-bottom: 4px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  max-height: 200px;
  overflow-y: auto;
  z-index: 100;
}

.slash-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;

  &:hover {
    background: rgba(var(--pc-primary-rgb), 0.08);
  }
}

.cmd-name {
  font-weight: 600;
  color: var(--el-color-primary);
}

.cmd-desc {
  color: var(--pc-text-muted);
  font-size: 12px;
}
</style>
