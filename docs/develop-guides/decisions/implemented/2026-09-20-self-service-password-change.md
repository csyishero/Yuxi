# 用户自助修改本人密码

状态：implemented
类型：feature
Owner：backend/package/yuxi/services/auth_service.py

## 问题

当前只有管理员更新用户资料的接口能够写入密码。已登录用户无法在账户设置中校验当前密码并修改本人的登录密码，只能联系管理员处理。

本次只增加用户修改本人密码的能力，不改变管理员现有的用户管理与密码更新行为，不增加数据库字段，也不承诺立即吊销其他设备已签发的 JWT。

## 决策

- 新增只接受 `current_password` 和 `new_password` 的 `PUT /api/auth/password` 接口，目标用户只能来自当前认证上下文。
- 业务服务校验当前密码、拒绝复用原密码、更新 Argon2 密码摘要，并把修改事实与操作日志放在同一事务提交。
- 账户设置增加密码表单；成功后清除当前浏览器登录状态并返回登录页。
- 管理员用户管理接口、角色规则和数据库 Schema 保持不变。

## 后果

- 普通用户、部门管理员和超级管理员均可在已登录状态下修改本人的密码；这不会改变管理员替其他用户重置密码的既有能力。
- 修改接口不接受用户 ID，不能借此修改其他账户。
- 当前浏览器在修改成功后退出登录；其他设备已经签发的 JWT 仍按原过期时间有效。
- 本次不需要数据库迁移。

## 替代方案

- 继续只允许管理员修改：无法满足用户自助维护凭据的需求。
- 扩展现有个人资料接口：会把低风险资料编辑和需要再次认证的密码操作混为同一契约。
- 同时引入全设备令牌失效：需要持久化令牌版本或密码变更时间并迁移数据库，超出本次范围。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 结果 |
|---|---|---|---|---|---|
| 当前用户可以用原密码修改本人的密码 | 接口仍要求管理员或修改了错误用户 | `auth_service.py` 与认证依赖 | `pytest test/unit/services/test_auth_service.py test/unit/server/test_auth_password_validation.py -q` | 原密码错误和复用原密码均被拒绝 | Passed（16 tests） |
| 密码与审计记录保持同一事务 | 审计失败但密码仍提交 | `auth_service.py` | 同上 | 模拟审计失败，确认执行 rollback 且不 commit | Passed |
| 管理员现有能力不变 | 复用或改写管理员更新接口 | `auth_router.py` 现有 `/users/{user_id}` | 代码差异审查 | 新接口没有目标用户 ID，原管理员接口未修改 | Passed |
| 账户设置提供本人密码入口且不泄露密码 | 按钮没有触发处理函数，或密码进入日志、状态和错误上下文 | `AccountSettingsComponent.vue`、`auth_api.js` | `node --test test/unit/api_boundary.test.js`、`pnpm run lint:check`、`pnpm run build` | 按钮显式绑定处理函数；API 请求只发送本人改密字段；错误日志只记录状态码 | Passed（14 tests；lint/build passed） |
| 真实 HTTP 链路中旧密码失效且新密码可登录 | 运行服务与数据库契约不一致 | `test/integration/api/test_auth_router.py` | `pytest ... -k standard_user_can_change_own_password_with_current_password` | 错误当前密码后旧密码仍可登录 | Not run（环境未配置 `TEST_USERNAME` / `TEST_PASSWORD`，用例被测试框架跳过） |

## 风险

- 当前 JWT 无持久化版本；本次只退出当前浏览器，其他设备的既有令牌会按原过期时间继续有效。
- OIDC 创建的账户使用随机本地密码；不知道当前本地密码的用户无法通过该接口修改，OIDC 凭据仍由身份提供方管理。
- 账户设置真实页面需要合法登录账号；本轮已完成构建与 API 边界验证，登录后的页面视觉仍需在具备测试账号的环境复验。
