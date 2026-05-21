<template>
  <div class="tool-calls-list">
    <ToolCallCard
      v-for="tc in toolCalls"
      :key="tc.id"
      :tool-id="tc.id"
      :tool-name="tc.name"
      :arguments="tc.arguments"
      :status="tc.status || 'pending'"
      :result="tc.result"
      :error="tc.error"
      :duration="tc.duration"
      :progress="tc.progress"
      :progress-message="tc.progressMessage"
    />
  </div>
</template>

<script setup lang="ts">
import type { ChatToolCall } from '@/types/chat'
import ToolCallCard from './ToolCallCard.vue'

interface Props {
  toolCalls: ChatToolCall[]
}

defineProps<Props>()
</script>

<style scoped lang="scss">
.tool-calls-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
</style>
