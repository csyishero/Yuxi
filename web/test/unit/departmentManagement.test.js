import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import { parse } from 'vue/compiler-sfc'
import { reactive } from 'vue'
import { isPasswordLongEnough, MIN_PASSWORD_LENGTH } from '../../src/utils/passwordValidation.js'

const source = readFileSync(
  new URL('../../src/components/DepartmentManagementComponent.vue', import.meta.url),
  'utf8'
)

/** 执行真实表单逻辑，以内存部门列表核对保存结果。 */
function setupComponent() {
  const departments = [{ id: 2, name: '开发部门', description: '原描述' }]
  const errors = []
  const created = []
  const { descriptor } = parse(source)
  const script = descriptor.scriptSetup.content.replace(/^import .* from .*$/gm, '')
  const component = runInNewContext(
    `${script}\n;({ departmentManagement, showEditDepartmentModal, showAddDepartmentModal, handleDepartmentFormSubmit })`,
    {
      reactive,
      onMounted: () => {},
      watch: () => {},
      isPasswordLongEnough,
      MIN_PASSWORD_LENGTH,
      notification: { error: (error) => errors.push(error.message), success: () => {} },
      message: { success: () => {} },
      departmentApi: {
        getDepartments: async () => departments.map((department) => ({ ...department })),
        updateDepartment: async (id, data) => {
          assert.deepEqual(Object.keys(data).sort(), ['description', 'name'])
          Object.assign(departments.find((department) => department.id === id), data)
        },
        createDepartment: async (data) => {
          created.push({ ...data })
          departments.push({ id: 3, name: data.name, description: data.description })
        }
      }
    }
  )
  return { ...component, departments, errors, created }
}

test('编辑部门无需管理员信息，保存名称和描述后刷新列表并关闭弹窗', async () => {
  const c = setupComponent()
  c.showEditDepartmentModal(c.departments[0])
  c.departmentManagement.form.name = ' 科技部门 '
  c.departmentManagement.form.description = ' 新描述 '
  await c.handleDepartmentFormSubmit()
  assert.deepEqual(c.errors, [])
  assert.deepEqual(c.departments[0], { id: 2, name: '科技部门', description: '新描述' })
  assert.equal(c.departmentManagement.departments[0].name, '科技部门')
  assert.equal(c.departmentManagement.modalVisible, false)
  assert.equal(c.created.length, 0)
})

test('编辑部门仍拒绝空名称', async () => {
  const c = setupComponent()
  c.showEditDepartmentModal(c.departments[0])
  c.departmentManagement.form.name = ' '
  await c.handleDepartmentFormSubmit()
  assert.deepEqual(c.errors, ['部门名称不能为空'])
  assert.equal(c.departments[0].name, '开发部门')
  assert.equal(c.departmentManagement.modalVisible, true)
})

test('创建部门仍要求管理员 UID 和密码，填写完整后可创建', async () => {
  const c = setupComponent()
  c.showAddDepartmentModal()
  c.departmentManagement.form.name = '测试部门'
  await c.handleDepartmentFormSubmit()
  assert.deepEqual(c.errors, ['请输入管理员UID'])
  assert.equal(c.created.length, 0)
  c.errors.length = 0
  c.departmentManagement.form.adminUid = ' test_admin '
  await c.handleDepartmentFormSubmit()
  assert.deepEqual(c.errors, ['请输入管理员密码'])
  assert.equal(c.created.length, 0)
  c.errors.length = 0
  c.departmentManagement.form.adminPassword = 'test-password'
  c.departmentManagement.form.adminConfirmPassword = 'test-password'
  await c.handleDepartmentFormSubmit()
  assert.deepEqual(c.errors, [])
  assert.equal(c.created[0].admin_uid, 'test_admin')
  assert.equal(c.departments[1].name, '测试部门')
  assert.equal(c.departmentManagement.modalVisible, false)
})
