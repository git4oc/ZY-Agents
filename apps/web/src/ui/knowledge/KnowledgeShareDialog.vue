<template>
  <n-modal :show="show" preset="card" :title="t('knowledge.share')" style="width:min(620px,calc(100vw - 32px))" @update:show="emit('update:show',$event)">
    <n-select v-model:value="selected" multiple filterable remote :options="options" :loading="loading" :placeholder="t('knowledge.share_people_placeholder')" @search="search" />
    <n-checkbox v-model:checked="canReshare" class="permission">{{ t('knowledge.allow_recipients_reshare') }}</n-checkbox>
    <p class="hint">{{ t('knowledge.share_hint') }}</p>
    <template #footer><n-space justify="end"><n-button @click="emit('update:show',false)">{{ t('knowledge.cancel') }}</n-button><n-button type="primary" :disabled="!selected.length" :loading="sending" @click="submit">{{ t('knowledge.share_action') }}</n-button></n-space></template>
  </n-modal>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NButton, NCheckbox, NModal, NSelect, NSpace, useMessage } from 'naive-ui'
import { searchSkillShareMembers, type SkillShareMember } from '../../api/skillSharing'
import { sharePersonalKnowledge, type PersonalKnowledge } from '../../api/personalKnowledge'
import { t } from '../../hooks/i18n'
const props = defineProps<{ show: boolean; knowledge: PersonalKnowledge | null }>()
const emit = defineEmits<{ 'update:show': [value:boolean]; shared: [] }>()
const message=useMessage(), selected=ref<string[]>([]), members=ref<SkillShareMember[]>([]), loading=ref(false), sending=ref(false), canReshare=ref(false)
const options=computed(()=>members.value.map(item=>({value:item.userId,label:[item.displayName,item.username||item.email].filter(Boolean).join(' · ')})))
let timer:ReturnType<typeof setTimeout>|null=null
function search(value:string){ if(timer)clearTimeout(timer);timer=setTimeout(async()=>{loading.value=true;try{members.value=(await searchSkillShareMembers({keyword:value,limit:40})).items}finally{loading.value=false}},250) }
async function submit(){if(!props.knowledge)return;sending.value=true;try{await sharePersonalKnowledge(props.knowledge.id,selected.value.map(userId=>({userId,canReshare:canReshare.value})));message.success(t('knowledge.share_success'));emit('update:show',false);emit('shared')}catch(error:any){message.error(error?.response?.data?.detail||t('knowledge.share_failed'))}finally{sending.value=false}}
watch(()=>props.show,value=>{if(value){selected.value=[];canReshare.value=false;search('')}})
</script>
<style scoped>.permission{margin-top:18px}.hint{margin:8px 0;color:#7a8699;font-size:12px}</style>
