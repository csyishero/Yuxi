export function buildServiceLink(spec, currentLocation = window.location) {
  if (!spec || !currentLocation?.origin) {
    throw new Error('服务地址配置缺失')
  }

  const portText = String(spec.port ?? '').trim()
  const port = Number(portText)
  if (!/^\d+$/.test(portText) || !Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error('服务端口配置无效')
  }

  const path = String(spec.path || '/')
  if (!path.startsWith('/')) {
    throw new Error('服务路径配置无效')
  }

  const target = new URL(currentLocation.origin)
  if (!['http:', 'https:'].includes(target.protocol)) {
    throw new Error('当前页面协议不支持服务跳转')
  }
  target.port = portText
  target.pathname = path
  target.search = ''
  target.hash = ''
  return target.toString()
}
