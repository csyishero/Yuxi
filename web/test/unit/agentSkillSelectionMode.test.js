import assert from 'node:assert/strict'
import { readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { pid } from 'node:process'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { createPinia, setActivePinia } from 'pinia'
import { createRenderer, h, nextTick, ssrContextKey } from 'vue'
import { compileScript, parse } from 'vue/compiler-sfc'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createServer } from 'vite'

const componentPath = fileURLToPath(
  new URL('../../src/components/AgentRuntimeConfigForm.vue', import.meta.url)
)
const compiledPath = fileURLToPath(
  new URL(`../../src/components/.agent-skill-selection-test-${pid}.mjs`, import.meta.url)
)

function node(type, text = '') {
  return { type, text, props: {}, children: [], parent: null }
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

function findAll(target, predicate, result = []) {
  if (predicate(target)) result.push(target)
  for (const child of target.children || []) findAll(child, predicate, result)
  return result
}

function visibleText(target) {
  return [target.text, ...(target.children || []).map(visibleText)].join(' ')
}

const Slot = {
  setup(_props, { slots, attrs }) {
    return () => h('slot-shell', attrs, slots.default?.())
  }
}
const Button = {
  setup(_props, { slots, attrs }) {
    return () => h('button', attrs, slots.default?.())
  }
}
const Modal = {
  props: { open: Boolean },
  setup(props, { slots }) {
    return () => (props.open ? h('dialog', {}, slots.default?.()) : null)
  }
}

test('Skill 三态切换保持原配置契约，固定模式只保存明确勾选的同名项', async () => {
  const { descriptor } = parse(readFileSync(componentPath, 'utf8'))
  writeFileSync(
    compiledPath,
    compileScript(descriptor, { id: 'agent-skill-test', inlineTemplate: true }).content
  )
  const server = await createServer({
    configFile: false,
    plugins: [vue()],
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, hmr: false },
    appType: 'custom'
  })
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({ history: createMemoryHistory(), routes: [] })
  try {
    const { default: AgentRuntimeConfigForm } = await server.ssrLoadModule(
      `/src/components/.agent-skill-selection-test-${pid}.mjs`
    )
    const { useAgentStore } = await server.ssrLoadModule('/src/stores/agent.js')
    const store = useAgentStore()
    const agent = {
      id: 'test-agent',
      can_manage: true,
      config_json: { context: { skills: null } },
      configurable_items: {
        skills: {
          name: 'Skills',
          type: 'list',
          kind: 'skills',
          options: [
            { key: 'ppt-builder', name: 'ppt-builder', source_scope: 'shared' },
            { key: 'ppt-builder-team', name: 'ppt-builder', source_scope: 'personal' }
          ]
        }
      }
    }
    store.agentDetails[agent.id] = agent
    await store.selectAgent(agent.id)

    const host = node('root')
    const app = renderer.createApp({
      render: () => h(AgentRuntimeConfigForm, { segment: 'tools', showSegmented: false })
    })
    app.use(pinia)
    app.use(router)
    app.provide(ssrContextKey, { modules: new Set() })
    app.component('a-form', Slot)
    app.component('a-form-item', Slot)
    app.component('a-button', Button)
    app.component('a-modal', Modal)
    for (const name of [
      'a-segmented',
      'a-alert',
      'a-empty',
      'a-switch',
      'a-select-option',
      'a-select',
      'a-divider',
      'a-tag',
      'a-input-number',
      'a-slider',
      'a-input',
      'a-textarea'
    ]) {
      app.component(name, Slot)
    }
    app.mount(host)

    const modeButton = (mode) =>
      findAll(
        host,
        (item) =>
          item.type === 'button' &&
          item.props.class?.includes('skill-mode-option') &&
          visibleText(item).includes(mode)
      )[0]
    assert.match(visibleText(host), /本账号当前可用\s+2\s+项/)
    assert.doesNotMatch(visibleText(host), /已选择 2 项/)

    modeButton('固定选择').props.onClick()
    await nextTick()
    assert.equal(store.agentConfig.skills, null)
    assert.equal(
      findAll(host, (item) => item.type === 'button' && visibleText(item).trim() === '确认')[0]
        .props.disabled,
      true
    )
    findAll(
      host,
      (item) => item.type === 'button' && visibleText(item).trim() === '取消'
    )[0].props.onClick()
    await nextTick()
    assert.equal(store.agentConfig.skills, null)

    modeButton('固定选择').props.onClick()
    await nextTick()
    const skillRows = findAll(host, (item) =>
      String(item.props.class || '')
        .split(/\s+/)
        .includes('selection-item')
    )
    assert.equal(skillRows.length, 2)
    assert.match(visibleText(skillRows[0]), /标识：ppt-builder/)
    assert.match(visibleText(skillRows[0]), /共享/)
    assert.match(visibleText(skillRows[1]), /标识：ppt-builder-team/)
    assert.match(visibleText(skillRows[1]), /个人/)

    skillRows[1].props.onClick()
    await nextTick()
    const confirm = findAll(
      host,
      (item) => item.type === 'button' && visibleText(item).trim() === '确认'
    )[0]
    confirm.props.onClick()
    await nextTick()
    assert.deepEqual(store.agentConfig.skills, ['ppt-builder-team'])
    assert.match(visibleText(host), /固定选择 1 项 Skill/)

    modeButton('不启用').props.onClick()
    await nextTick()
    assert.deepEqual(store.agentConfig.skills, [])
    modeButton('动态使用全部').props.onClick()
    await nextTick()
    assert.equal(store.agentConfig.skills, null)

    app.unmount()
  } finally {
    await server.close()
    unlinkSync(compiledPath)
  }
})
