export const DEFAULT_KNOWLEDGE_BASE_CAPABILITY_POLICY = Object.freeze({
  read: Object.freeze(['view', 'search', 'download']),
  edit: Object.freeze(['upload', 'metadata', 'parse', 'index']),
  manage: Object.freeze([
    'configure',
    'share',
    'grant',
    'delete-document',
    'delete-knowledge-base'
  ])
})

export function normalizeKnowledgeBaseCapabilityPolicy(policy) {
  const source = policy && typeof policy === 'object' ? policy : {}
  return Object.fromEntries(
    Object.entries(DEFAULT_KNOWLEDGE_BASE_CAPABILITY_POLICY).map(([level, defaults]) => {
      const values = Array.isArray(source[level]) ? source[level] : defaults
      const allowed = new Set(defaults)
      return [level, Array.from(new Set(values.filter((value) => allowed.has(value))))]
    })
  )
}

export function getShareConfigLabel(shareConfig) {
  const config = shareConfig || {}
  const readScope = config.version === 2 ? config.read_scope : config
  const editScope = config.edit_scope
  const manageScope = config.manage_scope
  if (config.version === 2 && !config.read_scope && !editScope && !manageScope) return '仅所有者'
  const scopeLabel = (scope) => {
    if (!scope) return '无'
    if (scope.access_level === 'global') return '全局'
    if (scope.access_level === 'department') return `部门(${scope.department_ids?.length || 0})`
    return `用户(${scope.user_uids?.length || 0})`
  }
  if (editScope || manageScope) {
    const parts = [`读${scopeLabel(readScope)}`]
    if (editScope) parts.push(`编${scopeLabel(editScope)}`)
    if (manageScope) parts.push(`管${scopeLabel(manageScope)}`)
    return parts.join(' · ')
  }
  return `只读${scopeLabel(readScope)}`
}

export function isScopeWithinParent(childScope, parentScope, userDepartmentByUid = {}) {
  if (!childScope || !parentScope || parentScope.access_level === 'global') return true

  if (parentScope.access_level === 'department' && childScope.access_level === 'user') {
    const parentDepartmentIds = new Set((parentScope.department_ids || []).map(Number))
    return (childScope.user_uids || []).every((uid) => {
      const departmentId =
        userDepartmentByUid instanceof Map
          ? userDepartmentByUid.get(String(uid))
          : userDepartmentByUid[String(uid)]
      const normalizedDepartmentId = Number(departmentId)
      return Number.isFinite(normalizedDepartmentId) && parentDepartmentIds.has(normalizedDepartmentId)
    })
  }

  if (childScope.access_level !== parentScope.access_level) return false
  if (parentScope.access_level === 'user') {
    const parentUserUids = new Set((parentScope.user_uids || []).map(String))
    return (childScope.user_uids || []).every((uid) => parentUserUids.has(String(uid)))
  }
  if (parentScope.access_level === 'department') {
    const parentDepartmentIds = new Set((parentScope.department_ids || []).map(Number))
    return (childScope.department_ids || []).every((id) => parentDepartmentIds.has(Number(id)))
  }
  return true
}
