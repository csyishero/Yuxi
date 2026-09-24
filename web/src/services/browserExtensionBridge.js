import { browserApi } from '@/apis/browser_api'

const EXTENSION_ID_STORAGE_KEY = 'yuxi_browser_extension_id'
const pendingRunBindings = new Map()
const boundRuns = new Map()

function safeStorageGet(key) {
  try {
    return globalThis.localStorage?.getItem(key) || ''
  } catch {
    return ''
  }
}

function safeStorageSet(key, value) {
  try {
    if (value) globalThis.localStorage?.setItem(key, value)
    else globalThis.localStorage?.removeItem(key)
  } catch {
    // 浏览器禁用持久化时仅在当前页面使用配置。
  }
}

/** 读取用户本地覆盖或后端默认的 Extension ID。 */
export function resolveBrowserExtensionId(config = {}) {
  return safeStorageGet(EXTENSION_ID_STORAGE_KEY).trim() || String(config.extension_id || '').trim()
}

/** 保存 unpacked Extension 的本地 ID；该值不是凭据。 */
export function saveBrowserExtensionId(extensionId) {
  const value = String(extensionId || '').trim()
  safeStorageSet(EXTENSION_ID_STORAGE_KEY, value)
  return value
}

function chromeRuntime() {
  return globalThis.chrome?.runtime || null
}

/** 向 externally_connectable Extension 发送消息，并对无响应设置上限。 */
export function sendBrowserExtensionMessage(extensionId, payload, timeoutMs = 12_000) {
  const runtime = chromeRuntime()
  if (!runtime?.sendMessage || !extensionId) {
    return Promise.reject(new Error('EXTENSION_NOT_AVAILABLE'))
  }
  return new Promise((resolve, reject) => {
    let settled = false
    const timer = setTimeout(() => {
      if (settled) return
      settled = true
      reject(new Error('EXTENSION_TIMEOUT'))
    }, timeoutMs)
    runtime.sendMessage(extensionId, payload, (response) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      const runtimeError = runtime.lastError
      if (runtimeError) {
        reject(new Error('EXTENSION_NOT_AVAILABLE'))
        return
      }
      if (!response?.ok) {
        reject(new Error(String(response?.error || 'EXTENSION_REQUEST_FAILED')))
        return
      }
      resolve(response)
    })
  })
}

/** 检测扩展的配对与 WSS 连接状态。 */
export async function detectBrowserExtension(extensionId) {
  return sendBrowserExtensionMessage(extensionId, {
    type: 'YUXI_EXTENSION_PING',
    protocol: 1,
    nonce: globalThis.crypto.randomUUID()
  })
}

/** 通过后端创建一次性 token，并在网页内存中交给 Extension 完成配对。 */
export async function pairBrowserExtension(extensionId, config) {
  const pairing = await browserApi.createPairing()
  return sendBrowserExtensionMessage(
    extensionId,
    {
      type: 'YUXI_PAIR_REQUEST',
      protocol: config?.protocol || 1,
      gateway_base_url: pairing.gateway_base_url || config?.gateway_base_url,
      pairing_token: pairing.pairing_token,
      device_name: 'Yuxi Browser'
    },
    20_000
  )
}

async function performRunBinding(runId) {
  const config = await browserApi.getConfig()
  if (!config?.enabled) return { ok: false, reason: 'disabled' }
  const extensionId = resolveBrowserExtensionId(config)
  if (!extensionId) return { ok: false, reason: 'extension_id_missing' }
  const response = await sendBrowserExtensionMessage(
    extensionId,
    {
      type: 'YUXI_CURRENT_DEVICE_ASSERTION_REQUEST',
      protocol: config.protocol || 1,
      run_id: runId,
      web_session_nonce: globalThis.crypto.randomUUID(),
      yuxi_origin: globalThis.location?.origin || config.yuxi_origin
    },
    15_000
  )
  const stored = await browserApi.bindRunAssertion(runId, response.assertion)
  const ttlSources =
    stored?.binding_status === 'ready'
      ? [stored.expires_in]
      : [response.expires_in, stored?.expires_in]
  const ttlCandidates = ttlSources
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value) && value > 0)
  const expiresIn = ttlCandidates.length ? Math.min(...ttlCandidates) : 30
  boundRuns.set(runId, Date.now() + expiresIn * 1000)
  return { ok: true, expires_in: expiresIn }
}

/** 为真实 Agent Run 准备一次性设备断言；失败不会中断普通 Agent 流程。 */
export function bindCurrentBrowserToRun(runId) {
  const normalizedRunId = String(runId || '').trim()
  if (!normalizedRunId) return Promise.resolve({ ok: false, reason: 'run_id_missing' })
  const expiresAt = boundRuns.get(normalizedRunId) || 0
  if (expiresAt > Date.now()) {
    return Promise.resolve({ ok: true, cached: true })
  }
  boundRuns.delete(normalizedRunId)
  if (pendingRunBindings.has(normalizedRunId)) return pendingRunBindings.get(normalizedRunId)
  const task = performRunBinding(normalizedRunId)
    .catch((error) => ({ ok: false, reason: error?.message || 'extension_unavailable' }))
    .finally(() => pendingRunBindings.delete(normalizedRunId))
  pendingRunBindings.set(normalizedRunId, task)
  return task
}

/** 仅供单元测试或注销清理当前页面的 Run 绑定缓存。 */
export function resetBrowserRunBindings() {
  pendingRunBindings.clear()
  boundRuns.clear()
}
