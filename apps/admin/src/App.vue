<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  NConfigProvider,
  NDialogProvider,
  NMessageProvider,
  NNotificationProvider,
  type GlobalThemeOverrides,
} from 'naive-ui'
import { fetchPublicBranding } from './api/branding'
import { errorMessage, setUnauthorizedHandler } from './api/client'
import { useAuthStore } from './stores/auth'
import { router } from './router'

const auth = useAuthStore()

// 主题色跟随品牌配置（S-62：品牌与配色一律从 /branding 读，不在前端硬编码）
const primaryColor = ref('#2563eb')
const themeOverrides = computed<GlobalThemeOverrides>(() => ({
  common: {
    primaryColor: primaryColor.value,
    primaryColorHover: primaryColor.value,
    primaryColorPressed: primaryColor.value,
    primaryColorSuppl: primaryColor.value,
  },
}))

onMounted(async () => {
  setUnauthorizedHandler(() => {
    auth.signOut()
    // 带 expired 标记：登录页据此显示"会话已过期"，而不是让用户误以为误触了退出
    void router.push({ path: '/login', query: { expired: '1' } })
  })
  try {
    const branding = await fetchPublicBranding()
    if (branding?.display_name) {
      document.title = `${branding.display_name} · 管理端`
    }
    if (branding?.theme?.primary) {
      primaryColor.value = branding.theme.primary
      document.documentElement.style.setProperty('--brand-primary', branding.theme.primary)
    }
    if (branding?.theme?.accent) {
      document.documentElement.style.setProperty('--brand-accent', branding.theme.accent)
    }
  } catch (error) {
    console.warn('[admin] 品牌配置读取失败，使用默认标题：', errorMessage(error))
  }
  if (auth.isAuthenticated) {
    await auth.loadProfile()
  }
})
</script>

<template>
  <n-config-provider :theme-overrides="themeOverrides">
    <n-message-provider>
      <n-dialog-provider>
        <n-notification-provider>
          <router-view />
        </n-notification-provider>
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>
