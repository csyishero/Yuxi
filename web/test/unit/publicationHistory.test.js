import assert from 'node:assert/strict'
import test from 'node:test'

import { buildPublicationGroups } from '../../src/components/extensions/publicationHistory.js'

test('shows only this author’s submitted slugs, including requests whose source is gone', () => {
  const groups = buildPublicationGroups({
    ownerUid: 'alice',
    personalSkills: [
      { slug: 'report', name: 'Current report', owner_uid: 'alice' },
      { slug: 'draft-only', name: 'Never submitted', owner_uid: 'alice' },
      { slug: 'borrowed', name: 'Someone else’s source', owner_uid: 'bob' }
    ],
    sharedRequests: [
      { id: 's-report', owner_uid: 'alice', personal_slug: 'report', name: 'Old report', status: 'pending', created_at: '2026-10-10T10:00:00Z' },
      { id: 's-gone', owner_uid: 'alice', personal_slug: 'gone', name: 'Deleted source', status: 'rejected', created_at: '2026-10-09T10:00:00Z' },
      { id: 's-borrowed', owner_uid: 'alice', personal_slug: 'borrowed', status: 'pending', created_at: '2026-10-08T10:00:00Z' },
      { id: 's-other', owner_uid: 'bob', personal_slug: 'report', status: 'pending', created_at: '2026-10-11T10:00:00Z' }
    ],
    marketRequests: []
  })

  assert.deepEqual(groups.map((group) => group.slug), ['report', 'gone', 'borrowed'])
  assert.equal(groups[0].sourceAvailable, true)
  assert.equal(groups[0].skill.name, 'Current report')
  assert.equal(groups[0].name, 'Current report')
  assert.equal(groups[1].sourceAvailable, false)
  assert.equal(groups[1].name, 'Deleted source')
  assert.equal(groups[2].sourceAvailable, false)
  assert.deepEqual(groups[0].shared.requests.map((request) => request.id), ['s-report'])
})

test('combines both channels while retaining every request and each latest approved release', () => {
  const sharedRequests = Object.freeze([
    Object.freeze({ id: 's-approved-1', owner_uid: 'alice', personal_slug: 'report', status: 'approved', created_at: '2026-10-01T10:00:00Z', published_version: '1.0.0', published_copy_status: 'disabled' }),
    Object.freeze({ id: 's-pending', owner_uid: 'alice', personal_slug: 'report', status: 'pending', created_at: '2026-10-12T10:00:00Z' }),
    Object.freeze({ id: 's-approved-2', owner_uid: 'alice', personal_slug: 'report', status: 'approved', created_at: '2026-10-08T10:00:00Z', published_version: '2.0.0', published_copy_status: 'missing' }),
    Object.freeze({ id: 's-rejected', owner_uid: 'alice', personal_slug: 'report', status: 'rejected', created_at: '2026-10-10T10:00:00Z' })
  ])
  const marketRequests = Object.freeze([
    Object.freeze({ id: 'm-approved', owner_uid: 'alice', personal_slug: 'report', status: 'approved', created_at: '2026-10-02T10:00:00Z', published_version: 3, current_market_version: 4, existing_market_scope: 'department', existing_market_department_id: 7 }),
    Object.freeze({ id: 'm-pending', owner_uid: 'alice', personal_slug: 'report', status: 'pending', created_at: '2026-10-11T10:00:00Z', current_market_version: 4, existing_market_scope: 'department', existing_market_department_id: 7 })
  ])

  const [group] = buildPublicationGroups({ sharedRequests, marketRequests, personalSkills: [], ownerUid: 'alice' })

  assert.deepEqual(group.shared.requests.map((request) => request.id), ['s-pending', 's-rejected', 's-approved-2', 's-approved-1'])
  assert.equal(group.shared.latest.id, 's-pending')
  assert.equal(group.shared.approved.id, 's-approved-2')
  assert.equal(group.shared.version, '2.0.0')
  assert.equal(group.shared.copyStatus, 'missing')
  assert.deepEqual(group.market.requests.map((request) => request.id), ['m-pending', 'm-approved'])
  assert.equal(group.market.latest.id, 'm-pending')
  assert.equal(group.market.approved.id, 'm-approved')
  assert.equal(group.market.version, 4)
  assert.equal(group.market.scope, 'department')
  assert.equal(group.market.departmentId, 7)
})

test('sorts groups by latest review or submission without changing request order', () => {
  const groups = buildPublicationGroups({
    ownerUid: 'alice',
    personalSkills: [],
    sharedRequests: [
      { id: 'a-approved', owner_uid: 'alice', personal_slug: 'a', status: 'approved', created_at: '2026-10-01T10:00:00Z', reviewed_at: '2026-10-15T10:00:00Z' },
      { id: 'a-pending', owner_uid: 'alice', personal_slug: 'a', status: 'pending', created_at: '2026-10-10T10:00:00Z' },
      { id: 'b-pending', owner_uid: 'alice', personal_slug: 'b', status: 'pending', created_at: '2026-10-14T10:00:00Z' }
    ],
    marketRequests: []
  })

  assert.deepEqual(groups.map((group) => group.slug), ['a', 'b'])
  assert.equal(groups[0].lastActivityAt, '2026-10-15T10:00:00Z')
  assert.deepEqual(groups[0].shared.requests.map((request) => request.id), ['a-pending', 'a-approved'])
  assert.equal(groups[0].shared.latest.id, 'a-pending')
})

test('keeps source availability unknown when the personal-skill list was not loaded', () => {
  const sharedRequests = [{ id: 's-old', owner_uid: 'alice', personal_slug: 'archived', status: 'approved', created_at: '2026-10-01T10:00:00Z' }]

  const unknown = buildPublicationGroups({ ownerUid: 'alice', sharedRequests, marketRequests: [], personalSkills: null })
  const missing = buildPublicationGroups({ ownerUid: 'alice', sharedRequests, marketRequests: [], personalSkills: [] })

  assert.equal(unknown[0].sourceAvailable, null)
  assert.equal(missing[0].sourceAvailable, false)
})
