import assert from 'node:assert/strict'
import { readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { pid } from 'node:process'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { createPinia, setActivePinia } from 'pinia'
import { createRenderer, h, nextTick } from 'vue'
import { compileScript, parse } from 'vue/compiler-sfc'
import { createServer } from 'vite'

function node(type, text = '') {
  return {
    type,
    text,
    props: {},
    style: {},
    children: [],
    parent: null,
    addEventListener() {},
    getRootNode: () => ({})
  }
}

const renderer = createRenderer({
  createElement: (type) => node(type),
  createText: (text) => node('text', text),
  createComment: (text) => node('comment', text),
  insert(child, parent, anchor = null) {
    child.parent = parent
    const index = anchor ? parent.children.indexOf(anchor) : -1
    if (index < 0) parent.children.push(child)
    else parent.children.splice(index, 0, child)
  },
  remove(child) {
    const index = child.parent?.children.indexOf(child) ?? -1
    if (index >= 0) child.parent.children.splice(index, 1)
  },
  setText(target, text) {
    target.text = text
  },
  setElementText(target, text) {
    target.text = text
    target.children = []
  },
  parentNode: (target) => target.parent,
  nextSibling(target) {
    const siblings = target.parent?.children || []
    return siblings[siblings.indexOf(target) + 1] || null
  },
  patchProp(target, key, _old, value) {
    target.props[key] = value
  }
})

function findAll(target, predicate) {
  return [
    ...(predicate(target) ? [target] : []),
    ...(target.children || []).flatMap((child) => findAll(child, predicate))
  ]
}

function visibleText(target) {
  return [target.text, ...(target.children || []).map(visibleText)].join(' ')
}

test('委托编辑保留全局和部门授权，创建人只能通过显式重设替换授权', async () => {
  const componentDir = new URL('../../src/components/model-management/', import.meta.url)
  const compiledPath = fileURLToPath(new URL(`.agent-permission-test-${pid}.mjs`, componentDir))
  const previousStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: { getItem: () => null, setItem() {}, removeItem() {} }
  })
  // 仅扩展测试可观察状态，使用组件原模板渲染并点击真实按钮。
  const { descriptor } = parse(
    readFileSync(new URL('AgentEditModal.vue', componentDir), 'utf8').replace(
      'defineExpose({',
      'defineExpose({ buildAgentPayload, agentForm, hasShareChanges, agentShareConfig,' +
        'canEditAgentShareConfig, editingAgentCanManage,'
    )
  )
  writeFileSync(
    compiledPath,
    compileScript(descriptor, { id: 'agent-permission-test', inlineTemplate: true }).content
  )
  const server = await createServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom',
    optimizeDeps: { noDiscovery: true }
  })
  const pinia = createPinia()
  setActivePinia(pinia)
  const previousDomTypes = new Map(
    ['Document', 'ShadowRoot'].map((name) => [
      name,
      Object.getOwnPropertyDescriptor(globalThis, name)
    ])
  )
  let app
  try {
    const { default: component } = await server.ssrLoadModule(
      `/src/components/model-management/.agent-permission-test-${pid}.mjs`
    )
    const { useAgentStore } = await server.ssrLoadModule('/src/stores/agent.js')
    const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
    for (const [path, tag] of [
      ['/src/components/ShareConfigForm.vue', 'share-form'],
      ['/src/components/AgentRuntimeConfigForm.vue', 'runtime-form'],
      ['/src/components/common/FallbackAvatar.vue', 'avatar']
    ]) {
      const { default: child } = await server.ssrLoadModule(path)
      child.setup = () => () => h(tag)
    }
    const agentStore = useAgentStore()
    const userStore = useUserStore()
    userStore.uid = 'editor'
    userStore.userRole = 'user'
    agentStore.selectAgent = async () => {}
    let state
    const setup = component.setup
    component.setup = (props, context) =>
      setup(props, {
        ...context,
        expose(exposed) {
          state = exposed
          context.expose(exposed)
        }
      })
    for (const name of previousDomTypes.keys()) {
      Object.defineProperty(globalThis, name, { configurable: true, value: class {} })
    }
    const host = node('root')
    app = renderer.createApp(component)
    const renderErrors = []
    app.config.errorHandler = (error) => renderErrors.push(error)
    app.use(pinia)
    app.component('a-modal', {
      props: { open: Boolean },
      setup:
        (props, { slots }) =>
        () =>
          props.open ? h('dialog', {}, [slots.title?.(), slots.default?.()]) : null
    })
    for (const name of ['a-button', 'a-upload', 'a-textarea', 'a-select']) {
      app.component(name, {
        setup:
          (_props, { attrs, slots }) =>
          () =>
            h(name, attrs, slots.default?.())
      })
    }
    app.mount(host)
    const resetButtons = () =>
      findAll(
        host,
        (item) => item.type === 'a-button' && visibleText(item).includes('重新设置共享权限')
      )

    for (const scope of [
      { access_level: 'global' },
      { access_level: 'department', department_ids: [7] }
    ]) {
      const agent = {
        id: 'shared-agent',
        name: '原智能体',
        can_edit: true,
        can_manage: false,
        share_config: { version: 2, read_scope: scope, edit_scope: null, manage_scope: scope }
      }
      agentStore.fetchAgentDetail = async () => agent
      await state.openEdit(agent)
      await nextTick()
      assert.equal(state.canEditAgentShareConfig.value, false)
      assert.equal(state.editingAgentCanManage.value, false)
      assert.equal(resetButtons().length, 0)
      assert.equal(findAll(host, (item) => item.type === 'share-form').length, 0)
      assert.match(visibleText(host), /当前可编辑配置/)
      assert.match(visibleText(host), /原管理范围/)
      assert.match(visibleText(host), /原管理授权对普通用户仅授予配置编辑权限/)
      assert.equal(state.agentShareConfig.value.read_scope.access_level, scope.access_level)
      assert.equal(state.hasShareChanges.value, false)
      state.agentForm.name = '编辑后的名称'
      assert.equal(Object.hasOwn(state.buildAgentPayload(), 'share_config'), false)

      agent.can_manage = true
      await state.openEdit(agent)
      await nextTick()
      assert.equal(state.canEditAgentShareConfig.value, false)
      assert.equal(state.editingAgentCanManage.value, true)
      assert.equal(Object.hasOwn(state.buildAgentPayload(), 'share_config'), false)
      assert.equal(resetButtons().length, 1)
      resetButtons()[0].props.onClick()
      await nextTick()
      assert.equal(state.canEditAgentShareConfig.value, true)
      assert.equal(findAll(host, (item) => item.type === 'share-form').length, 1)
      assert.equal(state.hasShareChanges.value, true)
      assert.deepEqual(state.buildAgentPayload().share_config, {
        version: 2,
        read_scope: { access_level: 'user', department_ids: [], user_uids: ['editor'] },
        edit_scope: null,
        manage_scope: null
      })
      await state.openEdit(agent)
      await nextTick()
      assert.equal(state.agentShareConfig.value.read_scope.access_level, scope.access_level)
      assert.equal(state.hasShareChanges.value, false)
    }
    assert.deepEqual(renderErrors, [])
  } finally {
    app?.unmount()
    await server.close()
    unlinkSync(compiledPath)
    if (previousStorage) Object.defineProperty(globalThis, 'localStorage', previousStorage)
    else delete globalThis.localStorage
    for (const [name, descriptor] of previousDomTypes) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor)
      else delete globalThis[name]
    }
  }
})
