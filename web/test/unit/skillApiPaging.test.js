import assert from 'node:assert/strict'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'

test('技能卡片与管理员个人目录请求都把搜索和续页条件传给服务器', async () => {
  const originalFetch = globalThis.fetch
  const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  let server
  try {
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      value: { getItem: () => null, setItem() {}, removeItem() {} }
    })
    setActivePinia(createPinia())
    const requested = []
    globalThis.fetch = async (url) => {
      requested.push(String(url))
      return Response.json({ data: [], total: 0, has_more: false })
    }
    server = await createServer({
      configFile: false,
      resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
      server: { middlewareMode: true, hmr: false },
      appType: 'custom',
      optimizeDeps: { noDiscovery: true }
    })
    const { listSkillCards, listPersonalSkillCatalog } = await server.ssrLoadModule('/src/apis/skill_api.js')
    const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
    const user = useUserStore()
    user.token = 'test-token'

    await listSkillCards({ source: 'shared', limit: 30, offset: 30, search: '分析', audienceDepartmentId: 3 })
    await listPersonalSkillCatalog({ limit: 30, offset: 30, search: '报告', ownerSearch: 'alice', departmentId: 3, excludeOwn: true, cardPage: true })

    assert.equal(requested.length, 2)
    const url = new URL(requested[0], 'http://local.test')
    assert.equal(url.pathname, '/api/skills')
    assert.equal(url.searchParams.get('source'), 'shared')
    assert.equal(url.searchParams.get('limit'), '30')
    assert.equal(url.searchParams.get('offset'), '30')
    assert.equal(url.searchParams.get('search'), '分析')
    assert.equal(url.searchParams.get('audience_department_id'), '3')

    const catalogUrl = new URL(requested[1], 'http://local.test')
    assert.equal(catalogUrl.pathname, '/api/skills/personal-catalog')
    assert.equal(catalogUrl.searchParams.get('limit'), '30')
    assert.equal(catalogUrl.searchParams.get('offset'), '30')
    assert.equal(catalogUrl.searchParams.get('search'), '报告')
    assert.equal(catalogUrl.searchParams.get('owner_search'), 'alice')
    assert.equal(catalogUrl.searchParams.get('department_id'), '3')
    assert.equal(catalogUrl.searchParams.get('exclude_own'), 'true')
    assert.equal(catalogUrl.searchParams.get('card_page'), 'true')
  } finally {
    await server?.close()
    globalThis.fetch = originalFetch
    if (originalStorage) Object.defineProperty(globalThis, 'localStorage', originalStorage)
    else delete globalThis.localStorage
  }
})
