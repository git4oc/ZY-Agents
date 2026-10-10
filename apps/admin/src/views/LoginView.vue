<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NAlert, NButton, NCard, NForm, NFormItem, NInput, NSpace } from 'naive-ui'
import { fetchPublicBranding } from '../api/branding'
import { errorMessage } from '../api/client'
import { DEV_CREDENTIALS, type DevCredential } from '../devCredentials'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

/** 会话过期被踢回登录页时（401 handler 带 expired=1），明确告知原因。 */
const sessionExpired = computed(() => route.query.expired === '1')

// dev 构建下预填第一个种子账号（管理员）；生产构建 DEV_CREDENTIALS 为空数组，
// 于是表单保持空白，页面也不渲染快捷填充区。见 src/devCredentials.ts 的说明。
const username = ref(DEV_CREDENTIALS[0]?.username ?? '')
const password = ref('')
const submitting = ref(false)
const error = ref('')
// 品牌名来自公开端点 /branding（S-62：不硬编码品牌字面量）
const productName = ref('')

/** 当前选中的是哪个种子账号，用于展示权限范围说明。 */
const activeCredential = computed<DevCredential | undefined>(() =>
  DEV_CREDENTIALS.find((item) => item.username === username.value),
)

/** 点快捷按钮即填充，并清掉上一轮的报错，避免"填了新账号却还挂着旧提示"。 */
function applyCredential(credential: DevCredential): void {
  username.value = credential.username
  error.value = ''
}

onMounted(async () => {
  try {
    const branding = await fetchPublicBranding()
    if (branding?.display_name) productName.value = branding.display_name
  } catch {
    // 公开端点不可用：留空，由下方中性文案兜底
  }
})

async function submit(): Promise<void> {
  if (!username.value.trim() || !password.value) {
    error.value = '请输入用户名与密码'
    return
  }
  submitting.value = true
  error.value = ''
  try {
    await auth.signIn(username.value.trim(), password.value)
    // 兜底落地概览页（原来是 /branding——品牌管理是低频配置页，不配作登录首因路径）；
    // redirect 只接受站内路径，防开放重定向
    const raw = typeof route.query.redirect === 'string' ? route.query.redirect : '/dashboard'
    const redirect = raw.startsWith('/') && !raw.startsWith('//') ? raw : '/dashboard'
    await router.push(redirect)
  } catch (err) {
    error.value = errorMessage(err, '登录失败')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div style="min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 24px">
    <n-card style="width: 100%; max-width: 400px" :bordered="false">
      <div style="margin-bottom: 4px; font-size: 18px; font-weight: 650">
        {{ productName || '管理端' }}
      </div>
      <div class="muted" style="margin-bottom: 18px; font-size: 13px">使用平台账号登录（JWT 存于本地，仅存管理端 key）</div>

      <n-alert v-if="sessionExpired" type="warning" :bordered="false" style="margin-bottom: 14px">
        会话已过期，请重新登录
      </n-alert>

      <n-alert v-if="error" type="error" :bordered="false" style="margin-bottom: 14px">
        {{ error }}
      </n-alert>

      <form v-if="DEV_CREDENTIALS.length" style="margin-bottom: 16px">
        <div class="muted" style="font-size: 12px; margin-bottom: 7px">开发种子账号（点击填充）</div>
        <n-space size="small">
          <n-button
            v-for="credential in DEV_CREDENTIALS"
            :key="credential.username"
            size="small"
            :type="username === credential.username ? 'primary' : 'default'"
            :disabled="submitting"
            @click="applyCredential(credential)"
          >
            {{ credential.role }}
          </n-button>
        </n-space>
        <div v-if="activeCredential" class="muted" style="font-size: 12px; margin-top: 7px">
          {{ activeCredential.username }} 拥有：{{ activeCredential.note }}
        </div>
      </form>

      <n-form @submit.prevent="submit">
        <n-form-item label="用户名">
          <n-input v-model:value="username" placeholder="用户名" autocomplete="username" @keyup.enter="submit" />
        </n-form-item>
        <n-form-item label="密码">
          <n-input
            v-model:value="password"
            type="password"
            placeholder="密码"
            autocomplete="current-password"
            show-password-on="click"
            @keyup.enter="submit"
          />
        </n-form-item>
        <n-space style="width: 100%; justify-content: flex-end">
          <n-button type="primary" :loading="submitting" @click="submit">登录</n-button>
        </n-space>
      </n-form>
    </n-card>
  </div>
</template>