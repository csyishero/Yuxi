/** 管理能力以服务端的权限计算结果为准，缺失字段不能视为授权。 */
export const canManageSkillRecord = (skill) => skill?.can_manage === true

/** 部门管理员通过所属部门继承的管理权，不能调整 Skill 的受众部门。 */
export const isDepartmentManagedSkill = (skill) =>
  canManageSkillRecord(skill) && skill?.department_managed === true

/** 发布入口仅属于本人的个人原件；本人目录的旧记录可没有所有者字段。 */
export const canPublishPersonalSkill = (skill, viewerUid) =>
  Boolean(viewerUid) && Boolean(skill?.slug) &&
  (skill.sourceScope === 'personal' || skill.source_scope === 'personal') &&
  (!skill.owner_uid || skill.owner_uid === viewerUid)

/** 管理员目录卡缺少权限字段；只有本人个人原件可在此处恢复管理入口。 */
export const personalCatalogSkillForViewer = (skill, viewerUid) => ({
  ...skill,
  sourceType: 'personal',
  sourceScope: 'personal',
  can_manage: Boolean(viewerUid) && skill?.owner_uid === viewerUid
})
