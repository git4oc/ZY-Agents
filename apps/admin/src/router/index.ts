import { createRouter, createWebHashHistory, type RouteRecordRaw } from 'vue-router'
import { getToken } from '../api/client'
import AdminShell from '../layouts/AdminShell.vue'

/** 管理端路由：登录页 + 首页概览 + 各管理页（全部有后端读端点；写端点能力边界见 api/admin.ts 注释）。 */
const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/LoginView.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/',
    component: AdminShell,
    children: [
      { path: '', redirect: '/dashboard' },
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('../views/DashboardView.vue'),
        meta: { title: '概览', group: '概览', desc: '本月用量、资源计数与最近审计' },
      },
      {
        path: 'branding',
        name: 'branding',
        component: () => import('../views/BrandingView.vue'),
        meta: { title: '品牌管理', group: '配置', desc: '唯一具备完整写能力的资源' },
      },
      {
        path: 'org-units',
        name: 'org-units',
        component: () => import('../views/OrgUnitsView.vue'),
        meta: { title: '组织单元', group: '身份' },
      },
      {
        path: 'users',
        name: 'users',
        component: () => import('../views/UsersView.vue'),
        meta: { title: '用户', group: '身份' },
      },
      {
        path: 'role-bindings',
        name: 'role-bindings',
        component: () => import('../views/RoleBindingsView.vue'),
        meta: { title: '角色绑定', group: '身份' },
      },
      {
        path: 'grants',
        name: 'grants',
        component: () => import('../views/GrantsView.vue'),
        meta: { title: '资源授权', group: '身份' },
      },
      {
        path: 'model-providers',
        name: 'model-providers',
        component: () => import('../views/ModelProvidersView.vue'),
        meta: { title: '模型供应商', group: '模型' },
      },
      {
        path: 'model-instances',
        name: 'model-instances',
        component: () => import('../views/ModelInstancesView.vue'),
        meta: { title: '模型实例', group: '模型' },
      },
      {
        path: 'tools',
        name: 'tools',
        component: () => import('../views/ToolsView.vue'),
        meta: { title: '工具', group: '资源' },
      },
      {
        path: 'skills',
        name: 'skills',
        component: () => import('../views/SkillsView.vue'),
        meta: { title: '技能', group: '资源' },
      },
      {
        path: 'knowledge',
        name: 'knowledge',
        component: () => import('../views/KnowledgeView.vue'),
        meta: { title: '知识库', group: '资源', desc: '全 org 文档、解析状态与上传' },
      },
      {
        // 无 children 的 'knowledge' 是精确路径，不会吞掉 /knowledge/:id，两条顺序无关
        path: 'knowledge/:id',
        name: 'knowledge-detail',
        component: () => import('../views/KnowledgeDetailView.vue'),
        meta: { title: '文档详情', group: '资源' },
      },
      {
        path: 'devices',
        name: 'devices',
        component: () => import('../views/DevicesView.vue'),
        meta: { title: '设备', group: '运维' },
      },
      {
        path: 'audit',
        name: 'audit',
        component: () => import('../views/AuditView.vue'),
        meta: { title: '审计日志', group: '运维' },
      },
      {
        path: 'quota',
        name: 'quota',
        component: () => import('../views/QuotaView.vue'),
        meta: { title: '配额用量', group: '运维' },
      },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
]

export const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

router.beforeEach((to) => {
  const authed = Boolean(getToken())
  if (!to.meta.public && !authed) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (to.path === '/login' && authed) {
    return { path: '/dashboard' }
  }
  return true
})
