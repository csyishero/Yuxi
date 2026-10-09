import { apiAdminGet } from './base'

/**
 * Dashboard API模块
 * 用于超级管理员查看系统概览、调用监控与会话深度分析
 */

// 历史范围沿用默认请求，当前范围显式传给后端。
const scopeQuery = (scope) => (scope === 'current' ? '?scope=current' : '')

export const dashboardApi = {
  /**
   * 获取所有对话记录
   * @param {Object} params - 查询参数
   * @param {string} [params.uid] - 用户 UID 过滤
   * @param {string} [params.agent_id] - 智能体ID过滤
   * @param {string} [params.status] - 状态过滤 (active/archived/deleted/all)
   * @param {string} [params.search] - 标题/ID/UID 关键字搜索
   * @param {number} [params.limit] - 每页数量
   * @param {number} [params.offset] - 偏移量
   * @returns {Promise<Object>} - 分页对话列表（items/total/limit/offset）
   */
  getConversations: (params = {}) => {
    const queryParams = new URLSearchParams()
    if (params.scope) queryParams.append('scope', params.scope)
    if (params.uid) queryParams.append('uid', params.uid)
    if (params.agent_id) queryParams.append('agent_id', params.agent_id)
    if (params.status) queryParams.append('status', params.status)
    if (params.search) queryParams.append('search', params.search)
    if (params.limit) queryParams.append('limit', params.limit)
    if (params.offset) queryParams.append('offset', params.offset)
    if (params.project_id) queryParams.append('project_id', params.project_id)
    if (params.time_range) queryParams.append('time_range', params.time_range)
    if (params.include_subagents !== undefined)
      queryParams.append('include_subagents', String(params.include_subagents))

    return apiAdminGet(`/api/dashboard/conversations?${queryParams.toString()}`)
  },

  /**
   * 获取会话审计筛选选项
   * @returns {Promise<Object>} - 用户与智能体选项
   */
  getConversationFilterOptions: (scope = 'all') => {
    return apiAdminGet(`/api/dashboard/conversations/options${scopeQuery(scope)}`)
  },

  /**
   * 获取对话详情
   * @param {string} threadId - 对话线程ID
   * @returns {Promise<Object>} - 对话详情
   */
  getConversationDetail: (threadId, scope = 'all') => {
    return apiAdminGet(`/api/dashboard/conversations/${threadId}${scopeQuery(scope)}`)
  },

  /**
   * 获取Dashboard基础统计信息
   * @returns {Promise<Object>} - 统计信息
   */
  getStats: (scope = 'all') => {
    return apiAdminGet(`/api/dashboard/stats${scopeQuery(scope)}`)
  },

  /**
   * 获取用户反馈列表
   * @param {Object} params - 查询参数
   * @param {string} [params.rating] - 反馈类型过滤 (like/dislike/all)
   * @param {string} [params.agent_id] - 智能体ID过滤
   * @returns {Promise<Array>} - 反馈列表
   */
  getFeedbacks: (params = {}) => {
    const queryParams = new URLSearchParams()
    if (params.scope) queryParams.append('scope', params.scope)
    if (params.rating && params.rating !== 'all') queryParams.append('rating', params.rating)
    if (params.agent_id) queryParams.append('agent_id', params.agent_id)

    return apiAdminGet(`/api/dashboard/feedbacks?${queryParams.toString()}`)
  },

  /**
   * 获取用户活跃度统计
   * @returns {Promise<Object>} - 用户活跃度统计信息
   */
  getUserStats: (scope = 'all') => {
    return apiAdminGet(`/api/dashboard/stats/users${scopeQuery(scope)}`)
  },

  /**
   * 获取工具调用统计
   * @returns {Promise<Object>} - 工具调用统计信息
   */
  getToolStats: (scope = 'all') => {
    return apiAdminGet(`/api/dashboard/stats/tools${scopeQuery(scope)}`)
  },

  /**
   * 获取知识库统计
   * @returns {Promise<Object>} - 知识库统计信息
   */
  getKnowledgeStats: () => {
    return apiAdminGet('/api/dashboard/stats/knowledge')
  },

  /**
   * 获取AI智能体分析数据
   * @returns {Promise<Object>} - AI智能体分析信息
   */
  getAgentStats: (scope = 'all') => {
    return apiAdminGet(`/api/dashboard/stats/agents${scopeQuery(scope)}`)
  },

  /**
   * 获取会话（Thread）多维分析统计
   * @param {Object} params - 查询参数
   * @param {string} [params.timeRange='30days'] - 时间范围 (7days/14days/30days/90days)
   * @param {string} [params.agentId] - 智能体过滤
   * @param {boolean} [params.includeSubagents=false] - 是否纳入子智能体会话
   * @returns {Promise<Object>} - 会话分析统计数据
   */
  getThreadStats: (params = {}) => {
    const queryParams = new URLSearchParams()
    if (params.scope) queryParams.append('scope', params.scope)
    if (params.timeRange) queryParams.append('time_range', params.timeRange)
    if (params.agentId) queryParams.append('agent_id', params.agentId)
    if (params.includeSubagents !== undefined)
      queryParams.append('include_subagents', String(params.includeSubagents))
    if (params.projectId) queryParams.append('project_id', params.projectId)
    if (params.uid) queryParams.append('uid', params.uid)
    if (params.status) queryParams.append('status', params.status)
    if (params.search) queryParams.append('search', params.search)

    return apiAdminGet(`/api/dashboard/stats/threads?${queryParams.toString()}`)
  },

  /**
   * 批量获取系统概览所有统计数据（并行请求）
   * @returns {Promise<Object>} - 所有统计数据
   */
  getAllStats: async (scope = 'all') => {
    try {
      const requests = {
        basic: apiAdminGet(`/api/dashboard/stats${scopeQuery(scope)}`),
        users: apiAdminGet(`/api/dashboard/stats/users${scopeQuery(scope)}`),
        tools: apiAdminGet(`/api/dashboard/stats/tools${scopeQuery(scope)}`),
        agents: apiAdminGet(`/api/dashboard/stats/agents${scopeQuery(scope)}`),
        knowledge: apiAdminGet('/api/dashboard/stats/knowledge')
      }

      const entries = Object.entries(requests)
      const values = await Promise.all(entries.map(([, request]) => request))
      return {
        ...Object.fromEntries(entries.map(([name], index) => [name, values[index]]))
      }
    } catch (error) {
      console.error('批量获取统计数据失败:', error)
      throw error
    }
  },

  /**
   * 获取调用统计时间序列数据
   * @param {string} type - 数据类型 (models/agents/tokens/tools)
   * @param {string} timeRange - 时间范围 (14hours/14days/14weeks)
   * @returns {Promise<Object>} - 时间序列统计数据
   */
  getCallTimeseries: (type = 'models', timeRange = '14days', scope = 'all') => {
    return apiAdminGet(
      `/api/dashboard/stats/calls/timeseries?type=${type}&time_range=${timeRange}${scope === 'current' ? '&scope=current' : ''}`
    )
  }
}
