/**
 * 开发/演示用的种子账号清单（**仅 dev 构建存在**）。
 *
 * ## 为什么放在前端
 *
 * 阶段 1 的登录后端（`services/api/app/identity/seed.py`）会幂等写入三个种子账号，
 * 口令经 `SEED_*_PASSWORD` 环境变量注入（不落代码，本地开发在 `.env` 配置）。
 * 把这三个账号的用户名做成登录页上的快捷填充，省掉重复输入，也顺带把
 * "哪个账号有哪些权限"摆在明面上——三个角色权限差异是阶段 2 的验收项之一。
 * 口令不在本文件中，快捷填充只填用户名，口令仍需手工输入。
 *
 * ## 为什么用 `import.meta.env.DEV` 守卫
 *
 * 快捷填充只在本地演示有意义。`import.meta.env.DEV` 在 `vite build` 时为
 * `false`，本模块会被静态消除（tree-shaking），生产产物里不含本清单。
 *
 * ⚠️ 生产部署前必须改掉种子密码或删除种子账号，见 `deploy/SEED-ACCOUNTS.md`。
 */

export interface DevCredential {
  /** 角色中文名，用于按钮标签 */
  readonly role: string
  /** 该账号实际拥有的权限码（与后端 `seed.py` 的 `*_PERMISSIONS` 对齐） */
  readonly note: string
  readonly username: string
}

/** 按权限从高到低排列，默认预填第一个（管理员）。 */
const CREDENTIALS: DevCredential[] = [
  {
    role: '管理员',
    username: 'admin',
    note: 'org/user/model/tool/skill/device/audit/quota/acl/brand 共 10 项',
  },
  {
    role: '运维',
    username: 'ops',
    note: '仅 tool.manage + device.manage，可演示"权限不足 403"',
  },
  {
    role: '普通成员',
    username: 'member',
    note: '仅 knowledge.read，进管理端会大面积 403',
  },
]

/**
 * 可用的开发凭据。生产构建下为空数组——调用方据此隐藏快捷填充区域。
 * 空数组而非 `null`：调用方不必写分支判空，v-for 空转即可。
 */
export const DEV_CREDENTIALS: DevCredential[] = import.meta.env.DEV ? CREDENTIALS : []
