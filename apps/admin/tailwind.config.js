/** Tailwind 配置：admin-web 管理后台（Vue 3 + Naive UI 共存）。
 * 品牌色经 CSS 变量注入（/branding 端点），此处不做静态映射。 */
export default {
  content: ['./index.html', './src/**/*.{vue,js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: 'var(--brand-primary)',
        // 品牌色的浅底（10% 混白），用于徽章等软色背景，避免硬编码 blue-50
        'brand-soft': 'color-mix(in srgb, var(--brand-primary) 10%, white)',
        accent: 'var(--brand-accent)',
      },
      fontFamily: {
        sans: [
          '-apple-system',
          'BlinkMacSystemFont',
          'Segoe UI',
          'PingFang SC',
          'Hiragino Sans GB',
          'Microsoft YaHei',
          'sans-serif',
        ],
      },
    },
  },
  plugins: [],
}
