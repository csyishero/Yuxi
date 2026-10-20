import assert from 'node:assert/strict'
import test from 'node:test'
import { combineSkillCards, filterSkillCards, marketInstallManagementItems, ownPersonalSkill, personalSkillItems, personalSpaceSkillGroups, searchPersonalSpaceSkillGroups, skillDetailRoute } from '../../src/components/extensions/skillHubCatalog.js'
import { skillCategory, skillCategoryLabel } from '../../src/components/extensions/skillCategories.js'

test('统一目录保留市场版本，个人和退役内置技能不重复出现', () => {
  const cards = combineSkillCards(
    [{ slug: 'weekly-brief', name: '项目周报', installed_version: 1 }],
    [
      { slug: 'weekly-brief', name: '旧共享版', source_scope: 'global', source_type: 'upload' },
      { slug: 'my-draft', name: '我的草稿', source_scope: 'personal', source_type: 'personal' },
      { slug: 'image-gen', name: '退役技能', source_scope: 'builtin', source_type: 'builtin' },
      { slug: 'disabled-shared', name: '停用技能', source_scope: 'global', source_type: 'upload', enabled: false },
      { slug: 'shadowed-shared', name: '被个人版本覆盖', source_scope: 'global', source_type: 'upload', shadowed_by_personal: true },
      { slug: 'knowledge-base', name: '知识库', source_scope: 'builtin', source_type: 'builtin' }
    ]
  )
  assert.deepEqual(filterSkillCards(cards, '', 'all').map((item) => [item.kind, item.slug]), [['market', 'weekly-brief'], ['existing', 'knowledge-base']])
  assert.deepEqual(filterSkillCards(cards, '', 'shared').map((item) => item.slug), ['weekly-brief'])
  assert.deepEqual(filterSkillCards(cards, '', 'installed').map((item) => item.slug), ['weekly-brief'])
  assert.deepEqual(filterSkillCards(cards, '知识库', 'builtin').map((item) => item.slug), ['knowledge-base'])
})

test('当前页的市场卡替代同源共享，缺席时回退最新共享，同名他人保持独立', () => {
  const shared = [
    { id: 2, slug: 'my-skill-v2', name: '演示助手', owner_uid: 'alice', source_type: 'personal_share', related_market_slug: 'my-skill-2' },
    { id: 4, slug: 'my-skill-v3', name: '演示助手', owner_uid: 'alice', source_type: 'personal_share', related_market_slug: 'my-skill-2' },
    { id: 5, slug: 'another-skill', name: '演示助手', owner_uid: 'bob', source_type: 'upload' }
  ]
  const cards = combineSkillCards(
    [{ slug: 'my-skill-2', name: '演示助手', owner_uid: 'alice' }],
    shared
  )
  assert.equal(cards[0].shared_available_slug, 'my-skill-v3')
  assert.deepEqual(filterSkillCards(cards, '演示助手', 'all').map((item) => item.slug), ['my-skill-2', 'another-skill'])
  assert.deepEqual(filterSkillCards(cards, '', 'shared').map((item) => item.slug), ['my-skill-v3', 'another-skill'])
  const firstPageWithoutMarket = combineSkillCards([], shared)
  assert.deepEqual(filterSkillCards(firstPageWithoutMarket, '', 'all').map((item) => item.slug), ['my-skill-v3', 'another-skill'])
  assert.deepEqual(filterSkillCards(firstPageWithoutMarket, '', 'shared').map((item) => item.slug), ['my-skill-v3', 'another-skill'])
})

test('搜索和方向不同于市场卡时仍显示匹配的共享代表', () => {
  const cards = combineSkillCards(
    [{ slug: 'report-market', name: 'PPT 助手', category: 'office_tools' }],
    [
      { id: 2, slug: 'report-v2', name: '月报助手', category: 'finance_analysis', source_type: 'personal_share', related_market_slug: 'report-market' },
      { id: 4, slug: 'report-v3', name: 'PPT 助手', category: 'office_tools', source_type: 'personal_share', related_market_slug: 'report-market' }
    ]
  )
  assert.deepEqual(filterSkillCards(cards, 'PPT', 'all').map((item) => item.slug), ['report-market'])
  assert.deepEqual(filterSkillCards(cards, 'PPT', 'shared').map((item) => item.slug), ['report-v3'])
  assert.deepEqual(filterSkillCards(cards, '月报', 'all').map((item) => item.slug), ['report-v2'])
  assert.deepEqual(filterSkillCards(cards, '', 'all', 'finance_analysis').map((item) => item.slug), ['report-v2'])
})

test('停用或被个人原件覆盖的共享副本不标记市场卡为已可用', () => {
  const [market] = combineSkillCards(
    [{ slug: 'mine-2', name: '演示助手' }],
    [
      { slug: 'mine-v2', related_market_slug: 'mine-2', enabled: false },
      { slug: 'mine-v3', related_market_slug: 'mine-2', shadowed_by_personal: true }
    ]
  )
  assert.equal(market.shared_available_slug, null)
})

test('市场后端给出的可见共享来源不因当前共享页缺席而丢失', () => {
  const [market] = combineSkillCards(
    [{ slug: 'report-market', shared_available_slug: 'report-shared-v3' }],
    []
  )
  assert.equal(market.shared_available_slug, 'report-shared-v3')

  const [noShared] = combineSkillCards(
    [{ slug: 'report-market', shared_available_slug: null }],
    [{ slug: 'stale-shared', related_market_slug: 'report-market', source_type: 'personal_share' }]
  )
  assert.equal(noShared.shared_available_slug, null)
})

test('个人空间折叠本人原件和已安装市场版本，保留他人市场安装', () => {
  const items = personalSkillItems(
    [
      { slug: 'mine', source_scope: 'personal', owner_uid: 'alice' },
      { slug: 'other', source_scope: 'personal', owner_uid: 'bob' },
      { slug: 'shared', source_scope: 'global' }
    ],
    [
      { slug: 'mine-2', owner_uid: 'alice', own_personal_slug: 'mine', installed_version: 1 },
      { slug: 'market-installed', owner_uid: 'bob', installed_version: 2 },
      { slug: 'not-installed', owner_uid: 'alice', own_personal_slug: 'mine', installed_version: null }
    ],
    'alice'
  )
  assert.deepEqual(items.map((item) => [item.kind, item.slug]), [['personal', 'mine'], ['market', 'market-installed']])
  assert.equal(items[0].market_installed_version, 1)
  assert.equal(items[0].market_slug, 'mine-2')
  assert.equal(items[0].market_install.slug, 'mine-2')
  assert.equal(items[0].market_visible, true)
})

test('共享来源保留每个已启用版本和个人覆盖项，全部视图继续合并同源入口', () => {
  const groups = personalSpaceSkillGroups(
    [
      { slug: 'mine', source_scope: 'personal', owner_uid: 'alice' },
      { id: 1, slug: 'report-v1', source_scope: 'shared', source_type: 'personal_share', related_market_slug: 'report-market' },
      { id: 2, slug: 'report-v2', source_scope: 'shared', source_type: 'personal_share', related_market_slug: 'report-market' },
      { id: 3, slug: 'mine', source_scope: 'shared', source_type: 'upload', shadowed_by_personal: true }
    ],
    [],
    'alice'
  )

  assert.deepEqual(groups.shared.map((item) => item.id), [1, 2, 3])
  assert.deepEqual(groups.all.map((item) => item.slug), ['mine', 'report-v2'])
})

test('失去部门权限后仍保留自己的市场安装入口以便卸载', () => {
  const items = personalSkillItems(
    [{ slug: 'mine', source_scope: 'personal', owner_uid: 'alice' }],
    [
      { slug: 'mine-2', owner_uid: 'alice', own_personal_slug: 'mine', installed_version: 1, visible: false },
      { slug: 'other-2', owner_uid: 'bob', installed_version: 2, visible: false }
    ],
    'alice'
  )
  assert.deepEqual(items.map((item) => [item.kind, item.slug]), [['personal', 'mine'], ['market', 'other-2']])
  assert.equal(items[0].market_visible, false)
  assert.equal(items[0].market_install.slug, 'mine-2')
  assert.equal(items[1].visible, false)
})

test('作者个人原件删除后市场安装仍显示，其他作者同名原件不阻止安装', () => {
  const market = { slug: 'mine-2', name: '演示助手', owner_uid: 'alice', own_personal_slug: 'mine', installed_version: 1 }
  assert.equal(ownPersonalSkill(market, [{ slug: 'mine', source_scope: 'personal', owner_uid: 'bob' }], 'alice'), null)
  assert.equal(ownPersonalSkill({ ...market, owner_uid: 'bob' }, [{ slug: 'mine', source_scope: 'personal', owner_uid: 'alice' }], 'alice'), null)
  assert.deepEqual(personalSkillItems([], [market], 'alice').map((item) => [item.kind, item.slug]), [['market', 'mine-2']])
  assert.deepEqual(ownPersonalSkill(market, [{ slug: 'mine', source_scope: 'personal', owner_uid: 'alice' }], 'alice')?.slug, 'mine')
})

test('市场卡链接到作者个人原件时携带个人范围，普通共享技能不携带', () => {
  const market = { slug: 'reading-market', owner_uid: 'alice', own_personal_slug: 'reading' }
  const personal = ownPersonalSkill(market, [{ slug: 'reading', source_scope: 'personal', owner_uid: 'alice' }], 'alice')
  assert.deepEqual(skillDetailRoute(personal), { path: '/extensions/skill/reading', query: { scope: 'personal' } })
  assert.deepEqual(skillDetailRoute({ slug: 'reading', source_scope: 'shared' }), { path: '/extensions/skill/reading' })
})

test('个人空间分类包含本人安装、共享和系统技能，全部视图折叠同源共享副本', () => {
  const groups = personalSpaceSkillGroups(
    [
      { slug: 'mine', source_scope: 'personal', owner_uid: 'alice' },
      { slug: 'brief-v2', source_scope: 'shared', source_type: 'personal_share', related_market_slug: 'brief-market' },
      { slug: 'team', source_scope: 'shared', source_type: 'upload' },
      { slug: 'knowledge-base', source_scope: 'builtin', source_type: 'builtin' },
      { slug: 'other-personal', source_scope: 'personal', owner_uid: 'bob' },
      { slug: 'disabled', source_scope: 'shared', enabled: false }
    ],
    [{ slug: 'brief-market', name: '简报', installed_version: 2, visible: true }],
    'alice'
  )
  assert.deepEqual(groups.private.map((item) => item.slug), ['mine', 'brief-market'])
  assert.deepEqual(groups.shared.map((item) => item.slug), ['brief-v2', 'team'])
  assert.deepEqual(groups.system.map((item) => item.slug), ['knowledge-base'])
  assert.deepEqual(groups.all.map((item) => item.slug), ['mine', 'brief-market', 'team', 'knowledge-base'])
})

test('个人空间搜索同步缩小所有来源分类的数量和列表', () => {
  const groups = personalSpaceSkillGroups(
    [
      { slug: 'my-report', name: '周报助手', source_scope: 'personal', owner_uid: 'alice' },
      { slug: 'team-report', name: '部门报告', source_scope: 'shared', source_type: 'upload' },
      { slug: 'knowledge-base', name: '知识库', source_scope: 'builtin', source_type: 'builtin' }
    ],
    [{ slug: 'market-report', name: '报告市场版', installed_version: 1, visible: true }],
    'alice'
  )
  const searched = searchPersonalSpaceSkillGroups(groups, '报告')
  assert.deepEqual(searched.all.map((item) => item.slug), ['market-report', 'team-report'])
  assert.deepEqual(searched.private.map((item) => item.slug), ['market-report'])
  assert.deepEqual(searched.shared.map((item) => item.slug), ['team-report'])
  assert.deepEqual(searched.system, [])
  assert.deepEqual(searchPersonalSpaceSkillGroups(groups, '  KNOWLEDGE ').system.map((item) => item.slug), ['knowledge-base'])
})

test('旧市场安装仍可管理，但不冒充个人原件或公开详细信息', () => {
  const cards = marketInstallManagementItems([
    { slug: 'mine', installed_version: 1, visible: true, name: '简报' },
    { slug: 'old', installed_version: 2, visible: false, name: '他人技能', description: '不应显示', owner_uid: 'bob', category: 'office_tools' },
    { slug: 'not-installed', installed_version: null }
  ])
  assert.deepEqual(cards.map((item) => item.slug), ['mine', 'old'])
  assert.ok(cards.every((item) => item.kind === 'market_install' && item.sourceScope === 'market_install'))
  assert.equal(cards[1].visible, false)
  assert.equal(cards[1].name, 'old')
  assert.equal(cards[1].description, '')
  assert.equal(cards[1].owner_uid, undefined)
  assert.equal(cards[1].category, undefined)
})

test('目录按方向筛选，历史未分类技能归入其它', () => {
  const cards = [
    { kind: 'market', slug: 'table', name: '表格助手', category: 'office_tools' },
    { kind: 'market', slug: 'old', name: '旧技能' },
    { kind: 'existing', slug: 'legacy', name: '旧共享', category: 'unknown' }
  ]
  assert.deepEqual(filterSkillCards(cards, '', 'all', 'office_tools').map((item) => item.slug), ['table'])
  assert.deepEqual(filterSkillCards(cards, '', 'all', 'other').map((item) => item.slug), ['old', 'legacy'])
  assert.equal(skillCategory('unknown'), 'other')
  assert.equal(skillCategoryLabel('office_tools'), '办公工具')
})
