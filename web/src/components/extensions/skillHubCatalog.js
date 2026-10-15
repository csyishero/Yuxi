import { skillCategory } from './skillCategories.js'

const retiredBuiltinSlugs = new Set(['image-gen', 'mysql-reporter'])

/** 合并当前市场页与已授权的共享目录，保留筛选所需的共享候选。 */
export function combineSkillCards(marketCards, existingCards) {
  const publicExisting = existingCards.filter((item) =>
    item.source_scope !== 'personal' &&
    item.enabled !== false &&
    item.shadowed_by_personal !== true &&
    (item.source_type !== 'builtin' || !retiredBuiltinSlugs.has(item.slug))
  )
  const sharedByMarketSlug = new Map()
  for (const item of publicExisting) {
    if (!item.related_market_slug) continue
    const current = sharedByMarketSlug.get(item.related_market_slug)
    if (!current || Number(item.id) > Number(current.id)) sharedByMarketSlug.set(item.related_market_slug, item)
  }
  return [
    ...marketCards.map((item) => ({ ...item, kind: 'market', shared_available_slug: sharedByMarketSlug.get(item.slug)?.slug || null })),
    ...publicExisting.map((item) => ({ ...item, kind: 'existing' }))
  ]
}

/** 按当前查询选一个共享代表，仅在同页匹配的市场卡存在时由市场替代。 */
export function filterSkillCards(cards, query, source, category = 'all') {
  const search = query.trim().toLowerCase()
  const matching = cards.filter((item) => {
    if (search && !`${item.name} ${item.description || ''} ${item.slug}`.toLowerCase().includes(search)) return false
    if (category !== 'all' && skillCategory(item.category) !== category) return false
    return true
  })
  const matchingMarketSlugs = new Set(matching.filter((item) => item.kind === 'market').map((item) => item.slug))
  const latestShared = new Map()
  for (const item of matching) {
    if (item.kind !== 'existing' || !item.related_market_slug) continue
    const current = latestShared.get(item.related_market_slug)
    if (!current || Number(item.id) > Number(current.id)) latestShared.set(item.related_market_slug, item)
  }
  return matching.filter((item) => {
    if (source === 'market' && item.kind !== 'market') return false
    if (source === 'shared' && (item.kind !== 'existing' || item.source_type === 'builtin')) return false
    if (source === 'builtin' && item.source_type !== 'builtin') return false
    if (source === 'installed' && (item.kind !== 'market' || !item.installed_version)) return false
    if (item.kind === 'existing' && item.related_market_slug) {
      if (latestShared.get(item.related_market_slug) !== item) return false
      if (source === 'all' && matchingMarketSlugs.has(item.related_market_slug)) return false
    }
    if (source === 'all' && item.kind === 'existing' && matchingMarketSlugs.has(item.slug)) return false
    return true
  })
}

/** 按作者及个人原件 slug 查找当前用户自己的市场来源技能。 */
export function ownPersonalSkill(marketCard, existingCards, uid) {
  if (!uid || marketCard.owner_uid !== uid || !marketCard.own_personal_slug) return null
  return existingCards.find((item) =>
    item.source_scope === 'personal' &&
    item.slug === marketCard.own_personal_slug &&
    (!item.owner_uid || item.owner_uid === uid)
  ) || null
}

/** 个人空间只展示本人源文件和已安装的市场版本。 */
export function personalSkillItems(existingCards, marketCards, uid) {
  const personal = existingCards.filter((item) =>
    item.source_scope === 'personal' && (!item.owner_uid || item.owner_uid === uid)
  )
  const ownInstalled = new Map(marketCards
    .filter((item) => item.installed_version && ownPersonalSkill(item, personal, uid))
    .map((item) => [item.own_personal_slug, item]))
  return [
    ...personal.map((item) => {
      const installed = ownInstalled.get(item.slug)
      return { ...item, kind: 'personal', market_install: installed || null, market_installed_version: installed?.installed_version || null, market_slug: installed?.slug || null, market_visible: installed?.visible !== false }
    }),
    ...marketCards.filter((item) => item.installed_version && !ownInstalled.has(item.own_personal_slug)).map((item) => ({ ...item, kind: 'market' }))
  ]
}

/** 个人空间按来源列出本人可使用的技能；全部视图只保留同源技能的一个入口。 */
export function personalSpaceSkillGroups(existingCards, marketCards, uid) {
  const privateItems = personalSkillItems(existingCards, marketCards, uid)
  const publicItems = filterSkillCards(combineSkillCards([], existingCards), '', 'all')
  const sharedItems = publicItems.filter((item) => item.source_type !== 'builtin')
  const systemItems = publicItems.filter((item) => item.source_type === 'builtin')
  const installedMarketSlugs = new Set(privateItems.flatMap((item) => {
    if (item.kind === 'market') return item.visible === false ? [] : [item.slug]
    return item.market_slug && item.market_visible !== false ? [item.market_slug] : []
  }))
  return {
    all: [
      ...privateItems,
      ...publicItems.filter((item) => !installedMarketSlugs.has(item.related_market_slug || item.slug))
    ],
    private: privateItems,
    shared: sharedItems,
    system: systemItems
  }
}

/** 对个人空间各来源使用同一搜索条件，让分类数量与列表一致。 */
export function searchPersonalSpaceSkillGroups(groups, query) {
  const search = query.trim().toLowerCase()
  return Object.fromEntries(Object.entries(groups).map(([source, items]) => [
    source,
    items.filter((item) => !search || `${item.name || ''} ${item.slug}`.toLowerCase().includes(search))
  ]))
}

/** 市场安装在管理页作为只读的本人安装记录，不伪装成可编辑个人原件。 */
export function marketInstallManagementItems(marketCards) {
  return marketCards
    .filter((item) => item.installed_version)
    .map((item) => ({
      ...(item.visible === false
        ? { slug: item.slug, name: item.slug, description: '', installed_version: item.installed_version, visible: false }
        : item),
      kind: 'market_install',
      sourceType: 'market_install',
      sourceScope: 'market_install'
    }))
}
