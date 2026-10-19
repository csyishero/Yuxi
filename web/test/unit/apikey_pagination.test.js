import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import { parse } from 'vue/compiler-sfc'
import { reactive, ref } from 'vue'

test('API Key 列表将偏移和页大小编码到请求 URL', async () => {
  const source = readFileSync(new URL('../../src/apis/apikey_api.js', import.meta.url), 'utf8')
  const urls = []
  const api = runInNewContext(`${source.replace(/^import .*$/gm, '').replace('export const', 'const')}\n;apikeyApi`, {
    buildQuery: (params) => new URLSearchParams(params).toString(),
    apiGet: async (url) => {
      urls.push(url)
      return { api_keys: [], total: 0 }
    }
  })

  await api.list(100, 20)
  assert.equal(urls[0], '/api/user/apikey/?skip=100&limit=20')
})

test('API Key 管理可翻到下一页并保留服务端总数', async () => {
  const source = readFileSync(
    new URL('../../src/components/ApiKeyManagementComponent.vue', import.meta.url),
    'utf8'
  )
  const { descriptor } = parse(source)
  const script = descriptor.scriptSetup.content.replace(/^import .* from .*$/gm, '')
  const calls = []
  const component = runInNewContext(
    `${script}\n;({ apiKeys, total, currentPage, loadApiKeys, handlePageChange })`,
    {
      ref,
      reactive,
      onMounted: () => {},
      apikeyApi: {
        list: async (skip, limit) => {
          calls.push([skip, limit])
          return { api_keys: [{ id: skip + 1 }], total: 121 }
        }
      },
      message: { error: () => {} }
    }
  )

  await component.loadApiKeys()
  await component.handlePageChange(2)

  assert.deepEqual(calls, [
    [0, 20],
    [20, 20]
  ])
  assert.equal(component.currentPage.value, 2)
  assert.equal(component.apiKeys.value[0].id, 21)
  assert.equal(component.total.value, 121)
})

test('删除后末页空了会回到仍有记录的上一页', async () => {
  const source = readFileSync(
    new URL('../../src/components/ApiKeyManagementComponent.vue', import.meta.url),
    'utf8'
  )
  const { descriptor } = parse(source)
  const script = descriptor.scriptSetup.content.replace(/^import .* from .*$/gm, '')
  const calls = []
  const component = runInNewContext(
    `${script}\n;({ apiKeys, currentPage, loadApiKeys })`,
    {
      ref,
      reactive,
      onMounted: () => {},
      apikeyApi: {
        list: async (skip) => {
          calls.push(skip)
          return { api_keys: skip === 0 ? [{ id: 1 }] : [], total: 20 }
        }
      },
      message: { error: () => {} }
    }
  )

  component.currentPage.value = 2
  await component.loadApiKeys()

  assert.deepEqual(calls, [20, 0])
  assert.equal(component.currentPage.value, 1)
  assert.equal(component.apiKeys.value[0].id, 1)
})

test('较早的 API Key 页响应不会覆盖用户后来选择的页', async () => {
  const source = readFileSync(
    new URL('../../src/components/ApiKeyManagementComponent.vue', import.meta.url),
    'utf8'
  )
  const { descriptor } = parse(source)
  const script = descriptor.scriptSetup.content.replace(/^import .* from .*$/gm, '')
  const pending = new Map()
  const component = runInNewContext(
    `${script}\n;({ apiKeys, currentPage, loadApiKeys, handlePageChange })`,
    {
      ref,
      reactive,
      onMounted: () => {},
      apikeyApi: {
        list: (skip) => new Promise((resolve) => pending.set(skip, resolve))
      },
      message: { error: () => {} }
    }
  )

  const first = component.loadApiKeys()
  const second = component.handlePageChange(2)
  pending.get(20)({ api_keys: [{ id: 21 }], total: 40 })
  await second
  pending.get(0)({ api_keys: [{ id: 1 }], total: 40 })
  await first

  assert.equal(component.currentPage.value, 2)
  assert.equal(component.apiKeys.value[0].id, 21)
})
