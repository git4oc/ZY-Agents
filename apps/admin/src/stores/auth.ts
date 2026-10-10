import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { fetchMe, loginRequest, type AdminProfile } from '../api/auth'
import { clearToken, errorStatus, getToken, setToken } from '../api/client'

/** 管理端登录态：token 存 localStorage，profile 来自 GET /auth/me。 */
export const useAuthStore = defineStore('admin-auth', () => {
  const token = ref<string>(getToken())
  const profile = ref<AdminProfile | null>(null)
  const profileError = ref<string>('')

  const isAuthenticated = computed(() => Boolean(token.value))
  const roleLabel = computed(() => {
    const roles = profile.value?.roles ?? []
    return roles.length ? roles.join('、') : '无管理角色码'
  })

  async function signIn(username: string, password: string): Promise<void> {
    const nextToken = await loginRequest(username, password)
    setToken(nextToken)
    token.value = nextToken
    await loadProfile()
  }

  async function loadProfile(): Promise<void> {
    if (!token.value) return
    try {
      profile.value = await fetchMe()
      profileError.value = ''
    } catch (error) {
      // 登录态失效：清 token 回登录页；其余错误只提示（管理端可能暂无角色码）
      if (errorStatus(error) === 401) {
        signOut()
        return
      }
      profileError.value = '无法读取当前登录信息'
    }
  }

  function signOut(): void {
    clearToken()
    token.value = ''
    profile.value = null
    profileError.value = ''
  }

  return { token, profile, profileError, isAuthenticated, roleLabel, signIn, loadProfile, signOut }
})
