import { onBeforeUnmount, onMounted, watch, type ComputedRef } from 'vue'

interface StatusPollingOptions {
  enabled: ComputedRef<boolean>
  refresh: () => Promise<void>
  intervalMs?: number
}

export function useStatusPolling(options: StatusPollingOptions): void {
  let timer: ReturnType<typeof setTimeout> | null = null
  let refreshing = false
  let mounted = false
  const intervalMs = options.intervalMs ?? 2000

  function clearTimer() {
    if (timer) clearTimeout(timer)
    timer = null
  }

  function schedule() {
    clearTimer()
    if (!mounted || !options.enabled.value || document.visibilityState === 'hidden') return
    timer = setTimeout(() => { void poll() }, intervalMs)
  }

  async function poll() {
    if (refreshing || !mounted || !options.enabled.value || document.visibilityState === 'hidden') return
    refreshing = true
    try {
      await options.refresh()
    } finally {
      refreshing = false
      schedule()
    }
  }

  function handleVisibilityChange() {
    if (document.visibilityState === 'hidden') clearTimer()
    else if (options.enabled.value) void poll()
  }

  watch(options.enabled, enabled => {
    if (enabled) schedule()
    else clearTimer()
  })

  onMounted(() => {
    mounted = true
    document.addEventListener('visibilitychange', handleVisibilityChange)
    schedule()
  })

  onBeforeUnmount(() => {
    mounted = false
    clearTimer()
    document.removeEventListener('visibilitychange', handleVisibilityChange)
  })
}
