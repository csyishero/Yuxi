<template>
  <section class="skill-market">
    <div class="skill-hub-layout" :class="{ 'personal-collapsed': !personalOpen }">
      <main class="skill-hub-main">
        <div class="market-heading">
          <div class="market-heading-copy">
            <div class="market-heading-icon"><Blocks :size="23" /></div>
            <div>
              <h2>技能</h2>
              <p>找到适合任务的能力，安装后在 Agent 中使用。</p>
            </div>
          </div>
          <div class="market-heading-actions">
            <a-button @click="managementOpen = true">管理与上传</a-button>
            <a-button type="primary" @click="openSubmit">发布技能</a-button>
            <a-button class="personal-toggle" :aria-expanded="personalOpen" aria-controls="skill-personal-space" @click="personalOpen = !personalOpen">
              <PanelRightClose v-if="personalOpen" :size="16" />
              <PanelRightOpen v-else :size="16" />
              {{ personalOpen ? '收起个人空间' : '展开个人空间' }}
            </a-button>
          </div>
        </div>

        <div class="market-toolbar">
          <a-input-search
            v-model:value="query"
            placeholder="搜索技能名称或用途"
            allow-clear
            class="market-search"
            @search="refresh"
            @pressEnter="refresh"
          />
        </div>

        <nav class="market-filters" aria-label="技能来源筛选">
          <button
            v-for="filter in sourceFilters"
            :key="filter.key"
            type="button"
            :class="{ active: sourceFilter === filter.key }"
            :aria-current="sourceFilter === filter.key ? 'page' : undefined"
            @click="selectSourceFilter(filter.key)"
          >{{ filter.label }}</button>
        </nav>

        <div v-if="sourceFilter === 'market'" class="market-subtools">
          <span>市场技能按发布时间展示</span>
          <a-button @click="openRankings"><ChartNoAxesColumn :size="16" /> 查看排行榜</a-button>
        </div>

        <a-alert v-if="error" type="error" :message="error" show-icon class="market-error">
          <template #action><a-button size="small" @click="refresh">重试</a-button></template>
        </a-alert>
        <a-spin :spinning="loading || legacyLoading">
          <div v-if="!visibleCards.length && !loading && !legacyLoading" class="market-empty">
            <Store :size="28" />
            <strong>{{ query ? '没有找到匹配的技能' : '这里还没有技能' }}</strong>
            <span>{{ query ? '换个关键词试试。' : '可以上传个人技能，或请同事发布到市场。' }}</span>
          </div>
          <div v-else class="market-grid">
            <article v-for="item in visibleCards" :key="`${item.kind}:${item.source_type || ''}:${item.owner_uid || ''}:${item.slug}`" class="market-card">
              <div class="market-card-top">
                <div class="market-icon"><Sparkles v-if="item.kind === 'market'" :size="20" /><BookOpen v-else :size="20" /></div>
                <span class="market-scope">{{ sourceLabel(item) }}</span>
              </div>
              <button type="button" class="market-card-title" @click="openCard(item)">{{ item.name }}</button>
              <p>{{ item.description || '暂无描述' }}</p>
              <div class="market-card-author">
                {{ item.kind === 'market' ? `由 ${item.owner_name} 发布 · v${item.latest_version}` : item.source_type === 'builtin' ? '系统内置 · 可直接使用' : '已共享 · 可直接使用' }}
              </div>
              <div class="market-card-bottom">
                <template v-if="item.kind === 'market'">
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
                </template>
                <template v-else>
                  <span class="market-available">已可用</span>
                  <a-button size="small" @click="openCard(item)">查看技能</a-button>
                </template>
              </div>
            </article>
          </div>
          <div v-if="hasMore && !loading && ['all', 'market', 'installed'].includes(sourceFilter)" class="market-more"><a-button :loading="loadingMore" @click="loadMore">加载更多</a-button></div>
        </a-spin>
      </main>

      <aside v-show="personalOpen" id="skill-personal-space" class="personal-space" aria-label="个人空间">
        <div class="personal-space-heading"><h3>个人空间</h3><span>{{ personalItems.length }} 项技能</span></div>
        <div class="personal-tabs" role="tablist" aria-label="个人空间视图">
          <button type="button" role="tab" :aria-selected="personalTab === 'skills'" :class="{ active: personalTab === 'skills' }" @click="personalTab = 'skills'">我的技能</button>
          <button type="button" role="tab" :aria-selected="personalTab === 'requests'" :class="{ active: personalTab === 'requests' }" @click="personalTab = 'requests'; fetchRequests()">我的审批<span v-if="pendingRequestCount" class="personal-pending">{{ pendingRequestCount }}</span></button>
        </div>
        <template v-if="personalTab === 'skills'">
          <a-input v-model:value="personalQuery" allow-clear placeholder="搜索我的技能" class="personal-search" />
          <a-spin :spinning="legacyLoading || installedLoading">
            <div v-if="!filteredPersonalItems.length && !legacyLoading && !installedLoading" class="personal-empty">暂无个人技能。可先上传，再申请发布。</div>
            <div v-else class="personal-list">
              <button v-for="item in filteredPersonalItems" :key="`${item.kind}:${item.slug}`" type="button" class="personal-item" @click="openPersonalItem(item)">
                <span class="personal-item-icon"><BookOpen :size="17" /></span>
                <span class="personal-item-copy"><strong>{{ item.name }}</strong><small>{{ item.kind === 'market' ? `市场已安装 · v${item.installed_version}` : '个人技能' }}</small></span>
                <ChevronRight :size="15" class="personal-item-chevron" />
              </button>
            </div>
          </a-spin>
          <a-button block class="personal-manage" @click="managementOpen = true">管理个人技能</a-button>
        </template>
        <template v-else>
          <a-spin :spinning="requestsLoading">
            <div v-if="!requests.length && !requestsLoading" class="personal-empty">还没有发布申请。</div>
            <div v-else class="personal-list">
              <button v-for="request in requests.slice(0, 12)" :key="request.id" type="button" class="personal-item" @click="openRequests">
                <span class="personal-item-icon"><ClipboardList :size="17" /></span>
                <span class="personal-item-copy"><strong>{{ request.name }}</strong><small>{{ request.owner_uid }} · {{ requestStatusLabel(request.status) }}</small></span>
                <ChevronRight :size="15" class="personal-item-chevron" />
              </button>
            </div>
          </a-spin>
          <a-button block class="personal-manage" @click="openRequests">查看全部申请</a-button>
        </template>
      </aside>
    </div>

    <a-drawer v-model:open="managementOpen" title="技能管理与上传" width="min(1080px, 96vw)" class="skill-management-drawer">
      <SkillCardList v-if="managementOpen" />
    </a-drawer>

    <a-drawer v-model:open="rankingsOpen" title="市场排行榜" width="min(520px, 96vw)" class="skill-rankings-drawer">
      <div class="ranking-controls">
        <a-segmented v-model:value="rankingMetric" :options="rankingMetricOptions" @change="fetchRankings" />
        <a-segmented v-model:value="rankingScope" :options="rankingScopeOptions" @change="fetchRankings" />
        <a-select
          v-if="rankingScope === 'department' && userStore.isSuperAdmin"
          v-model:value="rankingDepartmentId"
          class="ranking-department-select"
          placeholder="选择部门"
          aria-label="排行统计部门"
          :options="rankingDepartmentOptions"
          :loading="departmentLoading"
          :disabled="departmentLoading || !rankingDepartmentOptions.length"
          @change="fetchRankings"
        />
      </div>
      <a-alert v-if="userStore.isSuperAdmin && departmentError" type="error" :message="`部门列表加载失败：${departmentError}`" show-icon class="market-error">
        <template #action><a-button size="small" @click="fetchDepartmentNames">重试</a-button></template>
      </a-alert>
      <p class="ranking-hint">{{ rankingScopeHint }}，仅展示你可查看的市场技能。</p>
      <a-alert v-if="rankingError" type="error" :message="rankingError" show-icon class="market-error">
        <template #action><a-button size="small" @click="fetchRankings">重试</a-button></template>
      </a-alert>
      <a-spin :spinning="rankingsLoading">
        <div v-if="!rankings.length && !rankingsLoading && !rankingError" class="personal-empty">暂无上榜技能</div>
        <ol v-else-if="rankings.length" class="ranking-list">
          <li v-for="(item, index) in rankings" :key="item.slug">
            <span class="ranking-position">{{ index + 1 }}</span>
            <span class="ranking-copy"><strong>{{ item.name }}</strong><small>{{ marketScopeLabel(item) }}发布</small></span>
            <span class="ranking-count">{{ rankingMetric === 'installs' ? item.install_count : item.likes_count }} {{ rankingMetric === 'installs' ? '安装' : '点赞' }}</span>
            <a-button size="small" @click="openDetail(item.slug)">查看</a-button>
          </li>
        </ol>
      </a-spin>
    </a-drawer>

    <a-modal v-model:open="detailOpen" :footer="null" width="760px" title="Skill 详情">
      <a-spin :spinning="detailLoading">
        <div v-if="detail" class="market-detail">
          <div class="market-detail-heading">
            <div><h3>{{ detail.name }}</h3><span>{{ detail.slug }} · {{ detail.owner_name }}</span></div>
            <a-tag>{{ marketScopeLabel(detail) }}</a-tag>
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
          <div class="market-request-meta">{{ request.owner_uid }} · {{ request.personal_slug }} <span v-if="request.status === 'approved'">· {{ requestScopeLabel(request) }}</span><span v-if="request.review_note"> · {{ request.review_note }}</span></div>
          <div v-if="request.status === 'pending' && userStore.isAdmin" class="market-request-actions">
            <a-button size="small" @click="previewRequest(request)">查看提交快照</a-button>
            <a-button size="small" type="primary" @click="openReview(request, true)">批准</a-button>
            <a-button size="small" danger @click="openReview(request, false)">拒绝</a-button>
          </div>
        </div>
      </a-spin>
    </a-modal>

    <a-modal v-model:open="snapshotOpen" title="提交快照" :footer="null" width="740px" :body-style="{ maxHeight: 'calc(100vh - 160px)', overflowY: 'auto' }">
      <a-button class="market-snapshot-download" @click="downloadSnapshot">下载完整快照（含附件）</a-button>
      <div class="market-files snapshot-files"><div class="market-files-list"><strong>文件</strong><button v-for="file in snapshotFiles" :key="file.path" type="button" :class="{ active: file.path === snapshotSelected }" @click="selectSnapshotFile(file.path)">{{ file.path }}</button></div><div class="market-file-content"><a-spin :spinning="snapshotLoading"><MarkdownPreview v-if="snapshotContent" :content="snapshotContent" :compact="true" /><a-empty v-else :description="snapshotError || '选择文件预览'" /></a-spin></div></div>
    </a-modal>

    <a-modal v-model:open="reviewOpen" :title="reviewApproved ? '批准发布' : '拒绝申请'" :ok-text="reviewApproved ? '确认发布' : '确认拒绝'" :confirm-loading="reviewLoading" @ok="review">
      <p>{{ reviewRequest?.name }} · {{ reviewRequest?.personal_slug }}</p>
      <a-radio-group v-if="reviewApproved" v-model:value="reviewScope" class="market-review-scope">
        <a-radio value="department">申请提交时所属部门（{{ reviewDepartmentName }}）</a-radio><a-radio v-if="userStore.isSuperAdmin" value="global">全公司</a-radio>
      </a-radio-group>
      <a-alert v-if="reviewApproved && reviewScope === 'department'" type="info" show-icon :message="reviewDepartmentNotice" />
      <a-textarea v-model:value="reviewNote" placeholder="审核意见（可选）" :rows="3" :maxlength="2000" />
    </a-modal>
  </section>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { Blocks, BookOpen, ChartNoAxesColumn, ChevronRight, ClipboardList, Download, Heart, PanelRightClose, PanelRightOpen, Sparkles, Store } from '@lucide/vue'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import SkillCardList from '@/components/extensions/SkillCardList.vue'
import { combineSkillCards, filterSkillCards, personalSkillItems } from '@/components/extensions/skillHubCatalog'
import { useUserStore } from '@/stores/user'
import { departmentApi } from '@/apis/department_api'
import {
  getMarketSkill,
  getMarketSkillFile,
  getMarketSkillTree,
  installMarketSkill,
  listMarketRequests,
  listMarketRankings,
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
const rankingMetricOptions = [
  { label: '安装榜', value: 'installs' },
  { label: '点赞榜', value: 'likes' }
]
const sourceFilters = [
  { key: 'all', label: '全部' },
  { key: 'market', label: '市场' },
  { key: 'shared', label: '共享' },
  { key: 'builtin', label: '系统' },
  { key: 'installed', label: '已安装' }
]
const query = ref('')
const sourceFilter = ref('all')
const personalOpen = ref(true)
const rankingsOpen = ref(false)
const rankingMetric = ref('installs')
const rankingScope = ref('company')
const rankingDepartmentId = ref(userStore.departmentId ? Number(userStore.departmentId) : undefined)
const departmentNames = ref({})
const departmentLoading = ref(false)
const departmentError = ref('')
const rankingDepartmentOptions = computed(() => Object.entries(departmentNames.value).map(([id, name]) => ({ value: Number(id), label: name })))
const rankingScopeOptions = computed(() => [
  { label: '全公司', value: 'company' },
  { label: userStore.isSuperAdmin ? '单部门' : '本部门', value: 'department', disabled: !userStore.departmentId && !rankingDepartmentOptions.value.length }
])
const rankingScopeHint = computed(() => {
  if (rankingScope.value === 'company') return '按全公司当前成员统计'
  const departmentId = userStore.isSuperAdmin ? rankingDepartmentId.value : userStore.departmentId
  const departmentName = departmentNames.value[departmentId] || (Number(departmentId) === Number(userStore.departmentId) ? userStore.departmentName : '') || `部门 ${departmentId}`
  return `按${departmentName}当前成员统计`
})
const rankings = ref([])
const rankingsLoading = ref(false)
const rankingError = ref('')
let rankingRequestSequence = 0
const cards = ref([])
const installedCards = ref([])
const legacyCards = ref([])
const loading = ref(false)
const legacyLoading = ref(false)
const installedLoading = ref(false)
const loadingMore = ref(false)
const hasMore = ref(false)
const pageSize = 100
let requestSequence = 0
let installedRequestSequence = 0
const error = ref('')
const busySlug = ref('')
const managementOpen = ref(false)
const personalTab = ref('skills')
const personalQuery = ref('')
const combinedCards = computed(() => combineSkillCards(cards.value, legacyCards.value))
const visibleCards = computed(() => filterSkillCards(combinedCards.value, query.value, sourceFilter.value))
const personalItems = computed(() => personalSkillItems(legacyCards.value, installedCards.value, userStore.uid))
const filteredPersonalItems = computed(() => {
  const search = personalQuery.value.trim().toLowerCase()
  return personalItems.value.filter((item) => !search || `${item.name} ${item.slug}`.toLowerCase().includes(search))
})
const pendingRequestCount = computed(() => requests.value.filter((item) => item.status === 'pending').length)
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
const reviewDepartmentName = computed(() => departmentNames.value[reviewRequest.value?.owner_department_id] || `部门 ${reviewRequest.value?.owner_department_id || '未分配'}`)
const reviewDepartmentNotice = computed(() => {
  const departmentId = reviewRequest.value?.owner_department_id
  if (!departmentId) return '申请人未分配部门，无法按部门发布。'
  if (!userStore.isSuperAdmin && Number(userStore.departmentId) !== Number(departmentId)) return '申请提交时所属部门成员和超级管理员可见。你当前不属于该部门，发布后不会在你的市场目录出现。'
  return '申请提交时所属部门成员和超级管理员可在市场查看和安装。'
})
const snapshotOpen = ref(false)
const snapshotLoading = ref(false)
const snapshotFiles = ref([])
const snapshotSelected = ref('')
const snapshotContent = ref('')
const snapshotError = ref('')
const snapshotRequestId = ref('')

const errorText = (failure) => failure?.response?.data?.detail || failure?.message || '操作失败，请稍后重试'
const flattenFiles = (nodes) => nodes.flatMap((node) => node.is_dir ? flattenFiles(node.children || []) : [node])
const marketScopeLabel = (item) => {
  if (item.scope === 'global') return '全公司'
  if (Number(item.department_id) === Number(userStore.departmentId)) return '本部门'
  return departmentNames.value[item.department_id] || `部门 ${item.department_id}`
}
const sourceLabel = (item) => item.kind === 'market'
  ? `市场 · ${marketScopeLabel(item)}`
  : item.source_type === 'builtin' ? '系统内置' : '共享技能'
const requestStatusLabel = (status) => ({ pending: '待审核', approved: '已发布', rejected: '已驳回' })[status] || status
const requestScopeLabel = (request) => request.read_scope?.access_level === 'global'
  ? '全公司可见'
  : `${departmentNames.value[request.owner_department_id] || `部门 ${request.owner_department_id || '未分配'}`}及超级管理员可见`

function openCard(item) {
  if (item.kind === 'market') return openDetail(item.slug)
  router.push(`/extensions/skill/${encodeURIComponent(item.slug)}`)
}

function openPersonalItem(item) {
  if (item.kind === 'market') return openDetail(item.slug)
  router.push({ path: `/extensions/skill/${encodeURIComponent(item.slug)}`, query: { scope: 'personal' } })
}

function selectSourceFilter(key) {
  const previous = sourceFilter.value
  sourceFilter.value = key
  if (key === 'installed' || previous === 'installed') refresh()
}

function openRankings() {
  rankingsOpen.value = true
  if (userStore.isSuperAdmin && !rankingDepartmentOptions.value.length) fetchDepartmentNames()
  fetchRankings()
}

async function fetchRankings() {
  const requestId = ++rankingRequestSequence
  rankingsLoading.value = true
  rankingError.value = ''
  rankings.value = []
  try {
    const result = await listMarketRankings({
      metric: rankingMetric.value,
      scope: rankingScope.value,
      departmentId: rankingScope.value === 'department' && userStore.isSuperAdmin ? rankingDepartmentId.value : undefined,
      limit: 20
    })
    if (requestId === rankingRequestSequence) rankings.value = result?.data || []
  } catch (failure) {
    if (requestId === rankingRequestSequence) rankingError.value = errorText(failure)
  } finally {
    if (requestId === rankingRequestSequence) rankingsLoading.value = false
  }
}

async function fetchLegacy() {
  legacyLoading.value = true
  try {
    legacyCards.value = (await listSkillCards({ refreshPersonal: true }))?.data || []
  } catch (failure) {
    error.value = errorText(failure)
  } finally {
    legacyLoading.value = false
  }
}

async function fetchInstalled() {
  const requestId = ++installedRequestSequence
  installedLoading.value = true
  const items = []
  let offset = 0
  try {
    while (true) {
      const page = (await listMarketSkills({ installedOnly: true, offset, limit: pageSize }))?.data || []
      if (requestId !== installedRequestSequence) return
      items.push(...page)
      if (page.length < pageSize) break
      offset += pageSize
    }
    if (requestId === installedRequestSequence) installedCards.value = items
  } finally {
    if (requestId === installedRequestSequence) installedLoading.value = false
  }
}

async function fetchRequests() {
  requestsLoading.value = true
  try {
    requests.value = (await listMarketRequests())?.data || []
  } catch (failure) {
    message.error(errorText(failure))
  } finally {
    requestsLoading.value = false
  }
}

async function fetchDepartmentNames() {
  if (!userStore.isAdmin || departmentLoading.value) return
  departmentLoading.value = true
  departmentError.value = ''
  try {
    const result = await departmentApi.getDepartments()
    departmentNames.value = Object.fromEntries((result?.departments || result || []).map((item) => [item.id, item.name]))
    if (userStore.isSuperAdmin && !rankingDepartmentOptions.value.some((item) => item.value === rankingDepartmentId.value)) {
      rankingDepartmentId.value = rankingDepartmentOptions.value[0]?.value
    }
    if (rankingsOpen.value && rankingScope.value === 'department') fetchRankings()
  } catch (failure) {
    departmentError.value = errorText(failure)
  } finally {
    departmentLoading.value = false
  }
}

async function refresh() {
  const requestId = ++requestSequence
  loading.value = true
  loadingMore.value = false
  error.value = ''
  const legacyRefresh = fetchLegacy()
  const installedRefresh = fetchInstalled().catch((failure) => { if (requestId === requestSequence) error.value = errorText(failure) })
  try {
    const items = (await listMarketSkills({ query: query.value, installedOnly: sourceFilter.value === 'installed', limit: pageSize }))?.data || []
    if (requestId !== requestSequence) return
    cards.value = items
    hasMore.value = items.length === pageSize
  } catch (failure) {
    if (requestId === requestSequence) error.value = errorText(failure)
  } finally {
    if (requestId === requestSequence) loading.value = false
    await Promise.all([legacyRefresh, installedRefresh])
  }
}

async function loadMore() {
  if (!hasMore.value || loadingMore.value || loading.value) return
  const requestId = requestSequence
  loadingMore.value = true
  try {
    const items = (await listMarketSkills({ query: query.value, installedOnly: sourceFilter.value === 'installed', offset: cards.value.length, limit: pageSize }))?.data || []
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
    installedCards.value = installedCards.value.filter((card) => card.slug !== item.slug).concat(result.data)
    fetchInstalled().catch(() => message.warning('已安装，个人列表刷新失败，请稍后重试'))
    if (rankingsOpen.value) fetchRankings()
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
    if (rankingsOpen.value) fetchRankings()
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
    await fetchRequests()
    message.success('已提交审核')
  } catch (failure) {
    message.error(errorText(failure))
  } finally {
    submitLoading.value = false
  }
}

async function openRequests() {
  requestsOpen.value = true
  await fetchRequests()
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
    const result = await reviewMarketSkill(reviewRequest.value.id, reviewApproved.value, reviewScope.value, reviewNote.value)
    reviewOpen.value = false
    const outsideDepartment = reviewApproved.value && reviewScope.value === 'department' && !userStore.isSuperAdmin && Number(userStore.departmentId) !== Number(result.data.owner_department_id)
    if (reviewApproved.value && !outsideDepartment) {
      query.value = ''
      sourceFilter.value = 'market'
    }
    await Promise.all([openRequests(), refresh()])
    if (outsideDepartment) message.info('已发布到申请提交时所属部门；当前账号不在该部门，市场目录不会展示此技能')
    else message.success(reviewApproved.value ? '已发布到市场，可在市场分类查看' : '已拒绝申请')
  } catch (failure) {
    message.error(errorText(failure))
  } finally {
    reviewLoading.value = false
  }
}

watch(managementOpen, (open) => { if (!open) refresh() })
onMounted(() => { refresh(); fetchRequests(); fetchDepartmentNames() })
defineExpose({ loading: computed(() => loading.value || legacyLoading.value || installedLoading.value) })
</script>

<style scoped lang="less">
.skill-market { min-height: 100%; color: var(--color-text); }
.skill-hub-layout { display: grid; grid-template-columns: minmax(0, 1fr) 318px; min-height: 100%; }
.skill-hub-layout.personal-collapsed { grid-template-columns: minmax(0, 1fr); }
.skill-hub-main { min-width: 0; padding: 28px var(--page-padding) 48px; }
.market-heading { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 25px; }
.market-heading-copy { display: flex; align-items: center; gap: 13px; min-width: 0; }
.market-heading-icon { display: grid; flex: none; place-items: center; width: 46px; height: 46px; border-radius: 13px; background: var(--main-50); color: var(--main-600); }
.market-heading h2 { margin: 0 0 3px; font-size: 24px; font-weight: 650; }
.market-heading p { margin: 0; color: var(--color-text-secondary); font-size: 13px; }
.market-heading-actions, .market-toolbar { display: flex; align-items: center; gap: 10px; }
.market-heading-actions { flex: none; }
.personal-toggle { display: inline-flex; align-items: center; gap: 7px; }
.market-toolbar { flex-wrap: wrap; justify-content: space-between; margin-bottom: 18px; }
.market-search { width: min(370px, 100%); }
.market-subtools { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: -7px 0 16px; color: var(--color-text-secondary); font-size: 13px; }
.market-subtools :deep(.ant-btn) { display: inline-flex; align-items: center; gap: 7px; }
.ranking-controls { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 12px; }
.ranking-department-select { min-width: 160px; flex: 1; }
.ranking-hint { margin-bottom: 18px; color: var(--color-text-secondary); font-size: 12px; }
.ranking-list { display: grid; gap: 8px; margin: 0; padding: 0; list-style: none; }
.ranking-list li { display: flex; align-items: center; gap: 10px; min-width: 0; padding: 11px; border: 1px solid var(--gray-150); border-radius: 9px; }
.ranking-position { display: grid; flex: none; place-items: center; width: 30px; height: 30px; border-radius: 8px; background: var(--main-50); color: var(--main-600); font-weight: 700; }
.ranking-copy { display: grid; gap: 2px; min-width: 0; flex: 1; }
.ranking-copy strong, .ranking-copy small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ranking-copy small { color: var(--color-text-tertiary); font-size: 11px; }
.ranking-count { flex: none; color: var(--color-text-secondary); font-size: 12px; }
.market-filters { display: flex; gap: 4px; overflow-x: auto; border-bottom: 1px solid var(--gray-150); margin-bottom: 22px; }
.market-filters button { display: inline-flex; align-items: center; gap: 5px; flex: none; padding: 10px 14px 11px; border: 0; border-bottom: 2px solid transparent; background: none; color: var(--color-text-secondary); cursor: pointer; font-size: 13px; }
.market-filters button.active { color: var(--main-600); border-bottom-color: var(--main-500); font-weight: 600; }
.market-filters button:hover { color: var(--main-600); }
.market-filters button span { color: var(--color-text-tertiary); font-size: 12px; }
.market-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(235px, 1fr)); gap: 14px; }
.market-more { display: flex; justify-content: center; margin-top: 22px; }
.market-card { min-height: 235px; display: flex; flex-direction: column; padding: 18px; background: var(--color-bg-container); border: 1px solid var(--gray-150); border-radius: 12px; transition: border-color .2s, background-color .2s; }
.market-card:hover { border-color: var(--main-400); background: var(--gray-0); }
.market-card-top, .market-card-bottom, .market-detail-heading, .market-detail-actions, .market-request-title, .market-request-actions { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.market-icon { width: 38px; height: 38px; display: grid; place-items: center; border-radius: 10px; color: var(--main-600); background: var(--main-50); }
.market-scope { font-size: 12px; color: var(--color-text-secondary); }
.market-card-title { display: block; margin: 14px 0 1px; padding: 0; border: 0; background: none; color: inherit; text-align: left; font-size: 16px; font-weight: 650; cursor: pointer; }
.market-card-title:hover { color: var(--main-600); }
.market-card-slug, .market-card-author, .market-request-meta { color: var(--color-text-tertiary); font-size: 12px; }
.market-card p { margin: 11px 0; color: var(--color-text-secondary); line-height: 1.55; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; overflow: hidden; font-size: 13px; }
.market-card-author { margin-top: auto; }
.market-card-bottom { border-top: 1px solid var(--gray-100); margin-top: 13px; padding-top: 11px; }
.market-like { display: flex; align-items: center; gap: 5px; border: 0; background: none; color: var(--color-text-secondary); cursor: pointer; }
.market-like.liked { color: var(--color-error-500); }
.market-install-count { display: flex; align-items: center; gap: 5px; color: var(--color-text-secondary); font-size: 12px; }
.market-available { color: var(--color-text-tertiary); font-size: 12px; }
.market-empty { display: grid; justify-items: center; gap: 8px; padding: 80px 16px; color: var(--color-text-secondary); }
.market-empty strong { color: var(--color-text); }
.market-error { margin-bottom: 16px; }
.personal-space { min-width: 0; padding: 29px 18px 28px; border-left: 1px solid var(--gray-150); background: var(--gray-0); }
.personal-space-heading { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; margin-bottom: 18px; }
.personal-space-heading h3 { margin: 0; font-size: 17px; font-weight: 650; }
.personal-space-heading span { color: var(--color-text-tertiary); font-size: 12px; }
.personal-tabs { display: grid; grid-template-columns: repeat(2, 1fr); gap: 7px; margin-bottom: 18px; }
.personal-tabs button { min-height: 35px; border: 1px solid var(--gray-150); border-radius: 7px; color: var(--color-text-secondary); background: var(--color-bg-container); cursor: pointer; }
.personal-tabs button.active { border-color: var(--main-500); color: var(--color-bg-container); background: var(--main-500); }
.personal-pending { display: inline-grid; place-items: center; min-width: 16px; height: 16px; margin-left: 4px; padding: 0 3px; border-radius: 8px; background: var(--color-warning-100); color: var(--color-warning-700); font-size: 11px; }
.personal-search { margin-bottom: 13px; }
.personal-list { display: grid; gap: 8px; }
.personal-item { display: flex; align-items: center; gap: 10px; width: 100%; padding: 11px; text-align: left; border: 1px solid var(--gray-150); border-radius: 9px; color: var(--color-text); background: var(--color-bg-container); cursor: pointer; }
.personal-item:hover { border-color: var(--main-400); }
.personal-item-icon { display: grid; flex: none; place-items: center; width: 32px; height: 32px; border-radius: 8px; color: var(--main-600); background: var(--main-50); }
.personal-item-copy { display: grid; gap: 2px; min-width: 0; flex: 1; }
.personal-item-copy strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; font-weight: 600; }
.personal-item-copy small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--color-text-tertiary); font-size: 11px; }
.personal-item-chevron { flex: none; color: var(--color-text-tertiary); }
.personal-empty { padding: 32px 12px; text-align: center; color: var(--color-text-secondary); font-size: 13px; }
.personal-manage { margin-top: 14px; }
.market-filters button:focus-visible, .personal-tabs button:focus-visible, .personal-item:focus-visible, .market-card-title:focus-visible { outline: 2px solid var(--main-500); outline-offset: 2px; }
.market-detail-heading h3 { margin: 0 0 3px; font-size: 22px; }
.market-detail-heading span, .market-detail-meta { color: var(--color-text-secondary); font-size: 13px; }
.market-detail > p { margin: 18px 0; line-height: 1.6; }
.market-detail-meta, .market-dependencies { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin: 16px 0; }
.market-files { display: grid; grid-template-columns: 180px minmax(0, 1fr); grid-template-rows: minmax(0, 1fr); height: min(54vh, 400px); min-height: 260px; overflow: hidden; border: 1px solid var(--gray-150); border-radius: 10px; margin-top: 18px; }
.snapshot-files { height: min(58vh, 520px); }
.market-files-list { min-height: 0; overflow: auto; display: flex; flex-direction: column; padding: 10px; border-right: 1px solid var(--gray-150); }
.market-files-list strong { padding: 7px; }
.market-files-list button { border: 0; background: none; text-align: left; padding: 7px; border-radius: 6px; color: var(--color-text-secondary); cursor: pointer; overflow-wrap: anywhere; }
.market-files-list button.active { color: var(--color-primary-700); background: var(--color-primary-50); }
.market-file-content { min-height: 0; overflow: auto; padding: 16px; }
.market-detail-actions { justify-content: flex-end; margin-top: 20px; }
.market-submit-select { display: block; width: 100%; margin: 18px 0; }
.market-request { padding: 16px 0; border-bottom: 1px solid var(--gray-150); }
.market-request p { margin: 8px 0; color: var(--color-text-secondary); }
.market-request-actions { justify-content: flex-end; margin-top: 10px; }
.market-review-scope { display: block; margin: 18px 0; }
@media (max-width: 1100px) { .skill-hub-layout { grid-template-columns: minmax(0, 1fr); } .personal-space { border-left: 0; border-top: 1px solid var(--gray-150); } }
@media (max-width: 680px) { .market-heading { align-items: start; flex-direction: column; } .market-heading-actions { width: 100%; flex-wrap: wrap; } .market-heading-actions :deep(.ant-btn) { flex: 1; } .market-files { grid-template-columns: 1fr; grid-template-rows: auto minmax(0, 1fr); } .market-files-list { max-height: 120px; border-right: 0; border-bottom: 1px solid var(--gray-150); } .market-card-bottom { flex-wrap: wrap; } }
@media (max-width: 480px) { .ranking-list li { display: grid; grid-template-columns: 30px minmax(0, 1fr) auto; } .ranking-position { grid-column: 1; grid-row: 1; } .ranking-copy { grid-column: 2 / 4; grid-row: 1; } .ranking-count { grid-column: 2; grid-row: 2; } .ranking-list li :deep(.ant-btn) { grid-column: 3; grid-row: 2; } }
</style>
