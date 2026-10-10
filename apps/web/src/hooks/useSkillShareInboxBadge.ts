import { onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import { fetchReceivedSkillShareCounts } from '../api/skillSharing'

interface SkillShareInboxScope {
  token: string
  mainId: string
  userId: string
  enabled: boolean
}

interface SkillShareInboxBadgeOptions {
  scope: Ref<SkillShareInboxScope>
  refreshIntervalMs?: number
}

/** Syncs the sidebar inbox badge counts while skipping share preview loading. */
export function useSkillShareInboxBadge(options: SkillShareInboxBadgeOptions) {
  const pendingCount = ref(0)
  const actionCount = ref(0)
  const feedbackCount = ref(0)
  let timer: ReturnType<typeof setInterval> | null = null
  let requestVersion = 0
  let inFlight: Promise<void> | null = null

  const isAvailable = () => {
    const value = options.scope.value
    return Boolean(value.enabled && value.token && value.mainId && value.userId)
  }

  const refresh = (): Promise<void> => {
    if (!isAvailable()) {
      pendingCount.value = 0
      actionCount.value = 0
      feedbackCount.value = 0
      return Promise.resolve()
    }
    if (inFlight) return inFlight
    const version = requestVersion
    let request: Promise<void>
    request = fetchReceivedSkillShareCounts()
      .then((counts) => {
        if (version === requestVersion && isAvailable()) {
          pendingCount.value = counts.pendingCount
          actionCount.value = counts.sharedCount + counts.updateCount
          feedbackCount.value = counts.feedbackCount
        }
      })
      .catch(() => {})
      .finally(() => {
        if (inFlight === request) inFlight = null
      })
    inFlight = request
    return request
  }

  const setPendingCount = (count: number) => {
    pendingCount.value = Math.max(0, Number(count) || 0)
  }

  watch(
    () => {
      const value = options.scope.value
      return [value.token, value.mainId, value.userId, value.enabled] as const
    },
    () => {
      requestVersion += 1
      inFlight = null
      pendingCount.value = 0
      actionCount.value = 0
      feedbackCount.value = 0
      void refresh()
    },
    { immediate: true },
  )

  onMounted(() => {
    timer = setInterval(() => {
      if (document.visibilityState === 'visible') void refresh()
    }, options.refreshIntervalMs ?? 30_000)
  })
  onBeforeUnmount(() => {
    if (timer) clearInterval(timer)
  })

  return { pendingCount, actionCount, feedbackCount, refresh, setPendingCount }
}
