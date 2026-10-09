import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import { h, ref } from 'vue'
import { buildProjectConversationGroups } from '../../src/utils/projectConversationGroups.js'

const nav = readFileSync(
  new URL('../../src/components/ConversationNavSection.vue', import.meta.url),
  'utf8'
)

function promotionDialog(promote) {
  let dialog, info
  const events = [],
    notices = []
  const projectsExpanded = ref(false),
    expandedProjects = ref(new Set())
  const actions = runInNewContext(
    `${nav.slice(nav.indexOf('const openChatProject ='), nav.indexOf('const renameProject ='))}\n;({confirmPromoteChat, openChatProject})`,
    {
      h,
      props: { promoteConversation: promote },
      projectsExpanded,
      expandedProjects,
      Modal: {
        confirm: (value) => {
          dialog = value
        },
        info: (value) => {
          info = value
        }
      },
      emit: (...args) => events.push(args),
      message: {
        success: (text) => notices.push(text),
        error: (text) => notices.push(text),
        warning: (text) => notices.push(text)
      }
    }
  )
  return {
    ...actions,
    get dialog() {
      return dialog
    },
    get info() {
      return info
    },
    events,
    notices,
    projectsExpanded,
    expandedProjects
  }
}
const chat = {
  id: 'old-thread',
  title: '原对话',
  project_id: 'same-project',
  workdir_path: 'projects/date_12345678',
  can_delete_workdir: true
}

test('确认前及取消不转换，成功后展开同一项目并定位原对话', async () => {
  const calls = []
  const state = promotionDialog(async (...args) => {
    calls.push(args)
    return { id: chat.project_id }
  })
  state.confirmPromoteChat(chat)
  assert.equal(calls.length, 0)
  assert.match(state.dialog.content.children[1].children, /projects\/date_12345678/)
  state.dialog.content.children[0].props.onInput({ target: { value: ' 新名称 ' } })
  await state.dialog.onOk()
  assert.deepEqual(calls, [['old-thread', '新名称']])
  assert.equal(state.projectsExpanded.value, true)
  assert.equal(state.expandedProjects.value.has('same-project'), true)
  assert.deepEqual(state.events, [['select-chat', 'old-thread']])
})

test('转换失败保持弹窗可重试、未打开项目；非独立历史目录只提示不调用', async () => {
  let calls = 0
  const state = promotionDialog(async () => {
    calls++
    if (calls === 1) throw new Error('任务未结束')
    return { id: chat.project_id }
  })
  state.confirmPromoteChat({ ...chat, can_delete_workdir: false })
  assert.match(state.info.content, /原对话和文件保持原样/)
  assert.equal(calls, 0)
  state.confirmPromoteChat(chat)
  await assert.rejects(state.dialog.onOk(), /任务未结束/)
  assert.equal(state.events.length, 0)
  assert.equal(state.projectsExpanded.value, false)
  await state.dialog.onOk()
  assert.equal(state.events.length, 1)
})

test('空名称拒绝请求；已有项目打开动作不调用转换', async () => {
  let calls = 0
  const state = promotionDialog(async () => {
    calls++
  })
  state.confirmPromoteChat(chat)
  state.dialog.content.children[0].props.onInput({ target: { value: ' ' } })
  await assert.rejects(state.dialog.onOk(), /不能为空/)
  state.openChatProject(chat)
  assert.equal(calls, 0)
  assert.deepEqual(state.events, [['select-chat', 'old-thread']])
})

test('服务成功才更新侧栏分组，同时撤销所有同项目会话的文件清理能力', async () => {
  const source = readFileSync(new URL('../../src/layouts/AppLayout.vue', import.meta.url), 'utf8')
  const threads = ref([
    chat,
    { ...chat, id: 'child' },
    { ...chat, id: 'other', project_id: 'another' }
  ])
  const projects = []
  let fail = true
  const userStore = { uid: 'owner' }
  const project = {
    id: chat.project_id,
    selection_status: 'selectable',
    name: '项目',
    status: 'active'
  }
  const action = runInNewContext(
    `${source.slice(source.indexOf('const handlePromoteConversation ='), source.indexOf('const handleRenameProject ='))}\n;handlePromoteConversation`,
    {
      threads,
      userStore,
      projectApi: {
        promoteConversation: async () => {
          if (fail) throw new Error('失败')
          return project
        }
      },
      projectsStore: { upsertProject: (value) => projects.push(value) },
      chatThreadsStore: {
        upsertThread: (value) => {
          threads.value[threads.value.findIndex((item) => item.id === value.id)] = value
        }
      }
    }
  )
  await assert.rejects(action(chat.id, '项目'))
  assert.equal(projects.length, 0)
  assert.equal(threads.value[0].can_delete_workdir, true)
  fail = false
  const switched = action(chat.id, '项目')
  userStore.uid = 'other'
  await assert.rejects(switched, /登录用户已变化/)
  assert.equal(projects.length, 0)
  userStore.uid = 'owner'
  await action(chat.id, '项目')
  const grouping = buildProjectConversationGroups(projects, threads.value)
  assert.equal(grouping.groups[0].conversations.length, 2)
  assert.equal(grouping.otherConversations.length, 1)
  assert.equal(threads.value[0].can_delete_workdir, false)
  assert.equal(threads.value[1].can_delete_workdir, false)
  assert.equal(threads.value[2].can_delete_workdir, true)
  assert.equal(threads.value[0].workdir_path, chat.workdir_path)
})
