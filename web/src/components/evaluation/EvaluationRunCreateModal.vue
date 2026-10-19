<template>
  <a-modal
    v-model:open="visible"
    title="新建评估"
    width="560px"
    :mask-closable="!submitting"
    :closable="!submitting"
    @cancel="closeModal"
  >
    <a-form ref="formRef" :model="form" :rules="rules" layout="vertical">
      <a-form-item label="评估名称" name="name">
        <a-input
          v-model:value="form.name"
          placeholder="请输入评估名称"
          :maxlength="100"
          show-count
        />
      </a-form-item>

      <a-form-item label="评估基准" name="datasetId">
        <a-select
          v-model:value="form.datasetId"
          placeholder="请选择评估基准"
          :options="datasetOptions"
          :loading="datasetsLoading"
          show-search
          :filter-option="false"
          @search="searchDatasets"
          @popupScroll="loadMoreDatasets"
          @change="handleDatasetChange"
        />
        <div v-if="datasetsTotal > 20" class="dataset-picker-hint">
          已显示 {{ remoteDatasets.length }} / {{ datasetsTotal }} 项，可搜索或向下滚动加载
        </div>
      </a-form-item>

      <div class="evaluation-model-grid">
        <a-form-item
          :label="selectedDataset?.has_gold_answers ? '答案生成模型（可选）' : '答案生成模型'"
        >
          <ModelSelectorComponent
            :model_spec="form.answerModel"
            placeholder="选择答案生成模型"
            display-name="short"
            clearable
            :disabled="!selectedDataset?.has_gold_answers"
            @select-model="(value) => (form.answerModel = value)"
          />
        </a-form-item>

        <a-form-item
          :label="selectedDataset?.has_gold_answers ? '答案评判模型（可选）' : '答案评判模型'"
        >
          <ModelSelectorComponent
            :model_spec="form.judgeModel"
            placeholder="选择答案评判模型"
            display-name="short"
            clearable
            :disabled="!selectedDataset?.has_gold_answers"
            @select-model="(value) => (form.judgeModel = value)"
          />
        </a-form-item>
      </div>

      <div class="evaluation-run-hint" role="status">
        <Info :size="15" aria-hidden="true" />
        <span>{{ evaluationHint }}</span>
      </div>
    </a-form>

    <template #footer>
      <a-button :disabled="submitting" @click="closeModal">取消</a-button>
      <a-button
        type="primary"
        :loading="submitting"
        :disabled="!form.datasetId"
        @click="submitEvaluation"
      >
        开始评估
      </a-button>
    </template>
  </a-modal>
</template>

<script setup>
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { Info } from '@lucide/vue'
import ModelSelectorComponent from '@/components/ModelSelectorComponent.vue'
import { evaluationApi } from '@/apis/knowledge_api'

const props = defineProps({
  open: { type: Boolean, default: false },
  kbId: { type: String, required: true },
  datasets: { type: Array, default: () => [] },
  initialDatasetId: { type: String, default: '' }
})

const emit = defineEmits(['update:open', 'success'])

const formRef = ref(null)
const submitting = ref(false)
const datasetsLoading = ref(false)
const remoteDatasets = ref([])
const datasetsTotal = ref(0)
const chosenDataset = ref(null)
let datasetsSearchTimer = null
let datasetsRequestId = 0
let datasetsQuery = ''
const form = reactive({
  name: '',
  datasetId: '',
  answerModel: '',
  judgeModel: ''
})

const rules = {
  name: [{ required: true, message: '请输入评估名称', trigger: 'blur' }],
  datasetId: [{ required: true, message: '请选择评估基准', trigger: 'change' }]
}

const visible = computed({
  get: () => props.open,
  set: (value) => emit('update:open', value)
})

const datasetOptions = computed(() =>
  [
    ...new Map(
      [chosenDataset.value, ...remoteDatasets.value]
        .filter(Boolean)
        .map((dataset) => [dataset.dataset_id, dataset])
    ).values()
  ].map((dataset) => ({
    value: dataset.dataset_id,
    label: `${dataset.name}（${dataset.item_count || 0} 题）`
  }))
)

const selectedDataset = computed(
  () =>
    chosenDataset.value ||
    remoteDatasets.value.find((dataset) => dataset.dataset_id === form.datasetId)
)

const fetchDatasets = async (search = '', append = false) => {
  if (!props.kbId || (datasetsLoading.value && append)) return
  const requestId = ++datasetsRequestId
  const offset = append ? remoteDatasets.value.length : 0
  datasetsLoading.value = true
  try {
    const response = await evaluationApi.listDatasets(props.kbId, {
      limit: 20,
      offset,
      status: 'completed',
      search
    })
    if (requestId !== datasetsRequestId || !props.open) return
    if (response?.message !== 'success' || !Array.isArray(response.data)) {
      throw new Error('评估基准数据格式错误')
    }
    const items = append ? [...remoteDatasets.value, ...response.data] : response.data
    remoteDatasets.value = [...new Map(items.map((item) => [item.dataset_id, item])).values()]
    datasetsTotal.value = response.pagination?.total ?? remoteDatasets.value.length
    if (!form.datasetId && remoteDatasets.value.length) {
      form.datasetId = remoteDatasets.value[0].dataset_id
      chosenDataset.value = remoteDatasets.value[0]
    }
  } catch (error) {
    if (requestId === datasetsRequestId && props.open) {
      message.error(error.message || '加载评估基准失败')
    }
  } finally {
    if (requestId === datasetsRequestId) datasetsLoading.value = false
  }
}

const searchDatasets = (value) => {
  datasetsQuery = value.trim()
  if (datasetsSearchTimer) clearTimeout(datasetsSearchTimer)
  datasetsSearchTimer = setTimeout(() => fetchDatasets(datasetsQuery), 250)
}

const loadMoreDatasets = (event) => {
  const target = event.target
  if (!target || target.scrollTop + target.clientHeight < target.scrollHeight - 16) return
  if (remoteDatasets.value.length < datasetsTotal.value) fetchDatasets(datasetsQuery, true)
}

const buildDefaultName = () => {
  const now = new Date()
  const date = [now.getFullYear(), now.getMonth() + 1, now.getDate()]
    .map((value, index) => (index === 0 ? value : String(value).padStart(2, '0')))
    .join('')
  const suffix =
    globalThis.crypto?.randomUUID?.().replaceAll('-', '').slice(0, 6) ||
    Date.now().toString(16).slice(-6)
  return `eval-${date}-${suffix}`
}

const evaluationHint = computed(() => {
  if (!selectedDataset.value) return '选择一个已完成的评估基准后开始。'
  if (!selectedDataset.value.has_gold_answers) return '当前基准仅执行检索评估，无需选择模型。'
  if (form.answerModel && form.judgeModel) return '将同时执行检索评估和答案评估。'
  if (!form.answerModel && !form.judgeModel) return '不选择模型时仅执行检索评估。'
  return '答案生成模型和答案评判模型需要同时选择。'
})

const resetForm = () => {
  form.name = buildDefaultName()
  form.datasetId =
    props.datasets.find((dataset) => dataset.dataset_id === props.initialDatasetId)?.dataset_id ||
    props.datasets[0]?.dataset_id ||
    ''
  form.answerModel = ''
  form.judgeModel = ''
  chosenDataset.value =
    props.datasets.find((dataset) => dataset.dataset_id === form.datasetId) || null
  remoteDatasets.value = []
  datasetsTotal.value = 0
  datasetsQuery = ''
  fetchDatasets()
  formRef.value?.clearValidate()
}

const handleDatasetChange = (datasetId) => {
  chosenDataset.value =
    remoteDatasets.value.find((dataset) => dataset.dataset_id === datasetId) ||
    props.datasets.find((dataset) => dataset.dataset_id === datasetId) ||
    null
  form.answerModel = ''
  form.judgeModel = ''
}

const closeModal = () => {
  if (submitting.value) return
  visible.value = false
}

const submitEvaluation = async () => {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }

  const hasAnswerModel = !!form.answerModel
  const hasJudgeModel = !!form.judgeModel
  if (hasAnswerModel !== hasJudgeModel) {
    message.warning('答案生成模型和答案评判模型需要同时选择')
    return
  }

  submitting.value = true
  try {
    const response = await evaluationApi.runEvaluation(props.kbId, {
      dataset_id: form.datasetId,
      name: form.name.trim(),
      model_config: {
        answer_llm: selectedDataset.value?.has_gold_answers ? form.answerModel : '',
        judge_llm: selectedDataset.value?.has_gold_answers ? form.judgeModel : ''
      }
    })
    if (response?.message !== 'success') {
      throw new Error(response?.message || '启动评估失败')
    }

    message.success('评估任务已开始')
    visible.value = false
    emit('success', response.data)
  } catch (error) {
    console.error('启动评估失败:', error)
    message.error(error.message || '启动评估失败')
  } finally {
    submitting.value = false
  }
}

watch(
  () => props.open,
  (open) => {
    if (open) resetForm()
    else {
      datasetsRequestId += 1
      if (datasetsSearchTimer) clearTimeout(datasetsSearchTimer)
    }
  }
)
onUnmounted(() => {
  datasetsRequestId += 1
  if (datasetsSearchTimer) clearTimeout(datasetsSearchTimer)
})
</script>

<style lang="less" scoped>
.evaluation-model-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.dataset-picker-hint {
  margin-top: 6px;
  color: var(--gray-500);
  font-size: 12px;
}

.evaluation-run-hint {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 6px;
  background: var(--color-info-50);
  color: var(--color-info-700);
  font-size: 12px;
  line-height: 1.5;

  svg {
    flex: 0 0 auto;
    margin-top: 1px;
  }
}

@media (max-width: 640px) {
  .evaluation-model-grid {
    grid-template-columns: 1fr;
    gap: 0;
  }
}
</style>
