import assert from 'node:assert/strict'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { ref } from 'vue'
import { createServer } from 'vite'

import { buildMentionResourceItems } from '../../src/utils/mention_resource_items.js'

test('MCP 提及只提供当前用户可使用的服务', () => {
  const items = buildMentionResourceItems({
    mcps: [
      { slug: 'visible', name: '仅可查看', enabled: true, permission: 'read' },
      { slug: 'usable', name: '允许使用', enabled: true, permission: 'edit' },
      { slug: 'manager', name: '管理服务', enabled: true, permission: 'manage' },
      { slug: 'disabled', name: '已停用', enabled: false, permission: 'manage' }
    ]
  })

  assert.deepEqual(items.mcps.map((item) => item.value), ['usable', 'manager'])
})

test('Agent 已授权选项无需目录权限字段也能显示 MCP 提及', async () => {
  const server = await createServer({
    configFile: false,
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, hmr: false },
    appType: 'custom'
  })
  try {
    const { useAgentMentionConfig } = await server.ssrLoadModule('/src/composables/useAgentMentionConfig.js')
    const { mentionConfig } = useAgentMentionConfig({
      currentAgentState: ref({}),
      currentThreadAttachments: ref([]),
      configurableItems: ref({
        mcps: {
          kind: 'mcps',
          options: [{ key: 'usable', name: '允许使用', description: '已授权' }]
        }
      }),
      agentConfig: ref({ mcps: null })
    })

    assert.deepEqual(mentionConfig.value.mcps, [
      { slug: 'usable', name: '允许使用', description: '已授权' }
    ])
    assert.deepEqual(
      buildMentionResourceItems(mentionConfig.value).mcps.map((item) => item.value),
      ['usable']
    )
  } finally {
    await server.close()
  }
})
