<template>
  <div class="dialog-body">
    <n-tabs v-if="channels.length > 1" v-model:value="activeId" type="segment" animated>
      <n-tab-pane v-for="channel in channels" :key="channel.id" :name="channel.id">
        <template #tab>{{ channel.role === 'owner' ? t('skills.feedback.shared_feedback') : t('skills.feedback.usage_discussion') }}<span v-if="channel.unreadCount" class="dot"></span></template>
      </n-tab-pane>
    </n-tabs>
    <SkillFeedbackPanel
      v-if="activeChannel"
      :key="activeChannel.id"
      resource-type="skill_distribution"
      :resource-id="activeChannel.id"
      :comment-count="activeChannel.commentCount"
      :unread-count="activeChannel.unreadCount"
      @read="emit('read')"
      @changed="emit('changed')"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NTabPane, NTabs } from 'naive-ui'
import type { SkillItem } from '../../api/skills'
import { t } from '../../hooks/i18n'
import SkillFeedbackPanel from './SkillFeedbackPanel.vue'

const props = defineProps<{ skill: SkillItem }>()
const emit = defineEmits<{ read: []; changed: [] }>()
const channels = computed(() => props.skill.feedback?.channels || [])
const preferred = () => channels.value.find(item => item.unreadCount)?.id || channels.value.find(item => item.role === 'owner')?.id || channels.value[0]?.id || ''
const activeId = ref(preferred())
const activeChannel = computed(() => channels.value.find(item => item.id === activeId.value) || channels.value[0])
watch(() => props.skill.id, () => { activeId.value = preferred() })
</script>

<style scoped>
.dialog-body{display:grid;gap:14px}.dot{display:inline-block;width:6px;height:6px;margin-left:5px;border-radius:50%;background:#e5484d;vertical-align:middle}
</style>
