import assert from 'node:assert/strict'
import { existsSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { pid } from 'node:process'
import test from 'node:test'
import { setImmediate, setTimeout } from 'node:timers/promises'
import { fileURLToPath } from 'node:url'

import { createPinia, setActivePinia } from 'pinia'
import { createRenderer, h, nextTick, ssrContextKey } from 'vue'
import { compileScript, parse } from 'vue/compiler-sfc'
import { createServer } from 'vite'

const node = (type, text = '') => ({ type, text, props: {}, children: [], parent: null })
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
  setElementText(target, value) { target.text = value; target.children = [] },
  parentNode: (target) => target.parent,
  nextSibling(target) {
    const siblings = target.parent?.children || []
    return siblings[siblings.indexOf(target) + 1] || null
  },
  patchProp(target, key, _old, value) { target.props[key] = value }
})
const findAll = (target, predicate) => [
  ...(predicate(target) ? [target] : []),
  ...(target.children || []).flatMap((child) => findAll(child, predicate))
]
const visibleText = (target) => [target.text, ...(target.children || []).map(visibleText)].join(' ')
const tick = async () => { await setImmediate(); await nextTick() }
const model = (selected = []) => ({
  version: 2,
  read_scope: { access_level: 'user', department_ids: [], user_uids: selected },
  manage_scope: null
})

test('共享表单远程找到第 1001 位用户，保留已选项并遵守部门与提供的用户范围', async () => {
  const componentDir = new URL('../../src/components/', import.meta.url)
  const compiledPath = fileURLToPath(new URL(`.share-remote-users-${pid}.mjs`, componentDir))
  const originalFetch = globalThis.fetch
  const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  const requests = []
  const people = Array.from({ length: 1000 }, (_, index) => ({
    uid: `person-${index + 1}`,
    username: `Person ${index + 1}`,
    department_id: 1,
    department_name: '研发部',
    role: 'user'
  }))
  people.push({ uid: 'target-1001', username: 'Target 1001', department_id: 2, department_name: '市场部', role: 'user' })
  let app, server
  try {
    const { descriptor } = parse(readFileSync(new URL('ShareConfigForm.vue', componentDir), 'utf8'))
    writeFileSync(compiledPath, compileScript(descriptor, { id: 'share-remote-users', inlineTemplate: true }).content)
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      value: { getItem: () => 'test-token', setItem() {}, removeItem() {} }
    })
    server = await createServer({
      configFile: false,
      resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
      server: { middlewareMode: true, hmr: false },
      appType: 'custom',
      optimizeDeps: { noDiscovery: true }
    })
    const pinia = createPinia()
    setActivePinia(pinia)
    const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
    const user = useUserStore()
    user.token = 'test-token'
    user.uid = 'person-1'
    user.username = 'Person 1'
    user.departmentId = 1
    user.departmentName = '研发部'
    const { default: ShareConfigForm } = await server.ssrLoadModule(`/src/components/.share-remote-users-${pid}.mjs`)
    globalThis.fetch = async (url) => {
      const path = String(url)
      requests.push(path)
      if (path.startsWith('/api/auth/users/access-options/by-uids')) {
        const selectedUids = new URL(path, 'http://test').searchParams.getAll('uids')
        assert.ok(selectedUids.length <= 50, 'selected-user lookup must stay bounded')
        return Response.json(people.filter((person) =>
          selectedUids.includes(person.uid) &&
          (user.userRole === 'superadmin' || person.department_id === user.departmentId)
        ))
      }
      if (path.startsWith('/api/auth/users/access-options')) {
        const query = new URL(path, 'http://test').searchParams
        const search = query.get('search')?.toLowerCase() || ''
        const visible = people.filter((person) =>
          (user.userRole === 'superadmin' || person.department_id === user.departmentId) &&
          (person.username.toLowerCase().includes(search) || person.uid.toLowerCase().includes(search))
        )
        return Response.json(visible.slice(Number(query.get('skip') || 0), Number(query.get('skip') || 0) + Number(query.get('limit') || 1000)))
      }
      throw new Error(`Unexpected request: ${path}`)
    }

    const mount = (role, value = model(), extraProps = {}) => {
      user.userRole = role
      app = renderer.createApp(ShareConfigForm, {
        modelValue: value,
        showManageScope: false,
        availableDepartments: [],
        ...extraProps
      })
      app.use(pinia)
      app.provide(ssrContextKey, { modules: new Set() })
      app.component('a-dropdown', { setup: (_props, { slots }) => () => h('dropdown', [slots.default?.(), slots.overlay?.()]) })
      app.component('a-input', {
        props: ['value'],
        emits: ['update:value'],
        setup: (props, { attrs, emit }) => () => h('input', {
          ...attrs,
          value: props.value,
          onInput: (value) => emit('update:value', value)
        })
      })
      for (const name of ['a-button', 'a-tag', 'a-checkbox', 'a-switch', 'a-alert']) {
        app.component(name, { setup: (_props, { attrs, slots }) => () => h(name, attrs, slots.default?.()) })
      }
      const host = node('root')
      const instance = app.mount(host)
      return { host, instance }
    }
    const openPicker = async (host) => {
      const button = findAll(host, (item) => item.props['aria-label'] === '选择用户')[0]
      assert.ok(button, 'user picker must be mounted')
      button.props.onClick()
      await tick()
    }
    const search = async (host, query) => {
      const input = findAll(host, (item) => item.type === 'input' && item.props.placeholder === '搜索用户')[0]
      assert.ok(input)
      input.props.onInput(query)
      await setTimeout(280)
      await tick()
    }

    let mounted = mount('superadmin', model(['person-999']))
    await tick()
    await openPicker(mounted.host)
    assert.match(visibleText(mounted.host), /Person 999/, 'saved user beyond the first page is hydrated')
    assert.ok(findAll(mounted.host, (item) => item.type === 'button' && visibleText(item) === '加载更多用户').length)
    const more = findAll(mounted.host, (item) => visibleText(item) === '加载更多用户' && item.type === 'button')[0]
    assert.ok(more)
    more.props.onClick()
    await tick()
    assert.ok(requests.some((path) => path.includes('skip=50') && path.includes('limit=51')))
    await search(mounted.host, 'Target 1001')
    assert.match(visibleText(mounted.host), /Target 1001/)
    assert.match(visibleText(mounted.host), /Person 999/, 'previously selected user remains visible by name')
    const target = findAll(mounted.host, (item) => item.props.role === 'checkbox' && visibleText(item).includes('Target 1001'))[0]
    assert.ok(target)
    target.props.onClick({ stopPropagation() {} })
    await tick()
    assert.deepEqual(mounted.instance.scopes.read_scope.user_uids, ['person-999', 'target-1001'])
    app.unmount()
    app = null

    const beforeChunkedLookup = requests.length
    mounted = mount('superadmin', model(Array.from({ length: 51 }, (_, index) => `person-${index + 1}`)))
    for (let index = 0; index < 4; index++) await tick()
    const lookupRequests = requests.slice(beforeChunkedLookup)
      .filter((path) => path.startsWith('/api/auth/users/access-options/by-uids'))
    assert.deepEqual(lookupRequests.map((path) => new URL(path, 'http://test').searchParams.getAll('uids').length), [50, 1])
    app.unmount()
    app = null

    mounted = mount('admin', model(['person-1', 'target-1001']))
    await tick()
    await openPicker(mounted.host)
    assert.match(visibleText(mounted.host), /Person 1/, 'own selected user resolves inside the department')
    assert.match(visibleText(mounted.host), /target-1001/, 'unavailable selected UID remains visible')
    await search(mounted.host, 'Target 1001')
    assert.doesNotMatch(visibleText(mounted.host), /Target 1001/)
    app.unmount()
    app = null

    const requestCount = requests.length
    mounted = mount('user', model(['person-1']))
    await tick()
    await openPicker(mounted.host)
    assert.equal(requests.length, requestCount, 'ordinary users must not call admin-only access-options')
    app.unmount()
    app = null

    mounted = mount('admin', model(['person-1']), {
      resourceKind: 'mcp',
      availableUsers: [people[0]]
    })
    await tick()
    await openPicker(mounted.host)
    assert.equal(requests.length, requestCount, 'MCP supplied users must remain authoritative')
    assert.doesNotMatch(visibleText(mounted.host), /Target 1001/)
    app.unmount()
    app = null

    const beforeCustomAdminSearch = requests.length
    mounted = mount('admin', model(['person-999']), {
      availableUsers: people.slice(0, 50),
      searchUsers: async () => ({ items: people.slice(0, 50), has_more: true })
    })
    await tick()
    await openPicker(mounted.host)
    assert.match(visibleText(mounted.host), /Person 999/, 'custom remote search hydrates a selected user beyond its supplied page')
    assert.ok(requests.slice(beforeCustomAdminSearch).some((path) =>
      path.startsWith('/api/auth/users/access-options/by-uids')))
    assert.ok(requests.slice(beforeCustomAdminSearch).every((path) =>
      path.startsWith('/api/auth/users/access-options/by-uids')), 'custom search must remain the paging provider')
    app.unmount()
    app = null

    const scopedSearches = []
    const beforeOrdinarySearch = requests.length
    mounted = mount('user', model(['person-1']), {
      searchUsers: async (params) => {
        scopedSearches.push(params)
        return { items: [{ uid: 'scoped-user', username: 'Scoped User', department_id: 1 }], has_more: false }
      }
    })
    await tick()
    await openPicker(mounted.host)
    assert.deepEqual(scopedSearches, [{ search: '', offset: 0, limit: 50 }])
    assert.match(visibleText(mounted.host), /Scoped User/)
    assert.equal(requests.length, beforeOrdinarySearch, 'ordinary scoped search must not call admin endpoints')
  } finally {
    app?.unmount()
    await server?.close()
    if (existsSync(compiledPath)) unlinkSync(compiledPath)
    globalThis.fetch = originalFetch
    if (originalStorage) Object.defineProperty(globalThis, 'localStorage', originalStorage)
    else delete globalThis.localStorage
  }
})
