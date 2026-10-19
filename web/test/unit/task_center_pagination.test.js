import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import { parse } from 'vue/compiler-sfc'
import { computed, h, nextTick, ref, watch } from 'vue'

test('任务中心切换状态后从第一页查询，并能翻看后续页', async () => {
  const source = readFileSync(new URL('../../src/components/TaskCenterDrawer.vue', import.meta.url), 'utf8')
  const { descriptor } = parse(source)
  const script = descriptor.scriptSetup.content.replace(/^import .* from .*$/gm, '')
  const requests = []
  const store = {
    isDrawerOpen: ref(false),
    sortedTasks: ref([]),
    loading: ref(false),
    lastError: ref(null),
    activeCount: ref(0),
    totalCount: ref(125),
    filteredTotal: ref(24),
    successCount: ref(24),
    failedCount: ref(0),
    loadTasks: async (params) => requests.push({ ...params })
  }
  const component = runInNewContext(
    `${script}\n;({ statusFilter, currentPage, handlePageChange })`,
    {
      computed,
      h,
      watch,
      ref,
      useTaskerStore: () => store,
      storeToRefs: (value) => value
    }
  )

  store.isDrawerOpen.value = true
  await nextTick()
  requests.length = 0
  component.statusFilter.value = 'success'
  await nextTick()
  await component.handlePageChange(2)

  assert.deepEqual(requests, [
    { status_group: 'success', offset: 0, limit: 20 },
    { status_group: 'success', offset: 20, limit: 20 }
  ])
  assert.equal(component.currentPage.value, 2)
})

test('任务状态变化导致末页消失时自动返回有效页', async () => {
  const source = readFileSync(new URL('../../src/components/TaskCenterDrawer.vue', import.meta.url), 'utf8')
  const { descriptor } = parse(source)
  const script = descriptor.scriptSetup.content.replace(/^import .* from .*$/gm, '')
  const requests = []
  const store = {
    isDrawerOpen: ref(true),
    sortedTasks: ref([]),
    loading: ref(false),
    lastError: ref(null),
    activeCount: ref(21),
    totalCount: ref(21),
    filteredTotal: ref(21),
    successCount: ref(0),
    failedCount: ref(0),
    loadTasks: async (params) => requests.push({ ...params })
  }
  const component = runInNewContext(
    `${script}\n;({ currentPage, handlePageChange })`,
    { computed, h, watch, ref, useTaskerStore: () => store, storeToRefs: (value) => value }
  )

  await component.handlePageChange(2)
  requests.length = 0
  store.filteredTotal.value = 20
  await nextTick()
  assert.equal(component.currentPage.value, 1)
  assert.deepEqual(requests, [{ status_group: 'all', offset: 0, limit: 20 }])
})
