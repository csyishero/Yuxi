const retiredBuiltinSlugs = new Set(['image-gen', 'mysql-reporter'])

/** 合并两种目录，市场发布版本优先占用同名公共卡片。 */
export function combineSkillCards(marketCards, existingCards) {
  const marketSlugs = new Set(marketCards.map((item) => item.slug))
  return [
    ...marketCards.map((item) => ({ ...item, kind: 'market' })),
    ...existingCards
      .filter((item) =>
        item.source_scope !== 'personal' &&
        item.enabled !== false &&
        item.shadowed_by_personal !== true &&
        !marketSlugs.has(item.slug) &&
        (item.source_type !== 'builtin' || !retiredBuiltinSlugs.has(item.slug))
      )
      .map((item) => ({ ...item, kind: 'existing' }))
  ]
}

/** 只在当前用户已获授权的卡片中筛选，个人项不进入公共目录。 */
export function filterSkillCards(cards, query, source) {
  const search = query.trim().toLowerCase()
  return cards.filter((item) => {
    if (search && !`${item.name} ${item.description || ''} ${item.slug}`.toLowerCase().includes(search)) return false
    if (source === 'market') return item.kind === 'market'
    if (source === 'shared') return item.kind === 'existing' && item.source_type !== 'builtin'
    if (source === 'builtin') return item.source_type === 'builtin'
    if (source === 'installed') return item.kind === 'market' && !!item.installed_version
    return true
  })
}

/** 个人空间只展示本人源文件和已安装的市场版本。 */
export function personalSkillItems(existingCards, marketCards, uid) {
  const personal = existingCards.filter((item) =>
    item.source_scope === 'personal' && (!item.owner_uid || item.owner_uid === uid)
  )
  return [
    ...personal.map((item) => ({ ...item, kind: 'personal' })),
    ...marketCards.filter((item) => item.installed_version).map((item) => ({ ...item, kind: 'market' }))
  ]
}
