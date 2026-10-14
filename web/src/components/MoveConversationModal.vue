<template>
  <a-modal
    :open="open"
    title="移入已有项目"
    width="720px"
    :style="{ top: '4vh' }"
    :body-style="{ maxHeight: '70vh', overflowY: 'auto' }"
    :closable="!submitting"
    :mask-closable="!submitting"
    :confirm-loading="submitting"
    :ok-button-props="{
      disabled: !targetProjectId || projectsLoading || filesLoading || !!filesError || !availableProjects.length
    }"
    :cancel-button-props="{ disabled: submitting }"
    ok-text="移入项目"
    cancel-text="取消"
    @ok="submitMove"
    @cancel="closeModal"
  >
    <div class="move-conversation-content">
      <p class="move-intro">
        “{{
          chat?.title || '新的对话'
        }}”的历史记录会保留。之后使用目标项目目录；原文件继续留在原目录，勾选的文件会复制到目标项目。
      </p>

      <section class="move-section" aria-labelledby="move-project-heading">
        <h3 id="move-project-heading">目标项目</h3>
        <label class="move-search-label">
          <span>查找项目</span>
          <input
            v-model="projectQuery"
            type="search"
            placeholder="按项目名称搜索"
            :disabled="submitting"
          />
        </label>
        <div class="move-project-list" role="radiogroup" aria-label="选择目标项目">
          <div v-if="projectsLoading" class="move-state" role="status">正在加载项目...</div>
          <div v-else-if="projectsError" class="move-state move-error" role="alert">
            项目加载失败。<button type="button" @click="$emit('retry-projects')">重试</button>
          </div>
          <div v-else-if="!filteredProjects.length" class="move-state">
            {{ projectQuery ? '没有匹配的项目' : '没有可移入的已有项目' }}
          </div>
          <button
            v-for="project in projectsLoading || projectsError ? [] : filteredProjects"
            :key="project.id"
            type="button"
            class="move-project-option"
            :class="{ selected: targetProjectId === project.id }"
            role="radio"
            :aria-checked="targetProjectId === project.id"
            :disabled="submitting"
            @click="selectProject(project.id)"
          >
            <FolderClosed :size="16" />
            <span>{{ project.name }}</span>
            <Check v-if="targetProjectId === project.id" :size="15" />
          </button>
        </div>
      </section>

      <section class="move-section" aria-labelledby="move-files-heading">
        <div class="move-section-heading">
          <h3 id="move-files-heading">导入原目录文件</h3>
          <span>{{ selectedFiles.length }} 个已选</span>
        </div>
        <p v-if="chat?.workdir_path" class="move-source-path" :title="chat.workdir_path">
          原目录：个人空间/{{ chat.workdir_path }}
        </p>
        <p class="move-helper">
          可逐个选择，或全选当前文件夹中的文件；子文件夹需进入后选择。默认不导入文件，也不会覆盖同名文件。最多
          100 个，单个 50 MB，合计 100 MB。
        </p>
        <nav class="move-breadcrumbs" aria-label="原文件目录">
          <button
            v-for="part in breadcrumbs"
            :key="part.path"
            type="button"
            :disabled="submitting || filesLoading || part.path === currentDirectory"
            @click="loadDirectory(part.path)"
          >
            {{ part.name }}
          </button>
        </nav>
        <label v-if="!filesLoading && !filesError && files.length" class="move-folder-selection">
          <input
            type="checkbox"
            :checked="allCurrentFilesSelected"
            :indeterminate="someCurrentFilesSelected && !allCurrentFilesSelected"
            :disabled="submitting"
            @change="onCurrentDirectorySelectionChange($event)"
          />
          {{ allCurrentFilesSelected ? '取消当前文件夹全选' : '全选当前文件夹' }}
          <span>（{{ files.length }} 个文件）</span>
        </label>
        <div class="move-file-list" :aria-busy="filesLoading">
          <div v-if="filesLoading" class="move-state" role="status">正在加载文件...</div>
          <div v-else-if="filesError" class="move-state move-error" role="alert">
            {{ filesError }}
            <button type="button" @click="loadDirectory(currentDirectory)">重试</button>
          </div>
          <div v-else-if="!entries.length" class="move-state">这个文件夹中没有文件</div>
          <template v-else>
            <button
              v-for="entry in directories"
              :key="entry.path"
              type="button"
              class="move-file-row"
              :disabled="submitting"
              @click="loadDirectory(entry.path)"
            >
              <FolderClosed :size="16" />
              <span :title="entry.path">{{ entry.name }}</span>
              <ChevronRight :size="15" />
            </button>
            <label v-for="entry in files" :key="entry.path" class="move-file-row">
              <input
                type="checkbox"
                :checked="selectedPaths.has(entry.path)"
                :disabled="submitting"
                @change="onFileChange(entry, $event)"
              />
              <FileTypeIcon :name="entry.name" :size="16" />
              <span :title="entry.path">{{ entry.name }}</span>
              <small>{{ formatFileSize(entry.size) }}</small>
            </label>
          </template>
        </div>
        <div v-if="selectedFiles.length" class="move-selected-files">
          <strong>已选原文件</strong>
          <div v-for="file in selectedFiles" :key="file.path" class="move-selected-row">
            <span :title="file.path">{{ file.path }}</span>
            <button
              type="button"
              :disabled="submitting"
              :aria-label="`移除 ${file.path}`"
              @click="toggleFile(file, false)"
            >
              移除
            </button>
          </div>
        </div>
      </section>

      <section
        v-if="conflicts.length"
        class="move-section move-conflicts"
        aria-labelledby="move-conflicts-heading"
      >
        <h3 id="move-conflicts-heading">处理文件冲突</h3>
        <p class="move-helper">
          这些目标位置无法直接导入。逐项跳过或改名；默认跳过，现有文件不会被覆盖。
        </p>
        <div v-for="conflict in conflicts" :key="conflict.source_path" class="move-conflict-row">
          <span class="move-conflict-path" :title="conflict.destination_path">{{
            conflict.destination_path
          }}</span>
          <span class="move-conflict-reason">{{ conflictReason(conflict.reason) }}</span>
          <label>
            <span>处理方式</span>
            <select v-model="resolutions[conflict.source_path].action" :disabled="submitting">
              <option value="skip">跳过</option>
              <option v-if="canRenameConflict(conflict)" value="rename">改名导入</option>
            </select>
          </label>
          <label v-if="resolutions[conflict.source_path].action === 'rename'">
            <span>新文件名</span>
            <input
              v-model="resolutions[conflict.source_path].name"
              :disabled="submitting"
              maxlength="255"
            />
          </label>
        </div>
      </section>

      <p v-if="formError" class="move-error-message" role="alert">{{ formError }}</p>
    </div>
  </a-modal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { Check, ChevronRight, FolderClosed } from '@lucide/vue'
import FileTypeIcon from '@/components/common/FileTypeIcon.vue'
import { getViewerFileSystemTree } from '@/apis/viewer_filesystem'
import { filterProjects } from '@/utils/projectSelection'

const MAX_FILES = 100
const MAX_FILE_BYTES = 50 * 1024 * 1024
const MAX_TOTAL_BYTES = 100 * 1024 * 1024

const props = defineProps({
  open: { type: Boolean, default: false },
  chat: { type: Object, default: null },
  projects: { type: Array, default: () => [] },
  projectsLoading: { type: Boolean, default: false },
  projectsError: { type: String, default: '' },
  moveConversation: { type: Function, required: true }
})
const emit = defineEmits(['update:open', 'moved', 'retry-projects'])

const projectQuery = ref('')
const targetProjectId = ref('')
const currentDirectory = ref('/')
const entries = ref([])
const selectedFiles = ref([])
const moveRequestId = ref('')
const submittedIntent = ref('')
const filesLoading = ref(false)
const filesError = ref('')
const formError = ref('')
const submitting = ref(false)
const conflicts = ref([])
const resolutions = ref({})
let directoryRequest = 0

/** 为一次移动尝试生成稳定的请求 ID。 */
const newRequestId = () =>
  typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `req-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`

const availableProjects = computed(() =>
  props.projects.filter(
    (project) =>
      project.status !== 'deleted' &&
      project.selection_status === 'selectable' &&
      project.id !== props.chat?.project_id
  )
)
const filteredProjects = computed(() => filterProjects(availableProjects.value, projectQuery.value))
const selectedPaths = computed(() => new Set(selectedFiles.value.map((file) => file.path)))
const directories = computed(() => entries.value.filter((entry) => entry.is_dir))
const files = computed(() => entries.value.filter((entry) => !entry.is_dir))
const allCurrentFilesSelected = computed(
  () => files.value.length > 0 && files.value.every((file) => selectedPaths.value.has(file.path))
)
const someCurrentFilesSelected = computed(() =>
  files.value.some((file) => selectedPaths.value.has(file.path))
)
const breadcrumbs = computed(() => {
  const parts = [{ name: '原目录', path: '/' }]
  let path = ''
  for (const name of currentDirectory.value.split('/').filter(Boolean)) {
    path += `/${name}`
    parts.push({ name, path })
  }
  return parts
})

/** 读取当前会话目录的一层，不预加载其他文件夹。 */
async function loadDirectory(path) {
  if (!props.chat?.id) return
  const request = ++directoryRequest
  filesLoading.value = true
  filesError.value = ''
  try {
    const response = await getViewerFileSystemTree(props.chat.id, path)
    if (request !== directoryRequest) return
    currentDirectory.value = path
    entries.value = Array.isArray(response?.entries) ? response.entries : []
  } catch {
    if (request !== directoryRequest) return
    entries.value = []
    filesError.value = '原目录暂时无法读取。迁移需要原目录可用，请确认后重试。'
  } finally {
    if (request === directoryRequest) filesLoading.value = false
  }
}

/** 打开弹窗时清理上次选择并读取原目录。 */
function resetForm() {
  directoryRequest += 1
  projectQuery.value = ''
  targetProjectId.value = ''
  currentDirectory.value = '/'
  entries.value = []
  selectedFiles.value = []
  moveRequestId.value = ''
  submittedIntent.value = ''
  filesLoading.value = false
  filesError.value = ''
  formError.value = ''
  conflicts.value = []
  resolutions.value = {}
  if (props.chat?.id) void loadDirectory('/')
}

watch(
  () => [props.open, props.chat?.id],
  ([open]) => {
    if (open) resetForm()
    else directoryRequest += 1
  }
)

/** 提交期间保持弹窗，避免丢失正在确认的结果。 */
function closeModal() {
  if (submitting.value) return
  emit('update:open', false)
}

/** 切换目标项目时清空旧项目的冲突处理。 */
function selectProject(projectId) {
  targetProjectId.value = projectId
  conflicts.value = []
  resolutions.value = {}
  formError.value = ''
}

/** 逐文件选择并检查导入上限。 */
function toggleFile(file, checked) {
  if (checked && !selectedPaths.value.has(file.path)) {
    if (selectedFiles.value.length >= MAX_FILES) {
      formError.value = '一次最多导入 100 个文件。'
      return
    }
    if (Number(file.size) > MAX_FILE_BYTES) {
      formError.value = '单个文件不能超过 50 MB。'
      return
    }
    const total = selectedFiles.value.reduce((size, item) => size + (Number(item.size) || 0), 0)
    if (total + (Number(file.size) || 0) > MAX_TOTAL_BYTES) {
      formError.value = '所选文件总计不能超过 100 MB。'
      return
    }
    selectedFiles.value = [...selectedFiles.value, file]
  } else if (!checked) {
    deselectFiles(new Set([file.path]))
  }
  formError.value = ''
}

/** 取消选择时同时清理对应的文件冲突决议。 */
function deselectFiles(paths) {
  selectedFiles.value = selectedFiles.value.filter((file) => !paths.has(file.path))
  conflicts.value = conflicts.value.filter((conflict) => !paths.has(conflict.source_path))
  resolutions.value = Object.fromEntries(
    Object.entries(resolutions.value).filter(([sourcePath]) => !paths.has(sourcePath))
  )
  formError.value = ''
}

/** 批量操作只作用于当前已加载文件夹，且超限时不部分选择。 */
function toggleCurrentDirectory(checked) {
  if (submitting.value || filesLoading.value || filesError.value || !files.value.length) return
  if (!checked) {
    deselectFiles(new Set(files.value.map((file) => file.path)))
    return
  }

  const newFiles = files.value.filter((file) => !selectedPaths.value.has(file.path))
  const nextSelected = [...selectedFiles.value, ...newFiles]
  if (nextSelected.length > MAX_FILES) {
    formError.value = '一次最多导入 100 个文件；当前文件夹无法全部选中。'
    return
  }
  if (newFiles.some((file) => Number(file.size) > MAX_FILE_BYTES)) {
    formError.value = '当前文件夹有文件超过 50 MB，请逐个选择。'
    return
  }
  const total = nextSelected.reduce((size, file) => size + (Number(file.size) || 0), 0)
  if (total > MAX_TOTAL_BYTES) {
    formError.value = '所选文件总计不能超过 100 MB；当前文件夹无法全部选中。'
    return
  }
  selectedFiles.value = nextSelected
  formError.value = ''
}

/** 批量选择失败时恢复原复选框状态。 */
function onCurrentDirectorySelectionChange(event) {
  toggleCurrentDirectory(event.target.checked)
  event.target.checked = allCurrentFilesSelected.value
  event.target.indeterminate = someCurrentFilesSelected.value && !allCurrentFilesSelected.value
}

/** 文件超限时恢复复选框状态。 */
function onFileChange(file, event) {
  toggleFile(file, event.target.checked)
  event.target.checked = selectedPaths.value.has(file.path)
}

/** 将文件字节数格式化为简短标签。 */
function formatFileSize(size) {
  if (!Number.isFinite(size)) return ''
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

/** 将服务端冲突原因显示为用户可处理的说明。 */
function conflictReason(reason) {
  if (reason === 'already_exists') return '已有同名文件或文件夹'
  if (reason === 'same_file') return '目标与原文件是同一文件'
  if (reason === 'duplicate_destination') return '所选文件使用了相同目标位置'
  if (reason === 'selected_parent_is_file') return '目标上层是文件，无法创建文件夹'
  if (reason === 'destination_unavailable') return '目标位置不可用'
  return '目标位置已被占用'
}

/** 仅可通过改名解决的冲突提供改名选项。 */
function canRenameConflict(conflict) {
  return ['already_exists', 'same_file', 'duplicate_destination'].includes(conflict.reason)
}

/** 为冲突文件建议保留扩展名的新名称。 */
function suggestedName(path) {
  const name = path.split('/').pop() || ''
  const dot = name.lastIndexOf('.')
  return dot > 0 ? `${name.slice(0, dot)}-副本${name.slice(dot)}` : `${name}-副本`
}

/** 只发送用户决定导入的文件，不包含默认跳过项。 */
function filesForRequest() {
  const conflictPaths = new Set(conflicts.value.map((conflict) => conflict.source_path))
  return selectedFiles.value.flatMap((file) => {
    if (!conflictPaths.has(file.path)) {
      return [{ source_path: file.path, destination_path: file.path }]
    }
    const choice = resolutions.value[file.path]
    if (!choice || choice.action === 'skip') return []
    const name = choice.name.trim()
    if (
      !name ||
      name === '.' ||
      name === '..' ||
      name.includes('/') ||
      name.includes('\\') ||
      Array.from(name).some((character) => character.charCodeAt(0) < 32)
    ) {
      throw new Error(`“${file.path}”的新文件名无效`)
    }
    const parent = file.path.slice(0, file.path.lastIndexOf('/'))
    return [{ source_path: file.path, destination_path: `${parent}/${name}` }]
  })
}

/** 提交移动，并在文件冲突时保留选择供逐项修正。 */
async function submitMove() {
  if (submitting.value || filesLoading.value || filesError.value || !targetProjectId.value || !props.chat?.id) return
  formError.value = ''
  let filesToImport
  try {
    filesToImport = filesForRequest()
    filesToImport.sort((left, right) => left.source_path.localeCompare(right.source_path))
    if (new Set(filesToImport.map((file) => file.destination_path)).size !== filesToImport.length) {
      throw new Error('多个文件不能导入到同一位置，请修改文件名。')
    }
  } catch (error) {
    formError.value = error.message
    return
  }
  const intent = JSON.stringify({ target_project_id: targetProjectId.value, files: filesToImport })
  if (intent !== submittedIntent.value) {
    moveRequestId.value = newRequestId()
    submittedIntent.value = intent
  }
  submitting.value = true
  try {
    const result = await props.moveConversation(
      props.chat.id,
      targetProjectId.value,
      filesToImport,
      moveRequestId.value
    )
    emit('moved', result)
    emit('update:open', false)
  } catch (error) {
    const detail = error?.response?.data?.detail
    if (
      error?.status === 409 &&
      detail?.code === 'file_conflicts' &&
      Array.isArray(detail.conflicts)
    ) {
      const currentConflicts = detail.conflicts.filter(
        (item) =>
          selectedPaths.value.has(item.source_path) && typeof item.destination_path === 'string'
      )
      // 后续请求只包含未跳过的文件；服务端不会再返回此前已跳过的冲突。
      const bySource = new Map(conflicts.value.map((item) => [item.source_path, item]))
      currentConflicts.forEach((item) => bySource.set(item.source_path, item))
      conflicts.value = [...bySource.values()]
      resolutions.value = Object.fromEntries(
        conflicts.value.map((item) => {
          const previous = resolutions.value[item.source_path]
          return [
            item.source_path,
            previous?.action === 'skip' ||
            (previous?.action === 'rename' && canRenameConflict(item))
              ? previous
              : { action: 'skip', name: suggestedName(item.destination_path) }
          ]
        })
      )
      formError.value = conflicts.value.length
        ? '请处理文件冲突后重试。'
        : '目标项目中的文件发生冲突，请刷新后重试。'
    } else if (error?.status === 413) {
      formError.value = '文件超出导入上限：最多 100 个、单个 50 MB、合计 100 MB。'
    } else {
      formError.value = error?.message || '移入项目失败，请重试。'
    }
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped lang="less">
.move-conversation-content {
  display: grid;
  gap: 20px;
  color: var(--gray-900);
}
.move-intro,
.move-helper {
  margin: 0;
  color: var(--gray-600);
  font-size: 13px;
  line-height: 1.6;
}
.move-source-path {
  margin: 0;
  color: var(--gray-500);
  font-size: 12px;
  overflow-wrap: anywhere;
}
.move-section {
  display: grid;
  gap: 10px;
  min-width: 0;
}
.move-section h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}
.move-section-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.move-section-heading span {
  color: var(--gray-500);
  font-size: 12px;
}
.move-search-label,
.move-conflict-row label {
  display: grid;
  gap: 5px;
  font-size: 12px;
  color: var(--gray-600);
}
.move-search-label input,
.move-conflict-row input,
.move-conflict-row select {
  width: 100%;
  min-width: 0;
  height: 34px;
  padding: 0 10px;
  color: var(--gray-900);
  background: var(--gray-0);
  border: 1px solid var(--gray-150);
  border-radius: 6px;
}
.move-search-label input:focus-visible,
.move-conflict-row input:focus-visible,
.move-conflict-row select:focus-visible,
.move-project-option:focus-visible,
.move-file-row:focus-visible,
.move-breadcrumbs button:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}
.move-project-list,
.move-file-list {
  max-height: 164px;
  overflow-y: auto;
  background: var(--gray-0);
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}
.move-project-option,
.move-file-row {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  min-height: 38px;
  padding: 7px 12px;
  color: var(--gray-900);
  background: transparent;
  border: 0;
  text-align: left;
  cursor: pointer;
}
.move-project-option + .move-project-option,
.move-file-row + .move-file-row {
  border-top: 1px solid var(--gray-100);
}
.move-project-option:hover,
.move-file-row:hover {
  background: var(--gray-25);
}
.move-project-option.selected {
  color: var(--main-color);
  background: var(--main-30);
}
.move-project-option span,
.move-file-row span {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.move-file-row small {
  color: var(--gray-500);
  white-space: nowrap;
}
.move-file-row input {
  accent-color: var(--main-color);
}
.move-folder-selection {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  width: fit-content;
  color: var(--gray-900);
  font-size: 13px;
  cursor: pointer;
}
.move-folder-selection input {
  margin: 0;
  accent-color: var(--main-color);
}
.move-folder-selection input:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}
.move-folder-selection span {
  color: var(--gray-500);
  font-size: 12px;
}
.move-breadcrumbs {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}
.move-breadcrumbs button,
.move-state button,
.move-selected-row button {
  padding: 2px 4px;
  color: var(--main-color);
  background: transparent;
  border: 0;
  cursor: pointer;
}
.move-breadcrumbs button:not(:last-child)::after {
  content: '/';
  margin-left: 9px;
  color: var(--gray-400);
}
.move-breadcrumbs button:disabled {
  color: var(--gray-500);
  cursor: default;
}
.move-state {
  padding: 18px 12px;
  color: var(--gray-500);
  font-size: 13px;
}
.move-selected-files {
  display: grid;
  gap: 4px;
  padding: 10px;
  background: var(--gray-25);
  border-radius: 8px;
}
.move-selected-files strong {
  font-size: 12px;
}
.move-selected-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
  font-size: 12px;
}
.move-selected-row span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.move-conflicts {
  padding: 12px;
  background: var(--color-warning-50);
  border: 1px solid var(--color-warning-100);
  border-radius: 8px;
}
.move-conflict-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 90px 105px minmax(130px, 1fr);
  gap: 10px;
  align-items: end;
  padding: 10px 0;
  border-top: 1px solid var(--gray-150);
}
.move-conflict-path {
  overflow-wrap: anywhere;
  font-size: 12px;
}
.move-conflict-reason {
  color: var(--color-warning-900);
  font-size: 12px;
}
.move-error-message,
.move-error {
  color: var(--color-error-700);
}
.move-error-message {
  margin: 0;
  font-size: 13px;
}
@media (max-width: 600px) {
  .move-conflict-row {
    grid-template-columns: minmax(0, 1fr) 105px;
  }
  .move-conflict-path,
  .move-conflict-reason {
    grid-column: span 2;
  }
}
</style>
