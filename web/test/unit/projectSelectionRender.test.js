import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { compileTemplate, parse } from 'vue/compiler-sfc'

test('项目选择菜单内容会被渲染，而不是藏在原生 template 元素中', () => {
  const filename = new URL('../../src/components/ProjectSelectionSection.vue', import.meta.url)
  const source = readFileSync(filename, 'utf8')
  const { descriptor } = parse(source)
  const compiled = compileTemplate({
    source: descriptor.template.content,
    filename: filename.pathname,
    id: 'project-selection'
  })

  assert.deepEqual(compiled.errors, [])
  assert.match(compiled.code, /新建项目/)
  assert.doesNotMatch(compiled.code, /_createElement(?:VNode|Block)\("template"/)
})
