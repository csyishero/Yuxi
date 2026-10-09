import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import { computed, ref } from 'vue'
import { parse } from 'vue/compiler-sfc'

const script = parse(
  readFileSync(
    new URL('../../src/components/dashboard/AgentStatsComponent.vue', import.meta.url),
    'utf8'
  )
).descriptor.scriptSetup.content

function chartHarness() {
  const instances = [],
    observers = [],
    watchers = []
  const props = {
    loading: false,
    agentStats: {
      agent_conversation_counts: [
        { agent_id: 'a', conversation_count: 75 },
        { agent_id: 'b', conversation_count: 3 }
      ],
      agent_tool_usage: [{ agent_id: 'a', tool_usage_count: 160 }],
      agent_names: { a: '智能助手长名称', b: '知识专家' }
    }
  }
  const actions = runInNewContext(
    `${script.slice(script.indexOf('const conversationToolChartRef ='))}\n;({conversationToolChartRef, updateCharts, cleanup})`,
    {
      props,
      ref,
      computed,
      nextTick: async () => {},
      themeStore: { isDark: false },
      getCSSVariable: () => '#999',
      getColorByIndex: () => '#099',
      watch: (dependencies, callback) => watchers.push({ dependencies, callback }),
      onMounted() {},
      onBeforeUnmount() {},
      defineExpose() {},
      ResizeObserver: class {
        constructor(callback) {
          this.callback = callback
          observers.push(this)
        }
        observe(element) {
          this.element = element
        }
        disconnect() {
          this.disconnected = true
        }
      },
      echarts: {
        init(element) {
          const instance = {
            width: element.clientWidth,
            disposed: false,
            setOption(option) {
              this.option = option
            },
            dispose() {
              this.disposed = true
            }
          }
          instances.push(instance)
          return instance
        }
      }
    }
  )
  return { ...actions, props, instances, observers, watchers }
}

test('隐藏时数据到达不初始化，显示和卡片宽度变化后按实际尺寸绘图', async () => {
  const h = chartHarness()
  h.conversationToolChartRef.value = { clientWidth: 0, clientHeight: 250 }
  await h.updateCharts()
  assert.equal(h.instances.length, 0)
  h.conversationToolChartRef.value.clientWidth = 380
  h.observers[0].callback()
  assert.equal(h.instances[0].width, 380)
  assert.deepEqual([...h.instances[0].option.series[0].data], [75, 3])
  assert.deepEqual([...h.instances[0].option.series[1].data], [160, 0])
  h.conversationToolChartRef.value.clientWidth = 620
  h.observers[0].callback()
  assert.equal(h.instances[0].disposed, true)
  assert.equal(h.instances[1].width, 620)
  assert.ok(
    h.instances[1].option.xAxis.axisLabel.width > h.instances[0].option.xAxis.axisLabel.width
  )
})

test('loading 替换节点后重新绑定，空数据清除旧柱形', async () => {
  const h = chartHarness()
  h.conversationToolChartRef.value = { clientWidth: 380, clientHeight: 250 }
  await h.updateCharts()
  assert.equal(h.watchers[0].dependencies[1](), false)
  h.props.loading = true
  await h.watchers[0].callback()
  assert.equal(h.instances[0].disposed, true)
  assert.equal(h.observers[0].disconnected, true)
  h.props.loading = false
  h.props.agentStats = { agent_conversation_counts: [], agent_tool_usage: [] }
  h.conversationToolChartRef.value = { clientWidth: 500, clientHeight: 250 }
  await h.watchers[0].callback()
  assert.equal(h.instances[1].width, 500)
  assert.equal(h.instances[1].option.series[0].data.length, 0)
  assert.equal(h.observers[1].element, h.conversationToolChartRef.value)
})

test('卸载后排队的更新和尺寸通知不能重建图表', async () => {
  const h = chartHarness()
  h.conversationToolChartRef.value = { clientWidth: 380, clientHeight: 250 }
  await h.updateCharts()
  const queued = h.updateCharts()
  h.cleanup()
  await queued
  h.observers[0].callback()
  assert.equal(h.instances.length, 1)
  assert.equal(h.instances[0].disposed, true)
  assert.equal(h.observers[0].disconnected, true)
})
