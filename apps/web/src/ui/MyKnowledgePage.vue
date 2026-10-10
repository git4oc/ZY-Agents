<template>
  <div class="page-stack knowledge-page">
    <div class="body">
      <KnowledgeDirectoryTree :model-value="directoryId" :shared-active="view==='shared'" :shared-unread-count="sharedUnreadCount" :shared-total-count="sharedTotalCount" :items="directories" :root-count="rootCount" @update:model-value="selectDirectory" @select-shared="selectShared" @create="createDirectory" @rename="renameDirectory" @move="showMoveDirectory" @remove="removeDirectory" />
      <main class="content" @dragover.prevent="dragActive=true" @dragleave.self="dragActive=false" @drop.prevent="pageDrop">
          <div class="breadcrumb">{{ currentBreadcrumb }}</div>
          <div class="toolbar"><n-input v-model:value="keyword" clearable :placeholder="t('knowledge.search_placeholder')" @keyup.enter="searchResources" @clear="searchResources"/><n-button secondary @click="loadAll"><template #icon><n-icon><RefreshOutline /></n-icon></template>{{ t('knowledge.refresh') }}</n-button><n-button v-if="view==='mine'" type="primary" @click="uploadOpen=true"><template #icon><n-icon><CloudUploadOutline /></n-icon></template>{{ t('knowledge.upload') }}</n-button></div>
          <div v-if="dragActive&&view==='mine'" class="drop-mask">{{ t('knowledge.drop_here') }}</div>
          <div class="list-scroll">
            <n-spin :show="loading">
              <div v-if="items.length" class="knowledge-list">
                <div class="list-head">
                  <span>{{ t('knowledge.name') }}</span><span>{{ t('knowledge.status') }}</span><span class="context-column">{{ t(view === 'mine' ? 'knowledge.directory_column' : 'knowledge.share_source') }}</span><span>{{ t('knowledge.updated_at') }}</span><span class="actions-column">{{ t('knowledge.actions') }}</span>
                </div>
                <article v-for="item in items" :key="item.id" class="knowledge-row" :class="{ revoked: item.accessStatus === 'revoked' || item.accessStatus === 'deleted' }" @click="open(item)">
                  <div class="knowledge-main">
                    <div class="knowledge-title">
                      <strong :title="item.name">{{ item.name }}</strong>
                      <n-tag v-if="view==='mine'&&item.share?.shared" size="tiny" type="info" :bordered="false" class="shared-tag" :title="t('knowledge.shared_count',{count:item.share.recipientCount})">{{ t('knowledge.shared') }}</n-tag>
                      <span
                        v-if="hasUnreadMarker(item)"
                        class="unread-comment-dot"
                        :title="unreadMarkerText(item)"
                        :aria-label="unreadMarkerText(item)"
                      ></span>
                    </div>
                    <span :title="item.description || t('knowledge.no_description')">{{ item.description || t('knowledge.no_description') }}</span>
                    <div v-if="item.tags.length" class="row-tags"><n-tag v-for="tag in item.tags" :key="tag" size="tiny" :bordered="false">{{ tag }}</n-tag></div>
                  </div>
                  <div><KnowledgeLearningStatus :status="item.status" :revoked="item.accessStatus==='revoked'" :deleted="item.accessStatus==='deleted'" :retryable="view==='mine'" @retry="retryFromStatus(item)" /></div>
                  <div class="context-column context-text">
                    <template v-if="view === 'shared'">{{ item.grantedBy?.displayName || item.grantedBy?.username || t('knowledge.colleague') }} · {{ t(item.canReshare ? 'knowledge.can_reshare' : 'knowledge.cannot_reshare') }}</template>
                    <template v-else>{{ directoryName(item.directoryId) }}</template>
                  </div>
                  <time>{{ formatTime(item.updatedAt) }}</time>
                  <div class="row-actions" @click.stop>
                    <n-button v-if="view==='mine'" text @click="showMoveKnowledge(item)"><template #icon><n-icon><MoveOutline /></n-icon></template>{{ t('knowledge.move') }}</n-button>
                    <n-button v-if="view==='mine'" text type="error" @click="remove(item)"><template #icon><n-icon><TrashOutline /></n-icon></template>{{ t('knowledge.delete') }}</n-button>
                    <n-button v-if="view==='shared'&&item.accessStatus==='deleted'" text type="error" @click="dismissDeleted(item)"><template #icon><n-icon><TrashOutline /></n-icon></template>{{ t('knowledge.dismiss_deleted') }}</n-button>
                  </div>
                </article>
              </div>
              <div v-else-if="!loading" class="empty-state"><n-empty :description="t(view==='mine'?'knowledge.empty_mine':'knowledge.empty_shared')" /></div>
            </n-spin>
          </div>
          <div v-if="total>0" class="pagination"><span>{{ t('knowledge.total',{count:total}) }}</span><n-pagination v-model:page="page" v-model:page-size="pageSize" :item-count="total" :page-sizes="[12,24,48]" show-size-picker @update:page="()=>loadResources()" @update:page-size="changePageSize" /></div>
      </main>
    </div>
    <KnowledgeUploadDialog v-model:show="uploadOpen" :directory-id="directoryId" @uploaded="loadAll" />
    <KnowledgeDirectoryMoveDialog v-model:show="directoryMoveOpen" :item="movingDirectory" :directories="directories" @moved="loadAll" />
    <KnowledgeResourceMoveDialog v-model:show="resourceMoveOpen" :item="movingResource" :directories="directories" @moved="loadAll" />
    <KnowledgeDetail v-model:show="detailOpen" :item="selected" @changed="handleDetailChanged" />
  </div>
</template>
<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from 'vue'
import { NButton, NEmpty, NIcon, NInput, NPagination, NSpin, NTag, useDialog, useMessage } from 'naive-ui'
import CloudUploadOutline from '@vicons/ionicons5/es/CloudUploadOutline'
import MoveOutline from '@vicons/ionicons5/es/MoveOutline'
import RefreshOutline from '@vicons/ionicons5/es/RefreshOutline'
import TrashOutline from '@vicons/ionicons5/es/TrashOutline'
import { createKnowledgeDirectory, deleteKnowledgeDirectory, deletePersonalKnowledge, dismissDeletedPersonalKnowledge, fetchKnowledgeDirectories, fetchPersonalKnowledge, relearnPersonalKnowledge, updateKnowledgeDirectory, uploadPersonalKnowledge, type KnowledgeDirectory, type PersonalKnowledge } from '../api/personalKnowledge'
import { useStatusPolling } from '../hooks/useStatusPolling'
import { t } from '../hooks/i18n'
import KnowledgeDirectoryTree from './knowledge/KnowledgeDirectoryTree.vue'
import KnowledgeDirectoryMoveDialog from './knowledge/KnowledgeDirectoryMoveDialog.vue'
import KnowledgeResourceMoveDialog from './knowledge/KnowledgeResourceMoveDialog.vue'
import KnowledgeUploadDialog from './knowledge/KnowledgeUploadDialog.vue'
import KnowledgeDetail from './knowledge/KnowledgeDetail.vue'
import KnowledgeLearningStatus from './knowledge/KnowledgeLearningStatus.vue'
import { isKnowledgeProcessingStatus } from './knowledge/knowledgeStatus'
const props=defineProps<{sharedUnreadCount:number;sharedTotalCount?:number;unreadCount:number}>()
const emit=defineEmits<{viewed:[]}>(),message=useMessage(),dialog=useDialog(),view=ref<'mine'|'shared'>('mine'),directoryId=ref('all'),directories=ref<KnowledgeDirectory[]>([]),rootCount=ref(0),items=ref<PersonalKnowledge[]>([]),keyword=ref(''),page=ref(1),pageSize=ref(12),total=ref(0),loading=ref(false),uploadOpen=ref(false),directoryMoveOpen=ref(false),movingDirectory=ref<KnowledgeDirectory|null>(null),resourceMoveOpen=ref(false),movingResource=ref<PersonalKnowledge|null>(null),detailOpen=ref(false),selected=ref<PersonalKnowledge|null>(null),dragActive=ref(false)
const flatDirectories=computed(()=>{const out:Array<KnowledgeDirectory&{depth:number}>=[];const walk=(rows:KnowledgeDirectory[],depth=0)=>rows.forEach(row=>{out.push({...row,depth});walk(row.children||[],depth+1)});walk(directories.value);return out})
function directoryName(id:string){return id ? flatDirectories.value.find(row=>row.id===id)?.name || t('knowledge.directory') : t('knowledge.uncategorized')}
function isDeletedNotice(item:PersonalKnowledge){return view.value==='shared'&&item.accessStatus==='deleted'&&!item.seen}
function isNewShare(item:PersonalKnowledge){return view.value==='shared'&&item.accessStatus==='active'&&!item.seen}
function hasUnreadMarker(item:PersonalKnowledge){return isDeletedNotice(item)||isNewShare(item)||Boolean(item.feedback?.unreadCount)}
function unreadMarkerText(item:PersonalKnowledge){if(isDeletedNotice(item)&&item.feedback?.unreadCount)return t('knowledge.deleted_notice_with_feedback',{count:item.feedback.unreadCount});if(isDeletedNotice(item))return t('knowledge.deleted_notice');if(isNewShare(item)&&item.feedback?.unreadCount)return t('knowledge.new_share_feedback',{count:item.feedback.unreadCount});if(isNewShare(item))return t('knowledge.new_share');return t('knowledge.new_feedback',{count:item.feedback?.unreadCount||0})}
const currentBreadcrumb=computed(()=>{if(view.value==='shared')return t('knowledge.shared_with_me');if(directoryId.value==='all')return`${t('knowledge.mine')} / ${t('knowledge.all')}`;if(directoryId.value==='')return`${t('knowledge.mine')} / ${t('knowledge.uncategorized')}`;const path:string[]=[];const find=(rows:KnowledgeDirectory[]):boolean=>{for(const row of rows){path.push(row.name);if(row.id===directoryId.value||find(row.children||[]))return true;path.pop()}return false};find(directories.value);return`${t('knowledge.mine')} / ${path.join(' / ')||t('knowledge.directory')}`})
async function loadDirectories(){const data=await fetchKnowledgeDirectories();directories.value=data.items;rootCount.value=data.rootCount}
async function loadResources(silent=false){if(!silent)loading.value=true;try{const data=await fetchPersonalKnowledge(view.value,directoryId.value,keyword.value,page.value,pageSize.value);items.value=data.items;if(selected.value){const refreshed=items.value.find(item=>item.id===selected.value?.id);if(refreshed)selected.value=refreshed}total.value=data.total;if(page.value>1&&!items.value.length&&total.value){page.value=Math.max(1,Math.ceil(total.value/pageSize.value));await loadResources(silent);return}if(view.value==='shared')emit('viewed')}catch(error:any){if(!silent)message.error(error?.response?.data?.detail||t('knowledge.load_failed'))}finally{if(!silent)loading.value=false}}
async function loadAll(){await Promise.all([loadDirectories(),loadResources()])}
function searchResources(){page.value=1;void loadResources()}
function changePageSize(){page.value=1;void loadResources()}
function selectDirectory(id:string){view.value='mine';directoryId.value=id;page.value=1;void loadResources()}
function selectShared(){view.value='shared';page.value=1;void loadResources()}
function promptName(title:string,initial='',onOk:(name:string)=>void){const value=ref(initial);dialog.create({title,content:()=>h(NInput,{value:value.value,'onUpdate:value':(next:string)=>value.value=next,placeholder:t('knowledge.directory_name_placeholder')}),positiveText:t('knowledge.confirm'),negativeText:t('knowledge.cancel'),onPositiveClick:()=>{if(!value.value.trim())return false;onOk(value.value.trim())}})}
function createDirectory(parentId:string){promptName(t('knowledge.create_directory'),'',async name=>{await createKnowledgeDirectory(name,parentId);await loadDirectories()})}
function renameDirectory(item:KnowledgeDirectory){promptName(t('knowledge.rename_directory'),item.name,async name=>{await updateKnowledgeDirectory(item.id,name,item.parentId);await loadDirectories()})}
function showMoveDirectory(item:KnowledgeDirectory){movingDirectory.value=item;directoryMoveOpen.value=true}
function showMoveKnowledge(item:PersonalKnowledge){movingResource.value=item;resourceMoveOpen.value=true}
async function removeDirectory(item:KnowledgeDirectory){try{await deleteKnowledgeDirectory(item.id);if(directoryId.value===item.id)directoryId.value='all';await loadAll()}catch{message.error(t('knowledge.directory_not_empty'))}}
function open(item:PersonalKnowledge){selected.value=item;detailOpen.value=true}
async function handleDetailChanged(){await loadResources();emit('viewed')}
function retryFromStatus(item:PersonalKnowledge){if(view.value!=='mine'||item.status!=='failed')return;dialog.warning({title:t('knowledge.relearn_title'),content:t('knowledge.relearn_confirm',{name:item.name}),positiveText:t('knowledge.relearn'),negativeText:t('knowledge.cancel'),onPositiveClick:async()=>{try{await relearnPersonalKnowledge(item.id);message.success(t('knowledge.relearn_submitted'));await loadResources()}catch(error:any){message.error(error?.response?.data?.detail||t('knowledge.relearn_failed'))}}})}
function remove(item:PersonalKnowledge){const recipients=item.share?.recipientCount||0;dialog.warning({title:t('knowledge.delete_title'),content:t(recipients?'knowledge.delete_shared_confirm':'knowledge.delete_confirm',{name:item.name,count:recipients}),positiveText:t('knowledge.delete'),negativeText:t('knowledge.cancel'),onPositiveClick:async()=>{await deletePersonalKnowledge(item.id);await loadAll()}})}
function dismissDeleted(item:PersonalKnowledge){dialog.warning({title:t('knowledge.dismiss_deleted_title'),content:t('knowledge.dismiss_deleted_confirm',{name:item.name}),positiveText:t('knowledge.dismiss_deleted'),negativeText:t('knowledge.cancel'),onPositiveClick:async()=>{try{await dismissDeletedPersonalKnowledge(item.id);message.success(t('knowledge.dismiss_deleted_success'));await loadResources();emit('viewed')}catch(error:any){message.error(error?.response?.data?.detail||t('knowledge.dismiss_deleted_failed'))}}})}
async function pageDrop(event:DragEvent){dragActive.value=false;if(view.value!=='mine')return;const files=Array.from(event.dataTransfer?.files||[]);if(!files.length)return;loading.value=true;try{await uploadPersonalKnowledge(files,directoryId.value==='all'?'':directoryId.value);message.success(t('knowledge.upload_submitted'));await loadAll()}catch{message.error(t('knowledge.upload_failed'))}finally{loading.value=false}}
function formatTime(value:string){return value?new Date(value).toLocaleString():'--'}
const hasProcessingKnowledge=computed(()=>items.value.some(item=>item.accessStatus!=='revoked'&&isKnowledgeProcessingStatus(item.status)))
useStatusPolling({enabled:hasProcessingKnowledge,refresh:()=>loadResources(true)})
watch(()=>props.unreadCount,()=>{void loadResources(true)})
onMounted(loadAll)
</script>
<style scoped>
.knowledge-page{box-sizing:border-box;height:100%;max-height:100%;min-height:0;padding:12px;overflow:hidden;background:#f6f8fc}.body{display:flex;width:100%;height:100%;min-height:0;gap:12px;overflow:hidden}.content{position:relative;display:flex;min-width:0;min-height:0;flex:1;flex-direction:column;padding:18px;overflow:hidden;border:1px solid #e6ebf5;border-radius:14px;background:#fff;box-shadow:0 6px 20px rgba(16,38,84,.05)}.breadcrumb,.toolbar,.pagination{flex:0 0 auto}.breadcrumb{margin-bottom:10px;color:#7a8699;font-size:12px}.toolbar{display:flex;gap:10px;margin-bottom:16px}.toolbar .n-input{flex:1}.list-scroll{min-height:0;flex:1;overflow:auto;padding-right:4px;scrollbar-gutter:stable}.list-scroll :deep(.n-spin-container),.list-scroll :deep(.n-spin-content){height:100%;min-height:100%}.empty-state{width:100%;height:100%;display:grid;place-items:center}.pagination{display:flex;align-items:center;justify-content:space-between;padding-top:14px;color:#7a8699;font-size:12px}.knowledge-list{width:max(100%,1392px);overflow:visible;border:1px solid #e5e9f1;border-radius:10px}.list-head,.knowledge-row{display:grid;grid-template-columns:548px 190px 180px 200px minmax(210px,1fr);gap:16px;align-items:center;padding:0}.list-head{min-height:40px;background:#f7f9fc;color:#8791a2;font-size:12px;font-weight:600}.list-head>span:first-child{position:sticky;z-index:3;left:0;display:flex;height:100%;box-sizing:border-box;align-items:center;padding:0 14px;background:#f7f9fc;box-shadow:10px 0 14px -14px rgba(45,55,72,.55)}.knowledge-row{min-height:96px;border-top:1px solid #edf0f5;background:#fff;cursor:pointer;transition:background .15s ease}.knowledge-row:hover{background:#f8faff}.knowledge-row.revoked{opacity:.65}.knowledge-main{position:sticky;z-index:2;left:0;display:flex;height:100%;min-width:0;box-sizing:border-box;justify-content:center;flex-direction:column;gap:4px;padding:12px 14px;background:#fff;box-shadow:10px 0 14px -14px rgba(45,55,72,.55);transition:background .15s ease}.knowledge-row:hover .knowledge-main{background:#f8faff}.knowledge-title{display:flex;min-width:0;align-items:center;gap:7px}.knowledge-title strong{min-width:0}.shared-tag{flex:0 0 auto}.unread-comment-dot{width:10px;height:10px;flex:0 0 10px;border-radius:50%;background:#e5484d}.knowledge-main strong,.knowledge-main>span,.context-text{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.knowledge-main strong{color:#252b37;font-size:14px}.knowledge-main>span,.context-text,.knowledge-row time{color:#7a8699;font-size:12px}.knowledge-row time{white-space:nowrap}.row-tags{display:flex;min-width:0;gap:4px;overflow:hidden}.row-actions{display:flex;min-width:max-content;justify-content:flex-end;gap:12px;padding-right:14px;white-space:nowrap}.actions-column{padding-right:14px;text-align:right}.drop-mask{position:absolute;z-index:3;inset:10px;display:grid;place-items:center;border:2px dashed #2459e8;border-radius:12px;background:rgba(238,243,255,.94);color:#2459e8;font-size:18px;font-weight:700}
</style>
