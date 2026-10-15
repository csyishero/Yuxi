import assert from 'node:assert/strict'
import test from 'node:test'
import { combineSkillCards, filterSkillCards, personalSkillItems } from '../../src/components/extensions/skillHubCatalog.js'

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
  assert.deepEqual(cards.map((item) => [item.kind, item.slug]), [['market', 'weekly-brief'], ['existing', 'knowledge-base']])
  assert.deepEqual(filterSkillCards(cards, '', 'installed').map((item) => item.slug), ['weekly-brief'])
  assert.deepEqual(filterSkillCards(cards, '知识库', 'builtin').map((item) => item.slug), ['knowledge-base'])
})

test('个人空间不展示其他人的个人技能，并保留作者同名的市场安装', () => {
  const items = personalSkillItems(
    [
      { slug: 'mine', source_scope: 'personal', owner_uid: 'alice' },
      { slug: 'other', source_scope: 'personal', owner_uid: 'bob' },
      { slug: 'shared', source_scope: 'global' }
    ],
    [{ slug: 'mine', installed_version: 1 }, { slug: 'market-installed', installed_version: 2 }, { slug: 'not-installed', installed_version: null }],
    'alice'
  )
  assert.deepEqual(items.map((item) => [item.kind, item.slug]), [['personal', 'mine'], ['market', 'mine'], ['market', 'market-installed']])
})
