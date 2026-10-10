import { computed, type ComputedRef } from 'vue'

/**
 * 原样透传 runtime store 的消息数组，使展示组件
 * 不会拿到第二份可变副本。
 */
export function useSettledMessages<T>(source: () => T[] | undefined): ComputedRef<T[]> {
  return computed(() => source() || [])
}
