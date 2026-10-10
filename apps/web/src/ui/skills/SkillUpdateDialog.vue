<template>
  <n-modal :show="show" preset="card" :title="t('skills.update.confirm_title')" style="width:560px" :mask-closable="!busy" @update:show="emit('update:show', $event)">
    <div v-if="item" class="update-details">
      <div class="skill-heading">
        <strong>{{ item.name }}</strong>
        <span>{{ t('skills.update.confirm_hint') }}</span>
      </div>

      <section class="version-card">
        <div><small>{{ t('skills.update.current_version') }}</small><strong>v{{ item.currentVersion || '-' }}</strong></div>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M5 12h14m-5-5 5 5-5 5" /></svg>
        <div><small>{{ t('skills.update.new_version') }}</small><strong>v{{ item.newVersion || '-' }}</strong></div>
      </section>

      <section class="detail-section">
        <h3>{{ t('skills.update.changes_title') }}</h3>
        <div class="change-tags">
          <n-tag v-for="change in item.changes" :key="change" :bordered="false" type="info">{{ t(`skills.update.change.${change}`) }}</n-tag>
        </div>
      </section>

      <section class="detail-section">
        <h3>{{ t('skills.update.notes_title') }}</h3>
        <p :class="{ muted: !item.releaseNotes }">{{ item.releaseNotes || t('skills.update.no_notes') }}</p>
      </section>

      <n-alert v-if="item.localModified" type="warning" :title="t('skills.update.local_title')">{{ t('skills.update.local_changes') }}</n-alert>
    </div>
    <template #footer>
      <n-space justify="end">
        <n-button :disabled="busy" @click="emit('update:show', false)">{{ t('ui.cancel') }}</n-button>
        <n-button v-if="item" type="primary" :loading="busy" @click="emit('confirm', item)">{{ item.localModified ? t('skills.update.replace') : t('skills.update.confirm') }}</n-button>
      </n-space>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { NAlert, NButton, NModal, NSpace, NTag } from 'naive-ui'
import type { SkillUpdateNotice } from '../../api/skillSharing'
import { t } from '../../hooks/i18n'

defineProps<{ show: boolean; item: SkillUpdateNotice | null; busy: boolean }>()
const emit = defineEmits<{ 'update:show': [value: boolean]; confirm: [item: SkillUpdateNotice] }>()
</script>

<style scoped>
.update-details{display:grid;gap:18px}.skill-heading{display:grid;gap:4px}.skill-heading strong{color:#17233d;font-size:19px}.skill-heading span{color:#8491a7;font-size:13px}.version-card{display:grid;grid-template-columns:1fr 30px 1fr;align-items:center;gap:12px;padding:15px 18px;border:1px solid #dce5f5;border-radius:12px;background:#f7f9fd}.version-card>div{display:grid;gap:5px}.version-card>div:last-child{text-align:right}.version-card small{color:#8491a7}.version-card strong{color:#2459e8;font-size:17px}.version-card svg{width:22px;color:#98a2b3}.detail-section{display:grid;gap:9px}.detail-section h3{margin:0;color:#344054;font-size:14px}.detail-section p{margin:0;padding:12px 14px;border-radius:10px;background:#f7f8fa;color:#52627d;line-height:1.65;white-space:pre-wrap}.detail-section p.muted{color:#98a2b3}.change-tags{display:flex;flex-wrap:wrap;gap:8px}
</style>
