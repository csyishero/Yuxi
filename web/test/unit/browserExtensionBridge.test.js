import assert from 'node:assert/strict'
import path from 'node:path'
import { after, before, test } from 'node:test'
import { fileURLToPath } from 'node:url'

import { createServer } from 'vite'

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
let server
let bridge
let browserApi
let storage

before(async () => {
  storage = new Map()
  globalThis.localStorage = {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key)
  }
  Object.defineProperty(globalThis, 'location', {
    configurable: true,
    value: { origin: 'http://localhost:5173' }
  })
  server = await createServer({ root: webRoot, server: { middlewareMode: true } })
  bridge = await server.ssrLoadModule('/src/services/browserExtensionBridge.js')
  ;({ browserApi } = await server.ssrLoadModule('/src/apis/browser_api.js'))
})

after(async () => {
  await server?.close()
  delete globalThis.chrome
  delete globalThis.localStorage
  delete globalThis.location
})

test('检测与配对使用本地 Extension ID 且 pairing token 只发给扩展', async () => {
  const messages = []
  globalThis.chrome = {
    runtime: {
      lastError: null,
      sendMessage(extensionId, payload, callback) {
        messages.push({ extensionId, payload })
        callback(
          payload.type === 'YUXI_EXTENSION_PING'
            ? { ok: true, pairing_state: 'paired', connection_state: 'online' }
            : { ok: true, binding_fingerprint: 'fingerprint' }
        )
      }
    }
  }
  browserApi.createPairing = async () => ({
    pairing_token: 'secret-pairing-token',
    gateway_base_url: 'http://localhost:8088'
  })

  bridge.saveBrowserExtensionId('local-extension')
  const detected = await bridge.detectBrowserExtension(
    bridge.resolveBrowserExtensionId({ extension_id: 'default-extension' })
  )
  await bridge.pairBrowserExtension('local-extension', { protocol: 1 })

  assert.equal(detected.connection_state, 'online')
  assert.equal(messages[0].extensionId, 'local-extension')
  assert.equal(messages[1].payload.type, 'YUXI_PAIR_REQUEST')
  assert.equal(messages[1].payload.pairing_token, 'secret-pairing-token')
})

test('真实 Run 的 assertion 只提交一次并使用当前页面 origin', async () => {
  bridge.resetBrowserRunBindings()
  const messages = []
  const assertions = []
  globalThis.chrome = {
    runtime: {
      lastError: null,
      sendMessage(_extensionId, payload, callback) {
        messages.push(payload)
        callback({ ok: true, assertion: 'opaque-current-device-assertion', expires_in: 60 })
      }
    }
  }
  browserApi.getConfig = async () => ({
    enabled: true,
    protocol: 1,
    extension_id: 'extension-id',
    yuxi_origin: 'http://localhost:5173'
  })
  browserApi.bindRunAssertion = async (runId, assertion) => {
    assertions.push([runId, assertion])
    return { expires_in: 90 }
  }

  const [first, second] = await Promise.all([
    bridge.bindCurrentBrowserToRun('run-real-1'),
    bridge.bindCurrentBrowserToRun('run-real-1')
  ])

  assert.equal(first.ok, true)
  assert.equal(second.ok, true)
  assert.equal(messages.length, 1)
  assert.equal(messages[0].run_id, 'run-real-1')
  assert.equal(messages[0].yuxi_origin, 'http://localhost:5173')
  assert.deepEqual(assertions, [['run-real-1', 'opaque-current-device-assertion']])
})

test('Run assertion 本地去重会随服务端 TTL 到期', async () => {
  bridge.resetBrowserRunBindings()
  const originalNow = Date.now
  let now = 1_000_000
  let messageCount = 0
  Date.now = () => now
  globalThis.chrome = {
    runtime: {
      lastError: null,
      sendMessage(_extensionId, _payload, callback) {
        messageCount += 1
        callback({ ok: true, assertion: `assertion-${messageCount}-value`, expires_in: 60 })
      }
    }
  }
  browserApi.getConfig = async () => ({ enabled: true, protocol: 1, extension_id: 'extension-id' })
  browserApi.bindRunAssertion = async () => ({ expires_in: 90 })

  try {
    await bridge.bindCurrentBrowserToRun('run-expiring')
    await bridge.bindCurrentBrowserToRun('run-expiring')
    now += 61_000
    await bridge.bindCurrentBrowserToRun('run-expiring')
  } finally {
    Date.now = originalNow
  }

  assert.equal(messageCount, 2)
})

test('Gateway 已立即绑定时使用 Run 绑定 TTL 而不是 assertion TTL', async () => {
  bridge.resetBrowserRunBindings()
  const originalNow = Date.now
  let now = 2_000_000
  let messageCount = 0
  Date.now = () => now
  globalThis.chrome = {
    runtime: {
      lastError: null,
      sendMessage(_extensionId, _payload, callback) {
        messageCount += 1
        callback({ ok: true, assertion: 'ready-binding-assertion', expires_in: 60 })
      }
    }
  }
  browserApi.getConfig = async () => ({ enabled: true, protocol: 1, extension_id: 'extension-id' })
  browserApi.bindRunAssertion = async () => ({ binding_status: 'ready', expires_in: 1800 })

  try {
    await bridge.bindCurrentBrowserToRun('run-ready')
    now += 61_000
    await bridge.bindCurrentBrowserToRun('run-ready')
  } finally {
    Date.now = originalNow
  }

  assert.equal(messageCount, 1)
})
