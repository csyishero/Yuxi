import assert from 'node:assert/strict'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'

test('共享与知识库授权按关键词请求后续候选用户页', async () => {
  const previousFetch = globalThis.fetch
  const previousStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  const requests = []
  const server = await createServer({
    configFile: false,
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, hmr: false },
    appType: 'custom',
    optimizeDeps: { noDiscovery: true }
  })
  try {
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      value: { getItem: () => 'test-token', setItem() {}, removeItem() {} }
    })
    globalThis.fetch = async (url) => {
      requests.push(String(url))
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'content-type': 'application/json' }
      })
    }
    setActivePinia(createPinia())
    const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
    useUserStore().userRole = 'superadmin'
    const { authApi } = await server.ssrLoadModule('/src/apis/auth_api.js')
    const { databaseApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js')

    await authApi.getUserAccessOptions({ skip: 1000, limit: 51, search: '张 三' })
    await databaseApi.getPermissionOptions('kb-1', { offset: 1000, limit: 50, search: '张 三' })

    assert.deepEqual(requests, [
      '/api/auth/users/access-options?skip=1000&limit=51&search=%E5%BC%A0+%E4%B8%89',
      '/api/knowledge/databases/kb-1/permission-options?offset=1000&limit=50&search=%E5%BC%A0+%E4%B8%89'
    ])
  } finally {
    globalThis.fetch = previousFetch
    if (previousStorage) Object.defineProperty(globalThis, 'localStorage', previousStorage)
    else delete globalThis.localStorage
    await server.close()
  }
})
