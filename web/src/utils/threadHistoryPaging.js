const utcTime = (value) => {
  if (!value) return 0
  const text = String(value)
  return Date.parse(/[Zz]|[+-]\d\d:\d\d$/.test(text) ? text : `${text}Z`) || 0
}

/** 合并较早或刷新后的持久历史，按消息与运行标识去重并维持展示顺序。 */
export const mergeHistoryPage = (currentHistory = [], currentRuns = [], page = {}) => {
  const runById = new Map()
  for (const run of [...currentRuns, ...(page.runs || [])]) {
    if (run?.run_id) runById.set(run.run_id, run)
  }
  const runs = [...runById.values()].sort(
    (left, right) =>
      utcTime(left.timing?.created_at) - utcTime(right.timing?.created_at) ||
      String(left.run_id).localeCompare(String(right.run_id))
  )

  const messageById = new Map()
  for (const item of [...currentHistory, ...(page.history || [])]) {
    if (item?.id != null) messageById.set(item.id, item)
  }
  const sortKey = (item) => utcTime(runById.get(item.run_id)?.timing?.created_at || item.created_at)
  const roleOrder = (item) => item.type === 'human' ? 0 : 1
  const history = [...messageById.values()].sort(
    (left, right) =>
      sortKey(left) - sortKey(right) ||
      roleOrder(left) - roleOrder(right) ||
      utcTime(left.created_at) - utcTime(right.created_at) ||
      Number(left.id) - Number(right.id)
  )

  return { history, runs }
}

/** 刷新最新一页时保留已连续加载的旧游标，发现缓存断档时改从新页续取。 */
export const reconcileHistoryPage = (previous, cachedHistory = [], cachedRuns = [], response = {}) => {
  const latest = response.page
  if (!latest || !previous) return latest || previous

  const reconcile = (flag, cursor, cached, fetched, key) => {
    if (!latest[flag]) return { hasMore: false, before: latest[cursor] }
    const cachedIds = new Set(cached.map((item) => item?.[key]).filter((id) => id != null))
    const overlaps = fetched.some((item) => cachedIds.has(item?.[key]))
    return overlaps
      ? { hasMore: Boolean(previous[flag]), before: previous[cursor] }
      : { hasMore: true, before: latest[cursor] }
  }

  const messages = reconcile('has_more_messages', 'before_message_id', cachedHistory, response.history || [], 'id')
  const runPageIds = latest.run_page_ids ? new Set(latest.run_page_ids) : null
  const fetchedRuns = runPageIds
    ? (response.runs || []).filter((run) => runPageIds.has(run?.run_id))
    : response.runs || []
  const runs = reconcile('has_more_runs', 'before_run_id', cachedRuns, fetchedRuns, 'run_id')
  return {
    has_more: messages.hasMore || runs.hasMore,
    has_more_messages: messages.hasMore,
    has_more_runs: runs.hasMore,
    before_message_id: messages.before,
    before_run_id: runs.before
  }
}
