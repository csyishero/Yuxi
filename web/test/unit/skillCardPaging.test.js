import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { setImmediate } from 'node:timers/promises'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import { ref } from 'vue'

const source = readFileSync(
  new URL('../../src/components/extensions/SkillCardList.vue', import.meta.url),
  'utf8'
)
const filterCode = source.slice(source.indexOf('const applyCatalogFilters ='), source.indexOf('const loadDepartments ='))
const pageCode = source.slice(source.indexOf('const fetchSkills ='), source.indexOf('const beforeSkillUpload ='))
const searchWatchStart = source.indexOf('watch(searchQuery, () => {')
const searchWatchCode = source.slice(searchWatchStart, source.indexOf('\n})', searchWatchStart) + 3)

function createCatalog(listSkillCards, { isAdmin = false } = {}) {
  const state = {
    loading: ref(false),
    skills: ref([]),
    sharedPage: ref(1),
    sharedTotal: ref(0),
    selectedCardSlugs: ref([]),
    searchQuery: ref(''),
    catalogDepartmentId: ref(undefined),
    ownerSearch: ref(''),
    appliedOwnerSearch: ref(''),
    appliedDepartmentId: ref(undefined),
    catalogCards: ref([]),
    catalogPage: ref(1),
    catalogHasMore: ref(false)
  }
  const calls = []
  const catalogCalls = []
  let onSearch
  let scheduledSearch
  const actions = runInNewContext(
    `let sharedRequestSeq = 0; let catalogRequestSeq = 0; let searchTimer = null; const sharedPageSize = 30; ${pageCode}; ${filterCode}; ${searchWatchCode}; ({ fetchSkills, applyCatalogFilters })`,
    {
      ...state,
      props: { toolbarOnly: false },
      watch: (_source, callback) => { onSearch = callback },
      window: { clearTimeout: () => {}, setTimeout: (callback) => { scheduledSearch = callback; return 1 } },
      userStore: { isAdmin },
      skillApi: { listSkillCards: async (params) => { calls.push(params); return listSkillCards(params) } },
      fetchCatalog: async (options) => { catalogCalls.push(options) },
      message: { error: (value) => { throw new Error(value) } }
    }
  )
  return { ...state, ...actions, calls, catalogCalls, triggerSearch: () => onSearch(), flushSearch: () => scheduledSearch() }
}

test('共享技能刷新保留独立的个人目录页，筛选变化才重置', async () => {
  const catalog = createCatalog(async () => ({ data: [], total: 0, has_more: false }), { isAdmin: true })
  catalog.catalogPage.value = 3

  await catalog.fetchSkills()
  assert.deepEqual(Array.from(catalog.catalogCalls, ({ reset }) => reset), [false])
  assert.equal(catalog.catalogPage.value, 3)

  catalog.catalogDepartmentId.value = 2
  catalog.applyCatalogFilters()
  await setImmediate()
  assert.deepEqual(Array.from(catalog.catalogCalls, ({ reset }) => reset), [false, true])
  assert.equal(catalog.catalogPage.value, 1)
})

test('治理目录只请求共享来源，翻页按页码偏移替换卡片且不重复', async () => {
  const catalog = createCatalog(async ({ offset }) => offset === 0
    ? { data: [{ id: 1, slug: 'a' }, { id: 2, slug: 'b' }], total: 61, has_more: true }
    : { data: [{ id: 3, slug: 'c' }], total: 61, has_more: true })

  await catalog.fetchSkills()
  catalog.selectedCardSlugs.value = ['a']
  await catalog.fetchSkills({ page: 2 })
  assert.ok(catalog.calls.every(({ excludeRetiredBuiltin }) => excludeRetiredBuiltin === true))
  assert.deepEqual(catalog.calls.map(({ source, limit, offset }) => ({ source, limit, offset })), [
    { source: 'shared', limit: 30, offset: 0 },
    { source: 'shared', limit: 30, offset: 30 }
  ])
  assert.deepEqual(Array.from(catalog.skills.value, ({ slug }) => slug), ['c'])
  assert.equal(catalog.sharedPage.value, 2)
  assert.equal(catalog.sharedTotal.value, 61)
  assert.deepEqual(Array.from(catalog.selectedCardSlugs.value), [])
})

test('切换部门和人员筛选后共享技能回到第一页', async () => {
  const catalog = createCatalog(async () => ({ data: [{ id: 1, slug: 'a' }], total: 61, has_more: true }), { isAdmin: true })
  await catalog.fetchSkills({ page: 2 })
  catalog.catalogDepartmentId.value = 3
  catalog.ownerSearch.value = 'alice'
  catalog.applyCatalogFilters()
  await setImmediate()

  assert.equal(catalog.sharedPage.value, 1)
  assert.deepEqual(Array.from(catalog.calls, ({ offset, audienceSearch, audienceDepartmentId }) => ({
    offset, audienceSearch, audienceDepartmentId
  })), [
    { offset: 30, audienceSearch: '', audienceDepartmentId: undefined },
    { offset: 0, audienceSearch: 'alice', audienceDepartmentId: 3 }
  ])
})

test('新筛选等待返回时不继续展示旧页卡片', async () => {
  let finishFiltered
  const catalog = createCatalog(async ({ audienceSearch }) => audienceSearch
    ? new Promise((resolve) => { finishFiltered = resolve })
    : { data: [{ id: 1, slug: 'old' }], total: 40, has_more: true }, { isAdmin: true })

  await catalog.fetchSkills({ page: 2 })
  catalog.ownerSearch.value = 'alice'
  catalog.applyCatalogFilters()

  assert.deepEqual(Array.from(catalog.skills.value), [])
  assert.equal(catalog.sharedPage.value, 1)
  finishFiltered({ data: [{ id: 2, slug: 'new' }], total: 1, has_more: false })
  await setImmediate()
  assert.deepEqual(Array.from(catalog.skills.value, ({ slug }) => slug), ['new'])
})

test('末页记录删除后回退到有内容的最后一页', async () => {
  const catalog = createCatalog(async ({ offset }) => offset === 60
    ? { data: [], total: 31, has_more: false }
    : { data: [{ id: 30, slug: 'last' }], total: 31, has_more: false })

  await catalog.fetchSkills({ page: 3 })

  assert.deepEqual(Array.from(catalog.calls, ({ offset }) => offset), [60, 30])
  assert.equal(catalog.sharedPage.value, 2)
  assert.deepEqual(Array.from(catalog.skills.value, ({ slug }) => slug), ['last'])
})

test('新的搜索替换目录，旧请求不能覆盖搜索结果', async () => {
  let finishOld
  const catalog = createCatalog(async ({ search }) => search
    ? { data: [{ id: 9, slug: 'new' }], total: 1, has_more: false }
    : new Promise((resolve) => { finishOld = resolve }))

  const oldRequest = catalog.fetchSkills({ page: 2 })
  catalog.searchQuery.value = 'new'
  await catalog.fetchSkills({ page: 1 })
  finishOld({ data: [{ id: 1, slug: 'old' }], total: 40, has_more: true })
  await oldRequest

  assert.deepEqual(Array.from(catalog.skills.value, ({ slug }) => slug), ['new'])
  assert.equal(catalog.sharedPage.value, 1)
  assert.equal(catalog.sharedTotal.value, 1)
  assert.equal(catalog.loading.value, false)
})

test('搜索等待期也会阻止旧共享请求重新显示旧筛选结果', async () => {
  let finishOld
  const catalog = createCatalog(async ({ search }) => search
    ? { data: [{ id: 9, slug: 'new' }], total: 1, has_more: false }
    : new Promise((resolve) => { finishOld = resolve }))

  const oldRequest = catalog.fetchSkills({ page: 2 })
  catalog.searchQuery.value = 'new'
  catalog.triggerSearch()
  finishOld({ data: [{ id: 1, slug: 'old' }], total: 40, has_more: true })
  await oldRequest

  assert.deepEqual(Array.from(catalog.skills.value), [])
  assert.equal(catalog.sharedTotal.value, 0)
  catalog.flushSearch()
  await setImmediate()
  assert.deepEqual(Array.from(catalog.skills.value, ({ slug }) => slug), ['new'])
})
