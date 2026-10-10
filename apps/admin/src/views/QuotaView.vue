<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NAlert, NButton, NGrid, NGridItem, NStatistic } from 'naive-ui'
import { fetchQuotaUsage, type QuotaUsage } from '../api/admin'
import { errorMessage, isPermissionDenied } from '../api/client'

/** Token 数量千分位。 */
const fmtNumber = (n: number) => new Intl.NumberFormat('zh-CN').format(n)

/** GET /api/v1/admin/quota（需 quota.read）—— 当前登录用户本月用量。 */
const usage = ref<QuotaUsage | null>(null)
const loading = ref(false)
const error = ref('')
const denied = ref(false)

const totalTokens = () =>
  (usage.value?.model_tokens_prompt ?? 0) + (usage.value?.model_tokens_completion ?? 0)

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  denied.value = false
  try {
    usage.value = await fetchQuotaUsage()
  } catch (err) {
    usage.value = null
    denied.value = isPermissionDenied(err)
    error.value = denied.value
      ? `${errorMessage(err)} —— 配额查询需要 quota.read 角色码`
      : errorMessage(err)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <n-alert v-if="error" :type="denied ? 'warning' : 'error'" :bordered="false" style="margin-bottom: 14px">
      {{ error }}
      <!-- 缺权限码时重试必然失败，只给提示 -->
      <template v-if="!denied" #action>
        <n-button size="tiny" quaternary @click="load">重试</n-button>
      </template>
    </n-alert>

    <div class="panel" v-if="usage">
      <h2 class="panel-title">本月用量</h2>
      <p class="panel-hint">
        对应 <code class="mono">GET /api/v1/admin/quota</code>：数据源是后端配额集合（没有记录时后端返回全 0，
        表示本月尚无用量，而非查询失败）。
      </p>
      <n-grid :cols="4" :x-gap="16" :y-gap="12">
        <n-grid-item>
          <n-statistic label="输入 Token" :value="fmtNumber(usage.model_tokens_prompt)" />
        </n-grid-item>
        <n-grid-item>
          <n-statistic label="输出 Token" :value="fmtNumber(usage.model_tokens_completion)" />
        </n-grid-item>
        <n-grid-item>
          <n-statistic label="合计 Token" :value="fmtNumber(totalTokens())" />
        </n-grid-item>
        <n-grid-item>
          <n-statistic label="工具调用次数" :value="fmtNumber(usage.tool_calls)" />
        </n-grid-item>
      </n-grid>
      <n-button size="small" style="margin-top: 14px" :loading="loading" @click="load">刷新</n-button>
    </div>
  </div>
</template>
