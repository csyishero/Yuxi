export const skillCategories = [
  { value: 'office_tools', label: '办公工具' },
  { value: 'office_efficiency', label: '办公效率' },
  { value: 'office_automation', label: '办公自动化' },
  { value: 'data_processing', label: '数据处理' },
  { value: 'data_analysis', label: '数据分析' },
  { value: 'finance_analysis', label: '财务分析' },
  { value: 'business_review', label: '经营复盘' },
  { value: 'project_management', label: '项目管理' },
  { value: 'legal_compliance', label: '法律合规' },
  { value: 'development_tools', label: '开发工具' },
  { value: 'other', label: '其它' }
]

const labels = Object.fromEntries(skillCategories.map(({ value, label }) => [value, label]))

/** 将历史未分类技能放入“其它”，避免它们从目录消失。 */
export const skillCategory = (value) => labels[value] ? value : 'other'
/** 返回用户可读的技能方向名称。 */
export const skillCategoryLabel = (value) => labels[skillCategory(value)]
