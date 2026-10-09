import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import { computed, ref } from 'vue'
import { parse } from 'vue/compiler-sfc'
import { normalizePreviewResponse } from '../../src/utils/file_preview.js'
import { invalidatePreviewCacheEntryBeforeReload, replacePreviewCacheEntryIfCurrent, settlePreviewCacheLoad } from '../../src/utils/agentPanelFilesystemPolling.js'

const readComponent = (path) => parse(readFileSync(new URL(path, import.meta.url), 'utf8')).descriptor
const preview = readComponent('../../src/components/AgentFilePreview.vue')
const panel = readComponent('../../src/components/AgentPanel.vue').scriptSetup.content

test('普通和全屏提供刷新入口，加载/保存/编辑期间不刷新，失败后可重试', () => {
  assert.equal((preview.template.content.match(/aria-label="刷新文件预览"/g) || []).length, 2)
  const source = preview.scriptSetup.content
  for (const [status, saving, editing, expected] of [
    ['loading', false, false, 0], ['ready', true, false, 0], ['ready', false, true, 0],
    ['ready', false, false, 1], ['error', false, false, 1]
  ]) {
    const emitted = []
    runInNewContext(`${source.slice(source.indexOf('const refreshDisabled ='), source.indexOf('const draftContent ='))}\nrequestRefresh()`, {
      computed, props: { refreshable: true, filePath: '/file.txt', saving },
      currentStatus: ref(status), editMode: ref(editing ? 'edit' : 'preview'), emit: (event) => emitted.push(event)
    })
    assert.equal(emitted.length, expected)
  }
})

/** 执行正式预览加载器，用可控响应检查缓存和竞态结果。 */
function panelLoader({ workspace = false, workdir = true, path = '/report.txt', read }) {
  const revoked = []
  const oldFile = { content: 'old', previewUrl: 'blob:old', path, workspace, workdir }
  const props = { threadId: 'thread-1', activePreviewPath: path, previewCache: new Map() }
  const key = workspace ? `workspace:${path}` : `thread-1:${path}`
  props.previewCache.set(key, { status: 'ready', file: oldFile })
  const currentFile = ref(oldFile)
  const calls = []
  const call = (kind) => async (...args) => { calls.push(kind); return read(...args) }
  const script = panel.slice(panel.indexOf('const refreshActivePreview ='), panel.indexOf('const findTreeNode ='))
  const actions = runInNewContext(`${script}\n({ refreshActivePreview, loadActivePreview })`, {
    props, currentFile, currentFilePath: ref(path), previewRequestSeq: 0,
    activePreviewTab: ref({ path, workspace, workdir }),
    workspacePreviewCacheKey: (value) => `workspace:${value}`,
    previewCacheKey: (value, thread = props.threadId) => `${thread}:${value}`,
    window: { URL: { revokeObjectURL: (url) => revoked.push(url) } },
    getFileName: ({ path: value }) => value.split('/').pop(),
    invalidatePreviewCacheEntryBeforeReload, replacePreviewCacheEntryIfCurrent, settlePreviewCacheLoad, normalizePreviewResponse,
    getWorkspaceFileContent: call('workspace'), getViewerFileContent: call('viewer'),
    threadApi: { previewThreadArtifact: call('artifact') }, prunePreviewCache: () => {}, revokeCurrentPreviewUrl: () => {}, Date
  })
  return { ...actions, props, currentFile, calls, revoked }
}

test('刷新绕过对话、个人空间和 artifact 缓存并重新读取文本', async () => {
  for (const [workspace, workdir, kind] of [[false, true, 'viewer'], [true, false, 'workspace'], [false, false, 'artifact']]) {
    const loader = panelLoader({ workspace, workdir, read: async () => new Response('updated', { headers: { 'content-type': 'text/plain' } }) })
    await loader.refreshActivePreview()
    assert.equal(loader.currentFile.value.content, 'updated')
    assert.deepEqual(loader.calls, [kind])
    assert.deepEqual(loader.revoked, ['blob:old'])
  }
})

test('二进制刷新生成新对象URL，失败后可以再次刷新', async () => {
  const originalWindow = globalThis.window
  const blobs = []
  globalThis.window = { URL: { createObjectURL: (blob) => { blobs.push(blob); return 'blob:new' } } }
  try {
    let fail = true
    const loader = panelLoader({ path: '/report.pdf', read: async () => {
      if (fail) throw new Error('temporary error')
      return new Response('new PDF bytes', { headers: { 'content-type': 'application/pdf' } })
    } })
    await loader.refreshActivePreview()
    assert.equal(loader.currentFile.value.status, 'error')
    fail = false
    await loader.refreshActivePreview()
    assert.equal(loader.currentFile.value.previewUrl, 'blob:new')
    assert.equal(await blobs[0].text(), 'new PDF bytes')
  } finally { globalThis.window = originalWindow }
})

test('刷新期间切换文件，迟到结果不能覆盖新文件', async () => {
  let finishOld
  const loader = panelLoader({ read: async (_thread, path) => path === '/report.txt'
    ? new Promise((resolve) => { finishOld = resolve })
    : new Response('other file', { headers: { 'content-type': 'text/plain' } }) })
  const pending = loader.refreshActivePreview()
  loader.props.activePreviewPath = '/other.txt'
  await loader.loadActivePreview()
  finishOld(new Response('late old file', { headers: { 'content-type': 'text/plain' } }))
  await pending
  assert.equal(loader.currentFile.value.content, 'other file')
})

test('个人空间按当前来源刷新，加载期间不重复请求', async () => {
  const script = readComponent('../../src/views/WorkspaceView.vue').scriptSetup.content
  const part = script.slice(script.indexOf('const refreshSelectedPreview ='), script.indexOf('const loadWorkspacePreview ='))
  const calls = []
  const selectedEntry = ref({ path: '/file.txt' })
  const loadingPreview = ref(false)
  const refresh = runInNewContext(`${part}\nrefreshSelectedPreview`, {
    selectedEntry, loadingPreview, savingPreviewFile: ref(false),
    loadWorkspacePreview: async (entry) => calls.push(['workspace', entry.path]),
    loadKnowledgePreview: async (entry) => calls.push(['knowledge', entry.file_id])
  })
  await refresh()
  selectedEntry.value = { source: 'knowledge', file_id: 'file-1' }
  await refresh()
  loadingPreview.value = true
  await refresh()
  assert.deepEqual(calls, [['workspace', '/file.txt'], ['knowledge', 'file-1']])
})
