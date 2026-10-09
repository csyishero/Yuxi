import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import { createSSRApp, h } from 'vue'
import { parse } from 'vue/compiler-sfc'
import { renderToString } from 'vue/server-renderer'

const source = readFileSync(
  new URL('../../src/components/ConversationNavItem.vue', import.meta.url), 'utf8'
)
const script = parse(source).descriptor.scriptSetup.content
const start = script.indexOf('const confirmDeleteChat =')
const end = script.indexOf('\n}', start) + 2

function openDialog(canDeleteWorkdir) {
  let config
  let emitted
  runInNewContext(`${script.slice(start, end)}\nconfirmDeleteChat()`, {
    h,
    props: { chat: {
      id: 'thread-1', title: '独立会话', can_delete_workdir: canDeleteWorkdir,
      workdir_path: 'projects/conversation_12345678'
    } },
    Modal: { confirm: (value) => { config = value } },
    emit: (event, payload) => { emitted = { event, payload } }
  })
  const content = config.content()
  const checkbox = content.children.find((node) => node.type === 'label')?.children[0]
  return { config, content, checkbox, emitted: () => emitted }
}

test('无项目会话默认保留文件，只有勾选后才要求清理', async () => {
  const dialog = openDialog(true)
  const html = await renderToString(createSSRApp({ render: () => dialog.content }))
  assert.match(html, /同时永久删除会话文件夹/)
  assert.match(html, /projects\/conversation_12345678/)
  assert.doesNotMatch(html, /<input[^>]*checked/)
  assert.equal(dialog.emitted(), undefined)
  dialog.config.onOk()
  assert.equal(dialog.emitted().payload.deleteWorkdir, false)

  const chosen = openDialog(true)
  chosen.checkbox.props.onChange({ target: { checked: true } })
  chosen.config.onOk()
  assert.equal(chosen.emitted().event, 'delete-chat')
  assert.equal(chosen.emitted().payload.threadId, 'thread-1')
  assert.equal(chosen.emitted().payload.deleteWorkdir, true)
})

test('项目内或能力未知的会话只允许保留目录', async () => {
  for (const capability of [false, undefined]) {
    const dialog = openDialog(capability)
    assert.equal(dialog.checkbox, undefined)
    dialog.config.onOk()
    assert.equal(dialog.emitted().payload.deleteWorkdir, false)
  }
})

test('清理响应丢失后读取最终任务状态，完成的任务也能移除会话并继续提示', async () => {
  const layout = parse(readFileSync(
    new URL('../../src/layouts/AppLayout.vue', import.meta.url), 'utf8'
  )).descriptor.scriptSetup.content
  const begin = layout.indexOf('const handleDeleteChat =')
  const finish = layout.indexOf('\n}', begin) + 2
  for (const outcome of ['success', 'missing', 'offline']) {
    const removed = []
    const tracked = []
    const errors = []
    const context = {
      threads: { value: [{ id: 'thread-1', project_id: 'implicit-1' }] },
      chatThreadsStore: {
        deleteThread: async (_id, options) => {
          assert.equal(options.deleteWorkdir, true)
          throw new Error('请求未返回')
        },
        removeThread: (id) => removed.push(id)
      },
      route: { params: { thread_id: 'thread-1' } },
      router: { replace: async () => {} },
      userStore: { uid: 'user-1' },
      projectApi: {
        getWorkdirDeletion: async (id) => {
          assert.equal(id, 'implicit-1')
          if (outcome === 'missing') throw Object.assign(new Error('任务不存在'), { status: 404 })
          if (outcome === 'offline') throw new Error('网络中断')
          return { status: 'success' }
        }
      },
      trackWorkdirDeletion: (uid, id) => tracked.push([uid, id]),
      message: { info: () => {}, error: (value) => errors.push(value) }
    }
    await runInNewContext(
      `${layout.slice(begin, finish)}\nhandleDeleteChat({ threadId: 'thread-1', deleteWorkdir: true })`,
      context
    )
    assert.deepEqual(removed, outcome === 'success' ? ['thread-1'] : [])
    assert.deepEqual(tracked, outcome !== 'missing' ? [['user-1', 'implicit-1']] : [])
    assert.equal(errors.length, outcome === 'missing' ? 1 : 0)
  }
})

test('断网时保留待确认目录，恢复后查到任务才移除会话并展示最终结果', async () => {
  const layout = parse(readFileSync(
    new URL('../../src/layouts/AppLayout.vue', import.meta.url), 'utf8'
  )).descriptor.scriptSetup.content
  const begin = layout.indexOf('const trackWorkdirDeletion =')
  const finish = layout.indexOf('\n}', begin) + 2
  const scheduled = []
  const saved = []
  const removed = []
  const finished = []
  let connected = false
  const context = {
    workdirDeletionTimers: new Map(),
    workdirDeletionEpoch: 1,
    userStore: { uid: 'user-1' },
    pendingWorkdirDeletions: () => [],
    savePendingWorkdirDeletions: (_uid, ids) => saved.push(...ids),
    stopTrackingWorkdirDeletion: (_uid, id) => finished.push(id),
    removeDeletedProjectFromView: async (id) => removed.push(id),
    projectApi: { getWorkdirDeletion: async () => {
      if (!connected) throw new Error('网络中断')
      return { status: 'success' }
    } },
    setTimeout: (fn) => { scheduled.push(fn); return scheduled.length },
    message: { success: () => {} }
  }
  runInNewContext(`${layout.slice(begin, finish)}\ntrackWorkdirDeletion('user-1', 'implicit-1')`, context)
  assert.deepEqual(saved, ['implicit-1'])
  await scheduled.shift()()
  assert.deepEqual(removed, [])
  assert.deepEqual(finished, [])
  connected = true
  await scheduled.shift()()
  assert.deepEqual(removed, ['implicit-1'])
  assert.deepEqual(finished, ['implicit-1'])
})
