import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import { setImmediate } from 'node:timers'
import { ref, watch, nextTick } from 'vue'

// 执行页面实际加载逻辑，用可控返回顺序验证最终显示的数据。
function dashboardLoader() {
  const source = readFileSync(new URL('../../src/views/DashboardView.vue', import.meta.url), 'utf8')
  const state = {
    dataScope: ref('all'),
    overviewError: ref(''),
    overviewLoaded: ref(false),
    overviewActivated: ref(true),
    basicStats: ref({}),
    allStatsData: ref({}),
    loading: ref(false)
  }
  const requests = []
  const notices = []
  const request = (kind) => (scope) =>
    new Promise((resolve, reject) => requests.push({ kind, scope, resolve, reject }))
  const actions = runInNewContext(
    `let latestOverviewRequest = 0;\n${source.slice(source.indexOf('const loadAllStats ='), source.indexOf('// 切换 Tab 处理'))}\n;({ loadAllStats })`,
    {
      ...state,
      watch,
      console: { error() {} },
      dashboardApi: { getAllStats: request('all'), getStats: request('basic') },
      message: { warning: (text) => notices.push(text), error: (text) => notices.push(text) }
    }
  )
  return { ...state, ...actions, requests, notices }
}

test('快速切换后，迟到的历史结果不能覆盖当前范围或提前结束加载', async () => {
  const state = dashboardLoader()
  const first = state.loadAllStats()
  state.dataScope.value = 'current'
  await nextTick()
  assert.deepEqual(
    state.requests.map(({ scope }) => scope),
    ['all', 'current']
  )
  assert.equal(Object.keys(state.basicStats.value).length, 0)
  state.requests[0].resolve({ basic: { total_messages: 100 } })
  await first
  assert.equal(state.loading.value, true)
  assert.equal(state.basicStats.value.total_messages, undefined)
  state.requests[1].resolve({ basic: { total_messages: 3 }, users: { total_users: 1 } })
  await new Promise(setImmediate)
  assert.equal(state.basicStats.value.total_messages, 3)
  assert.equal(state.loading.value, false)
})

test('切换前的降级请求不能回写；新范围失败可以按相同范围重试', async () => {
  const state = dashboardLoader()
  const first = state.loadAllStats()
  state.requests[0].reject(new Error('overview unavailable'))
  await new Promise(setImmediate)
  assert.equal(state.requests[1].kind, 'basic')
  assert.equal(state.requests[1].scope, 'all')
  state.dataScope.value = 'current'
  await nextTick()
  state.requests[1].resolve({ total_messages: 100 })
  await first
  assert.equal(state.basicStats.value.total_messages, undefined)
  assert.equal(state.notices.length, 0)
  state.requests[2].reject(new Error('overview unavailable'))
  await new Promise(setImmediate)
  assert.equal(state.requests[3].scope, 'current')
  state.requests[3].reject(new Error('basic unavailable'))
  await new Promise(setImmediate)
  assert.equal(state.overviewLoaded.value, false)
  assert.equal(state.loading.value, false)
  const retry = state.loadAllStats()
  assert.equal(state.requests[4].scope, 'current')
  state.requests[4].resolve({ basic: { total_messages: 4 } })
  await retry
  assert.equal(state.basicStats.value.total_messages, 4)
})

for (const [component, start, end, action, values, apiName] of [
  [
    'FeedbackModalComponent',
    'let disposed =',
    'const getFeedbackDefaultAvatarSrc',
    'loadFeedbacks',
    () => ({
      loadingFeedbacks: ref(false),
      feedbackFilter: ref('all'),
      feedbacks: ref([]),
      expandedStates: ref(new Map())
    }),
    'getFeedbacks'
  ],
  [
    'ThreadDetailDrawer',
    'let disposed =',
    'const handleClose =',
    'open',
    () => ({
      visible: ref(false),
      loading: ref(false),
      detail: ref(null),
      expandedTools: ref(new Set())
    }),
    'getConversationDetail'
  ]
]) {
  test(`${component} 卸载后的成功或失败都不回写旧弹窗、不弹错误`, async () => {
    const source = readFileSync(
      new URL(`../../src/components/dashboard/${component}.vue`, import.meta.url),
      'utf8'
    )
    for (const fail of [false, true]) {
      let unmount, resolve, reject
      const notices = []
      const state = values()
      const actions = runInNewContext(
        `${source.slice(source.indexOf(start), source.indexOf(end))}\n;({${action}})`,
        {
          ...state,
          props: { scope: 'all' },
          onUnmounted: (callback) => {
            unmount = callback
          },
          dashboardApi: {
            [apiName]: () =>
              new Promise((yes, no) => {
                resolve = yes
                reject = no
              })
          },
          message: { error: (text) => notices.push(text) },
          console: { error() {} }
        }
      )
      const pending = actions[action]('thread-example')
      unmount()
      if (fail) reject(new Error('old scope request'))
      else resolve([{ id: 'old-scope' }])
      await pending
      assert.deepEqual(notices, [])
      if (state.detail) assert.equal(state.detail.value, null)
      if (state.feedbacks) assert.deepEqual(state.feedbacks.value, [])
    }
  })
}

test('实际页面模板按范围重建会话、反馈与调用组件，旧实例状态和迟到结果不可见', async () => {
  const Vue = await import('vue')
  const source = readFileSync(new URL('../../src/views/DashboardView.vue', import.meta.url), 'utf8')
  const names = ['ThreadStatsComponent', 'FeedbackModalComponent', 'CallStatsComponent']
  const template =
    '<div>' +
    names.map((name) => source.match(new RegExp(`<${name}\\b[^>]*?/>`))[0]).join('') +
    '</div>'
  const render = Vue.compile(template)
  const instances = Object.fromEntries(names.map((name) => [name, []]))
  const components = Object.fromEntries(
    names.map((name) => [
      name,
      {
        props: ['scope', 'loading'],
        setup(props) {
          const state = Vue.reactive({
            status: 'all',
            search: '',
            page: 1,
            dialogOpen: false,
            result: ''
          })
          instances[name].push(state)
          return () => Vue.h('item', JSON.stringify({ scope: props.scope, ...state }))
        }
      }
    ])
  )
  const renderer = Vue.createRenderer({
    createElement: (tag) => ({ tag, children: [], text: '' }),
    createText: (text) => ({ text }),
    createComment: () => ({ text: '' }),
    setElementText: (node, text) => {
      node.text = text
    },
    setText: (node, text) => {
      node.text = text
    },
    patchProp() {},
    parentNode: (node) => node.parent,
    nextSibling: () => null,
    insert(node, parent, anchor) {
      node.parent = parent
      const index = parent.children.indexOf(anchor)
      if (index < 0) parent.children.push(node)
      else parent.children.splice(index, 0, node)
    },
    remove(node) {
      const siblings = node.parent.children
      siblings.splice(siblings.indexOf(node), 1)
    }
  })
  const dataScope = ref('all')
  const app = renderer.createApp({
    components,
    setup: () => ({ dataScope, loading: false }),
    render
  })
  const host = { children: [] }
  app.mount(host)
  for (const name of names)
    Object.assign(instances[name][0], {
      status: 'deleted',
      search: '历史',
      page: 3,
      dialogOpen: true
    })
  await nextTick()
  dataScope.value = 'current'
  await nextTick()
  for (const name of names) {
    assert.equal(instances[name].length, 2, `${name} 必须重建以清除旧范围状态`)
    assert.equal(instances[name][1].status, 'all')
    assert.equal(instances[name][1].search, '')
    assert.equal(instances[name][1].page, 1)
    assert.equal(instances[name][1].dialogOpen, false)
    instances[name][0].result = '迟到的历史数据'
  }
  await nextTick()
  for (const node of host.children[0].children) {
    assert.equal(JSON.parse(node.text).scope, 'current')
    assert.equal(JSON.parse(node.text).result, '')
  }
  app.unmount()
})
