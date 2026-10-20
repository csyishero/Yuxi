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
  if (target.type === 'comment') return ''
  return [target.text, ...(target.children || []).map(visibleText)].join(' ').trim()
}

function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

const scriptContent = '#!/usr/bin/env python3\n  print("<resource>")\n'

const stub = (name) => ({
  setup(_props, { attrs, slots }) {
    return () => h(name, attrs, [attrs.message, slots.default?.()])
  }
})

async function withPreview(view, verify) {
  const componentDir = new URL('../../src/components/extensions/', import.meta.url)
  const compiledPath = fileURLToPath(new URL(`.skill-market-preview-race-${pid}.mjs`, componentDir))
  const previewStubPath = fileURLToPath(new URL(`.skill-market-preview-stub-${pid}.mjs`, componentDir))
  const listStubPath = fileURLToPath(new URL(`.skill-market-list-stub-${pid}.mjs`, componentDir))
  const originalFetch = globalThis.fetch
  const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  const originalMessageError = message.error
  const pending = {
    snapshot: { 'A.md': deferred(), 'B.md': deferred() },
    market: { 'A.md': deferred(), 'B.md': deferred() }
  }
  const files = ['SKILL.md', 'A.md', 'B.md', 'scripts/run.py', 'scripts/bad.py'].map((path) => ({ path, is_dir: false, previewable: true }))
  files.push({ path: 'diagram.png', is_dir: false, previewable: false })
  const requestedFiles = []
  const marketSkill = {
    slug: 'example', name: 'Example', owner_uid: 'publisher', owner_name: 'Publisher', description: 'Example skill',
    category: 'other', scope: 'global', latest_version: 1, install_count: 0, likes_count: 0,
    tool_dependencies: [], mcp_dependencies: [], skill_dependencies: []
  }
  const request = { id: 'release-2', name: 'Example', owner_uid: 'publisher', personal_slug: 'example', status: 'pending', category: 'other' }
  let app, server
  try {
    const { descriptor } = parse(readFileSync(new URL('SkillMarket.vue', componentDir), 'utf8'))
    writeFileSync(compiledPath, compileScript(descriptor, { id: 'skill-market-preview-race', inlineTemplate: true }).content)
    writeFileSync(previewStubPath, "import { h } from 'vue'\nexport default { props: ['content'], setup(props) { return () => h('preview-text', props.content) } }\n")
    writeFileSync(listStubPath, "export default { setup() { return () => null } }\n")
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      value: { getItem: () => null, setItem() {}, removeItem() {} }
    })
    message.error = () => {}
    globalThis.fetch = async (url) => {
      const path = String(url)
      if (path.startsWith('/api/skill-market/requests?')) return Response.json({ data: { items: [request], total: 1 } })
      if (path === '/api/skills/share-requests/release-2/tree') return Response.json({ data: files })
      if (path.startsWith('/api/skills/share-requests/release-2/snapshot?')) {
        const file = new URL(path, 'http://yuxi.local').searchParams.get('path')
        requestedFiles.push(file)
        if (file === 'scripts/run.py') return Response.json({ data: { content: scriptContent } })
        return pending.snapshot[file]?.promise || Response.json({ data: { content: '# Introduction' } })
      }
      if (path.startsWith('/api/skill-market?')) return Response.json({ data: [marketSkill] })
      if (path === '/api/skill-market/example') return Response.json({ data: marketSkill })
      if (path === '/api/skill-market/example/tree') return Response.json({ data: files })
      if (path.startsWith('/api/skill-market/example/file?')) {
        const file = new URL(path, 'http://yuxi.local').searchParams.get('path')
        requestedFiles.push(file)
        if (file === 'scripts/bad.py') return Response.json({ detail: '仅支持预览 UTF-8 文本文件' }, { status: 400 })
        if (file === 'scripts/run.py') return Response.json({ data: { content: scriptContent } })
        return pending.market[file]?.promise || Response.json({ data: { content: '# Introduction' } })
      }
      return Response.json({ data: [] })
    }

    server = await createServer({
      configFile: false,
      plugins: [vue()],
      resolve: { alias: [
        { find: '@/components/common/MarkdownPreview.vue', replacement: previewStubPath },
        { find: '@/components/extensions/SkillCardList.vue', replacement: listStubPath },
        { find: '@', replacement: fileURLToPath(new URL('../../src', import.meta.url)) }
      ] },
      server: { middlewareMode: true, hmr: false },
      appType: 'custom',
      optimizeDeps: { noDiscovery: true }
    })
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/extensions', component: { render: () => h('div') } }] })
    await router.push({ path: '/extensions', query: view === 'market' ? { view: 'market' } : {} })
    const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
    const user = useUserStore()
    user.token = 'test-token'
    user.uid = 'reviewer'
    user.userRole = 'admin'
    user.departmentId = 3
    const { default: SkillMarket } = await server.ssrLoadModule(`/src/components/extensions/.skill-market-preview-race-${pid}.mjs`)
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
        setup: (props, { slots }) => () => props.open ? h(name, { title: props.title }, slots.default?.()) : null
      })
    }
    const host = node('root')
    app.mount(host)
    const tick = async () => { await setImmediate(); await nextTick() }
    await tick()
    await verify({ host, pending, requestedFiles, tick })
  } finally {
    app?.unmount()
    await server?.close()
    if (existsSync(compiledPath)) unlinkSync(compiledPath)
    if (existsSync(previewStubPath)) unlinkSync(previewStubPath)
    if (existsSync(listStubPath)) unlinkSync(listStubPath)
    globalThis.fetch = originalFetch
    message.error = originalMessageError
    if (originalStorage) Object.defineProperty(globalThis, 'localStorage', originalStorage)
    else delete globalThis.localStorage
  }
}

function modal(host, title) {
  return findAll(host, (item) => item.type === 'a-modal' && item.props.title === title)[0]
}

function select(modalNode, path) {
  const button = findAll(modalNode, (item) => item.type === 'button' && visibleText(item) === path)[0]
  assert.ok(button, `${path} should be available in the preview`)
  button.props.onClick()
}

function previewContent(modalNode) {
  return findAll(modalNode, (item) => item.type === 'preview-text').map(visibleText)
}

test('approval snapshot keeps B visible when A finishes after B', async () => {
  await withPreview('personal', async ({ host, pending, tick }) => {
    const reviewEntry = findAll(host, (item) => item.type === 'a-button' && visibleText(item).includes('市场审核'))[0]
    reviewEntry.props.onClick()
    await tick()
    const previewEntry = findAll(modal(host, '市场审核申请'), (item) => item.type === 'a-button' && visibleText(item) === '查看提交快照')[0]
    previewEntry.props.onClick()
    await tick()
    assert.deepEqual(previewContent(modal(host, '提交快照')), ['# Introduction'])

    select(modal(host, '提交快照'), 'A.md')
    select(modal(host, '提交快照'), 'B.md')
    await nextTick()
    assert.deepEqual(previewContent(modal(host, '提交快照')), [], '新选择加载期间不能显示前一文件')
    pending.snapshot['B.md'].resolve(Response.json({ data: { content: '# B snapshot' } }))
    await tick()
    pending.snapshot['A.md'].resolve(Response.json({ data: { content: '# A snapshot' } }))
    await tick()

    const snapshot = modal(host, '提交快照')
    assert.match(String(findAll(snapshot, (item) => item.type === 'button' && visibleText(item) === 'B.md')[0].props.class), /active/)
    assert.deepEqual(previewContent(snapshot), ['# B snapshot'])
  })
})

test('market detail keeps B visible when A finishes after B', async () => {
  await withPreview('market', async ({ host, pending, tick }) => {
    const skill = findAll(host, (item) => item.type === 'button' && item.props.class === 'market-card-title')[0]
    assert.ok(skill)
    skill.props.onClick()
    await tick()
    assert.deepEqual(previewContent(modal(host, 'Skill 详情')), ['# Introduction'])

    select(modal(host, 'Skill 详情'), 'A.md')
    select(modal(host, 'Skill 详情'), 'B.md')
    await nextTick()
    assert.deepEqual(previewContent(modal(host, 'Skill 详情')), [], '新选择加载期间不能显示前一文件')
    pending.market['B.md'].resolve(Response.json({ data: { content: '# B detail' } }))
    await tick()
    pending.market['A.md'].resolve(Response.json({ data: { content: '# A detail' } }))
    await tick()

    const detail = modal(host, 'Skill 详情')
    assert.match(String(findAll(detail, (item) => item.type === 'button' && visibleText(item) === 'B.md')[0].props.class), /active/)
    assert.deepEqual(previewContent(detail), ['# B detail'])
  })
})

test('market script preview shows source text instead of parsing it as Markdown', async () => {
  await withPreview('market', async ({ host, tick }) => {
    const skill = findAll(host, (item) => item.type === 'button' && item.props.class === 'market-card-title')[0]
    skill.props.onClick()
    await tick()

    const detail = modal(host, 'Skill 详情')
    assert.deepEqual(previewContent(detail), ['# Introduction'])
    select(detail, 'scripts/run.py')
    await tick()

    assert.deepEqual(previewContent(detail), [])
    const code = findAll(detail, (item) => item.type === 'code')[0]
    assert.ok(code)
    assert.equal(code.text, scriptContent)
    assert.equal(findAll(detail, (item) => item.type === 'resource').length, 0)
  })
})

test('approval snapshot shows scripts as unmodified text', async () => {
  await withPreview('personal', async ({ host, tick }) => {
    const reviewEntry = findAll(host, (item) => item.type === 'a-button' && visibleText(item).includes('市场审核'))[0]
    reviewEntry.props.onClick()
    await tick()
    const previewEntry = findAll(modal(host, '市场审核申请'), (item) => item.type === 'a-button' && visibleText(item) === '查看提交快照')[0]
    previewEntry.props.onClick()
    await tick()

    const snapshot = modal(host, '提交快照')
    assert.deepEqual(previewContent(snapshot), ['# Introduction'])
    select(snapshot, 'scripts/run.py')
    await tick()

    assert.deepEqual(previewContent(snapshot), [])
    const code = findAll(snapshot, (item) => item.type === 'code')[0]
    assert.ok(code)
    assert.equal(code.text, scriptContent)
    assert.equal(findAll(snapshot, (item) => item.type === 'resource').length, 0)
  })
})

test('market detail explains binary attachments without requesting their text preview', async () => {
  await withPreview('market', async ({ host, requestedFiles, tick }) => {
    findAll(host, (item) => item.type === 'button' && item.props.class === 'market-card-title')[0].props.onClick()
    await tick()

    const detail = modal(host, 'Skill 详情')
    const attachment = findAll(detail, (item) => item.type === 'button' && visibleText(item).startsWith('diagram.png'))[0]
    assert.match(visibleText(attachment), /不可预览/)
    attachment.props.onClick()
    await tick()

    assert.equal(findAll(detail, (item) => item.type === 'a-empty')[0].props.description, '此文件不支持在线预览')
    assert.deepEqual(requestedFiles, ['SKILL.md'])
  })
})

test('approval snapshot explains binary attachments without requesting their text preview', async () => {
  await withPreview('personal', async ({ host, requestedFiles, tick }) => {
    findAll(host, (item) => item.type === 'a-button' && visibleText(item).includes('市场审核'))[0].props.onClick()
    await tick()
    findAll(modal(host, '市场审核申请'), (item) => item.type === 'a-button' && visibleText(item) === '查看提交快照')[0].props.onClick()
    await tick()

    const snapshot = modal(host, '提交快照')
    const attachment = findAll(snapshot, (item) => item.type === 'button' && visibleText(item).startsWith('diagram.png'))[0]
    assert.match(visibleText(attachment), /不可预览/)
    attachment.props.onClick()
    await tick()

    assert.equal(findAll(snapshot, (item) => item.type === 'a-empty')[0].props.description, '此文件不支持在线预览，请下载完整快照检查')
    assert.deepEqual(requestedFiles, ['SKILL.md'])
  })
})

test('market detail keeps an unsupported encoded script error inside the preview', async () => {
  await withPreview('market', async ({ host, requestedFiles, tick }) => {
    findAll(host, (item) => item.type === 'button' && item.props.class === 'market-card-title')[0].props.onClick()
    await tick()

    const detail = modal(host, 'Skill 详情')
    select(detail, 'scripts/bad.py')
    await tick()

    assert.equal(findAll(detail, (item) => item.type === 'a-empty')[0].props.description, '无法在线预览此文件')
    assert.deepEqual(requestedFiles, ['SKILL.md', 'scripts/bad.py'])
  })
})
