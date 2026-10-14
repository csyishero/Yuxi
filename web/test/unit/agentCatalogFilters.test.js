import assert from 'node:assert/strict'
import { readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { pid } from 'node:process'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { createPinia, setActivePinia } from 'pinia'
import { createRenderer, h, nextTick } from 'vue'
import { compileScript, parse } from 'vue/compiler-sfc'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createServer } from 'vite'

/** 建立可检查的轻量渲染节点。 */
function node(type, text = '') {
  return { type, text, props: {}, children: [], parent: null, style: {}, addEventListener() {} }
}

const renderer = createRenderer({
  createElement: (type) => node(type),
  createText: (text) => node('text', text),
  createComment: (text) => node('comment', text),
  insert(child, parent, anchor = null) {
    if (child.parent) {
      const siblings = child.parent.children
      const oldIndex = siblings.indexOf(child)
      if (oldIndex >= 0) siblings.splice(oldIndex, 1)
    }
    child.parent = parent
    const index = anchor ? parent.children.indexOf(anchor) : -1
    if (index < 0) parent.children.push(child)
    else parent.children.splice(index, 0, child)
  },
  remove(child) {
    const index = child.parent?.children.indexOf(child) ?? -1
    if (index >= 0) child.parent.children.splice(index, 1)
    child.parent = null
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

/** 在渲染树中查找符合条件的节点。 */
function findAll(target, predicate) {
  return [
    ...(predicate(target) ? [target] : []),
    ...(target.children || []).flatMap((child) => findAll(child, predicate))
  ]
}

/** 收集节点及子节点的可见文字。 */
function visibleText(target) {
  return [target.text, ...(target.children || []).map(visibleText)].join(' ')
}

/** 判断节点是否带有指定 CSS 类。 */
function hasClass(target, name) {
  return String(target.props.class || '')
    .split(/\s+/)
    .includes(name)
}

test('智能体目录搜索创建人与部门，并组合管理员筛选条件', async () => {
  const componentDir = new URL('../../src/components/model-management/', import.meta.url)
  const compiledPath = fileURLToPath(new URL(`.agent-catalog-filter-test-${pid}.mjs`, componentDir))
  const previousStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: { getItem: () => null, setItem() {}, removeItem() {} }
  })
  const { descriptor } = parse(readFileSync(new URL('AgentManagePanel.vue', componentDir), 'utf8'))
  writeFileSync(
    compiledPath,
    compileScript(descriptor, { id: 'agent-catalog-filter-test', inlineTemplate: true }).content
  )
  const server = await createServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom',
    optimizeDeps: { noDiscovery: true }
  })
  const pinia = createPinia()
  setActivePinia(pinia)
  let app
  try {
    const { agentApi } = await server.ssrLoadModule('/src/apis/agent_api.js')
    const agents = [
      {
        id: 'default-chatbot',
        name: '内置聊天',
        is_builtin: true,
        owner_is_system: true,
        owner_name: '系统',
        created_by: 'system',
        share_config: { access_level: 'global' }
      },
      {
        id: 'sales-agent',
        name: '周报助手',
        description: '客户周报',
        backend_id: 'sales-backend',
        owner_name: '张敏',
        created_by: 'zhangmin',
        owner_department_id: 7,
        owner_department_name: '市场部',
        share_config: { access_level: 'global' }
      },
      {
        id: 'dev-agent',
        name: '代码助手',
        description: '代码审查',
        owner_name: '李雷',
        created_by: 'lilei',
        owner_department_id: 9,
        owner_department_name: '研发部',
        share_config: {
          version: 2,
          read_scope: { access_level: 'department', department_ids: [11, 12, 13] },
          edit_scope: null,
          manage_scope: { access_level: 'department', department_ids: [11, 13] }
        },
        read_department_names: ['质量部', '平台部', '运营部'],
        edit_department_names: [],
        manage_department_names: ['质量部', '运营部']
      },
      {
        id: 'legacy-agent',
        name: '归档助手',
        created_by: 'legacy.account',
        share_config: {
          version: 2,
          read_scope: { access_level: 'user', user_uids: ['legacy.account'] },
          edit_scope: null,
          manage_scope: null
        }
      },
      {
        id: 'empty-user-scope-agent',
        name: '独享助手',
        created_by: 'sole-owner',
        share_config: {
          version: 2,
          read_scope: { access_level: 'user', user_uids: [] }
        }
      },
      {
        id: 'named-system-agent',
        name: '系统同名助手',
        owner_name: '系统',
        owner_is_system: false,
        created_by: 'real-system',
        share_config: {
          version: 2,
          read_scope: { access_level: 'user', user_uids: ['real-system'] }
        }
      },
      {
        id: 'research-explorer',
        name: '系统调研子智能体',
        is_subagent: true,
        owner_is_system: true,
        created_by: 'system',
        share_config: { access_level: 'global' }
      },
      {
        id: 'sales-subagent',
        name: '周报整理子智能体',
        is_subagent: true,
        owner_name: '张敏',
        created_by: 'zhangmin',
        owner_department_id: 7,
        owner_department_name: '市场部',
        share_config: { access_level: 'global' }
      }
    ]
    agentApi.getAgents = async () => ({ agents })
    agentApi.getAgentBackends = async () => ({ backends: [] })
    const { departmentApi } = await server.ssrLoadModule('/src/apis/department_api.js')
    let directoryDepartments = [
      { id: 7, name: '市场部' },
      { id: 9, name: '研发部' },
      { id: 11, name: '科技部门' }
    ]
    let departmentRequests = 0
    let nextDepartmentResponse = null
    departmentApi.getDepartments = async () => {
      departmentRequests += 1
      if (nextDepartmentResponse) {
        const response = nextDepartmentResponse
        nextDepartmentResponse = null
        return response
      }
      return directoryDepartments
    }
    const { default: pageShoulder } = await server.ssrLoadModule(
      '/src/components/shared/PageShoulder.vue'
    )
    pageShoulder.setup =
      (props, { emit, slots }) =>
      () =>
        h('page-shoulder', {}, [
          h('test-a-input', {
            value: props.search,
            placeholder: props.searchPlaceholder,
            'onUpdate:value': (value) => emit('update:search', value)
          }),
          slots.actions?.()
        ])
    const { default: cardGrid } = await server.ssrLoadModule(
      '/src/components/extensions/ExtensionCardGrid.vue'
    )
    cardGrid.setup =
      (_props, { slots }) =>
      () =>
        h('grid', {}, slots.default?.())
    const { default: infoCard } = await server.ssrLoadModule('/src/components/shared/InfoCard.vue')
    infoCard.setup =
      (props, { slots }) =>
      () =>
        h('article', { class: 'info-card' }, [
          h('span', { class: 'info-card-name' }, props.title),
          ...props.tags.map((tag) => h('span', {}, tag.name)),
          slots['tag-actions']?.()
        ])
    const { default: editModal } = await server.ssrLoadModule(
      '/src/components/model-management/AgentEditModal.vue'
    )
    editModal.setup = () => () => h('edit-modal')
    const { default: AgentManagePanel } = await server.ssrLoadModule(
      `/src/components/model-management/.agent-catalog-filter-test-${pid}.mjs`
    )
    const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
    const userStore = useUserStore()
    userStore.userRole = 'admin'

    const host = node('root')
    app = renderer.createApp(AgentManagePanel)
    const renderErrors = []
    app.config.errorHandler = (error) => renderErrors.push(error)
    app.use(pinia)
    app.use(createRouter({ history: createMemoryHistory(), routes: [] }))
    for (const name of ['a-input', 'a-select', 'a-button', 'a-menu', 'a-menu-item']) {
      app.component(name, {
        inheritAttrs: false,
        setup:
          (_props, { attrs, slots }) =>
          () =>
            h(`test-${name}`, attrs, slots.default?.())
      })
    }
    const panel = app.mount(host)
    await panel.refresh()
    await nextTick()

    const cardTitles = () =>
      findAll(host, (item) => hasClass(item, 'info-card-name')).map((item) =>
        visibleText(item).trim()
      )
    const control = (type, placeholder) =>
      findAll(host, (item) => item.type === type && item.props.placeholder === placeholder)[0]
    const search = () => control('test-a-input', '搜索智能体、创建人或部门...')
    const owner = () => control('test-a-input', '搜索创建人姓名或账号')
    const department = () => control('test-a-select', '全部创建人部门')
    const sectionTitleFor = (title) => {
      const section = findAll(
        host,
        (item) => hasClass(item, 'agent-group-section') && visibleText(item).includes(title)
      )[0]
      return visibleText(findAll(section, (item) => hasClass(item, 'agent-group-header'))[0]).trim()
    }
    const kindSectionFor = (title) =>
      findAll(
        host,
        (item) => hasClass(item, 'agent-kind-section') && visibleText(item).includes(title)
      )[0]
    const kindTitleFor = (title) =>
      visibleText(
        findAll(kindSectionFor(title), (item) => hasClass(item, 'agent-kind-header'))[0]
      ).trim()

    assert.deepEqual(
      new Set(cardTitles()),
      new Set([
        '内置聊天',
        '周报助手',
        '代码助手',
        '归档助手',
        '独享助手',
        '系统同名助手',
        '系统调研子智能体',
        '周报整理子智能体'
      ])
    )
    assert.equal(kindTitleFor('内置聊天'), '智能体 6 个')
    assert.equal(kindTitleFor('系统调研子智能体'), '子智能体 2 个')
    assert.equal(kindTitleFor('周报助手'), '智能体 6 个')
    assert.equal(kindTitleFor('周报整理子智能体'), '子智能体 2 个')
    assert.doesNotMatch(visibleText(kindSectionFor('内置聊天')), /周报整理子智能体/)
    assert.equal(sectionTitleFor('内置聊天'), '系统预置')
    assert.equal(sectionTitleFor('系统调研子智能体'), '系统预置')
    assert.equal(sectionTitleFor('周报整理子智能体'), '全局共享')
    assert.equal(sectionTitleFor('归档助手'), '个人')
    assert.equal(sectionTitleFor('独享助手'), '个人')
    assert.equal(sectionTitleFor('系统同名助手'), '个人')
    assert.equal(
      findAll(kindSectionFor('系统调研子智能体'), (item) => hasClass(item, 'agent-chat-entry'))
        .length,
      0
    )
    assert.equal(
      findAll(kindSectionFor('内置聊天'), (item) => hasClass(item, 'agent-chat-entry')).length,
      6
    )
    assert.equal(panel.stats.total, 6)
    assert.equal(panel.stats.subagents, 2)
    assert.deepEqual(
      department()
        .props.options.map(({ value, label }) => ({ value, label }))
        .sort((a, b) => a.value.localeCompare(b.value)),
      [
        { value: '11', label: '科技部门' },
        { value: '7', label: '市场部' },
        { value: '9', label: '研发部' }
      ]
    )
    department().props['onUpdate:value']('11')
    await nextTick()
    assert.deepEqual(cardTitles(), [])
    assert.match(visibleText(host), /没有匹配的智能体或子智能体/)
    department().props['onUpdate:value'](undefined)
    await nextTick()
    assert.equal(cardTitles().length, agents.length)

    department().props['onUpdate:value']('11')
    directoryDepartments = directoryDepartments.filter((item) => item.id !== 11)
    await panel.refresh()
    await nextTick()
    assert.equal(department().props.value, undefined)
    assert.equal(cardTitles().length, agents.length)
    assert.deepEqual(
      department()
        .props.options.map(({ value }) => value)
        .sort(),
      ['7', '9']
    )
    let resolveOlderDirectory
    nextDepartmentResponse = new Promise((resolve) => {
      resolveOlderDirectory = resolve
    })
    const olderRefresh = panel.refresh()
    await panel.refresh()
    resolveOlderDirectory([
      { id: 7, name: '市场部' },
      { id: 9, name: '研发部' },
      { id: 11, name: '科技部门' }
    ])
    await olderRefresh
    await nextTick()
    assert.deepEqual(
      department()
        .props.options.map(({ value }) => value)
        .sort(),
      ['7', '9']
    )
    const cards = findAll(host, (item) => hasClass(item, 'info-card'))
    assert.match(
      visibleText(cards.find((card) => visibleText(card).includes('周报助手'))),
      /创建人：张敏/
    )
    assert.match(
      visibleText(cards.find((card) => visibleText(card).includes('周报助手'))),
      /创建人部门：市场部/
    )
    const devCardText = visibleText(cards.find((card) => visibleText(card).includes('代码助手')))
    assert.match(devCardText, /读取：质量部、平台部、运营部/)
    assert.match(devCardText, /管理：质量部、运营部/)
    assert.match(devCardText, /创建人部门：研发部/)
    assert.doesNotMatch(devCardText, /部门\(2\)/)
    assert.match(
      visibleText(cards.find((card) => visibleText(card).includes('归档助手'))),
      /创建人：legacy\.account/
    )
    assert.match(
      visibleText(cards.find((card) => visibleText(card).includes('归档助手'))),
      /创建人部门：部门未知/
    )
    assert.match(
      visibleText(cards.find((card) => visibleText(card).includes('归档助手'))),
      /仅创建人/
    )
    const systemNamedCard = cards.find((card) => visibleText(card).includes('系统同名助手'))
    assert.match(visibleText(systemNamedCard), /创建人：系统/)
    assert.match(visibleText(systemNamedCard), /创建人部门：未分配部门/)
    assert.doesNotMatch(visibleText(systemNamedCard), /系统预置/)
    assert.match(
      visibleText(cards.find((card) => visibleText(card).includes('内置聊天'))),
      /系统预置/
    )

    search().props['onUpdate:value']('  市场部  ')
    await nextTick()
    assert.deepEqual(cardTitles(), ['周报助手', '周报整理子智能体'])
    assert.equal(kindTitleFor('周报整理子智能体'), '子智能体 1 个')
    search().props['onUpdate:value'](' SALES-BACKEND ')
    await nextTick()
    assert.deepEqual(cardTitles(), ['周报助手'])
    search().props['onUpdate:value']('运营部')
    await nextTick()
    assert.deepEqual(cardTitles(), ['代码助手'])
    search().props['onUpdate:value']('')
    owner().props['onUpdate:value'](' 张敏 ')
    await nextTick()
    assert.deepEqual(cardTitles(), ['周报助手', '周报整理子智能体'])
    owner().props['onUpdate:value']('legacy.account')
    await nextTick()
    assert.deepEqual(cardTitles(), ['归档助手'])

    owner().props['onUpdate:value']('')
    department().props['onUpdate:value']('9')
    await nextTick()
    assert.deepEqual(cardTitles(), ['代码助手'])
    owner().props['onUpdate:value']('张敏')
    await nextTick()
    assert.deepEqual(cardTitles(), [])
    assert.match(visibleText(host), /没有匹配的智能体/)

    const requestsBeforeRoleChange = departmentRequests
    userStore.userRole = 'user'
    await nextTick()
    assert.equal(owner(), undefined)
    assert.equal(department(), undefined)
    assert.equal(departmentRequests, requestsBeforeRoleChange)
    assert.equal(cardTitles().length, agents.length)
    await panel.refresh()
    assert.equal(departmentRequests, requestsBeforeRoleChange)
    search().props['onUpdate:value']('张敏')
    await nextTick()
    assert.deepEqual(cardTitles(), ['周报助手', '周报整理子智能体'])
    assert.deepEqual(renderErrors, [])
  } finally {
    app?.unmount()
    await server.close()
    unlinkSync(compiledPath)
    if (previousStorage) Object.defineProperty(globalThis, 'localStorage', previousStorage)
    else delete globalThis.localStorage
  }
})
