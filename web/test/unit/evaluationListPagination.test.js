import assert from 'node:assert/strict'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'

test('评估基准与运行列表把页码、服务端筛选和单条定位传给接口', async () => {
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
      return new Response(JSON.stringify({ message: 'success', data: [], pagination: { total: 0 } }), {
        status: 200,
        headers: { 'content-type': 'application/json' }
      })
    }
    setActivePinia(createPinia())
    const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
    useUserStore().userRole = 'superadmin'
    const { evaluationApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js')

    await evaluationApi.listDatasets('kb-1', {
      limit: 20,
      offset: 40,
      status: 'completed',
      search: '历史 基准'
    })
    await evaluationApi.listRuns('kb-1', { limit: 20, datasetId: 'ds-1', beforeId: 73 })
    await evaluationApi.listRuns('kb-1', { limit: 1, offset: 0, runId: 'run-old' })

    assert.deepEqual(requests, [
      '/api/evaluation/databases/kb-1/datasets?limit=20&offset=40&status=completed&search=%E5%8E%86%E5%8F%B2+%E5%9F%BA%E5%87%86',
      '/api/evaluation/databases/kb-1/runs?limit=20&dataset_id=ds-1&before_id=73',
      '/api/evaluation/databases/kb-1/runs?limit=1&offset=0&run_id=run-old'
    ])
  } finally {
    globalThis.fetch = previousFetch
    if (previousStorage) Object.defineProperty(globalThis, 'localStorage', previousStorage)
    else delete globalThis.localStorage
    await server.close()
  }
})
