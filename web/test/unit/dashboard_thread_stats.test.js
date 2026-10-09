import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'

const storageValues = new Map()
globalThis.localStorage = {
  getItem: (key) => storageValues.get(key) ?? null,
  setItem: (key, value) => storageValues.set(key, String(value)),
  removeItem: (key) => storageValues.delete(key),
  clear: () => storageValues.clear()
}

function jsonResponse(payload) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  })
}

async function withServer(run) {
  const server = await createServer({
    server: { middlewareMode: true },
    appType: 'custom',
    ssr: { noExternal: ['ant-design-vue'] },
    plugins: [
      {
        name: 'test-message-api',
        enforce: 'pre',
        resolveId(id) {
          return id === 'ant-design-vue' ? '\0test-message-api' : null
        },
        load(id) {
          if (id !== '\0test-message-api') return null
          return 'export const message = { error() {}, success() {}, warning() {} }'
        }
      }
    ]
  })

  try {
    await run(server)
  } finally {
    await server.close()
  }
}

async function prepareStores(server) {
  setActivePinia(createPinia())
  const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
  const userStore = useUserStore()
  userStore.token = 'dashboard-test-token'
  userStore.userId = 1
  userStore.userRole = 'superadmin'
  return userStore
}

test('dashboardApi.getThreadStats 正确拼接时间范围与智能体过滤参数', async () => {
  await withServer(async (server) => {
    storageValues.clear()
    const requests = []
    globalThis.fetch = async (input) => {
      requests.push(String(input))
      return jsonResponse({
        summary: { total_threads: 10, active_threads: 5 },
        daily_trends: [],
        depth_distribution: {},
        agent_distribution: [],
        top_users: [],
        status_distribution: {}
      })
    }

    await prepareStores(server)
    const { dashboardApi } = await server.ssrLoadModule('/src/apis/dashboard_api.js')

    const res1 = await dashboardApi.getThreadStats({ timeRange: '14days' })
    assert.equal(requests[0], '/api/dashboard/stats/threads?time_range=14days')
    assert.equal(res1.summary.total_threads, 10)

    const res2 = await dashboardApi.getThreadStats({ timeRange: '30days', agentId: 'agent-coder' })
    assert.equal(requests[1], '/api/dashboard/stats/threads?time_range=30days&agent_id=agent-coder')
    assert.equal(res2.summary.active_threads, 5)

    await dashboardApi.getThreadStats({ timeRange: '90days', includeSubagents: true })
    assert.equal(
      requests[2],
      '/api/dashboard/stats/threads?time_range=90days&include_subagents=true'
    )
  })
})

test('dashboardApi.getAllStats 始终请求知识库统计', async () => {
  await withServer(async (server) => {
    storageValues.clear()
    const requests = []
    const responses = {
      '/api/dashboard/stats': { total_conversations: 3 },
      '/api/dashboard/stats/users': { total_users: 2 },
      '/api/dashboard/stats/tools': { total_calls: 4 },
      '/api/dashboard/stats/agents': { total_agents: 1 },
      '/api/dashboard/stats/knowledge': { total_databases: 5 }
    }
    globalThis.fetch = async (input) => {
      const url = String(input)
      requests.push(url)
      return jsonResponse(responses[url])
    }

    await prepareStores(server)
    const { dashboardApi } = await server.ssrLoadModule('/src/apis/dashboard_api.js')
    const result = await dashboardApi.getAllStats()

    assert.deepEqual(requests, Object.keys(responses))
    assert.deepEqual(result.knowledge, responses['/api/dashboard/stats/knowledge'])
  })
})

test('会话分析保持紧凑摘要、彩色排行、无刷新 loading 与统一头像 fallback', () => {
  const source = readFileSync(
    new URL('../../src/components/dashboard/ThreadStatsComponent.vue', import.meta.url),
    'utf8'
  )
  const refreshButton = source.match(/<button[^>]*class="refresh-btn"[\s\S]*?<\/button>/)?.[0]
  const summaryStart = source.indexOf('<DashboardMetricGrid class="thread-summary-grid">')
  const summaryEnd = source.indexOf('<!-- 2x2 可视化图表区域 -->')
  const summarySource = source.slice(summaryStart, summaryEnd)
  const agentChartStart = source.indexOf('const renderAgentChart')
  const agentChartEnd = source.indexOf('const handleResize')
  const agentChartSource = source.slice(agentChartStart, agentChartEnd)

  assert.ok(refreshButton)
  assert.equal(refreshButton.includes(':loading'), false)
  assert.match(source, /:default-src="generatePixelAvatar\(record\.agent_id\)"/)
  assert.match(source, /:default-src="generatePixelAvatar\(record\.uid\)"/)
  assert.match(source, /role="switch"/)
  assert.match(source, /:aria-checked="includeSubagents"/)
  assert.match(source, /includeSubagents \? '包含' : '不含'/)
  assert.equal(summarySource.includes('#meta'), false)
  assert.equal((summarySource.match(/<DashboardMetricCard/g) || []).length, 4)
  assert.equal(summarySource.includes('Token'), false)
  assert.match(agentChartSource, /color:\s*\(params\)\s*=>\s*getColorByIndex\(params\.dataIndex\)/)
})

test('智能体分析不再渲染 TOP 5 排行且保留分布图', () => {
  const source = readFileSync(
    new URL('../../src/components/dashboard/AgentStatsComponent.vue', import.meta.url),
    'utf8'
  )

  assert.match(source, /对话\/工具调用分布 \(TOP 3\)/)
  assert.doesNotMatch(source, /表现最佳智能体|top_performing_agents|topPerformers|performerColumns/)
  assert.doesNotMatch(source, /<a-table/)
})

test('会话统计源码包含筛选请求代次和 loading 回写守卫', () => {
  const source = readFileSync(
    new URL('../../src/components/dashboard/ThreadStatsComponent.vue', import.meta.url),
    'utf8'
  )
  const statsLoader = source.slice(
    source.indexOf('const loadData'),
    source.indexOf('const toggleSubagents')
  )
  const conversationLoader = source.slice(
    source.indexOf('const loadConversations'),
    source.indexOf('const resetFilters')
  )

  assert.match(statsLoader, /const requestId = \+\+latestStatsRequest/)
  assert.match(statsLoader, /includeSubagents: requestedIncludeSubagents/)
  assert.match(statsLoader, /if \(requestId !== latestStatsRequest\) return/)
  assert.ok(
    statsLoader.indexOf('includeSubagents.value = requestedIncludeSubagents') >
      statsLoader.indexOf('await dashboardApi.getThreadStats')
  )
  assert.match(statsLoader, /if \(requestId === latestStatsRequest\) loading\.value = false/)
  assert.match(conversationLoader, /const requestId = \+\+latestConversationRequest/)
  assert.match(conversationLoader, /if \(requestId !== latestConversationRequest\) return/)
  assert.match(
    conversationLoader,
    /if \(requestId === latestConversationRequest\) tableLoading\.value = false/
  )
})

test('formatStorageSize 将容量限制为四位有效数字并分离单位', async () => {
  await withServer(async (server) => {
    const { formatStorageSize } = await server.ssrLoadModule('/src/utils/dashboard.js')

    assert.deepEqual(formatStorageSize(518.2 * 1024), { value: '518.2', unit: 'KB' })
    assert.deepEqual(formatStorageSize(12.345 * 1024 ** 3), { value: '12.35', unit: 'GB' })
    assert.deepEqual(formatStorageSize(10 * 1024 ** 5), { value: '10', unit: 'PB' })
    assert.deepEqual(formatStorageSize(0), { value: '0', unit: 'B' })
  })
})

test('buildHeatmapMonthSegments 忽略拥挤的残月并保留完整月份', async () => {
  await withServer(async (server) => {
    const { buildHeatmapMonthSegments } = await server.ssrLoadModule('/src/utils/dashboard.js')
    const weeks = [
      [{ date: '2026-04-27' }],
      [{ date: '2026-05-04' }],
      [{ date: '2026-05-11' }],
      [{ date: '2026-06-01' }],
      [{ date: '2026-06-08' }]
    ]

    assert.deepEqual(buildHeatmapMonthSegments(weeks), [
      { key: '5月-1', label: '5月', start: 1, span: 2 },
      { key: '6月-3', label: '6月', start: 3, span: 2 }
    ])
  })
})

test('dashboardApi.getConversationFilterOptions 请求会话审计筛选项', async () => {
  await withServer(async (server) => {
    storageValues.clear()
    const requests = []
    globalThis.fetch = async (input) => {
      requests.push(String(input))
      return jsonResponse({ users: [], agents: [] })
    }

    await prepareStores(server)
    const { dashboardApi } = await server.ssrLoadModule('/src/apis/dashboard_api.js')
    const result = await dashboardApi.getConversationFilterOptions()

    assert.equal(requests[0], '/api/dashboard/conversations/options')
    assert.deepEqual(result, { users: [], agents: [] })
  })
})

test('dashboardApi.getConversations 正确拼接 search 搜索关键词与分页参数', async () => {
  await withServer(async (server) => {
    storageValues.clear()
    const requests = []
    globalThis.fetch = async (input) => {
      requests.push(String(input))
      return jsonResponse({
        items: [
          {
            thread_id: 'thread-123',
            title: 'Search match',
            status: 'active'
          }
        ],
        total: 41,
        limit: 20,
        offset: 40
      })
    }

    await prepareStores(server)
    const { dashboardApi } = await server.ssrLoadModule('/src/apis/dashboard_api.js')

    const result = await dashboardApi.getConversations({
      status: 'active',
      search: 'search term',
      limit: 20,
      offset: 40
    })

    assert.equal(
      requests[0],
      '/api/dashboard/conversations?status=active&search=search+term&limit=20&offset=40'
    )
    assert.equal(result.total, 41)
    assert.equal(result.items.length, 1)
    assert.equal(result.items[0].thread_id, 'thread-123')
  })
})

test('会话图表与明细 API 共享筛选并显式发送不含子会话', async () => {
  await withServer(async (server) => {
    const requests = []
    globalThis.fetch = async (input) => {
      requests.push(new URL(String(input), 'http://localhost'))
      return jsonResponse({})
    }
    await prepareStores(server)
    const { dashboardApi } = await server.ssrLoadModule('/src/apis/dashboard_api.js')
    await dashboardApi.getThreadStats({
      timeRange: '7days',
      agentId: 'removed-agent',
      projectId: 'deleted-project',
      uid: 'deleted-user',
      status: 'deleted',
      search: '报告',
      includeSubagents: false
    })
    await dashboardApi.getConversations({
      time_range: '7days',
      agent_id: 'removed-agent',
      project_id: 'deleted-project',
      uid: 'deleted-user',
      status: 'deleted',
      search: '报告',
      include_subagents: false
    })
    assert.deepEqual(
      Object.fromEntries(requests[0].searchParams),
      Object.fromEntries(requests[1].searchParams)
    )
    assert.equal(requests[0].searchParams.get('include_subagents'), 'false')
  })
})

async function auditLoader() {
  const { runInNewContext } = await import('node:vm')
  const { ref } = await import('vue')
  const source = readFileSync(
    new URL('../../src/components/dashboard/ThreadStatsComponent.vue', import.meta.url),
    'utf8'
  )
  const calls = [],
    pending = []
  const read = (kind) => (params) => {
    calls.push({ kind, params: JSON.parse(JSON.stringify(params)) })
    return new Promise((resolve, reject) => pending.push({ resolve, reject }))
  }
  const state = {
    props: { scope: 'all' },
    includeSubagents: ref(true),
    timeRange: ref('7days'),
    selectedAgentId: ref('agent'),
    selectedUid: ref('user'),
    selectedProjectId: ref('project'),
    selectedStatus: ref('deleted'),
    searchKeyword: ref('报告'),
    appliedSearchKeyword: ref('报告'),
    loading: ref(false),
    tableLoading: ref(false),
    threadData: ref(null),
    conversationList: ref([]),
    tablePagination: ref({ current: 1, pageSize: 10, total: 0 }),
    filterOptions: ref({})
  }
  let unmount
  const notices = []
  const actions = runInNewContext(
    `${source.match(/onUnmounted\(\(\) => \{[\s\S]*?\n\}\)/)[0]};\n${source.slice(source.indexOf('const loadData ='), source.indexOf('const resetFilters ='))}\n({loadData, loadConversations})`,
    {
      ...state,
      onUnmounted: (callback) => {
        unmount = callback
      },
      cleanup() {},
      latestStatsRequest: 0,
      latestConversationRequest: 0,
      nextTick: async () => {},
      renderAllCharts() {},
      trendChart: null,
      depthChart: null,
      agentChart: null,
      dashboardApi: { getThreadStats: read('stats'), getConversations: read('list') },
      message: {
        error(text) {
          notices.push(text)
        }
      },
      console: { error() {} }
    }
  )
  return { ...actions, state, calls, pending, unmount, notices }
}

test('审计快速切换筛选后迟到响应不能覆盖新图表和明细', async () => {
  const loader = await auditLoader()
  const oldStats = loader.loadData(),
    oldList = loader.loadConversations()
  loader.state.selectedProjectId.value = 'new-project'
  const newStats = loader.loadData(),
    newList = loader.loadConversations()
  loader.pending[2].resolve({ summary: { total_threads: 2 } })
  loader.pending[3].resolve({ items: [{ thread_id: 'new' }], total: 2 })
  await Promise.all([newStats, newList])
  loader.pending[0].resolve({ summary: { total_threads: 999 } })
  loader.pending[1].resolve({ items: [{ thread_id: 'old' }], total: 999 })
  await Promise.all([oldStats, oldList])
  assert.equal(loader.state.threadData.value.summary.total_threads, 2)
  assert.equal(loader.state.conversationList.value[0].thread_id, 'new')
  assert.equal(loader.state.tablePagination.value.total, 2)
  assert.equal(loader.calls[2].params.projectId, loader.calls[3].params.project_id)
  assert.equal(loader.calls[2].params.status, 'deleted')
  assert.equal(loader.calls[2].params.timeRange, loader.calls[3].params.time_range)
})

test('审计请求失败清除旧数值，重试空结果保持零条', async () => {
  const loader = await auditLoader()
  loader.state.threadData.value = { summary: { total_threads: 999 } }
  loader.state.conversationList.value = [{ thread_id: 'old' }]
  const stats = loader.loadData(),
    list = loader.loadConversations()
  loader.pending[0].reject(new Error('offline'))
  loader.pending[1].reject(new Error('offline'))
  await Promise.all([stats, list])
  assert.equal(loader.state.threadData.value, null)
  assert.equal(loader.state.conversationList.value.length, 0)
  assert.equal(loader.state.tablePagination.value.total, 0)
  const retry = loader.loadConversations()
  loader.pending[2].resolve({ items: [], total: 0 })
  await retry
  assert.equal(loader.state.tableLoading.value, false)
  assert.equal(loader.state.conversationList.value.length, 0)
})

test('尚未提交的新关键词不会改变分页查询的已应用条件', async () => {
  const loader = await auditLoader()
  loader.state.searchKeyword.value = '未提交关键词'
  loader.state.tablePagination.value.current = 2
  const page = loader.loadConversations()
  assert.equal(loader.calls[0].params.search, '报告')
  assert.equal(loader.calls[0].params.offset, 10)
  loader.pending[0].resolve({ items: [], total: 0 })
  await page
})

test('统计范围贯穿使用量、调用、反馈、列表和详情，知识库存保持当前值', async () => {
  await withServer(async (server) => {
    const requests = []
    globalThis.fetch = async (input) => {
      requests.push(new URL(String(input), 'http://localhost'))
      return jsonResponse({})
    }
    await prepareStores(server)
    const { dashboardApi } = await server.ssrLoadModule('/src/apis/dashboard_api.js')
    await dashboardApi.getAllStats('current')
    await dashboardApi.getCallTimeseries('tokens', '14days', 'current')
    await dashboardApi.getThreadStats({ scope: 'current' })
    await dashboardApi.getConversations({ scope: 'current' })
    await dashboardApi.getConversationDetail('thread-example', 'current')
    await dashboardApi.getConversationFilterOptions('current')
    await dashboardApi.getFeedbacks({ scope: 'current' })
    assert.equal(requests.length, 11)
    for (const url of requests) {
      assert.equal(
        url.searchParams.get('scope'),
        url.pathname.endsWith('/knowledge') ? null : 'current'
      )
    }
  })
})

test('卸载旧范围后，迟到的会话统计和明细错误不污染当前页面', async () => {
  const loader = await auditLoader()
  const stats = loader.loadData(),
    list = loader.loadConversations()
  loader.unmount()
  loader.pending[0].reject(new Error('old scope stats'))
  loader.pending[1].reject(new Error('old scope list'))
  await Promise.all([stats, list])
  assert.deepEqual(loader.notices, [])
})
