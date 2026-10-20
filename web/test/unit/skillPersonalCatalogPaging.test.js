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
const code = source.slice(source.indexOf('const fetchCatalog ='), source.indexOf('const loadDepartments ='))

function createCatalog(listPersonalSkillCatalog, { governanceOnly = false } = {}) {
  const state = {
    skills: ref([]),
    sharedPage: ref(1),
    sharedTotal: ref(0),
    catalogCards: ref([]),
    catalogDepartmentId: ref(3),
    ownerSearch: ref(''),
    appliedDepartmentId: ref(3),
    appliedOwnerSearch: ref('alice'),
    catalogPage: ref(1),
    catalogHasMore: ref(false),
    selectedCardSlugs: ref([]),
    catalogLoading: ref(false),
    searchQuery: ref('报告')
  }
  const calls = []
  const actions = runInNewContext(
    `let catalogRequestSeq = 0; const catalogPageSize = 30; ${code}; const fetchSkills = () => fetchCatalog({ reset: true }); ({ fetchCatalog, applyCatalogFilters })`,
    {
      ...state,
      userStore: { isAdmin: true },
      props: { governanceOnly },
      skillApi: {
        listPersonalSkillCatalog: async (params) => {
          calls.push(params)
          return listPersonalSkillCatalog(params)
        }
      },
      message: { error: (value) => { throw new Error(value) } }
    }
  )
  return { ...state, ...actions, calls }
}

test('治理个人目录按固定偏移翻页，当前页替换前页且不同所有者同名技能保留', async () => {
  const catalog = createCatalog(async ({ offset }) => offset === 0
    ? { data: { items: [
      { owner_uid: 'alice', slug: 'same' },
      { owner_uid: 'bob', slug: 'same' },
      ...Array.from({ length: 28 }, (_, index) => ({ owner_uid: 'alice', slug: `filler-${index}` }))
    ], next_offset: 30 } }
    : { data: { items: [
      { owner_uid: 'bob', slug: 'next' }
    ], next_offset: null } }, { governanceOnly: true })

  await catalog.fetchCatalog({ reset: true })
  assert.deepEqual(Array.from(catalog.catalogCards.value.slice(0, 2), ({ owner_uid, slug }) => `${owner_uid}:${slug}`), [
    'alice:same', 'bob:same'
  ])
  assert.equal(catalog.catalogCards.value.length, 30)
  assert.equal(catalog.catalogHasMore.value, true)
  await catalog.fetchCatalog({ page: 2 })

  assert.deepEqual(catalog.calls.map(({ departmentId, ownerSearch, offset, limit, cardPage, search, excludeOwn }) => ({
    departmentId, ownerSearch, offset, limit, cardPage, search, excludeOwn
  })), [
    { departmentId: 3, ownerSearch: 'alice', offset: 0, limit: 30, cardPage: true, search: '报告', excludeOwn: true },
    { departmentId: 3, ownerSearch: 'alice', offset: 30, limit: 30, cardPage: true, search: '报告', excludeOwn: true }
  ])
  assert.deepEqual(
    Array.from(catalog.catalogCards.value, ({ owner_uid, slug }) => `${owner_uid}:${slug}`),
    ['bob:next']
  )
  assert.equal(catalog.catalogPage.value, 2)
  assert.equal(catalog.catalogHasMore.value, false)
})

test('部门和人员筛选变化后个人目录回到第一页', async () => {
  const catalog = createCatalog(async () => ({ data: { items: [{ owner_uid: 'bob', slug: 'next' }], next_offset: null } }))
  await catalog.fetchCatalog({ page: 2 })
  catalog.catalogDepartmentId.value = 5
  catalog.ownerSearch.value = 'bob'
  catalog.applyCatalogFilters()
  await setImmediate()

  assert.equal(catalog.catalogPage.value, 1)
  assert.deepEqual(Array.from(catalog.calls, ({ departmentId, ownerSearch, offset }) => ({ departmentId, ownerSearch, offset })), [
    { departmentId: 3, ownerSearch: 'alice', offset: 30 },
    { departmentId: 5, ownerSearch: 'bob', offset: 0 }
  ])
})

test('当前尾页删除后自动回到上一页', async () => {
  const catalog = createCatalog(async ({ offset }) => offset === 30
    ? { data: { items: [], next_offset: null } }
    : { data: { items: [{ owner_uid: 'alice', slug: 'remaining' }], next_offset: null } })

  await catalog.fetchCatalog({ page: 2 })

  assert.deepEqual(Array.from(catalog.calls, ({ offset }) => offset), [30, 0])
  assert.equal(catalog.catalogPage.value, 1)
  assert.deepEqual(Array.from(catalog.catalogCards.value, ({ slug }) => slug), ['remaining'])
})

test('筛选条件改变后忽略旧个人目录响应', async () => {
  let finishOld
  const catalog = createCatalog(async () => new Promise((resolve) => { finishOld = resolve }))

  const oldRequest = catalog.fetchCatalog({ reset: true })
  catalog.searchQuery.value = '新报告'
  finishOld({ data: { items: [{ owner_uid: 'alice', slug: 'old' }], next_offset: 1 } })
  await oldRequest

  assert.deepEqual(Array.from(catalog.catalogCards.value), [])
  assert.equal(catalog.catalogPage.value, 1)
  assert.equal(catalog.catalogHasMore.value, false)
  assert.equal(catalog.catalogLoading.value, false)
})

test('旧个人目录请求失败时不提示新筛选结果出错', async () => {
  let rejectOld
  const catalog = createCatalog(async ({ search }) => search === '报告'
    ? new Promise((resolve, reject) => { rejectOld = reject })
    : { data: { items: [{ owner_uid: 'alice', slug: 'new' }], next_offset: null } })

  const oldRequest = catalog.fetchCatalog({ reset: true })
  catalog.searchQuery.value = '新报告'
  await catalog.fetchCatalog({ reset: true })
  rejectOld(new Error('旧请求失败'))
  await oldRequest

  assert.deepEqual(Array.from(catalog.catalogCards.value, ({ slug }) => slug), ['new'])
  assert.equal(catalog.catalogLoading.value, false)
})
