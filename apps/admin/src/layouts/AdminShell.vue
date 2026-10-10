<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NDrawer, NDrawerContent } from 'naive-ui'
import { applyBrandingState, brandState, fetchPublicBranding } from '../api/branding'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const drawerOpen = ref(false)

interface MenuItem {
  key: string
  label: string
  group?: string
}

// 菜单按使用频率排序：概览 → 身份 → 模型 → 资源 → 运维，低频的"配置"组垫底
const MENU: MenuItem[] = [
  { key: '/dashboard', label: '概览', group: '概览' },
  { key: '/org-units', label: '组织单元', group: '身份' },
  { key: '/users', label: '用户', group: '身份' },
  { key: '/role-bindings', label: '角色绑定', group: '身份' },
  { key: '/grants', label: '资源授权', group: '身份' },
  { key: '/model-providers', label: '模型供应商', group: '模型' },
  { key: '/model-instances', label: '模型实例', group: '模型' },
  { key: '/tools', label: '工具', group: '资源' },
  { key: '/skills', label: '技能', group: '资源' },
  { key: '/knowledge', label: '知识库', group: '资源' },
  { key: '/devices', label: '设备', group: '运维' },
  { key: '/audit', label: '审计日志', group: '运维' },
  { key: '/quota', label: '配额用量', group: '运维' },
  { key: '/branding', label: '品牌管理', group: '配置' },
]

const GROUPED = computed(() => {
  const groups: Array<{ name: string; items: MenuItem[] }> = []
  for (const item of MENU) {
    let g = groups.find((x) => x.name === item.group)
    if (!g) {
      g = { name: item.group ?? '', items: [] }
      groups.push(g)
    }
    g.items.push(item)
  }
  return groups
})

// 取路径首段再匹配菜单：/knowledge/xxx 这类详情页也要让侧栏"知识库"保持高亮
const activeKey = computed(() => `/${String(route.path).replace(/^\//, '').split('/')[0]}`)
const pageTitle = computed(() => String(route.meta.title ?? '管理端'))
const pageDesc = computed(() => String(route.meta.desc ?? ''))
/**
 * 是否持有任意管理类权限码（*.manage）。用 permissions 而不是 roles 判断：
 * member 有角色（member）但只有 knowledge.read，按 roles 判断时全局警告永不显示，
 * 登录后会陷入"菜单全可点、全页 403"的迷宫而无解释。
 */
const hasManageScope = computed(() =>
  (auth.profile?.permissions ?? []).some((code) => code.endsWith('.manage')),
)
const permissionSummary = computed(() => {
  const perms = auth.profile?.permissions ?? []
  return perms.length ? perms.join('、') : '无'
})

onMounted(async () => {
  try {
    const branding = await fetchPublicBranding()
    if (branding) applyBrandingState(branding)
  } catch {
    // 品牌端点不可用时保留中性标题，不编造品牌名
  }
  if (auth.isAuthenticated) {
    await auth.loadProfile()
  }
})

function go(key: string): void {
  drawerOpen.value = false
  void router.push(key)
}

function signOut(): void {
  auth.signOut()
  drawerOpen.value = false
  void router.push('/login')
}
</script>

<template>
  <div class="min-h-screen flex bg-slate-100">
    <!-- 宽屏固定侧栏（lg 以上） -->
    <aside class="hidden lg:flex lg:flex-col lg:fixed lg:inset-y-0 lg:w-60 bg-slate-900 text-slate-100 border-r border-slate-800 z-40">
      <div class="px-5 py-4 text-[15px] font-semibold tracking-tight border-b border-slate-800 truncate">
        {{ brandState.displayName }}
        <span class="block text-[11px] font-normal text-slate-400 mt-0.5">管理控制台</span>
      </div>
      <nav class="flex-1 overflow-y-auto py-3 px-2.5 space-y-4">
        <div v-for="g in GROUPED" :key="g.name">
          <div class="px-2.5 pb-1.5 text-[11px] uppercase tracking-wider text-slate-500">{{ g.name }}</div>
          <button
            v-for="item in g.items"
            :key="item.key"
            class="w-full text-left px-2.5 py-1.5 rounded-md text-[13px] transition-colors cursor-pointer"
            :class="activeKey === item.key ? 'bg-brand text-white font-medium' : 'text-slate-300 hover:bg-slate-800 hover:text-white'"
            :aria-current="activeKey === item.key ? 'page' : undefined"
            @click="go(item.key)"
          >
            {{ item.label }}
          </button>
        </div>
      </nav>
      <div class="px-5 py-3 border-t border-slate-800 text-[11px] text-slate-500">ZY-Agents Admin</div>
    </aside>

    <!-- 主区（宽屏左侧留出侧栏宽度） -->
    <div class="flex-1 min-w-0 lg:ml-60 flex flex-col">
      <!-- 顶部 header -->
      <header class="sticky top-0 z-30 bg-white/95 backdrop-blur border-b border-slate-200 h-[52px] flex items-center gap-3 px-4 sm:px-6">
        <!-- 窄屏汉堡 -->
        <button
          class="lg:hidden p-1.5 -ml-1.5 rounded-md text-slate-600 hover:bg-slate-100 cursor-pointer"
          aria-label="打开导航"
          @click="drawerOpen = true"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>
        <div class="font-semibold text-sm sm:text-[15px] text-slate-800 truncate">{{ pageTitle }}</div>
        <div v-if="pageDesc" class="hidden md:block text-xs text-slate-500 truncate">{{ pageDesc }}</div>
        <div class="flex-1"></div>
        <span
          v-if="auth.profile"
          class="hidden sm:inline-flex px-2 py-0.5 rounded-full bg-brand-soft text-brand text-xs font-medium"
        >{{ auth.profile.displayName || auth.profile.username }}</span>
        <span
          v-if="auth.profile"
          class="inline-flex px-2 py-0.5 rounded-full text-xs font-medium"
          :class="hasManageScope ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'"
        >{{ auth.roleLabel }}</span>
        <button
          class="text-xs px-2.5 py-1 rounded-md text-slate-600 hover:bg-slate-100 cursor-pointer"
          @click="signOut"
        >退出</button>
      </header>

      <!-- 主内容 -->
      <main class="flex-1">
        <div class="page">
          <div
            v-if="auth.profile && !hasManageScope"
            class="mb-4 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-[13px] text-amber-800"
          >
            <span>
              当前登录态没有任何管理权限码（*.manage），后端管理接口会返回 403。当前权限码：{{ permissionSummary }}。这是后端鉴权结果，不是页面故障。
            </span>
          </div>
          <router-view />
        </div>
      </main>
    </div>

    <!-- 窄屏抽屉导航：NDrawer 自带 Esc 关闭与焦点管理（不再手写 overlay） -->
    <n-drawer :show="drawerOpen" placement="left" :width="240" @update:show="drawerOpen = $event">
      <n-drawer-content :body-content-style="{ padding: 0 }">
        <aside class="flex flex-col h-full bg-slate-900 text-slate-100">
          <div class="px-5 py-4 text-[15px] font-semibold border-b border-slate-800 truncate">
            {{ brandState.displayName }}
          </div>
          <nav class="flex-1 overflow-y-auto py-3 px-2.5 space-y-4">
            <div v-for="g in GROUPED" :key="g.name">
              <div class="px-2.5 pb-1.5 text-[11px] uppercase tracking-wider text-slate-500">{{ g.name }}</div>
              <button
                v-for="item in g.items"
                :key="item.key"
                class="w-full text-left px-2.5 py-1.5 rounded-md text-[13px] transition-colors cursor-pointer"
                :class="activeKey === item.key ? 'bg-brand text-white font-medium' : 'text-slate-300 hover:bg-slate-800 hover:text-white'"
                :aria-current="activeKey === item.key ? 'page' : undefined"
                @click="go(item.key)"
              >
                {{ item.label }}
              </button>
            </div>
          </nav>
        </aside>
      </n-drawer-content>
    </n-drawer>
  </div>
</template>
