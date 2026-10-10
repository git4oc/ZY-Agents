<template>
  <div class="ask-panel">
    <p class="ask-hint">{{ t('knowledge.ask_hint') }}</p>
    <n-input
      v-model:value="query"
      type="textarea"
      :rows="3"
      :placeholder="t('knowledge.ask_placeholder')"
      :disabled="asking"
    />
    <div class="ask-actions">
      <n-button type="primary" size="small" :loading="asking" :disabled="!canSubmit" @click="submit">
        {{ t('knowledge.ask_submit') }}
      </n-button>
    </div>
    <div v-if="answer" class="ask-answer">
      <div class="ask-answer-title">{{ t('knowledge.ask_answer') }}</div>
      <div class="ask-answer-body">{{ answer }}</div>
    </div>
    <div v-if="citations.length" class="ask-citations">
      <div class="ask-citations-title">{{ t('knowledge.ask_citations', { count: citations.length }) }}</div>
      <ol class="ask-citation-list">
        <li v-for="cite in citations" :key="cite.chunkId">
          <strong>{{ cite.documentTitle || t('knowledge.ask_unknown_document') }}</strong>
          <small v-if="cite.headingPath.length">{{ cite.headingPath.join(' / ') }}</small>
        </li>
      </ol>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NButton, NInput, useMessage } from 'naive-ui'
import { askPersonalKnowledge, type KnowledgeAskCitation } from '../../api/personalKnowledge'
import { extractErrorMessage } from '../../api/errorMessage'
import { t } from '../../hooks/i18n'

/** 知识详情页「问一问」面板：全组织知识库检索 + 提供商生成，回答带引用来源。 */
const props = defineProps<{ knowledgeId: string }>()
const message = useMessage()
const query = ref('')
const asking = ref(false)
const answer = ref('')
const citations = ref<KnowledgeAskCitation[]>([])

const canSubmit = computed(() => Boolean(query.value.trim()) && !asking.value)

function reset() {
  query.value = ''
  asking.value = false
  answer.value = ''
  citations.value = []
}

async function submit() {
  const trimmed = query.value.trim()
  if (!trimmed || asking.value) return
  asking.value = true
  try {
    const result = await askPersonalKnowledge(props.knowledgeId, trimmed)
    answer.value = result.answer
    citations.value = result.citations
  } catch (error: any) {
    // 统一错误文案提取：后端 DomainError 统一体（{"error": {...}}）与 detail 纯文本都兜住，
    // 真实原因（问题不能为空 / 问答生成失败: …）优先透出，无则回退通用文案
    message.error(extractErrorMessage(error?.response?.data, t('knowledge.ask_failed')))
  } finally {
    asking.value = false
  }
}

watch(() => props.knowledgeId, reset)
</script>

<style scoped>
.ask-panel{display:flex;flex-direction:column;gap:12px}
.ask-hint{margin:0;color:#8a94a5;font-size:12px;line-height:1.6}
.ask-actions{display:flex;justify-content:flex-end}
.ask-answer{display:flex;flex-direction:column;gap:8px;padding:14px;border:1px solid #edf0f5;border-radius:10px;background:#f7f9fc}
.ask-answer-title{color:#667085;font-size:12px;font-weight:600}
.ask-answer-body{white-space:pre-wrap;word-break:break-word;font-size:13px;line-height:1.7;color:#1f2937}
.ask-citations{display:flex;flex-direction:column;gap:8px}
.ask-citations-title{color:#667085;font-size:12px;font-weight:600}
.ask-citation-list{margin:0;padding-left:20px;display:flex;flex-direction:column;gap:6px}
.ask-citation-list li{font-size:12px;line-height:1.6;color:#344054}
.ask-citation-list strong{font-weight:600;color:#1f2937}
.ask-citation-list small{display:block;color:#8a94a5}
</style>
