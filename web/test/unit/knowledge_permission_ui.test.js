import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  getShareConfigLabel,
  isScopeWithinParent,
  normalizeKnowledgeBaseCapabilityPolicy
} from '../../src/utils/shareConfig.js'

const readSource = (relativePath) => readFileSync(new URL(relativePath, import.meta.url), 'utf8')

test('知识库共享标签区分读取、编辑和管理范围', () => {
  assert.equal(
    getShareConfigLabel({
      version: 2,
      read_scope: { access_level: 'global' },
      edit_scope: { access_level: 'department', department_ids: [1, 2] },
      manage_scope: { access_level: 'user', user_uids: ['admin-1'] }
    }),
    '读全局 · 编部门(2) · 管用户(1)'
  )
})

test('部门读取范围允许给部门内指定用户授予内容贡献权限', () => {
  const readScope = { access_level: 'department', department_ids: [1] }
  const contributor = { access_level: 'user', user_uids: ['editor-1'] }
  const outsider = { access_level: 'user', user_uids: ['editor-2'] }
  const userDepartments = new Map([
    ['editor-1', 1],
    ['editor-2', 2]
  ])

  assert.equal(isScopeWithinParent(contributor, readScope, userDepartments), true)
  assert.equal(isScopeWithinParent(outsider, readScope, userDepartments), false)
})

test('旧知识库自动使用默认能力模板，自定义模板只保留对应档位能力', () => {
  assert.deepEqual(normalizeKnowledgeBaseCapabilityPolicy(), {
    read: ['view', 'search', 'download'],
    edit: ['upload', 'metadata', 'parse', 'index'],
    manage: [
      'configure',
      'share',
      'grant',
      'delete-document',
      'delete-knowledge-base'
    ]
  })
  assert.deepEqual(
    normalizeKnowledgeBaseCapabilityPolicy({
      read: ['view', 'upload'],
      edit: ['parse'],
      manage: ['grant']
    }),
    { read: ['view'], edit: ['parse'], manage: ['grant'] }
  )
})

test('授权普通用户可以上传和处理文档，但删除与配置只对管理员开放', () => {
  const extensionsSource = readSource('../../src/views/ExtensionsView.vue')
  const detailSource = readSource('../../src/views/DataBaseInfoView.vue')
  const fileTableSource = readSource('../../src/components/FileTable.vue')
  const listSource = readSource('../../src/views/DataBaseView.vue')
  const routerSource = readSource('../../src/router/index.js')
  const apiSource = readSource('../../src/apis/knowledge_api.js')

  assert.match(extensionsSource, /const userExtensionTabs = \[[\s\S]*?key: 'knowledge'/)
  assert.match(extensionsSource, /v-if="activeTab === 'knowledge'"/)
  assert.match(
    routerSource,
    /name: 'ExtensionKnowledgeBaseDetail'[\s\S]*?meta: \{\s*keepAlive: false,\s*requiresAuth: true\s*\}/
  )
  assert.match(
    detailSource,
    /const canEditDatabase = computed\(\(\) => database\.value\?\.can_edit === true\)/
  )
  assert.match(detailSource, /v-if="canUploadFiles \|\| canEditMetadata"[\s\S]*?>上传</)
  assert.match(detailSource, /:readonly="!canMaintainFiles"/)
  assert.match(detailSource, /:can-delete="canDeleteDocuments"/)
  assert.match(detailSource, /v-if="canOpenConfiguration"[\s\S]*?>配置</)
  assert.match(fileTableSource, /v-if="!readonly && canDelete"[\s\S]*?删除文件/)
  assert.match(fileTableSource, /if \(readonly\.value \|\| !canDelete\.value\) return/)
  assert.match(listSource, /v-if="canDeleteDatabase\(database\)" key="delete"/)
  assert.match(
    listSource,
    /const canDeleteDatabase = \(database\) =>[\s\S]*?'delete-knowledge-base'/
  )
  assert.match(apiSource, /getPermissionOptions:[\s\S]*?apiGet\(`/)
  assert.match(apiSource, /updateDatabase:[\s\S]*?apiPut\(`/)
  assert.match(apiSource, /deleteDatabase:[\s\S]*?apiDelete\(`/)
})

test('知识库权限界面展示快捷授权、高级权限树和系统继承管理规则', () => {
  const createSource = readSource('../../src/components/knowledge/DatabaseCreateFlowModal.vue')
  const shareSource = readSource('../../src/components/ShareConfigForm.vue')
  const detailSource = readSource('../../src/views/DataBaseInfoView.vue')

  assert.match(createSource, /v-model:value="form\.department_id"/)
  assert.match(createSource, /:resource-department-id="form\.department_id"/)
  assert.match(createSource, /show-knowledge-base-inheritance/)
  assert.match(shareSource, /超级管理员和所属部门管理员自动拥有管理权限/)
  assert.match(shareSource, /candidate\.role === 'superadmin'/)
  assert.match(shareSource, /candidate\.role === 'admin'/)
  assert.match(shareSource, /指定协管成员/)
  assert.match(
    shareSource,
    /scopeKey === 'manage_scope'[\s\S]*?shareModeOptions\.value\.filter\(\(option\) => option\.value === 'user'\)/
  )
  assert.match(shareSource, /scopeKey === 'manage_scope'[\s\S]*?accessLevel !== 'user'/)
  assert.match(shareSource, /!inheritedManagerUids\.value\.has\(String\(uid\)\)/)
  assert.match(shareSource, />\s*快捷授权\s*</)
  assert.match(shareSource, />\s*高级权限树\s*</)
  assert.match(shareSource, /profileTitle: '只读成员'/)
  assert.match(shareSource, /profileTitle: '内容贡献者'/)
  assert.match(shareSource, /profileTitle: '知识库管理员'/)
  assert.match(shareSource, /title: '访问与检索'/)
  assert.match(shareSource, /title: '内容维护'/)
  assert.match(shareSource, /title: '成员与共享'/)
  assert.match(shareSource, /title: '危险操作'/)
  assert.match(shareSource, /勾选每一档新增的能力/)
  assert.match(shareSource, /capability_policy/)
  assert.match(shareSource, /toggleCapability/)
  assert.match(
    shareSource,
    /permissionLevelOrder\[item\.level\] <= selectedPermissionProfile\.value\.order/
  )
  assert.match(shareSource, /修改下级能力会同时影响对应成员/)
  assert.match(shareSource, />\s*继承\s*</)
  assert.match(shareSource, /配置知识库/)
  assert.doesNotMatch(shareSource, /创建人.*继承管理权限/)
  assert.match(detailSource, /:resource-department-id="database\.owner_department_id"/)
  assert.match(detailSource, /:available-departments="departments"/)
  assert.match(detailSource, /databaseApi\.getPermissionOptions\(targetKbId\)/)
  assert.doesNotMatch(detailSource, /:auto-select-user-dept="true"/)
})
