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
  setText(target, value) {
    target.text = value
  },
  setElementText(target, value) {
    target.text = value
    target.children = []
  },
  parentNode: (target) => target.parent,
  nextSibling(target) {
    const siblings = target.parent?.children || []
    return siblings[siblings.indexOf(target) + 1] || null
  },
  patchProp(target, key, _old, value) {
    target.props[key] = value
  }
})

const findAll = (target, predicate) => [
  ...(predicate(target) ? [target] : []),
  ...(target.children || []).flatMap((child) => findAll(child, predicate))
]
const visibleText = (target) =>
  [target.text, ...(target.children || []).map(visibleText)].join(' ').trim()
const tick = async () => {
  await setImmediate()
  await nextTick()
}
const result = (page, query) => ({
  message: 'success',
  data: {
    run_id: 'run_12345678',
    status: 'completed',
    items: [
      {
        item_index: (page - 1) * 20,
        query,
        gold_answer: '标准答案',
        generated_answer: '生成答案',
        metrics: { 'recall@10': 1, score: 1 },
        retrieved_chunks: []
      }
    ],
    pagination: { current_page: page, page_size: 20, total: 41 }
  }
})

test('删除基准后的运行可查看、翻页和重试；关闭后迟到结果不会污染下次打开', async () => {
  const componentDir = new URL('../../src/components/evaluation/', import.meta.url)
  const compiledPath = fileURLToPath(new URL(`.orphan-run-workspace-${pid}.mjs`, componentDir))
  const stubPath = fileURLToPath(new URL(`.orphan-run-stub-${pid}.mjs`, componentDir))
  const originalFetch = globalThis.fetch
  const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  const requests = []
  let resolveLatePage
  let pageTwoAttempts = 0
  let app, server
  try {
    const { descriptor } = parse(
      readFileSync(new URL('KnowledgeEvaluationWorkspace.vue', componentDir), 'utf8')
    )
    writeFileSync(
      compiledPath,
      compileScript(descriptor, { id: 'orphan-run-workspace', inlineTemplate: true }).content
    )
    writeFileSync(
      stubPath,
      "import { h } from 'vue'\nexport const useTaskerStore = () => ({ loadTasks() {} })\nexport default { setup() { return () => h('stub') } }\n"
    )
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      value: { getItem: () => null, setItem() {}, removeItem() {} }
    })
    globalThis.fetch = async (url) => {
      const path = String(url)
      requests.push(path)
      if (path.startsWith('/api/evaluation/databases/kb-1/datasets?')) {
        return Response.json({ message: 'success', data: [], pagination: { total: 0 } })
      }
      if (path.startsWith('/api/evaluation/databases/kb-1/runs?')) {
        return Response.json({
          message: 'success',
          data: [
            {
              run_id: 'run_12345678',
              dataset_id: null,
              name: '历史评估',
              status: 'completed',
              started_at: '2026-01-01T00:00:00Z',
              completed_at: '2026-01-01T00:01:00Z',
              total_items: 41,
              completed_items: 41,
              overall_score: 1,
              metrics: {},
              retrieval_config: {}
            }
          ],
          pagination: { total: 1 }
        })
      }
      if (path.includes('/runs/run_12345678?')) {
        const page = Number(new URL(path, 'http://yuxi.local').searchParams.get('page'))
        if (page === 2 && ++pageTwoAttempts === 1) {
          return Response.json({ detail: '暂时不可用' }, { status: 503 })
        }
        if (page === 3)
          return new Promise((resolve) => {
            resolveLatePage = resolve
          })
        return Response.json(result(page, page === 1 ? '首页问题' : '次页问题'))
      }
      throw new Error(`unexpected request: ${path}`)
    }
    server = await createServer({
      configFile: false,
      plugins: [vue()],
      resolve: {
        alias: [
          ...[
            '@/components/modals/BenchmarkUploadModal.vue',
            '@/components/modals/BenchmarkGenerateModal.vue',
            '@/components/evaluation/EvaluationRunCreateModal.vue',
            '@/components/shared/ResourceEmptyState.vue',
            '@/stores/tasker'
          ].map((find) => ({ find, replacement: stubPath })),
          { find: '@', replacement: fileURLToPath(new URL('../../src', import.meta.url)) }
        ]
      },
      server: { middlewareMode: true, hmr: false },
      appType: 'custom',
      optimizeDeps: { noDiscovery: true }
    })
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/knowledge/:kbId', component: { render: () => h('div') } }]
    })
    await router.push('/knowledge/kb-1')
    const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
    const user = useUserStore()
    user.token = 'test-token'
    user.userRole = 'admin'
    const { default: Workspace } = await server.ssrLoadModule(
      `/src/components/evaluation/.orphan-run-workspace-${pid}.mjs`
    )
    app = renderer.createApp(Workspace, { kbId: 'kb-1' })
    app.use(pinia)
    app.use(router)
    app.provide(ssrContextKey, { modules: new Set() })
    for (const name of [
      'a-button',
      'a-skeleton',
      'a-dropdown',
      'a-menu',
      'a-menu-item',
      'a-popconfirm',
      'a-progress'
    ]) {
      app.component(name, {
        setup(_props, { attrs, slots }) {
          return () => h(name, attrs, slots.default?.())
        }
      })
    }
    app.component('a-modal', {
      props: ['open', 'title'],
      setup(props, { attrs, slots }) {
        return () =>
          props.open
            ? h('a-modal', { title: props.title, ...attrs }, [slots.default?.(), slots.footer?.()])
            : null
      }
    })
    app.component('a-table', {
      props: ['dataSource'],
      setup(props) {
        return () =>
          h(
            'a-table',
            { rows: props.dataSource },
            props.dataSource?.map((row) => h('row', row.query))
          )
      }
    })
    app.component('a-pagination', {
      props: ['current', 'pageSize', 'total'],
      setup(props, { attrs }) {
        return () => h('a-pagination', { ...props, ...attrs })
      }
    })
    const host = node('root')
    app.mount(host)
    await tick()

    const runButton = findAll(
      host,
      (item) => item.type === 'button' && visibleText(item).includes('历史评估')
    )[0]
    assert.ok(runButton, 'orphan run should appear in the list')
    runButton.props.onClick()
    await tick()
    let modal = findAll(
      host,
      (item) => item.type === 'a-modal' && item.props.title?.includes('历史评估')
    )[0]
    assert.ok(modal, 'orphan run should open a results modal')
    assert.match(visibleText(modal), /首页问题/)
    assert.ok(
      requests.includes('/api/evaluation/databases/kb-1/runs/run_12345678?page=1&page_size=20')
    )

    let pagination = findAll(modal, (item) => item.type === 'a-pagination')[0]
    pagination.props.onChange(2)
    await tick()
    modal = findAll(
      host,
      (item) => item.type === 'a-modal' && item.props.title?.includes('历史评估')
    )[0]
    assert.ok(findAll(modal, (item) => item.props.role === 'alert' && visibleText(item))[0])
    const retry = findAll(
      modal,
      (item) => item.type === 'a-button' && visibleText(item).includes('重试')
    )[0]
    assert.ok(retry, 'failed page should offer retry')
    retry.props.onClick()
    await tick()
    modal = findAll(
      host,
      (item) => item.type === 'a-modal' && item.props.title?.includes('历史评估')
    )[0]
    assert.match(visibleText(modal), /次页问题/)
    assert.equal(requests.filter((path) => path.includes('/runs/run_12345678?page=2&')).length, 2)

    pagination = findAll(modal, (item) => item.type === 'a-pagination')[0]
    pagination.props.onChange(3)
    await nextTick()
    modal.props.onCancel()
    await tick()
    assert.equal(
      findAll(host, (item) => item.type === 'a-modal' && item.props.title?.includes('历史评估'))
        .length,
      0
    )
    resolveLatePage(Response.json(result(3, '迟到问题')))
    await tick()
    runButton.props.onClick()
    await tick()
    modal = findAll(
      host,
      (item) => item.type === 'a-modal' && item.props.title?.includes('历史评估')
    )[0]
    assert.match(visibleText(modal), /首页问题/)
    assert.doesNotMatch(visibleText(modal), /迟到问题/)
  } finally {
    app?.unmount()
    await server?.close()
    if (existsSync(compiledPath)) unlinkSync(compiledPath)
    if (existsSync(stubPath)) unlinkSync(stubPath)
    globalThis.fetch = originalFetch
    if (originalStorage) Object.defineProperty(globalThis, 'localStorage', originalStorage)
    else delete globalThis.localStorage
  }
})
