import { apiGet, apiPost, apiPut, apiDelete, apiAdminGet, apiAdminPost } from './base'

const BASE_URL = '/api/system/skills'
const USER_BASE_URL = '/api/skills'
const MARKET_BASE_URL = '/api/skill-market'

export const listMarketSkills = async ({ query = '', sort = 'recent', installedOnly = false, offset = 0, limit = 100 } = {}) => {
  const params = new URLSearchParams({ sort, offset: String(offset), limit: String(limit) })
  if (query.trim()) params.set('query', query.trim())
  if (installedOnly) params.set('installed_only', 'true')
  return apiGet(`${MARKET_BASE_URL}?${params}`)
}

export const getMarketSkill = async (slug) => apiGet(`${MARKET_BASE_URL}/${encodeURIComponent(slug)}`)
export const getMarketSkillTree = async (slug) => apiGet(`${MARKET_BASE_URL}/${encodeURIComponent(slug)}/tree`)
export const getMarketSkillFile = async (slug, path = 'SKILL.md') =>
  apiGet(`${MARKET_BASE_URL}/${encodeURIComponent(slug)}/file?path=${encodeURIComponent(path)}`)
export const installMarketSkill = async (slug) => apiPost(`${MARKET_BASE_URL}/${encodeURIComponent(slug)}/install`)
export const upgradeMarketSkill = async (slug) => apiPost(`${MARKET_BASE_URL}/${encodeURIComponent(slug)}/upgrade`)
export const toggleMarketSkillLike = async (slug) => apiPost(`${MARKET_BASE_URL}/${encodeURIComponent(slug)}/like`)
export const submitMarketSkill = async (slug, applicationReason) =>
  apiPost(`${MARKET_BASE_URL}/personal/${encodeURIComponent(slug)}/submit`, { application_reason: applicationReason })
export const listMarketRequests = async () => apiGet(`${MARKET_BASE_URL}/requests`)
export const reviewMarketSkill = async (requestId, approved, scope, note) =>
  apiAdminPost(`${MARKET_BASE_URL}/requests/${encodeURIComponent(requestId)}/${approved ? 'approve' : 'reject'}`, {
    scope,
    note
  })

export const listSkills = async () => {
  return apiGet(BASE_URL)
}

export const listSkillCards = async ({ refreshPersonal = false, audienceSearch, audienceDepartmentId } = {}) => {
  const params = new URLSearchParams()
  if (refreshPersonal) params.set('refresh_personal', 'true')
  if (audienceSearch?.trim()) params.set('audience_search', audienceSearch.trim())
  if (audienceDepartmentId) params.set('audience_department_id', String(audienceDepartmentId))
  return apiGet(`${USER_BASE_URL}${params.size ? `?${params}` : ''}`)
}

export const listPersonalSkillCatalog = async ({ departmentId, ownerSearch, offset = 0 } = {}) => {
  const params = new URLSearchParams({ offset: String(offset) })
  if (departmentId) params.set('department_id', String(departmentId))
  if (ownerSearch?.trim()) params.set('owner_search', ownerSearch.trim())
  return apiGet(`${USER_BASE_URL}/personal-catalog?${params}`)
}

export const getPersonalSkillCatalogFile = async (ownerUid, slug, path) => {
  return apiGet(
    `${USER_BASE_URL}/personal-catalog/${encodeURIComponent(ownerUid)}/${encodeURIComponent(slug)}/file?path=${encodeURIComponent(path)}`
  )
}

export const publishPersonalSkillDirectly = async (ownerUid, slug, readScope, note = '') => {
  return apiPost(
    `${USER_BASE_URL}/personal-catalog/${encodeURIComponent(ownerUid)}/${encodeURIComponent(slug)}/publish`,
    { read_scope: readScope, note }
  )
}

export const listAccessibleSkills = async () => {
  return apiGet(`${USER_BASE_URL}/accessible`)
}

export const prepareSkillUpload = async (file) => {
  const formData = new FormData()
  formData.append('file', file)
  return apiPost(`${USER_BASE_URL}/import/prepare`, formData)
}

export const listRemoteSkills = async (source) => {
  return apiPost(`${USER_BASE_URL}/remote/list`, { source })
}

export const prepareRemoteSkills = async (payload) => {
  return apiPost(`${USER_BASE_URL}/remote/prepare`, payload)
}

export const searchRemoteSkills = async (query) => {
  return apiPost(`${USER_BASE_URL}/remote/search`, { query })
}

export const confirmSkillInstallDraft = async (draftId, shareConfig, slugs) => {
  return apiPost(`${USER_BASE_URL}/install-drafts/${encodeURIComponent(draftId)}/confirm`, {
    share_config: shareConfig,
    slugs
  })
}

export const confirmPersonalSkillInstallDraft = async (draftId, slugs) => {
  return apiPost(
    `${USER_BASE_URL}/personal/install-drafts/${encodeURIComponent(draftId)}/confirm`,
    {
      slugs
    }
  )
}

export const discardSkillInstallDraft = async (draftId) => {
  return apiDelete(`${USER_BASE_URL}/install-drafts/${encodeURIComponent(draftId)}`)
}

export const getSkillDependencyOptions = async (slug) => {
  const query = slug ? `?slug=${encodeURIComponent(slug)}` : ''
  return apiGet(`${BASE_URL}/dependency-options${query}`)
}

export const listBuiltinSkills = async () => {
  return apiAdminGet(`${BASE_URL}/builtin`)
}

export const syncBuiltinSkills = async () => {
  return apiAdminPost(`${BASE_URL}/builtin/sync`)
}

export const getSkillTree = async (slug) => {
  return apiGet(`${BASE_URL}/${encodeURIComponent(slug)}/tree`)
}

export const getSkillFile = async (slug, path) => {
  return apiGet(`${BASE_URL}/${encodeURIComponent(slug)}/file?path=${encodeURIComponent(path)}`)
}

export const getPersonalSkillFile = async (slug, path) => {
  return apiGet(
    `${USER_BASE_URL}/personal/${encodeURIComponent(slug)}/file?path=${encodeURIComponent(path)}`
  )
}

export const getPersonalSkillTree = async (slug) => {
  return apiGet(`${USER_BASE_URL}/personal/${encodeURIComponent(slug)}/tree`)
}

export const createPersonalSkillFile = async (slug, payload) => {
  return apiPost(`${USER_BASE_URL}/personal/${encodeURIComponent(slug)}/file`, payload)
}

export const updatePersonalSkillFile = async (slug, payload) => {
  return apiPut(`${USER_BASE_URL}/personal/${encodeURIComponent(slug)}/file`, payload)
}

export const deletePersonalSkillFile = async (slug, path) => {
  return apiDelete(`${USER_BASE_URL}/personal/${encodeURIComponent(slug)}/file?path=${encodeURIComponent(path)}`)
}

export const exportPersonalSkill = async (slug) => {
  return apiGet(`${USER_BASE_URL}/personal/${encodeURIComponent(slug)}/export`, {}, true, 'blob')
}

export const createSkillFile = async (slug, payload) => {
  return apiPost(`${BASE_URL}/${encodeURIComponent(slug)}/file`, payload)
}

export const updateSkillFile = async (slug, payload) => {
  return apiPut(`${BASE_URL}/${encodeURIComponent(slug)}/file`, payload)
}

export const updateSkillDependencies = async (slug, payload) => {
  return apiPut(`${BASE_URL}/${encodeURIComponent(slug)}/dependencies`, payload)
}

export const updatePersonalSkillDependencies = async (slug, payload) => {
  return apiPut(`${USER_BASE_URL}/personal/${encodeURIComponent(slug)}/dependencies`, payload)
}

export const updateSkillShareConfig = async (slug, shareConfig) => {
  return apiPut(`${BASE_URL}/${encodeURIComponent(slug)}/share-config`, {
    share_config: shareConfig
  })
}

export const updateSkillEnabled = async (slug, enabled) => {
  return apiPut(`${BASE_URL}/${encodeURIComponent(slug)}/enabled`, { enabled })
}

export const deleteSkillFile = async (slug, path) => {
  return apiDelete(`${BASE_URL}/${encodeURIComponent(slug)}/file?path=${encodeURIComponent(path)}`)
}

export const exportSkill = async (slug) => {
  return apiGet(`${BASE_URL}/${encodeURIComponent(slug)}/export`, {}, true, 'blob')
}

export const deleteSkill = async (slug) => {
  return apiDelete(`${BASE_URL}/${encodeURIComponent(slug)}`)
}

export const deletePersonalSkill = async (slug) => {
  return apiDelete(`${USER_BASE_URL}/personal/${encodeURIComponent(slug)}`)
}

export const submitSkillShareRequest = async (slug, applicationReason) => {
  return apiPost(`${USER_BASE_URL}/personal/${encodeURIComponent(slug)}/share-request`, {
    application_reason: applicationReason
  })
}

export const listSkillShareRequests = async () => {
  return apiGet(`${USER_BASE_URL}/share-requests`)
}

export const getSkillShareSnapshot = async (requestId, path = 'SKILL.md') => {
  return apiGet(
    `${USER_BASE_URL}/share-requests/${encodeURIComponent(requestId)}/snapshot?path=${encodeURIComponent(path)}`
  )
}

export const getSkillShareSnapshotTree = async (requestId) => {
  return apiGet(`${USER_BASE_URL}/share-requests/${encodeURIComponent(requestId)}/tree`)
}

export const exportSkillShareSnapshot = async (requestId) => {
  return apiGet(
    `${USER_BASE_URL}/share-requests/${encodeURIComponent(requestId)}/export`,
    {},
    true,
    'blob'
  )
}

export const approveSkillShareRequest = async (requestId, readScope, note = '') => {
  return apiAdminPost(`${USER_BASE_URL}/share-requests/${encodeURIComponent(requestId)}/approve`, {
    read_scope: readScope,
    note
  })
}

export const rejectSkillShareRequest = async (requestId, note = '') => {
  return apiAdminPost(`${USER_BASE_URL}/share-requests/${encodeURIComponent(requestId)}/reject`, {
    note
  })
}

export const deleteSkillsBatch = async (slugs) => {
  return apiPost(`${BASE_URL}/delete-batch`, { slugs })
}

export const skillApi = {
  listSkills,
  listSkillCards,
  listPersonalSkillCatalog,
  getPersonalSkillCatalogFile,
  publishPersonalSkillDirectly,
  listAccessibleSkills,
  prepareSkillUpload,
  listRemoteSkills,
  prepareRemoteSkills,
  searchRemoteSkills,
  confirmSkillInstallDraft,
  confirmPersonalSkillInstallDraft,
  discardSkillInstallDraft,
  getSkillDependencyOptions,
  listBuiltinSkills,
  syncBuiltinSkills,
  getSkillTree,
  getSkillFile,
  getPersonalSkillFile,
  getPersonalSkillTree,
  createPersonalSkillFile,
  updatePersonalSkillFile,
  deletePersonalSkillFile,
  exportPersonalSkill,
  createSkillFile,
  updateSkillFile,
  updateSkillDependencies,
  updatePersonalSkillDependencies,
  updateSkillShareConfig,
  updateSkillEnabled,
  deleteSkillFile,
  exportSkill,
  deleteSkill,
  deletePersonalSkill,
  submitSkillShareRequest,
  listSkillShareRequests,
  getSkillShareSnapshot,
  getSkillShareSnapshotTree,
  exportSkillShareSnapshot,
  approveSkillShareRequest,
  rejectSkillShareRequest,
  deleteSkillsBatch
}

export default skillApi
