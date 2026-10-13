import { formatMentionToken } from './mention_token.js'

const toResourceItem = (type, { value, label, extra = {} } = {}) => {
  if (!value && !label) return null
  const resolvedValue = value || label
  return {
    value: resolvedValue,
    label: label || resolvedValue,
    type,
    tokenLabel: formatMentionToken(type, label || resolvedValue),
    ...extra
  }
}

export const buildMentionResourceItems = (mention = {}) => {
  const { knowledgeBases = [], mcps = [], skills = [], subagents = [] } = mention

  return {
    knowledgeBases: knowledgeBases
      .map((kb) =>
        toResourceItem('knowledge', {
          value: kb.name,
          label: kb.name,
          extra: { description: kb.description || '', resourceId: kb.kb_id }
        })
      )
      .filter(Boolean),
    mcps: mcps
      // Agent 配置选项已经由后端按使用权过滤；提及项不重复要求目录接口的权限字段。
      .filter((m) => m.enabled !== false && (!m.permission || ['edit', 'manage'].includes(m.permission)))
      .map((m) =>
        toResourceItem('mcp', {
          value: m.slug || m.value || m.id || m.name,
          label: m.name || m.label,
          extra: { description: m.description || '' }
        })
      )
      .filter(Boolean),
    skills: skills
      .map((s) =>
        toResourceItem('skill', {
          value: s.slug || s.value || s.id || s.name,
          label: s.name || s.label,
          extra: { description: s.description || '' }
        })
      )
      .filter(Boolean),
    subagents: subagents
      .map((s) =>
        toResourceItem('subagent', {
          value: s.id || s.value || s.slug || s.name,
          label: s.name || s.label,
          extra: { description: s.description || '' }
        })
      )
      .filter(Boolean)
  }
}
