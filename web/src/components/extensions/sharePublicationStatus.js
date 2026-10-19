/** 审核结果属于历史记录；副本当前状态由服务端按发布身份确认。 */
export function sharedCopyStatusLabel(status) {
  return {
    enabled: '共享副本当前已启用',
    disabled: '共享副本当前已停用',
    missing: '该版本的共享副本已删除或被替换'
  }[status] || '旧发布记录无法核对当前共享副本'
}
