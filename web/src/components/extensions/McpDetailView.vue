<template>
  <ExtensionDetailLayout
    v-model:active-key="detailTab"
    :tabs="mcpDetailTabs"
    :loading="loading"
    :ready="Boolean(server)"
    empty-description="未找到 MCP 服务器"
    class="mcp-detail"
  >
    <template #breadcrumb>
      <nav class="extension-detail-breadcrumb" aria-label="MCP 详情导航">
        <button type="button" class="extension-detail-back" @click="goBack">MCP</button>
        <ChevronRight :size="15" aria-hidden="true" />
        <span class="extension-detail-current" :title="server?.name || slug">
          {{ server?.name || slug }}
        </span>
      </nav>
    </template>

    <template #actions>
      <div v-if="userStore.isAdmin" class="extension-detail-actions">
        <a-space :size="8">
          <button
            type="button"
            :aria-label="testLoading ? '正在测试 MCP' : '测试 MCP'"
            @click="handleTestServer"
            :disabled="testLoading || !server?.enabled || server?.requires_migration"
            :title="server?.requires_migration ? '请先迁移为远程 MCP' : ''"
            class="lucide-icon-btn extension-panel-action extension-panel-action-secondary"
          >
            <Zap :size="14" v-if="!testLoading" />
            <span>测试</span>
          </button>
          <button
            type="button"
            aria-label="编辑 MCP"
            @click="startEdit"
            :disabled="isEditing || !server || server.is_builtin"
            :title="server?.is_builtin ? '系统内置 MCP 的连接配置由代码管理' : ''"
            class="lucide-icon-btn extension-panel-action extension-panel-action-secondary"
          >
            <Pencil :size="14" />
            <span>编辑</span>
          </button>
          <button
            type="button"
            :aria-label="`${actionLabel} MCP`"
            @click="handleDangerAction"
            :class="[
              'lucide-icon-btn',
              'extension-panel-action',
              server?.requires_migration
                ? 'extension-panel-action-danger'
                : server?.enabled === false
                  ? 'extension-panel-action-primary'
                  : 'extension-panel-action-secondary'
            ]"
          >
            <Plus v-if="server?.enabled === false && !server?.requires_migration" :size="14" />
            <PowerOff v-else-if="!server?.requires_migration" :size="14" />
            <Trash2 v-else :size="14" />
            <span>{{ actionLabel }}</span>
          </button>
          <button
            v-if="server && !server.is_builtin && !server.requires_migration"
            type="button"
            aria-label="删除 MCP"
            @click="confirmDeleteServer(server)"
            class="lucide-icon-btn extension-panel-action extension-panel-action-danger"
          >
            <Trash2 :size="14" />
            <span>删除</span>
          </button>
        </a-space>
      </div>
    </template>

    <template #panel-general>
      <div class="extension-detail-view mcp-general-view">
        <a-alert
          v-if="server.enabled === false && !server.requires_migration"
          type="info"
          show-icon
          class="migration-alert"
          :message="userStore.isAdmin ? '此 MCP 已停用。可先配置权限，再启用服务。' : '此 MCP 已停用，当前无法在智能体中使用。'"
        />
        <a-alert
          v-if="server.requires_migration"
          type="warning"
          show-icon
          class="migration-alert"
          message="此 stdio MCP 已禁用，请编辑为 SSE 或 Streamable HTTP，或直接删除。"
        />
        <div v-if="isEditing" class="edit-panel">
          <div class="edit-panel-header">
            <div>
              <h3>编辑 MCP</h3>
              <p>修改后保存会立即更新当前 MCP 配置。</p>
            </div>
          </div>

          <a-form layout="vertical" class="extension-form inline-edit-form">
            <section class="form-section">
              <div class="form-section-title">
                <span>基础信息</span>
                <small>定义 MCP 的名称、描述与展示方式。</small>
              </div>
              <div class="form-grid form-grid-three">
                <a-form-item label="MCP 标识" required class="form-item">
                  <a-input v-model:value="editForm.slug" disabled />
                </a-form-item>
                <a-form-item label="MCP 名称" required class="form-item">
                  <a-input v-model:value="editForm.name" placeholder="请输入 MCP 展示名称" />
                </a-form-item>
                <a-form-item label="传输类型" required class="form-item">
                  <a-select v-model:value="editForm.transport">
                    <a-select-option value="streamable_http">streamable_http</a-select-option>
                    <a-select-option value="sse">sse</a-select-option>
                  </a-select>
                </a-form-item>
                <a-form-item label="图标" class="form-item">
                  <a-input
                    v-model:value="editForm.icon"
                    placeholder="输入 emoji，如 🧠"
                    :maxlength="2"
                  />
                </a-form-item>
              </div>
              <a-form-item label="描述" class="form-item form-item-full">
                <a-textarea
                  v-model:value="editForm.description"
                  placeholder="请输入 MCP 描述"
                  :rows="2"
                />
              </a-form-item>
            </section>

            <section class="form-section">
              <div class="form-section-title">
                <span>连接配置</span>
                <small>配置当前传输方式需要的连接参数。</small>
              </div>
              <template
                v-if="editForm.transport === 'streamable_http' || editForm.transport === 'sse'"
              >
                <a-form-item label="MCP URL" required class="form-item form-item-full">
                  <a-input v-model:value="editForm.url" placeholder="https://example.com/mcp" />
                </a-form-item>
                <div class="form-grid">
                  <a-form-item label="HTTP 超时（秒）" class="form-item">
                    <a-input-number
                      v-model:value="editForm.timeout"
                      :min="1"
                      :max="300"
                      style="width: 100%"
                    />
                  </a-form-item>
                  <a-form-item label="SSE 读取超时（秒）" class="form-item">
                    <a-input-number
                      v-model:value="editForm.sse_read_timeout"
                      :min="1"
                      :max="300"
                      style="width: 100%"
                    />
                  </a-form-item>
                </div>
              </template>
              <a-form-item label="标签" class="form-item form-item-full">
                <a-select
                  v-model:value="editForm.tags"
                  mode="tags"
                  placeholder="输入标签后回车添加"
                  style="width: 100%"
                />
              </a-form-item>
            </section>

            <section class="form-section">
              <div class="form-section-title">
                <span>高级配置</span>
                <small>请求头会直接影响 MCP 连接。</small>
              </div>
              <template
                v-if="editForm.transport === 'streamable_http' || editForm.transport === 'sse'"
              >
                <a-form-item label="HTTP 请求头" class="form-item form-item-full">
                  <a-textarea
                    v-model:value="editForm.headersText"
                    placeholder='JSON 格式，如：{"Authorization": "Bearer xxx"}'
                    :rows="4"
                    class="config-textarea"
                  />
                  <div class="form-helper">请输入合法 JSON 对象，留空表示不发送额外请求头。</div>
                </a-form-item>
              </template>
            </section>
          </a-form>

          <div class="edit-panel-actions">
            <a-button
              size="small"
              @click="cancelEdit"
              :disabled="editLoading"
              class="lucide-icon-btn"
            >
              <template #icon><X :size="14" /></template>
              取消
            </a-button>
            <a-button
              type="primary"
              size="small"
              @click="handleSaveEdit"
              :loading="editLoading"
              class="lucide-icon-btn"
            >
              <template #icon><Save :size="14" /></template>
              保存
            </a-button>
          </div>
        </div>

        <div v-else class="info-grid extension-detail-divider-list">
          <div class="info-item" v-if="server.description">
            <label>描述</label>
            <span>{{ server.description }}</span>
          </div>
          <div class="info-item" v-if="server.transport">
            <label>传输类型</label>
            <span>
              <a-tag :color="getTransportColor(server.transport)">{{ server.transport }}</a-tag>
            </span>
          </div>
          <div class="info-item" v-if="Array.isArray(server.tags) && server.tags.length > 0">
            <label>标签</label>
            <span>
              <a-tag v-for="tag in server.tags" :key="tag">{{ tag }}</a-tag>
            </span>
          </div>
          <template
            v-if="
              userStore.isAdmin &&
              (server.transport === 'streamable_http' || server.transport === 'sse')
            "
          >
            <div class="info-item" v-if="server.url">
              <label>MCP URL</label>
              <span class="code-inline text-break-all">{{ server.url }}</span>
            </div>
            <div class="info-item" v-if="server.headers && Object.keys(server.headers).length > 0">
              <label>请求头</label>
              <pre class="code-pre">{{ JSON.stringify(server.headers, null, 2) }}</pre>
            </div>
            <div class="info-item" v-if="server.timeout">
              <label>HTTP 超时</label>
              <span>{{ server.timeout }} 秒</span>
            </div>
            <div class="info-item" v-if="server.sse_read_timeout">
              <label>SSE 读取超时</label>
              <span>{{ server.sse_read_timeout }} 秒</span>
            </div>
          </template>
          <template v-if="userStore.isAdmin && server.transport === 'stdio'">
            <div class="info-item" v-if="server.command">
              <label>命令</label>
              <span class="code-inline">{{ server.command }}</span>
            </div>
            <div class="info-item" v-if="server.args && server.args.length > 0">
              <label>参数</label>
              <span>
                <a-tag v-for="(arg, index) in server.args" :key="index" size="small">{{
                  arg
                }}</a-tag>
              </span>
            </div>
            <div class="info-item" v-if="server.env && Object.keys(server.env).length > 0">
              <label>环境变量</label>
              <pre class="code-pre">{{ JSON.stringify(server.env, null, 2) }}</pre>
            </div>
          </template>
          <div class="info-item" v-if="server.created_at">
            <label>创建时间</label>
            <span>{{ formatTime(server.created_at) }}</span>
          </div>
          <div class="info-item" v-if="server.updated_at">
            <label>更新时间</label>
            <span>{{ formatTime(server.updated_at) }}</span>
          </div>
          <div class="info-item" v-if="server.created_by">
            <label>创建人</label>
            <span>{{ server.created_by }}</span>
          </div>
          <div class="info-item" v-if="server.permission && !userStore.isAdmin">
            <label>我的权限</label>
            <span>{{ permissionLabel }}</span>
          </div>
        </div>
      </div>
    </template>

    <template #panel-tools>
      <div class="extension-detail-view extension-detail-gray-switches tools-tab">
        <div class="tools-toolbar">
          <a-input-search
            v-model:value="toolSearchText"
            placeholder="搜索工具..."
            style="width: 200px"
            allowClear
          />
          <a-button
            size="small"
            @click="fetchTools"
            :loading="toolsLoading"
            class="lucide-icon-btn"
          >
            <RotateCw :size="14" />
            <span>刷新</span>
          </a-button>
        </div>
        <a-spin :spinning="toolsLoading">
          <div v-if="filteredTools.length === 0" class="empty-tools">
            <a-empty :description="toolsError || '暂无工具'" />
          </div>
          <div v-else class="tools-list extension-detail-divider-list">
            <div
              v-for="tool in filteredTools"
              :key="tool.name"
              class="tool-card extension-detail-divider-row"
              :class="{ disabled: !tool.enabled }"
            >
              <div class="tool-header">
                <div class="tool-info">
                  <span class="tool-name">{{ tool.name }}</span>
                  <a-tooltip :title="`ID: ${tool.id}`">
                    <Info :size="14" class="info-icon" />
                  </a-tooltip>
                </div>
                <div class="tool-actions">
                  <a-switch
                    v-if="userStore.isAdmin"
                    :checked="tool.enabled"
                    :aria-label="`${tool.name} ${tool.enabled ? '已启用' : '已禁用'}`"
                    @change="handleToggleTool(tool)"
                    :loading="toggleToolLoading === tool.name"
                    size="small"
                  />
                  <a-tag v-else>{{ tool.enabled ? '已启用' : '已禁用' }}</a-tag>
                  <a-tooltip title="复制工具名称">
                    <a-button
                      type="text"
                      size="small"
                      :aria-label="`复制工具名称 ${tool.name}`"
                      @click="copyToolName(tool.name)"
                      class="lucide-icon-btn"
                    >
                      <Copy :size="14" />
                    </a-button>
                  </a-tooltip>
                </div>
              </div>
              <div class="tool-description" v-if="tool.description">
                {{ tool.description }}
              </div>
              <a-collapse v-if="tool.parameters && Object.keys(tool.parameters).length > 0" ghost>
                <a-collapse-panel key="params" header="参数">
                  <div class="params-list">
                    <div
                      v-for="(param, paramName) in tool.parameters"
                      :key="paramName"
                      class="param-item"
                    >
                      <div class="param-header">
                        <span class="param-name">{{ paramName }}</span>
                        <span class="param-required" v-if="tool.required?.includes(paramName)"
                          >必填</span
                        >
                        <span class="param-type">{{ param.type || 'any' }}</span>
                      </div>
                      <div class="param-desc" v-if="param.description">
                        {{ param.description }}
                      </div>
                    </div>
                  </div>
                </a-collapse-panel>
              </a-collapse>
            </div>
          </div>
        </a-spin>
      </div>
    </template>

    <template #panel-sharing>
      <div v-if="canManageSharing" class="extension-detail-view mcp-sharing-view">
        <div class="sharing-header">
          <div>
            <h3>共享权限</h3>
            <p>分别选择可查看 MCP、在智能体中使用工具，以及可调整授权范围的成员。</p>
          </div>
          <a-button
            type="primary"
            size="small"
            :disabled="!sharingOptionsLoaded || Boolean(sharingOptionsError)"
            :loading="savingSharing"
            @click="saveSharing"
          >
            <Save :size="14" />
            <span>保存权限</span>
          </a-button>
        </div>
        <a-alert
          type="info"
          show-icon
          :message="userStore.isAdmin
            ? '被授权的管理者可调整此 MCP 的共享范围；连接配置、测试、启停和工具开关仍由系统管理员管理。'
            : '你只能给本部门或本部门成员授权。既有全员和其他部门授权会保留，由系统管理员调整。'"
          class="sharing-hint"
        />
        <a-alert v-if="sharingOptionsError" type="error" show-icon class="sharing-hint">
          <template #message>
            无法加载可授权成员。<a-button type="link" size="small" @click="fetchSharingOptions"
              >重试</a-button
            >
          </template>
        </a-alert>
        <a-spin :spinning="sharingOptionsLoading">
          <ShareConfigForm
            ref="shareConfigFormRef"
            v-model="shareConfigForm"
            resource-kind="mcp"
            :show-edit-scope="true"
            :show-manage-scope="true"
            :disabled="!sharingOptionsLoaded || Boolean(sharingOptionsError)"
            :available-departments="sharingDepartments"
            :available-users="sharingUsers"
            :allowed-access-levels="['global', 'department', 'user']"
            :restrict-new-global="!userStore.isAdmin"
            :existing-share-config="sharingBaseline"
            :protected-scopes="protectedScopes"
          />
        </a-spin>
      </div>
    </template>
  </ExtensionDetailLayout>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import {
  ChevronRight,
  Zap,
  Pencil,
  Trash2,
  Plus,
  PowerOff,
  RotateCw,
  Info,
  Copy,
  Settings2,
  Wrench,
  ShieldCheck,
  Save,
  X
} from '@lucide/vue'
import ExtensionDetailLayout from '@/components/shared/ExtensionDetailLayout.vue'
import ShareConfigForm from '@/components/ShareConfigForm.vue'
import { mcpApi } from '@/apis/mcp_api'
import { useUserStore } from '@/stores/user'
import { formatFullDateTime } from '@/utils/time'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const slug = computed(() => decodeURIComponent(route.params.slug ?? route.params.name))

const loading = ref(false)
const server = ref(null)
const detailTab = ref('general')
const testLoading = ref(null)

const tools = ref([])
const toolsLoading = ref(false)
const toolsError = ref(null)
const toolSearchText = ref('')
const toggleToolLoading = ref(null)
const canUse = computed(
  () =>
    server.value?.enabled !== false &&
    (userStore.isAdmin || ['edit', 'manage'].includes(server.value?.permission))
)
const canManageSharing = computed(
  () => userStore.isAdmin || server.value?.can_manage_sharing === true
)
const permissionLabel = computed(
  () =>
    ({
      read: '查看',
      edit: '使用',
      manage: '授权'
    })[server.value?.permission] || '查看'
)

const mcpDetailTabs = computed(() => {
  const tabs = [{ key: 'general', label: '信息', icon: Settings2 }]
  if (canUse.value && !server.value?.requires_migration) {
    tabs.push({ key: 'tools', label: `工具 (${tools.value.length})`, icon: Wrench })
  }
  if (canManageSharing.value) {
    tabs.push({ key: 'sharing', label: '权限', icon: ShieldCheck })
  }
  return tabs
})

const savingSharing = ref(false)
const sharingOptionsLoading = ref(false)
const sharingOptionsLoaded = ref(false)
const sharingOptionsError = ref(false)
const sharingDepartments = ref([])
const sharingUsers = ref([])
const shareConfigFormRef = ref(null)
const shareConfigForm = ref({ version: 2, read_scope: null, edit_scope: null, manage_scope: null })
const sharingBaseline = ref(null)
const protectedScopes = ref({})

/** 为旧 MCP 保留原有的全员查看、使用范围。 */
const cloneSharing = (config) => {
  const source = config || {
    version: 2,
    read_scope: { access_level: 'global', department_ids: [], user_uids: [] },
    edit_scope: { access_level: 'global', department_ids: [], user_uids: [] },
    manage_scope: null
  }
  return {
    version: 2,
    read_scope: source.read_scope ? structuredClone(source.read_scope) : null,
    edit_scope: source.edit_scope ? structuredClone(source.edit_scope) : null,
    manage_scope: source.manage_scope ? structuredClone(source.manage_scope) : null
  }
}

/** 加载当前 MCP 可授予的部门与用户。 */
const fetchSharingOptions = async () => {
  if (!server.value || !canManageSharing.value || sharingOptionsLoading.value) return
  try {
    sharingOptionsLoading.value = true
    sharingOptionsError.value = false
    const result = await mcpApi.getMcpServerSharingOptions(server.value.slug)
    if (!result.success) throw new Error(result.message || '加载失败')
    sharingDepartments.value = result.data?.departments || []
    sharingUsers.value = result.data?.users || []
    protectedScopes.value = result.data?.protected_scopes || {}
    sharingBaseline.value = cloneSharing(result.data?.share_config)
    shareConfigForm.value = cloneSharing(result.data?.share_config)
    sharingOptionsLoaded.value = true
  } catch {
    sharingOptionsLoaded.value = false
    sharingOptionsError.value = true
  } finally {
    sharingOptionsLoading.value = false
  }
}

/** 校验并保存 MCP 的共享范围，以服务端返回的配置为准。 */
const saveSharing = async () => {
  if (!server.value || !canManageSharing.value) return
  const validation = shareConfigFormRef.value?.validate?.()
  if (validation && !validation.valid) {
    message.warning(validation.message || '请检查授权范围')
    return
  }
  try {
    savingSharing.value = true
    const result = await mcpApi.updateMcpServerSharing(server.value.slug, shareConfigForm.value)
    if (!result.success) {
      message.error(result.message || '保存权限失败')
      return
    }
    if (result.data) {
      server.value = result.data
      if (canManageSharing.value) {
        await fetchSharingOptions()
      } else {
        detailTab.value = 'general'
      }
    } else {
      await fetchServer()
    }
    message.success('权限已保存')
  } catch (err) {
    message.error(err.message || '保存权限失败')
  } finally {
    savingSharing.value = false
  }
}

const isEditing = ref(false)
const editLoading = ref(false)

const editForm = reactive({
  slug: '',
  name: '',
  description: '',
  transport: 'streamable_http',
  url: '',
  headersText: '',
  timeout: null,
  sse_read_timeout: null,
  tags: [],
  icon: ''
})

const actionLabel = computed(() => {
  if (server.value?.requires_migration) return '删除'
  if (server.value?.enabled === false) return '启用'
  return '停用'
})

const filteredTools = computed(() => {
  if (!toolSearchText.value) return tools.value
  const search = toolSearchText.value.toLowerCase()
  return tools.value.filter(
    (t) =>
      t.name.toLowerCase().includes(search) ||
      (t.description && t.description.toLowerCase().includes(search))
  )
})

const goBack = () => {
  router.push({ path: '/extensions', query: { tab: 'mcp' } })
}

const formatTime = (timeStr) => formatFullDateTime(timeStr)

const getTransportColor = (transport) => {
  const colors = { sse: 'orange', stdio: 'green', streamable_http: 'blue' }
  return colors[transport] || 'blue'
}

const resetEditForm = (data) => {
  Object.assign(editForm, {
    slug: data?.slug || '',
    name: data?.name || '',
    description: data?.description || '',
    transport: data?.requires_migration ? 'streamable_http' : data?.transport || 'streamable_http',
    url: data?.url || '',
    headersText: data?.headers ? JSON.stringify(data.headers, null, 2) : '',
    timeout: data?.timeout,
    sse_read_timeout: data?.sse_read_timeout,
    tags: data?.tags || [],
    icon: data?.icon || ''
  })
}

const startEdit = () => {
  if (!userStore.isAdmin || !server.value) return
  detailTab.value = 'general'
  resetEditForm(server.value)
  isEditing.value = true
}

const cancelEdit = () => {
  isEditing.value = false
  resetEditForm(server.value)
}

const buildEditPayload = () => {
  let headers = null
  if (editForm.headersText.trim()) {
    try {
      headers = JSON.parse(editForm.headersText)
    } catch {
      message.error('请求头 JSON 格式错误')
      return null
    }
  }

  return {
    name: editForm.name,
    description: editForm.description || null,
    transport: editForm.transport,
    url: editForm.url || null,
    headers,
    timeout: editForm.timeout || null,
    sse_read_timeout: editForm.sse_read_timeout || null,
    tags: editForm.tags.length > 0 ? editForm.tags : null,
    icon: editForm.icon || null
  }
}

const validateEditPayload = (data) => {
  if (!data.name?.trim()) {
    message.error('MCP 名称不能为空')
    return false
  }
  if (!data.transport) {
    message.error('请选择传输类型')
    return false
  }
  if (['sse', 'streamable_http'].includes(data.transport) && !data.url?.trim()) {
    message.error('HTTP 类型必须填写 MCP URL')
    return false
  }
  return true
}

const handleSaveEdit = async () => {
  if (!userStore.isAdmin || !server.value) return
  const data = buildEditPayload()
  if (!data || !validateEditPayload(data)) return

  try {
    editLoading.value = true
    const result = await mcpApi.updateMcpServer(server.value.slug, data)
    if (result.success) {
      message.success('MCP 更新成功')
      isEditing.value = false
      await fetchServer()
    } else {
      message.error(result.message || '更新失败')
    }
  } catch (err) {
    message.error(err.message || '更新失败')
  } finally {
    editLoading.value = false
  }
}

const fetchServer = async () => {
  try {
    loading.value = true
    const result = await mcpApi.getMcpServer(slug.value)
    if (result.success) {
      server.value = result.data
      if (canManageSharing.value && userStore.isAdmin) {
        sharingBaseline.value = cloneSharing(result.data.share_config)
        shareConfigForm.value = cloneSharing(result.data.share_config)
      }
      if (detailTab.value === 'sharing' && !canManageSharing.value) detailTab.value = 'general'
      if (detailTab.value === 'tools' && !canUse.value) detailTab.value = 'general'
    } else {
      message.error(result.message || '获取 MCP 详情失败')
    }
  } catch (err) {
    message.error(err.message || '获取 MCP 详情失败')
  } finally {
    loading.value = false
  }
}

const fetchTools = async () => {
  if (!server.value || !canUse.value) return
  try {
    toolsLoading.value = true
    toolsError.value = null
    const result = await mcpApi.getMcpServerTools(server.value.slug)
    if (result.success) {
      tools.value = result.data || []
    } else {
      toolsError.value = result.message || '获取工具列表失败'
      tools.value = []
    }
  } catch (err) {
    toolsError.value = err.message || '获取工具列表失败'
    tools.value = []
  } finally {
    toolsLoading.value = false
  }
}

const handleToggleTool = async (tool) => {
  if (!userStore.isAdmin || !server.value) return
  try {
    toggleToolLoading.value = tool.name
    const result = await mcpApi.toggleMcpServerTool(server.value.slug, tool.name)
    if (result.success) {
      message.success(result.message)
      const targetTool = tools.value.find((t) => t.name === tool.name)
      if (targetTool) targetTool.enabled = result.enabled
    } else {
      message.error(result.message || '操作失败')
    }
  } catch (err) {
    message.error(err.message || '操作失败')
  } finally {
    toggleToolLoading.value = null
  }
}

const copyToolName = async (toolName) => {
  try {
    await navigator.clipboard.writeText(toolName)
    message.success('已复制到剪贴板')
  } catch {
    message.error('复制失败')
  }
}

const handleTestServer = async () => {
  if (!userStore.isAdmin || !server.value) return
  try {
    testLoading.value = server.value.name
    const result = await mcpApi.testMcpServer(server.value.slug)
    if (result.success) {
      message.success(result.message)
    } else {
      message.warning(result.message || '连接失败')
    }
  } catch (err) {
    message.error(err.message || '测试失败')
  } finally {
    testLoading.value = null
  }
}

const handleDangerAction = async () => {
  if (!userStore.isAdmin || !server.value) return
  if (server.value.requires_migration) {
    confirmDeleteServer(server.value)
    return
  }
  if (server.value.enabled === false) {
    await handleSetServerEnabled(server.value, true)
    return
  }
  await handleSetServerEnabled(server.value, false)
}

const handleSetServerEnabled = async (srv, enabled) => {
  if (!userStore.isAdmin) return
  try {
    const result = await mcpApi.updateMcpServerStatus(srv.slug, enabled)
    if (result.success) {
      message.success(result.message || `MCP 已${enabled ? '启用' : '停用'}`)
      await fetchServer()
    } else {
      message.error(result.message || '操作失败')
    }
  } catch (err) {
    message.error(err.message || '操作失败')
  }
}

const confirmDeleteServer = (srv) => {
  if (!userStore.isAdmin) return
  Modal.confirm({
    title: '确认删除 MCP',
    content: `确定要删除 MCP "${srv.name}" 吗？此操作不可撤销。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        const result = await mcpApi.deleteMcpServer(srv.slug)
        if (result.success) {
          message.success('MCP 删除成功')
          router.push({ path: '/extensions', query: { tab: 'mcp' } })
        } else {
          message.error(result.message || '删除失败')
        }
      } catch (err) {
        message.error(err.message || '删除失败')
      }
    }
  })
}

watch(detailTab, (tab) => {
  if (tab === 'tools' && server.value) {
    fetchTools()
  }
  if (tab === 'sharing' && server.value && canManageSharing.value && !sharingOptionsLoaded.value) {
    fetchSharingOptions()
  }
})

onMounted(() => {
  fetchServer()
})
</script>

<style lang="less" scoped>
@import '@/assets/css/extensions.less';

.migration-alert {
  margin-bottom: 20px;
}

.info-grid {
  gap: 0;

  .info-item {
    padding: 16px 0;
    border-bottom: 1px solid var(--gray-150);
  }
}

.mcp-sharing-view {
  .sharing-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 16px;

    h3 {
      margin: 0 0 4px;
      color: var(--gray-900);
      font-size: 16px;
      font-weight: 600;
    }

    p {
      margin: 0;
      color: var(--gray-500);
      font-size: 13px;
    }
  }

  .sharing-hint {
    margin-bottom: 16px;
  }
}

.edit-panel {
  background: transparent;
  border: 0;
  border-radius: 0;
  padding: 0;
  box-shadow: none;

  .edit-panel-header {
    display: flex;
    justify-content: space-between;
    gap: 16px;
    align-items: flex-start;
    margin-bottom: 18px;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--gray-100);

    h3 {
      margin: 0 0 4px;
      font-size: 16px;
      font-weight: 600;
      color: var(--gray-900);
    }

    p {
      margin: 0;
      font-size: 13px;
      color: var(--gray-500);
    }
  }

  .inline-edit-form {
    display: flex;
    flex-direction: column;

    :deep(.ant-form-item) {
      margin-bottom: 0;
    }

    :deep(.ant-form-item-label > label) {
      color: var(--gray-700);
      font-size: 13px;
      font-weight: 500;
    }
  }

  .form-section {
    display: flex;
    flex-direction: column;
    gap: 14px;
    padding: 18px 0 0;

    & + .form-section {
      margin-top: 18px;
      border-top: 1px solid var(--gray-100);
    }

    &:first-child {
      padding-top: 0;
    }
  }

  .form-section-title {
    display: flex;
    flex-direction: column;
    gap: 3px;

    span {
      font-size: 14px;
      font-weight: 600;
      color: var(--gray-900);
    }

    small {
      font-size: 12px;
      color: var(--gray-500);
    }
  }

  .form-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 14px;
  }

  .form-grid-three {
    grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr) minmax(120px, 0.45fr);
  }

  .form-item-full {
    width: 100%;
  }

  .form-helper {
    margin-top: 6px;
    font-size: 12px;
    line-height: 1.5;
    color: var(--gray-500);
  }

  .config-textarea {
    font-family: @mono-font;
    font-size: 13px;
    line-height: 1.6;
  }

  .env-editor-shell {
    padding: 0;
  }

  .edit-panel-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 18px;
    padding-top: 16px;
    border-top: 1px solid var(--gray-100);
  }
}

/* 工具列表样式 */
.tools-tab {
  .tools-toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;
  }

  .empty-tools {
    padding: 40px 0;
  }

  .tools-list {
    display: flex;
    flex-direction: column;
    gap: 0;

    .tool-card {
      background: transparent;
      border: 0;
      border-bottom: 1px solid var(--gray-150);
      border-radius: 0;
      padding: 16px 0;

      &.disabled {
        opacity: 0.6;
      }

      .tool-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 8px;

        .tool-info {
          display: flex;
          align-items: center;
          gap: 8px;

          .tool-name {
            font-weight: 600;
            font-size: 14px;
            color: var(--gray-900);
          }

          .info-icon {
            color: var(--gray-400);
            cursor: pointer;
            &:hover {
              color: var(--gray-600);
            }
          }
        }

        .tool-actions {
          display: flex;
          align-items: center;
          gap: 8px;
        }
      }

      .tool-description {
        font-size: 13px;
        color: var(--gray-600);
        line-height: 1.4;
        margin-bottom: 8px;
      }

      :deep(.ant-collapse) {
        background: transparent;
        border: none;

        .ant-collapse-header {
          padding: 8px 0;
          font-size: 13px;
          color: var(--gray-600);
        }
        .ant-collapse-content-box {
          padding: 0;
        }
      }

      .params-list {
        display: flex;
        flex-direction: column;
        gap: 8px;

        .param-item {
          background: var(--gray-50);
          padding: 8px 12px;
          border-radius: 4px;

          .param-header {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 4px;

            .param-name {
              font-weight: 500;
              font-size: 13px;
              color: var(--gray-900);
              font-family: @mono-font;
            }
            .param-required {
              font-size: 11px;
              color: var(--color-error-500);
              background: var(--color-error-50);
              padding: 1px 6px;
              border-radius: 3px;
            }
            .param-type {
              font-size: 11px;
              color: var(--gray-500);
              background: var(--gray-100);
              padding: 1px 6px;
              border-radius: 3px;
              font-family: @mono-font;
            }
          }

          .param-desc {
            font-size: 12px;
            color: var(--gray-600);
            line-height: 1.4;
          }
        }
      }
    }
  }
}

@media (max-width: 768px) {
  .mcp-sharing-view .sharing-header {
    flex-direction: column;
  }

  .edit-panel {
    .form-grid,
    .form-grid-three {
      grid-template-columns: minmax(0, 1fr);
    }
  }

  .tools-toolbar {
    gap: 8px;

    :deep(.ant-input-search) {
      min-width: 0;
      flex: 1;
    }
  }
}
</style>
