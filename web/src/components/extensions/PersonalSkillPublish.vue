<template>
  <a-modal
    :open="open"
    title="发布技能"
    :footer="null"
    :width="880"
    :destroy-on-close="true"
    :body-style="{ maxHeight: 'calc(100vh - 170px)', overflowY: 'auto' }"
    @cancel="close"
  >
    <div v-if="!isOwnSkill" class="publish-unavailable" role="alert">只能发布自己的个人技能。</div>
    <div v-else class="personal-skill-publish">
      <p class="publish-intro">为「{{ skill.name }}」选择发布方式。每次提交都会保存当前内容的独立快照。</p>

      <div class="publish-destinations" role="group" aria-label="发布方式与状态">
        <section v-for="destination in destinations" :key="destination.key" :data-channel="destination.key" class="publish-destination" :class="{ selected: channel === destination.key }">
          <button type="button" class="publish-choice" :data-channel-choice="destination.key" :aria-pressed="channel === destination.key" @click="channel = destination.key">
            <span class="publish-choice-heading">
              <strong>{{ destination.label }}</strong>
              <span :class="['publish-status', destination.history.latest?.status || 'empty']">{{ historyLoading ? '读取中' : statusLabel(destination.history.latest?.status) }}</span>
            </span>
            <span class="publish-choice-description">{{ destination.key === 'market' ? '审核通过后，成员可自行安装使用' : userStore.isAdmin ? '发布后，指定成员即可使用' : '审核通过后，获授权成员即可使用' }}</span>
          </button>
          <div class="publish-history-body">
            <template v-if="!historyLoading">
              <p v-if="destination.history.latest?.status === 'rejected' && destination.history.latest.review_note" class="publish-history-note">
                审核意见：{{ destination.history.latest.review_note }}
              </p>
              <p v-if="destination.history.latest" class="publish-history-detail">
                最近申请：{{ formatTime(destination.history.latest.created_at) }}
                <button type="button" class="publish-link" :data-request-id="destination.history.latest.id" @click="openSnapshot(destination.history.latest)">查看提交快照</button>
              </p>
              <p v-if="destination.history.version" class="publish-history-detail">
                {{ destination.key === 'shared' ? '最近发布' : '当前已上架' }} v{{ destination.history.version }} · {{ destination.key === 'shared' ? '审核发布时范围：' : '' }}{{ destination.history.audience }}
                <button v-if="destination.history.released && destination.history.released.id !== destination.history.latest?.id" type="button" class="publish-link" :data-request-id="destination.history.released.id" @click="openSnapshot(destination.history.released)">查看已发布快照</button>
              </p>
              <p v-else class="publish-history-detail">{{ destination.history.released ? '曾通过审核，当前发布版本无法确认' : '尚无已发布版本' }}</p>
              <p v-if="destination.key === 'shared' && destination.history.released" class="publish-history-detail">
                {{ sharedCopyStatusLabel(destination.history.released.published_copy_status) }}
              </p>
            </template>
          </div>
        </section>
      </div>
      <div v-if="historyError" class="publish-error" role="alert">
        {{ historyError }} <button type="button" class="publish-link" @click="loadHistory">重试</button>
      </div>

      <div class="publish-form">
        <label class="publish-field">
          <span>技能方向</span>
          <a-select v-model:value="category" :options="skillCategories" aria-label="技能方向" />
        </label>
        <label class="publish-field">
          <span>发布说明 <em>必填</em></span>
          <a-textarea v-model:value="reason" aria-label="发布说明" :rows="3" :maxlength="1000" placeholder="说明使用场景和适用人群" show-count />
        </label>
        <div v-if="channel === 'shared' && userStore.isAdmin" class="publish-share-scope">
          <div class="publish-field-title">共享范围</div>
          <ShareConfigForm
            ref="shareConfigFormRef"
            v-model="shareConfig"
            :require-read-scope="true"
            :show-manage-scope="false"
            :allowed-access-levels="['department', 'user']"
            :available-departments="userStore.isSuperAdmin ? null : ownDepartmentOptions"
          />
        </div>
        <p v-if="selectedHistory.latest?.status === 'pending'" class="publish-pending">这个渠道已有待审核申请，请等待处理后再提交新版本。</p>
        <div class="publish-form-actions">
          <span>{{ channel === 'shared' && userStore.isAdmin ? '将立即发布独立共享副本' : '将当前文件作为不可变快照提交审核' }}</span>
          <button type="button" class="publish-submit" :disabled="!canSubmit" @click="submit">
            {{ submitLabel }}
          </button>
        </div>
        <p v-if="submitError" class="publish-error" role="alert">{{ submitError }}</p>
      </div>

      <section class="publish-preview" aria-label="当前技能文件预览">
        <div class="publish-preview-heading">
          <div><strong>当前个人技能内容</strong><span>提交前核对，提交后以快照为准</span></div>
          <button v-if="sourceError" type="button" class="publish-link" @click="loadSource">重试</button>
        </div>
        <div v-if="sourceError" class="publish-error" role="alert">{{ sourceError }}</div>
        <div v-else-if="sourceLoading" class="publish-preview-empty">正在读取当前文件…</div>
        <div v-else class="publish-file-layout">
          <div class="publish-file-list" aria-label="当前文件">
            <button v-for="entry in sourceFiles" :key="entry.path" type="button" :disabled="entry.is_dir" :class="{ active: sourcePath === entry.path }" :style="{ paddingLeft: `${10 + entry.depth * 14}px` }" @click="selectSourceFile(entry.path)">{{ entry.name }}</button>
          </div>
          <pre class="publish-file-content">{{ sourceContent }}</pre>
        </div>
      </section>

      <section v-if="snapshotRequest" class="publish-preview publish-snapshot" aria-label="提交快照">
        <div class="publish-preview-heading">
          <div><strong>提交时的完整快照</strong><span>{{ snapshotRequest.name || skill.name }} · {{ statusLabel(snapshotRequest.status) }}</span></div>
          <button type="button" class="publish-link" @click="closeSnapshot">关闭快照</button>
        </div>
        <div v-if="snapshotError" class="publish-error" role="alert">{{ snapshotError }}</div>
        <div v-else-if="snapshotLoading" class="publish-preview-empty">正在读取提交快照…</div>
        <div v-else class="publish-file-layout">
          <div class="publish-file-list" aria-label="快照文件">
            <button v-for="entry in snapshotFiles" :key="entry.path" type="button" :disabled="entry.is_dir" :class="{ active: snapshotPath === entry.path }" :style="{ paddingLeft: `${10 + entry.depth * 14}px` }" @click="selectSnapshotFile(entry.path)">{{ entry.name }}</button>
          </div>
          <pre class="publish-file-content">{{ snapshotContent }}</pre>
        </div>
      </section>
    </div>
  </a-modal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import ShareConfigForm from '@/components/ShareConfigForm.vue'
import { skillApi, listMarketRequests, submitMarketSkill } from '@/apis/skill_api'
import { useUserStore } from '@/stores/user'
import { skillCategories, skillCategory } from './skillCategories.js'
import { canPublishPersonalSkill } from './skillManagementAccess.js'
import { sharedCopyStatusLabel } from './sharePublicationStatus.js'

const props = defineProps({
  skill: { type: Object, default: null },
  open: { type: Boolean, default: false }
})
const emit = defineEmits(['update:open', 'published'])
const userStore = useUserStore()
const isOwnSkill = computed(() => canPublishPersonalSkill(props.skill, userStore.uid))
const ownDepartmentOptions = computed(() => userStore.departmentId
  ? [{ id: userStore.departmentId, name: userStore.departmentName || '本部门' }]
  : [])

const channel = ref('shared')
const category = ref('other')
const reason = ref('')
const submitting = ref(false)
const submitError = ref('')
const shareConfigFormRef = ref(null)
const shareConfig = ref(defaultShareConfig())
const sharedRequests = ref([])
const marketRequests = ref([])
const historyLoading = ref(false)
const historyReady = ref(false)
const historyError = ref('')
const sourceFiles = ref([])
const sourcePath = ref('SKILL.md')
const sourceContent = ref('')
const sourceLoading = ref(false)
const sourceReady = ref(false)
const sourceError = ref('')
const snapshotRequest = ref(null)
const snapshotFiles = ref([])
const snapshotPath = ref('SKILL.md')
const snapshotContent = ref('')
const snapshotLoading = ref(false)
const snapshotError = ref('')
let panelSequence = 0
let sourceSequence = 0
let snapshotSequence = 0

/** 创建本人直接共享时的默认授权范围。 */
function defaultShareConfig() {
  return {
    version: 2,
    read_scope: { access_level: 'department', department_ids: userStore.departmentId ? [Number(userStore.departmentId)] : [], user_uids: [] },
    manage_scope: null
  }
}

/** 把接口错误转换为可操作的短句。 */
function errorText(error, fallback) {
  const detail = error?.response?.data?.detail
  return typeof detail === 'string' ? detail : fallback
}

/** 把目录树展成供键盘选择的文件列表。 */
function flattenFiles(entries, depth = 0) {
  return entries.flatMap((entry) => [
    { ...entry, depth },
    ...flattenFiles(entry.children || [], depth + 1)
  ])
}

/** 按提交时间找最近申请，并保留最近一次审核通过的版本。 */
function requestHistory(rows) {
  const own = rows
    .filter((item) => item.owner_uid === userStore.uid && item.personal_slug === props.skill?.slug)
    .sort((left, right) => Date.parse(right.created_at || 0) - Date.parse(left.created_at || 0))
  return { latest: own[0] || null, released: own.find((item) => item.status === 'approved') || null }
}

/** 显示共享审核确定的真实授权人群。 */
function sharedAudience(request) {
  const scope = request?.read_scope
  if (scope?.access_level === 'global') return '全公司'
  if (scope?.access_level === 'user') return (scope.user_uids || []).join('、') || '范围未确认'
  const ids = scope?.department_ids || request?.department_ids || []
  if (!ids.length) return '范围未确认'
  return ids.map((id) => Number(id) === Number(userStore.departmentId) ? userStore.departmentName || '本部门' : `部门 ${id}`).join('、')
}

/** 显示市场条目的审核范围。 */
function marketAudience(latest, released) {
  const scope = latest?.existing_market_scope || released?.read_scope?.access_level
  if (scope === 'global') return '全公司'
  const id = latest?.existing_market_department_id || released?.owner_department_id
  return Number(id) === Number(userStore.departmentId)
    ? userStore.departmentName || '本部门'
    : id ? `部门 ${id}` : '所属部门'
}

const sharedHistory = computed(() => {
  const history = requestHistory(sharedRequests.value)
  return { ...history, version: history.released?.published_version, audience: sharedAudience(history.released) }
})
const marketHistory = computed(() => {
  const history = requestHistory(marketRequests.value)
  return {
    ...history,
    version: history.latest?.current_market_version,
    audience: marketAudience(history.latest, history.released)
  }
})
const destinations = computed(() => [
  { key: 'shared', label: '直接共享', history: sharedHistory.value },
  { key: 'market', label: '上架市场', history: marketHistory.value }
])
const selectedHistory = computed(() => channel.value === 'shared' ? sharedHistory.value : marketHistory.value)
const canSubmit = computed(() => isOwnSkill.value && historyReady.value && sourceReady.value && !submitting.value && !!category.value && !!reason.value.trim() && selectedHistory.value.latest?.status !== 'pending')
const submitLabel = computed(() => submitting.value ? '发布中…' : channel.value === 'market' ? '提交市场审核' : userStore.isAdmin ? '确认直接共享' : '提交共享审核')

/** 返回审核状态的人类可读名称。 */
function statusLabel(status) {
  return { pending: '待审核', approved: '审核通过', rejected: '已驳回' }[status] || '暂无申请'
}

/** 格式化提交时间，仅展示已由服务端返回的值。 */
function formatTime(value) {
  if (!value) return '时间未知'
  const time = new Date(value)
  return Number.isNaN(time.getTime()) ? value : time.toLocaleString('zh-CN')
}

/** 仅读取当前技能在两个渠道的最新申请与最新发布。 */
async function loadHistory() {
  if (!isOwnSkill.value) return
  const sequence = panelSequence
  historyLoading.value = true
  historyReady.value = false
  historyError.value = ''
  try {
    const query = { mineOnly: true, slug: props.skill.slug, limit: 1 }
    const [shared, sharedApproved, market, marketApproved] = await Promise.all([
      skillApi.listSkillShareRequests(query),
      skillApi.listSkillShareRequests({ ...query, status: 'approved' }),
      listMarketRequests(query),
      listMarketRequests({ ...query, status: 'approved' })
    ])
    if (sequence !== panelSequence) return
    sharedRequests.value = [shared?.data?.items?.[0], sharedApproved?.data?.items?.[0]].filter((item, index, rows) => item && rows.findIndex((row) => row?.id === item.id) === index)
    marketRequests.value = [market?.data?.items?.[0], marketApproved?.data?.items?.[0]].filter((item, index, rows) => item && rows.findIndex((row) => row?.id === item.id) === index)
    historyReady.value = true
  } catch {
    if (sequence === panelSequence) historyError.value = '发布状态读取失败，请重试。'
  } finally {
    if (sequence === panelSequence) historyLoading.value = false
  }
}

/** 读取提交前的个人原件文件。 */
async function loadSource() {
  if (!isOwnSkill.value) return
  const sequence = ++sourceSequence
  sourceLoading.value = true
  sourceReady.value = false
  sourceError.value = ''
  sourcePath.value = 'SKILL.md'
  try {
    const [tree, file] = await Promise.all([
      skillApi.getPersonalSkillTree(props.skill.slug),
      skillApi.getPersonalSkillFile(props.skill.slug, 'SKILL.md')
    ])
    if (sequence !== sourceSequence) return
    sourceFiles.value = flattenFiles(tree?.data || [])
    sourceContent.value = file?.data?.content || ''
    sourceReady.value = true
  } catch (error) {
    if (sequence === sourceSequence) sourceError.value = errorText(error, '当前文件读取失败，请重试后再提交。')
  } finally {
    if (sequence === sourceSequence) sourceLoading.value = false
  }
}

/** 切换当前原件的文本预览。 */
async function selectSourceFile(path) {
  const sequence = ++sourceSequence
  sourcePath.value = path
  sourceError.value = ''
  sourceLoading.value = true
  try {
    const result = await skillApi.getPersonalSkillFile(props.skill.slug, path)
    if (sequence === sourceSequence) sourceContent.value = result?.data?.content || ''
  } catch (error) {
    if (sequence === sourceSequence) sourceError.value = errorText(error, '这个文件无法在线预览。')
  } finally {
    if (sequence === sourceSequence) sourceLoading.value = false
  }
}

/** 读取申请当时保存的不可变文件快照。 */
async function openSnapshot(request) {
  if (!request?.id) return
  const sequence = ++snapshotSequence
  snapshotRequest.value = request
  snapshotPath.value = 'SKILL.md'
  snapshotContent.value = ''
  snapshotError.value = ''
  snapshotLoading.value = true
  try {
    const [tree, file] = await Promise.all([
      skillApi.getSkillShareSnapshotTree(request.id),
      skillApi.getSkillShareSnapshot(request.id, 'SKILL.md')
    ])
    if (sequence !== snapshotSequence) return
    snapshotFiles.value = flattenFiles(tree?.data || [])
    snapshotContent.value = file?.data?.content || ''
  } catch (error) {
    if (sequence === snapshotSequence) snapshotError.value = errorText(error, '提交快照读取失败，请重试。')
  } finally {
    if (sequence === snapshotSequence) snapshotLoading.value = false
  }
}

/** 切换审核快照中的文件，不读取后来改动的个人原件。 */
async function selectSnapshotFile(path) {
  if (!snapshotRequest.value) return
  const sequence = ++snapshotSequence
  snapshotPath.value = path
  snapshotError.value = ''
  snapshotLoading.value = true
  try {
    const result = await skillApi.getSkillShareSnapshot(snapshotRequest.value.id, path)
    if (sequence === snapshotSequence) snapshotContent.value = result?.data?.content || ''
  } catch (error) {
    if (sequence === snapshotSequence) snapshotError.value = errorText(error, '这个快照文件无法在线预览。')
  } finally {
    if (sequence === snapshotSequence) snapshotLoading.value = false
  }
}

/** 关闭快照预览并丢弃过期读取。 */
function closeSnapshot() {
  snapshotSequence++
  snapshotRequest.value = null
}

/** 提交当前内容，并以服务端申请记录刷新界面。 */
async function submit() {
  if (!canSubmit.value) return
  const applicationReason = reason.value.trim()
  const selectedChannel = channel.value
  const selectedSkill = props.skill
  let readScope = null
  if (selectedChannel === 'shared' && userStore.isAdmin) {
    const validation = shareConfigFormRef.value?.validate?.()
    if (validation?.valid === false) {
      submitError.value = validation.message
      return
    }
    readScope = shareConfig.value?.read_scope
    const audience = readScope?.access_level === 'user' ? readScope.user_uids : readScope?.department_ids
    if (!audience?.length) {
      submitError.value = readScope?.access_level === 'user' ? '请选择可使用的人员' : '请选择发布部门'
      return
    }
  }
  submitting.value = true
  submitError.value = ''
  try {
    const result = selectedChannel === 'market'
      ? await submitMarketSkill(selectedSkill.slug, applicationReason, category.value)
      : userStore.isAdmin
        ? await skillApi.publishPersonalSkillDirectly(userStore.uid, selectedSkill.slug, readScope, applicationReason, category.value)
        : await skillApi.submitSkillShareRequest(selectedSkill.slug, applicationReason, category.value)
    reason.value = ''
    emit('published', { channel: selectedChannel, request: result?.data || null })
    await loadHistory()
    if (result?.data?.id) await openSnapshot(result.data)
  } catch (error) {
    submitError.value = errorText(error, '发布失败，请检查内容后重试。')
  } finally {
    submitting.value = false
  }
}

/** 关闭弹窗并使所有进行中的读取失效。 */
function close() {
  panelSequence++
  sourceSequence++
  closeSnapshot()
  emit('update:open', false)
}

watch(() => [props.open, props.skill?.slug, props.skill?.owner_uid, props.skill?.source_scope, props.skill?.sourceScope, userStore.uid], ([open]) => {
  panelSequence++
  sourceSequence++
  closeSnapshot()
  if (!open || !isOwnSkill.value) return
  channel.value = 'shared'
  category.value = skillCategory(props.skill.category)
  reason.value = ''
  submitError.value = ''
  shareConfig.value = defaultShareConfig()
  sharedRequests.value = []
  marketRequests.value = []
  historyReady.value = false
  loadHistory()
  loadSource()
}, { immediate: true })
</script>

<style scoped>
.personal-skill-publish { color: var(--gray-800); }
.publish-intro { margin: 0 0 18px; color: var(--gray-600); line-height: 1.6; }
.publish-destinations { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }
.publish-destination { min-width: 0; border: 1px solid var(--gray-200); border-radius: 10px; background: var(--gray-0); overflow: hidden; }
.publish-destination.selected { border-color: var(--main-color); }
.publish-choice { display: flex; flex-direction: column; gap: 5px; width: 100%; padding: 13px 15px; border: 0; background: var(--gray-25); color: var(--gray-600); text-align: left; cursor: pointer; }
.publish-destination.selected .publish-choice { background: var(--main-10); }
.publish-choice-heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%; }
.publish-choice-heading strong { color: var(--gray-900); font-size: 15px; }
.publish-choice-description { font-size: 12px; line-height: 1.5; }
.publish-choice:focus-visible, .publish-link:focus-visible, .publish-submit:focus-visible, .publish-file-list button:focus-visible { outline: 2px solid var(--main-color); outline-offset: 2px; }
.publish-history-body { min-height: 62px; padding: 9px 15px 12px; }
.publish-status { flex: none; border-radius: 999px; padding: 2px 8px; background: var(--gray-100); color: var(--gray-600); font-size: 12px; }
.publish-status.pending { background: var(--color-warning-50); color: var(--color-warning-700); }
.publish-status.approved { background: var(--color-success-50); color: var(--color-success-700); }
.publish-status.rejected { background: var(--color-error-50); color: var(--color-error-700); }
.publish-history-detail, .publish-history-note { margin: 5px 0 0; color: var(--gray-600); font-size: 12px; line-height: 1.6; }
.publish-history-note { color: var(--color-error-700); }
.publish-link { border: 0; padding: 0; background: transparent; color: var(--main-600); font: inherit; cursor: pointer; }
.publish-history-detail .publish-link { margin-left: 7px; }
.publish-form { border-top: 1px solid var(--gray-150); padding-top: 16px; }
.publish-field { display: grid; gap: 6px; margin-bottom: 14px; font-size: 13px; font-weight: 600; }
.publish-field em { color: var(--color-error-700); font-size: 12px; font-style: normal; font-weight: 400; }
.publish-field-title { margin: 8px 0; font-size: 13px; font-weight: 600; }
.publish-share-scope { margin-bottom: 14px; }
.publish-pending { margin: 0 0 12px; color: var(--color-warning-700); font-size: 12px; }
.publish-form-actions { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.publish-form-actions span { color: var(--gray-500); font-size: 12px; }
.publish-submit { flex: none; border: 1px solid var(--main-color); border-radius: 6px; padding: 8px 16px; background: var(--main-color); color: var(--gray-0); font-weight: 600; cursor: pointer; }
.publish-submit:disabled { border-color: var(--gray-200); background: var(--gray-100); color: var(--gray-400); cursor: not-allowed; }
.publish-error, .publish-unavailable { margin: 10px 0; color: var(--color-error-700); font-size: 13px; }
.publish-preview { margin-top: 18px; border: 1px solid var(--gray-150); border-radius: 8px; overflow: hidden; }
.publish-preview-heading { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 10px 13px; background: var(--gray-25); }
.publish-preview-heading div { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; }
.publish-preview-heading strong { font-size: 13px; }
.publish-preview-heading span { color: var(--gray-500); font-size: 12px; }
.publish-preview-empty { padding: 20px; color: var(--gray-500); font-size: 13px; }
.publish-file-layout { display: grid; grid-template-columns: minmax(130px, 28%) minmax(0, 1fr); min-height: 150px; max-height: 220px; }
.publish-file-list { display: flex; flex-direction: column; overflow: auto; border-right: 1px solid var(--gray-150); }
.publish-file-list button { border: 0; padding: 7px 10px; background: transparent; color: var(--gray-700); text-align: left; font-size: 12px; cursor: pointer; }
.publish-file-list button.active { background: var(--main-10); color: var(--main-700); }
.publish-file-list button:disabled { color: var(--gray-500); cursor: default; }
.publish-file-content { min-width: 0; overflow: auto; margin: 0; padding: 12px 14px; background: var(--gray-0); color: var(--gray-800); font-size: 12px; line-height: 1.5; white-space: pre-wrap; overflow-wrap: anywhere; }
.publish-snapshot { border-color: var(--main-color); }
@media (max-width: 640px) {
  .publish-destinations, .publish-file-layout { grid-template-columns: 1fr; }
  .publish-file-list { max-height: 100px; border-right: 0; border-bottom: 1px solid var(--gray-150); }
  .publish-form-actions { align-items: stretch; flex-direction: column; }
}
</style>
