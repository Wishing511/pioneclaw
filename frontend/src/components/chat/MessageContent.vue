<template>
  <div class="message-content" v-html="formattedContent" />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

interface Props {
  content: string
}

const props = defineProps<Props>()

const formattedContent = computed(() => {
  if (!props.content) return ''

  // 修复 LLM 常见格式问题
  let text = props.content

  // 修复以空格开头的行（markdown 代码块问题）
  text = text.replace(/^ +```/gm, '```')

  // 修复缺少换行的列表
  text = text.replace(/^(\d+\.)\s+/gm, '$1 ')

  // 修复连续换行
  text = text.replace(/\n{3,}/g, '\n\n')

  // marked 解析
  let html = marked.parse(text, { breaks: true, gfm: true }) as string

  // 修复代码块高亮
  html = html.replace(
    /<pre><code class="language-(\w+)">/g,
    '<pre class="hljs"><code class="language-$1">'
  )

  // DOMPurify 消毒
  html = DOMPurify.sanitize(html, {
    ALLOWED_TAGS: [
      'p', 'br', 'strong', 'em', 'u', 's', 'code', 'pre', 'blockquote',
      'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
      'ul', 'ol', 'li',
      'a', 'img', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
      'div', 'span', 'hr',
    ],
    ALLOWED_ATTR: [
      'href', 'title', 'target', 'rel', 'src', 'alt', 'class', 'id',
    ],
  })

  return html
})
</script>

<style scoped lang="scss">
.message-content {
  line-height: 1.6;
  word-break: break-word;

  :deep(pre) {
    background: var(--el-fill-color);
    border-radius: 6px;
    padding: 12px;
    overflow-x: auto;
    margin: 8px 0;

    code {
      font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
      font-size: 13px;
      background: transparent;
      padding: 0;
    }
  }

  :deep(code) {
    background: var(--el-fill-color);
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 13px;
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  }

  :deep(blockquote) {
    border-left: 3px solid var(--el-border-color);
    padding-left: 12px;
    margin: 8px 0;
    color: var(--el-text-color-secondary);
  }

  :deep(table) {
    border-collapse: collapse;
    margin: 8px 0;
    width: 100%;

    th, td {
      border: 1px solid var(--el-border-color);
      padding: 8px 12px;
      text-align: left;
    }

    th {
      background: var(--el-fill-color);
      font-weight: 600;
    }
  }

  :deep(ul), :deep(ol) {
    padding-left: 20px;
    margin: 8px 0;
  }

  :deep(li) {
    margin: 4px 0;
  }

  :deep(a) {
    color: var(--el-color-primary);
    text-decoration: none;

    &:hover {
      text-decoration: underline;
    }
  }

  :deep(img) {
    max-width: 100%;
    border-radius: 6px;
    margin: 8px 0;
  }

  :deep(h1), :deep(h2), :deep(h3), :deep(h4) {
    margin: 16px 0 8px;
    font-weight: 600;
  }

  :deep(h1) { font-size: 20px; }
  :deep(h2) { font-size: 18px; }
  :deep(h3) { font-size: 16px; }
  :deep(h4) { font-size: 14px; }

  :deep(p) {
    margin: 8px 0;
  }

  :deep(hr) {
    border: none;
    border-top: 1px solid var(--el-border-color);
    margin: 16px 0;
  }
}
</style>
