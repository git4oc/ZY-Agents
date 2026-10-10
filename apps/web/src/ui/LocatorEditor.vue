<script setup lang="ts">
// 单个浏览器步骤的精简定位器编辑器（手动录入）。
// 用户可任填 ARIA role / 可访问名称 / 图标类名 / 行锚点中的若干项，
// 运行时按 AND 匹配。字段刻意保持精简，避免界面需要额外培训才能上手。
//
// If a user pastes an HTML snippet into the "从 HTML 提取" box, we
// 这里改为做一次轻量抽取以预填表单——这条路径足够轻，
// "one-shot tool" discussed earlier, kept inline to avoid a separate
// 完整的弹窗仍是另一个更重的入口。

import { computed, ref, watch } from 'vue'
import { NButton, NCollapse, NCollapseItem, NInput, NInputGroup, NInputNumber, NSelect, NSpace } from 'naive-ui'
import type { Locator } from '../api/compositeSkill'
import { t } from '../hooks/i18n'

const props = defineProps<{
  modelValue: Locator | undefined
  // 按操作区分的步骤，用操作名（如 "editing"）作为该分区标题。
  title?: string
}>()
const emit = defineEmits(['update:modelValue'])

const local = ref<Locator>({ ...(props.modelValue || {}) })
const htmlSnippet = ref<string>('')

watch(() => props.modelValue, (v) => { local.value = { ...(v || {}) } }, { deep: true })
watch(local, (v) => {
  const cleaned: Locator = {}
  for (const [k, val] of Object.entries(v)) {
    if (val === undefined || val === '' || val === null) continue
    ;(cleaned as any)[k] = val
  }
  emit('update:modelValue', Object.keys(cleaned).length ? cleaned : undefined)
}, { deep: true })

const roleOptions = [
  { label: '(any)', value: '' },
  { label: 'button', value: 'button' },
  { label: 'link', value: 'link' },
  { label: 'textbox', value: 'textbox' },
  { label: 'combobox', value: 'combobox' },
  { label: 'menuitem', value: 'menuitem' },
  { label: 'checkbox', value: 'checkbox' },
]

// 轻量的本地 HTML → 定位信号抽取器。基于 DOMParser，无外部依赖。
function extractFromHTML() {
  const src = htmlSnippet.value.trim()
  if (!src) return
  try {
    const doc = new DOMParser().parseFromString(src, 'text/html')
    const el = doc.body.firstElementChild as HTMLElement | null
    if (!el) return
    const next: Locator = { ...local.value }
    const tag = el.tagName.toLowerCase()
    const explicit = el.getAttribute('role')?.toLowerCase() || ''
    next.role = explicit || (tag === 'a' ? 'link'
      : tag === 'button' ? 'button'
      : tag === 'input' ? 'textbox'
      : tag === 'textarea' ? 'textbox'
      : tag === 'select' ? 'combobox'
      : next.role || '')
    const aria = el.getAttribute('aria-label') || ''
    if (aria) next.aria_label = aria
    const txt = (el.textContent || '').replace(/\s+/g, ' ').trim()
    if (txt) next.text = txt.slice(0, 120)
    // iconfont 形式的类名识别
    const cls = (el.className || '').toString()
    const iconTok = cls.split(/\s+/).find(t => /^(icon|ic|anticon|el-icon)[-_]/i.test(t))
    if (iconTok) next.icon_class = iconTok
    if (!iconTok) {
      // 扫描单子节点图标（<i class="iconfont xxx"></i>）
      const child = el.querySelector('[class*="icon"]')
      if (child) {
        const tok = (child.className || '').toString()
          .split(/\s+/).find(t => /^(icon|ic|anticon|el-icon)[-_]/i.test(t))
        if (tok) next.icon_class = tok
      }
    }
    local.value = next
  } catch { /* bad HTML snippet */ }
}

const hasAny = computed(() => Object.values(local.value).some(v => v !== undefined && v !== ''))

function clearAll() { local.value = {} }
</script>

<template>
  <n-collapse>
    <n-collapse-item :title="title || t('locator.title')" :name="'loc'">
      <div class="space-y-2">
        <n-input-group>
          <n-select
            v-model:value="local.role"
            :options="roleOptions"
            size="small"
            style="width: 130px"
            placeholder="role"
          />
          <n-input v-model:value="local.name" size="small" :placeholder="t('locator.exact_match')" />
        </n-input-group>

        <n-input-group>
          <n-input v-model:value="local.name_contains" size="small" :placeholder="t('locator.substring_match')" />
          <n-input v-model:value="local.icon_class" size="small" :placeholder="t('locator.icon_class')" />
        </n-input-group>

        <n-input-group>
          <n-input v-model:value="local.aria_label" size="small" placeholder="aria-label" />
          <n-input v-model:value="local.text" size="small" placeholder="visible text" />
        </n-input-group>

        <n-input-group>
          <n-input
            v-model:value="local.ancestor_contains_text"
            size="small"
            :placeholder="t('locator.ancestor_text')"
          />
          <n-input-number
            v-model:value="local.nth"
            size="small"
            :min="0"
            placeholder="nth"
            style="width: 100px"
          />
        </n-input-group>

        <div class="border-t border-dashed border-gray-200 pt-2 mt-2">
          <div class="text-xs text-gray-500 mb-1">{{ t('locator.auto_fill_desc') }}</div>
          <n-input
            v-model:value="htmlSnippet"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 4 }"
            :placeholder="t('locator.auto_fill_placeholder')"
          />
          <n-space :size="6" class="mt-1">
            <n-button size="tiny" @click="extractFromHTML">{{ t('locator.btn_extract') }}</n-button>
            <n-button size="tiny" quaternary :disabled="!hasAny" @click="clearAll">{{ t('locator.btn_clear') }}</n-button>
          </n-space>
        </div>
      </div>
    </n-collapse-item>
  </n-collapse>
</template>
