function timestamp(value) {
  const parsed = Date.parse(value || '')
  return Number.isFinite(parsed) ? parsed : 0
}

function channelHistory(requests) {
  const rows = [...requests].sort((left, right) => timestamp(right.created_at) - timestamp(left.created_at))
  return {
    requests: rows,
    latest: rows[0] || null,
    approved: rows.find((request) => request.status === 'approved') || null
  }
}

function currentMarketField(requests, field) {
  return requests.find((request) => request[field] != null)?.[field] ?? null
}

/** 按个人技能原件汇总作者的真实发布申请。 */
export function buildPublicationGroups({ sharedRequests = [], marketRequests = [], personalSkills = null, ownerUid } = {}) {
  if (!ownerUid) return []

  const sources = new Map(
    (Array.isArray(personalSkills) ? personalSkills : [])
      .filter((skill) => skill?.slug && (skill.owner_uid == null || skill.owner_uid === ownerUid))
      .map((skill) => [skill.slug, skill])
  )
  const bySlug = new Map()

  for (const [channel, requests] of [['shared', sharedRequests], ['market', marketRequests]]) {
    for (const request of Array.isArray(requests) ? requests : []) {
      if (request?.owner_uid !== ownerUid || !request.personal_slug?.trim()) continue
      if (!bySlug.has(request.personal_slug)) {
        bySlug.set(request.personal_slug, { slug: request.personal_slug, shared: [], market: [] })
      }
      bySlug.get(request.personal_slug)[channel].push(request)
    }
  }

  return [...bySlug.values()].map(({ slug, shared: sharedRows, market: marketRows }) => {
    const shared = channelHistory(sharedRows)
    const market = channelHistory(marketRows)
    const skill = sources.get(slug) || null
    const newestRequest = timestamp(shared.latest?.created_at) >= timestamp(market.latest?.created_at)
      ? shared.latest || market.latest
      : market.latest
    const activity = [...shared.requests, ...market.requests]
      .map((request) => request.reviewed_at || request.created_at)
      .sort((left, right) => timestamp(right) - timestamp(left))[0] || null

    return {
      slug,
      skill,
      sourceAvailable: Array.isArray(personalSkills) ? Boolean(skill) : null,
      name: skill?.name || newestRequest?.name || slug,
      category: skill?.category || newestRequest?.category || null,
      lastActivityAt: activity,
      shared: {
        ...shared,
        version: shared.approved?.published_version ?? null,
        copyStatus: shared.approved?.published_copy_status ?? null
      },
      market: {
        ...market,
        version: currentMarketField(market.requests, 'current_market_version'),
        isPublished: currentMarketField(market.requests, 'existing_market_is_published'),
        scope: currentMarketField(market.requests, 'existing_market_scope'),
        departmentId: currentMarketField(market.requests, 'existing_market_department_id')
      }
    }
  }).sort((left, right) => timestamp(right.lastActivityAt) - timestamp(left.lastActivityAt) || left.slug.localeCompare(right.slug))
}
