import assert from 'node:assert/strict'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'

test('个人空间和当前对话文件搜索请求指定结果页', async () => {
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
      return new Response(JSON.stringify({ entries: [], has_more: false, truncated: false }), {
        status: 200,
        headers: { 'content-type': 'application/json' }
      })
    }
    setActivePinia(createPinia())
    const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
    useUserStore().token = 'test-token'
    const { searchWorkspaceFiles } = await server.ssrLoadModule('/src/apis/workspace_api.js')
    const { searchViewerFiles } = await server.ssrLoadModule('/src/apis/viewer_filesystem.js')

    await searchWorkspaceFiles('报告', { offset: 100, limit: 50 })
    await searchViewerFiles('thread-1', '报告', { offset: 100, limit: 50 })

    assert.deepEqual(requests, [
      '/api/workspace/search?query=%E6%8A%A5%E5%91%8A&offset=100&limit=50',
      '/api/viewer/filesystem/search?thread_id=thread-1&query=%E6%8A%A5%E5%91%8A&offset=100&limit=50'
    ])
  } finally {
    globalThis.fetch = previousFetch
    if (previousStorage) Object.defineProperty(globalThis, 'localStorage', previousStorage)
    else delete globalThis.localStorage
    await server.close()
  }
})
