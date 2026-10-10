<template>
  <section ref="feedbackRoot" class="feedback">
    <header>
      <div class="heading">
        <div class="title-line">
          <strong>{{ t('skills.feedback.title') }}</strong>
          <span class="total">{{ t('skills.feedback.total', { count: thread.commentCount }) }}</span>
          <n-tag v-if="visibleUnreadCount" type="error" size="small" round>{{ t('skills.feedback.unread', { count: visibleUnreadCount }) }}</n-tag>
        </div>
      </div>
      <n-button size="small" :type="thread.likedByMe ? 'primary' : 'default'" secondary round @click="likeSkill"><span class="resource-heart" :class="{ 'heart-highlighted': highlightLike }">{{ highlightLike || thread.likedByMe ? '♥' : '♡' }}</span>{{ thread.likes }}</n-button>
    </header>
    <n-spin :show="loading">
      <div v-if="groups.length" class="comments">
        <section v-for="group in groups" :key="group.root.id" :class="{ 'has-replies': group.replies.length }">
          <FeedbackCommentItem :comment="group.root" :highlighted="highlightedCommentId === group.root.id" :like-highlighted="highlightedCommentLikeId === group.root.id" @reply="startReply" @like="likeComment" @delete="remove" />
          <div v-if="group.replies.length" class="replies">
            <FeedbackCommentItem v-for="reply in group.replies" :key="reply.id" :comment="reply" nested :highlighted="highlightedCommentId === reply.id" :like-highlighted="highlightedCommentLikeId === reply.id" @reply="startReply" @like="likeComment" @delete="remove" />
          </div>
        </section>
      </div>
      <n-empty v-else-if="!loading" size="small" :description="t('skills.feedback.empty')" class="empty" />
      <div v-if="thread.hasMore" class="load-more"><n-button secondary size="small" :loading="loadingMore" @click="loadMore">{{ t('ui.load_more') }}</n-button></div>
    </n-spin>
    <div v-if="replying" class="replying">
      <span>{{ t('skills.feedback.replying_to', { name: replying.author.displayName || t('skills.feedback.member') }) }}</span>
      <n-button text size="tiny" @click="replying = null">{{ t('ui.cancel') }}</n-button>
    </div>
    <div class="composer">
      <n-input v-model:value="content" type="textarea" :autosize="{ minRows: 2, maxRows: 5 }" maxlength="2000" show-count :placeholder="replying ? t('skills.feedback.reply_placeholder') : t('skills.feedback.placeholder')" />
      <n-button type="primary" :disabled="!content.trim()" :loading="sending" @click="send">{{ replying ? t('skills.feedback.reply') : t('skills.feedback.send') }}</n-button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { NButton, NEmpty, NInput, NSpin, NTag, useMessage } from 'naive-ui'
import { addFeedbackComment, deleteFeedbackComment, fetchFeedback, toggleFeedbackCommentLike, toggleFeedbackLike, type FeedbackComment, type FeedbackThread } from '../../api/resourceFeedback'
import { t } from '../../hooks/i18n'
import FeedbackCommentItem from './FeedbackCommentItem.vue'

const props = withDefaults(defineProps<{ resourceType: string; resourceId: string; commentCount?: number; unreadCount?: number; live?: boolean }>(), {
  commentCount: 0,
  unreadCount: 0,
  live: false,
})
const emit = defineEmits<{ read: []; changed: [] }>()
const message = useMessage(), loading = ref(false), loadingMore = ref(false), sending = ref(false), content = ref(''), replying = ref<FeedbackComment | null>(null)
const thread = reactive<FeedbackThread>({ items: [], commentCount: props.commentCount, likes: 0, likedByMe: false, hasMore: false, nextCursor: '' })
const feedbackRoot = ref<HTMLElement | null>(null), highlightedCommentId = ref(''), highlightedCommentLikeId = ref(''), highlightLike = ref(false)
const visibleUnreadCount = ref(props.unreadCount)
let refreshTimer: ReturnType<typeof setInterval> | null = null
let focusTimer: ReturnType<typeof setTimeout> | null = null
const groups = computed(() => {
  const byId = new Map(thread.items.map(item => [item.id, item]))
  const roots = thread.items.filter(item => !item.rootId || !byId.has(item.rootId))
  return roots.map(root => ({ root, replies: thread.items.filter(item => item.id !== root.id && item.rootId === root.id).slice().reverse() }))
})

async function load(append = false, silent = false) {
  if (loading.value || loadingMore.value) return
  if (append) loadingMore.value = true
  else if (!silent) loading.value = true
  try {
    const data = await fetchFeedback(props.resourceType, props.resourceId, append ? thread.nextCursor : '')
    const clearedUnread = !append && (visibleUnreadCount.value > 0 || Boolean(data.focus))
    thread.items = append ? [...thread.items, ...data.items] : data.items
    thread.commentCount = Number(data.commentCount ?? data.items.length); thread.likes = data.likes; thread.likedByMe = data.likedByMe; thread.hasMore = data.hasMore; thread.nextCursor = data.nextCursor
    if (!append && data.focus && !highlightedCommentId.value && !highlightedCommentLikeId.value && !highlightLike.value) focusNewActivity(data.focus)
    visibleUnreadCount.value = 0
    if (clearedUnread) emit('read')
  } catch { if (!silent) message.error(t('skills.feedback.load_failed')) }
  finally {
    if (!silent) loading.value = false
    loadingMore.value = false
  }
}
function focusNewActivity(focus: NonNullable<FeedbackThread['focus']>) {
  const commentLike = focus.kind === 'like' && Boolean(focus.commentId)
  highlightedCommentId.value = commentLike ? '' : focus.commentId || ''
  highlightedCommentLikeId.value = commentLike ? focus.commentId : ''
  highlightLike.value = focus.kind === 'like' && !focus.commentId
  void nextTick(() => {
    const targetId = highlightedCommentId.value || highlightedCommentLikeId.value
    if (!targetId) return
    const target = Array.from(feedbackRoot.value?.querySelectorAll<HTMLElement>('[data-feedback-id]') || []).find(node => node.dataset.feedbackId === targetId)
    target?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  })
  if (focusTimer) clearTimeout(focusTimer)
  const duration = focus.kind === 'like' ? 1600 : 3900
  focusTimer = setTimeout(() => { highlightedCommentId.value = ''; highlightedCommentLikeId.value = ''; highlightLike.value = false }, duration)
}
async function send() {
  if (!content.value.trim()) return
  sending.value = true
  try {
    const item = await addFeedbackComment(props.resourceType, props.resourceId, content.value, replying.value?.id || '')
    thread.items.unshift(item); thread.commentCount += 1; content.value = ''; replying.value = null; emit('changed')
  } catch { message.error(t('skills.feedback.send_failed')) }
  finally { sending.value = false }
}
function startReply(item: FeedbackComment) { replying.value = item }
async function likeSkill() { try { Object.assign(thread, await toggleFeedbackLike(props.resourceType, props.resourceId)) } catch { message.error(t('skills.feedback.like_failed')) } }
async function likeComment(item: FeedbackComment) { try { Object.assign(item, await toggleFeedbackCommentLike(item.id)) } catch { message.error(t('skills.feedback.like_failed')) } }
async function remove(item: FeedbackComment) { try { await deleteFeedbackComment(item.id); thread.items = thread.items.filter(row => row.id !== item.id); thread.commentCount = Math.max(0, thread.commentCount - 1); emit('changed') } catch { message.error(t('skills.feedback.delete_failed')) } }
function loadMore() { void load(true) }
watch(() => props.unreadCount, value => { visibleUnreadCount.value = value })
watch(() => props.resourceId, () => { replying.value = null; content.value = ''; highlightedCommentId.value = ''; highlightedCommentLikeId.value = ''; highlightLike.value = false; visibleUnreadCount.value = props.unreadCount; void load() })
onMounted(() => {
  void load()
  if (props.live) refreshTimer = setInterval(() => void load(false, true), 8000)
})
onBeforeUnmount(() => {
  if (refreshTimer) clearInterval(refreshTimer)
  refreshTimer = null
  if (focusTimer) clearTimeout(focusTimer)
  focusTimer = null
})
</script>

<style scoped>
.feedback{display:flex;height:100%;min-height:0;flex-direction:column;gap:14px;overflow:hidden}.feedback>header{display:flex;flex:0 0 auto;align-items:center;justify-content:space-between;gap:16px;padding:2px 0 10px;border-bottom:1px solid #edf0f5}.feedback> :deep(.n-spin-container){display:flex;min-height:0;flex:1;flex-direction:column}.feedback> :deep(.n-spin-container) > .n-spin-content{display:flex;height:100%;min-height:0;flex-direction:column}.heading{min-width:0}.title-line{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.total{color:#667085;font-size:13px;font-weight:400}.comments{min-height:0;flex:1;max-height:none;overflow:auto;padding:0 4px 2px 0}.comments>section{margin-bottom:16px;padding:0 10px 16px;border-bottom:1px solid #edf0f5}.comments>section:last-child{margin-bottom:0;border-bottom:0}.comments>section.has-replies> :deep(.comment){padding-bottom:10px}.replies{margin:8px 0 8px 42px;padding:0 10px;border:1px solid #e7eefc;border-radius:10px;background:#f8faff}.replies :deep(.comment.reply){margin-left:0}.replies :deep(.comment.reply:first-child){border-top:0}.replies :deep(.comment.reply + .comment.reply){border-top:1px solid #e8edf5}.empty{padding:54px 0}.load-more{text-align:center;padding:12px}.replying{display:flex;align-items:center;justify-content:space-between;padding:9px 12px;border-radius:9px;color:#52627d;background:#f2f5fa}.composer{display:flex;flex:0 0 auto;align-items:flex-end;gap:10px;padding-top:10px;border-top:1px solid #edf0f5;background:#fff}.composer :deep(.n-input){flex:1}.resource-heart{display:inline-block;margin-right:4px;transform-origin:center}.resource-heart.heart-highlighted{color:#e5484d;animation:feedback-heart-pulse 1.4s ease-in-out}@keyframes feedback-heart-pulse{0%,100%{transform:scale(1)}30%{transform:scale(1.65)}58%{transform:scale(.88)}78%{transform:scale(1.15)}}
</style>
