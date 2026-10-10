<template>
  <article class="comment" :class="{ reply: nested, highlighted }" :data-feedback-id="comment.id">
    <div class="avatar">{{ initial }}</div>
    <div class="body">
      <div class="meta">
        <strong>{{ comment.author.displayName || t('skills.feedback.member') }}</strong>
        <span v-if="comment.replyTo?.displayName" class="reply-to">{{ t('skills.feedback.reply_to', { name: comment.replyTo.displayName }) }}</span>
        <time>{{ formatAppDateTime(comment.createdAt, '') }}</time>
      </div>
      <p>{{ comment.content }}</p>
      <div class="actions">
        <n-button text size="tiny" @click="emit('reply', comment)">{{ t('skills.feedback.reply') }}</n-button>
        <n-button text size="tiny" :type="comment.likedByMe ? 'primary' : 'default'" @click="emit('like', comment)"><span class="heart" :class="{ 'heart-highlighted': likeHighlighted }">{{ likeHighlighted || comment.likedByMe ? '♥' : '♡' }}</span><span v-if="comment.likes">{{ comment.likes }}</span></n-button>
        <n-button v-if="comment.mine" text size="tiny" type="error" @click="emit('delete', comment)">{{ t('ui.delete') }}</n-button>
      </div>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { NButton } from 'naive-ui'
import type { FeedbackComment } from '../../api/resourceFeedback'
import { formatAppDateTime } from '../../hooks/appTimezone'
import { t } from '../../hooks/i18n'

const props = defineProps<{ comment: FeedbackComment; nested?: boolean; highlighted?: boolean; likeHighlighted?: boolean }>()
const emit = defineEmits<{ reply: [FeedbackComment]; like: [FeedbackComment]; delete: [FeedbackComment] }>()
const initial = computed(() => (props.comment.author.displayName || t('skills.feedback.member')).slice(0, 1).toUpperCase())
</script>

<style scoped>
.comment{display:flex;gap:11px;padding:14px 2px;border:0;border-radius:8px;transform-origin:center}.comment.highlighted{position:relative;z-index:1;animation:feedback-highlight 3.35s ease-in-out .28s both}.comment.reply{margin-left:42px;padding:11px 2px 11px 12px;border:0;border-top:1px solid #f1f3f6;border-radius:0;background:transparent}.avatar{width:32px;height:32px;display:grid;place-items:center;flex:0 0 32px;border-radius:50%;color:#2459e8;background:#eaf0ff;font-size:12px;font-weight:750}.body{min-width:0;flex:1}.meta{display:flex;align-items:center;gap:8px}.meta strong{color:#344054;font-size:13px}.meta time{margin-left:auto;color:#98a2b3;font-size:11px}.reply-to{color:#667085;font-size:12px}.body p{margin:7px 0;color:#475467;line-height:1.65;white-space:pre-wrap;overflow-wrap:anywhere}.actions{display:flex;align-items:center;gap:12px}.heart{display:inline-block;margin-right:3px;font-size:15px;transform-origin:center}.heart.heart-highlighted{color:#e5484d;animation:feedback-heart-pulse 1.4s ease-in-out}@keyframes feedback-highlight{0%,100%{background:transparent;transform:scale(1)}28%,58%{background:#dce8ff;transform:scale(1.025)}76%{background:#e8f0ff;transform:scale(1.008)}}@keyframes feedback-heart-pulse{0%,100%{transform:scale(1)}30%{transform:scale(1.65)}58%{transform:scale(.88)}78%{transform:scale(1.15)}}
</style>
