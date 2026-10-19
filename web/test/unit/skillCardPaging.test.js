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
    sharedHasMore: ref(false),
    sharedLoadingMore: ref(false),
    sharedNextOffset: ref(0),
    searchQuery: ref(''),
    appliedOwnerSearch: ref(''),
    appliedDepartmentId: ref(undefined)
  }
  const calls = []
  const actions = runInNewContext(
    `let sharedRequestSeq = 0; const sharedPageSize = 30; ${code}; ({ fetchSkills, loadMoreSharedSkills })`,
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

test('治理目录只请求共享来源，加载更多使用下一页偏移且不会重复卡片', async () => {
  const catalog = createCatalog(async ({ offset }) => offset === 0
    ? { data: [{ id: 1, slug: 'a' }, { id: 2, slug: 'b' }], has_more: true }
    : { data: [{ id: 2, slug: 'b' }, { id: 3, slug: 'c' }], has_more: false })

  await catalog.fetchSkills()
  assert.equal(catalog.sharedHasMore.value, true)
  assert.equal(catalog.sharedNextOffset.value, 2)
  await catalog.loadMoreSharedSkills()
  assert.deepEqual(catalog.calls.map(({ source, limit, offset }) => ({ source, limit, offset })), [
    { source: 'shared', limit: 30, offset: 0 },
    { source: 'shared', limit: 30, offset: 2 }
  ])
  assert.deepEqual(Array.from(catalog.skills.value, ({ slug }) => slug), ['a', 'b', 'c'])
  assert.equal(catalog.sharedNextOffset.value, 4)
  assert.equal(catalog.sharedHasMore.value, false)
})

test('新的搜索替换目录，旧请求不能覆盖搜索结果', async () => {
  let finishOld
  const catalog = createCatalog(async ({ search }) => search
    ? { data: [{ id: 9, slug: 'new' }], has_more: false }
    : new Promise((resolve) => { finishOld = resolve }))

  const oldRequest = catalog.fetchSkills()
  catalog.searchQuery.value = 'new'
  await catalog.fetchSkills()
  finishOld({ data: [{ id: 1, slug: 'old' }], has_more: true })
  await oldRequest

  assert.deepEqual(Array.from(catalog.skills.value, ({ slug }) => slug), ['new'])
  assert.equal(catalog.sharedHasMore.value, false)
  assert.equal(catalog.loading.value, false)
})
