import assert from 'node:assert/strict'
import { existsSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { pid } from 'node:process'
import test from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { fileURLToPath } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { message, Modal } from 'ant-design-vue'
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
const visibleText = (target) => [target.text, ...(target.children || []).map(visibleText)].join(' ').trim()
const stub = (name) => ({
  setup(_props, { attrs, slots }) {
    return () => h(name, attrs, [attrs.message, slots.default?.(), slots.action?.()])
  }
})

async function withHome({ role = 'user', query = {}, shares = [], hiddenOwn = false, marketFailure = false, personalFailure = false } = {}, verify) {
  const componentDir = new URL('../../src/components/extensions/', import.meta.url)
  const sharedDir = new URL('../../src/components/shared/', import.meta.url)
  const compiledPath = fileURLToPath(new URL(`.skill-market-home-${pid}.mjs`, componentDir))
  const compiledListPath = fileURLToPath(new URL(`.skill-market-home-list-${pid}.mjs`, componentDir))
  const compiledGridPath = fileURLToPath(new URL(`.skill-market-home-grid-${pid}.mjs`, componentDir))
  const compiledShoulderPath = fileURLToPath(new URL(`.skill-market-home-shoulder-${pid}.mjs`, sharedDir))
  const compiledCardPath = fileURLToPath(new URL(`.skill-market-home-card-${pid}.mjs`, sharedDir))
  const originalFetch = globalThis.fetch
  const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  const originalConfirm = Modal.confirm
  const originalMessages = Object.fromEntries(['success', 'info', 'error', 'warning'].map((name) => [name, message[name]]))
  const requested = []
  const confirms = []
  let app, server
  try {
    const { descriptor } = parse(readFileSync(new URL('SkillMarket.vue', componentDir), 'utf8'))
    writeFileSync(compiledPath, compileScript(descriptor, { id: 'skill-market-home', inlineTemplate: true }).content)
    const { descriptor: listDescriptor } = parse(readFileSync(new URL('SkillCardList.vue', componentDir), 'utf8'))
    writeFileSync(compiledListPath, compileScript(listDescriptor, { id: 'skill-market-home-list', inlineTemplate: true }).content.replace("'./ExtensionCardGrid.vue'", `'./.skill-market-home-grid-${pid}.mjs'`))
    for (const [sourceFile, outputPath, id] of [
      [new URL('ExtensionCardGrid.vue', componentDir), compiledGridPath, 'skill-market-home-grid'],
      [new URL('PageShoulder.vue', sharedDir), compiledShoulderPath, 'skill-market-home-shoulder'],
      [new URL('InfoCard.vue', sharedDir), compiledCardPath, 'skill-market-home-card']
    ]) {
      const { descriptor: childDescriptor } = parse(readFileSync(sourceFile, 'utf8'))
      writeFileSync(outputPath, compileScript(childDescriptor, { id, inlineTemplate: true }).content)
    }
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      value: { getItem: () => null, setItem() {}, removeItem() {} }
    })
    Modal.confirm = (options) => { confirms.push(options) }
    for (const name of Object.keys(originalMessages)) message[name] = () => {}
    globalThis.fetch = async (url, options = {}) => {
      const path = String(url)
      requested.push({ path, method: options.method || 'GET' })
      if (path.startsWith('/api/skill-market?')) return marketFailure
        ? Response.json({ detail: '市场暂不可用' }, { status: 503 })
        : Response.json({ data: [
        { slug: 'new-market', name: '市场新技能', owner_uid: 'bob', owner_name: 'Bob', latest_version: 1, install_count: 4, likes_count: 2, category: 'other', scope: 'global', description: '市场展示条目' }
        ] })
      if (path === '/api/skill-market/-/installs') return Response.json({ data: [
        { slug: 'installed-market', name: '已安装市场版', installed_version: 2, visible: true, category: 'other' },
        { slug: 'old-market', name: '不应显示的旧详情', description: '不应显示的描述', owner_uid: 'bob', installed_version: 1, visible: false },
        ...(hiddenOwn ? [{ slug: 'my-market-old', owner_uid: 'alice', own_personal_slug: 'my-skill', name: '不应显示的本人旧详情', installed_version: 1, visible: false }] : [])
      ] })
      if (path.startsWith('/api/skills?') || path === '/api/skills') return personalFailure
        ? Response.json({ detail: '个人技能暂不可用' }, { status: 503 })
        : Response.json({ data: [
        { slug: 'my-skill', name: '我创建的技能', source_scope: 'personal', source_type: 'upload', owner_uid: 'alice', category: 'other' },
        { slug: 'team-skill', name: '团队共享技能', source_scope: 'shared', source_type: 'upload', enabled: true, category: 'other', can_manage: role !== 'user' },
        { slug: 'builtin-skill', name: '内置技能', source_scope: 'builtin', source_type: 'builtin', enabled: true, category: 'other' }
        ] })
      if (path === '/api/skill-market/requests') return Response.json({ data: [] })
      if (path === '/api/skills/share-requests') return Response.json({ data: shares })
      if (path.startsWith('/api/skills/personal-catalog?')) return Response.json({ data: { items: [
        { slug: 'my-skill', name: '我创建的技能', owner_uid: 'alice', category: 'other' },
        { slug: 'bob-skill', name: '他人个人技能', owner_uid: 'bob', category: 'other' }
      ], next_offset: null } })
      if (path === '/api/departments') return Response.json({ departments: [{ id: 3, name: '设计部' }] })
      return Response.json({ data: [] })
    }

    server = await createServer({
      configFile: false,
      plugins: [vue()],
      resolve: { alias: [
        { find: '@/components/extensions/SkillCardList.vue', replacement: compiledListPath },
        { find: '@/components/shared/PageShoulder.vue', replacement: compiledShoulderPath },
        { find: '@/components/shared/InfoCard.vue', replacement: compiledCardPath },
        { find: '@', replacement: fileURLToPath(new URL('../../src', import.meta.url)) }
      ] },
      server: { middlewareMode: true, hmr: false },
      appType: 'custom',
      optimizeDeps: { noDiscovery: true }
    })
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/extensions', component: { render: () => h('div') } },
        { path: '/extensions/skill/:slug', component: { render: () => h('div') } }
      ]
    })
    await router.push({ path: '/extensions', query })
    const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
    const user = useUserStore()
    user.token = 'test-token'
    user.uid = 'alice'
    user.userRole = role
    user.departmentId = 3
    user.departmentName = '设计部'
    const { default: SkillMarket } = await server.ssrLoadModule(`/src/components/extensions/.skill-market-home-${pid}.mjs`)
    app = renderer.createApp(SkillMarket)
    app.use(pinia)
    app.use(router)
    app.provide(ssrContextKey, { modules: new Set() })
    for (const name of ['a-button', 'a-spin', 'a-input', 'a-input-search', 'a-select', 'a-alert', 'a-empty', 'a-tag', 'a-textarea', 'a-radio', 'a-radio-group', 'a-upload', 'a-checkbox', 'a-switch', 'a-tabs', 'a-tab-pane']) app.component(name, stub(name))
    for (const name of ['a-modal', 'a-drawer']) app.component(name, {
      props: { open: Boolean, title: String },
      setup: (props, { attrs, slots }) => () => props.open ? h(name, { ...attrs, title: props.title }, slots.default?.()) : null
    })
    const host = node('root')
    app.mount(host)
    await setImmediate()
    await nextTick()
    await verify({ host, router, requested, confirms, tick: async () => { await setImmediate(); await nextTick() } })
  } finally {
    app?.unmount()
    await server?.close()
    if (existsSync(compiledPath)) unlinkSync(compiledPath)
    if (existsSync(compiledListPath)) unlinkSync(compiledListPath)
    if (existsSync(compiledGridPath)) unlinkSync(compiledGridPath)
    if (existsSync(compiledShoulderPath)) unlinkSync(compiledShoulderPath)
    if (existsSync(compiledCardPath)) unlinkSync(compiledCardPath)
    globalThis.fetch = originalFetch
    Modal.confirm = originalConfirm
    for (const [name, original] of Object.entries(originalMessages)) message[name] = original
    if (originalStorage) Object.defineProperty(globalThis, 'localStorage', originalStorage)
    else delete globalThis.localStorage
  }
}

test('我的技能默认是唯一清单，包含本人来源而不混入待安装市场条目', async () => {
  await withHome({}, async ({ host }) => {
    const home = findAll(host, (item) => item.props.role === 'tabpanel' && item.props.id === 'skill-personal-view')[0]
    assert.ok(home, '首次进入应展示我的技能')
    assert.doesNotMatch(visibleText(home), /不应显示的旧详情|不应显示的描述/)
    const names = ['我创建的技能', '已安装市场版', '团队共享技能', '内置技能', 'old-market']
    for (const name of names) assert.match(visibleText(home), new RegExp(name))
    assert.doesNotMatch(visibleText(home), /市场新技能/)
    assert.ok(findAll(home, (item) => item.type === 'a-button' && visibleText(item) === '卸载旧安装').length)
    assert.equal(findAll(host, (item) => item.type === 'aside' && item.props['aria-label'] === '个人空间').length, 0)
    assert.equal(findAll(host, (item) => item.type === 'a-drawer' && item.props.title === '技能管理与上传').length, 0)
  })
})

test('切换技能市场后只展示已上架条目，返回仍是个人清单', async () => {
  await withHome({}, async ({ host, router, tick }) => {
    const marketTab = findAll(host, (item) => item.props.role === 'tab' && visibleText(item) === '技能市场')[0]
    assert.ok(marketTab)
    marketTab.props.onClick()
    await tick()
    const market = findAll(host, (item) => item.props.role === 'tabpanel' && item.props.id === 'skill-market-view')[0]
    assert.ok(market)
    assert.match(visibleText(market), /市场新技能/)
    assert.doesNotMatch(visibleText(market), /团队共享技能|内置技能|我创建的技能/)
    assert.equal(router.currentRoute.value.query.view, 'market')
    const homeTab = findAll(host, (item) => item.props.role === 'tab' && visibleText(item) === '我的技能')[0]
    homeTab.props.onClick()
    await tick()
    assert.ok(findAll(host, (item) => item.props.id === 'skill-personal-view').length)
    assert.equal(router.currentRoute.value.query.view, undefined)
  })
})

test('个人清单直接提供上传、发布及两种管理员审核入口', async () => {
  await withHome({ role: 'admin' }, async ({ host }) => {
    const home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    const uploads = findAll(home, (item) => item.type === 'a-upload')
    assert.ok(uploads.length, '上传应直接可用')
    assert.equal(typeof (uploads[0].props['custom-request'] || uploads[0].props.customRequest), 'function')
    assert.ok(findAll(home, (item) => item.type === 'a-button' && visibleText(item) === '发布技能').length, '本人原件应可发布')
    assert.ok(findAll(home, (item) => item.type === 'a-button' && visibleText(item).includes('共享申请')).length)
    assert.ok(findAll(home, (item) => item.type === 'a-button' && visibleText(item).includes('市场审核')).length)
    assert.ok(findAll(home, (item) => item.type === 'a-button' && visibleText(item) === '配置 Agent').length)
  })
})

test('失去市场可见性的旧安装仍能从个人清单卸载', async () => {
  await withHome({}, async ({ host, confirms, requested, tick }) => {
    const home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    const oldRow = findAll(home, (item) => item.props.class === 'skill-row' && visibleText(item).includes('old-market'))[0]
    const uninstall = findAll(oldRow, (item) => item.type === 'a-button' && visibleText(item) === '卸载旧安装')[0]
    uninstall.props.onClick()
    assert.equal(confirms.length, 1)
    await confirms[0].onOk()
    await tick()
    assert.ok(requested.some((request) => request.method === 'DELETE' && request.path === '/api/skill-market/old-market/install'))
  })
})

test('作者不可见的旧市场安装仍折叠到个人原件且不泄露市场详情', async () => {
  await withHome({ hiddenOwn: true }, async ({ host }) => {
    const home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    const ownRows = findAll(home, (item) => item.props.class === 'skill-row' && visibleText(item).includes('我创建的技能'))
    assert.equal(ownRows.length, 1)
    assert.match(visibleText(ownRows[0]), /市场安装已失效 v1/)
    assert.ok(findAll(ownRows[0], (item) => item.type === 'a-button' && visibleText(item) === '卸载旧安装').length)
    assert.doesNotMatch(visibleText(home), /my-market-old|不应显示的本人旧详情/)
  })
})

test('管理员从个人首页可打开共享审核并看到待审申请', async () => {
  const share = { id: 'share-1', name: '待共享技能', owner_uid: 'bob', personal_slug: 'bob-skill', status: 'pending', application_reason: '给团队使用', category: 'other', created_at: '2026-10-14T08:00:00Z' }
  await withHome({ role: 'admin', shares: [share] }, async ({ host, tick }) => {
    const entry = findAll(host, (item) => item.type === 'a-button' && visibleText(item).includes('共享申请'))[0]
    entry.props.onClick()
    await tick()
    const modal = findAll(host, (item) => item.type === 'a-modal' && item.props.title === 'Skill 共享申请')[0]
    assert.ok(modal)
    assert.match(visibleText(modal), /待共享技能/)
    assert.ok(findAll(modal, (item) => item.type === 'a-button' && visibleText(item) === '批准并发布').length)
  })
})

test('治理视图只列共享、内置和其他成员技能，不复制个人上传与安装清单', async () => {
  await withHome({ role: 'admin' }, async ({ host, tick }) => {
    const entry = findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '技能治理')[0]
    assert.ok(entry)
    entry.props.onClick()
    await tick()
    const governance = findAll(host, (item) => item.type === 'div' && String(item.props.class || '').includes('skill-cards-page'))[0]
    assert.ok(governance)
    assert.doesNotMatch(visibleText(governance), /我创建的技能|已安装市场版|old-market|我的市场安装|上传 Skill|共享申请/)
    assert.match(visibleText(governance), /团队共享技能|内置技能|他人个人技能/)
    assert.ok(findAll(governance, (item) => item.type === 'a-button' && visibleText(item) === '批量管理').length)
    const enabledManageActions = findAll(governance, (item) => item.type === 'button' && item.props['aria-label'] === '禁用 Skill' && item.props.disabled === false)
    assert.ok(enabledManageActions.length, '管理员仍能管理共享技能的启用状态')
  })
})

test('市场读取失败只在市场提示，个人读取失败只在首页提示', async () => {
  await withHome({ marketFailure: true }, async ({ host, tick }) => {
    const home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    assert.match(visibleText(home), /我创建的技能/)
    assert.equal(findAll(home, (item) => item.type === 'a-alert').length, 0)
    findAll(host, (item) => item.props.role === 'tab' && visibleText(item) === '技能市场')[0].props.onClick()
    await tick()
    const market = findAll(host, (item) => item.props.id === 'skill-market-view')[0]
    assert.equal(findAll(market, (item) => item.type === 'a-alert').length, 1)
  })
  await withHome({ personalFailure: true }, async ({ host, tick }) => {
    const home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    assert.equal(findAll(home, (item) => item.type === 'a-alert').length, 1)
    findAll(host, (item) => item.props.role === 'tab' && visibleText(item) === '技能市场')[0].props.onClick()
    await tick()
    const market = findAll(host, (item) => item.props.id === 'skill-market-view')[0]
    assert.match(visibleText(market), /市场新技能/)
    assert.equal(findAll(market, (item) => item.type === 'a-alert').length, 0)
  })
})

test('个人读取失败时发布记录仍显示可重试错误，不误报空记录或概况', async () => {
  await withHome({ personalFailure: true }, async ({ host, tick }) => {
    let home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    assert.equal(findAll(home, (item) => item.props.class === 'skill-overview').length, 0)
    findAll(home, (item) => item.type === 'a-button' && visibleText(item) === '查看发布记录')[0].props.onClick()
    await tick()
    home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    assert.equal(findAll(home, (item) => item.type === 'a-alert').length, 1)
    assert.ok(findAll(home, (item) => item.type === 'a-button' && visibleText(item) === '重试').length)
    assert.doesNotMatch(visibleText(home), /还没有可发布的个人技能|这里还没有技能/)
  })
})

test('市场读取失败时不把失败当作空市场', async () => {
  await withHome({ marketFailure: true, query: { tab: 'skills', view: 'market' } }, async ({ host }) => {
    const market = findAll(host, (item) => item.props.id === 'skill-market-view')[0]
    assert.equal(findAll(market, (item) => item.type === 'a-alert').length, 1)
    assert.ok(findAll(market, (item) => item.type === 'a-button' && visibleText(item) === '重试').length)
    assert.equal(findAll(market, (item) => item.props.class === 'market-result-count').length, 0)
    assert.doesNotMatch(visibleText(market), /市场里还没有技能|这个方向还没有技能|没有找到匹配的技能/)
  })
})

test('离开技能首页后再返回默认清单，不保留治理或发布记录子视图', async () => {
  await withHome({ role: 'admin' }, async ({ host, tick }) => {
    const button = (label) => findAll(host, (item) => item.type === 'a-button' && visibleText(item) === label)[0]
    const tab = (label) => findAll(host, (item) => item.props.role === 'tab' && visibleText(item) === label)[0]
    button('技能治理').props.onClick()
    await tick()
    assert.match(visibleText(findAll(host, (item) => item.props.id === 'skill-personal-view')[0]), /技能治理/)
    tab('技能市场').props.onClick()
    await tick()
    tab('我的技能').props.onClick()
    await tick()
    let home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    assert.match(visibleText(home), /我创建的技能/)
    assert.doesNotMatch(visibleText(home), /返回我的技能/)
    button('查看发布记录').props.onClick()
    await tick()
    tab('技能市场').props.onClick()
    await tick()
    tab('我的技能').props.onClick()
    await tick()
    home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    assert.match(visibleText(home), /技能列表/)
    assert.doesNotMatch(visibleText(home), /返回技能列表/)
  })
})

test('市场链接可直接打开市场视图，技能详情返回仍打开我的技能', async () => {
  await withHome({ query: { tab: 'skills', view: 'market' } }, async ({ host }) => {
    assert.ok(findAll(host, (item) => item.props.id === 'skill-market-view').length)
  })
  await withHome({ query: { tab: 'skills' } }, async ({ host }) => {
    assert.ok(findAll(host, (item) => item.props.id === 'skill-personal-view').length)
  })
})
