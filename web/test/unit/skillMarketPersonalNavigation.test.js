import assert from 'node:assert/strict'
import { existsSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { pid } from 'node:process'
import test from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { fileURLToPath } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { createPinia, setActivePinia } from 'pinia'
import { createRenderer, h, nextTick, ssrContextKey } from 'vue'
import { compileScript, parse } from 'vue/compiler-sfc'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createServer } from 'vite'

function node(type, text = '') {
  return { type, text, props: {}, children: [], parent: null, style: {} }
}

const renderer = createRenderer({
  createElement: (type) => node(type),
  createText: (value) => node('text', value),
  createComment: (value) => node('comment', value),
  insert(child, parent, anchor = null) {
    if (child.parent) this.remove(child)
    child.parent = parent
    const index = anchor ? parent.children.indexOf(anchor) : -1
    if (index < 0) parent.children.push(child)
    else parent.children.splice(index, 0, child)
  },
  remove(child) {
    const index = child.parent?.children.indexOf(child) ?? -1
    if (index >= 0) child.parent.children.splice(index, 1)
    child.parent = null
  },
  setText(target, value) { target.text = value },
  setElementText(target, value) {
    target.text = value
    target.children = []
  },
  parentNode: (target) => target.parent,
  nextSibling(target) {
    const siblings = target.parent?.children || []
    return siblings[siblings.indexOf(target) + 1] || null
  },
  patchProp(target, key, _old, value) { target.props[key] = value }
})

function findAll(target, predicate) {
  return [...(predicate(target) ? [target] : []), ...(target.children || []).flatMap((child) => findAll(child, predicate))]
}

function visibleText(target) {
  return [target.text, ...(target.children || []).map(visibleText)].join(' ')
}

const Slot = {
  setup(_props, { attrs, slots }) {
    return () => h('slot', attrs, slots.default?.())
  }
}

test('点击市场卡的查看个人技能进入个人详情', async () => {
  const componentDir = new URL('../../src/components/extensions/', import.meta.url)
  const compiledPath = fileURLToPath(new URL(`.skill-market-personal-navigation-${pid}.mjs`, componentDir))
  const originalFetch = globalThis.fetch
  const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  let app, server
  try {
    const { descriptor } = parse(readFileSync(new URL('SkillMarket.vue', componentDir), 'utf8'))
    writeFileSync(compiledPath, compileScript(descriptor, { id: 'skill-market-personal-navigation', inlineTemplate: true }).content)
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      value: { getItem: () => null, setItem() {}, removeItem() {} }
    })
    globalThis.fetch = async (url) => {
      const path = String(url)
      if (path.startsWith('/api/skill-market/requests?')) return Response.json({ data: { items: [], total: 0 } })
      const data = path.startsWith('/api/skill-market?')
        ? [{ slug: 'reading-market', name: 'reading', owner_uid: 'work1', owner_name: 'work1', own_personal_slug: 'reading', scope: 'department', department_id: 3, latest_version: 1, install_count: 0, likes_count: 0, category: 'other' }]
        : path.startsWith('/api/skills?')
          ? new URL(path, 'http://yuxi.local').searchParams.get('source') === 'shared' ? [] : [{ slug: 'reading', name: 'reading', source_scope: 'personal', owner_uid: 'work1', category: 'other' }]
          : []
      return new Response(JSON.stringify({ data, has_more: false }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }

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
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/extensions/skill/:slug', component: { render: () => h('div') } }]
    })
    const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
    const user = useUserStore()
    user.token = 'test-token'
    user.uid = 'work1'
    user.departmentId = 3
    const { default: SkillMarket } = await server.ssrLoadModule(`/src/components/extensions/.skill-market-personal-navigation-${pid}.mjs`)
    app = renderer.createApp(SkillMarket)
    app.use(pinia)
    app.use(router)
    app.provide(ssrContextKey, { modules: new Set() })
    for (const name of ['a-button', 'a-spin', 'a-input', 'a-input-search', 'a-select', 'a-alert', 'a-empty', 'a-tag', 'a-textarea', 'a-radio', 'a-radio-group']) {
      app.component(name, Slot)
    }
    for (const name of ['a-modal', 'a-drawer']) {
      app.component(name, { props: { open: Boolean }, setup: (props, { slots }) => () => props.open ? h('slot', {}, slots.default?.()) : null })
    }
    const host = node('root')
    app.mount(host)
    await setImmediate()
    await nextTick()

    const marketTab = findAll(host, (item) => item.type === 'button' && item.props.role === 'tab' && visibleText(item).trim() === '技能市场')[0]
    assert.ok(marketTab)
    marketTab.props.onClick()
    await nextTick()

    const action = findAll(host, (item) => item.type === 'slot' && visibleText(item).trim() === '查看个人技能')[0]
    assert.ok(action, '市场卡应显示“查看个人技能”操作')
    action.props.onClick()
    await setImmediate()
    assert.equal(router.currentRoute.value.fullPath, '/extensions/skill/reading?scope=personal')
  } finally {
    app?.unmount()
    await server?.close()
    if (existsSync(compiledPath)) unlinkSync(compiledPath)
    globalThis.fetch = originalFetch
    if (originalStorage) Object.defineProperty(globalThis, 'localStorage', originalStorage)
    else delete globalThis.localStorage
  }
})
