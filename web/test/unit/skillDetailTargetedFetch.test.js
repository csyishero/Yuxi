import assert from 'node:assert/strict'
import { existsSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { pid } from 'node:process'
import test from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { fileURLToPath } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { message } from 'ant-design-vue'
import { createPinia, setActivePinia } from 'pinia'
import { createRenderer, h, nextTick, ssrContextKey } from 'vue'
import { compileScript, parse } from 'vue/compiler-sfc'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createServer } from 'vite'

const node = (type, text = '') => ({ type, text, props: {}, children: [], parent: null, style: {} })
const renderer = createRenderer({
  createElement: (type) => node(type),
  createText: (value) => node('text', value),
  createComment: (value) => node('comment', value),
  insert(child, parent) { parent.children.push(child); child.parent = parent },
  remove(child) {
    const index = child.parent?.children.indexOf(child) ?? -1
    if (index >= 0) child.parent.children.splice(index, 1)
    child.parent = null
  },
  setText(target, value) { target.text = value },
  setElementText(target, value) { target.text = value; target.children = [] },
  parentNode: (target) => target.parent,
  nextSibling: () => null,
  patchProp(target, key, _old, value) { target.props[key] = value }
})

test('技能详情按 slug 获取后续页的记录，不请求全量目录', async () => {
  const componentDir = new URL('../../src/components/extensions/', import.meta.url)
  const compiledPath = fileURLToPath(new URL(`.skill-detail-targeted-${pid}.mjs`, componentDir))
  const originalFetch = globalThis.fetch
  const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  const originalError = message.error
  const requested = []
  let app, server
  try {
    const { descriptor } = parse(readFileSync(new URL('SkillDetailView.vue', componentDir), 'utf8'))
    writeFileSync(compiledPath, compileScript(descriptor, { id: 'skill-detail-targeted', inlineTemplate: true }).content)
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      value: { getItem: () => null, setItem() {}, removeItem() {} }
    })
    globalThis.fetch = async (url) => {
      const path = String(url)
      requested.push(path)
      if (path === '/api/system/skills/later-skill/details') return Response.json({ data: {
        slug: 'later-skill', name: 'Later Skill', source_scope: 'shared', source_type: 'upload',
        enabled: true, can_manage: false, tool_dependencies: [], mcp_dependencies: [], skill_dependencies: []
      } })
      if (path === '/api/system/skills/later-skill/tree') return Response.json({ data: [] })
      if (path === '/api/system/skills/later-skill/file?path=SKILL.md') return Response.json({ data: { content: '# Later' } })
      if (path === '/api/system/skills/dependency-options?slug=later-skill') return Response.json({ data: { tools: [], mcps: [], skills: [] } })
      if (path === '/api/system/skills' || path.startsWith('/api/system/skills?')) throw new Error('全量目录不应被请求')
      return Response.json({ data: [] })
    }
    message.error = () => {}
    server = await createServer({
      configFile: false,
      plugins: [vue()],
      resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
      server: { middlewareMode: true, hmr: false },
      appType: 'custom',
      optimizeDeps: { noDiscovery: true }
    })
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/extensions/skill/:slug', component: { render: () => h('div') } },
      { path: '/extensions', component: { render: () => h('div') } }
    ] })
    await router.push('/extensions/skill/later-skill')
    const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
    const user = useUserStore()
    user.token = 'test-token'
    user.uid = 'reader'
    const { default: SkillDetailView } = await server.ssrLoadModule(`/src/components/extensions/.skill-detail-targeted-${pid}.mjs`)
    app = renderer.createApp(SkillDetailView)
    app.use(pinia)
    app.use(router)
    for (const name of ['a-button', 'a-space', 'a-tooltip', 'a-empty', 'a-switch', 'a-input', 'a-checkbox', 'a-dropdown', 'a-form-item', 'a-textarea', 'a-form', 'a-modal']) {
      app.component(name, { render: () => h('div') })
    }
    app.provide(ssrContextKey, { modules: new Set() })
    app.mount(node('root'))
    await setImmediate()
    await nextTick()

    assert.ok(requested.includes('/api/system/skills/later-skill/details'))
    assert.equal(requested.some((path) => path === '/api/system/skills' || path.startsWith('/api/system/skills?')), false)
  } finally {
    app?.unmount()
    await server?.close()
    if (existsSync(compiledPath)) unlinkSync(compiledPath)
    globalThis.fetch = originalFetch
    message.error = originalError
    if (originalStorage) Object.defineProperty(globalThis, 'localStorage', originalStorage)
    else delete globalThis.localStorage
  }
})
