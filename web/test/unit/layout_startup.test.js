import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { createRenderer, h, ssrContextKey } from 'vue'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createServer } from 'vite'

test('布局导航不等待品牌或知识库，卸载后清理状态同步计时器', async (t) => {
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  globalThis.window = { addEventListener() {}, removeEventListener() {} }
  const server = await createServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom'
  })
  const pinia = createPinia()
  setActivePinia(pinia)
  let app
  try {
    const calls = []
    for (const [module, storeName, method, pending] of [
      ['info', 'useInfoStore', 'loadInfoConfig', true],
      ['config', 'useConfigStore', 'refreshConfig', false],
      ['chatThreads', 'useChatThreadsStore', 'loadThreads', false],
      ['projects', 'useProjectsStore', 'loadProjects', false]
    ]) {
      const exports = await server.ssrLoadModule(`/src/stores/${module}.js`)
      const store = exports[storeName]()
      t.mock.method(store, method, () => {
        calls.push(method)
        return pending ? new Promise(() => {}) : Promise.resolve()
      })
    }
    const { useAgentStore } = await server.ssrLoadModule('/src/stores/agent.js')
    useAgentStore().isInitialized = true
    const { default: Layout } = await server.ssrLoadModule('/src/layouts/AppLayout.vue')
    const renderer = createRenderer({
      createElement: () => ({}),
      createText: () => ({}),
      createComment: () => ({}),
      insert() {},
      remove() {},
      setText() {},
      setElementText() {},
      patchProp() {},
      parentNode: () => null,
      nextSibling: () => null
    })
    app = renderer.createApp({ ...Layout, render: () => h('div') })
    app.provide(ssrContextKey, { modules: new Set() })
    app.use(pinia)
    app.use(createRouter({ history: createMemoryHistory(), routes: [] }))
    const { useDatabaseStore } = await server.ssrLoadModule('/src/stores/database.js')
    const database = app.runWithContext(() => useDatabaseStore())
    t.mock.method(database, 'loadDatabases', () => {
      calls.push('loadDatabases')
      return new Promise(() => {})
    })
    const interval = t.mock.method(globalThis, 'setInterval', () => 123)
    const clear = t.mock.method(globalThis, 'clearInterval', () => {})
    app.mount({})
    await Promise.resolve()
    assert.ok(calls.includes('loadInfoConfig'))
    assert.ok(calls.includes('loadDatabases'))
    assert.ok(calls.includes('loadThreads'))
    assert.ok(calls.includes('loadProjects'))
    assert.ok(calls.includes('refreshConfig'))
    assert.equal(interval.mock.callCount(), 1)
    app.unmount()
    app = null
    assert.equal(clear.mock.calls[0].arguments[0], 123)
  } finally {
    app?.unmount()
    disposePinia(pinia)
    await server.close()
    delete globalThis.window
  }
})

test('登录时清理任务列表暂不可用，恢复连接后仍能找回任务', async (t) => {
  const values = new Map()
  globalThis.localStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key)
  }
  globalThis.window = { addEventListener() {}, removeEventListener() {} }
  const server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  const pinia = createPinia()
  setActivePinia(pinia)
  let app
  try {
    for (const [module, storeName, method] of [
      ['info', 'useInfoStore', 'loadInfoConfig'],
      ['config', 'useConfigStore', 'refreshConfig'],
      ['chatThreads', 'useChatThreadsStore', 'loadThreads'],
      ['projects', 'useProjectsStore', 'loadProjects'],
      ['database', 'useDatabaseStore', 'loadDatabases']
    ]) {
      const exports = await server.ssrLoadModule(`/src/stores/${module}.js`)
      t.mock.method(exports[storeName](), method, async () => {})
    }
    const { useAgentStore } = await server.ssrLoadModule('/src/stores/agent.js')
    useAgentStore().isInitialized = true
    const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
    useUserStore().uid = 'test-user'
    const { projectApi } = await server.ssrLoadModule('/src/apis/project_api.js')
    let listCalls = 0
    t.mock.method(projectApi, 'listWorkdirDeletions', async () => {
      listCalls += 1
      if (listCalls === 1) throw new Error('offline')
      return [{ project_id: 'lost-project', status: 'failed' }]
    })
    const timers = []
    t.mock.method(globalThis, 'setTimeout', (callback, delay) => {
      timers.push({ callback, delay })
      return timers.length
    })
    t.mock.method(globalThis, 'clearTimeout', () => {})
    t.mock.method(globalThis, 'setInterval', () => 123)
    t.mock.method(globalThis, 'clearInterval', () => {})

    const { default: Layout } = await server.ssrLoadModule('/src/layouts/AppLayout.vue')
    const renderer = createRenderer({
      createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
      insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {},
      parentNode: () => null, nextSibling: () => null
    })
    app = renderer.createApp({ ...Layout, render: () => h('div') })
    app.provide(ssrContextKey, { modules: new Set() })
    app.use(pinia)
    app.use(createRouter({ history: createMemoryHistory(), routes: [] }))
    app.mount({})
    await Promise.resolve()
    assert.equal(listCalls, 1)
    const retry = timers.find((timer) => timer.delay === 30000)
    assert.ok(retry)
    await retry.callback()
    assert.equal(listCalls, 2)
    assert.deepEqual(JSON.parse(values.get('yuxi:project-workdir-deletions:test-user')), ['lost-project'])
  } finally {
    app?.unmount()
    disposePinia(pinia)
    await server.close()
    delete globalThis.window
  }
})
