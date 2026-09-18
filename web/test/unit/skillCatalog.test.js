import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(
  new URL('../../src/components/extensions/SkillCardList.vue', import.meta.url),
  'utf8'
)

test('Skill 目录移除固定推荐区但保留自主安装入口', () => {
  assert.doesNotMatch(source, /RECOMMENDED_SUITES/)
  assert.doesNotMatch(source, /key: 'recommended'/)
  assert.doesNotMatch(source, /title: '推荐'/)
  assert.doesNotMatch(source, /SkillSuiteCard/)
  assert.match(source, />远程安装</)
  assert.match(source, />上传 Skill</)
})

test('远程安装不再默认填入推荐仓库', () => {
  assert.match(source, /const remoteInstallForm = reactive\(\{\s*source: ''/)
})

test('前端在 worker 完成退役同步前也隐藏两个已下线内置 Skill', () => {
  assert.match(source, /RETIRED_BUILTIN_SKILL_SLUGS = new Set\(\['image-gen', 'mysql-reporter'\]\)/)
  assert.match(source, /!RETIRED_BUILTIN_SKILL_SLUGS\.has\(skill\.slug\)/)
})
