<template>
  <div class="stats-overview-container">
    <p class="scope-note">
      {{
        scope === 'all'
          ? '历史累计 · 包含已删除会话、项目及已注销用户的留存记录'
          : '当前有效记录 · 排除已删除会话、项目、已注销用户和已移除智能体的记录'
      }}
    </p>
    <DashboardMetricGrid>
      <DashboardMetricCard
        :icon="MessageCircle"
        :value="formatNumber(basicStats?.total_conversations)"
        label="累计会话"
        tone="primary"
      >
        <template #meta v-if="basicStats?.conversation_trend">
          <span class="metric-trend" :class="basicStats.conversation_trend > 0 ? 'up' : 'down'">
            <TrendingUp v-if="basicStats.conversation_trend > 0" />
            <TrendingDown v-else />
            {{ Math.abs(basicStats.conversation_trend) }}%
          </span>
        </template>
      </DashboardMetricCard>

      <DashboardMetricCard
        :icon="Mail"
        :value="formatNumber(basicStats?.total_messages)"
        label="总消息数"
        tone="info"
      />
      <DashboardMetricCard
        :icon="BarChart3"
        :value="formatNumber(basicStats?.feedback_stats?.total_feedbacks)"
        label="总反馈数"
        tone="accent"
        clickable
        @click="handleFeedbackClick"
      />
      <DashboardMetricCard
        :icon="Heart"
        :value="
          basicStats?.feedback_stats?.satisfaction_rate == null
            ? '无反馈'
            : `${basicStats.feedback_stats.satisfaction_rate}%`
        "
        label="满意度"
        :tone="getSatisfactionTone()"
      />
    </DashboardMetricGrid>
    <p class="scope-note">当前可用资源</p>
    <DashboardMetricGrid>
      <DashboardMetricCard
        :icon="Folder"
        :value="formatNumber(basicStats?.current_resources?.projects)"
        label="当前项目"
      />
      <DashboardMetricCard
        :icon="Activity"
        :value="formatNumber(basicStats?.current_resources?.conversations)"
        label="当前会话"
      />
      <DashboardMetricCard
        :icon="Users"
        :value="formatNumber(basicStats?.current_resources?.users)"
        label="当前用户"
      />
    </DashboardMetricGrid>
  </div>
</template>

<script setup>
import {
  Folder,
  MessageCircle,
  Activity,
  Mail,
  Users,
  BarChart3,
  Heart,
  TrendingUp,
  TrendingDown
} from '@lucide/vue'
import { formatNumber } from '@/utils/dashboard'
import DashboardMetricCard from './DashboardMetricCard.vue'
import DashboardMetricGrid from './DashboardMetricGrid.vue'

const props = defineProps({
  scope: { type: String, default: 'all' },
  basicStats: {
    type: Object,
    default: () => ({})
  }
})

const emit = defineEmits(['open-feedback'])

const handleFeedbackClick = () => {
  emit('open-feedback')
}

const getSatisfactionTone = () => {
  const rate = props.basicStats?.feedback_stats?.satisfaction_rate || 0
  if (rate >= 80) return 'success'
  if (rate >= 60) return 'warning'
  return 'neutral'
}
</script>

<style lang="less" scoped>
.scope-note {
  padding: 0 var(--page-padding);
  color: var(--gray-600);
  margin: 16px 0 8px;
  font-size: 12px;
}

.stats-overview-container {
  margin-top: 8px;
}

.dashboard-metric-grid {
  padding: 0 var(--page-padding);
}

.metric-trend {
  display: inline-flex;
  align-items: center;
  gap: 2px;

  svg {
    width: 12px;
    height: 12px;
  }

  &.up {
    color: var(--color-success-700);
  }

  &.down {
    color: var(--color-error-700);
  }
}
</style>
