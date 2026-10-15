import assert from 'node:assert/strict'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { ref } from 'vue'
import { createServer } from 'vite'

test('Agent @Skill options follow auto representatives, preload overrides, and fixed/off modes', async () => {
  const server = await createServer({
    configFile: false,
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, hmr: false },
    appType: 'custom'
  })
  try {
    const { useAgentMentionConfig } = await server.ssrLoadModule(
      '/src/composables/useAgentMentionConfig.js'
    )
    const { buildMentionResourceItems } = await server.ssrLoadModule(
      '/src/utils/mention_resource_items.js'
    )
    const skillOptions = [
      { key: 'shared', name: 'Builder', auto_default_slug: 'personal' },
      { key: 'market', name: 'Builder', auto_default_slug: 'personal' },
      { key: 'personal', name: 'Builder', auto_default_slug: 'personal' },
      { key: 'local-pptx-2', name: 'local-pptx', auto_default_slug: 'local-pptx-2' },
      { key: 'other', name: 'Other', auto_default_slug: 'other' }
    ]
    const agentConfig = ref({ skills: null, preload_skills: [] })
    const { mentionConfig } = useAgentMentionConfig({
      currentAgentState: ref({}),
      currentThreadAttachments: ref([]),
      configurableItems: ref({
        skills: { kind: 'skills', options: skillOptions },
        preload_skills: { kind: 'skills', options: skillOptions }
      }),
      agentConfig
    })
    const offered = () => mentionConfig.value.skills.map((skill) => skill.slug)

    assert.deepEqual(offered(), ['personal', 'local-pptx-2', 'other'])
    assert.equal(
      mentionConfig.value.skills.find((skill) => skill.slug === 'local-pptx-2').name,
      'local-pptx (local-pptx-2)'
    )
    const mentionItem = buildMentionResourceItems(mentionConfig.value).skills.find(
      (skill) => skill.value === 'local-pptx-2'
    )
    assert.equal(mentionItem.label, 'local-pptx (local-pptx-2)')
    assert.equal(mentionItem.value, 'local-pptx-2')

    agentConfig.value = { skills: null, preload_skills: [' shared '] }
    assert.deepEqual(offered(), ['shared', 'local-pptx-2', 'other'])

    agentConfig.value = { skills: null, preload_skills: ['shared', 'market', 'missing'] }
    assert.deepEqual(offered(), ['shared', 'market', 'local-pptx-2', 'other'])

    agentConfig.value = { skills: [' market '], preload_skills: ['shared', 'other'] }
    assert.deepEqual(offered(), ['market'])

    agentConfig.value = { skills: [], preload_skills: ['shared'] }
    assert.deepEqual(offered(), [])
  } finally {
    await server.close()
  }
})
