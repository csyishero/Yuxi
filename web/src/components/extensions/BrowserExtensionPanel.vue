<template>
  <div class="browser-extension-panel">
    <a-spin :spinning="loading">
      <div class="browser-panel-grid">
        <section class="browser-card browser-status-card">
          <div class="card-heading">
            <div class="heading-icon"><PanelsTopLeft :size="20" /></div>
            <div>
              <h2>Yuxi Browser Assistant</h2>
              <p>把当前 Chrome 或 Edge 安全绑定到你的 Agent Run，仅开放只读能力。</p>
            </div>
          </div>

          <a-alert
            v-if="configLoaded && !config.enabled"
            type="warning"
            show-icon
            message="Browser Gateway 尚未启用"
            description="请先在 API 与 Worker 中配置 Gateway 地址、Yuxi 来源和 ES256 私钥。"
          />

          <template v-else>
            <div class="status-row">
              <span class="status-label">扩展</span>
              <a-tag :color="presence ? 'green' : 'default'">
                {{ presence ? '已检测' : '未检测' }}
              </a-tag>
            </div>
            <div class="status-row">
              <span class="status-label">设备</span>
              <a-tag :color="presence?.pairing_state === 'paired' ? 'blue' : 'default'">
                {{ presence?.pairing_state === 'paired' ? '已配对' : '未配对' }}
              </a-tag>
            </div>
            <div class="status-row">
              <span class="status-label">连接</span>
              <a-tag :color="presence?.connection_state === 'online' ? 'green' : 'orange'">
                {{ presence?.connection_state === 'online' ? '在线' : '离线' }}
              </a-tag>
            </div>
            <div v-if="presence?.binding_fingerprint" class="fingerprint">
              设备指纹：{{ presence.binding_fingerprint }}
            </div>
          </template>
        </section>

        <section class="browser-card browser-actions-card">
          <h3>连接扩展</h3>
          <p class="section-help">
            从 <code>chrome://extensions</code> 复制扩展 ID。开发环境的 ID 可保存在当前浏览器中。
          </p>
          <a-input
            v-model:value="extensionId"
            :disabled="!config.enabled"
            placeholder="输入 Yuxi Browser Assistant Extension ID"
            @change="handleExtensionIdChange"
          />
          <div class="action-row">
            <a-button :disabled="!canOperate" @click="handleDetect">
              <RefreshCw :size="14" />
              检测扩展
            </a-button>
            <a-button type="primary" :disabled="!canOperate" @click="handlePair">
              <Cable :size="14" />
              {{ presence?.pairing_state === 'paired' ? '重新配对' : '开始配对' }}
            </a-button>
          </div>
          <a-alert
            v-if="operationError"
            class="operation-alert"
            type="error"
            show-icon
            :message="operationError"
          />
        </section>

        <section class="browser-card browser-guide-card">
          <h3>使用方式</h3>
          <ol>
            <li>检测扩展并完成设备配对，状态应显示“在线”。</li>
            <li>打开需要读取的 ERP、OA 或 CRM 页面。</li>
            <li>点击扩展图标，选择“授权当前网站”。</li>
            <li>在对话中启用浏览器只读工具，再让 Agent 查看标签页、快照、内容或截图。</li>
          </ol>
          <div class="scope-note">
            <ShieldCheck :size="16" />
            当前仅支持读取，不包含点击、填写、跳转和提交操作。
          </div>
        </section>

        <section class="browser-card browser-config-card">
          <h3>当前配置</h3>
          <dl>
            <div>
              <dt>Yuxi 来源</dt>
              <dd>{{ config.yuxi_origin || '未配置' }}</dd>
            </div>
            <div>
              <dt>Gateway</dt>
              <dd>{{ config.gateway_base_url || '未配置' }}</dd>
            </div>
            <div>
              <dt>扩展版本</dt>
              <dd>{{ presence?.extension_version || '—' }}</dd>
            </div>
          </dl>
        </section>
      </div>
    </a-spin>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { Cable, PanelsTopLeft, RefreshCw, ShieldCheck } from '@lucide/vue'
import { browserApi } from '@/apis/browser_api'
import {
  detectBrowserExtension,
  pairBrowserExtension,
  resolveBrowserExtensionId,
  saveBrowserExtensionId
} from '@/services/browserExtensionBridge'

const loading = ref(false)
const configLoaded = ref(false)
const config = ref({ enabled: false })
const extensionId = ref('')
const presence = ref(null)
const operationError = ref('')

const canOperate = computed(
  () => config.value.enabled && Boolean(extensionId.value.trim()) && !loading.value
)

const publicExtensionError = (error) => {
  const code = error?.message || ''
  if (code === 'EXTENSION_NOT_AVAILABLE')
    return '未找到扩展，请确认 Extension ID 正确且当前 Yuxi 来源已加入扩展白名单。'
  if (code === 'EXTENSION_TIMEOUT') return '扩展响应超时，请重新加载扩展后再试。'
  if (code === 'UNTRUSTED_SENDER' || code === 'UNTRUSTED_ORIGIN')
    return '当前 Yuxi 地址不在扩展的受信任来源中。'
  if (code === 'BROWSER_OFFLINE') return '设备已配对，但 WebSocket 尚未连接到 Browser Gateway。'
  if (code === 'DOMAIN_NOT_ALLOWED') return '当前来源未被 Browser Gateway 允许。'
  return '操作失败，请检查 Browser Gateway 与扩展状态。'
}

const refreshConfig = async () => {
  loading.value = true
  try {
    config.value = await browserApi.getConfig()
    extensionId.value = resolveBrowserExtensionId(config.value)
  } catch {
    config.value = { enabled: false }
    operationError.value = '无法读取 Browser Gateway 配置。'
  } finally {
    configLoaded.value = true
    loading.value = false
  }
}

const handleExtensionIdChange = () => {
  saveBrowserExtensionId(extensionId.value)
  presence.value = null
  operationError.value = ''
}

const handleDetect = async () => {
  loading.value = true
  operationError.value = ''
  try {
    presence.value = await detectBrowserExtension(saveBrowserExtensionId(extensionId.value))
  } catch (error) {
    presence.value = null
    operationError.value = publicExtensionError(error)
  } finally {
    loading.value = false
  }
}

const handlePair = async () => {
  loading.value = true
  operationError.value = ''
  try {
    saveBrowserExtensionId(extensionId.value)
    await pairBrowserExtension(extensionId.value, config.value)
    presence.value = await detectBrowserExtension(extensionId.value)
    message.success('浏览器扩展已配对并连接')
  } catch (error) {
    operationError.value = publicExtensionError(error)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await refreshConfig()
  if (canOperate.value) await handleDetect()
})

defineExpose({ loading, refreshConfig })
</script>

<style scoped lang="less">
.browser-extension-panel {
  min-height: 100%;
  padding: 24px;
  background: var(--color-bg-container, #fff);
}

.browser-panel-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  max-width: 1080px;
  margin: 0 auto;
}

.browser-card {
  min-width: 0;
  padding: 22px;
  border: 1px solid var(--color-border-secondary, #e8e8e8);
  border-radius: 12px;
  background: var(--color-bg-elevated, #fff);

  h2,
  h3,
  p {
    margin-top: 0;
  }

  h2 {
    margin-bottom: 6px;
    font-size: 18px;
  }

  h3 {
    margin-bottom: 8px;
    font-size: 15px;
  }
}

.card-heading {
  display: flex;
  gap: 14px;
  margin-bottom: 18px;

  p {
    margin-bottom: 0;
    color: var(--color-text-secondary, #666);
    line-height: 1.6;
  }
}

.heading-icon {
  display: grid;
  flex: 0 0 42px;
  width: 42px;
  height: 42px;
  place-items: center;
  border-radius: 10px;
  color: #1677ff;
  background: color-mix(in srgb, #1677ff 10%, transparent);
}

.status-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 7px 0;
  border-bottom: 1px solid var(--color-border-secondary, #eee);
}

.status-label,
.section-help,
.fingerprint,
dt {
  color: var(--color-text-secondary, #666);
}

.fingerprint {
  margin-top: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}

.section-help {
  line-height: 1.6;
}

.action-row {
  display: flex;
  gap: 10px;
  margin-top: 14px;

  :deep(.ant-btn) {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
}

.operation-alert {
  margin-top: 14px;
}

.browser-guide-card ol {
  margin: 10px 0 16px;
  padding-left: 20px;
  color: var(--color-text-secondary, #555);
  line-height: 1.9;
}

.scope-note {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 8px;
  color: #1677ff;
  background: color-mix(in srgb, #1677ff 8%, transparent);
}

.browser-config-card dl {
  margin: 0;
}

.browser-config-card dl > div {
  display: grid;
  grid-template-columns: 90px minmax(0, 1fr);
  gap: 12px;
  padding: 9px 0;
  border-bottom: 1px solid var(--color-border-secondary, #eee);
}

.browser-config-card dd {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
}

@media (max-width: 820px) {
  .browser-extension-panel {
    padding: 14px;
  }

  .browser-panel-grid {
    grid-template-columns: 1fr;
  }
}
</style>
