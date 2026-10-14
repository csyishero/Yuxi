import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import { computed, ref, watch } from 'vue'
import { filterProjects } from '../../src/utils/projectSelection.js'
import { buildProjectConversationGroups } from '../../src/utils/projectConversationGroups.js'

const modalSource = readFileSync(
  new URL('../../src/components/MoveConversationModal.vue', import.meta.url),
  'utf8'
)
const modalScript = modalSource.slice(
  modalSource.indexOf('const MAX_FILES'),
  modalSource.indexOf('</script>')
)

function moveModal({
  moveConversation = async () => ({}),
  getTree = async () => ({ entries: [] })
} = {}) {
  const events = []
  const props = {
    open: true,
    chat: { id: 'old-thread', title: '旧会话', project_id: 'source' },
    projects: [
      { id: 'target', name: '目标项目', status: 'active', selection_status: 'selectable' }
    ],
    projectsLoading: false,
    projectsError: '',
    moveConversation
  }
  const state = runInNewContext(
    `${modalScript}\n;({loadDirectory, toggleFile, toggleCurrentDirectory, filesForRequest, submitMove, selectedFiles, moveRequestId, targetProjectId, currentDirectory, entries, filesLoading, filesError, conflicts, resolutions, formError, submitting, allCurrentFilesSelected, someCurrentFilesSelected})`,
    {
      computed,
      ref,
      watch,
      filterProjects,
      defineProps: () => props,
      defineEmits:
        () =>
        (...args) =>
          events.push(args),
      getViewerFileSystemTree: getTree,
      Array,
      Number,
      Set
    }
  )
  return { ...state, events, props }
}

const file = (path, size = 12) => ({ path, name: path.split('/').pop(), is_dir: false, size })

test('文件夹按需读取，逐个选择的文件跨目录保留；超限文件不加入', async () => {
  const calls = []
  const state = moveModal({
    getTree: async (_, path) => {
      calls.push(path)
      return {
        entries:
          path === '/' ? [{ path: '/notes/', name: 'notes', is_dir: true }] : [file('/notes/a.md')]
      }
    }
  })
  await state.loadDirectory('/')
  assert.deepEqual(calls, ['/'])
  await state.loadDirectory('/notes/')
  assert.deepEqual(calls, ['/', '/notes/'])
  state.toggleFile(state.entries.value[0], true)
  await state.loadDirectory('/')
  assert.deepEqual(
    Array.from(state.selectedFiles.value, (item) => item.path),
    ['/notes/a.md']
  )
  state.toggleFile(file('/too-large.bin', 50 * 1024 * 1024 + 1), true)
  assert.equal(state.selectedFiles.value.length, 1)
  assert.match(state.formError.value, /50 MB/)
})

test('全选只选择当前文件夹，取消后保留其他目录的选择并清理本层冲突', async () => {
  const state = moveModal({
    getTree: async (_, path) => ({
      entries:
        path === '/'
          ? [file('/a.md'), file('/b.md'), { path: '/notes/', name: 'notes', is_dir: true }]
          : [file('/notes/c.md')]
    })
  })
  await state.loadDirectory('/notes/')
  state.toggleFile(state.entries.value[0], true)
  await state.loadDirectory('/')
  state.toggleCurrentDirectory(true)
  assert.deepEqual(
    Array.from(state.selectedFiles.value, (item) => item.path),
    ['/notes/c.md', '/a.md', '/b.md']
  )
  assert.equal(state.allCurrentFilesSelected.value, true)

  state.conflicts.value = [
    { source_path: '/a.md', destination_path: '/a.md', reason: 'already_exists' }
  ]
  state.resolutions.value = { '/a.md': { action: 'rename', name: 'a-copy.md' } }
  state.toggleCurrentDirectory(false)
  assert.deepEqual(
    Array.from(state.selectedFiles.value, (item) => item.path),
    ['/notes/c.md']
  )
  assert.equal(state.allCurrentFilesSelected.value, false)
  assert.equal(state.someCurrentFilesSelected.value, false)
  assert.equal(state.conflicts.value.length, 0)
  assert.equal(Object.keys(state.resolutions.value).length, 0)
})

test('全选超出数量或大小限制时保持原选择，不会只选入部分文件', async () => {
  const state = moveModal()
  state.entries.value = [file('/a.md'), file('/b.md')]
  state.selectedFiles.value = Array.from({ length: 99 }, (_, index) => file(`/other/${index}.md`))
  state.toggleCurrentDirectory(true)
  assert.equal(state.selectedFiles.value.length, 99)
  assert.match(state.formError.value, /100 个/)

  state.selectedFiles.value = []
  state.entries.value = [file('/too-large.bin', 50 * 1024 * 1024 + 1)]
  state.toggleCurrentDirectory(true)
  assert.equal(state.selectedFiles.value.length, 0)
  assert.match(state.formError.value, /50 MB/)

  state.selectedFiles.value = [file('/other/existing.bin', 90 * 1024 * 1024)]
  state.entries.value = [file('/new.bin', 11 * 1024 * 1024)]
  state.toggleCurrentDirectory(true)
  assert.equal(state.selectedFiles.value.length, 1)
  assert.match(state.formError.value, /100 MB/)
})

test('冲突默认逐项跳过，改名仅改变所选文件的目标位置', async () => {
  const requests = []
  let fail = true
  const state = moveModal({
    moveConversation: async (_, projectId, files, requestId) => {
      requests.push({ projectId, files, requestId })
      if (fail) {
        fail = false
        throw {
          status: 409,
          response: {
            data: {
              detail: {
                code: 'file_conflicts',
                conflicts: [
                  {
                    source_path: '/notes/a.md',
                    destination_path: '/notes/a.md',
                    reason: 'destination_exists'
                  }
                ]
              }
            }
          }
        }
      }
      return { thread: { id: 'old-thread', project_id: 'target' }, imported_files: files }
    }
  })
  state.targetProjectId.value = 'target'
  state.selectedFiles.value = [file('/notes/a.md'), file('/notes/b.md')]
  await state.submitMove()
  assert.equal(state.events.length, 0)
  assert.equal(state.resolutions.value['/notes/a.md'].action, 'skip')
  assert.match(state.formError.value, /文件冲突/)
  state.resolutions.value['/notes/a.md'].action = 'rename'
  state.resolutions.value['/notes/a.md'].name = 'a-new.md'
  await state.submitMove()
  assert.deepEqual(JSON.parse(JSON.stringify(requests[1].files)), [
    { source_path: '/notes/a.md', destination_path: '/notes/a-new.md' },
    { source_path: '/notes/b.md', destination_path: '/notes/b.md' }
  ])
  assert.ok(requests[0].requestId)
  assert.notEqual(requests[0].requestId, requests[1].requestId)
  assert.deepEqual(
    state.events.map(([name]) => name),
    ['moved', 'update:open']
  )
})

test('连续冲突时保留先前跳过的文件，不在第三次请求中重新导入', async () => {
  const requests = []
  const conflictError = (conflicts) => ({
    status: 409,
    response: { data: { detail: { code: 'file_conflicts', conflicts } } }
  })
  const state = moveModal({
    moveConversation: async (_, projectId, files) => {
      requests.push({ projectId, files })
      if (requests.length === 1) {
        throw conflictError([
          { source_path: '/a.md', destination_path: '/a.md', reason: 'already_exists' },
          { source_path: '/b.md', destination_path: '/b.md', reason: 'already_exists' }
        ])
      }
      if (requests.length === 2) {
        throw conflictError([
          { source_path: '/b.md', destination_path: '/c.md', reason: 'already_exists' }
        ])
      }
      return { thread: { id: 'old-thread', project_id: 'target' }, imported_files: files }
    }
  })
  state.targetProjectId.value = 'target'
  state.selectedFiles.value = [file('/a.md'), file('/b.md')]
  await state.submitMove()
  assert.equal(state.resolutions.value['/a.md'].action, 'skip')
  state.resolutions.value['/b.md'] = { action: 'rename', name: 'c.md' }
  await state.submitMove()
  assert.deepEqual(JSON.parse(JSON.stringify(requests[1].files)), [
    { source_path: '/b.md', destination_path: '/c.md' }
  ])
  assert.equal(state.resolutions.value['/a.md'].action, 'skip')
  assert.deepEqual(
    Array.from(state.conflicts.value, (item) => item.source_path),
    ['/a.md', '/b.md']
  )
  state.resolutions.value['/b.md'].name = 'd.md'
  await state.submitMove()
  assert.deepEqual(JSON.parse(JSON.stringify(requests[2].files)), [
    { source_path: '/b.md', destination_path: '/d.md' }
  ])
  assert.deepEqual(
    state.events.map(([name]) => name),
    ['moved', 'update:open']
  )
})

test('相同参数重试保留请求 ID，用户改变目标或所选文件时换新 ID', async () => {
  const requests = []
  const state = moveModal({
    moveConversation: async (_, projectId, files, requestId) => {
      requests.push({ projectId, files, requestId })
      throw new Error('网络暂不可用')
    }
  })
  state.targetProjectId.value = 'target'
  state.selectedFiles.value = [file('/a.md')]
  await state.submitMove()
  await state.submitMove()
  assert.equal(requests[0].requestId, requests[1].requestId)
  state.selectedFiles.value = [file('/b.md')]
  await state.submitMove()
  assert.notEqual(requests[1].requestId, requests[2].requestId)
})

test('请求未确认时不关闭弹窗，也不更新项目分组', async () => {
  let finish
  const state = moveModal({
    moveConversation: () =>
      new Promise((resolve) => {
        finish = resolve
      })
  })
  state.targetProjectId.value = 'target'
  const pending = state.submitMove()
  assert.equal(state.submitting.value, true)
  assert.equal(state.events.length, 0)
  finish({ thread: { id: 'old-thread', project_id: 'target' }, imported_files: [] })
  await pending
  assert.equal(state.submitting.value, false)
  assert.deepEqual(
    state.events.map(([name]) => name),
    ['moved', 'update:open']
  )
})

test('服务端会话事实成功后才重排侧栏，旧文件目录不被前端改写', async () => {
  const source = readFileSync(new URL('../../src/layouts/AppLayout.vue', import.meta.url), 'utf8')
  const oldThread = { id: 'old-thread', project_id: 'source', workdir_path: 'projects/old' }
  const movedThread = { ...oldThread, project_id: 'target', workdir_path: 'projects/target' }
  const threads = ref([oldThread])
  const projects = [
    { id: 'target', name: '目标项目', selection_status: 'selectable', status: 'active' }
  ]
  let fail = true
  const action = runInNewContext(
    `${source.slice(source.indexOf('const handleMoveConversation ='), source.indexOf('const handleRenameProject ='))}\n;handleMoveConversation`,
    {
      userStore: { uid: 'owner' },
      agentApi: { getAgentHistory: async () => ({ thread: movedThread }) },
      route: { params: {} },
      projectApi: {
        moveConversation: async () => {
          if (fail) throw new Error('移动失败')
          return { thread: movedThread, imported_files: [] }
        }
      },
      chatThreadsStore: {
        upsertThread: (thread) => {
          threads.value = [thread]
        },
        applyConversationMove: (thread) => {
          threads.value = [thread]
        },
        setCurrentThreadId: () => {}
      }
    }
  )
  await assert.rejects(action('old-thread', 'target', []), /移动失败/)
  assert.equal(
    buildProjectConversationGroups(projects, threads.value).groups[0].conversations.length,
    0
  )
  fail = false
  await action('old-thread', 'target', [])
  assert.equal(
    buildProjectConversationGroups(projects, threads.value).groups[0].conversations.length,
    1
  )
  assert.equal(oldThread.workdir_path, 'projects/old')
})

test('迁移后保留已加载的第 101 条以后会话，并更新持久子会话归属', async () => {
  const layoutSource = readFileSync(
    new URL('../../src/layouts/AppLayout.vue', import.meta.url),
    'utf8'
  )
  const storeSource = readFileSync(
    new URL('../../src/stores/chatThreads.js', import.meta.url),
    'utf8'
  )
  const loaded = Array.from({ length: 120 }, (_, index) => ({
    id: `thread-${index}`,
    project_id: 'source'
  }))
  loaded.push(
    { id: 'old-thread', project_id: 'source', workdir_path: 'projects/source' },
    {
      id: 'child-thread',
      project_id: 'source',
      workdir_path: 'projects/source',
      updated_at: '2026-10-14T10:00:00Z'
    },
    { id: 'unrelated-thread', project_id: 'source', workdir_path: 'projects/source' }
  )
  const threads = ref(loaded)
  const applyConversationMove = runInNewContext(
    `${storeSource.slice(storeSource.indexOf('  const applyConversationMove ='), storeSource.indexOf('  const setThreadStatus ='))}\n;applyConversationMove`,
    { threads, Set }
  )
  const movedThread = {
    id: 'old-thread',
    project_id: 'target',
    workdir_path: 'projects/target',
    can_delete_workdir: false,
    updated_at: '2026-10-14T10:01:00Z'
  }
  let refreshed = false
  const action = runInNewContext(
    `${layoutSource.slice(layoutSource.indexOf('const handleMoveConversation ='), layoutSource.indexOf('const handleRenameProject ='))}\n;handleMoveConversation`,
    {
      userStore: { uid: 'owner' },
      route: { params: {} },
      projectApi: {
        moveConversation: async () => ({
          thread: movedThread,
          imported_files: [],
          moved_thread_ids: ['old-thread', 'child-thread']
        })
      },
      chatThreadsStore: {
        applyConversationMove,
        loadThreads: async () => {
          refreshed = true
        },
        setCurrentThreadId: () => {}
      }
    }
  )
  await action('old-thread', 'target', [], 'request-1')
  assert.equal(refreshed, false)
  assert.equal(threads.value.length, loaded.length)
  assert.deepEqual(
    threads.value.map((thread) => thread.id),
    loaded.map((thread) => thread.id)
  )
  assert.equal(threads.value.find((thread) => thread.id === 'thread-119').project_id, 'source')
  assert.equal(threads.value.find((thread) => thread.id === 'old-thread').project_id, 'target')
  assert.deepEqual(
    JSON.parse(JSON.stringify(threads.value.find((thread) => thread.id === 'child-thread'))),
    {
      id: 'child-thread',
      project_id: 'target',
      workdir_path: 'projects/target',
      can_delete_workdir: false,
      updated_at: movedThread.updated_at
    }
  )
  assert.equal(
    threads.value.find((thread) => thread.id === 'unrelated-thread').project_id,
    'source'
  )
})

test('已移入目标项目的冲突会重新读取该会话，避免侧栏继续显示旧归属', async () => {
  const source = readFileSync(new URL('../../src/layouts/AppLayout.vue', import.meta.url), 'utf8')
  const threads = ref([{ id: 'old-thread', project_id: 'source' }])
  const action = runInNewContext(
    `${source.slice(source.indexOf('const handleMoveConversation ='), source.indexOf('const handleRenameProject ='))}\n;handleMoveConversation`,
    {
      userStore: { uid: 'owner' },
      route: { params: {} },
      projectApi: {
        moveConversation: async () => {
          throw Object.assign(new Error('会话已属于目标项目'), { status: 409 })
        }
      },
      agentApi: {
        getAgentHistory: async () => ({ thread: { id: 'old-thread', project_id: 'target' } })
      },
      chatThreadsStore: {
        upsertThread: (thread) => {
          threads.value = [thread]
        }
      }
    }
  )
  await assert.rejects(action('old-thread', 'target', [], 'request-1'), /已属于目标项目/)
  assert.equal(threads.value[0].project_id, 'target')
})

test('请求 ID 与既有迁移意图冲突时，也重新读取会话归属', async () => {
  const source = readFileSync(new URL('../../src/layouts/AppLayout.vue', import.meta.url), 'utf8')
  const threads = ref([{ id: 'old-thread', project_id: 'source' }])
  const action = runInNewContext(
    `${source.slice(source.indexOf('const handleMoveConversation ='), source.indexOf('const handleRenameProject ='))}\n;handleMoveConversation`,
    {
      userStore: { uid: 'owner' },
      route: { params: {} },
      projectApi: {
        moveConversation: async () => {
          throw Object.assign(new Error('request_id 已用于其他会话迁移意图'), { status: 409 })
        }
      },
      agentApi: {
        getAgentHistory: async () => ({ thread: { id: 'old-thread', project_id: 'target' } })
      },
      chatThreadsStore: {
        upsertThread: (thread) => {
          threads.value = [thread]
        }
      }
    }
  )
  await assert.rejects(action('old-thread', 'target', [], 'request-1'), /其他会话迁移意图/)
  assert.equal(threads.value[0].project_id, 'target')
})

test('仅移动接口的文件冲突详情可供逐项处理，其他接口错误仍脱敏', () => {
  const source = readFileSync(new URL('../../src/apis/base.js', import.meta.url), 'utf8')
  const safeErrorData = runInNewContext(
    `${source.slice(source.indexOf('function safeErrorData('), source.indexOf('// 仅允许目录删除接口'))}\n;safeErrorData`
  )
  const response = {
    detail: {
      code: 'file_conflicts',
      conflicts: [
        { source_path: '/report.md', destination_path: '/report.md', reason: 'destination_exists' }
      ]
    }
  }
  const movePath = '/api/projects/conversations/thread-1/move'
  const result = safeErrorData(response, 409, '请求冲突', movePath)
  assert.equal(result.detail.code, 'file_conflicts')
  assert.equal(result.detail.conflicts[0].destination_path, '/report.md')
  const unrelated = safeErrorData(response, 409, '请求冲突', '/api/projects/target')
  assert.equal(unrelated.detail, '请求冲突')
})

test('移动请求把同一幂等 ID 和逐文件路径交给服务端', () => {
  const source = readFileSync(new URL('../../src/apis/project_api.js', import.meta.url), 'utf8')
  let request
  const projectApi = runInNewContext(
    `${source.slice(source.indexOf('export const projectApi =')).replace('export const projectApi =', 'const projectApi =')}\n;projectApi`,
    {
      apiPost: (path, body) => {
        request = { path, body }
      },
      encodeURIComponent
    }
  )
  projectApi.moveConversation(
    'thread/1',
    'target',
    [{ source_path: '/old.txt', destination_path: '/new.txt' }],
    'request-1'
  )
  assert.equal(request.path, '/api/projects/conversations/thread%2F1/move')
  assert.deepEqual(JSON.parse(JSON.stringify(request.body)), {
    request_id: 'request-1',
    target_project_id: 'target',
    files: [{ source_path: '/old.txt', destination_path: '/new.txt' }]
  })
})

test('迟到的已读快照不会覆盖较新移动结果的项目绑定', () => {
  const source = readFileSync(new URL('../../src/stores/chatThreads.js', import.meta.url), 'utf8')
  const threads = ref([
    {
      id: 'thread-1',
      project_id: 'target',
      updated_at: '2026-10-14T10:01:00Z',
      thread_status: 'ready'
    }
  ])
  const upsertThread = runInNewContext(
    `${source.slice(source.indexOf('  const upsertThread ='), source.indexOf('  const setThreadStatus ='))}\n;upsertThread`,
    { threads, Date, Number }
  )
  upsertThread({
    id: 'thread-1',
    project_id: 'source',
    updated_at: '2026-10-14T10:00:00Z',
    thread_status: 'done'
  })
  assert.equal(threads.value[0].project_id, 'target')
  assert.equal(threads.value[0].thread_status, 'ready')
  upsertThread({ id: 'thread-1', project_id: 'later', updated_at: '2026-10-14T10:02:00Z' })
  assert.equal(threads.value[0].project_id, 'later')
})
