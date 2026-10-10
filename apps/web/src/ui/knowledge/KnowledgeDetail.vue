<template>
  <n-modal :show="show" preset="card" :title="knowledge?.name || t('knowledge.details')" class="detail-modal" style="width:min(1280px,calc(100vw - 32px))" @update:show="emit('update:show',$event)">
    <template #header-extra>
      <n-button v-if="knowledge?.canShare" type="primary" size="small" @click="shareOpen=true">{{ t('knowledge.share') }}</n-button>
    </template>
    <n-alert v-if="knowledge?.accessStatus === 'deleted'" type="warning" :title="t('knowledge.status_deleted')">{{ t('knowledge.source_deleted') }}</n-alert>
    <n-alert v-else-if="knowledge?.accessStatus === 'revoked'" type="warning" :title="t('knowledge.access_revoked')" />
    <div v-else-if="knowledge" class="detail-layout">
      <section class="preview-column">
        <div class="section-title"><strong>{{ t('knowledge.preview') }}</strong></div>
        <PersonalKnowledgePreview :document-id="knowledge.activeDocumentId" :title="knowledge.name" />
      </section>
      <aside class="detail-sidebar">
        <n-tabs v-model:value="tab" type="line" animated class="detail-tabs">
          <n-tab-pane name="discussion" :tab="t('knowledge.discussion')"><SkillFeedbackPanel resource-type="personal_knowledge" :resource-id="knowledge.id" :unread-count="item?.feedback?.unreadCount || 0" live @read="feedbackRead" /></n-tab-pane>
          <n-tab-pane name="ask" :tab="t('knowledge.ask')"><KnowledgeAskPanel :knowledge-id="knowledge.id" /></n-tab-pane>
          <n-tab-pane name="metadata" :tab="t('knowledge.info')">
            <div class="knowledge-state-row">
              <span>{{ t('knowledge.learning_status') }}</span>
              <KnowledgeLearningStatus :status="knowledge.status" :retryable="canRetry" @retry="retryFromStatus" />
            </div>
            <n-alert v-if="knowledge.status==='failed' && knowledge.error" type="error" :show-icon="false" class="learning-error">{{ knowledge.error }}</n-alert>
            <KnowledgeMetadataForm v-if="knowledge.isOwner" :knowledge="knowledge" @saved="metadataSaved" />
            <div v-else class="metadata-readonly">
              <div><span>{{ t('knowledge.name') }}</span><strong>{{ knowledge.name }}</strong></div>
              <div><span>{{ t('knowledge.description') }}</span><strong>{{ knowledge.description || t('knowledge.no_description') }}</strong></div>
              <div><span>{{ t('knowledge.owner') }}</span><strong>{{ knowledge.owner?.displayName || knowledge.owner?.username || t('knowledge.colleague') }}</strong></div>
              <div><span>{{ t('knowledge.updated_at') }}</span><strong>{{ formatTime(knowledge.updatedAt) }}</strong></div>
            </div>
            <div v-if="knowledge.isOwner" class="file-actions">
              <div><strong>{{ t('knowledge.update_source') }}</strong><p>{{ t('knowledge.replace_hint') }}</p></div>
              <n-button secondary :loading="replacing" @click="replacePicker?.click()">{{ t('knowledge.replace_file') }}</n-button>
            </div>
          </n-tab-pane>
          <n-tab-pane v-if="knowledge.isOwner" name="permissions" :tab="t('knowledge.permissions')">
            <div class="permission-head"><span>{{ t('knowledge.permissions_hint') }}</span><n-button type="primary" size="small" @click="shareOpen=true">{{ t('knowledge.add_share') }}</n-button></div>
            <n-spin :show="grantsLoading">
              <div v-if="grants.length" class="grants">
                <div v-for="grant in grants" :key="grant.userId">
                  <div><strong>{{ grant.user?.displayName || grant.user?.username || grant.userId }}</strong><small>{{ t('knowledge.shared_by_status',{name:grant.grantedBy?.displayName || grant.grantedBy?.username || grant.grantedByUserId,status:t(grant.status==='active'?'knowledge.active':'knowledge.revoked')}) }}</small></div>
                  <div v-if="grant.status==='active'" class="grant-actions"><n-switch :value="grant.canReshare" size="small" @update:value="value=>changeReshare(grant,value)"/><span>{{ t('knowledge.allow_reshare') }}</span><n-dropdown :options="revokeOptions" @select="(key:string)=>revoke(grant,key==='cascade')"><n-button size="small" type="error" secondary>{{ t('knowledge.revoke') }}</n-button></n-dropdown></div>
                </div>
              </div>
              <n-empty v-else :description="t('knowledge.no_grants')" />
            </n-spin>
          </n-tab-pane>
        </n-tabs>
      </aside>
    </div>
    <KnowledgeShareDialog v-model:show="shareOpen" :knowledge="knowledge" @shared="shared" />
    <input ref="replacePicker" type="file" hidden @change="submitReplace" />
  </n-modal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NAlert, NButton, NDropdown, NEmpty, NModal, NSpin, NSwitch, NTabPane, NTabs, useDialog, useMessage } from 'naive-ui'
import { fetchKnowledgeGrants, fetchPersonalKnowledgeItem, relearnPersonalKnowledge, replacePersonalKnowledge, revokePersonalKnowledge, updatePersonalKnowledgeGrant, type KnowledgeGrant, type PersonalKnowledge } from '../../api/personalKnowledge'
import { useStatusPolling } from '../../hooks/useStatusPolling'
import { t } from '../../hooks/i18n'
import SkillFeedbackPanel from '../skills/SkillFeedbackPanel.vue'
import KnowledgeAskPanel from './KnowledgeAskPanel.vue'
import KnowledgeLearningStatus from './KnowledgeLearningStatus.vue'
import KnowledgeMetadataForm from './KnowledgeMetadataForm.vue'
import KnowledgeShareDialog from './KnowledgeShareDialog.vue'
import PersonalKnowledgePreview from './PersonalKnowledgePreview.vue'
import { isKnowledgeProcessingStatus } from './knowledgeStatus'

const props=defineProps<{show:boolean;item:PersonalKnowledge|null}>()
const emit=defineEmits<{ 'update:show':[value:boolean]; changed:[] }>()
const message=useMessage(),dialog=useDialog(),knowledge=ref<PersonalKnowledge|null>(null),tab=ref('discussion'),grants=ref<KnowledgeGrant[]>([]),grantsLoading=ref(false),shareOpen=ref(false),replacePicker=ref<HTMLInputElement|null>(null),replacing=ref(false),shareSeenReported=ref(false)
const revokeOptions=computed(()=>[{label:t('knowledge.revoke_single'),key:'single'},{label:t('knowledge.revoke_cascade'),key:'cascade'}])
const canRetry=computed(()=>Boolean(knowledge.value?.isOwner&&knowledge.value.status==='failed'))

async function load(includeGrants=true){
  if(!props.item)return
  try{knowledge.value=await fetchPersonalKnowledgeItem(props.item.id)}catch(error:any){knowledge.value=error?.response?.status===403?{...props.item,accessStatus:'revoked'}:props.item}
  if(!props.item.seen&&knowledge.value.seen&&!shareSeenReported.value){shareSeenReported.value=true;emit('changed')}
  if(knowledge.value.accessStatus==='revoked'||knowledge.value.accessStatus==='deleted')return
  if(includeGrants&&knowledge.value.isOwner)await loadGrants()
}
async function metadataSaved(){await load();emit('changed')}
function feedbackRead(){if(props.item?.feedback?.unreadCount)emit('changed')}
async function loadGrants(){if(!knowledge.value)return;grantsLoading.value=true;try{grants.value=(await fetchKnowledgeGrants(knowledge.value.id)).items}finally{grantsLoading.value=false}}
async function shared(){await loadGrants();emit('changed')}
async function changeReshare(grant:KnowledgeGrant,value:boolean){if(!knowledge.value)return;await updatePersonalKnowledgeGrant(knowledge.value.id,grant.userId,value);grant.canReshare=value;message.success(t('knowledge.permission_updated'))}
function revoke(grant:KnowledgeGrant,cascade=false){if(!knowledge.value)return;dialog.warning({title:t('knowledge.revoke'),content:t(cascade?'knowledge.revoke_cascade_hint':'knowledge.revoke_single_hint'),positiveText:t('knowledge.revoke'),negativeText:t('knowledge.back'),onPositiveClick:async()=>{await revokePersonalKnowledge(knowledge.value!.id,grant.userId,cascade);message.success(t('knowledge.permission_revoked'));await loadGrants();emit('changed')}})}
function retryFromStatus(){
  if(!canRetry.value||!knowledge.value)return
  dialog.warning({title:t('knowledge.relearn_title'),content:t('knowledge.relearn_detail_confirm'),positiveText:t('knowledge.relearn'),negativeText:t('knowledge.cancel'),onPositiveClick:async()=>{await relearnPersonalKnowledge(knowledge.value!.id);message.success(t('knowledge.relearn_submitted'));await load();emit('changed')}})
}
async function submitReplace(event:Event){
  const input=event.target as HTMLInputElement,file=input.files?.[0]
  if(!file||!knowledge.value)return
  replacing.value=true
  try{await replacePersonalKnowledge(knowledge.value.id,file);message.success(t('knowledge.replace_submitted'));await load();emit('changed')}catch(error:any){message.error(error?.response?.data?.detail||t('knowledge.replace_failed'))}finally{replacing.value=false;input.value=''}
}
function formatTime(value:string){return value?new Date(value).toLocaleString():'--'}
const isProcessing=computed(()=>Boolean(props.show&&knowledge.value&&isKnowledgeProcessingStatus(knowledge.value.status)))
useStatusPolling({enabled:isProcessing,refresh:()=>load(false)})
watch(()=>[props.show,props.item?.id],()=>{if(props.show){tab.value='discussion';shareOpen.value=false;shareSeenReported.value=false;void load()}})
</script>

<style scoped>
.detail-layout{display:grid;height:min(720px,76vh);min-height:520px;grid-template-columns:minmax(0,1.55fr) minmax(380px,.85fr);gap:18px}.preview-column,.detail-sidebar{min-width:0;min-height:0}.preview-column{display:flex;flex-direction:column;gap:10px}.section-title{display:flex;align-items:baseline;justify-content:space-between}.detail-sidebar{display:flex;overflow:hidden;flex-direction:column}.knowledge-state-row{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px;padding-bottom:12px;border-bottom:1px solid #edf0f5;color:#667085;font-size:13px}.learning-error{margin:0 0 14px}.detail-tabs{min-height:0;flex:1}.detail-tabs :deep(.n-tabs-nav){padding:4px;border:1px solid #edf0f5;border-radius:10px;background:#f7f9fc}.detail-tabs :deep(.n-tabs-nav--line-type){border-bottom:0}.detail-tabs :deep(.n-tabs-tab){flex:1;justify-content:center;padding:8px 12px;border-radius:7px;color:#667085;font-weight:500;transition:color .2s ease,background-color .2s ease,box-shadow .2s ease}.detail-tabs :deep(.n-tabs-tab:hover){color:#2459e8}.detail-tabs :deep(.n-tabs-tab--active){color:#2459e8;background:#fff;box-shadow:0 1px 4px rgba(31,61,115,.12)}.detail-tabs :deep(.n-tabs-bar){display:none}.detail-tabs :deep(.n-tabs-pane-wrapper),.detail-tabs :deep(.n-tab-pane){height:100%;min-height:0}.detail-tabs :deep(.n-tab-pane){overflow:auto;padding-right:4px}.metadata-readonly{display:grid;gap:16px}.metadata-readonly>div{display:flex;flex-direction:column;gap:5px}.metadata-readonly span{color:#8a94a5;font-size:12px}.file-actions{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-top:22px;padding-top:18px;border-top:1px solid #edf0f5}.file-actions p{margin:5px 0 0;color:#8a94a5;font-size:12px}.permission-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px;color:#667085;font-size:12px}.grants>div{display:flex;align-items:center;justify-content:space-between;padding:12px 0;border-bottom:1px solid #edf0f5}.grants small{display:block;margin-top:4px;color:#8a94a5}.grant-actions{display:flex;align-items:center;gap:8px;color:#667085;font-size:12px}@media(max-width:900px){.detail-layout{height:min(760px,80vh);grid-template-columns:1fr;grid-template-rows:minmax(260px,1fr) minmax(300px,1fr)}}
</style>
