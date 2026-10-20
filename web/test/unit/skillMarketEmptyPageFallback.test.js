import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'

const source = readFileSync(new URL('../../src/components/extensions/SkillMarket.vue', import.meta.url), 'utf8')
const marketCode = source.slice(source.indexOf('async function fetchMarketPage('), source.indexOf('async function refresh()'))
const sharedCode = source.slice(source.indexOf('async function fetchLegacySource('), source.indexOf('async function fetchLegacy()'))
const state = (value) => ({ value })

test('市场空尾页即使总数仍指向当前页也只向前回退', async () => {
  const calls = []
  const fetchMarketPage = runInNewContext(`let requestSequence = 0; ${marketCode}; fetchMarketPage`, {
    marketPage: state(1), marketPageSize: state(24), loading: state(false), marketError: state(''),
    cards: state([]), marketTotal: state(0), appliedMarketQuery: state(''), selectedCategory: state('all'),
    listMarketSkills: async ({ offset }) => {
      calls.push(offset)
      if (calls.length > 3) throw new Error('repeated the same empty page')
      return offset === 24 ? { data: [], total: 48 } : { data: [{ slug: 'first' }], total: 48 }
    },
    errorText: (error) => error.message
  })

  await fetchMarketPage(2)
  assert.deepEqual(calls, [24, 0])
})

test('共享空尾页即使总数仍指向当前页也只向前回退', async () => {
  const calls = []
  const fetchLegacySource = runInNewContext(`const legacyRequestSequence = { personal: 0, shared: 0, builtin: 0 }; const personalPageSize = 30; ${sharedCode}; fetchLegacySource`, {
    appliedPersonalQuery: state(''), legacyPages: state({ personal: 1, shared: 1, builtin: 1 }),
    legacyPageLoading: state({ personal: false, shared: false, builtin: false }),
    legacyPageSizes: state({ shared: 30, builtin: 30 }), legacyPersonalCards: state([]),
    legacySharedCards: state([]), legacyBuiltinCards: state([]), legacyTotals: state({ shared: 0, builtin: 0 }),
    legacyHasMore: state({ personal: false }), missingPersonalSlugs: state(new Set()), personalError: state(''),
    listSkillCards: async ({ offset }) => {
      calls.push(offset)
      if (calls.length > 3) throw new Error('repeated the same empty page')
      return offset === 30 ? { data: [], total: 60 } : { data: [{ slug: 'first' }], total: 60 }
    },
    errorText: (error) => error.message
  })

  await fetchLegacySource('shared', 2)
  assert.deepEqual(calls, [30, 0])
})
