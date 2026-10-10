import { ref, shallowRef } from 'vue'
import { errorMessage, isPermissionDenied } from '../api/client'

export type ResourceState<T> = {
  rows: ReturnType<typeof shallowRef<T[]>>
  loading: ReturnType<typeof ref<boolean>>
  error: ReturnType<typeof ref<string>>
  denied: ReturnType<typeof ref<boolean>>
  reload: () => Promise<void>
}

/**
 * 通用列表加载：把 403（缺管理角色码）与其它错误**如实**呈现，
 * 绝不用假数据填充表格。
 */
export function useResourceList<T>(loader: () => Promise<T[]>) {
  const rows = shallowRef<T[]>([])
  const loading = ref(false)
  const error = ref('')
  const denied = ref(false)

  async function reload(): Promise<void> {
    loading.value = true
    error.value = ''
    denied.value = false
    try {
      rows.value = await loader()
    } catch (err) {
      rows.value = []
      denied.value = isPermissionDenied(err)
      error.value = denied.value
        ? `${errorMessage(err)} —— 当前登录态缺少该资源的管理角色码`
        : errorMessage(err)
    } finally {
      loading.value = false
    }
  }

  return { rows, loading, error, denied, reload }
}
