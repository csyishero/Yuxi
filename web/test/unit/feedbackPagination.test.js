import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import { ref } from 'vue'

test('反馈翻页只请求对应页并显示后端返回的总数', async () => {
  const source = readFileSync(
    new URL('../../src/components/dashboard/FeedbackModalComponent.vue', import.meta.url),
    'utf8'
  )
  const requests = []
  const feedbacks = ref([])
  const feedbackTotal = ref(0)
  const feedbackPage = ref(1)
  const actions = runInNewContext(
    `${source.slice(source.indexOf('let disposed ='), source.indexOf('const getFeedbackDefaultAvatarSrc'))}\n;({ loadFeedbacks })`,
    {
      props: { scope: 'all', agentId: null },
      onUnmounted() {},
      loadingFeedbacks: ref(false),
      feedbackFilter: ref('all'),
      feedbacks,
      feedbackTotal,
      feedbackPage,
      feedbackPageSize: 20,
      expandedStates: ref(new Map()),
      dashboardApi: {
        getFeedbacks: async (params) => {
          requests.push(params)
          return { items: [{ id: 21 }], total: 42, limit: 20, offset: 20 }
        }
      },
      message: { error: () => assert.fail('不应提示错误') },
      console: { error: () => assert.fail('不应记录错误') }
    }
  )

  await actions.loadFeedbacks(2)

  assert.equal(requests.length, 1)
  assert.equal(requests[0].limit, 20)
  assert.equal(requests[0].offset, 20)
  assert.deepEqual(feedbacks.value, [{ id: 21 }])
  assert.equal(feedbackTotal.value, 42)
})
