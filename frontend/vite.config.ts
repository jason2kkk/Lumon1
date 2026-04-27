import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const raw = env.VITE_DEV_TUNNEL_HOST?.trim()
  const tunnelHost = raw
    ? raw.replace(/^https?:\/\//, '').split('/')[0].split(':')[0]
    : undefined

  return {
    plugins: [react(), tailwindcss()],
    server: {
      host: true,
      // Cloudflare Tunnel / 自定义域名访问开发服务器时避免 Host 校验失败
      allowedHosts: true,
      ...(tunnelHost
        ? {
            hmr: {
              host: tunnelHost,
              protocol: 'wss',
              clientPort: 443,
            },
          }
        : {}),
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
      },
    },
  }
})
