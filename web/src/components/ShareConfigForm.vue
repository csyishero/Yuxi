<template>
  <div class="share-config-form" :class="{ disabled }">
    <a-alert
      v-if="scopeViolationMessage"
      type="warning"
      show-icon
      :message="scopeViolationMessage"
    />

    <div
      v-if="isKnowledgeBasePermissionMode"
      class="permission-mode-tabs"
      role="tablist"
      aria-label="知识库权限配置模式"
    >
      <button
        type="button"
        role="tab"
        class="permission-mode-tab"
        :class="{ active: activePermissionMode === 'quick' }"
        :aria-selected="activePermissionMode === 'quick'"
        @click="activePermissionMode = 'quick'"
      >
        快捷授权
      </button>
      <button
        type="button"
        role="tab"
        class="permission-mode-tab"
        :class="{ active: activePermissionMode === 'advanced' }"
        :aria-selected="activePermissionMode === 'advanced'"
        @click="activePermissionMode = 'advanced'"
      >
        高级权限树
      </button>
    </div>

    <template v-if="!isKnowledgeBasePermissionMode || activePermissionMode === 'quick'">
      <div v-if="isKnowledgeBasePermissionMode" class="quick-permission-intro">
        <div>
          <strong>按职责快速配置</strong>
          <span>先配置三档成员范围；具体能力可在“高级权限树”中调整。</span>
        </div>
        <a-tag>兼容现有授权配置</a-tag>
      </div>

      <section
        v-for="scope in scopeOptions"
        :key="scope.key"
        class="permission-scope-section"
        :class="{ 'permission-role-card': isKnowledgeBasePermissionMode }"
      >
        <div
          class="permission-scope-header"
          :class="{ 'has-content': Boolean(scopes[scope.key]) }"
        >
          <div v-if="isKnowledgeBasePermissionMode" class="permission-role-heading">
            <div class="permission-role-icon" aria-hidden="true">
              <component :is="scope.icon" :size="20" />
            </div>
            <div class="permission-role-copy">
              <div class="permission-role-title-row">
                <h4>{{ scope.profileTitle }}</h4>
                <a-tag :color="scope.badgeColor">{{ scope.badge }}</a-tag>
              </div>
              <p>{{ scope.description }}</p>
            </div>
            <div class="permission-capabilities" aria-label="包含能力">
              <span
                v-for="capability in scope.capabilities"
                :key="capability.label"
                class="permission-capability"
                :class="{ denied: capability.denied, danger: capability.danger }"
              >
                {{ capability.label }}
              </span>
            </div>
          </div>
          <h4 v-else>{{ scope.title }}</h4>
          <div class="permission-scope-actions">
            <span
              v-if="isKnowledgeBasePermissionMode && scope.key === 'manage_scope'"
              class="system-enabled-label"
            >
              系统继承
            </span>
            <span
              v-if="isKnowledgeBasePermissionMode && scope.key === 'manage_scope'"
              class="extra-scope-label"
            >
              额外协管
            </span>
            <a-switch
              v-if="scope.key !== 'read_scope' || !requireReadScope"
              size="small"
              :checked="Boolean(scopes[scope.key])"
              :aria-label="`${scope.profileTitle || scope.title}${scopes[scope.key] ? '已开启' : '已关闭'}`"
              :disabled="disabled || (scope.key === 'read_scope' && requireReadScope)"
              @change="(enabled) => toggleScope(scope.key, enabled)"
            />
          </div>
        </div>

        <section
          v-if="showKnowledgeBaseInheritance && scope.key === 'manage_scope'"
          class="inherited-managers-section"
        >
          <div class="inherited-managers-heading">
            <div>
              <h4>系统继承管理权限</h4>
              <p>超级管理员和所属部门管理员自动拥有管理权限，身份变化后自动更新。</p>
            </div>
            <a-tag color="cyan">MANAGE</a-tag>
          </div>
          <div v-if="inheritedManagerOptions.length" class="inherited-manager-list">
            <div
              v-for="manager in inheritedManagerOptions"
              :key="manager.value"
              class="inherited-manager-item"
            >
              <span>{{ manager.label }}</span>
              <a-tag>{{ manager.inheritedLabel }}</a-tag>
            </div>
          </div>
          <div v-else class="inherited-manager-empty">
            系统继承规则已生效；当前账号权限范围内暂无可展示成员。
          </div>
        </section>

        <template v-if="scopes[scope.key]">
          <div v-if="isKnowledgeBasePermissionMode" class="scope-label">
            {{ scope.scopePrompt }}
          </div>
          <div
            class="share-mode-cards"
            :class="`active-${scopes[scope.key].access_level}`"
            role="radiogroup"
            :aria-label="scope.title"
          >
            <div
              v-for="option in getShareModeOptions(scope.key)"
              :key="option.value"
              role="radio"
              class="share-mode-card"
              :class="{ active: scopes[scope.key].access_level === option.value }"
              :aria-checked="scopes[scope.key].access_level === option.value"
              :tabindex="!disabled && scopes[scope.key].access_level === option.value ? 0 : -1"
              @click="setAccessLevel(scope.key, option.value)"
              @keydown.enter.prevent="setAccessLevel(scope.key, option.value)"
              @keydown.space.prevent="setAccessLevel(scope.key, option.value)"
            >
              <div class="card-main">
                <div class="card-header">
                  <div class="card-icon-wrapper" aria-hidden="true">
                    <component :is="option.icon" class="card-icon" :size="20" />
                  </div>
                  <div class="card-title">{{ getModeTitle(scope.key, option) }}</div>
                  <div
                    v-if="
                      scopes[scope.key].access_level === option.value && option.value !== 'global'
                    "
                    class="card-action"
                    @click.stop
                  >
                    <a-dropdown
                      :trigger="['click']"
                      placement="bottomRight"
                      overlay-class-name="share-selection-popover"
                    >
                      <a-button
                        size="small"
                        class="select-action lucide-icon-btn"
                        :aria-label="option.value === 'department' ? '选择部门' : '选择用户'"
                        :disabled="disabled"
                      >
                        <UserPlus class="select-action-icon" :size="14" />
                        <span class="access-count">{{
                          getAccessCount(scope.key, option.value)
                        }}</span>
                      </a-button>
                      <template #overlay>
                        <div class="selection-dropdown" @mousedown.stop @click.stop>
                          <div class="selection-dropdown-header">
                            <div class="selection-dropdown-title">
                              {{ getScopeActionLabel(scope.key)
                              }}{{ option.value === 'department' ? '部门' : '用户' }}
                            </div>
                            <div class="selection-dropdown-subtitle">
                              {{ getAccessSummary(scope.key, option.value) }}
                            </div>
                          </div>
                          <a-input
                            v-model:value="selectionSearch[scope.key][option.value]"
                            size="small"
                            allow-clear
                            class="selection-search"
                            :placeholder="option.value === 'department' ? '搜索部门' : '搜索用户'"
                            @mousedown.stop
                            @click.stop
                          />
                          <div
                            v-if="getSelectionOptions(scope.key, option.value).length"
                            class="selection-list"
                          >
                            <div
                              v-for="item in getSelectionOptions(scope.key, option.value)"
                              :key="item.value"
                              role="checkbox"
                              :aria-checked="isSelected(scope.key, option.value, item.value)"
                              tabindex="0"
                              class="selection-item"
                              :class="{ selected: isSelected(scope.key, option.value, item.value) }"
                              @mousedown.stop
                              @click.stop="
                                toggleSelection(
                                  scope.key,
                                  option.value,
                                  item.value,
                                  !isSelected(scope.key, option.value, item.value)
                                )
                              "
                              @keydown.enter.prevent="
                                toggleSelection(
                                  scope.key,
                                  option.value,
                                  item.value,
                                  !isSelected(scope.key, option.value, item.value)
                                )
                              "
                              @keydown.space.prevent="
                                toggleSelection(
                                  scope.key,
                                  option.value,
                                  item.value,
                                  !isSelected(scope.key, option.value, item.value)
                                )
                              "
                            >
                              <span class="selection-item-content">
                                <a-checkbox
                                  :checked="isSelected(scope.key, option.value, item.value)"
                                  @click.stop
                                  @change="
                                    toggleSelection(
                                      scope.key,
                                      option.value,
                                      item.value,
                                      $event.target.checked
                                    )
                                  "
                                />
                                <span class="selection-label">{{ item.label }}</span>
                              </span>
                            </div>
                          </div>
                          <div v-else class="selection-empty">暂无可选项</div>
                        </div>
                      </template>
                    </a-dropdown>
                  </div>
                </div>
                <div class="card-description">{{ getModeDescription(scope.key, option) }}</div>
              </div>
            </div>
          </div>
          <div
            v-if="isKnowledgeBasePermissionMode && scope.key === 'edit_scope'"
            class="scope-guidance"
          >
            解析用于把文件转换为可检索文本；入库用于生成索引并进入问答范围。建议仅授予可信业务人员。
          </div>
        </template>
      </section>
    </template>

    <section v-else class="advanced-permission-panel" aria-label="高级权限树">
      <div class="advanced-permission-intro">
        <div>
          <h4>高级权限树</h4>
          <p>勾选每一档新增的能力；授权成员范围请在“快捷授权”中配置。</p>
        </div>
        <a-tag color="cyan">可编辑</a-tag>
      </div>
      <div class="advanced-permission-layout">
        <aside class="permission-profile-list" aria-label="授权档位">
          <button
            v-for="profile in permissionProfiles"
            :key="profile.key"
            type="button"
            class="permission-profile-button"
            :class="{ active: activePermissionProfile === profile.key }"
            @click="activePermissionProfile = profile.key"
          >
            <strong>{{ profile.title }}</strong>
            <span>{{ getPermissionProfileSummary(profile.key) }}</span>
          </button>
          <p>同一成员命中多条授权时取最高权限；系统继承管理员不进入指定成员列表。</p>
        </aside>
        <div class="permission-tree-panel">
          <header class="permission-tree-header">
            <div>
              <h4>{{ selectedPermissionProfile.title }} · 权限明细</h4>
              <p>{{ selectedPermissionProfile.description }}</p>
              <p v-if="selectedPermissionProfile.order > 1" class="permission-tree-edit-hint">
                可直接修改当前档位及下级档位能力；修改下级能力会同时影响对应成员。
              </p>
            </div>
            <a-tag color="cyan">{{ selectedPermissionProfile.level }}</a-tag>
          </header>
          <div class="permission-tree-groups">
            <section
              v-for="group in permissionGroups"
              :key="group.key"
              class="permission-tree-group"
            >
              <div class="permission-tree-group-title">{{ group.title }}</div>
              <div class="permission-tree-items">
                <div
                  v-for="item in group.items"
                  :key="item.key"
                  class="permission-tree-item"
                  :class="{
                    enabled: isCapabilityEnabled(item),
                    inherited: isCapabilityInherited(item),
                    editable: isCapabilityEditable(item),
                    danger: item.danger
                  }"
                  @click="toggleCapability(item)"
                >
                  <a-checkbox
                    :checked="isCapabilityEnabled(item)"
                    :disabled="disabled || !isCapabilityEditable(item)"
                    :aria-label="`${item.title}${isCapabilityEnabled(item) ? '已启用' : '未启用'}`"
                    @click.stop
                    @change="toggleCapability(item, $event.target.checked)"
                  />
                  <span>
                    <strong>{{ item.title }}</strong>
                    <small>{{ item.description }}</small>
                  </span>
                  <a-tag v-if="isCapabilityInherited(item)" class="permission-inherited-tag">
                    继承
                  </a-tag>
                </div>
              </div>
            </section>
          </div>
        </div>
      </div>
    </section>

    <a-alert
      v-if="disabled && disabledReason"
      type="info"
      show-icon
      class="share-disabled-alert"
      :message="disabledReason"
    />
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { Building2, BookOpen, FilePenLine, Globe, ShieldCheck, Users, UserPlus } from '@lucide/vue'
import { useUserStore } from '@/stores/user'
import { authApi } from '@/apis/auth_api'
import { departmentApi } from '@/apis/department_api'
import {
  isScopeWithinParent,
  normalizeKnowledgeBaseCapabilityPolicy
} from '@/utils/shareConfig'

const userStore = useUserStore()
const departments = ref([])
const users = ref([])
const syncingFromProps = ref(false)
const activePermissionMode = ref('quick')
const activePermissionProfile = ref('contributor')

const props = defineProps({
  modelValue: {
    type: Object,
    required: true,
    default: () => ({
      version: 2,
      read_scope: { access_level: 'global', department_ids: [], user_uids: [] },
      edit_scope: null,
      manage_scope: null
    })
  },
  autoSelectUserDept: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  disabledReason: { type: String, default: '' },
  requireReadScope: { type: Boolean, default: false },
  showEditScope: { type: Boolean, default: false },
  resourceDepartmentId: { type: [Number, String], default: null },
  showKnowledgeBaseInheritance: { type: Boolean, default: false },
  availableDepartments: { type: Array, default: null },
  availableUsers: { type: Array, default: null },
  allowedAccessLevels: {
    type: Array,
    default: () => ['global', 'department', 'user']
  }
})

const emit = defineEmits(['update:modelValue'])

const isKnowledgeBasePermissionMode = computed(
  () => props.showEditScope && props.showKnowledgeBaseInheritance
)
const scopeOptions = computed(() => [
  {
    key: 'read_scope',
    title: '读取权限',
    profileTitle: '只读成员',
    badge: '基础访问',
    badgeColor: 'blue',
    description: '适合浏览、检索、问答和下载知识内容',
    scopePrompt: '选择可以读取知识库的成员范围',
    icon: BookOpen,
    capabilities: [
      { label: '浏览' },
      { label: '搜索' },
      { label: '引用问答' },
      { label: '下载' }
    ]
  },
  ...(props.showEditScope
    ? [
        {
          key: 'edit_scope',
          title: '文档编辑权限（包含读取权限）',
          profileTitle: '内容贡献者',
          badge: '推荐给普通用户',
          badgeColor: 'green',
          description: '可以维护内容，但不能执行删除和授权操作',
          scopePrompt: '选择可以上传、解析与入库的成员范围',
          icon: FilePenLine,
          capabilities: [
            { label: '上传' },
            { label: '解析' },
            { label: '入库' },
            { label: '更新元数据' },
            { label: '不可删除', denied: true }
          ]
        }
      ]
    : []),
  {
    key: 'manage_scope',
    title: props.showEditScope
      ? '共享管理权限（包含编辑与读取权限）'
      : '共享管理权限（包含读取权限）',
    profileTitle: '知识库管理员',
    badge: '含高风险操作',
    badgeColor: 'red',
    description: '负责成员授权、共享设置和知识库生命周期管理',
    scopePrompt: '额外授权范围（系统继承人员不会出现在候选列表中）',
    icon: ShieldCheck,
    capabilities: [
      { label: '成员授权' },
      { label: '共享设置' },
      { label: '删除文档', danger: true },
      { label: '删除知识库', danger: true }
    ]
  }
])

const permissionProfiles = [
  {
    key: 'reader',
    title: '只读成员',
    level: 'READ',
    description: '用于浏览、检索和问答，不允许修改知识库内容。',
    policyKey: 'read',
    order: 1
  },
  {
    key: 'contributor',
    title: '内容贡献者',
    level: 'EDIT',
    description: '继承只读能力，并可按需配置内容维护能力。',
    policyKey: 'edit',
    order: 2
  },
  {
    key: 'manager',
    title: '知识库管理员',
    level: 'MANAGE',
    description: '继承读取与内容维护能力，并可配置管理和高风险操作。',
    policyKey: 'manage',
    order: 3
  }
]
const permissionGroups = [
  {
    key: 'access',
    title: '访问与检索',
    items: [
      { key: 'view', level: 'read', title: '浏览知识库', description: '查看目录与文档内容' },
      { key: 'search', level: 'read', title: '搜索与问答', description: '用于检索增强与智能问答' },
      { key: 'download', level: 'read', title: '下载原文件', description: '下载有权访问的源文件' }
    ]
  },
  {
    key: 'content',
    title: '内容维护',
    items: [
      { key: 'upload', level: 'edit', title: '上传文件', description: '创建文件记录并上传源文件' },
      { key: 'metadata', level: 'edit', title: '更新元数据', description: '修改标题、目录和文件信息' },
      { key: 'parse', level: 'edit', title: '解析文件', description: '将文档转换为可处理文本' },
      { key: 'index', level: 'edit', title: '入库 / 重建索引', description: '生成索引并进入检索范围' }
    ]
  },
  {
    key: 'sharing',
    title: '成员与共享',
    items: [
      { key: 'configure', level: 'manage', title: '配置知识库', description: '调整模型、检索、图谱和维护配置' },
      { key: 'share', level: 'manage', title: '调整共享范围', description: '设置全局、部门或指定成员' },
      { key: 'grant', level: 'manage', title: '管理成员权限', description: '修改成员档位与能力树' }
    ]
  },
  {
    key: 'danger',
    title: '危险操作',
    items: [
      {
        key: 'delete-document',
        level: 'manage',
        title: '删除文档',
        description: '清理文档、分段与索引',
        danger: true
      },
      {
        key: 'delete-knowledge-base',
        level: 'manage',
        title: '删除知识库',
        description: '删除知识库及其关联数据',
        danger: true
      }
    ]
  }
]
const selectedPermissionProfile = computed(
  () => permissionProfiles.find((profile) => profile.key === activePermissionProfile.value) || permissionProfiles[0]
)
const capabilityPolicy = reactive(normalizeKnowledgeBaseCapabilityPolicy())
const permissionLevelOrder = { read: 1, edit: 2, manage: 3 }
// 高档位可以直接维护自身及已继承的低档位能力。
// 例如知识库管理员在 MANAGE 视图中取消 upload，实际修改的是
// capability_policy.edit，因此内容贡献者和管理员的有效能力都会同步变化。
const isCapabilityEditable = (item) =>
  permissionLevelOrder[item.level] <= selectedPermissionProfile.value.order
const isCapabilityInherited = (item) =>
  permissionLevelOrder[item.level] < selectedPermissionProfile.value.order &&
  capabilityPolicy[item.level].includes(item.key)
const isCapabilityEnabled = (item) => {
  if (permissionLevelOrder[item.level] > selectedPermissionProfile.value.order) return false
  return capabilityPolicy[item.level].includes(item.key)
}
const toggleCapability = (item, checked = null) => {
  if (props.disabled || !isCapabilityEditable(item)) return
  const values = capabilityPolicy[item.level]
  const shouldEnable = checked ?? !values.includes(item.key)
  capabilityPolicy[item.level] = shouldEnable
    ? Array.from(new Set([...values, item.key]))
    : values.filter((value) => value !== item.key)
}

const baseShareModeOptions = [
  { value: 'global', title: '全局共享', description: '所有用户都可以访问', icon: Globe },
  {
    value: 'department',
    title: '部门共享',
    description: '选中的部门成员可以访问',
    icon: Building2
  },
  { value: 'user', title: '指定人', description: '选中的用户可以访问', icon: Users }
]

const scopes = reactive({ read_scope: null, edit_scope: null, manage_scope: null })
const selectionSearch = reactive({
  read_scope: { department: '', user: '' },
  edit_scope: { department: '', user: '' },
  manage_scope: { department: '', user: '' }
})

const currentDepartmentId = computed(() =>
  userStore.departmentId ? Number(userStore.departmentId) : null
)
const currentUserUid = computed(() => userStore.uid || '')
const normalizedAllowedAccessLevels = computed(() => {
  const allowed = props.allowedAccessLevels.filter((level) =>
    ['global', 'department', 'user'].includes(level)
  )
  return allowed.length ? allowed : ['global']
})
const shareModeOptions = computed(() =>
  baseShareModeOptions.filter((option) =>
    normalizedAllowedAccessLevels.value.includes(option.value)
  )
)
const getShareModeOptions = (scopeKey) => {
  if (isKnowledgeBasePermissionMode.value && scopeKey === 'manage_scope') {
    return shareModeOptions.value.filter((option) => option.value === 'user')
  }
  return shareModeOptions.value
}

const createScope = (scope) => ({
  access_level: scope?.access_level || 'global',
  department_ids: Array.from(
    new Set((scope?.department_ids || []).map(Number).filter(Number.isFinite))
  ),
  user_uids: Array.from(
    new Set((scope?.user_uids || []).map((uid) => String(uid).trim()).filter(Boolean))
  )
})

const normalizeScope = (scope, { includeCurrent = false } = {}) => {
  if (!scope) return null
  const normalized = createScope(scope)
  if (!normalizedAllowedAccessLevels.value.includes(normalized.access_level)) {
    normalized.access_level = normalizedAllowedAccessLevels.value[0]
  }
  if (normalized.access_level === 'global') {
    normalized.department_ids = []
    normalized.user_uids = []
  } else if (normalized.access_level === 'department') {
    normalized.user_uids = []
    if (
      includeCurrent &&
      currentDepartmentId.value &&
      !normalized.department_ids.includes(currentDepartmentId.value)
    ) {
      normalized.department_ids.unshift(currentDepartmentId.value)
    }
  } else {
    normalized.department_ids = []
    if (
      includeCurrent &&
      currentUserUid.value &&
      !normalized.user_uids.includes(currentUserUid.value)
    ) {
      normalized.user_uids.unshift(currentUserUid.value)
    }
  }
  return normalized
}

const initConfig = () => {
  syncingFromProps.value = true
  const source = props.modelValue || {}
  const isV2 = source.version === 2
  const readScope = isV2 ? source.read_scope : source
  scopes.read_scope = normalizeScope(readScope, { includeCurrent: props.autoSelectUserDept })
  scopes.edit_scope = props.showEditScope ? normalizeScope(isV2 ? source.edit_scope : null) : null
  const manageScope = normalizeScope(isV2 ? source.manage_scope : null)
  scopes.manage_scope =
    isKnowledgeBasePermissionMode.value && manageScope?.access_level !== 'user'
      ? null
      : manageScope
  Object.assign(
    capabilityPolicy,
    normalizeKnowledgeBaseCapabilityPolicy(isV2 ? source.capability_policy : null)
  )
  const hadMissingRequiredRead = props.requireReadScope && !scopes.read_scope
  if (hadMissingRequiredRead) {
    scopes.read_scope = normalizeScope({ access_level: 'global' })
  }
  nextTick(() => {
    syncingFromProps.value = false
    if (hadMissingRequiredRead) emitConfig()
  })
}

const emitConfig = () => {
  const manageScope =
    isKnowledgeBasePermissionMode.value && scopes.manage_scope?.access_level !== 'user'
      ? null
      : normalizeScope(scopes.manage_scope)
  if (manageScope?.access_level === 'user') {
    manageScope.user_uids = manageScope.user_uids.filter(
      (uid) => !inheritedManagerUids.value.has(String(uid))
    )
  }
  const config = {
    version: 2,
    read_scope: normalizeScope(scopes.read_scope),
    manage_scope: manageScope
  }
  if (props.showEditScope) {
    config.edit_scope = normalizeScope(scopes.edit_scope)
    config.capability_policy = normalizeKnowledgeBaseCapabilityPolicy(capabilityPolicy)
  }
  emit('update:modelValue', config)
}

const toggleScope = (scopeKey, enabled) => {
  if (props.disabled) return
  const parentScope =
    scopeKey === 'manage_scope' && props.showEditScope
      ? scopes.edit_scope || scopes.read_scope
      : scopes.read_scope
  const defaultAccessLevel =
    isKnowledgeBasePermissionMode.value && scopeKey === 'manage_scope'
      ? 'user'
      : parentScope?.access_level || 'global'
  scopes[scopeKey] = enabled
    ? normalizeScope({ access_level: scopeKey === 'read_scope' ? 'global' : defaultAccessLevel })
    : null
}

const setAccessLevel = (scopeKey, accessLevel) => {
  if (
    props.disabled ||
    !scopes[scopeKey] ||
    (isKnowledgeBasePermissionMode.value &&
      scopeKey === 'manage_scope' &&
      accessLevel !== 'user') ||
    !normalizedAllowedAccessLevels.value.includes(accessLevel)
  )
    return
  scopes[scopeKey].access_level = accessLevel
  scopes[scopeKey] = normalizeScope(scopes[scopeKey], {
    includeCurrent: scopeKey === 'read_scope' && props.autoSelectUserDept
  })
}

const departmentOptions = computed(() =>
  (props.availableDepartments || departments.value).map((dept) => ({
    label: dept.name,
    value: Number(dept.id)
  }))
)
const userOptions = computed(() =>
  (props.availableUsers || users.value).map((user) => ({
    label: user.department_name ? `${user.username}（${user.department_name}）` : user.username,
    value: user.uid,
    department_id: user.department_id,
    role: user.role
  }))
)
const userDepartmentByUid = computed(() => {
  const departmentByUid = new Map(
    userOptions.value.map((user) => [String(user.value), user.department_id])
  )
  if (currentUserUid.value) {
    departmentByUid.set(String(currentUserUid.value), currentDepartmentId.value)
  }
  return departmentByUid
})
const isChildScopeWithinParent = (childScope, parentScope) =>
  isScopeWithinParent(childScope, parentScope, userDepartmentByUid.value)
const normalizedResourceDepartmentId = computed(() => {
  const value = Number(props.resourceDepartmentId)
  return Number.isFinite(value) ? value : null
})
const inheritedManagerOptions = computed(() => {
  const candidates = [...userOptions.value]
  if (userStore.uid) {
    candidates.push({
      label: userStore.departmentName
        ? `${userStore.username}（${userStore.departmentName}）`
        : userStore.username,
      value: userStore.uid,
      department_id: userStore.departmentId,
      role: userStore.userRole
    })
  }

  const unique = new Map()
  for (const candidate of candidates) {
    const isSuperAdmin = candidate.role === 'superadmin'
    const isOwnerDepartmentAdmin =
      candidate.role === 'admin' &&
      normalizedResourceDepartmentId.value !== null &&
      Number(candidate.department_id) === normalizedResourceDepartmentId.value
    if (!isSuperAdmin && !isOwnerDepartmentAdmin) continue
    unique.set(candidate.value, {
      ...candidate,
      inheritedLabel: isSuperAdmin ? '超级管理员' : '所属部门管理员'
    })
  }
  return Array.from(unique.values())
})
const inheritedManagerUids = computed(
  () => new Set(inheritedManagerOptions.value.map((manager) => String(manager.value)))
)

const getModeTitle = (scopeKey, option) =>
  scopeKey === 'manage_scope' && option.value === 'user' ? '指定协管成员' : option.title
const getModeDescription = (scopeKey, option) =>
  scopeKey === 'manage_scope' && option.value === 'user'
    ? '添加系统继承范围之外的协管人员'
    : option.description

const getAccessCount = (scopeKey, accessLevel) => {
  const scope = scopes[scopeKey]
  if (accessLevel === 'department') return scope?.department_ids.length || 0
  if (accessLevel === 'user') {
    const userUids = scope?.user_uids || []
    return scopeKey === 'manage_scope'
      ? userUids.filter((uid) => !inheritedManagerUids.value.has(String(uid))).length
      : userUids.length
  }
  return ''
}
const getScopeSummary = (scopeKey) => {
  const scope = scopes[scopeKey]
  if (!scope) return '未启用'
  if (scope.access_level === 'global') return '全局'
  if (scope.access_level === 'department') {
    return `${scope.department_ids.length} 个部门`
  }
  return `${getAccessCount(scopeKey, 'user')} 个成员`
}
const getPermissionProfileSummary = (profileKey) => {
  if (profileKey === 'reader') return getScopeSummary('read_scope')
  if (profileKey === 'contributor') return getScopeSummary('edit_scope')
  const extraScope = scopes.manage_scope ? getScopeSummary('manage_scope') : '无额外协管'
  return `系统继承已启用 · ${extraScope}`
}
const getScopeActionLabel = (scopeKey) => {
  if (scopeKey === 'manage_scope') return '可管理'
  if (scopeKey === 'edit_scope') return '可编辑'
  return '可读取'
}
const getAccessSummary = (scopeKey, accessLevel) => {
  const scope = scopes[scopeKey]
  if (accessLevel === 'global') return '所有用户可访问'
  if (accessLevel === 'department') return `${scope?.department_ids.length || 0} 个部门可访问`
  return `${getAccessCount(scopeKey, accessLevel)} 个用户可访问`
}
const getSelectionOptions = (scopeKey, accessLevel) => {
  let options = accessLevel === 'department' ? departmentOptions.value : userOptions.value
  if (scopeKey === 'manage_scope' && accessLevel === 'user') {
    options = options.filter((item) => !inheritedManagerUids.value.has(String(item.value)))
  }

  const query = selectionSearch[scopeKey][accessLevel].trim().toLowerCase()
  return query ? options.filter((item) => item.label.toLowerCase().includes(query)) : options
}
const isSelected = (scopeKey, accessLevel, value) => {
  const scope = scopes[scopeKey]
  if (!scope) return false
  return accessLevel === 'department'
    ? scope.department_ids.includes(Number(value))
    : scope.user_uids.includes(String(value))
}
const toggleSelection = (scopeKey, accessLevel, value, checked) => {
  if (props.disabled || !scopes[scopeKey]) return
  if (
    scopeKey === 'manage_scope' &&
    accessLevel === 'user' &&
    inheritedManagerUids.value.has(String(value))
  )
    return
  const scope = scopes[scopeKey]
  if (accessLevel === 'department') {
    scope.department_ids = Array.from(
      new Set(
        checked
          ? [...scope.department_ids, Number(value)]
          : scope.department_ids.filter((id) => id !== Number(value))
      )
    )
  } else {
    scope.user_uids = Array.from(
      new Set(
        checked
          ? [...scope.user_uids, String(value)]
          : scope.user_uids.filter((uid) => uid !== String(value))
      )
    )
  }
}

const loadDepartments = async () => {
  if (props.availableDepartments) return
  try {
    const result = await departmentApi.getDepartments()
    departments.value = result.departments || result || []
  } catch (error) {
    console.error('加载部门列表失败:', error)
  }
}
const loadUsers = async () => {
  if (props.availableUsers) return
  try {
    users.value = await authApi.getUserAccessOptions()
  } catch (error) {
    console.error('加载用户列表失败:', error)
  }
}

watch(() => props.modelValue, initConfig, { deep: true })
watch(normalizedAllowedAccessLevels, initConfig)
watch(() => props.showEditScope, initConfig)
watch(
  scopes,
  () => {
    if (!syncingFromProps.value) emitConfig()
  },
  { deep: true }
)
watch(
  capabilityPolicy,
  () => {
    if (!syncingFromProps.value) emitConfig()
  },
  { deep: true }
)

const validateScope = (scope, title) => {
  if (!scope || scope.access_level === 'global') return { valid: true, message: '' }
  if (scope.access_level === 'department' && !scope.department_ids.length) {
    return { valid: false, message: `${title}至少需要选择一个部门` }
  }
  if (scope.access_level === 'user' && !scope.user_uids.length) {
    return { valid: false, message: `${title}至少需要选择一个用户` }
  }
  return { valid: true, message: '' }
}

const validate = () => {
  const readResult = validateScope(scopes.read_scope, '读取权限')
  if (!readResult.valid) return readResult
  const editResult = validateScope(scopes.edit_scope, '编辑权限')
  if (!editResult.valid) return editResult
  const manageResult = validateScope(scopes.manage_scope, '管理权限')
  if (!manageResult.valid) return manageResult
  if (!isChildScopeWithinParent(scopes.edit_scope, scopes.read_scope)) {
    return { valid: false, message: '编辑权限必须包含在读取权限范围内' }
  }
  const manageParent =
    props.showEditScope && scopes.edit_scope ? scopes.edit_scope : scopes.read_scope
  if (!isChildScopeWithinParent(scopes.manage_scope, manageParent)) {
    return {
      valid: false,
      message:
        props.showEditScope && scopes.edit_scope
          ? '管理权限必须包含在编辑权限范围内'
          : '管理权限必须包含在读取权限范围内'
    }
  }
  return manageResult
}

const scopeViolationMessage = computed(() => {
  if (scopes.edit_scope && !isChildScopeWithinParent(scopes.edit_scope, scopes.read_scope)) {
    return '当前编辑权限范围大于读取权限范围，请调整后再保存。'
  }
  const manageParent =
    props.showEditScope && scopes.edit_scope ? scopes.edit_scope : scopes.read_scope
  if (scopes.manage_scope && !isChildScopeWithinParent(scopes.manage_scope, manageParent)) {
    return props.showEditScope && scopes.edit_scope
      ? '当前管理权限范围大于编辑权限范围，请调整后再保存。'
      : '当前管理权限范围大于读取权限范围，请调整后再保存。'
  }
  return ''
})

onMounted(() => {
  initConfig()
  loadDepartments()
  loadUsers()
})

defineExpose({ scopes, validate })
</script>

<style lang="less" scoped>
.share-config-form {
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--gray-150);
}

.permission-mode-tabs {
  display: flex;
  gap: 24px;
  margin: 0 0 16px;
  border-bottom: 1px solid var(--gray-150);
}

.permission-mode-tab {
  position: relative;
  padding: 14px 2px 12px;
  border: 0;
  background: transparent;
  color: var(--gray-500);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;

  &::after {
    position: absolute;
    right: 0;
    bottom: -1px;
    left: 0;
    height: 2px;
    background: transparent;
    content: '';
  }

  &.active {
    color: var(--main-color);

    &::after {
      background: var(--main-color);
    }
  }
}

.quick-permission-intro,
.advanced-permission-intro {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.quick-permission-intro > div,
.advanced-permission-intro > div {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.quick-permission-intro strong,
.advanced-permission-intro h4 {
  margin: 0;
  color: var(--gray-800);
  font-size: 15px;
}

.quick-permission-intro span,
.advanced-permission-intro p {
  margin: 0;
  color: var(--gray-500);
  font-size: 12px;
}

.permission-role-card {
  margin-bottom: 12px;
  padding: 14px;
  border: 1px solid var(--gray-200);
  border-radius: 10px;
  background: var(--gray-0);
}

.permission-role-heading {
  display: grid;
  flex: 1;
  grid-template-columns: auto minmax(170px, 0.75fr) minmax(260px, 1fr);
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.permission-role-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 8px;
  background: var(--main-10);
  color: var(--main-color);
}

.permission-role-copy,
.permission-role-title-row {
  display: flex;
  min-width: 0;
}

.permission-role-copy {
  flex-direction: column;
  gap: 3px;
}

.permission-role-title-row {
  align-items: center;
  gap: 8px;
}

.permission-role-title-row h4,
.permission-role-copy p {
  margin: 0;
}

.permission-role-copy p {
  overflow: hidden;
  color: var(--gray-500);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.permission-capabilities {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.permission-capability {
  padding: 2px 7px;
  border-radius: 999px;
  background: var(--gray-100);
  color: var(--gray-600);
  font-size: 11px;
  white-space: nowrap;

  &.denied,
  &.danger {
    background: color-mix(in srgb, var(--error-color, #ff4d4f) 9%, transparent);
    color: var(--error-color, #cf1322);
  }
}

.permission-scope-actions {
  display: flex;
  flex: none;
  align-items: center;
  gap: 8px;
}

.system-enabled-label,
.extra-scope-label {
  font-size: 11px;
  white-space: nowrap;
}

.system-enabled-label {
  padding: 3px 7px;
  border-radius: 5px;
  background: var(--main-10);
  color: var(--main-color);
}

.extra-scope-label {
  color: var(--gray-500);
}

.scope-label {
  margin: 14px 0 8px;
  color: var(--gray-600);
  font-size: 12px;
  font-weight: 600;
}

.scope-guidance {
  margin-top: 10px;
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--main-10);
  color: var(--gray-600);
  font-size: 12px;
  line-height: 1.5;
}

.inherited-managers-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 12px;
  padding: 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-25);
}

.inherited-managers-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.inherited-managers-heading h4,
.inherited-managers-heading p {
  margin: 0;
}

.inherited-managers-heading p,
.inherited-manager-empty {
  margin-top: 4px;
  color: var(--gray-500);
  font-size: 12px;
  line-height: 18px;
}

.inherited-manager-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.inherited-manager-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
  padding: 8px 10px;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  background: var(--gray-25);
  color: var(--gray-700);
  font-size: 13px;
}

.permission-scope-section {
  padding: 14px 0;
  border: 0;
  border-radius: 0;
  background: transparent;
}

.advanced-permission-panel {
  padding: 4px 0 10px;
}

.advanced-permission-layout {
  display: grid;
  grid-template-columns: minmax(190px, 0.3fr) minmax(0, 1fr);
  gap: 12px;
}

.permission-profile-list,
.permission-tree-panel {
  border: 1px solid var(--gray-200);
  border-radius: 10px;
  background: var(--gray-0);
}

.permission-profile-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px;
}

.permission-profile-button {
  display: flex;
  align-items: flex-start;
  flex-direction: column;
  gap: 3px;
  padding: 10px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--gray-700);
  text-align: left;
  cursor: pointer;

  span {
    color: var(--gray-500);
    font-size: 11px;
  }

  &:hover,
  &.active {
    border-color: var(--main-color);
    background: var(--main-10);
  }
}

.permission-profile-list > p {
  margin: auto 2px 2px;
  color: var(--gray-500);
  font-size: 11px;
  line-height: 1.55;
}

.permission-tree-panel {
  min-width: 0;
  padding: 14px;
}

.permission-tree-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--gray-150);
}

.permission-tree-header h4,
.permission-tree-header p {
  margin: 0;
}

.permission-tree-header p {
  margin-top: 4px;
  color: var(--gray-500);
  font-size: 12px;
}

.permission-tree-groups {
  display: grid;
  gap: 10px;
  padding-top: 12px;
}

.permission-tree-group {
  display: grid;
  grid-template-columns: 100px minmax(0, 1fr);
  gap: 10px;
}

.permission-tree-group-title {
  color: var(--gray-700);
  font-size: 12px;
  font-weight: 600;
}

.permission-tree-items {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px;
}

.permission-tree-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px;
  border: 1px solid var(--gray-150);
  border-radius: 7px;
  color: var(--gray-400);

  &.editable {
    cursor: pointer;
  }

  &.inherited {
    border-style: dashed;
  }

  &.enabled {
    border-color: color-mix(in srgb, var(--main-color) 38%, var(--gray-150));
    background: var(--main-10);
    color: var(--gray-700);
  }

  &.danger.enabled {
    border-color: color-mix(in srgb, var(--error-color, #ff4d4f) 36%, var(--gray-150));
    background: color-mix(in srgb, var(--error-color, #ff4d4f) 7%, transparent);
  }

  strong,
  small {
    display: block;
  }

  > span:not(.ant-tag) {
    flex: 1;
    min-width: 0;
  }

  strong {
    font-size: 12px;
  }

  small {
    margin-top: 2px;
    color: var(--gray-500);
    font-size: 10px;
    line-height: 1.4;
  }
}

.permission-inherited-tag {
  flex: none;
  margin-left: auto;
  font-size: 10px;
  white-space: nowrap;
}

.permission-tree-status {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--gray-100);
  font-size: 11px;
}

.permission-tree-item.enabled .permission-tree-status {
  background: var(--main-color);
  color: white;
}

.permission-scope-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;

  &.has-content {
    margin-bottom: 12px;
  }
}

.permission-scope-header h4 {
  margin: 0;
  color: var(--gray-800);
  font-size: 14px;
}

.share-mode-cards {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.share-mode-card {
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--gray-200);
  border-radius: 10px;
  background: var(--gray-0);
  cursor: pointer;
}

.share-mode-card:hover,
.share-mode-card.active {
  border-color: var(--main-color);
  background: var(--main-10);
}

.card-header,
.card-main {
  display: flex;
  align-items: center;
  gap: 8px;
}

.card-main {
  align-items: stretch;
  flex-direction: column;
}

.card-title {
  flex: 1;
  color: var(--gray-800);
  font-size: 13px;
  font-weight: 600;
  line-height: 20px;
  height: 20px;
  display: flex;
  align-items: center;
}

.card-description {
  color: var(--gray-500);
  font-size: 12px;
  line-height: 1.45;
}

.card-icon-wrapper {
  display: inline-flex;
  align-items: center;
  color: var(--main-color);
  height: 20px;
}

.card-action {
  margin-left: auto;
  display: flex;
  align-items: center;
  height: 20px;

  :deep(.ant-btn) {
    height: 20px;
    padding: 0 8px;
    font-size: 12px;
    line-height: 1;
  }
}

.access-count {
  margin-left: 3px;
}

.share-disabled-alert {
  margin-top: 2px;
}

.selection-dropdown {
  width: 280px;
  padding: 10px;
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  background: var(--gray-0);
  box-shadow: 0 8px 24px rgb(0 0 0 / 12%);
}

.selection-dropdown-header {
  margin-bottom: 8px;
}

.selection-dropdown-title {
  color: var(--gray-800);
  font-size: 13px;
  font-weight: 600;
}

.selection-dropdown-subtitle,
.selection-empty {
  color: var(--gray-500);
  font-size: 12px;
}

.selection-search {
  margin-bottom: 8px;
}

.selection-list {
  max-height: 240px;
  overflow-y: auto;
}

.selection-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 7px 4px;
  border-radius: 6px;
  cursor: pointer;
}

.selection-item:hover,
.selection-item.selected {
  background: var(--main-10);
}

.selection-item-content {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
}

.selection-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 768px) {
  .permission-role-heading,
  .advanced-permission-layout,
  .permission-tree-group {
    grid-template-columns: 1fr;
  }

  .permission-role-icon {
    display: none;
  }

  .permission-scope-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .permission-tree-items {
    grid-template-columns: 1fr;
  }

  .share-mode-cards {
    grid-template-columns: 1fr;
  }
}
</style>
