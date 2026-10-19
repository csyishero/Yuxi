import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import { ref } from 'vue'

const source = readFileSync(
  new URL('../../src/components/dashboard/ThreadDetailDrawer.vue', import.meta.url),
  'utf8'
)

test('admin conversation detail opens on recent messages and prepends older pages', async () => {
  const calls = []
  const detail = ref(null)
  const actions = runInNewContext(
    `${source.slice(source.indexOf('let disposed ='), source.indexOf('const handleClose ='))}\n;({ open, loadOlder })`,
    {
      visible: ref(false),
      loading: ref(false),
      loadingOlder: ref(false),
      detail,
      drawerContentRef: ref(null),
      expandedTools: ref(new Set()),
      nextTick: async () => {},
      props: { scope: 'all' },
      onUnmounted: () => {},
      dashboardApi: {
        getConversationDetail: async (...args) => {
          calls.push(args)
          if (calls.length === 1) {
            return {
              message_count: 3,
              messages_offset: 1,
              has_older_messages: true,
              messages: [{ id: 2 }, { id: 3 }]
            }
          }
          return { messages_offset: 0, has_older_messages: false, messages: [{ id: 1 }] }
        }
      },
      message: { error: () => assert.fail('unexpected error') },
      console: { error: () => {} }
    }
  )

  await actions.open('thread-1')
  assert.equal(calls[0][0], 'thread-1')
  assert.equal(calls[0][2]?.limit, 50)
  assert.deepEqual(
    Array.from(detail.value.messages, (item) => item.id),
    [2, 3]
  )
  await actions.loadOlder()
  assert.equal(calls[1][2].limit, 1)
  assert.equal(calls[1][2].offset, 0)
  assert.deepEqual(
    Array.from(detail.value.messages, (item) => item.id),
    [1, 2, 3]
  )
  assert.equal(detail.value.has_older_messages, false)
})
