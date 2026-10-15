<template>
  <section class="skill-market">
    <div class="market-heading">
      <div>
        <span class="market-eyebrow">TEAM LIBRARY</span>
        <h2>Skill 市场</h2>
        <p>发现团队已经验证的工作方法，安装后在 Agent 中使用。</p>
      </div>
      <div class="market-heading-actions">
        <a-button v-if="userStore.isAdmin" @click="openRequests">审核申请</a-button>
        <a-button type="primary" @click="openSubmit">发布 Skill</a-button>
      </div>
    </div>

    <div class="market-toolbar">
      <a-input-search
        v-model:value="query"
        placeholder="搜索名称、描述或标识"
        allow-clear
        class="market-search"
        @search="refresh"
        @pressEnter="refresh"
      />
      <a-segmented v-model:value="sort" :options="sortOptions" @change="refresh" />
      <a-checkbox v-model:checked="installedOnly" @change="refresh">只看已安装</a-checkbox>
    </div>

    <a-alert v-if="error" type="error" :message="error" show-icon class="market-error">
      <template #action><a-button size="small" @click="refresh">重试</a-button></template>
    </a-alert>
    <a-spin :spinning="loading">
      <div v-if="!cards.length && !loading" class="market-empty">
        <Store :size="28" />
        <strong>{{ installedOnly ? '还没有安装 Skill' : '没有找到匹配的 Skill' }}</strong>
        <span>{{ installedOnly ? '从市场中选择一个 Skill 安装，便可添加到 Agent。' : '换一个关键词，或邀请同事发布第一个 Skill。' }}</span>
      </div>
      <div v-else class="market-grid">
        <article v-for="item in cards" :key="item.slug" class="market-card">
          <div class="market-card-top">
            <div class="market-icon"><Sparkles :size="20" /></div>
            <span class="market-scope">{{ item.scope === 'global' ? '全公司' : '本部门' }}</span>
          </div>
          <button type="button" class="market-card-title" @click="openDetail(item.slug)">{{ item.name }}</button>
          <span class="market-card-slug">{{ item.slug }}</span>
          <p>{{ item.description }}</p>
          <div class="market-card-author">由 {{ item.owner_name }} 发布 · v{{ item.latest_version }}</div>
          <div class="market-card-bottom">
            <button
              type="button"
              class="market-like"
              :class="{ liked: item.liked }"
              :aria-label="item.liked ? '取消点赞' : '点赞'"
              :disabled="busySlug === item.slug"
              @click="like(item)"
            ><Heart :size="16" :fill="item.liked ? 'currentColor' : 'none'" /> {{ item.likes_count }}</button>
            <span class="market-install-count"><Download :size="15" /> {{ item.install_count }} 人安装</span>
            <a-button size="small" :loading="busySlug === item.slug" @click="act(item)">
              {{ item.upgrade_available ? '升级' : item.installed_version ? '查看' : '安装' }}
            </a-button>
          </div>
        </article>
      </div>
      <div v-if="hasMore && !loading" class="market-more"><a-button :loading="loadingMore" @click="loadMore">加载更多</a-button></div>
    </a-spin>

    <a-modal v-model:open="detailOpen" :footer="null" width="760px" title="Skill 详情">
      <a-spin :spinning="detailLoading">
        <div v-if="detail" class="market-detail">
          <div class="market-detail-heading">
            <div><h3>{{ detail.name }}</h3><span>{{ detail.slug }} · {{ detail.owner_name }}</span></div>
            <a-tag>{{ detail.scope === 'global' ? '全公司' : '本部门' }}</a-tag>
          </div>
          <p>{{ detail.description }}</p>
          <div class="market-detail-meta">
            <span>当前版本 v{{ detail.latest_version }}</span>
            <span>已安装 {{ detail.installed_version ? `v${detail.installed_version}` : '—' }}</span>
            <span>{{ detail.install_count }} 人安装</span>
          </div>
          <a-alert v-if="detail.upgrade_available" type="info" show-icon message="新版本已发布。升级需由你手动确认。" />
          <div v-if="dependencyLabels.length" class="market-dependencies">
            <strong>依赖</strong><a-tag v-for="dependency in dependencyLabels" :key="dependency">{{ dependency }}</a-tag>
          </div>
          <div class="market-files">
            <div class="market-files-list">
              <strong>发布文件</strong>
              <button v-for="file in files" :key="file.path" type="button" :class="{ active: file.path === selectedFile }" @click="selectFile(file.path)">{{ file.path }}</button>
            </div>
            <div class="market-file-content">
              <a-spin :spinning="fileLoading"><MarkdownPreview v-if="fileContent" :content="fileContent" :compact="true" /><a-empty v-else description="选择文件预览" /></a-spin>
            </div>
          </div>
          <div class="market-detail-actions">
            <a-button v-if="detail.installed_version" @click="router.push('/agent-manage')">在 Agent 中使用</a-button>
            <a-button type="primary" :loading="busySlug === detail.slug" :disabled="detail.installed_version && !detail.upgrade_available" @click="act(detail)">{{ detail.upgrade_available ? '升级到最新版本' : detail.installed_version ? '已安装' : '安装 Skill' }}</a-button>
          </div>
        </div>
      </a-spin>
    </a-modal>

    <a-modal v-model:open="submitOpen" title="申请发布 Skill" ok-text="提交审核" :confirm-loading="submitLoading" @ok="submit">
      <p>从你的个人 Skill 中选择一项。提交时会保存不可变快照，管理员审核该快照。</p>
      <a-select v-model:value="submitSlug" placeholder="选择个人 Skill" class="market-submit-select" :options="personalOptions" />
      <a-textarea v-model:value="submitReason" placeholder="说明使用场景和适用人群" :rows="4" :maxlength="1000" show-count />
    </a-modal>

    <a-modal v-model:open="requestsOpen" title="市场审核申请" :footer="null" width="760px">
      <a-spin :spinning="requestsLoading">
        <a-empty v-if="!requests.length" description="暂无申请" />
        <div v-for="request in requests" :key="request.id" class="market-request">
          <div class="market-request-title"><strong>{{ request.name }}</strong><a-tag>{{ request.status === 'pending' ? '待审核' : request.status === 'approved' ? '已发布' : '已拒绝' }}</a-tag></div>
          <p>{{ request.application_reason }}</p>
          <div class="market-request-meta">{{ request.owner_uid }} · {{ request.personal_slug }} <span v-if="request.review_note">· {{ request.review_note }}</span></div>
          <div v-if="request.status === 'pending' && userStore.isAdmin" class="market-request-actions">
            <a-button size="small" @click="previewRequest(request)">查看提交快照</a-button>
            <a-button size="small" type="primary" @click="openReview(request, true)">批准</a-button>
            <a-button size="small" danger @click="openReview(request, false)">拒绝</a-button>
          </div>
        </div>
      </a-spin>
    </a-modal>

    <a-modal v-model:open="snapshotOpen" title="提交快照" :footer="null" width="740px">
      <a-button class="market-snapshot-download" @click="downloadSnapshot">下载完整快照（含附件）</a-button>
      <div class="market-files"><div class="market-files-list"><strong>文件</strong><button v-for="file in snapshotFiles" :key="file.path" type="button" :class="{ active: file.path === snapshotSelected }" @click="selectSnapshotFile(file.path)">{{ file.path }}</button></div><div class="market-file-content"><a-spin :spinning="snapshotLoading"><MarkdownPreview v-if="snapshotContent" :content="snapshotContent" :compact="true" /><a-empty v-else :description="snapshotError || '选择文件预览'" /></a-spin></div></div>
    </a-modal>

    <a-modal v-model:open="reviewOpen" :title="reviewApproved ? '批准发布' : '拒绝申请'" :ok-text="reviewApproved ? '确认发布' : '确认拒绝'" :confirm-loading="reviewLoading" @ok="review">
      <p>{{ reviewRequest?.name }} · {{ reviewRequest?.personal_slug }}</p>
      <a-radio-group v-if="reviewApproved" v-model:value="reviewScope" class="market-review-scope">
        <a-radio value="department">本部门</a-radio><a-radio v-if="userStore.isSuperAdmin" value="global">全公司</a-radio>
      </a-radio-group>
      <a-textarea v-model:value="reviewNote" placeholder="审核意见（可选）" :rows="3" :maxlength="2000" />
    </a-modal>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { Download, Heart, Sparkles, Store } from '@lucide/vue'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import { useUserStore } from '@/stores/user'
import {
  getMarketSkill,
  getMarketSkillFile,
  getMarketSkillTree,
  installMarketSkill,
  listMarketRequests,
  listMarketSkills,
  listSkillCards,
  getSkillShareSnapshotTree,
  getSkillShareSnapshot,
  exportSkillShareSnapshot,
  reviewMarketSkill,
  submitMarketSkill,
  toggleMarketSkillLike,
  upgradeMarketSkill
} from '@/apis/skill_api'

const router = useRouter()
const userStore = useUserStore()
const sortOptions = [
  { label: '最新发布', value: 'recent' },
  { label: '安装排行', value: 'installs' },
  { label: '点赞排行', value: 'likes' }
]
const query = ref('')
const sort = ref('recent')
const installedOnly = ref(false)
const cards = ref([])
const loading = ref(false)
const loadingMore = ref(false)
const hasMore = ref(false)
const pageSize = 100
let requestSequence = 0
const error = ref('')
const busySlug = ref('')
const detailOpen = ref(false)
const detailLoading = ref(false)
const detail = ref(null)
const files = ref([])
const selectedFile = ref('')
const fileContent = ref('')
const fileLoading = ref(false)
const dependencyLabels = computed(() => detail.value ? [
  ...detail.value.tool_dependencies.map((item) => `工具 · ${item}`),
  ...detail.value.mcp_dependencies.map((item) => `MCP · ${item}`),
  ...detail.value.skill_dependencies.map((item) => `Skill · ${item}`)
] : [])

const submitOpen = ref(false)
const submitLoading = ref(false)
const submitSlug = ref()
const submitReason = ref('')
const personalOptions = ref([])

const requestsOpen = ref(false)
const requestsLoading = ref(false)
const requests = ref([])
const reviewOpen = ref(false)
const reviewLoading = ref(false)
const reviewApproved = ref(true)
const reviewRequest = ref(null)
const reviewScope = ref('department')
const reviewNote = ref('')
const snapshotOpen = ref(false)
const snapshotLoading = ref(false)
const snapshotFiles = ref([])
const snapshotSelected = ref('')
const snapshotContent = ref('')
const snapshotError = ref('')
const snapshotRequestId = ref('')

const errorText = (failure) => failure?.response?.data?.detail || failure?.message || '操作失败，请稍后重试'
const flattenFiles = (nodes) => nodes.flatMap((node) => node.is_dir ? flattenFiles(node.children || []) : [node])

async function refresh() {
  const requestId = ++requestSequence
  loading.value = true
  loadingMore.value = false
  error.value = ''
  try {
    const items = (await listMarketSkills({ query: query.value, sort: sort.value, installedOnly: installedOnly.value, limit: pageSize }))?.data || []
    if (requestId !== requestSequence) return
    cards.value = items
    hasMore.value = items.length === pageSize
  } catch (failure) {
    if (requestId === requestSequence) error.value = errorText(failure)
  } finally {
    if (requestId === requestSequence) loading.value = false
  }
}

async function loadMore() {
  if (!hasMore.value || loadingMore.value || loading.value) return
  const requestId = requestSequence
  loadingMore.value = true
  try {
    const items = (await listMarketSkills({ query: query.value, sort: sort.value, installedOnly: installedOnly.value, offset: cards.value.length, limit: pageSize }))?.data || []
    if (requestId !== requestSequence) return
    cards.value = [...cards.value, ...items]
    hasMore.value = items.length === pageSize
  } catch (failure) {
    if (requestId === requestSequence) message.error(errorText(failure))
  } finally {
    if (requestId === requestSequence) loadingMore.value = false
  }
}

async function openDetail(slug) {
  detailOpen.value = true
  detailLoading.value = true
  detail.value = null
  files.value = []
  fileContent.value = ''
  try {
    const [info, tree] = await Promise.all([getMarketSkill(slug), getMarketSkillTree(slug)])
    detail.value = info.data
    files.value = flattenFiles(tree.data || [])
    await selectFile('SKILL.md')
  } catch (failure) {
    message.error(errorText(failure))
  } finally {
    detailLoading.value = false
  }
}

async function selectFile(path) {
  selectedFile.value = path
  fileLoading.value = true
  try {
    fileContent.value = (await getMarketSkillFile(detail.value.slug, path))?.data?.content || ''
  } catch (failure) {
    fileContent.value = ''
    message.error(errorText(failure))
  } finally {
    fileLoading.value = false
  }
}

async function act(item) {
  if (item.installed_version && !item.upgrade_available) return openDetail(item.slug)
  busySlug.value = item.slug
  try {
    const result = item.installed_version ? await upgradeMarketSkill(item.slug) : await installMarketSkill(item.slug)
    cards.value = cards.value.map((card) => card.slug === item.slug ? result.data : card)
    if (detail.value?.slug === item.slug) detail.value = result.data
    message.success(item.installed_version ? '已升级到最新审核版本' : '已安装，可在 Agent 中使用')
  } catch (failure) {
    message.error(errorText(failure))
  } finally {
    busySlug.value = ''
  }
}

async function like(item) {
  busySlug.value = item.slug
  try {
    const result = await toggleMarketSkillLike(item.slug)
    cards.value = cards.value.map((card) => card.slug === item.slug ? result.data : card)
    if (detail.value?.slug === item.slug) detail.value = result.data
  } catch (failure) {
    message.error(errorText(failure))
  } finally {
    busySlug.value = ''
  }
}

async function openSubmit() {
  submitOpen.value = true
  try {
    const result = await listSkillCards()
    personalOptions.value = (result?.data || []).filter((item) => item.source_scope === 'personal').map((item) => ({ label: `${item.name} · ${item.slug}`, value: item.slug }))
  } catch (failure) {
    message.error(errorText(failure))
  }
}

async function submit() {
  if (!submitSlug.value || !submitReason.value.trim()) return message.warning('请选择个人 Skill 并填写使用场景')
  submitLoading.value = true
  try {
    await submitMarketSkill(submitSlug.value, submitReason.value.trim())
    submitOpen.value = false
    submitReason.value = ''
    message.success('已提交审核')
  } catch (failure) {
    message.error(errorText(failure))
  } finally {
    submitLoading.value = false
  }
}

async function openRequests() {
  requestsOpen.value = true
  requestsLoading.value = true
  try {
    requests.value = (await listMarketRequests())?.data || []
  } catch (failure) {
    message.error(errorText(failure))
  } finally {
    requestsLoading.value = false
  }
}

async function previewRequest(request) {
  snapshotOpen.value = true
  snapshotLoading.value = true
  snapshotRequestId.value = request.id
  snapshotContent.value = ''
  snapshotError.value = ''
  try {
    snapshotFiles.value = flattenFiles((await getSkillShareSnapshotTree(request.id))?.data || [])
    await selectSnapshotFile('SKILL.md')
  } catch (failure) {
    message.error(errorText(failure))
  } finally {
    snapshotLoading.value = false
  }
}

async function selectSnapshotFile(path) {
  snapshotSelected.value = path
  snapshotLoading.value = true
  snapshotError.value = ''
  try {
    snapshotContent.value = (await getSkillShareSnapshot(snapshotRequestId.value, path))?.data?.content || ''
  } catch {
    snapshotContent.value = ''
    snapshotError.value = '此文件无法在线预览，请下载完整快照检查'
  } finally {
    snapshotLoading.value = false
  }
}

async function downloadSnapshot() {
  if (!snapshotRequestId.value) return
  try {
    const response = await exportSkillShareSnapshot(snapshotRequestId.value)
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = `skill-market-${snapshotRequestId.value}.zip`
    link.click()
    URL.revokeObjectURL(url)
  } catch (failure) {
    message.error(errorText(failure))
  }
}

function openReview(request, approved) {
  reviewRequest.value = request
  reviewApproved.value = approved
  reviewScope.value = 'department'
  reviewNote.value = ''
  reviewOpen.value = true
}

async function review() {
  reviewLoading.value = true
  try {
    await reviewMarketSkill(reviewRequest.value.id, reviewApproved.value, reviewScope.value, reviewNote.value)
    reviewOpen.value = false
    await Promise.all([openRequests(), refresh()])
    message.success(reviewApproved.value ? '已发布到 Skill 市场' : '已拒绝申请')
  } catch (failure) {
    message.error(errorText(failure))
  } finally {
    reviewLoading.value = false
  }
}

onMounted(refresh)
</script>

<style scoped lang="less">
.skill-market { padding: 24px var(--page-padding) 48px; color: var(--color-text); }
.market-heading { display: flex; align-items: end; justify-content: space-between; gap: 20px; margin-bottom: 28px; }
.market-heading h2 { margin: 4px 0 7px; font-size: 28px; font-weight: 650; letter-spacing: -.04em; }
.market-heading p { margin: 0; color: var(--color-text-secondary); }
.market-eyebrow { color: var(--color-primary-500); font-size: 11px; font-weight: 750; letter-spacing: .14em; }
.market-heading-actions, .market-toolbar { display: flex; align-items: center; gap: 10px; }
.market-toolbar { flex-wrap: wrap; margin-bottom: 22px; }
.market-search { width: min(340px, 100%); }
.market-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; }
.market-more { display: flex; justify-content: center; margin-top: 22px; }
.market-card { min-height: 242px; display: flex; flex-direction: column; padding: 20px; background: var(--color-bg-container); border: 1px solid var(--gray-150); border-radius: 14px; transition: border-color .2s, transform .2s; }
.market-card:hover { border-color: var(--color-primary-500); transform: translateY(-2px); }
.market-card-top, .market-card-bottom, .market-detail-heading, .market-detail-actions, .market-request-title, .market-request-actions { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.market-icon { width: 38px; height: 38px; display: grid; place-items: center; border-radius: 11px; color: var(--color-primary-700); background: var(--color-primary-50); }
.market-scope { font-size: 12px; color: var(--color-text-secondary); }
.market-card-title { display: block; margin: 16px 0 1px; padding: 0; border: 0; background: none; color: inherit; text-align: left; font-size: 17px; font-weight: 650; cursor: pointer; }
.market-card-title:hover { color: var(--color-primary-500); }
.market-card-slug, .market-card-author, .market-request-meta { color: var(--color-text-tertiary); font-size: 12px; }
.market-card p { margin: 13px 0; color: var(--color-text-secondary); line-height: 1.55; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; }
.market-card-author { margin-top: auto; }
.market-card-bottom { border-top: 1px solid var(--gray-100); margin-top: 14px; padding-top: 12px; }
.market-like { display: flex; align-items: center; gap: 5px; border: 0; background: none; color: var(--color-text-secondary); cursor: pointer; }
.market-like.liked { color: var(--color-error-500); }
.market-install-count { display: flex; align-items: center; gap: 5px; color: var(--color-text-secondary); font-size: 12px; }
.market-empty { display: grid; justify-items: center; gap: 8px; padding: 80px 16px; color: var(--color-text-secondary); }
.market-empty strong { color: var(--color-text); }
.market-error { margin-bottom: 16px; }
.market-detail-heading h3 { margin: 0 0 3px; font-size: 22px; }
.market-detail-heading span, .market-detail-meta { color: var(--color-text-secondary); font-size: 13px; }
.market-detail > p { margin: 18px 0; line-height: 1.6; }
.market-detail-meta, .market-dependencies { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin: 16px 0; }
.market-files { display: grid; grid-template-columns: 180px minmax(0, 1fr); min-height: 260px; max-height: 400px; overflow: hidden; border: 1px solid var(--gray-150); border-radius: 10px; margin-top: 18px; }
.market-files-list { overflow: auto; display: flex; flex-direction: column; padding: 10px; border-right: 1px solid var(--gray-150); }
.market-files-list strong { padding: 7px; }
.market-files-list button { border: 0; background: none; text-align: left; padding: 7px; border-radius: 6px; color: var(--color-text-secondary); cursor: pointer; overflow-wrap: anywhere; }
.market-files-list button.active { color: var(--color-primary-700); background: var(--color-primary-50); }
.market-file-content { overflow: auto; padding: 16px; }
.market-detail-actions { justify-content: flex-end; margin-top: 20px; }
.market-submit-select { display: block; width: 100%; margin: 18px 0; }
.market-request { padding: 16px 0; border-bottom: 1px solid var(--gray-150); }
.market-request p { margin: 8px 0; color: var(--color-text-secondary); }
.market-request-actions { justify-content: flex-end; margin-top: 10px; }
.market-review-scope { display: block; margin: 18px 0; }
@media (max-width: 680px) { .market-heading { align-items: start; flex-direction: column; } .market-files { grid-template-columns: 1fr; } .market-files-list { max-height: 120px; border-right: 0; border-bottom: 1px solid var(--gray-150); } }
</style>
