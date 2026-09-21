import assert from 'node:assert/strict'
import test from 'node:test'

import { buildServiceLink } from '../../src/utils/serviceLinks.js'

test('管理服务链接沿用当前访问 Yuxi 的内网主机', () => {
  const location = {
    origin: 'http://10.20.30.40:3000'
  }

  assert.equal(
    buildServiceLink({ port: '8474', path: '/' }, location),
    'http://10.20.30.40:8474/'
  )
  assert.equal(
    buildServiceLink({ port: '6050', path: '/docs' }, location),
    'http://10.20.30.40:6050/docs'
  )
})

test('管理服务链接支持域名、HTTPS 和服务路径', () => {
  const location = {
    origin: 'https://yuxi.internal.example'
  }

  assert.equal(
    buildServiceLink({ port: 10091, path: '/webui/' }, location),
    'https://yuxi.internal.example:10091/webui/'
  )
})

test('管理服务链接拒绝无效端口和相对路径', () => {
  const location = {
    origin: 'http://10.20.30.40'
  }

  assert.throws(() => buildServiceLink({ port: '9091x', path: '/' }, location), /端口/)
  assert.throws(() => buildServiceLink({ port: 9091, path: 'webui' }, location), /路径/)
})
