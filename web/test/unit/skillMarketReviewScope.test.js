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
  return [target.text, ...(target.children || []).map(visibleText)].join(' ').trim()
}

const stub = (name) => ({
  setup(_props, { attrs, slots }) {
    return () => h(name, attrs, [attrs.message, slots.default?.()])
  }
})

async function withReview({ role, departmentId = 3, existingScope = null, existingDepartmentId = null, ownerDepartmentId = 3, status = 'pending', readScope = null, responseDepartmentId = null }, verify) {
  const componentDir = new URL('../../src/components/extensions/', import.meta.url)
  const compiledPath = fileURLToPath(new URL(`.skill-market-review-scope-${pid}.mjs`, componentDir))
  const originalFetch = globalThis.fetch
  const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  const originalMessages = Object.fromEntries(['success', 'info', 'error'].map((name) => [name, message[name]]))
  const request = {
    id: 'release-2',
    owner_uid: 'publisher',
    personal_slug: 'local-pptx',
    name: 'PPTX',
    application_reason: '发布新版',
    category: 'other',
    status,
    owner_department_id: ownerDepartmentId,
    existing_market_scope: existingScope,
    existing_market_department_id: existingDepartmentId,
    review_note: null,
    read_scope: readScope
  }
  const posted = []
  const messages = []
  let app, server
  try {
    const { descriptor } = parse(readFileSync(new URL('SkillMarket.vue', componentDir), 'utf8'))
    writeFileSync(compiledPath, compileScript(descriptor, { id: 'skill-market-review-scope', inlineTemplate: true }).content)
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      value: { getItem: () => null, setItem() {}, removeItem() {} }
    })
    for (const name of Object.keys(originalMessages)) message[name] = (value) => messages.push({ name, value })
    globalThis.fetch = async (url, options = {}) => {
      const path = String(url)
      if (options.method === 'POST' && path.includes('/requests/release-2/')) {
        posted.push({ path, body: JSON.parse(options.body) })
        return Response.json({ data: {
          owner_department_id: ownerDepartmentId,
          read_scope: { access_level: 'department', department_ids: [responseDepartmentId ?? existingDepartmentId ?? ownerDepartmentId] }
        } })
      }
      if (path === '/api/skill-market/requests') return Response.json({ data: [request] })
      if (path === '/api/departments') return Response.json({ departments: [{ id: 3, name: '设计部' }, { id: 4, name: '研发部' }] })
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
    const router = createRouter({ history: createMemoryHistory(), routes: [] })
    const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
    const user = useUserStore()
    user.token = 'test-token'
    user.uid = 'reviewer'
    user.userRole = role
    user.departmentId = departmentId
    user.departmentName = '设计部'
    const { default: SkillMarket } = await server.ssrLoadModule(`/src/components/extensions/.skill-market-review-scope-${pid}.mjs`)
    app = renderer.createApp(SkillMarket)
    app.use(pinia)
    app.use(router)
    app.provide(ssrContextKey, { modules: new Set() })
    for (const name of ['a-button', 'a-spin', 'a-input', 'a-input-search', 'a-select', 'a-alert', 'a-empty', 'a-tag', 'a-textarea', 'a-radio', 'a-radio-group']) {
      app.component(name, stub(name))
    }
    for (const name of ['a-modal', 'a-drawer']) {
      app.component(name, {
        props: { open: Boolean, title: String },
        setup: (props, { attrs, slots }) => () => props.open ? h(name, { ...attrs, title: props.title }, slots.default?.()) : null
      })
    }
    const host = node('root')
    app.mount(host)
    await setImmediate()
    await nextTick()

    const button = (label) => findAll(host, (item) => item.type === 'a-button' && visibleText(item) === label)[0]
    const reviewModal = () => findAll(host, (item) => item.type === 'a-modal' && ['批准发布', '拒绝申请'].includes(item.props.title))[0]
    const scopeGroup = () => findAll(host, (item) => item.type === 'a-radio-group' && item.props.class === 'market-review-scope')[0]
    const reviewAlert = () => findAll(host, (item) => item.type === 'a-alert' && visibleText(item).includes('新版本'))[0]

    const reviewEntry = findAll(host, (item) => item.type === 'a-button' && visibleText(item).includes('市场审核'))[0]
    assert.ok(reviewEntry, '个人首页应能打开市场审核')
    reviewEntry.props.onClick()
    await setImmediate()
    await nextTick()

    await verify({ host, button, reviewModal, scopeGroup, reviewAlert, posted, messages, tick: nextTick })
  } finally {
    app?.unmount()
    await server?.close()
    if (existsSync(compiledPath)) unlinkSync(compiledPath)
    globalThis.fetch = originalFetch
    for (const [name, original] of Object.entries(originalMessages)) message[name] = original
    if (originalStorage) Object.defineProperty(globalThis, 'localStorage', originalStorage)
    else delete globalThis.localStorage
  }
}

test('全公司已发布技能的新版本默认沿用原范围并提交全公司审核', async () => {
  await withReview({ role: 'superadmin', existingScope: 'global' }, async ({ button, reviewModal, scopeGroup, reviewAlert, posted, tick }) => {
    button('批准').props.onClick()
    await tick()
    assert.equal(scopeGroup().props.value, 'global')
    assert.equal(scopeGroup().props.disabled, true)
    assert.match(visibleText(reviewAlert()), /新版本.*全公司.*不可更改/)
    scopeGroup().props['onUpdate:value']('department')
    reviewModal().props.onOk()
    await setImmediate()
    assert.equal(posted.length, 1)
    assert.equal(posted[0].path, '/api/skill-market/requests/release-2/approve')
    assert.equal(posted[0].body.scope, 'global')
  })
})

test('部门管理员不能批准现有全公司技能的新版本，但仍可拒绝', async () => {
  await withReview({ role: 'admin', existingScope: 'global' }, async ({ host, button, reviewModal, posted, tick }) => {
    assert.equal(button('批准').props.disabled, true)
    assert.match(visibleText(host), /只有超级管理员可以批准新版本/)
    assert.equal(button('拒绝').props.disabled, undefined)
    button('拒绝').props.onClick()
    await tick()
    reviewModal().props.onOk()
    await setImmediate()
    assert.equal(posted.length, 1)
    assert.equal(posted[0].path, '/api/skill-market/requests/release-2/reject')
  })
})

test('跨部门续版仅超级管理员可批准，部门管理员仍可拒绝', async () => {
  await withReview({ role: 'admin', existingScope: 'department', existingDepartmentId: 4 }, async ({ host, button, reviewModal, posted, tick }) => {
    assert.equal(button('批准').props.disabled, true)
    assert.match(visibleText(host), /仅超级管理员可以批准新版本/)
    button('拒绝').props.onClick()
    await tick()
    reviewModal().props.onOk()
    await setImmediate()
    assert.equal(posted.length, 1)
    assert.equal(posted[0].path, '/api/skill-market/requests/release-2/reject')
  })
})

test('跨部门续版沿用原发布部门显示和提交范围', async () => {
  await withReview({ role: 'superadmin', existingScope: 'department', existingDepartmentId: 4 }, async ({ button, reviewModal, scopeGroup, reviewAlert, posted, tick }) => {
    assert.notEqual(button('批准').props.disabled, true)
    button('批准').props.onClick()
    await tick()
    assert.equal(scopeGroup().props.value, 'department')
    assert.equal(scopeGroup().props.disabled, true)
    assert.match(visibleText(scopeGroup()), /原发布部门（研发部）/)
    assert.match(visibleText(reviewAlert()), /新版本.*研发部及超级管理员可见.*不可更改/)
    reviewModal().props.onOk()
    await setImmediate()
    assert.equal(posted.length, 1)
    assert.equal(posted[0].body.scope, 'department')
  })
})

test('已发布申请按实际发布范围显示部门', async () => {
  await withReview({ role: 'superadmin', existingScope: 'department', existingDepartmentId: 4, status: 'approved', readScope: { access_level: 'department', department_ids: [4] } }, async ({ host }) => {
    assert.match(visibleText(host), /研发部及超级管理员可见/)
    assert.doesNotMatch(visibleText(host), /设计部及超级管理员可见/)
  })
})

test('发布结果提示按实际受众判断当前管理员是否可见', async () => {
  await withReview({ role: 'admin', responseDepartmentId: 4 }, async ({ button, reviewModal, messages, tick }) => {
    button('批准').props.onClick()
    await tick()
    reviewModal().props.onOk()
    await setImmediate()
    assert.deepEqual(messages.filter((item) => item.name === 'info').map((item) => item.value), [
      '已发布到研发部；当前账号不在该部门，市场目录不会展示此技能'
    ])
  })
})

test('首次发布仍可由超级管理员选择全公司范围', async () => {
  await withReview({ role: 'superadmin' }, async ({ button, reviewModal, scopeGroup, posted, tick }) => {
    button('批准').props.onClick()
    await tick()
    assert.equal(scopeGroup().props.value, 'department')
    assert.notEqual(scopeGroup().props.disabled, true)
    scopeGroup().props['onUpdate:value']('global')
    await tick()
    reviewModal().props.onOk()
    await setImmediate()
    assert.equal(posted.length, 1)
    assert.equal(posted[0].path, '/api/skill-market/requests/release-2/approve')
    assert.equal(posted[0].body.scope, 'global')
  })
})
