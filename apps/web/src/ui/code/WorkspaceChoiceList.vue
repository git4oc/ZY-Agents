<script setup lang="ts">
import type { DshWorkspace } from '../../bridge/types'

withDefaults(defineProps<{
  workspaces?: readonly DshWorkspace[]
  currentWorkspaceId?: string
  loading?: boolean
  locale?: 'zh' | 'en'
}>(), { workspaces: () => [], locale: 'zh' })

const emit = defineEmits<{
  (event: 'select', workspace: DshWorkspace): void
  (event: 'add'): void
}>()
</script>

<template>
  <div class="workspace-choices">
    <div class="choice-heading">
      <strong>{{ locale === 'en' ? 'Pick a project' : '选择已有项目' }}</strong>
      <span>{{ locale === 'en' ? 'Each project only has to be added once.' : '项目只需添加一次，后续可直接选择。' }}</span>
    </div>
    <div v-if="loading" class="choice-state">{{ locale === 'en' ? 'Reading projects…' : '正在加载项目…' }}</div>
    <div v-else-if="!workspaces.length" class="choice-state">{{ locale === 'en' ? 'No projects have been added yet.' : '还没有添加过项目。' }}</div>
    <div v-else class="choice-list" role="listbox">
      <button
        v-for="item in workspaces"
        :key="item.workspace_id"
        type="button"
        class="choice-item"
        :class="{ selected: item.workspace_id === currentWorkspaceId }"
        :disabled="item.status !== 'ok'"
        role="option"
        :aria-selected="item.workspace_id === currentWorkspaceId"
        :title="item.path || item.title"
        @click="emit('select', item)"
      >
        <span class="folder-mark"><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 7.5h6l2 2h10v9.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7.5Z"/></svg></span>
        <span class="choice-copy"><strong>{{ item.title }}</strong><small>{{ item.status === 'ok' ? item.path : (locale === 'en' ? 'Directory not available on this device' : '此设备上的目录不可用') }}</small></span>
        <svg v-if="item.workspace_id === currentWorkspaceId" class="check" aria-hidden="true" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2"><path d="m4 10 4 4 8-8"/></svg>
      </button>
    </div>
    <button type="button" class="add-project" @click="emit('add')">
      <span aria-hidden="true">＋</span>{{ locale === 'en' ? 'Add another project…' : '添加新项目…' }}
    </button>
  </div>
</template>

<style scoped>
.workspace-choices{display:flex;flex-direction:column;min-width:0}.choice-heading{display:flex;flex-direction:column;gap:2px;padding:14px 15px 10px}.choice-heading strong{color:#0f172a;font-size:14px}.choice-heading span,.choice-state{color:#94a3b8;font-size:11px}.choice-state{padding:14px 15px 18px}.choice-list{max-height:240px;overflow-y:auto;padding:0 7px 7px}.choice-item{display:flex;width:100%;min-height:52px;align-items:center;gap:10px;border:0;border-radius:10px;background:transparent;padding:7px 8px;text-align:left;color:#334155;cursor:pointer}.choice-item:hover:not(:disabled),.choice-item.selected{background:#eff6ff}.choice-item:disabled{cursor:not-allowed;opacity:.55}.folder-mark{display:grid;width:32px;height:32px;flex:none;place-items:center;border-radius:8px;background:#f1f5f9;color:#64748b}.folder-mark svg{width:17px;height:17px}.choice-item.selected .folder-mark{background:#dbeafe;color:#2563eb}.choice-copy{display:flex;min-width:0;flex:1;flex-direction:column}.choice-copy strong,.choice-copy small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.choice-copy strong{font-size:12px}.choice-copy small{margin-top:2px;color:#94a3b8;font-size:10px}.check{width:17px;height:17px;flex:none;color:#2563eb}.add-project{display:flex;min-height:44px;align-items:center;gap:7px;border:0;border-top:1px solid #eef2f7;background:#fff;padding:8px 15px;color:#2563eb;font-size:12px;text-align:left;cursor:pointer}.add-project:hover{background:#f8fafc}.choice-item:focus-visible,.add-project:focus-visible{outline:2px solid #2563eb;outline-offset:-2px}:global(html.theme-dark) .choice-heading strong{color:#f8fafc}:global(html.theme-dark) .choice-item{color:#cbd5e1}:global(html.theme-dark) .choice-item:hover:not(:disabled),:global(html.theme-dark) .choice-item.selected{background:#172554}:global(html.theme-dark) .folder-mark{background:#1e293b}:global(html.theme-dark) .add-project{border-color:#334155;background:#111827}
</style>
