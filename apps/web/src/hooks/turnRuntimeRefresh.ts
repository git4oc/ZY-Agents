export type RunRefreshCallbacks = {
  onQuotaRefresh?: () => void | Promise<void>
  onSessionUpdated?: (sessionId: string) => void | Promise<void>
}

/** Kicks off the post-run UI refreshes; neither request may disrupt the cleanup path. */
export async function refreshAfterRun(
  callbacks: RunRefreshCallbacks,
  sessionId: string | null | undefined,
): Promise<void> {
  const refreshes: Promise<unknown>[] = []
  if (callbacks.onQuotaRefresh) {
    refreshes.push(Promise.resolve().then(() => callbacks.onQuotaRefresh?.()))
  }
  if (sessionId && callbacks.onSessionUpdated) {
    refreshes.push(Promise.resolve().then(() => callbacks.onSessionUpdated?.(sessionId)))
  }
  if (refreshes.length) await Promise.allSettled(refreshes)
}
