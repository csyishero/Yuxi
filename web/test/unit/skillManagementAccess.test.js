import assert from 'node:assert/strict'
import test from 'node:test'
import {
  canManageSkillRecord,
  isDepartmentManagedSkill,
  personalCatalogSkillForViewer
} from '../../src/components/extensions/skillManagementAccess.js'

test('Skill 管理操作只接受服务端明确授予的权限', () => {
  assert.equal(canManageSkillRecord({ can_manage: true }), true)
  assert.equal(canManageSkillRecord({ can_manage: false }), false)
  assert.equal(canManageSkillRecord({}), false)
  assert.equal(canManageSkillRecord(null), false)
})

test('本部门继承管理标记必须同时具备真实管理权限', () => {
  assert.equal(isDepartmentManagedSkill({ can_manage: true, department_managed: true }), true)
  assert.equal(isDepartmentManagedSkill({ can_manage: true, department_managed: false }), false)
  assert.equal(isDepartmentManagedSkill({ can_manage: false, department_managed: true }), false)
  assert.equal(isDepartmentManagedSkill({ department_managed: true }), false)
})

test('管理员个人目录仅恢复本人 Skill 的管理入口', () => {
  const own = personalCatalogSkillForViewer({ slug: 'mine', owner_uid: 'admin-3' }, 'admin-3')
  const other = personalCatalogSkillForViewer(
    { slug: 'other', owner_uid: 'worker-3', can_manage: true },
    'admin-3'
  )
  const unknown = personalCatalogSkillForViewer({ slug: 'unknown' }, 'admin-3')

  assert.equal(canManageSkillRecord(own), true)
  assert.equal(own.sourceScope, 'personal')
  assert.equal(canManageSkillRecord(other), false)
  assert.equal(canManageSkillRecord(unknown), false)
})
