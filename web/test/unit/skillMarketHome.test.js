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
    if (child.parent) {
      const previousIndex = child.parent.children.indexOf(child)
      if (previousIndex >= 0) child.parent.children.splice(previousIndex, 1)
    }
    child.parent = parent
    const index = anchor ? parent.children.indexOf(anchor) : -1
    if (index < 0) parent.children.push(child)
    else parent.children.splice(index, 0, child)
  },
  remove(child) {
    if (!child) return
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

async function withHome({ role = 'user', query = {}, shares = [], marketRequests = [], reviewMarket = [], installedItems = null, managedMarket = [], managedDetails = {}, scopeConflictOnce = false, publicMarketDetail = null, managedListGate = null, managedListFailure = false, managedEmptyPageOnce = false, mineShares = [], mineMarket = [], extraPersonal = [], extraShared = [], extraInstalls = [], extraMarket = [], snapshots = {}, hiddenOwn = false, marketFailure = false, personalFailure = false, historyFailureOnce = false, skillPageOverride, skillSummaryOverride, installPageOverride, marketPageOverride } = {}, verify) {
  const componentDir = new URL('../../src/components/extensions/', import.meta.url)
  const sharedDir = new URL('../../src/components/shared/', import.meta.url)
  const compiledPath = fileURLToPath(new URL(`.skill-market-home-${pid}.mjs`, componentDir))
  const compiledListPath = fileURLToPath(new URL(`.skill-market-home-list-${pid}.mjs`, componentDir))
  const compiledRecordsPath = fileURLToPath(new URL(`.skill-market-home-records-${pid}.mjs`, componentDir))
  const compiledGridPath = fileURLToPath(new URL(`.skill-market-home-grid-${pid}.mjs`, componentDir))
  const compiledShoulderPath = fileURLToPath(new URL(`.skill-market-home-shoulder-${pid}.mjs`, sharedDir))
  const compiledCardPath = fileURLToPath(new URL(`.skill-market-home-card-${pid}.mjs`, sharedDir))
  const originalFetch = globalThis.fetch
  const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  const originalConfirm = Modal.confirm
  const originalMessages = Object.fromEntries(['success', 'info', 'error', 'warning'].map((name) => [name, message[name]]))
  const requested = []
  const confirms = []
  const managedRows = managedMarket.map((item) => ({ ...item }))
  const detailRows = Object.fromEntries(Object.entries(managedDetails).map(([slug, item]) => [slug, item]))
  const catalogRow = {
    slug: 'new-market', name: '市场新技能', owner_uid: 'bob', owner_name: 'Bob', latest_version: 1,
    install_count: 4, likes_count: 2, category: 'other', scope: 'global', description: '市场展示条目',
    tool_dependencies: [], mcp_dependencies: [], skill_dependencies: [],
    ...publicMarketDetail
  }
  let conflictRemaining = scopeConflictOnce
  let emptyPageRemaining = managedEmptyPageOnce
  let historyAttempts = 0
  let app, server
  try {
    const { descriptor } = parse(readFileSync(new URL('SkillMarket.vue', componentDir), 'utf8'))
    writeFileSync(compiledPath, compileScript(descriptor, { id: 'skill-market-home', inlineTemplate: true }).content)
    const { descriptor: listDescriptor } = parse(readFileSync(new URL('SkillCardList.vue', componentDir), 'utf8'))
    writeFileSync(compiledListPath, compileScript(listDescriptor, { id: 'skill-market-home-list', inlineTemplate: true }).content.replace("'./ExtensionCardGrid.vue'", `'./.skill-market-home-grid-${pid}.mjs'`))
    const { descriptor: recordsDescriptor } = parse(readFileSync(new URL('PublicationRecords.vue', componentDir), 'utf8'))
    writeFileSync(compiledRecordsPath, compileScript(recordsDescriptor, { id: 'skill-market-home-records', inlineTemplate: true }).content)
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
      value: { getItem: (key) => key === 'user_token' ? 'test-token' : null, setItem() {}, removeItem() {} }
    })
    Modal.confirm = (options) => { confirms.push(options) }
    for (const name of Object.keys(originalMessages)) message[name] = () => {}
    globalThis.fetch = async (url, options = {}) => {
      const path = String(url)
      requested.push({ path, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null })
      if (path.startsWith('/api/skill-market?')) {
        if (marketFailure) return Response.json({ detail: '市场暂不可用' }, { status: 503 })
        const params = new URL(path, 'http://yuxi.local').searchParams
        const market = [
        catalogRow,
        ...extraMarket
        ]
        const query = (params.get('query') || '').toLowerCase()
        const category = params.get('category')
        const matching = market.filter((item) =>
          (!query || `${item.slug} ${item.name} ${item.description || ''}`.toLowerCase().includes(query)) &&
          (!category || item.category === category)
        )
        const offset = Number(params.get('offset') || 0)
        const limit = Number(params.get('limit') || 100)
        const override = await marketPageOverride?.({ offset, limit, query, category, matching })
        if (override) return Response.json(override)
        return Response.json({ data: matching.slice(offset, offset + limit), total: matching.length, limit, offset, has_more: offset + limit < matching.length })
      }
      if (path === `/api/skill-market/${catalogRow.slug}`) return Response.json({ data: catalogRow })
      if (path === '/api/skill-market/-/management' || path.startsWith('/api/skill-market/-/management?')) {
        const params = new URL(path, 'http://yuxi.local').searchParams
        if (managedListGate) await (typeof managedListGate === 'function' ? managedListGate(params) : managedListGate)
        if (typeof managedListFailure === 'function' ? managedListFailure() : managedListFailure) return Response.json({ detail: '管理清单暂不可用' }, { status: 503 })
        const search = (params.get('query') || '').toLowerCase()
        const exactSlug = params.get('slug')
        const status = params.get('status')
        const scope = params.get('scope')
        const offset = Number(params.get('offset') || 0)
        const limit = Number(params.get('limit') || 20)
        const matching = managedRows.filter((item) =>
          (!search || `${item.name} ${item.slug}`.toLowerCase().includes(search)) &&
          (!exactSlug || item.slug === exactSlug) &&
          (!status || (item.is_published ? 'published' : 'withdrawn') === status) &&
          (!scope || item.scope === scope)
        )
        if (emptyPageRemaining && offset > 0 && offset < matching.length) {
          emptyPageRemaining = false
          return Response.json({ data: [], total: matching.length, offset, limit, has_more: false })
        }
        return Response.json({ data: matching.slice(offset, offset + limit), total: matching.length, offset, limit, has_more: offset + limit < matching.length })
      }
      const managementSlug = path.match(/^\/api\/skill-market\/-\/management\/([^/]+)$/)?.[1]
      if (managementSlug) {
        const detail = detailRows[decodeURIComponent(managementSlug)] || managedRows.find((item) => item.slug === decodeURIComponent(managementSlug))
        return Response.json({ data: await detail })
      }
      const managementAction = path.match(/^\/api\/skill-market\/([^/]+)\/(unpublish|scope)$/)
      if (managementAction) {
        const [, slug, action] = managementAction
        const row = managedRows.find((item) => item.slug === slug)
        if (conflictRemaining) {
          conflictRemaining = false
          row.state_revision += 1
          if (detailRows[slug] && typeof detailRows[slug].then !== 'function') detailRows[slug].state_revision = row.state_revision
          return Response.json({ detail: 'stale' }, { status: 409 })
        }
        if (action === 'unpublish') row.is_published = false
        else {
          row.scope = JSON.parse(options.body).scope
          row.department_id = JSON.parse(options.body).department_id
        }
        row.state_revision += 1
        if (detailRows[slug] && typeof detailRows[slug].then !== 'function') Object.assign(detailRows[slug], row)
        return Response.json({ data: row })
      }
      if (path.startsWith('/api/skill-market/-/installs')) {
        const params = new URL(path, 'http://yuxi.local').searchParams
        const offset = Number(params.get('offset') || 0)
        const limit = Number(params.get('limit') || 30)
        const catalogAll = params.get('catalog_all') === 'true'
        const installs = installedItems || [
          { slug: 'installed-market', name: '已安装市场版', installed_version: 2, visible: true, category: 'other' },
          { slug: 'old-market', name: '不应显示的旧详情', description: '不应显示的描述', owner_uid: 'bob', installed_version: 1, visible: false },
          ...(hiddenOwn ? [{ slug: 'my-market-old', owner_uid: 'alice', own_personal_slug: 'my-skill', name: '不应显示的本人旧详情', installed_version: 1, visible: false }] : []),
          ...extraInstalls
        ]
        const search = (params.get('search') || '').toLowerCase()
        const matching = installs.filter((item) => !search || `${item.slug} ${item.visible === false ? '' : `${item.name} ${item.description || ''}`}`.toLowerCase().includes(search))
        const override = await installPageOverride?.({ offset, limit, search, catalogAll, matching })
        if (override) return Response.json(override)
        return Response.json({ data: matching.slice(offset, offset + limit), total: matching.length, has_more: offset + limit < matching.length, limit, offset })
      }
      if (path.startsWith('/api/skills/summary')) {
        const search = new URL(path, 'http://yuxi.local').searchParams.get('search') || ''
        return Response.json({ data: await skillSummaryOverride?.(search) || { created_total: 1, all_total: 5 } })
      }
      if (path.startsWith('/api/skills?') || path === '/api/skills') {
        if (personalFailure) return Response.json({ detail: '个人技能暂不可用' }, { status: 503 })
        const params = new URL(path, 'http://yuxi.local').searchParams
        const personal = [
          { slug: 'my-skill', name: '我创建的技能', source_scope: 'personal', source_type: 'upload', owner_uid: 'alice', category: 'other' },
          ...extraPersonal
        ]
        const shared = [
          { slug: 'team-skill', name: '团队共享技能', source_scope: 'shared', source_type: 'upload', enabled: true, category: 'other', can_manage: role !== 'user' },
          { slug: 'builtin-skill', name: '内置技能', source_scope: 'builtin', source_type: 'builtin', enabled: true, category: 'other' },
          ...extraShared
        ]
        const source = params.get('source')
        const search = (params.get('search') || '').toLowerCase()
        const sharedKind = params.get('shared_kind')
        const catalogAll = params.get('catalog_all') === 'true'
        const all = (source === 'personal' ? personal : source === 'shared' ? shared.filter((item) =>
          !sharedKind || (sharedKind === 'builtin' ? item.source_type === 'builtin' : item.source_type !== 'builtin')
        ) : [...personal, ...shared])
          .filter((item) => !search || `${item.slug} ${item.name} ${item.description || ''}`.toLowerCase().includes(search))
        const offset = Number(params.get('offset') || 0)
        const limit = Number(params.get('limit') || 30)
        const override = await skillPageOverride?.({ source, sharedKind, catalogAll, search, offset, limit, all })
        if (override) return Response.json(override)
        return Response.json({ data: all.slice(offset, offset + limit), total: source === 'personal' ? null : all.length, has_more: offset + limit < all.length, limit, offset })
      }
      if (path.startsWith('/api/skills/personal/')) {
        const slug = decodeURIComponent(path.split('/').at(-1))
        const found = [
          { slug: 'my-skill', name: '我创建的技能', source_scope: 'personal', owner_uid: 'alice' },
          ...extraPersonal
        ].find((item) => item.slug === slug)
        return found ? Response.json({ data: found }) : Response.json({ detail: '个人 Skill 不存在' }, { status: 404 })
      }
      if (path.startsWith('/api/skills/publication-records?')) {
        historyAttempts++
        if (historyFailureOnce && historyAttempts === 1) return Response.json({ detail: '发布记录暂不可用' }, { status: 503 })
        const params = new URL(path, 'http://yuxi.local').searchParams
        const rows = [...mineShares, ...mineMarket].filter((item) => item.owner_uid === 'alice')
        const activity = (item) => Date.parse(item.reviewed_at || item.created_at || 0)
        const latestActivity = (slug) => rows.filter((item) => item.personal_slug === slug)
          .sort((left, right) => activity(right) - activity(left))[0]
        const slugs = [...new Set(rows.map((item) => item.personal_slug))]
          .sort((left, right) => activity(latestActivity(right)) - activity(latestActivity(left)))
        const offset = Number(params.get('offset') || 0)
        const selected = slugs.slice(offset, offset + Number(params.get('limit') || 20))
        const latestAndApproved = (source) => selected.flatMap((slug) => {
          const own = source.filter((item) => item.owner_uid === 'alice' && item.personal_slug === slug)
            .sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at))
          return [own[0], own.find((item) => item.status === 'approved')].filter((item, index, items) => item && items.findIndex((candidate) => candidate?.id === item.id) === index)
        })
        return Response.json({ data: {
          slugs: selected,
          activities: Object.fromEntries(selected.map((slug) => [slug, latestActivity(slug).reviewed_at || latestActivity(slug).created_at])),
          shared_requests: latestAndApproved(mineShares), market_requests: latestAndApproved(mineMarket), total: slugs.length
        } })
      }
      if (path.startsWith('/api/skills/share-requests?') || path.startsWith('/api/skill-market/requests?')) {
        const params = new URL(path, 'http://yuxi.local').searchParams
        const source = path.startsWith('/api/skill-market')
          ? params.get('mine_only') === 'true' ? mineMarket : [...marketRequests, ...reviewMarket]
          : params.get('mine_only') === 'true' ? mineShares : shares
        const filtered = source.filter((item) => (!params.get('slug') || item.personal_slug === params.get('slug')) && (!params.get('status') || item.status === params.get('status')))
          .sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at))
        const offset = Number(params.get('offset') || 0)
        const limit = Number(params.get('limit') || 20)
        return Response.json({ data: { items: filtered.slice(offset, offset + limit), total: filtered.length } })
      }
      const snapshotMatch = path.match(/^\/api\/skills\/share-requests\/([^/]+)\/(tree|snapshot)(?:\?path=(.*))?$/)
      if (snapshotMatch) {
        const [, id, endpoint] = snapshotMatch
        const snapshot = snapshots[id]
        if (!snapshot) return Response.json({ detail: '未找到快照' }, { status: 404 })
        return Response.json({ data: endpoint === 'tree' ? snapshot.tree : { content: snapshot.content } })
      }
      if (path.startsWith('/api/skills/personal-catalog?')) return Response.json({ data: { items: [
        { slug: 'my-skill', name: '我创建的技能', owner_uid: 'alice', category: 'other' },
        { slug: 'bob-skill', name: '他人个人技能', owner_uid: 'bob', category: 'other' }
      ], next_offset: null } })
      if (path === '/api/departments') return Response.json({ departments: [{ id: 3, name: '设计部' }, { id: 4, name: '研发部' }] })
      return Response.json({ data: [] })
    }

    server = await createServer({
      configFile: false,
      plugins: [vue()],
      resolve: { alias: [
        { find: '@/components/extensions/SkillCardList.vue', replacement: compiledListPath },
        { find: '@/components/extensions/PublicationRecords.vue', replacement: compiledRecordsPath },
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
    for (const name of ['a-pagination', 'a-button', 'a-spin', 'a-input', 'a-input-search', 'a-select', 'a-alert', 'a-empty', 'a-tag', 'a-textarea', 'a-radio', 'a-radio-group', 'a-upload', 'a-checkbox', 'a-switch', 'a-tabs', 'a-tab-pane', 'a-dropdown', 'a-form', 'a-menu', 'a-menu-divider', 'a-menu-item', 'a-tooltip']) app.component(name, stub(name))
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
    if (existsSync(compiledRecordsPath)) unlinkSync(compiledRecordsPath)
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

test('来源标签显示真实总数，个人技能翻页不会把当前页当总数', async () => {
  const extraPersonal = Array.from({ length: 30 }, (_, index) => ({
    slug: `personal-${index}`, name: `个人技能 ${index}`, source_scope: 'personal', source_type: 'upload', owner_uid: 'alice'
  }))
  const extraShared = [{
    slug: 'installed-market-v1', name: '市场技能共享版', source_scope: 'shared', source_type: 'personal_share',
    related_market_slug: 'installed-market', enabled: true
  }]
  await withHome({ extraPersonal, extraShared, skillSummaryOverride: () => ({ created_total: 31, all_total: 35 }) }, async ({ host, requested, tick }) => {
    const sourceTab = (label) => findAll(host, (item) => item.type === 'button' && visibleText(item).startsWith(label))[0]
    const count = (label) => findAll(sourceTab(label), (item) => item.type === 'span').map(visibleText).join(' ')
    assert.equal(count('全部'), '35')
    assert.equal(count('我创建的'), '31')
    assert.equal(count('团队共享'), '2')

    const home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    const pager = findAll(home, (item) => item.props['aria-label'] === '我创建的技能分页')[0]
    findAll(pager, (item) => item.type === 'a-button' && visibleText(item) === '下一页')[0].props.onClick()
    await tick()
    assert.equal(count('全部'), '35')
    assert.equal(count('我创建的'), '31')
    assert.ok(requested.filter((item) => item.path.includes('source=personal')).every((item) => item.path.includes('limit=30')))
    assert.equal(requested.filter((item) => item.path.startsWith('/api/skills/summary')).length, 1)
  })
})

test('全部在共享来源分页前折叠同源版本和跨页安装，团队共享仍保留原始列表', async () => {
  const extraShared = [
    ...Array.from({ length: 30 }, (_, index) => ({
      id: index + 10, slug: `other-${index}`, name: `普通共享 ${index}`, source_scope: 'shared',
      source_type: 'upload', enabled: true
    })),
    { id: 100, slug: 'shared-v1', name: '共享旧版', source_scope: 'shared', source_type: 'personal_share', related_market_slug: 'market-y', enabled: true },
    { id: 101, slug: 'shared-v2', name: '共享新版', source_scope: 'shared', source_type: 'personal_share', related_market_slug: 'market-y', enabled: true },
    { id: 102, slug: 'shared-installed', name: '已安装的共享版', source_scope: 'shared', source_type: 'personal_share', related_market_slug: 'market-x', enabled: true },
    { id: 103, slug: 'market-x', name: '已安装的内置版', source_scope: 'builtin', source_type: 'builtin', enabled: true }
  ]
  const extraInstalls = [
    ...Array.from({ length: 29 }, (_, index) => ({ slug: `filler-install-${index}`, name: `安装 ${index}`, installed_version: 1, visible: true })),
    { slug: 'market-x', name: '跨页安装', installed_version: 1, visible: true }
  ]
  await withHome({
    extraShared, extraInstalls,
    skillSummaryOverride: () => ({ created_total: 1, all_total: 66 }),
    skillPageOverride: ({ source, catalogAll, all, offset, limit }) => {
      if (source !== 'shared' || !catalogAll) return null
      const eligible = all.filter((item) => !['shared-v1', 'shared-installed', 'market-x'].includes(item.slug))
      return { data: eligible.slice(offset, offset + limit), total: eligible.length, has_more: offset + limit < eligible.length }
    }
  }, async ({ host, requested, tick }) => {
    const home = () => findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    const rows = () => findAll(home(), (item) => item.props.class === 'skill-row').map(visibleText).join(' ')
    const sharedPager = () => findAll(home(), (item) => item.props['aria-label'] === '团队共享技能分页')[0]
    assert.ok(requested.some(({ path }) => path.includes('catalog_all=true')))
    assert.equal(findAll(sharedPager(), (item) => item.type === 'a-pagination')[0].props.total, 32)
    assert.doesNotMatch(rows(), /已安装的共享版|已安装的内置版|共享旧版/)
    findAll(sharedPager(), (item) => item.type === 'a-pagination')[0].props.onChange(2, 30)
    await tick()
    assert.match(rows(), /共享新版/)
    assert.doesNotMatch(rows(), /已安装的共享版|已安装的内置版|共享旧版/)
    const sharedTab = findAll(home(), (item) => item.type === 'button' && visibleText(item).startsWith('团队共享'))[0]
    sharedTab.props.onClick()
    await tick()
    assert.equal(findAll(sharedPager(), (item) => item.type === 'a-pagination')[0].props.total, 34)
    findAll(sharedPager(), (item) => item.type === 'a-pagination')[0].props.onChange(2, 30)
    await tick()
    assert.match(rows(), /共享旧版|已安装的共享版/)
    const systemTab = findAll(home(), (item) => item.type === 'button' && visibleText(item).startsWith('系统内置'))[0]
    systemTab.props.onClick()
    await tick()
    assert.match(rows(), /已安装的内置版/)
  })
})

test('全部在安装分页前折叠跨页个人原件，已安装来源保留安装记录', async () => {
  const extraPersonal = Array.from({ length: 30 }, (_, index) => ({
    slug: `personal-${index}`, name: `个人技能 ${index}`, source_scope: 'personal', source_type: 'upload', owner_uid: 'alice'
  }))
  const extraInstalls = [{
    slug: 'market-owned-last', name: '本人市场安装', installed_version: 1, visible: true,
    owner_uid: 'alice', own_personal_slug: 'personal-29',
    own_personal: { slug: 'personal-29', name: '个人技能 29', source_scope: 'personal', owner_uid: 'alice' }
  }]
  await withHome({
    extraPersonal, extraInstalls,
    skillSummaryOverride: () => ({ created_total: 31, all_total: 35 }),
    installPageOverride: ({ catalogAll, matching, offset, limit }) => {
      if (!catalogAll) return null
      const eligible = matching.filter((item) => item.slug !== 'market-owned-last')
      return { data: eligible.slice(offset, offset + limit), total: eligible.length, has_more: offset + limit < eligible.length }
    }
  }, async ({ host, requested, tick }) => {
    const home = () => findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    const rows = () => findAll(home(), (item) => item.props.class === 'skill-row').map(visibleText).join(' ')
    assert.ok(requested.some(({ path }) => path.startsWith('/api/skill-market/-/installs?') && path.includes('catalog_all=true')))
    assert.doesNotMatch(rows(), /个人技能 29/)
    const personalPager = findAll(home(), (item) => item.props['aria-label'] === '我创建的技能分页')[0]
    findAll(personalPager, (item) => item.type === 'a-button' && visibleText(item) === '下一页')[0].props.onClick()
    await tick()
    assert.equal(findAll(home(), (item) => item.props.class === 'skill-row' && visibleText(item).includes('个人技能 29')).length, 1)
    const installedTab = findAll(home(), (item) => item.type === 'button' && visibleText(item).startsWith('市场已安装'))[0]
    installedTab.props.onClick()
    await tick()
    assert.match(rows(), /个人技能 29/)
  })
})

test('搜索后来源标签改为匹配的总数', async () => {
  await withHome({
    skillSummaryOverride: (search) => search ? { created_total: 0, all_total: 1 } : { created_total: 1, all_total: 5 }
  }, async ({ host, requested, tick }) => {
    const home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    const search = findAll(home, (item) => item.type === 'a-input' && item.props['aria-label'] === '搜索我的技能')[0]
    search.props['onUpdate:value']('团队')
    await new Promise((resolve) => setTimeout(resolve, 300))
    await tick()
    const sourceTab = (label) => findAll(host, (item) => item.type === 'button' && visibleText(item).startsWith(label))[0]
    const count = (label) => findAll(sourceTab(label), (item) => item.type === 'span').map(visibleText).join(' ')
    assert.equal(count('全部'), '1')
    assert.equal(count('我创建的'), '0')
    assert.ok(requested.some((item) => item.path === '/api/skills/summary?search=%E5%9B%A2%E9%98%9F'))
  })
})

test('总数暂不可用时仍能浏览分页技能', async () => {
  await withHome({ skillSummaryOverride: () => { throw new Error('计数失败') } }, async ({ host }) => {
    const home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    const allTab = findAll(home, (item) => item.type === 'button' && visibleText(item).startsWith('全部'))[0]
    assert.equal(findAll(allTab, (item) => item.type === 'span').map(visibleText).join(' '), '—')
    assert.match(visibleText(home), /技能数量暂不可用/)
    assert.match(visibleText(findAll(home, (item) => item.props['aria-label'] === '我的技能概况')[0]), /—\s+我创建的/)
    assert.ok(findAll(home, (item) => item.props.class === 'skill-row').length)
  })
})

test('个人与团队技能分别翻页并替换当前页，系统技能单独计数', async () => {
  const extraPersonal = Array.from({ length: 30 }, (_, index) => ({
    slug: `personal-${index}`, name: `个人技能 ${index}`, source_scope: 'personal', source_type: 'upload', owner_uid: 'alice', category: 'other'
  }))
  const extraShared = Array.from({ length: 30 }, (_, index) => ({
    slug: `shared-${index}`, name: `共享技能 ${index}`, source_scope: 'shared', source_type: 'upload', enabled: true, category: 'other'
  }))
  await withHome({ extraPersonal, extraShared }, async ({ host, requested, tick }) => {
    let home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    assert.ok(requested.some((request) => request.path.includes('source=personal') && request.path.includes('limit=30') && request.path.includes('offset=0')))
    assert.ok(requested.some((request) => request.path.includes('source=shared') && request.path.includes('limit=30') && request.path.includes('offset=0')))
    assert.doesNotMatch(visibleText(home), /个人技能 29|共享技能 29/)
    const personalPages = findAll(home, (item) => item.props['aria-label'] === '我创建的技能分页')[0]
    assert.ok(personalPages)
    assert.match(visibleText(personalPages), /第 1 页/)
    const personalNext = findAll(personalPages, (item) => item.type === 'a-button' && visibleText(item) === '下一页')[0]
    personalNext.props.onClick()
    await tick()
    home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    assert.match(visibleText(home), /个人技能 29/)
    assert.equal(findAll(home, (item) => item.props.class === 'skill-row' && visibleText(item).includes('我创建的技能')).length, 0)
    assert.ok(requested.some((request) => request.path.includes('source=personal') && request.path.includes('offset=30')))
    const sharedPages = findAll(home, (item) => item.props['aria-label'] === '团队共享技能分页')[0]
    assert.ok(sharedPages)
    const sharedPagination = findAll(sharedPages, (item) => item.type === 'a-pagination')[0]
    assert.equal(sharedPagination.props.total, 31)
    sharedPagination.props.onChange(2, 30)
    await tick()
    home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    assert.match(visibleText(home), /共享技能 29/)
    assert.equal(findAll(home, (item) => item.props.class === 'skill-row' && visibleText(item).includes('团队共享技能')).length, 0)
    assert.ok(requested.some((request) => request.path.includes('shared_kind=shared') && request.path.includes('offset=30')))
    assert.ok(requested.some((request) => request.path.includes('shared_kind=builtin') && request.path.includes('offset=0')))
  })
})

test('个人技能尚未全部加载时，发布记录不推断未加载的原件已删除', async () => {
  const extraPersonal = Array.from({ length: 30 }, (_, index) => ({
    slug: `personal-${index}`, name: `个人技能 ${index}`, source_scope: 'personal', source_type: 'upload', owner_uid: 'alice', category: 'other'
  }))
  const mineShares = [{
    id: 'late-share', owner_uid: 'alice', personal_slug: 'personal-29', name: '个人技能 29', status: 'approved',
    published_version: 1, created_at: '2026-10-14T08:00:00Z'
  }]
  await withHome({ extraPersonal, mineShares }, async ({ host, tick }) => {
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '查看发布记录')[0].props.onClick()
    await tick()
    const records = findAll(host, (item) => item.props['aria-label'] === '发布与审核记录')[0]
    assert.match(visibleText(records), /个人技能 29/)
    assert.doesNotMatch(visibleText(records), /原件已删除/)
  })
})

test('个人技能搜索结果不能用来判定发布记录的原件是否已删除', async () => {
  const mineShares = [{
    id: 'share-my-skill', owner_uid: 'alice', personal_slug: 'my-skill', name: '我创建的技能', status: 'approved',
    published_version: 1, created_at: '2026-10-14T08:00:00Z'
  }]
  await withHome({ mineShares }, async ({ host, tick }) => {
    const search = findAll(host, (item) => item.type === 'a-input' && item.props['aria-label'] === '搜索我的技能')[0]
    search.props['onUpdate:value']('不存在的关键词')
    await new Promise((resolve) => setTimeout(resolve, 300))
    await tick()
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '查看发布记录')[0].props.onClick()
    await tick()
    const records = findAll(host, (item) => item.props['aria-label'] === '发布与审核记录')[0]
    assert.match(visibleText(records), /我创建的技能/)
    assert.doesNotMatch(visibleText(records), /原件已删除/)
  })
})

test('市场已安装技能有准确总数，翻页替换且旧安装仍可管理', async () => {
  const extraInstalls = Array.from({ length: 29 }, (_, index) => ({
    slug: `installed-${index}`, name: `安装技能 ${index}`, installed_version: 1, visible: true, category: 'other'
  }))
  await withHome({ extraInstalls }, async ({ host, requested, tick }) => {
    let home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    assert.ok(requested.some((request) => request.path.includes('/api/skill-market/-/installs?limit=30&offset=0')))
    assert.doesNotMatch(visibleText(home), /安装技能 28/)
    const installedPages = findAll(home, (item) => item.props['aria-label'] === '市场已安装技能分页')[0]
    assert.ok(installedPages)
    const pagination = findAll(installedPages, (item) => item.type === 'a-pagination')[0]
    assert.equal(pagination.props.total, 31)
    pagination.props.onChange(2, 30)
    await tick()
    home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    assert.match(visibleText(home), /安装技能 28/)
    assert.doesNotMatch(visibleText(home), /已安装市场版/)
    assert.ok(requested.some((request) => request.path.includes('/api/skill-market/-/installs?limit=30&offset=30')))
  })
})

test('原件在当前页而历史市场安装在后页时，原件仍显示安装版本且安装分页只显示当前页', async () => {
  const ownInstall = {
    slug: 'own-market', name: '市场发布版', owner_uid: 'alice', own_personal_slug: 'own-source',
    installed_version: 2, visible: true
  }
  const extraPersonal = [{
    slug: 'own-source', name: '个人原件', source_scope: 'personal', owner_uid: 'alice', market_install: ownInstall
  }]
  const extraInstalls = [
    ...Array.from({ length: 28 }, (_, index) => ({ slug: `filler-${index}`, name: `安装 ${index}`, installed_version: 1, visible: true })),
    { ...ownInstall, own_personal: extraPersonal[0] }
  ]
  await withHome({ extraPersonal, extraInstalls }, async ({ host, tick }) => {
    let home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    let ownRow = findAll(home, (item) => item.props.class === 'skill-row' && visibleText(item).includes('个人原件'))[0]
    assert.match(visibleText(ownRow), /市场已安装 v2/)
    assert.match(visibleText(ownRow), /管理安装/)

    findAll(home, (item) => item.type === 'button' && visibleText(item).includes('我创建的'))[0].props.onClick()
    await tick()
    home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    ownRow = findAll(home, (item) => item.props.class === 'skill-row' && visibleText(item).includes('个人原件'))[0]
    assert.match(visibleText(ownRow), /市场已安装 v2/)

    findAll(home, (item) => item.type === 'button' && visibleText(item).includes('市场已安装'))[0].props.onClick()
    await tick()
    home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    assert.equal(findAll(home, (item) => item.props.class === 'skill-row').length, 30)
    const pager = findAll(home, (item) => item.props['aria-label'] === '市场已安装技能分页')[0]
    assert.equal(findAll(pager, (item) => item.type === 'a-pagination')[0].props.total, 31)
    findAll(pager, (item) => item.type === 'a-pagination')[0].props.onChange(2, 30)
    await tick()
    home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    const rows = findAll(home, (item) => item.props.class === 'skill-row')
    assert.equal(rows.length, 1)
    assert.match(visibleText(rows[0]), /个人原件.*市场已安装 v2/)
  })
})

test('安装当前页可找到后页个人原件，市场名搜索不会过滤合并后的原件', async () => {
  const original = { slug: 'late-source', name: '个人笔记', source_scope: 'personal', owner_uid: 'alice' }
  const extraPersonal = [
    ...Array.from({ length: 29 }, (_, index) => ({ slug: `personal-${index}`, name: `个人技能 ${index}`, source_scope: 'personal', owner_uid: 'alice' })),
    original
  ]
  const extraInstalls = [{
    slug: 'market-report', name: '市场报表', owner_uid: 'alice', own_personal_slug: 'late-source',
    own_personal: original, installed_version: 3, visible: true
  }]
  await withHome({ extraPersonal, extraInstalls }, async ({ host, requested, tick }) => {
    let home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    findAll(home, (item) => item.type === 'button' && visibleText(item).includes('市场已安装'))[0].props.onClick()
    await tick()
    home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    let ownRow = findAll(home, (item) => item.props.class === 'skill-row' && visibleText(item).includes('个人笔记'))[0]
    assert.ok(ownRow)
    assert.match(visibleText(ownRow), /市场已安装 v3/)
    assert.equal(requested.some((item) => item.path.includes('source=personal') && item.path.includes('offset=30')), false)

    const search = findAll(home, (item) => item.type === 'a-input' && item.props['aria-label'] === '搜索我的技能')[0]
    search.props['onUpdate:value']('市场报表')
    await new Promise((resolve) => setTimeout(resolve, 300))
    await tick()
    home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    const rows = findAll(home, (item) => item.props.class === 'skill-row')
    assert.equal(rows.length, 1)
    ownRow = rows[0]
    assert.match(visibleText(ownRow), /个人笔记.*市场已安装 v3/)
    assert.ok(requested.some((item) => item.path.includes('/api/skill-market/-/installs?') && item.path.includes('search=%E5%B8%82%E5%9C%BA%E6%8A%A5%E8%A1%A8')))
  })
})

test('来源与关键词改变后回第一页，个人文件空尾页也回到仍有数据的页', async () => {
  const extraPersonal = Array.from({ length: 30 }, (_, index) => ({
    slug: `personal-${index}`, name: `个人技能 ${index}`, source_scope: 'personal', source_type: 'upload', owner_uid: 'alice',
    description: index === 0 ? '数据汇总' : ''
  }))
  let removedLast = false
  await withHome({
    extraPersonal,
    skillPageOverride: ({ source, offset, all }) => {
      if (source !== 'personal') return null
      if (offset === 30) {
        removedLast = true
        return { data: [], total: null, has_more: false }
      }
      return removedLast ? { data: all.slice(0, 30), total: null, has_more: false } : null
    }
  }, async ({ host, requested, tick }) => {
    let home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    const pager = findAll(home, (item) => item.props['aria-label'] === '我创建的技能分页')[0]
    findAll(pager, (item) => item.type === 'a-button' && visibleText(item) === '下一页')[0].props.onClick()
    await tick()
    home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    assert.equal(findAll(home, (item) => item.props['aria-label'] === '我创建的技能分页').length, 0)
    assert.ok(findAll(home, (item) => item.props.class === 'skill-row' && visibleText(item).includes('我创建的技能')).length)
    assert.ok(requested.filter((item) => item.path.includes('source=personal') && item.path.includes('offset=0')).length >= 2)

    const search = findAll(home, (item) => item.type === 'a-input' && item.props['aria-label'] === '搜索我的技能')[0]
    search.props['onUpdate:value']('数据汇总')
    await new Promise((resolve) => setTimeout(resolve, 300))
    await tick()
    home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    assert.ok(requested.some((item) => item.path.includes('source=personal') && item.path.includes('search=%E6%95%B0%E6%8D%AE%E6%B1%87%E6%80%BB') && item.path.includes('offset=0')))
    const rows = findAll(home, (item) => item.props.class === 'skill-row')
    assert.match(rows.map(visibleText).join(' '), /个人技能 0/)
    assert.doesNotMatch(rows.map(visibleText).join(' '), /个人技能 1/)
  })
})

test('翻页中的旧个人结果晚于新搜索返回时不覆盖新列表', async () => {
  const extraPersonal = Array.from({ length: 30 }, (_, index) => ({
    slug: `personal-${index}`, name: `个人技能 ${index}`, source_scope: 'personal', source_type: 'upload', owner_uid: 'alice',
    description: index === 0 ? '数据汇总' : ''
  }))
  let releaseOldPage
  const oldPage = new Promise((resolve) => { releaseOldPage = resolve })
  await withHome({
    extraPersonal,
    skillPageOverride: ({ source, offset }) => source === 'personal' && offset === 30 ? oldPage : null
  }, async ({ host, tick }) => {
    let home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    const pager = findAll(home, (item) => item.props['aria-label'] === '我创建的技能分页')[0]
    findAll(pager, (item) => item.type === 'a-button' && visibleText(item) === '下一页')[0].props.onClick()
    const search = findAll(home, (item) => item.type === 'a-input' && item.props['aria-label'] === '搜索我的技能')[0]
    search.props['onUpdate:value']('数据汇总')
    await new Promise((resolve) => setTimeout(resolve, 300))
    await tick()
    releaseOldPage({ data: [{ slug: 'late-old', name: '旧页技能', source_scope: 'personal', owner_uid: 'alice' }], total: null, has_more: false })
    await tick()
    home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    const rows = findAll(home, (item) => item.props.class === 'skill-row').map(visibleText).join(' ')
    assert.match(rows, /个人技能 0/)
    assert.doesNotMatch(rows, /旧页技能/)
  })
})

test('切换我的技能来源时各来源回第一页', async () => {
  const extraPersonal = Array.from({ length: 30 }, (_, index) => ({
    slug: `personal-${index}`, name: `个人技能 ${index}`, source_scope: 'personal', source_type: 'upload', owner_uid: 'alice'
  }))
  await withHome({ extraPersonal, skillSummaryOverride: () => ({ created_total: 31, all_total: 35 }) }, async ({ host, requested, tick }) => {
    let home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    const pager = findAll(home, (item) => item.props['aria-label'] === '我创建的技能分页')[0]
    findAll(pager, (item) => item.type === 'a-button' && visibleText(item) === '下一页')[0].props.onClick()
    await tick()
    home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    assert.match(visibleText(findAll(home, (item) => item.props['aria-label'] === '我创建的技能分页')[0]), /第 2 页/)
    const beforeShared = requested.length
    findAll(home, (item) => item.type === 'button' && visibleText(item).includes('团队共享'))[0].props.onClick()
    await tick()
    const sharedRequests = requested.slice(beforeShared).filter((item) => item.path.startsWith('/api/skills?') || item.path.startsWith('/api/skill-market/-/installs?'))
    assert.equal(sharedRequests.length, 1)
    assert.match(sharedRequests[0].path, /source=shared.*shared_kind=shared.*offset=0/)
    home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    assert.match(visibleText(findAll(home, (item) => item.props['aria-label'] === '我的技能概况')[0]), /31.*我创建的/)
    findAll(home, (item) => item.type === 'button' && visibleText(item).includes('全部'))[0].props.onClick()
    await tick()
    home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    assert.match(visibleText(findAll(home, (item) => item.props['aria-label'] === '我创建的技能分页')[0]), /第 1 页/)
    assert.ok(findAll(home, (item) => item.props.class === 'skill-row' && visibleText(item).includes('我创建的技能')).length)
    assert.ok(requested.filter((item) => item.path.includes('source=personal') && item.path.includes('offset=0')).length >= 2)
  })
})

test('市场安装尾页被删除后回到最后仍有数据的页', async () => {
  const extraInstalls = Array.from({ length: 29 }, (_, index) => ({
    slug: `installed-${index}`, name: `安装技能 ${index}`, installed_version: 1, visible: true
  }))
  let removedLast = false
  await withHome({
    extraInstalls,
    installPageOverride: ({ offset, limit, matching }) => {
      if (offset === 30) {
        removedLast = true
        return { data: [], total: 30, has_more: false }
      }
      return removedLast ? { data: matching.slice(0, limit), total: 30, has_more: false } : null
    }
  }, async ({ host, requested, tick }) => {
    let home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    const pager = findAll(home, (item) => item.props['aria-label'] === '市场已安装技能分页')[0]
    findAll(pager, (item) => item.type === 'a-pagination')[0].props.onChange(2, 30)
    await tick()
    home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    assert.equal(findAll(home, (item) => item.props['aria-label'] === '市场已安装技能分页').length, 0)
    assert.ok(findAll(home, (item) => item.props.class === 'skill-row' && visibleText(item).includes('已安装市场版')).length)
    assert.ok(requested.filter((item) => item.path.includes('/api/skill-market/-/installs?') && item.path.includes('offset=0')).length >= 2)
  })
})

test('市场安装旧页请求晚到失败时不覆盖新搜索结果', async () => {
  const extraInstalls = Array.from({ length: 29 }, (_, index) => ({
    slug: `installed-${index}`, name: `安装技能 ${index}`, installed_version: 1, visible: true,
    description: index === 0 ? '数据汇总' : ''
  }))
  let failOldPage
  const oldPage = new Promise((_resolve, reject) => { failOldPage = reject })
  await withHome({
    extraInstalls,
    installPageOverride: ({ offset }) => offset === 30 ? oldPage : null
  }, async ({ host, tick }) => {
    let home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    const pager = findAll(home, (item) => item.props['aria-label'] === '市场已安装技能分页')[0]
    findAll(pager, (item) => item.type === 'a-pagination')[0].props.onChange(2, 30)
    const search = findAll(home, (item) => item.type === 'a-input' && item.props['aria-label'] === '搜索我的技能')[0]
    search.props['onUpdate:value']('数据汇总')
    await new Promise((resolve) => setTimeout(resolve, 300))
    await tick()
    failOldPage(new Error('旧页查询失败'))
    await tick()
    home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    assert.equal(findAll(home, (item) => item.type === 'a-alert' && visibleText(item).includes('旧页查询失败')).length, 0)
    const rows = findAll(home, (item) => item.props.class === 'skill-row').map(visibleText).join(' ')
    assert.match(rows, /安装技能 0/)
  })
})

test('搜索市场安装时请求服务端，后续页的安装可直接找到', async () => {
  const extraInstalls = Array.from({ length: 29 }, (_, index) => ({
    slug: `installed-${index}`, name: `安装技能 ${index}`, installed_version: 1, visible: true, category: 'other',
    description: index === 28 ? '专门做数据汇总' : ''
  }))
  await withHome({ extraInstalls }, async ({ host, requested, tick }) => {
    const search = findAll(host, (item) => item.type === 'a-input' && item.props['aria-label'] === '搜索我的技能')[0]
    search.props['onUpdate:value']('数据汇总')
    await new Promise((resolve) => setTimeout(resolve, 300))
    await tick()
    const home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    assert.ok(requested.some((request) => request.path.includes('/api/skill-market/-/installs?') && request.path.includes('search=%E6%95%B0%E6%8D%AE%E6%B1%87%E6%80%BB')))
    assert.match(visibleText(home), /安装技能 28/)
  })
})

test('本人原件位于后续页时，市场操作定向核验并打开个人原件', async () => {
  const extraPersonal = Array.from({ length: 30 }, (_, index) => ({
    slug: `personal-${index}`, name: `个人技能 ${index}`, source_scope: 'personal', source_type: 'upload', owner_uid: 'alice', category: 'other'
  }))
  const extraMarket = [{
    slug: 'late-market', name: '后续页的市场技能', owner_uid: 'alice', owner_name: 'Alice', own_personal_slug: 'personal-29',
    latest_version: 1, install_count: 0, likes_count: 0, category: 'other', scope: 'global'
  }]
  await withHome({ extraPersonal, extraMarket }, async ({ host, requested, router, tick }) => {
    findAll(host, (item) => item.props.role === 'tab' && visibleText(item) === '技能市场')[0].props.onClick()
    await tick()
    const card = findAll(host, (item) => item.props.class === 'market-card' && visibleText(item).includes('后续页的市场技能'))[0]
    assert.ok(card)
    const action = findAll(card, (item) => item.type === 'a-button' && visibleText(item) === '查看个人技能')[0]
    assert.ok(action)
    action.props.onClick()
    await tick()
    assert.equal(router.currentRoute.value.fullPath, '/extensions/skill/personal-29?scope=personal')
    assert.ok(requested.some((request) => request.path === '/api/skills/personal/personal-29'))
    assert.equal(requested.some((request) => request.path.includes('/api/skill-market/late-market/install')), false)
  })
})

test('同名个人技能即使位于后续页，市场卡也不再误导用户安装', async () => {
  const extraPersonal = Array.from({ length: 30 }, (_, index) => ({
    slug: index === 29 ? 'same-slug' : `personal-${index}`,
    name: `个人技能 ${index}`,
    source_scope: 'personal', source_type: 'upload', owner_uid: 'alice', category: 'other'
  }))
  const extraMarket = [{
    slug: 'same-slug', name: '他人发布的同名市场技能', owner_uid: 'bob', owner_name: 'Bob',
    latest_version: 1, install_count: 0, likes_count: 0, category: 'other', scope: 'global',
    install_conflict: 'personal'
  }]
  await withHome({ extraPersonal, extraMarket }, async ({ host, requested, router, tick }) => {
    findAll(host, (item) => item.props.role === 'tab' && visibleText(item) === '技能市场')[0].props.onClick()
    await tick()
    const card = findAll(host, (item) => item.props.class === 'market-card' && visibleText(item).includes('他人发布的同名市场技能'))[0]
    assert.match(visibleText(card), /已有同名个人技能/)
    const action = findAll(card, (item) => item.type === 'a-button' && visibleText(item) === '查看已有技能')[0]
    assert.ok(action)
    action.props.onClick()
    await tick()
    assert.equal(router.currentRoute.value.fullPath, '/extensions/skill/same-slug?scope=personal')
    assert.equal(requested.some((request) => request.path === '/api/skill-market/same-slug/install'), false)
  })
})

test('技能标识被占用时市场卡显示原因且不提供安装操作', async () => {
  const extraMarket = [{
    slug: 'reserved-market', name: '标识冲突的市场技能', owner_uid: 'bob', owner_name: 'Bob',
    latest_version: 1, install_count: 0, likes_count: 0, category: 'other', scope: 'global',
    install_conflict: 'reserved'
  }]
  await withHome({ extraMarket }, async ({ host, requested, tick }) => {
    findAll(host, (item) => item.props.role === 'tab' && visibleText(item) === '技能市场')[0].props.onClick()
    await tick()
    const card = findAll(host, (item) => item.props.class === 'market-card' && visibleText(item).includes('标识冲突的市场技能'))[0]
    assert.match(visibleText(card), /技能标识已被占用/)
    assert.equal(findAll(card, (item) => item.type === 'a-button' && visibleText(item) === '安装').length, 0)
    assert.equal(requested.some((request) => request.path === '/api/skill-market/reserved-market/install'), false)
  })
})

test('作者原件可用时即使市场标识被占用仍能查看原件', async () => {
  const extraMarket = [{
    slug: 'reserved-market', name: '本人发布的市场技能', owner_uid: 'alice', owner_name: 'Alice',
    own_personal_slug: 'my-skill', latest_version: 1, install_count: 0, likes_count: 0,
    category: 'other', scope: 'global', install_conflict: 'reserved'
  }]
  await withHome({ extraMarket }, async ({ host, requested, router, tick }) => {
    findAll(host, (item) => item.props.role === 'tab' && visibleText(item) === '技能市场')[0].props.onClick()
    await tick()
    const card = findAll(host, (item) => item.props.class === 'market-card' && visibleText(item).includes('本人发布的市场技能'))[0]
    const action = findAll(card, (item) => item.type === 'a-button' && visibleText(item) === '查看个人技能')[0]
    assert.ok(action)
    action.props.onClick()
    await tick()
    assert.equal(router.currentRoute.value.fullPath, '/extensions/skill/my-skill?scope=personal')
    assert.equal(requested.some((request) => request.path === '/api/skill-market/reserved-market/install'), false)
  })
})

test('作者另有与市场标识同名的个人技能时优先打开同名技能', async () => {
  const extraPersonal = [{ slug: 'published-2', name: '实际同名个人技能', source_scope: 'personal', source_type: 'upload', owner_uid: 'alice', category: 'other' }]
  const extraMarket = [{
    slug: 'published-2', name: '本人发布的市场技能', owner_uid: 'alice', owner_name: 'Alice',
    own_personal_slug: 'my-skill', latest_version: 1, install_count: 0, likes_count: 0,
    category: 'other', scope: 'global', install_conflict: 'personal'
  }]
  await withHome({ extraPersonal, extraMarket }, async ({ host, requested, router, tick }) => {
    findAll(host, (item) => item.props.role === 'tab' && visibleText(item) === '技能市场')[0].props.onClick()
    await tick()
    const card = findAll(host, (item) => item.props.class === 'market-card' && visibleText(item).includes('本人发布的市场技能'))[0]
    assert.match(visibleText(card), /已有同名个人技能/)
    const action = findAll(card, (item) => item.type === 'a-button' && visibleText(item) === '查看已有技能')[0]
    assert.ok(action)
    action.props.onClick()
    await tick()
    assert.equal(router.currentRoute.value.fullPath, '/extensions/skill/published-2?scope=personal')
    assert.equal(requested.some((request) => request.path === '/api/skill-market/published-2/install'), false)
  })
})

test('作者原件已删除时仍提前显示市场标识占用', async () => {
  const extraMarket = [{
    slug: 'reserved-market', name: '原件已删除的市场技能', owner_uid: 'alice', owner_name: 'Alice',
    own_personal_slug: 'deleted-source', latest_version: 1, install_count: 0, likes_count: 0,
    category: 'other', scope: 'global', install_conflict: 'reserved'
  }]
  await withHome({ extraMarket }, async ({ host, requested, tick }) => {
    findAll(host, (item) => item.props.role === 'tab' && visibleText(item) === '技能市场')[0].props.onClick()
    await tick()
    const card = findAll(host, (item) => item.props.class === 'market-card' && visibleText(item).includes('原件已删除的市场技能'))[0]
    assert.match(visibleText(card), /技能标识已被占用/)
    assert.doesNotMatch(visibleText(card), /个人技能已可用/)
    const action = findAll(card, (item) => item.type === 'a-button' && visibleText(item) === '查看个人技能')[0]
    assert.ok(action)
    action.props.onClick()
    await tick()
    assert.equal(requested.some((request) => request.path === '/api/skill-market/reserved-market/install'), false)
    assert.equal(findAll(card, (item) => item.type === 'a-button' && visibleText(item) === '查看个人技能').length, 0)
  })
})

test('搜索个人技能时由服务器查询后续页，描述命中仍显示结果', async () => {
  const extraPersonal = Array.from({ length: 30 }, (_, index) => ({
    slug: `personal-${index}`, name: `个人技能 ${index}`, source_scope: 'personal', source_type: 'upload', owner_uid: 'alice', category: 'other',
    description: index === 29 ? '特别分析任务' : ''
  }))
  await withHome({ extraPersonal }, async ({ host, requested, tick }) => {
    const search = findAll(host, (item) => item.type === 'a-input' && item.props['aria-label'] === '搜索我的技能')[0]
    assert.ok(search)
    search.props['onUpdate:value']('特别分析')
    await new Promise((resolve) => setTimeout(resolve, 300))
    await tick()
    const home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    assert.ok(requested.some((request) => request.path.includes('source=personal') && request.path.includes('search=%E7%89%B9%E5%88%AB%E5%88%86%E6%9E%90')))
    assert.match(visibleText(home), /个人技能 29/)
    assert.doesNotMatch(visibleText(home), /没有找到匹配的技能/)
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

test('技能市场按页替换卡片，显示准确总数并可跳转后页', async () => {
  const extraMarket = Array.from({ length: 24 }, (_, index) => ({
    slug: `market-${index}`, name: `市场分页技能 ${index}`, owner_uid: 'bob', owner_name: 'Bob',
    latest_version: 1, install_count: 0, likes_count: 0, category: 'other', scope: 'global'
  }))
  await withHome({ extraMarket, query: { tab: 'skills', view: 'market' } }, async ({ host, requested, tick }) => {
    let market = findAll(host, (item) => item.props.id === 'skill-market-view')[0]
    assert.equal(findAll(market, (item) => item.props.class === 'market-card').length, 24)
    assert.match(visibleText(market), /共 25 项/)
    const pagination = findAll(market, (item) => item.type === 'a-pagination')[0]
    assert.ok(pagination)
    assert.equal(pagination.props.total, 25)
    pagination.props.onChange(2, 24)
    await tick()
    market = findAll(host, (item) => item.props.id === 'skill-market-view')[0]
    assert.equal(findAll(market, (item) => item.props.class === 'market-card').length, 1)
    assert.match(visibleText(market), /市场分页技能 23/)
    assert.doesNotMatch(visibleText(market), /市场新技能/)
    assert.ok(requested.some((request) => request.path.includes('/api/skill-market?') && request.path.includes('offset=24') && request.path.includes('limit=24')))
  })
})

test('市场最后一页全部删除时自动回到第一页空状态', async () => {
  const extraMarket = Array.from({ length: 24 }, (_, index) => ({
    slug: `market-${index}`, name: `市场分页技能 ${index}`, owner_uid: 'bob', owner_name: 'Bob',
    latest_version: 1, install_count: 0, likes_count: 0, category: 'other', scope: 'global'
  }))
  let deleted = false
  await withHome({
    extraMarket,
    query: { tab: 'skills', view: 'market' },
    marketPageOverride: ({ offset }) => {
      if (offset === 24) deleted = true
      return deleted ? { data: [], total: 0, limit: 24, offset } : null
    }
  }, async ({ host, requested, tick }) => {
    let market = findAll(host, (item) => item.props.id === 'skill-market-view')[0]
    findAll(market, (item) => item.type === 'a-pagination')[0].props.onChange(2, 24)
    await tick()
    market = findAll(host, (item) => item.props.id === 'skill-market-view')[0]
    assert.match(visibleText(market), /共 0 项/)
    assert.ok(findAll(market, (item) => item.props.class === 'market-empty').length)
    assert.equal(findAll(market, (item) => item.type === 'a-pagination').length, 0)
    assert.ok(requested.filter((item) => item.path.startsWith('/api/skill-market?') && item.path.includes('offset=0')).length >= 2)
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
    assert.equal(findAll(home, (item) => item.type === 'a-button' && visibleText(item) === '配置 Agent').length, 0)
  })
})

test('失去市场可见性的旧安装仍能从个人清单卸载', async () => {
  await withHome({ installedItems: [{ slug: 'old-market', installed_version: 1, visible: false, unavailable_reason: 'unpublished' }] }, async ({ host, confirms, requested, tick }) => {
    const home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    const oldRow = findAll(home, (item) => item.props.class === 'skill-row' && visibleText(item).includes('old-market'))[0]
    const uninstall = findAll(oldRow, (item) => item.type === 'a-button' && visibleText(item) === '卸载旧安装')[0]
    uninstall.props.onClick()
    assert.equal(confirms.length, 1)
    assert.equal(confirms[0].title, '卸载 old-market？')
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

test('共享与市场审核只展示当前页，并能继续查看后续申请', async () => {
  const requests = Array.from({ length: 11 }, (_, index) => ({
    id: `request-${index}`, name: `申请 ${index}`, owner_uid: 'bob', personal_slug: `skill-${index}`,
    status: 'pending', created_at: new Date(Date.UTC(2026, 9, index + 1)).toISOString()
  }))
  await withHome({ role: 'admin', shares: requests, marketRequests: requests }, async ({ host, tick }) => {
    findAll(host, (item) => item.type === 'a-button' && visibleText(item).includes('共享申请'))[0].props.onClick()
    await tick()
    let modal = findAll(host, (item) => item.type === 'a-modal' && item.props.title === 'Skill 共享申请')[0]
    assert.equal(findAll(modal, (item) => item.props.class === 'skill-share-request').length, 10)
    findAll(modal, (item) => item.type === 'a-button' && visibleText(item) === '下一页')[0].props.onClick()
    await tick()
    modal = findAll(host, (item) => item.type === 'a-modal' && item.props.title === 'Skill 共享申请')[0]
    assert.equal(findAll(modal, (item) => item.props.class === 'skill-share-request').length, 1)

    findAll(host, (item) => item.type === 'a-button' && visibleText(item).includes('市场审核'))[0].props.onClick()
    await tick()
    modal = findAll(host, (item) => item.type === 'a-modal' && item.props.title === '市场审核申请')[0]
    assert.equal(findAll(modal, (item) => item.props.class === 'market-request').length, 10)
    findAll(modal, (item) => item.type === 'a-button' && visibleText(item) === '下一页')[0].props.onClick()
    await tick()
    modal = findAll(host, (item) => item.type === 'a-modal' && item.props.title === '市场审核申请')[0]
    assert.equal(findAll(modal, (item) => item.props.class === 'market-request').length, 1)
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

test('可管理的共享技能在后续页时仍显示治理入口', async () => {
  const extraShared = Array.from({ length: 29 }, (_, index) => ({
    slug: `later-shared-${index}`,
    name: `共享技能 ${index}`,
    source_scope: 'shared',
    source_type: 'upload',
    enabled: true,
    can_manage: index === 28
  }))
  await withHome({ role: 'user', extraShared }, async ({ host }) => {
    const entry = findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '技能治理')[0]
    assert.ok(entry, '后续页可能有可治理技能时，入口仍应可见')
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
  const history = { id: 'archive-1', owner_uid: 'alice', personal_slug: 'old-skill', name: '归档技能', status: 'approved', published_version: 1, created_at: '2026-10-14T08:00:00Z' }
  await withHome({ personalFailure: true, mineShares: [history] }, async ({ host, tick }) => {
    let home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    assert.equal(findAll(home, (item) => item.props.class === 'skill-overview').length, 0)
    findAll(home, (item) => item.type === 'a-button' && visibleText(item) === '查看发布记录')[0].props.onClick()
    await tick()
    home = findAll(host, (item) => item.props.id === 'skill-personal-view')[0]
    assert.equal(findAll(home, (item) => item.type === 'a-alert').length, 1)
    assert.match(visibleText(home), /归档技能/)
    assert.doesNotMatch(visibleText(home), /原件已删除|还没有可发布的个人技能|这里还没有技能/)
  })
})

test('发布记录仅列真实申请，按技能合并两个渠道并保留已删除原件', async () => {
  const own = { owner_uid: 'alice', personal_slug: 'my-skill', name: '我创建的技能' }
  const archived = { owner_uid: 'alice', personal_slug: 'old-skill', name: '已删除原件的技能' }
  const mineShares = [
    { ...own, id: 'share-1', status: 'approved', published_version: 2, created_at: '2026-10-12T08:00:00Z', read_scope: { access_level: 'department', department_ids: [3] } },
    { ...archived, id: 'share-old', status: 'approved', published_version: 1, created_at: '2026-10-11T08:00:00Z' },
    { owner_uid: 'bob', personal_slug: 'bob-skill', name: '他人技能', id: 'share-bob', status: 'approved', created_at: '2026-10-14T08:00:00Z' }
  ]
  const mineMarket = [
    { ...own, id: 'market-2', status: 'pending', created_at: '2026-10-14T08:00:00Z', current_market_version: 1, existing_market_scope: 'global' },
    { ...own, id: 'market-1', status: 'approved', created_at: '2026-10-13T08:00:00Z', current_market_version: 1, existing_market_scope: 'global' }
  ]
  await withHome({ mineShares, mineMarket, extraPersonal: [{ slug: 'draft-only', name: '从未发布的草稿', owner_uid: 'alice', source_scope: 'personal', source_type: 'upload' }] }, async ({ host, requested, tick }) => {
    const open = findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '查看发布记录')[0]
    open.props.onClick()
    await tick()
    const records = findAll(host, (item) => item.props['aria-label'] === '发布与审核记录')[0]
    assert.ok(records)
    const text = visibleText(records)
    for (const label of ['我创建的技能', '已删除原件的技能', '直接共享', '上架市场', '已上架', '待审核', '全公司', '本部门', '原件已删除']) assert.match(text, new RegExp(label))
    assert.doesNotMatch(text, /从未发布的草稿|他人技能/)
    assert.equal(findAll(records, (item) => item.props['data-skill-slug'] === 'my-skill').length, 1)
    assert.ok(requested.some((request) => request.path.startsWith('/api/skills/publication-records?')))
  })
})

test('发布技能分组和历史申请各自按需加载后续记录', async () => {
  const mineShares = Array.from({ length: 21 }, (_, index) => ({
    id: `archived-${index}`, owner_uid: 'alice', personal_slug: `archived-${index}`, name: `归档 ${index}`,
    status: 'rejected', created_at: new Date(Date.UTC(2026, 9, index + 1)).toISOString()
  }))
  await withHome({ mineShares }, async ({ host, tick }) => {
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '查看发布记录')[0].props.onClick()
    await tick()
    let records = findAll(host, (item) => item.props['aria-label'] === '发布与审核记录')[0]
    assert.equal(findAll(records, (item) => item.props['data-skill-slug']).length, 20)
    findAll(records, (item) => item.type === 'a-button' && visibleText(item).includes('加载更多技能'))[0].props.onClick()
    await tick()
    records = findAll(host, (item) => item.props['aria-label'] === '发布与审核记录')[0]
    assert.equal(findAll(records, (item) => item.props['data-skill-slug']).length, 21)
  })

  const versions = Array.from({ length: 21 }, (_, index) => ({
    id: `version-${index}`, owner_uid: 'alice', personal_slug: 'archive', name: '归档技能',
    status: 'rejected', created_at: new Date(Date.UTC(2026, 9, index + 1)).toISOString()
  }))
  await withHome({ mineShares: versions }, async ({ host, tick }) => {
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '查看发布记录')[0].props.onClick()
    await tick()
    const row = findAll(host, (item) => item.props['data-skill-slug'] === 'archive')[0]
    findAll(row, (item) => item.type === 'a-button' && visibleText(item) === '查看记录')[0].props.onClick()
    await tick()
    let history = findAll(host, (item) => item.props['aria-label'] === '技能审核历程')[0]
    assert.equal(findAll(history, (item) => item.props['data-request-id']).length, 20)
    findAll(history, (item) => item.type === 'a-button' && visibleText(item) === '加载更多记录')[0].props.onClick()
    await tick()
    history = findAll(host, (item) => item.props['aria-label'] === '技能审核历程')[0]
    assert.equal(findAll(history, (item) => item.props['data-request-id']).length, 21)
  })
})

test('归档技能的完整审核历程和旧提交快照仍可按申请查看', async () => {
  const source = { owner_uid: 'alice', personal_slug: 'old-skill', name: '已删除原件的技能' }
  const mineShares = [
    { ...source, id: 'share-new', status: 'rejected', review_note: '请补充说明', application_reason: '再次共享', created_at: '2026-10-14T08:00:00Z', reviewed_at: '2026-10-14T09:00:00Z' },
    { ...source, id: 'share-old', status: 'approved', published_version: 1, application_reason: '首次共享', created_at: '2026-10-11T08:00:00Z', reviewed_at: '2026-10-11T09:00:00Z' }
  ]
  const mineMarket = [{ ...source, id: 'market-1', status: 'pending', application_reason: '上架试用', created_at: '2026-10-12T08:00:00Z' }]
  const snapshots = { 'share-old': { tree: [{ path: 'SKILL.md', name: 'SKILL.md', is_dir: false }], content: '# 首次提交的内容' } }
  await withHome({ mineShares, mineMarket, snapshots }, async ({ host, requested, tick }) => {
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '查看发布记录')[0].props.onClick()
    await tick()
    const row = findAll(host, (item) => item.props['data-skill-slug'] === 'old-skill')[0]
    findAll(row, (item) => item.type === 'a-button' && visibleText(item) === '查看记录')[0].props.onClick()
    await tick()
    const history = findAll(host, (item) => item.props['aria-label'] === '技能审核历程')[0]
    assert.ok(history)
    const items = findAll(history, (item) => item.props['data-request-id'])
    assert.deepEqual(items.map((item) => item.props['data-request-id']), ['share-new', 'share-old', 'market-1'])
    assert.match(visibleText(history), /请补充说明|再次共享|首次共享|上架试用/)
    const old = items.find((item) => item.props['data-request-id'] === 'share-old')
    findAll(old, (item) => item.type === 'a-button' && visibleText(item) === '查看提交快照')[0].props.onClick()
    await tick()
    const snapshot = findAll(host, (item) => item.props['aria-label'] === '历史提交快照')[0]
    assert.match(visibleText(snapshot), /首次提交的内容/)
    assert.ok(requested.some((request) => request.path === '/api/skills/share-requests/share-old/tree'))
    assert.ok(requested.some((request) => request.path === '/api/skills/share-requests/share-old/snapshot?path=SKILL.md'))
    assert.equal(requested.some((request) => request.path.includes('/api/skills/personal/old-skill')), false)
  })
})

test('发布记录读取失败时给出重试，不把失败显示为空记录', async () => {
  const mineShares = [{ id: 'share-1', owner_uid: 'alice', personal_slug: 'my-skill', name: '我创建的技能', status: 'pending', created_at: '2026-10-14T08:00:00Z' }]
  await withHome({ mineShares, historyFailureOnce: true }, async ({ host, tick }) => {
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '查看发布记录')[0].props.onClick()
    await tick()
    let records = findAll(host, (item) => item.props['aria-label'] === '发布与审核记录')[0]
    assert.match(visibleText(records), /发布记录读取失败/)
    assert.doesNotMatch(visibleText(records), /还没有发布申请记录/)
    findAll(records, (item) => item.type === 'a-button' && visibleText(item) === '重试')[0].props.onClick()
    await tick()
    records = findAll(host, (item) => item.props['aria-label'] === '发布与审核记录')[0]
    assert.match(visibleText(records), /我创建的技能/)
    assert.doesNotMatch(visibleText(records), /发布记录读取失败/)
  })
})

test('共享副本和指定成员范围按每次批准分别显示，不误判整个渠道已停用', async () => {
  const source = { owner_uid: 'alice', personal_slug: 'my-skill', name: '我创建的技能' }
  const mineShares = [
    { ...source, id: 'share-v2', status: 'approved', published_version: 2, published_copy_status: 'disabled', read_scope: { access_level: 'user', user_uids: ['bob', 'cara'] }, created_at: '2026-10-15T08:00:00Z' },
    { ...source, id: 'share-v1', status: 'approved', published_version: 1, published_copy_status: 'enabled', read_scope: { access_level: 'user', user_uids: ['dana'] }, created_at: '2026-10-11T08:00:00Z' }
  ]
  await withHome({ mineShares }, async ({ host, tick }) => {
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '查看发布记录')[0].props.onClick()
    await tick()
    const row = findAll(host, (item) => item.props['data-skill-slug'] === 'my-skill')[0]
    assert.match(visibleText(row), /审核结果：已通过|当前共享副本：已停用|指定成员（2 人）/)
    assert.doesNotMatch(visibleText(row), /共享已停用/)
    findAll(row, (item) => item.type === 'a-button' && visibleText(item) === '查看记录')[0].props.onClick()
    await tick()
    const history = findAll(host, (item) => item.props['aria-label'] === '技能审核历程')[0]
    assert.match(visibleText(history), /指定成员：bob、cara|指定成员：dana|当前共享副本：已启用|当前共享副本：已停用/)
  })
})

test('旧共享记录把审核、新申请和无法核对的副本情况分开显示', async () => {
  const mineShares = [
    { id: 'share-new', owner_uid: 'alice', personal_slug: 'reading', name: '阅读技能', status: 'pending', created_at: '2026-10-16T08:00:00Z' },
    { id: 'share-legacy', owner_uid: 'alice', personal_slug: 'reading', name: '阅读技能', status: 'approved', published_version: '2.0.0', published_copy_status: 'unknown', created_at: '2026-10-15T08:00:00Z' }
  ]
  await withHome({ mineShares }, async ({ host, tick }) => {
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '查看发布记录')[0].props.onClick()
    await tick()
    const row = findAll(host, (item) => item.props['data-skill-slug'] === 'reading')[0]
    assert.match(visibleText(row), /审核结果：已通过/)
    assert.match(visibleText(row), /当前共享副本：无法核对/)
    assert.match(visibleText(row), /这条历史记录没有保存对应的共享副本信息，因此无法判断它现在是否可用/)
    assert.match(visibleText(row), /新申请：待审核/)
    assert.doesNotMatch(visibleText(row), /已启用|已停用|已失效/)
    findAll(row, (item) => item.type === 'a-button' && visibleText(item) === '查看记录')[0].props.onClick()
    await tick()
    const history = findAll(host, (item) => item.props['aria-label'] === '技能审核历程')[0]
    assert.match(visibleText(history), /当前共享副本：无法核对/)
    assert.match(visibleText(history), /这条历史记录没有保存对应的共享副本信息，因此无法判断它现在是否可用/)
  })
})

test('只有待审共享申请时只显示申请进度，不暗示已有共享副本', async () => {
  const mineShares = [{
    id: 'share-pending', owner_uid: 'alice', personal_slug: 'new-skill', name: '新技能',
    status: 'pending', created_at: '2026-10-16T08:00:00Z'
  }]
  await withHome({ mineShares }, async ({ host, tick }) => {
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '查看发布记录')[0].props.onClick()
    await tick()
    const row = findAll(host, (item) => item.props['data-skill-slug'] === 'new-skill')[0]
    assert.match(visibleText(row), /直接共享.*最新申请：待审核/s)
    assert.doesNotMatch(visibleText(row), /审核结果：已通过|当前共享副本/)
  })
})

test('市场历史按审核确定的部门范围展示，而非申请人后来所属部门', async () => {
  const mineMarket = [{
    id: 'market-dept', owner_uid: 'alice', personal_slug: 'my-skill', name: '我创建的技能', status: 'approved',
    published_version: 2, current_market_version: 2, existing_market_scope: 'department', existing_market_department_id: 3,
    owner_department_id: 7, read_scope: { access_level: 'department', department_ids: [3] }, created_at: '2026-10-15T08:00:00Z'
  }]
  await withHome({ mineMarket }, async ({ host, tick }) => {
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '查看发布记录')[0].props.onClick()
    await tick()
    const row = findAll(host, (item) => item.props['data-skill-slug'] === 'my-skill')[0]
    assert.match(visibleText(row), /当前范围：本部门/)
    findAll(row, (item) => item.type === 'a-button' && visibleText(item) === '查看记录')[0].props.onClick()
    await tick()
    const history = findAll(host, (item) => item.props['aria-label'] === '技能审核历程')[0]
    assert.match(visibleText(history), /发布范围：本部门/)
    assert.doesNotMatch(visibleText(history), /部门 7/)
  })
})

test('旧市场审核记录缺少 read_scope 时以提交时的 department_ids 显示历史范围', async () => {
  const mineMarket = [{
    id: 'market-legacy-dept', owner_uid: 'alice', personal_slug: 'my-skill', name: '我创建的技能', status: 'approved',
    published_version: 2, current_market_version: 2, read_scope: null, department_ids: [3],
    existing_market_scope: 'global', existing_market_department_id: null, owner_department_id: 4,
    created_at: '2026-10-15T08:00:00Z'
  }]
  await withHome({ mineMarket }, async ({ host, tick }) => {
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '查看发布记录')[0].props.onClick()
    await tick()
    const row = findAll(host, (item) => item.props['data-skill-slug'] === 'my-skill')[0]
    assert.match(visibleText(row), /最近审核范围：本部门/)
    assert.match(visibleText(row), /当前范围：全公司/)
    findAll(row, (item) => item.type === 'a-button' && visibleText(item) === '查看记录')[0].props.onClick()
    await tick()
    const history = findAll(host, (item) => item.props['aria-label'] === '技能审核历程')[0]
    assert.match(visibleText(history), /发布范围：本部门/)
    assert.doesNotMatch(visibleText(history), /发布范围：全公司|发布范围未确认/)
  })
})

test('管理员审核列表沿用申请当时的部门记录，不显示后来变更的市场范围', async () => {
  const reviewMarket = [{
    id: 'market-approved', name: '周报助手', owner_uid: 'bob', personal_slug: 'weekly-report', status: 'approved',
    department_ids: [3], existing_market_scope: 'department', existing_market_department_id: 4,
    created_at: '2026-10-12T08:00:00Z'
  }]
  await withHome({ role: 'admin', reviewMarket }, async ({ host, tick }) => {
    findAll(host, (item) => item.type === 'a-button' && visibleText(item).includes('市场审核'))[0].props.onClick()
    await tick()
    const modal = findAll(host, (item) => item.type === 'a-modal' && item.props.title === '市场审核申请')[0]
    const filter = findAll(modal, (item) => item.type === 'a-select')[0]
    filter.props['onUpdate:value']('approved')
    filter.props.onChange()
    await tick()
    assert.match(visibleText(modal), /设计部及超级管理员可见/)
    assert.doesNotMatch(visibleText(modal), /研发部及超级管理员可见/)
  })
})

test('旧市场申请缺少发布版本时不把当前市场版本写成当次审核版本', async () => {
  const mineMarket = [{
    id: 'market-legacy', owner_uid: 'alice', personal_slug: 'my-skill', name: '我创建的技能', status: 'approved',
    current_market_version: 4, existing_market_scope: 'global', created_at: '2026-10-15T08:00:00Z'
  }]
  await withHome({ mineMarket }, async ({ host, tick }) => {
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '查看发布记录')[0].props.onClick()
    await tick()
    const row = findAll(host, (item) => item.props['data-skill-slug'] === 'my-skill')[0]
    assert.match(visibleText(row), /v4/)
    findAll(row, (item) => item.type === 'a-button' && visibleText(item) === '查看记录')[0].props.onClick()
    await tick()
    const event = findAll(host, (item) => item.props['data-request-id'] === 'market-legacy')[0]
    assert.match(visibleText(event), /审核通过/)
    assert.doesNotMatch(visibleText(event), /v4/)
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

const managedPublished = { slug: 'weekly-report', name: '周报助手', is_published: true, scope: 'department', department_id: 3, latest_version: 3, publication_generation: 1, state_revision: 6 }
const managedWithdrawn = { slug: 'withdrawn-report', name: '旧周报助手', is_published: false, scope: 'department', department_id: 3, latest_version: 2, publication_generation: 2, state_revision: 9 }

test('市场管理清单每页20条，分页后只展示服务端当前页且清除旧详情', async () => {
  const rows = Array.from({ length: 25 }, (_, index) => ({ ...managedPublished, slug: `report-${index}`, name: `周报 ${index}` }))
  await withHome({ role: 'admin', managedMarket: rows }, async ({ host, requested, tick }) => {
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '管理市场上架')[0].props.onClick()
    await tick()
    let drawer = findAll(host, (item) => item.type === 'a-drawer' && item.props.title === '市场上架管理')[0]
    assert.equal(findAll(drawer, (item) => item.props['data-managed-slug']).length, 20)
    assert.match(visibleText(drawer), /共 25 项/)
    assert.ok(requested.some((item) => item.path === '/api/skill-market/-/management?offset=0&limit=20'))
    findAll(drawer, (item) => item.props['data-managed-slug'] === 'report-0')[0].children.flatMap((child) => findAll(child, (item) => item.type === 'a-button' && visibleText(item) === '管理'))[0].props.onClick()
    await tick()
    findAll(drawer, (item) => item.type === 'a-pagination' && item.props['aria-label'] === '市场上架管理分页')[0].props.onChange(2)
    await tick()
    drawer = findAll(host, (item) => item.type === 'a-drawer' && item.props.title === '市场上架管理')[0]
    assert.equal(findAll(drawer, (item) => item.props['data-managed-slug']).length, 5)
    assert.equal(findAll(drawer, (item) => item.props['data-managed-slug'] === 'report-0').length, 0)
    assert.equal(findAll(drawer, (item) => item.props['aria-label'] === '市场条目管理详情').length, 0)
    assert.ok(requested.some((item) => item.path === '/api/skill-market/-/management?offset=20&limit=20'))
  })
})

test('从市场详情直达管理时精确查找 slug，主动搜索恢复普通匹配', async () => {
  const rows = [
    ...Array.from({ length: 25 }, (_, index) => ({ ...managedPublished, slug: `new-market-${index}`, name: `市场新技能 ${index}`, scope: 'global' })),
    { ...managedPublished, slug: 'new-market', name: '目标市场技能', scope: 'global' }
  ]
  await withHome({ role: 'superadmin', query: { tab: 'skills', view: 'market' }, managedMarket: rows }, async ({ host, requested, tick }) => {
    await tick()
    findAll(host, (item) => item.type === 'button' && item.props.class === 'market-card-title')[0].props.onClick()
    await tick()
    await tick()
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '管理上架')[0].props.onClick()
    await tick()
    let drawer = findAll(host, (item) => item.type === 'a-drawer' && item.props.title === '市场上架管理')[0]
    assert.equal(findAll(drawer, (item) => item.props['data-managed-slug'] === 'new-market').length, 1)
    assert.match(visibleText(findAll(drawer, (item) => item.props['aria-label'] === '市场条目管理详情')[0]), /目标市场技能/)
    assert.ok(requested.some((item) => item.path.startsWith('/api/skill-market/-/management?') && new URL(item.path, 'http://yuxi.local').searchParams.get('slug') === 'new-market'))
    findAll(drawer, (item) => item.type === 'a-input-search' && item.props['aria-label'] === '搜索市场管理技能')[0].props.onSearch()
    await tick()
    drawer = findAll(host, (item) => item.type === 'a-drawer' && item.props.title === '市场上架管理')[0]
    assert.equal(findAll(drawer, (item) => item.props['data-managed-slug']).length, 20)
    assert.equal(findAll(drawer, (item) => item.props['aria-label'] === '市场条目管理详情').length, 0)
    const listRequests = requested.filter((item) => item.path.startsWith('/api/skill-market/-/management?'))
    assert.equal(new URL(listRequests.at(-1).path, 'http://yuxi.local').searchParams.has('slug'), false)
  })
})

test('从精确直达改状态或范围时恢复普通管理清单', async () => {
  const rows = [
    ...Array.from({ length: 25 }, (_, index) => ({ ...managedPublished, slug: `new-market-${index}`, name: `市场新技能 ${index}`, scope: 'global' })),
    { ...managedPublished, slug: 'new-market', name: '目标市场技能', scope: 'global' }
  ]
  for (const filter of ['市场管理状态筛选', '市场管理范围筛选']) {
    await withHome({ role: 'superadmin', query: { tab: 'skills', view: 'market' }, managedMarket: rows }, async ({ host, requested, tick }) => {
      await tick()
      findAll(host, (item) => item.type === 'button' && item.props.class === 'market-card-title')[0].props.onClick()
      await tick()
      await tick()
      findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '管理上架')[0].props.onClick()
      await tick()
      assert.ok(requested.some((item) => item.path.startsWith('/api/skill-market/-/management?') && new URL(item.path, 'http://yuxi.local').searchParams.get('slug') === 'new-market'))
      const drawer = findAll(host, (item) => item.type === 'a-drawer' && item.props.title === '市场上架管理')[0]
      findAll(drawer, (item) => item.type === 'a-select' && item.props['aria-label'] === filter)[0].props.onChange(filter === '市场管理状态筛选' ? 'published' : 'global')
      await tick()
      const listRequests = requested.filter((item) => item.path.startsWith('/api/skill-market/-/management?'))
      assert.equal(new URL(listRequests.at(-1).path, 'http://yuxi.local').searchParams.has('slug'), false)
      assert.match(visibleText(findAll(host, (item) => item.type === 'a-drawer' && item.props.title === '市场上架管理')[0]), /共 26 项/)
    })
  }
})

test('总数尚未收敛但续页为空时回到第一页重新读取', async () => {
  const rows = Array.from({ length: 21 }, (_, index) => ({ ...managedPublished, slug: `report-${index}` }))
  await withHome({ role: 'admin', managedMarket: rows, managedEmptyPageOnce: true }, async ({ host, requested, tick }) => {
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '管理市场上架')[0].props.onClick()
    await tick()
    const drawer = findAll(host, (item) => item.type === 'a-drawer' && item.props.title === '市场上架管理')[0]
    findAll(drawer, (item) => item.type === 'a-pagination' && item.props['aria-label'] === '市场上架管理分页')[0].props.onChange(2)
    await tick()
    assert.equal(findAll(drawer, (item) => item.props['data-managed-slug']).length, 20)
    const offsets = requested.filter((item) => item.path.startsWith('/api/skill-market/-/management?')).map((item) => new URL(item.path, 'http://yuxi.local').searchParams.get('offset'))
    assert.deepEqual(offsets, ['0', '20', '0'])
  })
})

test('市场管理搜索与状态筛选回第一页，非超管没有范围筛选', async () => {
  const rows = [managedPublished, managedWithdrawn, ...Array.from({ length: 20 }, (_, index) => ({ ...managedPublished, slug: `other-${index}`, name: `其他 ${index}` }))]
  await withHome({ role: 'admin', managedMarket: rows }, async ({ host, requested, tick }) => {
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '管理市场上架')[0].props.onClick()
    await tick()
    let drawer = findAll(host, (item) => item.type === 'a-drawer' && item.props.title === '市场上架管理')[0]
    assert.equal(findAll(drawer, (item) => item.type === 'a-select' && item.props['aria-label'] === '市场管理范围筛选').length, 0)
    findAll(drawer, (item) => item.type === 'a-pagination' && item.props['aria-label'] === '市场上架管理分页')[0].props.onChange(2)
    await tick()
    findAll(drawer, (item) => item.type === 'a-input-search' && item.props['aria-label'] === '搜索市场管理技能')[0].props['onUpdate:value'](' withdrawn ')
    findAll(drawer, (item) => item.type === 'a-select' && item.props['aria-label'] === '市场管理状态筛选')[0].props.onChange('withdrawn')
    await tick()
    drawer = findAll(host, (item) => item.type === 'a-drawer' && item.props.title === '市场上架管理')[0]
    assert.equal(findAll(drawer, (item) => item.props['data-managed-slug']).length, 1)
    assert.match(visibleText(drawer), /共 1 项/)
    assert.ok(requested.some((item) => item.path === '/api/skill-market/-/management?offset=0&limit=20&query=withdrawn&status=withdrawn'))
    const search = findAll(drawer, (item) => item.type === 'a-input-search' && item.props['aria-label'] === '搜索市场管理技能')[0]
    search.props['onUpdate:value']('report')
    search.props.onSearch()
    await tick()
    assert.ok(requested.some((item) => item.path.includes('query=report') && item.path.includes('status=withdrawn') && item.path.includes('offset=0')))
  })
})

test('超级管理员可按全公司范围筛选并只保留当前条件内的详情', async () => {
  await withHome({ role: 'superadmin', managedMarket: [managedPublished, { ...managedWithdrawn, scope: 'global' }] }, async ({ host, requested, tick }) => {
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '管理市场上架')[0].props.onClick()
    await tick()
    let drawer = findAll(host, (item) => item.type === 'a-drawer' && item.props.title === '市场上架管理')[0]
    findAll(drawer, (item) => item.props['data-managed-slug'] === 'weekly-report')[0].children.flatMap((child) => findAll(child, (item) => item.type === 'a-button' && visibleText(item) === '管理'))[0].props.onClick()
    await tick()
    findAll(drawer, (item) => item.type === 'a-select' && item.props['aria-label'] === '市场管理范围筛选')[0].props.onChange('global')
    await tick()
    drawer = findAll(host, (item) => item.type === 'a-drawer' && item.props.title === '市场上架管理')[0]
    assert.equal(findAll(drawer, (item) => item.props['data-managed-slug'] === 'weekly-report').length, 0)
    assert.equal(findAll(drawer, (item) => item.props['aria-label'] === '市场条目管理详情').length, 0)
    assert.ok(requested.some((item) => item.path.includes('scope=global') && item.path.includes('offset=0')))
  })
})

test('管理清单的迟到搜索响应不能覆盖较新的筛选结果', async () => {
  let releaseOld
  const oldGate = new Promise((resolve) => { releaseOld = resolve })
  await withHome({ role: 'admin', managedMarket: [managedPublished, managedWithdrawn], managedListGate: (params) => params.get('query') === 'weekly' ? oldGate : undefined }, async ({ host, tick }) => {
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '管理市场上架')[0].props.onClick()
    await tick()
    let drawer = findAll(host, (item) => item.type === 'a-drawer' && item.props.title === '市场上架管理')[0]
    const search = findAll(drawer, (item) => item.type === 'a-input-search' && item.props['aria-label'] === '搜索市场管理技能')[0]
    search.props['onUpdate:value']('weekly')
    search.props.onSearch()
    await tick()
    search.props['onUpdate:value']('withdrawn')
    search.props.onSearch()
    await tick()
    releaseOld()
    await tick()
    drawer = findAll(host, (item) => item.type === 'a-drawer' && item.props.title === '市场上架管理')[0]
    assert.equal(findAll(drawer, (item) => item.props['data-managed-slug'] === 'weekly-report').length, 0)
    assert.equal(findAll(drawer, (item) => item.props['data-managed-slug'] === 'withdrawn-report').length, 1)
  })
})

test('下架使当前管理页变空时回退至有效页', async () => {
  const rows = Array.from({ length: 21 }, (_, index) => ({ ...managedPublished, slug: `report-${index}`, name: `周报 ${index}` }))
  await withHome({ role: 'admin', managedMarket: rows }, async ({ host, confirms, requested, tick }) => {
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '管理市场上架')[0].props.onClick()
    await tick()
    let drawer = findAll(host, (item) => item.type === 'a-drawer' && item.props.title === '市场上架管理')[0]
    findAll(drawer, (item) => item.type === 'a-select' && item.props['aria-label'] === '市场管理状态筛选')[0].props.onChange('published')
    await tick()
    findAll(drawer, (item) => item.type === 'a-pagination' && item.props['aria-label'] === '市场上架管理分页')[0].props.onChange(2)
    await tick()
    drawer = findAll(host, (item) => item.type === 'a-drawer' && item.props.title === '市场上架管理')[0]
    findAll(drawer, (item) => item.props['data-managed-slug'] === 'report-20')[0].children.flatMap((child) => findAll(child, (item) => item.type === 'a-button' && visibleText(item) === '管理'))[0].props.onClick()
    await tick()
    findAll(drawer, (item) => item.type === 'a-button' && visibleText(item) === '下架技能')[0].props.onClick()
    await confirms[0].onOk()
    await tick()
    drawer = findAll(host, (item) => item.type === 'a-drawer' && item.props.title === '市场上架管理')[0]
    assert.equal(findAll(drawer, (item) => item.props['data-managed-slug']).length, 20)
    assert.match(visibleText(drawer), /共 20 项/)
    assert.ok(requested.some((item) => item.path.includes('status=published') && item.path.includes('offset=0')))
    assert.equal(findAll(drawer, (item) => item.props['aria-label'] === '市场条目管理详情').length, 0)
    assert.ok(requested.some((item) => item.path.includes('status=published') && item.path.includes('offset=20')))
  })
})

test('市场管理入口仅向管理员开放，清单保留已下架条目且普通目录不展示', async () => {
  await withHome({ managedMarket: [managedPublished, managedWithdrawn] }, async ({ host, requested }) => {
    assert.equal(findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '管理市场上架').length, 0)
    assert.equal(requested.some((item) => item.path.includes('/management')), false)
  })
  await withHome({ role: 'admin', managedMarket: [managedPublished, managedWithdrawn] }, async ({ host, requested, tick }) => {
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '管理市场上架')[0].props.onClick()
    await tick()
    const drawer = findAll(host, (item) => item.type === 'a-drawer' && item.props.title === '市场上架管理')[0]
    assert.ok(drawer)
    assert.match(visibleText(drawer), /withdrawn-report|已下架|作者重新提交/)
    assert.ok(requested.some((item) => item.path === '/api/skill-market/-/management?offset=0&limit=20'))
    findAll(host, (item) => item.props.role === 'tab' && visibleText(item) === '技能市场')[0].props.onClick()
    await tick()
    const publicMarket = findAll(host, (item) => item.props.id === 'skill-market-view')[0]
    assert.doesNotMatch(visibleText(publicMarket), /旧周报助手/)
  })
})

test('市场详情仅向有权治理该条目的管理员显示管理上架', async () => {
  const openListing = async (host, tick) => {
    await tick()
    const title = findAll(host, (item) => item.type === 'button' && item.props.class === 'market-card-title')[0]
    assert.ok(title, visibleText(host))
    title.props.onClick()
    await tick()
    await tick()
    const detail = findAll(host, (item) => item.type === 'a-modal' && item.props.title === 'Skill 详情')[0]
    assert.ok(detail, visibleText(host))
    return detail
  }
  const controls = (detail) => findAll(detail, (item) => item.type === 'a-button' && visibleText(item) === '管理上架')
  const marketQuery = { tab: 'skills', view: 'market' }
  await withHome({ role: 'admin', query: marketQuery }, async ({ host, tick }) => {
    assert.equal(controls(await openListing(host, tick)).length, 0, '部门管理员不能治理全公司条目')
  })
  await withHome({ role: 'superadmin', query: marketQuery }, async ({ host, tick, requested }) => {
    const detail = await openListing(host, tick)
    assert.equal(controls(detail).length, 1, `${visibleText(detail)} ${JSON.stringify(requested)}`)
  })
  const departmentListing = { slug: 'new-market', name: '市场新技能', owner_uid: 'bob', owner_name: 'Bob', latest_version: 1, install_count: 4, likes_count: 2, category: 'other', scope: 'department', department_id: 3, description: '市场展示条目' }
  await withHome({ role: 'admin', query: marketQuery, publicMarketDetail: departmentListing }, async ({ host, tick }) => {
    assert.equal(controls(await openListing(host, tick)).length, 1)
  })
  await withHome({ role: 'admin', query: marketQuery, publicMarketDetail: { ...departmentListing, department_id: 4 } }, async ({ host, tick }) => {
    assert.equal(controls(await openListing(host, tick)).length, 0, '部门管理员不能治理其他部门条目')
  })
})

test('部门管理员能下架本部门已上架条目，但看不到范围修改入口', async () => {
  await withHome({ role: 'admin', managedMarket: [managedPublished, managedWithdrawn] }, async ({ host, requested, confirms, tick }) => {
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '管理市场上架')[0].props.onClick()
    await tick()
    const row = findAll(host, (item) => item.props['data-managed-slug'] === 'weekly-report')[0]
    findAll(row, (item) => item.type === 'a-button' && visibleText(item) === '管理')[0].props.onClick()
    await tick()
    const detail = findAll(host, (item) => item.props['aria-label'] === '市场条目管理详情')[0]
    assert.ok(detail)
    assert.equal(findAll(detail, (item) => item.type === 'a-select' && item.props['aria-label'] === '当前上架范围').length, 0)
    findAll(detail, (item) => item.type === 'a-button' && visibleText(item) === '下架技能')[0].props.onClick()
    assert.match(confirms[0].content, /不再供成员使用/)
    await confirms[0].onOk()
    await tick()
    assert.ok(requested.some((item) => item.path === '/api/skill-market/weekly-report/unpublish' && item.method === 'POST' && item.body.expected_revision === 6))
    assert.match(visibleText(host), /已下架/)
  })
})

test('超级管理员改范围携带读取的版本，冲突后刷新详情而不覆盖先前决定', async () => {
  await withHome({ role: 'superadmin', managedMarket: [managedPublished], scopeConflictOnce: true }, async ({ host, requested, confirms, tick }) => {
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '管理市场上架')[0].props.onClick()
    await tick()
    findAll(host, (item) => item.props['data-managed-slug'] === 'weekly-report')[0].children.flatMap((child) => findAll(child, (item) => item.type === 'a-button' && visibleText(item) === '管理'))[0].props.onClick()
    await tick()
    const scope = findAll(host, (item) => item.type === 'a-select' && item.props['aria-label'] === '当前上架范围')[0]
    scope.props['onUpdate:value']('department:4')
    await tick()
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '保存范围')[0].props.onClick()
    assert.match(confirms[0].content, /研发部/)
    await confirms[0].onOk()
    await tick()
    assert.ok(requested.some((item) => item.path === '/api/skill-market/weekly-report/scope' && item.method === 'PUT' && item.body.expected_revision === 6 && item.body.scope === 'department' && item.body.department_id === 4))
    assert.ok(requested.filter((item) => item.path === '/api/skill-market/-/management/weekly-report').length >= 2, '冲突后应重新读取管理详情')
    assert.ok(requested.filter((item) => item.path.startsWith('/api/skill-market/-/management?')).length >= 2, '冲突后应重新读取清单')
    assert.match(visibleText(host), /状态已变化|请重新确认/)
  })
})

test('异步管理详情只显示最后选中的条目', async () => {
  let resolveFirst
  const first = new Promise((resolve) => { resolveFirst = resolve })
  const second = { ...managedWithdrawn }
  await withHome({ role: 'admin', managedMarket: [managedPublished, managedWithdrawn], managedDetails: { 'weekly-report': first, 'withdrawn-report': second } }, async ({ host, tick }) => {
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '管理市场上架')[0].props.onClick()
    await tick()
    const manage = (slug) => findAll(findAll(host, (item) => item.props['data-managed-slug'] === slug)[0], (item) => item.type === 'a-button' && visibleText(item) === '管理')[0]
    manage('weekly-report').props.onClick()
    manage('withdrawn-report').props.onClick()
    await tick()
    resolveFirst({ ...managedPublished })
    await tick()
    const detail = findAll(host, (item) => item.props['aria-label'] === '市场条目管理详情')[0]
    assert.match(visibleText(detail), /withdrawn-report/)
    assert.doesNotMatch(visibleText(detail), /weekly-report/)
  })
})

test('管理清单加载中关闭抽屉后不再打开旧条目详情', async () => {
  let finishList
  const managedListGate = new Promise((resolve) => { finishList = resolve })
  await withHome({
    role: 'superadmin', query: { tab: 'skills', view: 'market' },
    managedMarket: [{ ...managedPublished, slug: 'new-market' }], managedListGate
  }, async ({ host, requested, tick }) => {
    await tick()
    findAll(host, (item) => item.type === 'button' && item.props.class === 'market-card-title')[0].props.onClick()
    await tick()
    await tick()
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '管理上架')[0].props.onClick()
    await tick()
    const drawer = findAll(host, (item) => item.type === 'a-drawer' && item.props.title === '市场上架管理')[0]
    assert.ok(drawer)
    drawer.props.onClose()
    await tick()
    finishList()
    await tick()
    await tick()
    assert.equal(findAll(host, (item) => item.type === 'a-drawer' && item.props.title === '市场上架管理').length, 0)
    assert.equal(requested.some((item) => item.path === '/api/skill-market/-/management/new-market'), false)
    findAll(host, (item) => item.type === 'a-button' && visibleText(item) === '管理市场上架')[0].props.onClick()
    await tick()
    const reopened = findAll(host, (item) => item.type === 'a-drawer' && item.props.title === '市场上架管理')[0]
    assert.match(visibleText(reopened), /选择一项查看当前状态/)
    assert.equal(findAll(reopened, (item) => item.props['aria-label'] === '市场条目管理详情').length, 0)
  })
})

test('重新上架且在范围内的旧安装保留卡片信息并提供本人显式确认升级', async () => {
  await withHome({ installedItems: [
    { slug: 'relisted', installed_version: 2, visible: true, unavailable_reason: 'needs_confirmation', name: '重新上架的周报', latest_version: 4, upgrade_available: true }
  ] }, async ({ host, confirms, requested, tick }) => {
    const row = findAll(host, (item) => item.props.class === 'skill-row' && visibleText(item).includes('relisted'))[0]
    assert.match(visibleText(row), /需确认新版/)
    assert.match(visibleText(row), /重新上架的周报/)
    findAll(row, (item) => item.type === 'a-button' && visibleText(item) === '确认升级')[0].props.onClick()
    assert.match(confirms[0].content, /新版本/)
    await confirms[0].onOk()
    await tick()
    assert.ok(requested.some((item) => item.path === '/api/skill-market/relisted/upgrade' && item.method === 'POST'))
  })
})
