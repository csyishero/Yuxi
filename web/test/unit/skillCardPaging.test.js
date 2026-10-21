import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import { ref } from 'vue'

const source = readFileSync(
  new URL('../../src/components/extensions/SkillCardList.vue', import.meta.url),
  'utf8'
)
const code = source.slice(source.indexOf('const fetchSkills ='), source.indexOf('const beforeSkillUpload ='))

function createCatalog(listSkillCards) {
  const state = {
    loading: ref(false),
    skills: ref([]),
    sharedPage: ref(1),
    sharedTotal: ref(0),
    selectedCardSlugs: ref([]),
    searchQuery: ref(''),
    appliedOwnerSearch: ref(''),
    appliedDepartmentId: ref(undefined)
  }
  const calls = []
  const actions = runInNewContext(
    `let sharedRequestSeq = 0; const sharedPageSize = 30; ${code}; ({ fetchSkills, changeSharedPage })`,
    {
      ...state,
      userStore: { isAdmin: false },
      skillApi: { listSkillCards: async (params) => { calls.push(params); return listSkillCards(params) } },
      fetchCatalog: async () => {},
      message: { error: (value) => { throw new Error(value) } }
    }
  )
  return { ...state, ...actions, calls }
}

test('治理目录只请求共享来源，切页替换卡片并保留精确总数', async () => {
  const catalog = createCatalog(async ({ offset }) => offset === 0
    ? { data: [{ id: 1, slug: 'a' }, { id: 2, slug: 'b' }], total: 32 }
    : { data: [{ id: 2, slug: 'b' }, { id: 3, slug: 'c' }], total: 32 })

  await catalog.fetchSkills()
  assert.equal(catalog.sharedTotal.value, 32)
  assert.equal(catalog.sharedPage.value, 1)
  await catalog.fetchSkills({ page: 2 })
  assert.deepEqual(catalog.calls.map(({ source, limit, offset }) => ({ source, limit, offset })), [
    { source: 'shared', limit: 30, offset: 0 },
    { source: 'shared', limit: 30, offset: 30 }
  ])
  assert.deepEqual(Array.from(catalog.skills.value, ({ slug }) => slug), ['b', 'c'])
  assert.equal(catalog.sharedPage.value, 2)
  assert.equal(catalog.sharedTotal.value, 32)
})

test('新的搜索替换目录，旧请求不能覆盖搜索结果', async () => {
  let finishOld
  const catalog = createCatalog(async ({ search }) => search
    ? { data: [{ id: 9, slug: 'new' }], total: 32 }
    : new Promise((resolve) => { finishOld = resolve }))

  const oldRequest = catalog.fetchSkills()
  catalog.searchQuery.value = 'new'
  await catalog.fetchSkills()
  finishOld({ data: [{ id: 1, slug: 'old' }], total: 32 })
  await oldRequest

  assert.deepEqual(Array.from(catalog.skills.value, ({ slug }) => slug), ['new'])
  assert.equal(catalog.sharedTotal.value, 32)
  assert.equal(catalog.loading.value, false)
})
