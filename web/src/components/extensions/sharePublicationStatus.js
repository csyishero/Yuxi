/** 审核结果属于历史记录；副本当前状态由服务端按发布身份确认。 */
export function sharedCopyStatusLabel(status) {
  return {
    enabled: '当前共享副本：已启用',
    disabled: '当前共享副本：已停用',
    missing: '当前共享副本：已删除或被替换'
  }[status] || '当前共享副本：无法核对'
}

/** 说明旧审核记录缺少副本关联时的核验限制。 */
export function sharedCopyStatusNote(status) {
  return status === 'unknown' || status == null
    ? '这条历史记录没有保存对应的共享副本信息，因此无法判断它现在是否可用。'
    : ''
}
