import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import { createSSRApp, h } from 'vue'
import { parse } from 'vue/compiler-sfc'
import { renderToString } from 'vue/server-renderer'

const source = readFileSync(
  new URL('../../src/components/ConversationNavSection.vue', import.meta.url),
  'utf8'
)
const script = parse(source).descriptor.scriptSetup.content
const functionStart = script.indexOf('const confirmDeleteProject =')
const functionEnd = script.indexOf('\n}', functionStart) + 2
const confirmDeleteProjectSource = script.slice(functionStart, functionEnd)

function openDeleteConfirmation(project) {
  let config
  let emitted
  runInNewContext(`${confirmDeleteProjectSource}\nconfirmDeleteProject(project)`, {
    h,
    project,
    Modal: { confirm: (value) => { config = value } },
    emit: (event, payload) => { emitted = { event, payload } }
  })
  const content = config.content()
  const checkbox = content.children.find((node) => node.type === 'label').children[0]
  return { config, content, checkbox, getEmitted: () => emitted }
}

test('managed 项目可选择同时清理专属目录，默认保留文件', async () => {
  const dialog = openDeleteConfirmation({
    id: 'managed-id', name: '新项目', directory_mode: 'managed', workdir_path: 'projects/new-id'
  })

  assert.equal(dialog.checkbox.props.disabled, false)
  const html = await renderToString(createSSRApp({ render: () => dialog.content }))
  assert.match(html, /同时永久删除项目文件夹/)
  assert.doesNotMatch(html, /<input[^>]*disabled/)
  dialog.config.onOk()
  assert.equal(dialog.getEmitted().payload.deleteWorkdir, false)

  const chosen = openDeleteConfirmation({
    id: 'managed-id', name: '新项目', directory_mode: 'managed', workdir_path: 'projects/new-id'
  })
  chosen.checkbox.props.onChange({ target: { checked: true } })
  chosen.config.onOk()
  assert.equal(chosen.getEmitted().payload.deleteWorkdir, true)
})

test('linked 项目明确说明已有目录会保留，不能选择随项目删除', async () => {
  const dialog = openDeleteConfirmation({
    id: 'linked-id', name: '旧项目', directory_mode: 'linked', workdir_path: 'project'
  })

  assert.equal(dialog.checkbox.props.disabled, true)
  const html = await renderToString(createSSRApp({ render: () => dialog.content }))
  assert.match(html, /<input[^>]*disabled/)
  assert.match(html, /关联的是已有目录/)
  dialog.checkbox.props.onChange({ target: { checked: true } })
  dialog.config.onOk()
  assert.equal(dialog.getEmitted().event, 'delete-project')
  assert.equal(dialog.getEmitted().payload.deleteWorkdir, false)
})
