<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { Plus, RefreshCw, Trash2, SquarePen, Bot, ChevronRight } from '@lucide/vue'
import { useRouter } from 'vue-router'

import { agentApi } from '@/apis/agent_api'
import { departmentApi } from '@/apis/department_api'
import AgentEditModal from '@/components/model-management/AgentEditModal.vue'
import { isBuiltinAgent, useAgentStore } from '@/stores/agent'
import { useUserStore } from '@/stores/user'
import PageShoulder from '@/components/shared/PageShoulder.vue'
import InfoCard from '@/components/shared/InfoCard.vue'
import FallbackAvatar from '@/components/common/FallbackAvatar.vue'
import ExtensionCardGrid from '@/components/extensions/ExtensionCardGrid.vue'
import { normalizeAgent, normalizeAgentBackendOption } from '@/utils/agentConfigUtils'
import { generatePixelAvatar } from '@/utils/pixelAvatar'

const agentStore = useAgentStore()
const userStore = useUserStore()
const router = useRouter()
const agentLoading = ref(false)
const searchQuery = ref('')
const ownerSearch = ref('')
const ownerDepartmentId = ref(undefined)

const agentBackendOptions = ref([])
const managedAgents = ref([])
const directoryDepartments = ref([])
const agentEditModalRef = ref(null)
let departmentRequestSeq = 0
const isSystemAgent = (agent) => agent.owner_is_system === true
const SCOPE_GROUPS = [
  { key: 'system', title: '系统预置' },
  { key: 'global', title: '全局共享' },
  { key: 'department', title: '部门共享' },
  { key: 'user', title: '指定人员' },
  { key: 'private', title: '个人' },
  { key: 'other', title: '其他共享范围' }
]

/** 创建人的独占读取范围属于个人智能体。 */
const isPersonalAgent = (agent) => {
  const config = agent.share_config
  const readScope = config?.version === 2 ? config.read_scope : config
  if (!readScope) return !!agent.created_by && !config?.edit_scope && !config?.manage_scope
  const userUids = readScope.user_uids || []
  return (
    readScope.access_level === 'user' &&
    !!agent.created_by &&
    userUids.every((uid) => String(uid) === String(agent.created_by))
  )
}

const departmentOptions = computed(() => {
  const departments = new Map(
    directoryDepartments.value.map((department) => [String(department.id), department.name])
  )
  managedAgents.value.forEach((agent) => {
    if (agent.owner_department_id == null) return
    const id = String(agent.owner_department_id)
    if (!departments.has(id)) departments.set(id, agent.owner_department_name || `部门 #${id}`)
  })
  return [...departments]
    .map(([value, label]) => ({ value, label }))
    .sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'))
})

const filteredAgents = computed(() => {
  const keyword = searchQuery.value.trim().toLowerCase()
  const ownerKeyword = userStore.isAdmin ? ownerSearch.value.trim().toLowerCase() : ''
  const filtered = managedAgents.value.filter((agent) => {
    if (
      userStore.isAdmin &&
      ownerDepartmentId.value != null &&
      String(agent.owner_department_id) !== ownerDepartmentId.value
    ) {
      return false
    }
    const owner = [agent.owner_name, agent.created_by].filter(Boolean).join(' ').toLowerCase()
    if (ownerKeyword && !owner.includes(ownerKeyword)) return false
    if (!keyword) return true
    return [
      agent.name,
      agent.id,
      agent.backend_id,
      agent.description,
      agent.owner_name,
      agent.created_by,
      agent.owner_department_name,
      ...(agent.read_department_names || []),
      ...(agent.edit_department_names || []),
      ...(agent.manage_department_names || [])
    ].some((value) =>
      String(value || '')
        .toLowerCase()
        .includes(keyword)
    )
  })
  return filtered.sort((a, b) => {
    if (isBuiltinAgent(a) !== isBuiltinAgent(b)) return isBuiltinAgent(a) ? -1 : 1
    return String(a.name || a.id).localeCompare(String(b.name || b.id), 'zh-CN')
  })
})

const groupedAgents = computed(() => {
  const sections = [
    {
      key: 'agents',
      title: '智能体',
      groups: SCOPE_GROUPS.map((group) => ({ ...group, agents: [] }))
    },
    {
      key: 'subagents',
      title: '子智能体',
      groups: SCOPE_GROUPS.map((group) => ({ ...group, agents: [] }))
    }
  ]
  filteredAgents.value.forEach((agent) => {
    const section = agent.is_subagent ? sections[1] : sections[0]
    const scope =
      agent.share_config?.version === 2 ? agent.share_config.read_scope : agent.share_config
    const key = isSystemAgent(agent)
      ? 'system'
      : isPersonalAgent(agent)
        ? 'private'
        : scope?.access_level || 'other'
    const group = section.groups.find((item) => item.key === key) || section.groups.at(-1)
    group.agents.push(agent)
  })
  return sections
    .map((section) => ({
      ...section,
      groups: section.groups.filter((group) => group.agents.length),
      count: section.groups.reduce((total, group) => total + group.agents.length, 0)
    }))
    .filter((section) => section.count)
})

const agentStats = computed(() => ({
  total: managedAgents.value.filter((agent) => !agent.is_subagent).length,
  subagents: managedAgents.value.filter((agent) => agent.is_subagent).length
}))
const canManageAgent = (agent) => !!agent?.can_manage
const canEditAgent = (agent) => !!agent?.can_edit
const getAgentDefaultIconSrc = (agent) => (agent.id ? generatePixelAvatar(agent.id) : '')

/** 将部门权限范围展开为具体部门名称。 */
const getAgentScopeLabel = (scope, departmentNames = []) => {
  if (!scope) return '无'
  if (scope.access_level === 'global') return '全局'
  if (scope.access_level === 'department') {
    return (
      (scope.department_ids || [])
        .map((id, index) => departmentNames[index] || `部门 #${id}`)
        .join('、') || '未指定部门'
    )
  }
  return `用户(${scope.user_uids?.length || 0})`
}

/** 分开展示读取、编辑和管理权限。 */
const getAgentShareTags = (agent) => {
  if (isPersonalAgent(agent)) return [{ name: '仅创建人', color: 'gray' }]
  const config = agent.share_config || {}
  const readScope = config.version === 2 ? config.read_scope : config
  if (!readScope && !config.edit_scope && !config.manage_scope) {
    return [{ name: '仅所有者', color: 'gray' }]
  }
  const tags = [
    {
      name: `读取：${getAgentScopeLabel(readScope, agent.read_department_names)}`,
      color: 'gray'
    }
  ]
  if (config.edit_scope) {
    tags.push({
      name: `编辑：${getAgentScopeLabel(config.edit_scope, agent.edit_department_names)}`,
      color: 'gray'
    })
  }
  if (config.manage_scope) {
    tags.push({
      name: `管理：${getAgentScopeLabel(config.manage_scope, agent.manage_department_names)}`,
      color: 'gray'
    })
  }
  return tags
}
/** 返回创建人的展示名称，缺失时保留身份线索。 */
const getAgentOwnerLabel = (agent) => agent.owner_name || agent.created_by || '创建人未知'
/** 返回创建人的当前部门。 */
const getAgentDepartmentLabel = (agent) => {
  if (isSystemAgent(agent)) return ''
  return agent.owner_department_name || (agent.owner_name ? '未分配部门' : '部门未知')
}
/** 组合权限范围和归属标签。 */
const getAgentCardTags = (agent) => {
  const system = isSystemAgent(agent)
  const tags = [
    ...getAgentShareTags(agent),
    { name: system ? '系统预置' : `创建人：${getAgentOwnerLabel(agent)}`, color: 'blue' }
  ]
  if (!system) {
    const department = getAgentDepartmentLabel(agent)
    if (department) tags.push({ name: `创建人部门：${department}`, color: 'gray' })
  }
  return tags
}

// ============ Agent Operations ============
const loadAgentBackends = async () => {
  try {
    const response = await agentApi.getAgentBackends()
    agentBackendOptions.value = (response.backends || []).map(normalizeAgentBackendOption)
  } catch (error) {
    message.error(error.message || '加载智能体后端失败')
  }
}

const loadAgents = async () => {
  agentLoading.value = true
  try {
    const response = await agentApi.getAgents({ includeSubagents: true })
    managedAgents.value = (response.agents || []).map(normalizeAgent)
  } catch (error) {
    message.error(error.message || '加载智能体失败')
  } finally {
    agentLoading.value = false
  }
}

const loadDepartments = async () => {
  if (!userStore.isAdmin) return
  const requestSeq = ++departmentRequestSeq
  try {
    const response = await departmentApi.getDepartments()
    if (requestSeq !== departmentRequestSeq || !userStore.isAdmin) return
    const departments = response?.departments || response || []
    directoryDepartments.value = Array.isArray(departments) ? departments : []
    if (
      ownerDepartmentId.value != null &&
      !directoryDepartments.value.some(
        (department) => String(department.id) === ownerDepartmentId.value
      )
    ) {
      ownerDepartmentId.value = undefined
    }
  } catch (error) {
    if (requestSeq === departmentRequestSeq && userStore.isAdmin) {
      message.error(error.message || '加载部门列表失败')
    }
  }
}

const refreshAgentCatalog = () => Promise.all([loadAgents(), loadDepartments()])

watch(
  () => userStore.isAdmin,
  (isAdmin) => {
    if (isAdmin) {
      loadDepartments()
      return
    }
    ownerSearch.value = ''
    ownerDepartmentId.value = undefined
    directoryDepartments.value = []
    departmentRequestSeq += 1
  }
)

const openCreateAgentModal = () => {
  agentEditModalRef.value?.openCreate()
}

const openEditAgentModal = (agent) => {
  if (!canEditAgent(agent)) return
  agentEditModalRef.value?.openEdit(agent)
}

const openAgentChat = (agent) => {
  if (!agent?.id || agent.is_subagent) return
  router.push({ name: 'AgentComp', query: { agent_id: agent.id } })
}

const refreshAgentLists = async () => {
  await Promise.all([refreshAgentCatalog(), agentStore.fetchAgents()])
}

const deleteAgent = async (agent) => {
  if (isBuiltinAgent(agent)) {
    message.warning('内置智能体不能删除')
    return
  }
  Modal.confirm({
    title: `删除 ${agent.name}`,
    content: '删除后不可恢复，已绑定该智能体的历史对话仍保留原始绑定信息。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        await agentApi.deleteAgent(agent.id)
        await refreshAgentLists()
        message.success('智能体已删除')
      } catch (error) {
        message.error(error.message || '删除智能体失败')
      }
    }
  })
}

onMounted(async () => {
  await Promise.all([loadAgentBackends(), refreshAgentCatalog()])
})

defineExpose({
  loading: agentLoading,
  stats: agentStats,
  refresh: refreshAgentCatalog
})
</script>

<template>
  <div class="agent-manage-panel">
    <PageShoulder v-model:search="searchQuery" search-placeholder="搜索智能体、创建人或部门...">
      <template #actions>
        <a-button type="primary" class="lucide-icon-btn" @click="openCreateAgentModal">
          <Plus :size="14" />
          新增智能体
        </a-button>
        <a-button class="lucide-icon-btn" @click="refreshAgentCatalog" :loading="agentLoading">
          <RefreshCw :size="14" :class="{ spinning: agentLoading }" />
        </a-button>
      </template>
    </PageShoulder>

    <div v-if="userStore.isAdmin" class="agent-catalog-filters">
      <a-select
        v-model:value="ownerDepartmentId"
        class="agent-catalog-department"
        placeholder="全部创建人部门"
        allow-clear
        :options="departmentOptions"
      />
      <a-input
        v-model:value="ownerSearch"
        class="agent-catalog-owner"
        placeholder="搜索创建人姓名或账号"
        allow-clear
      />
    </div>

    <div v-if="groupedAgents.length === 0" class="agent-empty-state">
      <div class="agent-empty-card">
        <div class="agent-empty-icon"><Bot :size="22" /></div>
        <div class="agent-empty-title">
          {{
            searchQuery || ownerSearch || ownerDepartmentId
              ? '没有匹配的智能体或子智能体'
              : '暂无智能体或子智能体'
          }}
        </div>
        <div class="agent-empty-desc">
          {{
            searchQuery || ownerSearch || ownerDepartmentId
              ? '换个关键词或筛选条件试试。'
              : '可以创建智能体开始使用。'
          }}
        </div>
      </div>
    </div>

    <template v-else>
      <section v-for="section in groupedAgents" :key="section.key" class="agent-kind-section">
        <div class="agent-kind-header">
          <h2>{{ section.title }}</h2>
          <span>{{ section.count }} 个</span>
        </div>
        <section v-for="group in section.groups" :key="group.key" class="agent-group-section">
          <div class="agent-group-header">
            <h3>{{ group.title }}</h3>
          </div>
          <ExtensionCardGrid :min-width="280">
            <InfoCard
              v-for="agent in group.agents"
              :key="agent.id"
              :title="agent.name"
              :subtitle="agent.slug || agent.id"
              :description="agent.description || '暂无描述'"
              :default-icon="Bot"
              :tags="getAgentCardTags(agent)"
              class="config-card agent-card"
              @click="canEditAgent(agent) && openEditAgentModal(agent)"
            >
              <template #icon>
                <FallbackAvatar
                  class="agent-card-icon-image"
                  :src="agent.icon"
                  :default-src="getAgentDefaultIconSrc(agent)"
                  :name="agent.name || agent.id"
                  :seed="agent.id || agent.name"
                  kind="agent"
                  :size="40"
                  shape="rounded"
                  :alt="`${agent.name || '智能体'}图标`"
                />
              </template>

              <template v-if="canEditAgent(agent)" #card-more-action-corner>
                <a-menu>
                  <a-menu-item key="edit" @click.stop="openEditAgentModal(agent)">
                    <span class="lucide-menu-item">
                      <SquarePen :size="14" />
                      <span>编辑智能体</span>
                    </span>
                  </a-menu-item>
                  <a-menu-item
                    v-if="canManageAgent(agent)"
                    key="delete"
                    :disabled="isBuiltinAgent(agent)"
                    :danger="!isBuiltinAgent(agent)"
                    @click.stop="deleteAgent(agent)"
                  >
                    <span class="lucide-menu-item">
                      <Trash2 :size="14" />
                      <span>删除智能体</span>
                    </span>
                  </a-menu-item>
                </a-menu>
              </template>

              <template v-if="!agent.is_subagent" #tag-actions>
                <a-button
                  type="text"
                  size="small"
                  class="agent-chat-entry"
                  @click.stop="openAgentChat(agent)"
                >
                  去对话
                  <ChevronRight :size="14" />
                </a-button>
              </template>
            </InfoCard>
          </ExtensionCardGrid>
        </section>
      </section>
    </template>

    <AgentEditModal
      ref="agentEditModalRef"
      :backend-options="agentBackendOptions"
      @saved="refreshAgentLists"
    />
  </div>
</template>

<style lang="less" scoped>
.agent-manage-panel {
  height: 100%;
  min-height: 0;
}

.agent-catalog-filters {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  padding: 12px var(--page-padding);
  border-bottom: 1px solid var(--gray-100);
}

.agent-catalog-department {
  width: 180px;
}

.agent-catalog-owner {
  width: 220px;
}

.agent-empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 280px;
  padding: 40px var(--page-padding);
  text-align: center;
}

.agent-empty-card {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  min-height: 220px;
  flex-direction: column;
  border: 1px dashed var(--gray-150);
  border-radius: 16px;
  background: linear-gradient(180deg, var(--gray-0) 0%, var(--gray-25) 100%);
  color: var(--gray-500);
}

.agent-empty-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  margin-bottom: 12px;
  border-radius: 14px;
  background: var(--gray-50);
  color: var(--gray-600);
}

.agent-empty-title {
  color: var(--gray-800);
  font-size: 15px;
  font-weight: 700;
  line-height: 22px;
}

.agent-empty-desc {
  margin-top: 4px;
  color: var(--gray-500);
  font-size: 13px;
  line-height: 20px;
}

.agent-group-section + .agent-group-section {
  padding-top: 2px;
}

.agent-kind-section + .agent-kind-section {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--gray-100);
}

.agent-kind-header {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 16px var(--page-padding) 2px;

  h2 {
    margin: 0;
    color: var(--gray-900);
    font-size: 16px;
    font-weight: 600;
    line-height: 24px;
  }

  span {
    color: var(--gray-500);
    font-size: 12px;
  }
}

.agent-group-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px var(--page-padding) 0;
  color: var(--gray-500);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.4px;
  line-height: 18px;

  h3 {
    margin: 0;
    color: inherit;
    font: inherit;
    letter-spacing: inherit;
  }
}

.agent-card-icon-image {
  display: block;
  width: 100%;
  height: 100%;
  border: 0;
}

.agent-card :deep(.info-card-tags) {
  justify-content: flex-start;
  margin-top: auto;
}

.agent-card :deep(.card-tag) {
  max-width: 100%;
  padding-top: 4px;
  padding-bottom: 4px;
  line-height: 1.4;
  overflow-wrap: anywhere;
}

.agent-chat-entry {
  display: inline-flex;
  align-items: center;
  min-width: auto;
  height: 24px;
  padding: 0 2px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  box-shadow: none;
  color: var(--gray-600);
  font-size: 12px;
  gap: 1px;

  &:hover {
    border: 0;
    background: transparent;
    box-shadow: none;
    color: var(--main-700);
  }

  &:focus:not(:focus-visible) {
    outline: none;
  }

  &:focus-visible {
    outline: 2px solid var(--main-200);
    outline-offset: 2px;
  }
}

.spinning {
  animation: spin 1s linear infinite;
}

@media (max-width: 600px) {
  .agent-manage-panel :deep(.page-shoulder) {
    flex-wrap: wrap;
  }

  .agent-manage-panel :deep(.page-shoulder-left),
  .agent-manage-panel :deep(.page-shoulder-right),
  .agent-manage-panel :deep(.search-input) {
    width: 100%;
  }

  .agent-manage-panel :deep(.page-shoulder-right) {
    justify-content: flex-start;
    flex-wrap: wrap;
  }

  .agent-catalog-department,
  .agent-catalog-owner {
    flex: 1 1 150px;
    width: auto;
  }
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>
