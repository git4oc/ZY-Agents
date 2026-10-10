<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { loginWithPassword, selectTenantAndLogin, type TenantCandidate, type UserProfile } from '../../api/auth'
import { t } from '../../hooks/i18n'

const props = defineProps<{
  suggestedUsername?: string
}>()

const emit = defineEmits<{
  (event: 'login-success', payload: { token: string; username: string; profile?: UserProfile }): void
}>()

const username = ref('')
const password = ref('')
const errorMessage = ref('')
const isSubmitting = ref(false)
const challengeToken = ref('')
const tenantCandidates = ref<TenantCandidate[]>([])
const selectedMainId = ref('')
const pendingSelection = computed(() => Boolean(challengeToken.value) && tenantCandidates.value.length > 0)

watch(
  () => props.suggestedUsername,
  (value) => {
    if (!username.value) username.value = value || ''
  },
  { immediate: true },
)

// 种子账号（用户名与后端种子一致）：口令由服务端环境变量注入，不落前端代码
const seedAccounts = [
  { username: 'admin', label: t('login.seed_admin') },
  { username: 'member', label: t('login.seed_member') },
]
if (!username.value) {
  username.value = seedAccounts[0].username
}

function fillSeedAccount(seed: { username: string }) {
  username.value = seed.username
  resetError()
}

function resetError() {
  errorMessage.value = ''
}

async function submit() {
  resetError()
  const normalizedUsername = username.value.trim()
  if (!normalizedUsername) {
    errorMessage.value = t('login.username_empty')
    return
  }
  if (!password.value) {
    errorMessage.value = t('login.password_empty')
    return
  }

  isSubmitting.value = true
  try {
    const result = pendingSelection.value
      ? await selectTenantAndLogin(challengeToken.value, selectedMainId.value)
      : await loginWithPassword(normalizedUsername, password.value)

    if (result.requiresTenantSelection) {
      challengeToken.value = result.challengeToken || ''
      tenantCandidates.value = result.tenantCandidates || []
      selectedMainId.value = tenantCandidates.value[0]?.mainId || ''
      errorMessage.value = result.message || t('api.auth.select_org')
      return
    }
    if (!result.ok || !result.token) {
      errorMessage.value = result.message || t('api.auth.login_failed')
      return
    }
    emit('login-success', { token: result.token, username: normalizedUsername, profile: result.profile })
  } catch (error: any) {
    console.error('[auth] password login completion failed', error)
    errorMessage.value = error?.message || t('api.auth.login_failed')
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <form class="space-y-4" @submit.prevent="submit">
    <div class="flex items-center justify-between gap-2">
      <span class="text-xs text-slate-400">{{ t('login.seed_accounts_label') }}</span>
      <div class="flex gap-1.5">
        <button
          v-for="seed in seedAccounts"
          :key="seed.username"
          type="button"
          class="rounded-full border px-2.5 py-1 text-xs transition-colors"
          :class="username === seed.username ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-200 text-slate-600 hover:border-slate-400'"
          @click="fillSeedAccount(seed)"
        >
          {{ seed.label }}
        </button>
      </div>
    </div>

    <div class="space-y-2">
      <label for="zy-login-username" class="text-sm font-medium text-slate-700">{{ t('login.username_label') }}</label>
      <input
        id="zy-login-username"
        v-model="username"
        type="text"
        autocomplete="username"
        class="min-h-[44px] w-full rounded-2xl border border-slate-200 px-4 text-slate-900 outline-none transition-colors focus:border-slate-400 focus-visible:ring-2 focus-visible:ring-slate-200"
        :placeholder="t('login.username_placeholder')"
        :disabled="pendingSelection"
        @input="resetError"
      />
    </div>

    <div class="space-y-2">
      <label for="zy-login-password" class="text-sm font-medium text-slate-700">{{ t('login.password_label') }}</label>
      <input
        id="zy-login-password"
        v-model="password"
        type="password"
        autocomplete="current-password"
        class="min-h-[44px] w-full rounded-2xl border border-slate-200 px-4 text-slate-900 outline-none transition-colors focus:border-slate-400 focus-visible:ring-2 focus-visible:ring-slate-200"
        :placeholder="t('login.password_placeholder')"
        :disabled="pendingSelection"
        @input="resetError"
      />
    </div>

    <div v-if="pendingSelection" class="space-y-2">
      <label for="zy-login-tenant" class="text-sm font-medium text-slate-700">{{ t('login.select_org_label') }}</label>
      <select
        id="zy-login-tenant"
        v-model="selectedMainId"
        class="min-h-[44px] w-full rounded-2xl border border-slate-200 px-4 text-slate-900 outline-none focus:border-slate-400 focus-visible:ring-2 focus-visible:ring-slate-200"
        @change="resetError"
      >
        <option v-for="tenant in tenantCandidates" :key="tenant.mainId" :value="tenant.mainId">
          {{ tenant.orgName || tenant.mainId }}
        </option>
      </select>
    </div>

    <p v-if="errorMessage" role="alert" class="rounded-2xl bg-rose-50 px-3 py-2 text-sm text-rose-600">
      {{ errorMessage }}
    </p>

    <button
      type="submit"
      class="min-h-[44px] w-full rounded-2xl bg-slate-900 px-4 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-70"
      :disabled="isSubmitting || (pendingSelection && !selectedMainId)"
    >
      {{ isSubmitting ? t('phase.verifying') : pendingSelection ? t('login.btn_enter_org') : t('login.password_submit') }}
    </button>

    <p class="rounded-2xl bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-500">
      {{ pendingSelection ? t('login.multiple_org_notice') : t('login.password_help') }}
    </p>
  </form>
</template>
