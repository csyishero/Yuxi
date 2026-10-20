import assert from 'node:assert/strict'
import test from 'node:test'
import { personalSpaceSkillGroups } from '../../src/components/extensions/skillHubCatalog.js'

const original = { slug: 'mine', name: 'My original', source_scope: 'personal', owner_uid: 'alice' }
const install = {
  slug: 'mine-market', name: 'Published version', owner_uid: 'alice',
  own_personal_slug: 'mine', installed_version: 2, visible: true
}

test('personal source page keeps its own market installation status when the installation is on another page', () => {
  const groups = personalSpaceSkillGroups(
    [{ ...original, market_install: install }],
    [{ slug: 'unrelated-install', name: 'Other', installed_version: 1, visible: true }],
    'alice'
  )

  const ownRow = groups.private.find((item) => item.slug === 'mine')
  assert.equal(ownRow.market_installed_version, 2)
  assert.equal(ownRow.market_install.slug, 'mine-market')
  assert.deepEqual(groups.created.map((item) => item.slug), ['mine'])
  assert.deepEqual(groups.installed.map((item) => item.slug), ['unrelated-install'])
})

test('installation source page keeps its personal original when the original is on another page', () => {
  const groups = personalSpaceSkillGroups([], [{ ...install, own_personal: original }], 'alice')

  assert.deepEqual(groups.private.map((item) => [item.kind, item.slug]), [['personal', 'mine']])
  assert.deepEqual(groups.created, [])
  assert.deepEqual(groups.installed.map((item) => [item.kind, item.slug]), [['personal', 'mine']])
  assert.equal(groups.private[0].market_installed_version, 2)
  assert.equal(groups.private[0].market_install.slug, 'mine-market')
})

test('both current pages show only one same-source row in the all view', () => {
  const groups = personalSpaceSkillGroups(
    [{ ...original, market_install: install }],
    [{ ...install, own_personal: original }],
    'alice'
  )

  assert.deepEqual(groups.all.map((item) => [item.kind, item.slug]), [['personal', 'mine']])
  assert.deepEqual(groups.created.map((item) => item.slug), ['mine'])
  assert.deepEqual(groups.installed.map((item) => item.slug), ['mine'])
})
