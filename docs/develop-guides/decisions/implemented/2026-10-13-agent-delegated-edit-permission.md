# 智能体委托编辑权限

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/permissions/resource_permission.py

## 问题

智能体需要分别表达配置编辑与授权管理。普通用户的可授权范围只有指定用户；把管理员设置的全局或部门范围交给可编辑的共享表单，会强制转换范围，造成显示失真及错误提交。

## 决策

智能体增加独立的共享编辑范围。普通用户通过共享范围取得的最高权限为编辑：可以修改名称、描述、图标及其有权配置的运行字段，不能修改共享范围或删除智能体。创建人继续管理自己的智能体，管理员的既有管理授权仍有效。已有管理范围命中的普通用户按编辑权限解释，避免现存委托继续授予删除能力。

编辑页按持久化值展示共享范围；无权修改共享范围时提供只读摘要。修改非共享配置时不提交 `share_config`。编辑与删除由后端不同权限检查最终执行。

普通创建人持有管理员设置的全局或部门授权时，默认保留范围，并提供显式重设入口。点击重设把草稿改为仅自己可读取、清空编辑和管理范围，保存后生效。受委托编辑者没有此入口。

## 替代方案

- 只修复表单转换：仍让受委托的普通用户删除智能体，不能满足权限要求。
- 全面迁移现有管理范围：需要解析已有全局、部门和指定用户的混合授权，迁移风险高；运行时解释可在不改写数据的情况下收敛权限。

## 后果

历史管理范围中的普通用户会立即失去修改共享和删除能力；这是明确的权限收敛。若将来要允许受委托编辑者进一步转授权，需要单独定义可授权对象与后端校验。

Skill 和知识库使用各自的权限解析器，其授权规则保持不变。共享表单的编辑权限标题允许由智能体指定；知识库能力策略仅在知识库模式下提交。

## 验证

- 后端相关单元测试：在 `backend/` 执行 `.venv/bin/python -m pytest test/unit/repositories/test_agent_repository.py test/unit/permissions/test_resource_permission.py test/unit/routers/test_subagent.py -q`，54 项通过。覆盖读取、显式编辑、历史全局和部门管理范围、创建人管理及拒绝共享修改。
- 真实 HTTP：隔离 PostgreSQL、Redis 和完整 FastAPI app 使用当前源码，容器内执行 `python /verify/run_http.py`。临时 runner 加载 `test_agent_config_resource_authorization.py` 的 3 个原测试函数，提供真实 HTTP 认证 fixture 并执行原断言，全部通过；配置、共享、撤权和并发保存均由 PostgreSQL 回读确认。未运行原 pytest autouse cleanup，以免触碰无关沙盒。空库内置 Skill 初始化存在旧共享格式问题，验证前仅对隔离库 3 条内置 Skill 规范化为 v2；此初始化路径不在通过范围内。测试资源、容器和网络均已清理。
- 前端组件回归：`node --test test/unit/agentDelegatedPermissions.test.js` 通过。使用原组件模板验证协管用户无共享表单或重设入口，创建人可点击真实重设按钮、生成明确替换授权的请求，重新打开恢复未保存范围。已有配置保存、Skill 选择和知识库权限测试通过。
- 前端全量 unit 已执行，知识库评估页面原有测试仍要求 `canManageDatabase`，实际源码使用 `canConfigureDatabase`，该未修改断言失败；不能声明全量通过。
- ESLint、Ruff check、前端构建、文档构建及 `git diff --check` 通过。格式检查仍检出相关文件中未修改行的既有排版差异。
- `python3 -m unittest scripts.test_verify_engineering_contracts`：62 项通过。`python3 scripts/verify_engineering_contracts.py`：已有两份 decision 文档存在 4 个元数据或 Owner 路径错误，gate 未通过。
- 独立审查指出的创建人重设入口及测试独立预期值问题已修复。真实浏览器验证与截图未完成：浏览器工具无法验证管理员安全策略并拒绝访问本机测试页。
