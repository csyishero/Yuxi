# 知识库读取、编辑与管理三级权限

状态：implemented
类型：feature
Owner：backend/package/yuxi/permissions/resource_permission.py

## 问题

知识库已经返回当前用户的有效资源权限，但只区分 `READ` 与 `MANAGE`。普通用户即使拥有读取权限，也无法进入“知识库 · 技能”中的知识库列表和详情；同时，文档上传、解析、移动、删除与知识库共享、模型、检索配置共用 `MANAGE`，无法把日常文档维护授权给非管理员。

## 决策

- 在资源权限等级中增加 `EDIT`，知识库权限按 `NONE < READ < EDIT < MANAGE` 解析。
- 知识库共享配置可选保存 `edit_scope`；缺少该字段的现有配置保持原有读/管语义。命中编辑范围的普通用户可以获得 `EDIT`，作为知识库贡献者上传、解析和入库文档，但删除能力仍只属于 `MANAGE`。
- 所有登录用户都可以请求“当前用户可访问知识库”列表并打开具备 `READ` 权限的详情；无权限资源不返回。
- 文档读取、预览和检索要求 `READ`；上传、解析、入库以及非删除的目录与文件变更要求 `EDIT`；文件/文件夹删除、知识库配置、共享范围、模型/检索配置、图谱管理、评估与知识库删除保持 `MANAGE`。
- 前端根据后端返回的 `effective_permission` 控制入口、只读标记和操作按钮；后端依赖仍是最终授权边界。

## 替代方案

- 继续只保留 `READ` 与 `MANAGE`：无法向普通用户授予文档维护能力。
- 仅在前端放开按钮：直接调用接口仍会越权或误拒绝，不构成权限边界。
- 把所有可访问知识库都授予 `MANAGE`：会同时开放共享、模型配置和删除能力，权限过大。

## 后果

- 普通用户可以被授权维护文档内容，但不能删除文档、修改共享范围或知识库配置。
- 知识库详情和列表按后端返回的有效权限展示入口；前端隐藏不替代后端授权校验。
- 现有未包含 `edit_scope` 的共享配置继续按读取与管理两级语义工作。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| `READ` 用户能看到并打开共享知识库 | 前端入口、列表 API、详情 API | `web/src/views/ExtensionsView.vue`、`backend/server/routers/knowledge_router.py` | Web unit + router unit | `NONE` 用户列表中不可见、详情返回 403 | Passed |
| 授权普通用户获得 `EDIT` 后能贡献和处理文档，但不能删除或修改知识库配置 | 权限解析、文档路由依赖 | `backend/package/yuxi/permissions/resource_permission.py`、`backend/server/utils/knowledge_permissions.py` | permissions/router unit | `READ` 调用上传接口返回 403；`EDIT` 调用文件删除或知识库更新接口返回 403 | Passed |
| 前端按权限隐藏高权限操作 | 知识库列表与详情组件 | `web/src/views/DataBaseView.vue`、`web/src/views/DataBaseInfoView.vue` | Web unit + build | `READ` 不显示上传/配置，`EDIT` 不显示配置/删除知识库 | Passed |
| 旧的无 `edit_scope` 配置仍可读取 | 共享配置兼容 | `backend/package/yuxi/permissions/resource_permission.py` | permissions unit | 旧配置不会因缺字段解析失败 | Passed |

- 后端权限、知识库路由与 Dashboard 时间测试：35 项通过。
- 前端相关权限与时区测试：25 项通过；前端生产构建通过。
- 前端全量单元测试：338 项通过，1 项 PDF 本地资源测试因受限环境禁止监听 `127.0.0.1` 而未运行成功，与本次权限和时区改动无关。
- 已补充 live API 集成用例，覆盖普通用户只读访问，以及命中 `edit_scope` 后可以贡献内容但不能删除或修改知识库配置；本机 Docker 未配置 `TEST_USERNAME` / `TEST_PASSWORD`，测试框架按约定跳过，未修改现有账号密码。
