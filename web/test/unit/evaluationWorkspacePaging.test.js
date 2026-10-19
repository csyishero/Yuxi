import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import { ref } from 'vue'

const source = readFileSync(
  new URL('../../src/components/evaluation/KnowledgeEvaluationWorkspace.vue', import.meta.url),
  'utf8'
)
const code = source.slice(source.indexOf('const loadOverview ='), source.indexOf('const handleBenchmarkCreateAction ='))

function workspace(datasetRequest, runRequest) {
  const state = {
    loading: ref(false),
    errorMessage: ref(''),
    datasets: ref([]),
    runs: ref([]),
    datasetPage: ref(1),
    runPage: ref(1),
    datasetTotal: ref(0),
    runTotal: ref(0),
    datasetPageSize: 20,
    runPageSize: 20
  }
  const actions = runInNewContext(
    `let latestOverviewRequest = 0; let overviewInFlight = false; let disposed = false;\n${code}\n;({ loadOverview, changeDatasetPage, changeRunPage })`,
    {
      ...state,
      props: { kbId: 'kb-1' },
      evaluationApi: { listDatasets: datasetRequest, listRuns: runRequest },
      syncRefreshTimer: () => {},
      console: { error: () => {} }
    }
  )
  return { ...state, ...actions }
}

test('静默轮询不抢占翻页请求，列表加载态能正常结束', async () => {
  const pending = []
  const request = () => new Promise((resolve) => pending.push(resolve))
  const state = workspace(request, request)
  const page = state.loadOverview()
  await state.loadOverview(true)
  assert.equal(pending.length, 2)
  pending[0]({ message: 'success', data: [{ dataset_id: 'ds' }], pagination: { total: 1 } })
  pending[1]({ message: 'success', data: [], pagination: { total: 0 } })
  await page
  assert.equal(state.loading.value, false)
  assert.equal(state.datasets.value[0].dataset_id, 'ds')
})

test('删除记录后末页为空时自动退回有效页', async () => {
  const offsets = []
  const state = workspace(
    async (_kb, page) => {
      offsets.push(page.offset)
      return {
        message: 'success',
        data: page.offset === 0 ? [{ dataset_id: 'ds' }] : [],
        pagination: { total: 20 }
      }
    },
    async () => ({ message: 'success', data: [], pagination: { total: 0 } })
  )
  state.datasetPage.value = 2
  await state.loadOverview()
  assert.deepEqual(offsets, [20, 0])
  assert.equal(state.datasetPage.value, 1)
  assert.equal(state.datasets.value[0].dataset_id, 'ds')
  assert.equal(state.loading.value, false)
})
