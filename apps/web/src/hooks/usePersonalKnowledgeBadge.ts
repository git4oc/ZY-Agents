import { onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import { fetchPersonalKnowledgeCounts } from '../api/personalKnowledge'

export function usePersonalKnowledgeBadge(scope: Ref<{ token: string; mainId: string; userId: string; enabled: boolean }>) {
  const unreadCount = ref(0)
  const shareCount = ref(0)
  const receivedCount = ref(0)
  const feedbackCount = ref(0)
  let timer: ReturnType<typeof setInterval> | null = null
  let version = 0
  async function refresh() {
    const current = scope.value
    if (!current.enabled || !current.token || !current.mainId || !current.userId) {
      unreadCount.value = 0
      shareCount.value = 0
      receivedCount.value = 0
      feedbackCount.value = 0
      return
    }
    const requestVersion = version
    try {
      const result = await fetchPersonalKnowledgeCounts()
      if (requestVersion === version) {
        unreadCount.value = result.unreadCount
        shareCount.value = result.shareCount
        receivedCount.value = result.receivedCount || 0
        feedbackCount.value = result.feedbackCount
      }
    }
    catch { /* a badge update failure must never block navigation */ }
  }
  watch(() => [scope.value.token, scope.value.mainId, scope.value.userId, scope.value.enabled], () => {
    version += 1
    unreadCount.value = 0
    shareCount.value = 0
    receivedCount.value = 0
    feedbackCount.value = 0
    void refresh()
  }, { immediate: true })
  onMounted(() => { timer = setInterval(() => { if (document.visibilityState === 'visible') void refresh() }, 30_000) })
  onBeforeUnmount(() => { if (timer) clearInterval(timer) })
  return { unreadCount, shareCount, receivedCount, feedbackCount, refresh }
}
