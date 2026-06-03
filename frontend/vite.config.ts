import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { NaiveUiResolver } from 'unplugin-vue-components/resolvers'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(__dirname, '..')

export default defineConfig(({ mode }) => {
  // Load env from the *repo root* (where .env lives), not from frontend/.
  // Empty prefix '' so we can read APP_PORT (not just VITE_* vars).
  const env = loadEnv(mode, repoRoot, '')

  const appPort = env.APP_PORT || '8000'

  return {
    plugins: [
      vue(),
      Components({
        resolvers: [NaiveUiResolver()],
        dts: 'src/components.d.ts',
      }),
    ],
    server: {
      host: 'localhost',
      port: 5173,
      strictPort: true,
      proxy: {
        '/api': `http://127.0.0.1:${appPort}`,
      },
    },
  }
})
