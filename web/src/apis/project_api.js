import { apiDelete, apiGet, apiPost, apiPut, buildQuery } from './base'

export const projectApi = {
  getProjects: () => apiGet('/api/projects'),

  createProject: ({ requestId, name }) =>
    apiPost('/api/projects', {
      request_id: requestId,
      name,
      workdir: { mode: 'managed' }
    }),

  promoteConversation: (threadId, name) =>
    apiPost(`/api/projects/from-conversation/${encodeURIComponent(threadId)}`, { name }),

  renameProject: (projectId, name) => apiPut(`/api/projects/${projectId}`, { name }),

  deleteProject: (projectId, { deleteWorkdir = false } = {}) =>
    apiDelete(`/api/projects/${projectId}?${buildQuery({ delete_workdir: deleteWorkdir })}`),

  getWorkdirDeletion: (projectId) => apiGet(`/api/projects/${projectId}/workdir-deletion`),

  listWorkdirDeletions: () => apiGet('/api/projects/workdir-deletions'),

  retryWorkdirDeletion: (projectId) =>
    apiPost(`/api/projects/${projectId}/workdir-deletion/retry`, {})
}
