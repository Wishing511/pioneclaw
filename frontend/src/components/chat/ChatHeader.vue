<template>
  <div class="chat-header">
    <div class="header-left">
      <el-button text @click="$emit('toggle-sidebar')">
        <el-icon><Fold /></el-icon>
      </el-button>
      <span class="chat-title">{{ title || $t('chat.newChat') }}</span>
    </div>
    <div class="header-right">
      <el-button text @click="$emit('clear-chat')">
        <el-icon><Delete /></el-icon>
      </el-button>
      <el-button text @click="$emit('open-settings')">
        <el-icon><Setting /></el-icon>
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Fold, Delete, Setting } from '@element-plus/icons-vue'

interface Props {
  title?: string
}

defineProps<Props>()

defineEmits<{
  'toggle-sidebar': []
  'clear-chat': []
  'open-settings': []
}>()
</script>

<style scoped lang="scss">
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  border-bottom: 1px solid var(--pc-border);
  background: var(--pc-bg);
  flex-shrink: 0;
}

.header-left,
.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.chat-title {
  font-weight: 500;
  font-size: 15px;
  color: var(--pc-text);
}
</style>
