<template>
  <section class="publication-records" aria-label="发布与审核记录">
    <a-spin :spinning="loading">
      <div v-if="error" class="publication-feedback" role="alert">
        <span>{{ error }}</span><a-button size="small" @click="loadRecords">重试</a-button>
      </div>
      <div v-else-if="!loading && !groups.length" class="publication-feedback">
        还没有发布申请记录。可从技能列表选择个人技能发起发布。
      </div>
      <div v-else class="publication-list">
        <article v-for="group in groups" :key="group.slug" :data-skill-slug="group.slug" class="publication-row">
          <div class="publication-identity">
            <div class="publication-name"><span class="publication-icon"><Sparkles :size="18" /></span><div><strong>{{ group.name }}</strong><small>{{ group.slug }}</small></div></div>
            <span v-if="group.sourceAvailable === false" class="publication-source-gone">原件已删除</span>
          </div>
          <div class="publication-channels">
            <div class="publication-channel">
              <span class="publication-channel-name">直接共享</span>
              <template v-if="group.shared.latest">
                <strong>{{ channelSummary(group.shared, 'shared') }}</strong>
                <small v-if="group.shared.approved">审核范围：{{ sharedScope(group.shared.approved, true) }}<span v-if="group.shared.version"> · v{{ group.shared.version }}</span></small>
              </template>
              <small v-else>未提交</small>
            </div>
            <div class="publication-channel">
              <span class="publication-channel-name">上架市场</span>
              <template v-if="group.market.latest">
                <strong>{{ channelSummary(group.market, 'market') }}</strong>
                <small v-if="group.market.approved">{{ group.market.version ? '当前范围：' : '最近审核范围：' }}{{ marketScope(group.market) }}<span v-if="group.market.version"> · v{{ group.market.version }}</span></small>
              </template>
              <small v-else>未提交</small>
            </div>
          </div>
          <div class="publication-row-end"><span>最近更新 {{ formatTime(group.lastActivityAt) }}</span><a-button size="small" @click="selectedSlug = group.slug">查看记录</a-button></div>
        </article>
      </div>
    </a-spin>

    <a-drawer :open="Boolean(selectedGroup)" :title="selectedGroup ? `${selectedGroup.name} · 发布历程` : '发布历程'" width="min(720px, 96vw)" @close="selectedSlug = ''">
      <div v-if="selectedGroup" class="publication-history" aria-label="技能审核历程">
        <p v-if="selectedGroup.sourceAvailable === false" class="publication-archive-note">个人原件已删除；以下记录和提交快照仍可查看。</p>
        <section v-for="channel in historyChannels" :key="channel.key" class="publication-history-channel">
          <div class="publication-history-heading"><h3>{{ channel.label }}</h3><span>{{ channel.requests.length }} 次提交</span></div>
          <p v-if="!channel.requests.length" class="publication-history-empty">暂无申请</p>
          <article v-for="request in channel.requests" :key="request.id" :data-request-id="request.id" class="publication-event">
            <div class="publication-event-heading"><strong>{{ statusLabel(request.status) }}</strong><time>{{ formatTime(request.created_at) }}</time></div>
            <p v-if="request.application_reason">提交说明：{{ request.application_reason }}</p>
            <p v-if="request.review_note">审核意见：{{ request.review_note }}</p>
            <p v-if="request.reviewed_at">审核时间：{{ formatTime(request.reviewed_at) }}</p>
            <p v-if="request.status === 'approved'">发布范围：{{ channel.key === 'shared' ? sharedScope(request) : requestMarketScope(request) }}<span v-if="request.published_version"> · v{{ request.published_version }}</span></p>
            <p v-if="channel.key === 'shared' && request.status === 'approved'">{{ sharedCopyStatusLabel(request.published_copy_status) }}</p>
            <a-button size="small" @click="openSnapshot(request)">查看提交快照</a-button>
          </article>
        </section>
      </div>
    </a-drawer>

    <a-modal :open="Boolean(snapshotRequest)" title="提交快照" :footer="null" :width="760" :body-style="{ maxHeight: 'calc(100vh - 160px)', overflowY: 'auto' }" @cancel="closeSnapshot">
      <section v-if="snapshotRequest" class="publication-snapshot" aria-label="历史提交快照">
        <div class="publication-snapshot-heading"><strong>{{ snapshotRequest.name || snapshotRequest.personal_slug }}</strong><span>{{ formatTime(snapshotRequest.created_at) }} · {{ statusLabel(snapshotRequest.status) }}</span></div>
        <div v-if="snapshotError" class="publication-feedback" role="alert">{{ snapshotError }}<a-button size="small" @click="openSnapshot(snapshotRequest)">重试</a-button></div>
        <a-spin v-else :spinning="snapshotLoading">
          <div class="publication-files">
            <nav aria-label="快照文件"><button v-for="entry in snapshotFiles" :key="entry.path" type="button" :disabled="entry.is_dir" :class="{ active: snapshotPath === entry.path }" :style="{ paddingLeft: `${12 + entry.depth * 12}px` }" @click="selectSnapshotFile(entry.path)">{{ entry.name }}</button></nav>
            <pre>{{ snapshotContent }}</pre>
          </div>
        </a-spin>
      </section>
    </a-modal>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { Sparkles } from '@lucide/vue'
import { listMarketRequests, skillApi } from '@/apis/skill_api'
import { useUserStore } from '@/stores/user'
import { buildPublicationGroups } from './publicationHistory.js'
import { sharedCopyStatusLabel } from './sharePublicationStatus.js'

const props = defineProps({ personalSkills: { type: Array, default: null } })
const userStore = useUserStore()
const sharedRequests = ref([])
const marketRequests = ref([])
const loading = ref(true)
const error = ref('')
const selectedSlug = ref('')
const snapshotRequest = ref(null)
const snapshotFiles = ref([])
const snapshotPath = ref('SKILL.md')
const snapshotContent = ref('')
const snapshotLoading = ref(false)
const snapshotError = ref('')
let snapshotSequence = 0

const groups = computed(() => buildPublicationGroups({
  sharedRequests: sharedRequests.value,
  marketRequests: marketRequests.value,
  personalSkills: props.personalSkills,
  ownerUid: userStore.uid
}))
const selectedGroup = computed(() => groups.value.find((group) => group.slug === selectedSlug.value) || null)
const historyChannels = computed(() => selectedGroup.value ? [
  { key: 'shared', label: '直接共享', requests: selectedGroup.value.shared.requests },
  { key: 'market', label: '上架市场', requests: selectedGroup.value.market.requests }
] : [])

/** 读取本人两个渠道的持久申请记录。 */
async function loadRecords() {
  loading.value = true
  error.value = ''
  try {
    const [shared, market] = await Promise.all([
      skillApi.listSkillShareRequests({ mineOnly: true }),
      listMarketRequests({ mineOnly: true })
    ])
    sharedRequests.value = shared?.data || []
    marketRequests.value = market?.data || []
  } catch {
    error.value = '发布记录读取失败，请重试。'
  } finally {
    loading.value = false
  }
}

/** 区分最近发布副本的状态与后续申请结果。 */
function channelSummary(channel, key) {
  if (!channel.latest) return '未提交'
  if (!channel.approved) return statusLabel(channel.latest.status)
  let current
  if (key === 'market') current = channel.version ? '已上架' : channel.approved.published_version ? '曾上架 · 当前未上架' : '审核通过 · 当前未上架'
  else current = { enabled: '最近发布副本已启用', disabled: '最近发布副本已停用', missing: '最近发布副本已失效' }[channel.copyStatus] || '审核通过'
  return channel.latest.status === 'approved' ? current : `${current} · 新申请${statusLabel(channel.latest.status)}`
}

/** 显示申请状态。 */
function statusLabel(status) {
  return { pending: '待审核', approved: '审核通过', rejected: '已驳回' }[status] || '状态未知'
}

/** 显示服务端记录的时间。 */
function formatTime(value) {
  if (!value) return '时间未知'
  const time = new Date(value)
  return Number.isNaN(time.getTime()) ? value : time.toLocaleString('zh-CN')
}

/** 显示共享副本审核确定的授权范围。 */
function sharedScope(request, compact = false) {
  const scope = request?.read_scope
  if (scope?.access_level === 'global') return '全公司'
  if (scope?.access_level === 'user') {
    const users = scope.user_uids || []
    if (!users.length) return '指定成员（范围未确认）'
    return compact ? `指定成员（${users.length} 人）` : `指定成员：${users.join('、')}`
  }
  const ids = scope?.department_ids?.length ? scope.department_ids : request?.department_ids || []
  if (!ids.length) return '发布范围未确认'
  return ids.length === 1 && Number(ids[0]) === Number(userStore.departmentId) ? '本部门' : ids.map((id) => `部门 ${id}`).join('、')
}

/** 显示市场当前已上架的可见范围。 */
function marketScope(channel) {
  if (channel.scope === 'global') return '全公司'
  if (channel.scope === 'department') return Number(channel.departmentId) === Number(userStore.departmentId) ? '本部门' : channel.departmentId ? `部门 ${channel.departmentId}` : '所属部门'
  return requestMarketScope(channel.approved)
}

/** 显示单次市场申请的审核范围。 */
function requestMarketScope(request) {
  const scope = request?.read_scope?.access_level || request?.existing_market_scope
  if (scope === 'global') return '全公司'
  if (scope === 'department') {
    const id = request?.read_scope?.department_ids?.[0] || request?.existing_market_department_id || request?.owner_department_id
    return Number(id) === Number(userStore.departmentId) ? '本部门' : id ? `部门 ${id}` : '所属部门'
  }
  return '发布范围未确认'
}

/** 展开文件树供快照预览选择。 */
function flattenFiles(entries, depth = 0) {
  return entries.flatMap((entry) => [
    { ...entry, depth },
    ...flattenFiles(entry.children || [], depth + 1)
  ])
}

/** 读取指定申请保存的独立快照。 */
async function openSnapshot(request) {
  if (!request?.id) return
  const sequence = ++snapshotSequence
  snapshotRequest.value = request
  snapshotPath.value = 'SKILL.md'
  snapshotFiles.value = []
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
  } catch {
    if (sequence === snapshotSequence) snapshotError.value = '提交快照读取失败，请重试。'
  } finally {
    if (sequence === snapshotSequence) snapshotLoading.value = false
  }
}

/** 切换历史快照中的文本文件。 */
async function selectSnapshotFile(path) {
  if (!snapshotRequest.value) return
  const sequence = ++snapshotSequence
  snapshotPath.value = path
  snapshotError.value = ''
  snapshotLoading.value = true
  try {
    const result = await skillApi.getSkillShareSnapshot(snapshotRequest.value.id, path)
    if (sequence === snapshotSequence) snapshotContent.value = result?.data?.content || ''
  } catch {
    if (sequence === snapshotSequence) snapshotError.value = '这个快照文件无法在线预览。'
  } finally {
    if (sequence === snapshotSequence) snapshotLoading.value = false
  }
}

/** 关闭预览并忽略过期读取结果。 */
function closeSnapshot() {
  snapshotSequence++
  snapshotRequest.value = null
}

onMounted(loadRecords)
</script>

<style scoped>
.publication-records { min-height: 120px; }
.publication-list { display: grid; gap: 12px; }
.publication-row { display: grid; grid-template-columns: minmax(150px, 1.15fr) minmax(280px, 2fr) auto; align-items: center; gap: 18px; padding: 17px 20px; border: 1px solid var(--gray-150); border-radius: 12px; background: var(--gray-0); }
.publication-identity, .publication-channel { min-width: 0; }
.publication-name { display: flex; align-items: center; gap: 12px; min-width: 0; }
.publication-name div { display: grid; gap: 3px; min-width: 0; }
.publication-name strong { overflow: hidden; color: var(--gray-900); text-overflow: ellipsis; white-space: nowrap; }
.publication-name small, .publication-channel small, .publication-row-end span { color: var(--gray-500); font-size: 12px; }
.publication-icon { display: grid; flex: none; place-items: center; width: 38px; height: 38px; border-radius: 10px; background: var(--main-50); color: var(--main-600); }
.publication-source-gone { display: inline-block; margin: 8px 0 0 50px; padding: 2px 7px; border-radius: 5px; background: var(--color-warning-50); color: var(--color-warning-700); font-size: 12px; }
.publication-channels { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.publication-channel { display: grid; align-content: center; gap: 3px; padding-left: 13px; border-left: 1px solid var(--gray-150); }
.publication-channel-name { color: var(--gray-600); font-size: 12px; }
.publication-channel strong { color: var(--gray-900); font-size: 13px; font-weight: 600; }
.publication-row-end { display: grid; justify-items: end; gap: 8px; }
.publication-feedback { display: flex; align-items: center; gap: 12px; padding: 20px; border: 1px solid var(--gray-150); border-radius: 10px; color: var(--gray-600); font-size: 13px; }
.publication-archive-note { padding: 11px 13px; border-radius: 8px; background: var(--color-warning-50); color: var(--color-warning-700); font-size: 13px; }
.publication-history-channel { margin-top: 24px; }
.publication-history-heading { display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--gray-150); }
.publication-history-heading h3 { margin: 0 0 10px; color: var(--gray-900); font-size: 16px; }
.publication-history-heading span, .publication-history-empty { color: var(--gray-500); font-size: 12px; }
.publication-event { margin: 14px 0; padding: 13px 15px; border: 1px solid var(--gray-150); border-radius: 9px; }
.publication-event-heading { display: flex; justify-content: space-between; gap: 12px; color: var(--gray-900); }
.publication-event-heading time { color: var(--gray-500); font-size: 12px; }
.publication-event p { margin: 7px 0; color: var(--gray-600); font-size: 13px; line-height: 1.5; }
.publication-files { display: grid; grid-template-columns: minmax(140px, 27%) minmax(0, 1fr); min-height: 300px; margin-top: 14px; border: 1px solid var(--gray-150); border-radius: 8px; overflow: hidden; }
.publication-files nav { display: flex; flex-direction: column; border-right: 1px solid var(--gray-150); background: var(--gray-25); }
.publication-files nav button { border: 0; padding: 9px 12px; background: transparent; color: var(--gray-700); text-align: left; cursor: pointer; }
.publication-files nav button.active { background: var(--main-50); color: var(--main-700); }
.publication-files nav button:disabled { color: var(--gray-400); cursor: default; }
.publication-files pre { margin: 0; padding: 16px; overflow: auto; color: var(--gray-800); font-family: monospace; font-size: 12px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
.publication-snapshot-heading { display: flex; justify-content: space-between; gap: 12px; color: var(--gray-900); }
.publication-snapshot-heading span { color: var(--gray-500); font-size: 12px; }
@media (max-width: 950px) { .publication-row { grid-template-columns: minmax(0, 1fr) auto; } .publication-channels { grid-column: 1 / -1; grid-row: 2; } }
@media (max-width: 600px) { .publication-row { grid-template-columns: 1fr; } .publication-row-end { display: flex; align-items: center; justify-content: space-between; } .publication-channels { grid-template-columns: 1fr 1fr; } .publication-files { grid-template-columns: 1fr; } .publication-files nav { border-right: 0; border-bottom: 1px solid var(--gray-150); } }
</style>
