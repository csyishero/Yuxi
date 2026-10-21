import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
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
    catalogCards: ref([]),
    catalogDepartmentId: ref(3),
    ownerSearch: ref(''),
    appliedDepartmentId: ref(3),
    appliedOwnerSearch: ref('alice'),
    catalogPage: ref(1),
    catalogHasMore: ref(false),
    catalogLoading: ref(false),
    searchQuery: ref('报告')
  }
  const calls = []
  const actions = runInNewContext(
    `let catalogRequestSeq = 0; const catalogPageSize = 30; ${code}; ({ fetchCatalog })`,
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

test('治理目录按页替换卡片并在服务端排除本人，不同所有者同名卡片保留', async () => {
  const catalog = createCatalog(async ({ offset }) => offset === 0
    ? { data: { items: [
      { owner_uid: 'alice', slug: 'same' }, { owner_uid: 'alice', slug: 'alpha' }
    ], next_offset: 2 } }
    : { data: { items: [
      { owner_uid: 'alice', slug: 'alpha' }, { owner_uid: 'bob', slug: 'same' }
    ], next_offset: null } }, { governanceOnly: true })

  await catalog.fetchCatalog({ reset: true })
  await catalog.fetchCatalog({ page: 2 })

  assert.deepEqual(catalog.calls.map(({ departmentId, ownerSearch, offset, limit, cardPage, search, excludeOwn }) => ({
    departmentId, ownerSearch, offset, limit, cardPage, search, excludeOwn
  })), [
    { departmentId: 3, ownerSearch: 'alice', offset: 0, limit: 30, cardPage: true, search: '报告', excludeOwn: true },
    { departmentId: 3, ownerSearch: 'alice', offset: 30, limit: 30, cardPage: true, search: '报告', excludeOwn: true }
  ])
  assert.deepEqual(
    Array.from(catalog.catalogCards.value, ({ owner_uid, slug }) => `${owner_uid}:${slug}`),
    ['alice:alpha', 'bob:same']
  )
  assert.equal(catalog.catalogHasMore.value, false)
})

test('筛选条件改变后忽略旧个人目录响应', async () => {
  let finishOld
  const catalog = createCatalog(async () => new Promise((resolve) => { finishOld = resolve }))

  const oldRequest = catalog.fetchCatalog({ reset: true })
  catalog.searchQuery.value = '新报告'
  finishOld({ data: { items: [{ owner_uid: 'alice', slug: 'old' }], next_offset: 1 } })
  await oldRequest

  assert.deepEqual(Array.from(catalog.catalogCards.value), [])
  assert.equal(catalog.catalogHasMore.value, false)
  assert.equal(catalog.catalogLoading.value, false)
})
