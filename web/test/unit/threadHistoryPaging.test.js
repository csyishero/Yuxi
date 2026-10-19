import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import { ref } from 'vue'

import { mergeHistoryPage, reconcileHistoryPage } from '../../src/utils/threadHistoryPaging.js'

test('older history merges by message and run ID while preserving conversation order', () => {
  const newer = {
    history: [
      { id: 3, run_id: 'run-2', type: 'human', content: 'C', created_at: '2026-07-12T09:02:00' },
      { id: 4, run_id: 'run-2', type: 'ai', content: 'C reply', created_at: '2026-07-12T09:04:00' }
    ],
    runs: [{ run_id: 'run-2', timing: { created_at: '2026-07-12T09:02:00' } }]
  }
  const older = {
    history: [
      { id: 1, run_id: 'run-0', type: 'human', content: 'A', created_at: '2026-07-12T09:00:00' },
      { id: 2, run_id: 'run-0', type: 'ai', content: 'A reply', created_at: '2026-07-12T09:03:00' }
    ],
    runs: [
      { run_id: 'run-0', timing: { created_at: '2026-07-12T09:00:00' } },
      { run_id: 'run-2', status: 'completed', timing: { created_at: '2026-07-12T09:02:00' } }
    ]
  }

  const merged = mergeHistoryPage(newer.history, newer.runs, older)
  assert.deepEqual(merged.history.map((message) => message.id), [1, 2, 3, 4])
  assert.deepEqual(merged.runs.map((run) => run.run_id), ['run-0', 'run-2'])
  assert.equal(merged.runs[1].status, 'completed')

  const refreshed = mergeHistoryPage(merged.history, merged.runs, {
    history: [{ ...newer.history[1], content: 'updated reply' }],
    runs: [{ ...newer.runs[0], status: 'completed' }]
  })
  assert.equal(refreshed.history.length, 4)
  assert.equal(refreshed.history[3].content, 'updated reply')
})

test('refreshing a thread after loading its oldest page does not reopen older pagination', async () => {
  const chatSource = readFileSync(new URL('../../src/components/AgentChatComponent.vue', import.meta.url), 'utf8')
  const subagentSource = readFileSync(new URL('../../src/components/SubagentThreadView.vue', import.meta.url), 'utf8')
  const newestPage = { has_more: true, has_more_messages: true, has_more_runs: false, before_message_id: 50, before_run_id: null }
  const exhausted = { has_more: false, has_more_messages: false, has_more_runs: false, before_message_id: 1, before_run_id: null }
  const response = { history: [{ id: 51 }], runs: [], page: newestPage, thread: { id: 'thread-1' } }

  const threadHistoryPages = ref({ 'thread-1': exhausted })
  const fetchThreadMessages = runInNewContext(
    `${chatSource.slice(chatSource.indexOf('const fetchThreadMessages ='), chatSource.indexOf('const loadOlderThreadMessages ='))}; fetchThreadMessages`,
    {
      threadHistoryPages,
      threadMessages: ref({ 'thread-1': [{ id: 51 }] }),
      threadRuns: ref({ 'thread-1': [] }),
      agentApi: { getAgentHistory: async () => response },
      mergeHistoryPage,
      reconcileHistoryPage,
      chatThreadsStore: { upsertThread() {} },
      handleChatError: () => assert.fail('unexpected error')
    }
  )
  await fetchThreadMessages({ agentId: 'agent-1', threadId: 'thread-1' })
  assert.equal(threadHistoryPages.value['thread-1'].has_more, false)
  assert.equal(threadHistoryPages.value['thread-1'].before_message_id, 1)

  const page = ref(exhausted)
  const loadPersistedMessages = runInNewContext(
    `${subagentSource.slice(subagentSource.indexOf('const loadPersistedMessages ='), subagentSource.indexOf('const loadOlderMessages ='))}; loadPersistedMessages`,
    {
      page,
      messages: ref([{ id: 51 }]),
      runs: ref([]),
      props: { threadId: 'thread-1' },
      agentApi: { getAgentHistory: async () => response },
      mergeHistoryPage,
      reconcileHistoryPage,
      normalizeMessages: (items) => items,
      disposed: false,
      loadVersion: 1
    }
  )
  await loadPersistedMessages(1)
  assert.equal(page.value.has_more, false)
  assert.equal(page.value.before_message_id, 1)
})

test('a fresh latest page reopens pagination when cached and new message ranges do not overlap', () => {
  const exhausted = { has_more: false, has_more_messages: false, has_more_runs: false, before_message_id: null, before_run_id: null }
  const fresh = {
    history: Array.from({ length: 50 }, (_, index) => ({ id: index + 71 })),
    runs: [],
    page: { has_more: true, has_more_messages: true, has_more_runs: false, before_message_id: 71, before_run_id: null }
  }
  const cached = Array.from({ length: 20 }, (_, index) => ({ id: index + 1 }))

  assert.deepEqual(reconcileHistoryPage(exhausted, cached, [], fresh), fresh.page)
  assert.deepEqual(
    reconcileHistoryPage(exhausted, [...cached, ...fresh.history], [], fresh),
    exhausted
  )
})

test('a run gap can reopen without reopening an already contiguous message history', () => {
  const previous = { has_more: false, has_more_messages: false, has_more_runs: false, before_message_id: null, before_run_id: null }
  const fresh = {
    history: [{ id: 1 }],
    runs: [{ run_id: 'run-100' }],
    page: { has_more: true, has_more_messages: false, has_more_runs: true, before_message_id: null, before_run_id: 'run-100' }
  }

  assert.deepEqual(reconcileHistoryPage(previous, [{ id: 1 }], [{ run_id: 'run-1' }], fresh), {
    has_more: true,
    has_more_messages: false,
    has_more_runs: true,
    before_message_id: null,
    before_run_id: 'run-100'
  })
})

test('a referenced old run does not hide a gap in the newly fetched run page', () => {
  const previous = {
    has_more: false,
    has_more_messages: false,
    has_more_runs: false,
    before_message_id: null,
    before_run_id: 'run-001'
  }
  const response = {
    history: [{ id: 1, run_id: 'run-001' }],
    runs: [
      { run_id: 'run-001' },
      { run_id: 'run-101' },
      { run_id: 'run-150' }
    ],
    page: {
      has_more: true,
      has_more_messages: false,
      has_more_runs: true,
      before_message_id: null,
      before_run_id: 'run-101',
      run_page_ids: ['run-101', 'run-150']
    }
  }

  const actual = reconcileHistoryPage(previous, [{ id: 1 }], [{ run_id: 'run-001' }], response)
  assert.equal(actual.has_more_runs, true)
  assert.equal(actual.before_run_id, 'run-101')
})
