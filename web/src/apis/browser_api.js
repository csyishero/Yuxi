import { apiGet, apiPost } from './base'

/** Browser Extension 与 Gateway 的 Yuxi 后端接口。 */
export const browserApi = {
  getConfig: () => apiGet('/api/browser/config'),
  createPairing: () => apiPost('/api/browser/pairings', {}),
  bindRunAssertion: (runId, assertion) =>
    apiPost(`/api/browser/runs/${encodeURIComponent(runId)}/assertion`, { assertion })
}
