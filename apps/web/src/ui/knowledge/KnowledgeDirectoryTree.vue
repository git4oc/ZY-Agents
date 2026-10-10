<template>
  <aside class="directories" :class="{ collapsed }">
    <div v-if="collapsed" class="collapsed-nav">
      <n-button text size="small" :title="t('knowledge.expand_directories')" :aria-label="t('knowledge.expand_directories')" @click="collapsed=false"><template #icon><n-icon><ChevronForwardOutline /></n-icon></template></n-button>
      <span>{{ t('knowledge.directory') }}</span>
    </div>
    <template v-else>
    <div class="directory-head"><strong>{{ t('knowledge.personal_directories') }}</strong><div class="head-actions"><n-button text size="small" :title="t('knowledge.create_root_directory')" :aria-label="t('knowledge.create_root_directory')" @click="createAt('')"><template #icon><n-icon><AddOutline /></n-icon></template></n-button><n-button text size="small" :title="t('knowledge.collapse_directories')" :aria-label="t('knowledge.collapse_directories')" @click="collapsed=true"><template #icon><n-icon><ChevronBackOutline /></n-icon></template></n-button></div></div>
    <button class="directory-row special" :class="{ active: !sharedActive && modelValue === 'all' }" @click="emit('update:modelValue', 'all')"><span class="nav-label"><n-icon><LibraryOutline /></n-icon><span>{{ t('knowledge.all') }}</span></span></button>
    <button class="directory-row special" :class="{ active: sharedActive }" @click="emit('selectShared')">
      <span class="nav-label"><n-icon><ShareSocialOutline /></n-icon><span>{{ t('knowledge.shared_with_me') }}</span></span>
      <span v-if="sharedUnreadCount" class="shared-unread">{{ sharedUnreadCount > 99 ? '99+' : sharedUnreadCount }}</span>
      <span v-else-if="sharedTotalCount" class="shared-total">{{ sharedTotalCount > 99 ? '99+' : sharedTotalCount }}</span>
    </button>
    <div class="directory-separator"></div>
    <div class="directory-tree-scroll">
      <template v-for="node in flatNodes" :key="node.id">
        <div class="directory-line" :style="{ marginLeft: `${node.depth * 20}px` }">
          <button class="toggle" :class="{ hidden: !node.children.length }" :aria-label="t(expanded.has(node.id) ? 'knowledge.collapse_directories' : 'knowledge.expand_directories')" @click="toggle(node.id)"><n-icon><ChevronDownOutline v-if="expanded.has(node.id)"/><ChevronForwardOutline v-else/></n-icon></button>
          <button class="directory-row grow" :class="{ active: !sharedActive && modelValue === node.id }" @click="emit('update:modelValue', node.id)"><span class="directory-name"><n-icon><FolderOpenOutline v-if="expanded.has(node.id)"/><FolderOutline v-else/></n-icon><span>{{ node.name }}</span></span><span class="directory-count">{{ node.count }}</span></button>
          <n-dropdown trigger="click" :options="actions" @select="(key: string) => act(key, node)"><n-button class="directory-actions" text size="tiny">•••</n-button></n-dropdown>
        </div>
      </template>
      <button class="directory-row uncategorized" :class="{ active: !sharedActive && modelValue === '' }" @click="emit('update:modelValue', '')"><span class="nav-label"><n-icon><FileTrayOutline /></n-icon><span>{{ t('knowledge.uncategorized') }}</span></span><span>{{ rootCount }}</span></button>
    </div>
    <n-button dashed block size="small" class="create" @click="createAt(modelValue === 'all' ? '' : modelValue)"><template #icon><n-icon><AddOutline /></n-icon></template>{{ t('knowledge.create_directory') }}</n-button>
    </template>
  </aside>
</template>
<script setup lang="ts">
import { computed, h, ref, watch } from 'vue'
import { NButton, NDropdown, NIcon, useDialog } from 'naive-ui'
import AddOutline from '@vicons/ionicons5/es/AddOutline'
import ChevronBackOutline from '@vicons/ionicons5/es/ChevronBackOutline'
import ChevronDownOutline from '@vicons/ionicons5/es/ChevronDownOutline'
import ChevronForwardOutline from '@vicons/ionicons5/es/ChevronForwardOutline'
import CreateOutline from '@vicons/ionicons5/es/CreateOutline'
import FileTrayOutline from '@vicons/ionicons5/es/FileTrayOutline'
import FolderOpenOutline from '@vicons/ionicons5/es/FolderOpenOutline'
import FolderOutline from '@vicons/ionicons5/es/FolderOutline'
import LibraryOutline from '@vicons/ionicons5/es/LibraryOutline'
import MoveOutline from '@vicons/ionicons5/es/MoveOutline'
import ShareSocialOutline from '@vicons/ionicons5/es/ShareSocialOutline'
import TrashOutline from '@vicons/ionicons5/es/TrashOutline'
import type { KnowledgeDirectory } from '../../api/personalKnowledge'
import { t } from '../../hooks/i18n'
const props = defineProps<{ modelValue: string; sharedActive: boolean; sharedUnreadCount: number; sharedTotalCount?: number; items: KnowledgeDirectory[]; rootCount: number }>()
const emit = defineEmits<{ 'update:modelValue': [id: string]; selectShared: []; create: [parentId: string]; rename: [item: KnowledgeDirectory]; move: [item: KnowledgeDirectory]; remove: [item: KnowledgeDirectory] }>()
const dialog = useDialog()
const collapsed = ref(false)
const expanded = ref(new Set<string>())
const flatNodes = computed(() => { const out: Array<KnowledgeDirectory & { depth: number }> = []; const walk = (rows: KnowledgeDirectory[], depth: number) => rows.forEach(row => { out.push({ ...row, depth }); if (expanded.value.has(row.id)) walk(row.children || [], depth + 1) }); walk(props.items, 0); return out })
const menuIcon = (icon: any) => () => h(NIcon, null, { default: () => h(icon) })
const actions = computed(() => [{ label: t('knowledge.create_subdirectory'), key: 'child', icon: menuIcon(AddOutline) }, { label: t('knowledge.rename_directory'), key: 'rename', icon: menuIcon(CreateOutline) }, { label: t('knowledge.move_directory'), key: 'move', icon: menuIcon(MoveOutline) }, { label: t('knowledge.delete'), key: 'remove', icon: menuIcon(TrashOutline) }])
function createAt(parentId: string) { emit('create', parentId) }
function toggle(id:string){const next=new Set(expanded.value);next.has(id)?next.delete(id):next.add(id);expanded.value=next}
function act(key: string, item: KnowledgeDirectory) {
  if (key === 'child') emit('create', item.id)
  else if (key === 'rename') emit('rename', item)
  else if (key === 'move') emit('move', item)
  else dialog.warning({ title: t('knowledge.delete_directory'), content: t('knowledge.delete_directory_confirm',{name:item.name}), positiveText: t('knowledge.delete'), negativeText: t('knowledge.cancel'), onPositiveClick: () => emit('remove', item) })
}
watch(() => props.items, rows => { const next=new Set(expanded.value); const collect=(items:KnowledgeDirectory[])=>items.forEach(row=>{next.add(row.id);collect(row.children||[])});collect(rows);expanded.value=next }, { immediate:true })
</script>
<style scoped>
.directories{box-sizing:border-box;width:270px;max-height:100%;min-height:0;align-self:stretch;flex:0 0 270px;display:flex;flex-direction:column;padding:14px;overflow:hidden;border:1px solid #e6ebf5;border-radius:14px;background:#fff;box-shadow:0 6px 20px rgba(16,38,84,.05);transition:width .18s ease,flex-basis .18s ease,padding .18s ease}.directories.collapsed{width:50px;flex-basis:50px;padding:12px 8px}.collapsed-nav{display:flex;height:100%;align-items:center;flex-direction:column;gap:14px}.collapsed-nav span{color:#7a8699;font-size:12px;letter-spacing:3px;writing-mode:vertical-rl}.directory-head,.directory-line{display:flex;align-items:center;justify-content:space-between}.directory-head{flex:0 0 auto;padding:2px 6px 10px}.head-actions{display:flex;align-items:center;gap:4px}.directory-row{display:flex;width:100%;min-width:0;align-items:center;justify-content:space-between;gap:8px;border:0;border-radius:8px;padding:8px 10px;background:transparent;color:#4b5565;text-align:left;cursor:pointer}.directory-row:hover,.directory-row.active{background:#eaf0ff;color:#2459e8}.directory-row.special{flex:0 0 auto;font-weight:650}.nav-label,.directory-name{display:flex;min-width:0;align-items:center;gap:5px}.nav-label .n-icon,.directory-name .n-icon{width:18px;height:18px;flex:0 0 18px;font-size:18px}.nav-label span,.directory-name span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.shared-unread{display:inline-grid;min-width:18px;height:18px;flex:0 0 auto;place-items:center;padding:0 5px;border-radius:9px;background:#e5484d;color:#fff;font-size:10px;font-weight:700;line-height:1}.shared-total{display:inline-grid;min-width:18px;height:18px;flex:0 0 auto;place-items:center;padding:0 5px;border-radius:9px;background:#e5e9f1;color:#4b5565;font-size:10px;font-weight:700;line-height:1}.directory-row.uncategorized{margin-top:8px;border-top:1px solid #e6ebf3;border-radius:0 0 8px 8px;padding-top:12px}.directory-separator{height:1px;flex:0 0 auto;margin:8px 6px;background:#e6ebf3}.directory-tree-scroll{min-height:0;flex:1 1 0;overflow-y:auto;overflow-x:hidden;padding-right:6px;scrollbar-gutter:stable;scrollbar-width:thin;scrollbar-color:#aeb8c8 transparent}.directory-tree-scroll::-webkit-scrollbar{width:8px}.directory-tree-scroll::-webkit-scrollbar-track{background:transparent}.directory-tree-scroll::-webkit-scrollbar-thumb{min-height:36px;border:2px solid #fff;border-radius:6px;background:#aeb8c8}.directory-line{position:relative;gap:0;min-width:0}.directory-line::before{content:"";position:absolute;left:10px;top:0;bottom:0;border-left:1px solid #e1e6ef}.directory-actions{position:absolute;z-index:2;right:0;top:50%;opacity:0;visibility:hidden;transform:translateY(-50%);background:#fff;transition:opacity .15s ease}.directory-line:hover .directory-actions,.directory-line:focus-within .directory-actions{opacity:1;visibility:visible}.directory-count{flex:0 0 auto;transition:opacity .1s ease}.directory-line:hover .directory-count,.directory-line:focus-within .directory-count{opacity:0}.grow{min-width:0}.toggle{position:relative;z-index:1;display:grid;width:20px;height:28px;flex:0 0 20px;place-items:center;border:0;background:#fff;color:#7a8699;cursor:pointer}.toggle .n-icon{font-size:15px}.toggle.hidden{visibility:hidden}.create{flex:0 0 auto;margin-top:12px}
</style>
