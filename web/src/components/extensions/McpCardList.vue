<template>
  <div class="mcp-cards-page extension-page-root">
    <PageShoulder search-placeholder="搜索 MCP..." v-model:search="searchQuery">
      <template #actions>
        <a-button v-if="userStore.isAdmin" type="primary" @click="handleMcpAdd" class="lucide-icon-btn">
          <Plus :size="14" />
          <span>添加 MCP</span>
        </a-button>
        <a-tooltip title="刷新 MCP" placement="bottom">
          <a-button class="lucide-icon-btn" :disabled="loading" @click="fetchServers">
            <RefreshCw :size="14" />
          </a-button>
        </a-tooltip>
      </template>
    </PageShoulder>

    <div
      v-if="filteredEnabledServers.length === 0 && filteredDisabledServers.length === 0"
      class="extension-card-grid-empty-state"
    >
      <a-empty
        :image="false"
        :description="searchQuery ? '无匹配 MCP' : userStore.isAdmin ? '暂无 MCP，点击上方按钮添加' : '暂无可见的 MCP'"
      />
    </div>

    <template v-else>
      <div v-if="filteredEnabledServers.length" class="extension-section-header">已启用</div>
      <ExtensionCardGrid v-if="filteredEnabledServers.length" :min-width="300">
        <InfoCard
          v-for="server in filteredEnabledServers"
          :key="server.slug"
          variant="mini"
          :title="formatExtensionCardTitle(server.name)"
          :description="server.description || '暂无描述'"
          @click="navigateToDetail(server)"
        >
          <template #icon>
            <span class="info-card-emoji-icon">{{ server.icon || '🔌' }}</span>
          </template>
          <template v-if="userStore.isAdmin" #action>
            <button
              type="button"
              class="mcp-card-action"
              :disabled="isActionLoading(server)"
              aria-label="停用 MCP"
              @click.stop="handleSetServerEnabled(server, false)"
            >
              <PowerOff :size="15" class="action-icon" />
            </button>
          </template>
          <template v-else #action>
            <span class="mcp-card-permission">{{ permissionLabel(server) }}</span>
          </template>
        </InfoCard>
      </ExtensionCardGrid>

      <div v-if="filteredDisabledServers.length" class="extension-section-header">
        已停用
      </div>
      <ExtensionCardGrid v-if="filteredDisabledServers.length" :min-width="300">
        <InfoCard
          v-for="server in filteredDisabledServers"
          :key="server.slug"
          variant="mini"
          :title="formatExtensionCardTitle(server.name)"
          :description="
            server.requires_migration ? '需要迁移为远程 MCP' : server.description || '暂无描述'
          "
          @click="navigateToDetail(server)"
        >
          <template #icon>
            <span class="info-card-emoji-icon">{{ server.icon || '🔌' }}</span>
          </template>
          <template v-if="userStore.isAdmin" #action>
            <button
              type="button"
              :class="['mcp-card-action', { 'mcp-card-action-danger': server.requires_migration }]"
              :disabled="isActionLoading(server)"
              :aria-label="server.requires_migration ? '删除 MCP' : '启用 MCP'"
              @click.stop="
                server.requires_migration
                  ? confirmDeleteServer(server)
                  : handleSetServerEnabled(server, true)
              "
            >
              <Trash2 v-if="server.requires_migration" :size="15" class="action-icon" />
              <Plus v-else :size="15" class="action-icon" />
            </button>
          </template>
          <template v-else #action>
            <span class="mcp-card-permission">{{ permissionLabel(server) }}</span>
          </template>
        </InfoCard>
      </ExtensionCardGrid>
    </template>

    <McpFormModal
      v-if="userStore.isAdmin"
      v-model:open="formModalVisible"
      :edit-mode="false"
      @submitted="handleFormSubmitted"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { Plus, PowerOff, RefreshCw, Trash2 } from '@lucide/vue'
import { mcpApi } from '@/apis/mcp_api'
import { useUserStore } from '@/stores/user'
import ExtensionCardGrid from './ExtensionCardGrid.vue'
import InfoCard from '@/components/shared/InfoCard.vue'
import PageShoulder from '@/components/shared/PageShoulder.vue'
import McpFormModal from './McpFormModal.vue'
import { formatExtensionCardTitle } from '@/utils/extensionDisplayName'

const router = useRouter()
const userStore = useUserStore()

const loading = ref(false)
const servers = ref([])
const searchQuery = ref('')
const formModalVisible = ref(false)
const actionLoadingSlug = ref('')

const filteredServers = computed(() => {
  const sorted = [...servers.value].sort((a, b) =>
    String(a.name || '').localeCompare(String(b.name || ''), 'zh-Hans-CN', {
      sensitivity: 'base',
      numeric: true
    })
  )
  if (!searchQuery.value) return sorted
  const q = searchQuery.value.toLowerCase()
  return sorted.filter(
    (s) => s.name.toLowerCase().includes(q) || (s.description || '').toLowerCase().includes(q)
  )
})

const filteredEnabledServers = computed(() =>
  filteredServers.value.filter((item) => !!item.enabled)
)
const filteredDisabledServers = computed(() =>
  filteredServers.value.filter((item) => !item.enabled)
)

const navigateToDetail = (server) => {
  router.push({ path: `/extensions/mcp/${encodeURIComponent(server.slug)}` })
}

const isActionLoading = (server) => actionLoadingSlug.value === server?.slug
const permissionLabel = (server) => {
  if (server.permission === 'manage') return '可授权'
  if (server.permission === 'edit') return '可使用'
  return '仅查看'
}

const handleMcpAdd = () => {
  if (!userStore.isAdmin) return
  formModalVisible.value = true
}

const handleFormSubmitted = async () => {
  formModalVisible.value = false
  await fetchServers()
}

const handleSetServerEnabled = async (server, enabled) => {
  if (!userStore.isAdmin) return
  try {
    actionLoadingSlug.value = server.slug
    const result = await mcpApi.updateMcpServerStatus(server.slug, enabled)
    if (result.success) {
      message.success(result.message || `MCP 已${enabled ? '启用' : '停用'}`)
      await fetchServers()
    } else {
      message.error(result.message || '操作失败')
    }
  } catch (err) {
    message.error(err.message || '操作失败')
  } finally {
    actionLoadingSlug.value = ''
  }
}

const confirmDeleteServer = (server) => {
  if (!userStore.isAdmin) return
  Modal.confirm({
    title: '确认删除 MCP',
    content: `确定要删除 MCP "${server.name}" 吗？此操作不可撤销。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        actionLoadingSlug.value = server.slug
        const result = await mcpApi.deleteMcpServer(server.slug)
        if (result.success) {
          message.success('MCP 删除成功')
          await fetchServers()
        } else {
          message.error(result.message || '删除失败')
        }
      } catch (err) {
        message.error(err.message || '删除失败')
      } finally {
        actionLoadingSlug.value = ''
      }
    }
  })
}

const fetchServers = async () => {
  try {
    loading.value = true
    const result = await mcpApi.getMcpServers()
    if (result.success) {
      servers.value = result.data || []
    }
  } catch (err) {
    message.error(err.message || '获取 MCP 列表失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  fetchServers()
})

defineExpose({ fetchServers, loading })
</script>

<style lang="less" scoped>
@import '@/assets/css/extensions.less';

.info-card-emoji-icon {
  font-size: 18px;
  line-height: 1;
}

.mcp-card-permission {
  display: inline-block;
  padding: 4px 8px;
  border-radius: 6px;
  background: var(--main-50);
  color: var(--main-color);
  font-size: 12px;
  white-space: nowrap;
}

.mcp-card-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
  color: var(--main-color);
  cursor: pointer;
  transition:
    border-color 0.18s ease,
    background-color 0.18s ease,
    color 0.18s ease;

  &:hover,
  &:focus {
    outline: none;
    border-color: var(--main-200);
    background: var(--main-50);
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.45;
  }

  &.mcp-card-action-danger {
    color: var(--color-success-700);

    .action-icon-trash {
      display: none;
    }

    &:hover,
    &:focus {
      border-color: var(--color-error-100);
      background: var(--color-error-50);
      color: var(--color-error-700);

      .action-icon-check {
        display: none;
      }

      .action-icon-trash {
        display: block;
      }
    }
  }
}

.action-icon {
  flex-shrink: 0;
}

</style>
