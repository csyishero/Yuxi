<template>
  <div class="evaluation-workspace">
    <div class="evaluation-workspace-content">
      <section class="evaluation-section" aria-labelledby="evaluation-benchmarks-title">
        <header class="evaluation-section-header">
          <div class="evaluation-section-heading">
            <div class="evaluation-section-title-row">
              <h3 id="evaluation-benchmarks-title">评估基准</h3>
              <span class="evaluation-section-count">{{ datasetTotal }}</span>
            </div>
            <p>管理评估题目与标准答案，选择基准后可查看题目和关联测试。</p>
          </div>

          <a-dropdown v-if="canManage" :trigger="['click']" placement="bottomRight">
            <button
              type="button"
              class="lucide-icon-btn extension-panel-action extension-panel-action-primary"
            >
              <Plus :size="14" aria-hidden="true" />
              <span>创建基准</span>
              <ChevronDown :size="13" aria-hidden="true" />
            </button>
            <template #overlay>
              <a-menu @click="handleBenchmarkCreateAction">
                <a-menu-item key="upload">
                  <span class="evaluation-menu-item">
                    <Upload :size="15" aria-hidden="true" />
                    <span>上传 JSONL</span>
                  </span>
                </a-menu-item>
                <a-menu-item key="generate">
                  <span class="evaluation-menu-item">
                    <Sparkles :size="15" aria-hidden="true" />
                    <span>自动生成</span>
                  </span>
                </a-menu-item>
              </a-menu>
            </template>
          </a-dropdown>
        </header>

        <div class="evaluation-list-shell">
          <div v-if="loading" class="evaluation-skeleton-list" aria-label="正在加载评估基准">
            <div v-for="index in 3" :key="index" class="evaluation-skeleton-row">
              <a-skeleton
                active
                :paragraph="{ rows: 1, width: ['72%'] }"
                :title="{ width: '38%' }"
              />
            </div>
          </div>

          <div v-else-if="errorMessage" class="evaluation-inline-state" role="alert">
            <CircleAlert :size="18" aria-hidden="true" />
            <div>
              <strong>评估数据加载失败</strong>
              <span>{{ errorMessage }}</span>
            </div>
            <a-button size="small" @click="loadOverview()">重试</a-button>
          </div>

          <ResourceEmptyState
            v-else-if="datasets.length === 0"
            title="暂无评估基准"
            description="上传现有题集，或从当前知识库自动生成评估问题。"
            :icon="ClipboardList"
            size="compact"
          >
            <template v-if="canManage" #actions>
              <a-button type="primary" @click="uploadModalOpen = true">上传基准</a-button>
              <a-button @click="generateModalOpen = true">自动生成</a-button>
            </template>
          </ResourceEmptyState>

          <div v-else class="evaluation-divider-list">
            <div
              v-for="dataset in datasets"
              :key="dataset.dataset_id"
              class="evaluation-list-row evaluation-benchmark-row"
            >
              <button
                type="button"
                class="evaluation-row-open"
                :disabled="!isDatasetViewable(dataset)"
                @click="openDataset(dataset)"
              >
                <span class="evaluation-row-icon" aria-hidden="true">
                  <ClipboardList :size="17" />
                </span>
                <span class="evaluation-row-main">
                  <span class="evaluation-row-title-line">
                    <strong :title="dataset.name">{{ dataset.name }}</strong>
                    <span
                      v-if="getDatasetStatus(dataset) !== 'completed'"
                      class="evaluation-status-tag"
                      :class="`status-${getDatasetStatus(dataset)}`"
                    >
                      {{ getDatasetStatusText(dataset) }}
                    </span>
                  </span>
                  <span class="evaluation-row-description">
                    {{ dataset.description || '暂无描述' }}
                  </span>
                  <span v-if="isDatasetBuilding(dataset)" class="evaluation-row-progress">
                    <a-progress
                      :percent="getDatasetProgress(dataset)"
                      :show-info="false"
                      size="small"
                      status="active"
                    />
                    <span>{{ getDatasetBuildMessage(dataset) }}</span>
                  </span>
                </span>
                <span class="evaluation-row-side">
                  <span class="evaluation-row-capabilities" aria-label="基准能力">
                    <span v-if="dataset.has_gold_chunks">Gold Chunks</span>
                    <span v-if="dataset.has_gold_answers">Gold Answer</span>
                    <span v-if="!dataset.has_gold_chunks && !dataset.has_gold_answers">仅查询</span>
                  </span>
                  <span class="evaluation-row-meta">
                    <span>{{ dataset.item_count || 0 }} 题</span>
                    <span>{{ formatTime(dataset.created_at) }}</span>
                  </span>
                </span>
                <ChevronRight class="evaluation-row-chevron" :size="17" aria-hidden="true" />
              </button>
              <span class="evaluation-row-actions">
                <a-dropdown :trigger="['click']" placement="bottomRight">
                  <button
                    type="button"
                    class="evaluation-icon-button"
                    :aria-label="`${dataset.name} 的更多操作`"
                  >
                    <MoreHorizontal :size="16" aria-hidden="true" />
                  </button>
                  <template #overlay>
                    <a-menu @click="({ key }) => handleDatasetAction(key, dataset)">
                      <a-menu-item
                        v-if="getDatasetStatus(dataset) === 'failed'"
                        key="resume"
                        :disabled="!canManage"
                      >
                        <span class="evaluation-menu-item">
                          <RotateCcw :size="14" aria-hidden="true" />
                          <span>继续生成</span>
                        </span>
                      </a-menu-item>
                      <a-menu-item
                        key="download"
                        :disabled="getDatasetStatus(dataset) !== 'completed'"
                      >
                        <span class="evaluation-menu-item">
                          <Download :size="14" aria-hidden="true" />
                          <span>下载</span>
                        </span>
                      </a-menu-item>
                      <a-menu-item
                        key="delete"
                        danger
                        :disabled="!canManage || isDatasetBuilding(dataset)"
                      >
                        <span class="evaluation-menu-item">
                          <Trash2 :size="14" aria-hidden="true" />
                          <span>删除</span>
                        </span>
                      </a-menu-item>
                    </a-menu>
                  </template>
                </a-dropdown>
              </span>
            </div>
          </div>
          <div v-if="datasetTotal > datasetPageSize" class="evaluation-list-pagination">
            <a-pagination
              :current="datasetPage"
              :page-size="datasetPageSize"
              :total="datasetTotal"
              size="small"
              @change="changeDatasetPage"
            />
          </div>
        </div>
      </section>

      <section class="evaluation-section" aria-labelledby="recent-evaluations-title">
        <header class="evaluation-section-header">
          <div class="evaluation-section-heading">
            <div class="evaluation-section-title-row">
              <h3 id="recent-evaluations-title">近期评估</h3>
              <span class="evaluation-section-count">{{ runTotal }}</span>
            </div>
            <p>查看最近运行的 RAG 测试，完成后可进入对应基准检查逐题结果。</p>
          </div>

          <button
            v-if="canManage"
            type="button"
            class="lucide-icon-btn extension-panel-action extension-panel-action-primary"
            @click="runModalOpen = true"
          >
            <Plus :size="14" aria-hidden="true" />
            <span>新建评估</span>
          </button>
        </header>

        <div class="evaluation-list-shell">
          <div v-if="loading" class="evaluation-skeleton-list" aria-label="正在加载近期评估">
            <div v-for="index in 3" :key="index" class="evaluation-skeleton-row">
              <a-skeleton
                active
                :paragraph="{ rows: 1, width: ['64%'] }"
                :title="{ width: '32%' }"
              />
            </div>
          </div>

          <ResourceEmptyState
            v-else-if="runs.length === 0"
            title="暂无评估记录"
            description="选择一个已完成的评估基准开始首次测试。"
            :icon="BarChart3"
            size="compact"
          >
            <template v-if="canManage" #actions>
              <a-button type="primary" @click="runModalOpen = true">新建评估</a-button>
            </template>
          </ResourceEmptyState>

          <div v-else class="evaluation-divider-list">
            <div
              v-for="run in recentRuns"
              :key="run.run_id"
              class="evaluation-list-row evaluation-run-row"
            >
              <button type="button" class="evaluation-row-open" @click="openRun(run)">
                <span class="evaluation-row-icon" :class="`run-${run.status}`" aria-hidden="true">
                  <LoaderCircle
                    v-if="run.status === 'running'"
                    :size="17"
                    class="evaluation-spin"
                  />
                  <CircleCheck v-else-if="run.status === 'completed'" :size="17" />
                  <CircleX v-else-if="run.status === 'failed'" :size="17" />
                  <BarChart3 v-else :size="17" />
                </span>
                <span class="evaluation-row-main">
                  <span class="evaluation-row-title-line">
                    <strong :title="getRunName(run)">{{ getRunName(run) }}</strong>
                    <span class="evaluation-status-tag" :class="`status-${run.status}`">
                      {{ getRunStatusText(run.status) }}
                    </span>
                  </span>
                  <span class="evaluation-row-description">
                    {{
                      datasetById[run.dataset_id]?.name ||
                      (run.dataset_id ? `基准 ${run.dataset_id}` : '关联基准已删除')
                    }}
                  </span>
                  <span v-if="run.status === 'running'" class="evaluation-row-progress">
                    <a-progress
                      :percent="getRunProgress(run)"
                      :show-info="false"
                      size="small"
                      status="active"
                    />
                    <span>
                      {{
                        run.message || `已完成 ${run.completed_items || 0}/${run.total_items || 0}`
                      }}
                    </span>
                  </span>
                </span>
                <span class="evaluation-row-side evaluation-run-side">
                  <span class="evaluation-run-metrics">
                    <span>
                      <small>Recall@10</small>
                      <strong>{{ formatMetric(run.metrics?.['recall@10']) }}</strong>
                    </span>
                    <span>
                      <small>综合评分</small>
                      <strong>{{ formatScore(run.overall_score) }}</strong>
                    </span>
                  </span>
                  <span class="evaluation-row-meta">
                    <span>{{ formatRunItems(run) }}</span>
                    <span>{{ formatTime(run.started_at) }}</span>
                  </span>
                </span>
                <ChevronRight class="evaluation-row-chevron" :size="17" aria-hidden="true" />
              </button>
              <span v-if="canManage" class="evaluation-row-actions">
                <a-popconfirm
                  title="删除这条评估记录？"
                  description="删除后无法恢复。"
                  ok-text="删除"
                  cancel-text="取消"
                  @confirm="deleteRun(run)"
                >
                  <button
                    type="button"
                    class="evaluation-icon-button evaluation-icon-button-danger"
                    :aria-label="`删除评估 ${getRunName(run)}`"
                  >
                    <Trash2 :size="15" aria-hidden="true" />
                  </button>
                </a-popconfirm>
              </span>
            </div>
          </div>
          <div v-if="runTotal > runPageSize" class="evaluation-list-pagination">
            <a-pagination
              :current="runPage"
              :page-size="runPageSize"
              :total="runTotal"
              size="small"
              @change="changeRunPage"
            />
          </div>
        </div>
      </section>
    </div>

    <BenchmarkUploadModal
      v-model:visible="uploadModalOpen"
      :kb-id="kbId"
      @success="handleDatasetCreated"
    />
    <BenchmarkGenerateModal
      v-model:visible="generateModalOpen"
      :kb-id="kbId"
      @success="handleDatasetCreated"
    />
    <EvaluationRunCreateModal
      v-model:open="runModalOpen"
      :kb-id="kbId"
      :datasets="completedDatasets"
      @success="handleRunCreated"
    />
    <a-modal
      :open="Boolean(orphanRun)"
      :title="`历史评估结果 · ${getRunName(orphanRun)}`"
      width="960px"
      :footer="null"
      @cancel="closeOrphanRun"
    >
      <div v-if="orphanRun" class="historical-run-results">
        <p class="historical-run-context">
          关联基准已删除，仍可查看运行时保存的逐题结果。共 {{ orphanPagination.total }} 条。
        </p>
        <div v-if="orphanResultsError" class="historical-run-error" role="alert">
          <span>{{ orphanResultsError }}</span>
          <a-button size="small" @click="loadOrphanResults(orphanPagination.current)"
            >重试</a-button
          >
        </div>
        <a-table
          v-else
          :columns="historicalResultColumns"
          :data-source="orphanResults"
          :pagination="false"
          :loading="orphanResultsLoading"
          :row-key="(record) => record.item_index"
          :scroll="{ x: 880 }"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'query'">
              <div class="historical-run-cell">{{ record.query || '-' }}</div>
            </template>
            <template v-else-if="column.key === 'gold_answer'">
              <div class="historical-run-cell">{{ record.gold_answer || '-' }}</div>
            </template>
            <template v-else-if="column.key === 'generated_answer'">
              <div class="historical-run-cell">{{ record.generated_answer || '-' }}</div>
            </template>
            <template v-else-if="column.key === 'metrics'">
              <div class="historical-run-metrics">
                <span>R@10 {{ formatMetric(record.metrics?.['recall@10']) }}</span>
                <span v-if="Number.isFinite(record.metrics?.score)">
                  答案评分 {{ formatMetric(record.metrics.score) }}
                </span>
                <span v-if="record.metrics?.reasoning" class="historical-run-cell">
                  {{ record.metrics.reasoning }}
                </span>
              </div>
            </template>
          </template>
          <template #emptyText>暂无逐题结果</template>
        </a-table>
        <a-pagination
          v-if="orphanPagination.total > orphanPagination.pageSize"
          class="historical-run-pagination"
          :current="orphanPagination.current"
          :page-size="orphanPagination.pageSize"
          :total="orphanPagination.total"
          size="small"
          @change="loadOrphanResults"
        />
      </div>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { useRouter } from 'vue-router'
import {
  BarChart3,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  CircleX,
  ClipboardList,
  Download,
  LoaderCircle,
  MoreHorizontal,
  Plus,
  RotateCcw,
  Sparkles,
  Trash2,
  Upload
} from '@lucide/vue'
import { evaluationApi } from '@/apis/knowledge_api'
import { useTaskerStore } from '@/stores/tasker'
import ResourceEmptyState from '@/components/shared/ResourceEmptyState.vue'
import BenchmarkUploadModal from '@/components/modals/BenchmarkUploadModal.vue'
import BenchmarkGenerateModal from '@/components/modals/BenchmarkGenerateModal.vue'
import EvaluationRunCreateModal from '@/components/evaluation/EvaluationRunCreateModal.vue'
import { parseDownloadFilename } from '@/utils/file_utils'

const props = defineProps({
  kbId: { type: String, required: true },
  canManage: { type: Boolean, default: false }
})

const router = useRouter()
const taskerStore = useTaskerStore()
const loading = ref(true)
const errorMessage = ref('')
const datasets = ref([])
const runs = ref([])
const datasetPage = ref(1)
const runPage = ref(1)
const datasetPageSize = 20
const runPageSize = 20
const datasetTotal = ref(0)
const runTotal = ref(0)
const uploadModalOpen = ref(false)
const generateModalOpen = ref(false)
const runModalOpen = ref(false)
const orphanRun = ref(null)
const orphanResults = ref([])
const orphanResultsLoading = ref(false)
const orphanResultsError = ref('')
const orphanPagination = reactive({ current: 1, pageSize: 20, total: 0 })
const historicalResultColumns = [
  { title: '问题', key: 'query', width: 250 },
  { title: '标准答案', key: 'gold_answer', width: 210 },
  { title: '生成答案', key: 'generated_answer', width: 210 },
  { title: '指标', key: 'metrics', width: 210 }
]
const downloading = reactive({})
let refreshTimer = null
let latestOverviewRequest = 0
let overviewInFlight = false
let latestOrphanRequest = 0
let disposed = false

const completedDatasets = computed(() =>
  datasets.value.filter((dataset) => getDatasetStatus(dataset) === 'completed')
)
const datasetById = computed(() =>
  Object.fromEntries(datasets.value.map((dataset) => [dataset.dataset_id, dataset]))
)
const recentRuns = computed(() => runs.value)

const getDatasetStatus = (dataset) => dataset?.build_metadata?.status || 'completed'
const isDatasetBuilding = (dataset) => ['pending', 'running'].includes(getDatasetStatus(dataset))
const isDatasetViewable = (dataset) => ['completed', 'failed'].includes(getDatasetStatus(dataset))

const getDatasetStatusText = (dataset) => {
  const labels = {
    pending: '等待生成',
    running: '生成中',
    completed: '已完成',
    failed: '生成失败'
  }
  return labels[getDatasetStatus(dataset)] || getDatasetStatus(dataset)
}

const getDatasetProgress = (dataset) => {
  const progress = Number(dataset?.build_metadata?.progress || 0)
  return Number.isFinite(progress) ? Math.max(0, Math.min(Math.round(progress), 100)) : 0
}

const getDatasetBuildMessage = (dataset) =>
  dataset?.build_metadata?.error_message ||
  dataset?.build_metadata?.message ||
  getDatasetStatusText(dataset)

const getRunName = (run) => run?.name || run?.run_name || run?.run_id?.slice(0, 8) || '-'
const getRunStatusText = (status) =>
  ({ running: '运行中', completed: '已完成', failed: '失败', paused: '已暂停' })[status] || status

const getRunProgress = (run) => {
  if (Number.isFinite(run?.progress)) return Math.max(0, Math.min(Math.round(run.progress), 100))
  const total = Number(run?.total_items || 0)
  return total ? Math.round((Number(run?.completed_items || 0) / total) * 100) : 0
}

const formatMetric = (value) => (Number.isFinite(value) ? value.toFixed(2) : '-')
const formatScore = (value) => (Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '-')
const formatRunItems = (run) => `${run?.completed_items || 0}/${run?.total_items || 0} 题`
const formatTime = (value) => (value ? new Date(value).toLocaleString('zh-CN') : '-')

const hasActiveWork = () =>
  datasets.value.some(isDatasetBuilding) || runs.value.some((run) => run.status === 'running')

const stopRefreshTimer = () => {
  if (refreshTimer) window.clearInterval(refreshTimer)
  refreshTimer = null
}

const syncRefreshTimer = () => {
  if (disposed) return
  if (!hasActiveWork()) {
    stopRefreshTimer()
    return
  }
  if (!refreshTimer) refreshTimer = window.setInterval(() => loadOverview(true), 3000)
}

const loadOverview = async (silent = false) => {
  if (!props.kbId) return
  if (silent && overviewInFlight) return
  const requestId = ++latestOverviewRequest
  overviewInFlight = true
  if (!silent) {
    loading.value = true
    errorMessage.value = ''
  }

  try {
    const [datasetResponse, runResponse] = await Promise.all([
      evaluationApi.listDatasets(props.kbId, {
        limit: datasetPageSize,
        offset: (datasetPage.value - 1) * datasetPageSize
      }),
      evaluationApi.listRuns(props.kbId, {
        limit: runPageSize,
        offset: (runPage.value - 1) * runPageSize
      })
    ])
    if (disposed || requestId !== latestOverviewRequest) return
    if (datasetResponse?.message !== 'success' || !Array.isArray(datasetResponse.data)) {
      throw new Error('评估基准数据格式错误')
    }
    if (runResponse?.message !== 'success' || !Array.isArray(runResponse.data)) {
      throw new Error('评估记录数据格式错误')
    }
    const nextDatasetTotal = datasetResponse.pagination?.total ?? datasetResponse.data.length
    const nextRunTotal = runResponse.pagination?.total ?? runResponse.data.length
    const validDatasetPage = Math.max(1, Math.ceil(nextDatasetTotal / datasetPageSize))
    const validRunPage = Math.max(1, Math.ceil(nextRunTotal / runPageSize))
    if (datasetPage.value > validDatasetPage || runPage.value > validRunPage) {
      datasetPage.value = Math.min(datasetPage.value, validDatasetPage)
      runPage.value = Math.min(runPage.value, validRunPage)
      await loadOverview(false)
      return
    }
    datasets.value = datasetResponse.data
    runs.value = runResponse.data
    datasetTotal.value = nextDatasetTotal
    runTotal.value = nextRunTotal
    errorMessage.value = ''
  } catch (error) {
    if (disposed || requestId !== latestOverviewRequest) return
    console.error('加载评估工作台失败:', error)
    if (!silent) errorMessage.value = error.message || '请稍后重试'
  } finally {
    if (!disposed && requestId === latestOverviewRequest) {
      overviewInFlight = false
      if (!silent) loading.value = false
      syncRefreshTimer()
    }
  }
}

const changeDatasetPage = async (page) => {
  datasetPage.value = page
  await loadOverview()
}

const changeRunPage = async (page) => {
  runPage.value = page
  await loadOverview()
}

const handleBenchmarkCreateAction = ({ key }) => {
  if (key === 'upload') uploadModalOpen.value = true
  if (key === 'generate') generateModalOpen.value = true
}

const openDataset = (dataset) => {
  if (!isDatasetViewable(dataset)) return
  router.push({
    name: 'ExtensionEvaluationBenchmarkDetail',
    params: { kbId: props.kbId, datasetId: dataset.dataset_id }
  })
}

const openRun = (run) => {
  if (!run.dataset_id) {
    orphanRun.value = run
    orphanResults.value = []
    orphanResultsError.value = ''
    orphanPagination.current = 1
    orphanPagination.total = 0
    loadOrphanResults(1)
    return
  }
  router.push({
    name: 'ExtensionEvaluationBenchmarkDetail',
    params: { kbId: props.kbId, datasetId: run.dataset_id },
    query: { view: 'results', run: run.run_id }
  })
}

const closeOrphanRun = () => {
  latestOrphanRequest += 1
  orphanRun.value = null
  orphanResults.value = []
  orphanResultsError.value = ''
  orphanResultsLoading.value = false
}

const loadOrphanResults = async (page = 1) => {
  const runId = orphanRun.value?.run_id
  if (!runId) return
  const requestId = ++latestOrphanRequest
  orphanPagination.current = page
  orphanResults.value = []
  orphanResultsError.value = ''
  orphanResultsLoading.value = true
  try {
    const response = await evaluationApi.getRunResults(props.kbId, runId, {
      page,
      pageSize: orphanPagination.pageSize
    })
    if (disposed || requestId !== latestOrphanRequest || orphanRun.value?.run_id !== runId) return
    if (response?.message !== 'success' || !Array.isArray(response.data?.items)) {
      throw new Error('逐题结果数据格式错误')
    }
    orphanResults.value = response.data.items
    orphanPagination.current = response.data.pagination?.current_page || page
    orphanPagination.total = response.data.pagination?.total ?? orphanResults.value.length
  } catch (error) {
    if (disposed || requestId !== latestOrphanRequest || orphanRun.value?.run_id !== runId) return
    orphanResultsError.value = error.message || '加载逐题结果失败'
  } finally {
    if (!disposed && requestId === latestOrphanRequest && orphanRun.value?.run_id === runId) {
      orphanResultsLoading.value = false
    }
  }
}

const downloadDataset = async (dataset) => {
  if (downloading[dataset.dataset_id]) return
  downloading[dataset.dataset_id] = true
  try {
    const response = await evaluationApi.downloadDataset(dataset.dataset_id)
    const blob = await response.blob()
    const filename =
      parseDownloadFilename(response.headers.get('content-disposition')) || `${dataset.name}.jsonl`
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(url)
  } catch (error) {
    console.error('下载评估基准失败:', error)
    message.error(error.message || '下载评估基准失败')
  } finally {
    delete downloading[dataset.dataset_id]
  }
}

const deleteDataset = (dataset) => {
  Modal.confirm({
    title: '删除评估基准？',
    content: `“${dataset.name}”及其题目将被删除，此操作无法恢复。`,
    okText: '删除',
    cancelText: '取消',
    okButtonProps: { danger: true },
    onOk: async () => {
      await evaluationApi.deleteDataset(dataset.dataset_id)
      message.success('评估基准已删除')
      if (datasets.value.length === 1 && datasetPage.value > 1) datasetPage.value -= 1
      await loadOverview()
    }
  })
}

const handleDatasetAction = async (key, dataset) => {
  if (key === 'download') return downloadDataset(dataset)
  if (key === 'delete') return deleteDataset(dataset)
  if (key === 'resume') {
    try {
      await evaluationApi.resumeDatasetGeneration(props.kbId, dataset.dataset_id)
      message.success('已继续生成评估基准')
      taskerStore.loadTasks()
      await loadOverview()
    } catch (error) {
      console.error('继续生成评估基准失败:', error)
      message.error(error.message || '继续生成评估基准失败')
    }
  }
}

const deleteRun = async (run) => {
  try {
    await evaluationApi.deleteRun(props.kbId, run.run_id)
    message.success('评估记录已删除')
    if (runs.value.length === 1 && runPage.value > 1) runPage.value -= 1
    await loadOverview()
  } catch (error) {
    console.error('删除评估记录失败:', error)
    message.error(error.message || '删除评估记录失败')
  }
}

const handleDatasetCreated = async () => {
  taskerStore.loadTasks()
  datasetPage.value = 1
  await loadOverview()
}

const handleRunCreated = async () => {
  taskerStore.loadTasks()
  runPage.value = 1
  await loadOverview()
}

onMounted(() => loadOverview())
onUnmounted(() => {
  disposed = true
  latestOverviewRequest += 1
  latestOrphanRequest += 1
  stopRefreshTimer()
})

defineExpose({ loadOverview })
</script>

<style lang="less" scoped>
@import '@/assets/css/extensions.less';

.evaluation-workspace {
  height: 100%;
  min-height: 0;
  overflow-y: auto;
  padding: 40px 24px 56px;
}

.evaluation-workspace-content {
  width: min(100%, 920px);
  margin: 0 auto;
}

.evaluation-section + .evaluation-section {
  margin-top: 42px;
}

.evaluation-section-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 14px;
}

.evaluation-section-heading {
  min-width: 0;

  p {
    margin: 5px 0 0;
    color: var(--gray-500);
    font-size: 13px;
    line-height: 1.5;
  }
}

.evaluation-section-title-row {
  display: flex;
  align-items: center;
  gap: 8px;

  h3 {
    margin: 0;
    color: var(--gray-900);
    font-size: 16px;
    font-weight: 700;
  }
}

.evaluation-section-count {
  min-width: 22px;
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--gray-100);
  color: var(--gray-600);
  font-size: 12px;
  line-height: 20px;
  text-align: center;
}

.evaluation-list-shell {
  overflow: hidden;
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  background: var(--gray-0);
  box-shadow: 0 1px 2px rgb(15 23 42 / 3%);
}

.evaluation-list-pagination {
  display: flex;
  justify-content: flex-end;
  padding: 12px 16px;
  border-top: 1px solid var(--gray-150);
}

.evaluation-divider-list {
  display: flex;
  flex-direction: column;
}

.evaluation-list-row {
  width: 100%;
  min-height: 82px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 40px;
  align-items: stretch;
  border-bottom: 1px solid var(--gray-150);
  background: transparent;
  transition: background-color 160ms ease;

  &:last-child {
    border-bottom: 0;
  }

  &:hover {
    background: var(--gray-25);
  }
}

.evaluation-row-open {
  min-width: 0;
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) auto 16px;
  align-items: center;
  gap: 14px;
  padding: 13px 8px 13px 16px;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;

  &:focus-visible {
    position: relative;
    z-index: 1;
    outline: 2px solid var(--main-color);
    outline-offset: -2px;
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.68;
  }
}

.evaluation-run-row .evaluation-row-open {
  grid-template-columns: 34px minmax(0, 1fr) auto 16px;
}

.evaluation-row-icon {
  width: 32px;
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 9px;
  background: var(--gray-50);
  color: var(--gray-600);

  &.run-running {
    background: var(--color-info-50);
    color: var(--color-info-700);
  }

  &.run-completed {
    background: var(--color-success-50);
    color: var(--color-success-700);
  }

  &.run-failed {
    background: var(--color-error-50);
    color: var(--color-error-700);
  }
}

.evaluation-row-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.evaluation-row-side {
  min-width: 0;
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 26px;
}

.evaluation-row-title-line {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;

  strong {
    min-width: 0;
    overflow: hidden;
    color: var(--gray-900);
    font-size: 14px;
    font-weight: 600;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.evaluation-row-description {
  overflow: hidden;
  color: var(--gray-500);
  font-size: 12px;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.evaluation-row-progress {
  display: flex;
  align-items: center;
  gap: 8px;
  max-width: 360px;
  color: var(--gray-500);
  font-size: 11px;

  :deep(.ant-progress) {
    width: 110px;
    min-width: 110px;
    margin: 0;
  }

  span {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.evaluation-status-tag {
  flex: 0 0 auto;
  padding: 2px 7px;
  border-radius: 999px;
  background: var(--gray-100);
  color: var(--gray-600);
  font-size: 11px;
  font-weight: 500;
  line-height: 18px;

  &.status-running {
    background: var(--color-info-50);
    color: var(--color-info-700);
  }

  &.status-completed {
    background: var(--color-success-50);
    color: var(--color-success-700);
  }

  &.status-failed {
    background: var(--color-error-50);
    color: var(--color-error-700);
  }

  &.status-pending,
  &.status-paused {
    background: var(--color-warning-50);
    color: var(--color-warning-900);
  }
}

.evaluation-row-meta {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
  color: var(--gray-500);
  font-size: 11px;
  line-height: 1.35;
  white-space: nowrap;
}

.evaluation-row-capabilities {
  display: flex;
  justify-content: flex-end;
  gap: 5px;
  max-width: 220px;
  flex-wrap: wrap;

  span {
    padding: 3px 7px;
    border: 1px solid var(--gray-150);
    border-radius: 999px;
    background: var(--gray-0);
    color: var(--gray-500);
    font-size: 10px;
    white-space: nowrap;
  }
}

.evaluation-run-metrics {
  display: flex;
  align-items: center;
  gap: 18px;

  span {
    min-width: 68px;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }

  small {
    color: var(--gray-500);
    font-size: 10px;
  }

  strong {
    color: var(--gray-800);
    font-family: var(--mono-font, ui-monospace, SFMono-Regular, Menlo, monospace);
    font-size: 12px;
    font-variant-numeric: tabular-nums;
  }
}

.evaluation-row-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  padding-right: 8px;
}

.evaluation-icon-button {
  width: 32px;
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--gray-500);
  cursor: pointer;

  &:hover,
  &:focus-visible {
    background: var(--gray-100);
    color: var(--gray-800);
    outline: none;
  }

  &:focus-visible {
    box-shadow: 0 0 0 2px var(--main-color);
  }

  &.evaluation-icon-button-danger:hover,
  &.evaluation-icon-button-danger:focus-visible {
    background: var(--color-error-50);
    color: var(--color-error-700);
  }
}

.evaluation-row-chevron {
  color: var(--gray-400);
}

.evaluation-menu-item {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.evaluation-inline-state {
  min-height: 150px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 24px;
  color: var(--color-error-700);

  div {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }

  strong {
    color: var(--gray-900);
    font-size: 13px;
  }

  span {
    color: var(--gray-500);
    font-size: 12px;
  }
}

.evaluation-skeleton-row {
  padding: 15px 16px;
  border-bottom: 1px solid var(--gray-150);

  &:last-child {
    border-bottom: 0;
  }

  :deep(.ant-skeleton-title) {
    margin-top: 0;
  }

  :deep(.ant-skeleton-paragraph) {
    margin: 8px 0 0;
  }
}

.evaluation-spin {
  animation: evaluation-spin 0.9s linear infinite;
}

.historical-run-results {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.historical-run-context {
  margin: 0;
  color: var(--gray-600);
  font-size: 13px;
}

.historical-run-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px;
  color: var(--color-error-700);
  background: var(--color-error-50);
  border-radius: 8px;
}

.historical-run-cell {
  max-height: 140px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.historical-run-metrics {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.historical-run-pagination {
  align-self: flex-end;
}

@keyframes evaluation-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 820px) {
  .evaluation-workspace {
    padding: 32px 16px 48px;
  }

  .evaluation-list-row,
  .evaluation-run-row {
    grid-template-columns: minmax(0, 1fr) 40px;
  }

  .evaluation-row-open,
  .evaluation-run-row .evaluation-row-open {
    grid-template-columns: 34px minmax(0, 1fr) 16px;
  }

  .evaluation-row-side {
    display: none;
  }
}

@media (max-width: 560px) {
  .evaluation-section-header {
    align-items: stretch;
    flex-direction: column;
    gap: 12px;
  }

  .evaluation-section-header > .extension-panel-action {
    align-self: flex-start;
  }

  .evaluation-row-open,
  .evaluation-run-row .evaluation-row-open {
    grid-template-columns: 30px minmax(0, 1fr) 16px;
    gap: 10px;
    padding: 13px 12px;
  }

  .evaluation-row-icon {
    width: 30px;
    height: 30px;
  }

  .evaluation-row-progress :deep(.ant-progress) {
    width: 72px;
    min-width: 72px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .evaluation-spin {
    animation: none;
  }
}
</style>
