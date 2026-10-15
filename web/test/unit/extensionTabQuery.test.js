import assert from 'node:assert/strict'
import test from 'node:test'

import { nextExtensionTabQuery } from '../../src/views/extensionTabQuery.js'

test('旧市场链接进入技能市场视图', () => {
  assert.deepEqual(nextExtensionTabQuery({ tab: 'market' }, 'skills', 'knowledge'), {
    tab: 'skills',
    view: 'market'
  })
})

test('离开技能页时清除市场视图参数', () => {
  assert.deepEqual(nextExtensionTabQuery({ tab: 'skills', view: 'market' }, 'mcp', 'knowledge'), {
    tab: 'mcp'
  })
})

test('新进入技能页默认显示个人视图', () => {
  assert.deepEqual(nextExtensionTabQuery({ tab: 'knowledge' }, 'skills', 'knowledge'), {
    tab: 'skills'
  })
})
