/** 保持扩展页当前标签与 URL 查询参数一致。 */
export function nextExtensionTabQuery(routeQuery, tab, defaultTab) {
  const query = { ...routeQuery }
  if (routeQuery.tab === 'market' && tab === 'skills') query.view = 'market'
  else if (tab !== 'skills') delete query.view
  if (tab === defaultTab) delete query.tab
  else query.tab = tab
  return query
}
