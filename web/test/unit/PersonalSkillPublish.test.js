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
import { createServer } from 'vite'

function node(type, text = '') {
  return { type, text, props: {}, children: [], parent: null }
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

function findAll(target, predicate) {
  return [...(predicate(target) ? [target] : []), ...(target.children || []).flatMap((child) => findAll(child, predicate))]
}

function visibleText(target) {
  return [target.text, ...(target.children || []).map(visibleText)].join(' ').trim()
}

const stub = (name) => ({
  setup(_props, { attrs, slots }) {
    return () => h(name, attrs, slots.default?.())
  }
})

const ownSkill = { slug: 'reporter', name: '周报助手', category: 'office_tools', source_scope: 'personal', owner_uid: 'alice' }
const shareRecords = [
  { id: 'share-rejected', owner_uid: 'alice', personal_slug: 'reporter', status: 'rejected', created_at: '2026-10-10T08:00:00Z', review_note: '请补充说明' },
  { id: 'share-approved', owner_uid: 'alice', personal_slug: 'reporter', status: 'approved', created_at: '2026-10-02T08:00:00Z', published_version: 2, published_copy_status: 'enabled', published_slug: 'reporter-shared', read_scope: { access_level: 'department', department_ids: [3], user_uids: [] } },
  { id: 'other-owner', owner_uid: 'bob', personal_slug: 'reporter', status: 'pending', created_at: '2026-10-14T08:00:00Z' }
]
const marketRecords = [
  { id: 'market-pending', owner_uid: 'alice', personal_slug: 'reporter', status: 'pending', created_at: '2026-10-12T08:00:00Z', existing_market_scope: 'global', current_market_version: 4 },
  { id: 'market-approved', owner_uid: 'alice', personal_slug: 'reporter', status: 'approved', created_at: '2026-10-01T08:00:00Z', published_version: 4, read_scope: { access_level: 'global', department_ids: [], user_uids: [] } },
  { id: 'other-skill', owner_uid: 'alice', personal_slug: 'another', status: 'rejected', created_at: '2026-10-14T08:00:00Z' }
]

async function withPanel({ role = 'user', shares = shareRecords, market = marketRecords, skill = ownSkill, historyFailure = false } = {}, verify) {
  const componentDir = new URL('../../src/components/extensions/', import.meta.url)
  const compiledPath = fileURLToPath(new URL(`.personal-skill-publish-${pid}.mjs`, componentDir))
  const originalFetch = globalThis.fetch
  const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  const posted = []
  const requested = []
  const published = []
  let app, server
  try {
    const { descriptor } = parse(readFileSync(new URL('PersonalSkillPublish.vue', componentDir), 'utf8'))
    writeFileSync(compiledPath, compileScript(descriptor, { id: 'personal-skill-publish', inlineTemplate: true }).content)
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      value: { getItem: () => null, setItem() {}, removeItem() {} }
    })
    globalThis.fetch = async (url, options = {}) => {
      const path = String(url)
      requested.push(path)
      if (options.method === 'POST') {
        posted.push({ path, body: JSON.parse(options.body) })
        return Response.json({ data: { id: 'new-request', status: 'pending' } })
      }
      if (path.startsWith('/api/skills/share-requests?') || path.startsWith('/api/skill-market/requests?')) {
        if (historyFailure && path.startsWith('/api/skill-market/requests?')) return Response.json({ detail: '状态服务不可用' }, { status: 503 })
        const params = new URL(path, 'http://yuxi.local').searchParams
        const source = path.startsWith('/api/skill-market') ? market : shares
        const filtered = source.filter((item) => item.personal_slug === params.get('slug') &&
          (params.get('mine_only') !== 'true' || item.owner_uid === 'alice') &&
          (!params.get('status') || item.status === params.get('status')))
          .sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at))
        const offset = Number(params.get('offset') || 0)
        const limit = Number(params.get('limit') || 20)
        return Response.json({ data: { items: filtered.slice(offset, offset + limit), total: filtered.length } })
      }
      if (path === '/api/skills/personal/reporter/tree') return Response.json({ data: [{ name: 'SKILL.md', path: 'SKILL.md', is_dir: false }] })
      if (path === '/api/skills/personal/reporter/file?path=SKILL.md') return Response.json({ data: { content: '# 当前源码' } })
      if (path === '/api/skills/share-requests/share-rejected/tree') return Response.json({ data: [{ name: 'SKILL.md', path: 'SKILL.md', is_dir: false }] })
      if (path === '/api/skills/share-requests/share-rejected/snapshot?path=SKILL.md') return Response.json({ data: { content: '# 被驳回时的内容' } })
      if (path === '/api/skills/share-requests/market-pending/tree') return Response.json({ data: [{ name: 'SKILL.md', path: 'SKILL.md', is_dir: false }] })
      if (path === '/api/skills/share-requests/market-pending/snapshot?path=SKILL.md') return Response.json({ data: { content: '# 市场待审内容' } })
      if (path === '/api/auth/users/access-options') return Response.json([])
      return Response.json({ data: [] })
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
    const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
    const user = useUserStore()
    user.token = 'test-token'
    user.uid = 'alice'
    user.userRole = role
    user.departmentId = 3
    user.departmentName = '设计部'
    const { default: Panel } = await server.ssrLoadModule(`/src/components/extensions/.personal-skill-publish-${pid}.mjs`)
    app = renderer.createApp({ render: () => h(Panel, { skill, open: true, onPublished: (value) => published.push(value) }) })
    app.use(pinia)
    app.provide(ssrContextKey, { modules: new Set() })
    for (const name of ['a-button', 'a-select', 'a-textarea', 'a-spin', 'a-alert']) app.component(name, stub(name))
    app.component('a-modal', {
      props: { open: Boolean, title: String },
      setup: (props, { attrs, slots }) => () => props.open ? h('a-modal', { ...attrs, title: props.title }, slots.default?.()) : null
    })
    const host = node('root')
    app.mount(host)
    await setImmediate()
    await nextTick()
    await verify({ host, posted, requested, published, tick: async () => { await setImmediate(); await nextTick() } })
  } finally {
    app?.unmount()
    await server?.close()
    if (existsSync(compiledPath)) unlinkSync(compiledPath)
    globalThis.fetch = originalFetch
    if (originalStorage) Object.defineProperty(globalThis, 'localStorage', originalStorage)
    else delete globalThis.localStorage
  }
}

test('打开后按作者和 slug 汇总两个渠道，同时保留最近申请与已发布版本', async () => {
  await withPanel({}, async ({ host, requested, tick }) => {
    assert.ok(requested.some((path) => path.startsWith('/api/skills/share-requests?') && path.includes('slug=reporter') && path.includes('limit=1')))
    assert.ok(requested.some((path) => path.startsWith('/api/skill-market/requests?') && path.includes('slug=reporter') && path.includes('limit=1')))
    const shared = findAll(host, (item) => item.props['data-channel'] === 'shared')[0]
    const market = findAll(host, (item) => item.props['data-channel'] === 'market')[0]
    assert.match(visibleText(shared), /最新申请：已驳回/, requested.join(', '))
    assert.match(visibleText(shared), /最近发布 v2/)
    assert.match(visibleText(shared), /审核结果：已通过/)
    assert.match(visibleText(shared), /当前共享副本：已启用/)
    assert.doesNotMatch(visibleText(shared), /当前已发布/)
    assert.match(visibleText(shared), /设计部/)
    assert.doesNotMatch(visibleText(shared), /other-owner/)
    assert.match(visibleText(market), /最新申请：待审核/)
    assert.match(visibleText(market), /当前已上架 v4/)
    assert.match(visibleText(market), /全公司/)
    assert.doesNotMatch(visibleText(market), /当前发布版本无法确认/)

    const snapshot = findAll(shared, (item) => item.type === 'button' && item.props['data-request-id'] === 'share-rejected')[0]
    assert.ok(snapshot)
    snapshot.props.onClick()
    await tick()
    assert.match(visibleText(host), /被驳回时的内容/)
  })
})

test('发布弹窗在较矮视口内保留底部空间并允许主体滚动', async () => {
  await withPanel({}, async ({ host }) => {
    const modal = findAll(host, (item) => item.type === 'a-modal')[0]
    assert.equal(modal.props.style?.top, '16px')
    assert.equal(modal.props.style?.paddingBottom, '16px')
    assert.equal(modal.props['body-style']?.maxHeight, 'calc(100dvh - 112px)')
    assert.equal(modal.props['body-style']?.overflowY, 'auto')
  })
})

test('技能文件预览可获得键盘焦点以继续向下阅读', async () => {
  await withPanel({}, async ({ host }) => {
    const content = findAll(host, (item) => item.type === 'pre' && item.props.class === 'publish-file-content')[0]
    assert.equal(content.props.tabindex, '0')
  })
})

test('共享审核要求分类和原因，并提交本人当前技能的渠道申请', async () => {
  await withPanel({ shares: [] }, async ({ host, posted, published, tick }) => {
    const category = findAll(host, (item) => item.type === 'a-select' && item.props['aria-label'] === '技能方向')[0]
    const reason = findAll(host, (item) => item.type === 'a-textarea' && item.props['aria-label'] === '发布说明')[0]
    const submit = findAll(host, (item) => item.type === 'button' && visibleText(item) === '提交共享审核')[0]
    assert.equal(submit.props.disabled, true)
    assert.match(visibleText(host), /当前源码/)
    category.props['onUpdate:value']('data_analysis')
    reason.props['onUpdate:value']('供团队分析报表')
    await nextTick()
    submit.props.onClick()
    await tick()
    assert.deepEqual(posted, [{
      path: '/api/skills/personal/reporter/share-request',
      body: { application_reason: '供团队分析报表', category: 'data_analysis' }
    }])
    assert.equal(published.length, 1)
    assert.equal(published[0].channel, 'shared')
  })
})

test('市场审核走独立渠道，已有共享申请不阻止市场提交', async () => {
  await withPanel({ market: [] }, async ({ host, posted, requested, tick }) => {
    const marketChoice = findAll(host, (item) => item.type === 'button' && item.props['data-channel-choice'] === 'market')[0]
    marketChoice.props.onClick()
    await nextTick()
    const reason = findAll(host, (item) => item.type === 'a-textarea' && item.props['aria-label'] === '发布说明')[0]
    reason.props['onUpdate:value']('让成员按需安装')
    await nextTick()
    const submit = findAll(host, (item) => item.type === 'button' && visibleText(item) === '提交市场审核')[0]
    assert.equal(submit.props.disabled, false, requested.join(', '))
    submit.props.onClick()
    await tick()
    assert.deepEqual(posted, [{
      path: '/api/skill-market/personal/reporter/submit',
      body: { application_reason: '让成员按需安装', category: 'office_tools' }
    }])
  })
})

test('详情页本人个人技能缺少 owner_uid 时仍可发布，显式其他作者必须拒绝', async () => {
  await withPanel({ skill: { slug: 'reporter', name: '周报助手', category: 'office_tools', source_scope: 'personal' }, shares: [], market: [] }, async ({ host }) => {
    assert.ok(findAll(host, (item) => item.type === 'button' && item.props['data-channel-choice'] === 'shared').length)
    assert.match(visibleText(host), /当前源码/)
  })
  await withPanel({ skill: { ...ownSkill, owner_uid: 'bob', source_scope: 'personal' } }, async ({ host, requested }) => {
    assert.match(visibleText(host), /只能发布自己的个人技能/)
    assert.equal(requested.length, 0)
  })
  await withPanel({ skill: { slug: 'reporter', name: '周报助手', category: 'office_tools', sourceScope: 'personal' }, shares: [], market: [] }, async ({ host }) => {
    assert.ok(findAll(host, (item) => item.type === 'button' && item.props['data-channel-choice'] === 'shared').length)
  })
})

test('任一渠道历史读取失败时不允许在未知待审状态下提交', async () => {
  await withPanel({ historyFailure: true }, async ({ host, posted }) => {
    const reason = findAll(host, (item) => item.type === 'a-textarea' && item.props['aria-label'] === '发布说明')[0]
    reason.props['onUpdate:value']('申请发布供团队使用')
    await nextTick()
    const submit = findAll(host, (item) => item.type === 'button' && visibleText(item) === '提交共享审核')[0]
    assert.equal(submit.props.disabled, true)
    submit.props.onClick()
    assert.deepEqual(posted, [])
    assert.match(visibleText(host), /发布状态读取失败/)
  })
})

test('管理员只对自己的个人技能直接共享，并带上方向、原因与目标范围', async () => {
  await withPanel({ role: 'admin', shares: [], market: [] }, async ({ host, posted, tick }) => {
    const reason = findAll(host, (item) => item.type === 'a-textarea' && item.props['aria-label'] === '发布说明')[0]
    reason.props['onUpdate:value']('给设计部使用')
    await nextTick()
    const submit = findAll(host, (item) => item.type === 'button' && visibleText(item) === '确认直接共享')[0]
    assert.equal(submit.props.disabled, false)
    submit.props.onClick()
    await tick()
    assert.deepEqual(posted.filter((item) => item.path.endsWith('/publish')), [{
      path: '/api/skills/personal-catalog/alice/reporter/publish',
      body: {
        read_scope: { access_level: 'department', department_ids: [3], user_uids: [] },
        note: '给设计部使用',
        category: 'office_tools'
      }
    }], visibleText(host))
  })
})

test('共享副本删除或替换后保留历史版本并显示当前失效状态', async () => {
  await withPanel({
    shares: [{ ...shareRecords[1], published_copy_status: 'missing' }],
    market: [{ ...marketRecords[1], current_market_version: null }]
  }, async ({ host }) => {
    const shared = findAll(host, (item) => item.props['data-channel'] === 'shared')[0]
    const market = findAll(host, (item) => item.props['data-channel'] === 'market')[0]
    assert.match(visibleText(shared), /最近发布 v2/)
    assert.match(visibleText(shared), /当前共享副本：已删除或被替换/)
    assert.match(visibleText(market), /曾通过审核，当前发布版本无法确认/)
    assert.doesNotMatch(visibleText(shared), /当前已发布/)
  })
})

test('旧共享记录没有可核验副本身份时保留发布历史但不推断当前状态', async () => {
  await withPanel({ shares: [{ ...shareRecords[1], published_copy_status: null, read_scope: null, department_ids: [] }], market: [] }, async ({ host }) => {
    const shared = findAll(host, (item) => item.props['data-channel'] === 'shared')[0]
    assert.match(visibleText(shared), /最近发布 v2/)
    assert.match(visibleText(shared), /审核结果：已通过/)
    assert.match(visibleText(shared), /当前共享副本：无法核对/)
    assert.match(visibleText(shared), /这条历史记录没有保存对应的共享副本信息，因此无法判断它现在是否可用/)
    assert.match(visibleText(shared), /审核发布时范围：范围未确认/)
    assert.doesNotMatch(visibleText(shared), /当前共享副本：已启用/)
  })
})

test('作者看到市场已下架状态，并区分当次审核与后来调整的范围', async () => {
  await withPanel({ market: [
    { id: 'market-latest', owner_uid: 'alice', personal_slug: 'reporter', status: 'rejected', created_at: '2026-10-12T08:00:00Z', current_market_version: 4, existing_market_is_published: false, existing_market_scope: 'global' },
    { id: 'market-approved', owner_uid: 'alice', personal_slug: 'reporter', status: 'approved', published_version: 4, created_at: '2026-10-01T08:00:00Z', read_scope: { access_level: 'department', department_ids: [3] } }
  ] }, async ({ host }) => {
    const market = findAll(host, (item) => item.props['data-channel'] === 'market')[0]
    assert.match(visibleText(market), /已下架/)
    assert.match(visibleText(market), /最近审核范围：设计部/)
    assert.match(visibleText(market), /当前范围：全公司/)
    assert.doesNotMatch(visibleText(market), /当前已上架/)
  })
})
