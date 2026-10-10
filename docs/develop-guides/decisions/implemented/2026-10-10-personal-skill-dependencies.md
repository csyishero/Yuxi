# 个人 Skill 依赖配置

状态：implemented
类型：feature
Owner：backend/package/yuxi/agents/skills/service.py

## 问题

个人 Skill 的 `SKILL.md` 可以声明工具、MCP 和 Skill 依赖，但有效 Skill 装配清空这三项；详情页也隐藏依赖配置。个人 Skill 提交共享审核时，发布快照会保留其依赖，因此个人运行结果和共享发布结果不一致。

## 决策

个人 Skill 使用 `SKILL.md` frontmatter 保存依赖。本人可在详情页选择依赖；后端验证工具、已启用 MCP 和可见共享 Skill 后安全写入本人工作区。有效 Skill 装配读取这些依赖，运行时只接纳当前仍存在的工具、已启用 MCP 和可访问 Skill。审核通过的共享发布版本保持不可原地修改；个人 Skill 更新后重新提交共享申请。文件保存由 `skills/service.py` 管理，运行时可用性由 `skills/runtime.py` 决定，HTTP 身份与页面交互分别由 `skill_router.py` 和 `SkillDetailView.vue` 管理。

## 替代方案

- 只开放界面，不修正运行时：配置显示与实际能力不符。
- 新增数据库依赖表：引入不必要的 schema 迁移和双份状态。
- 允许修改已发布共享依赖：绕过现有审核快照边界。

## 后果

个人 Skill 的依赖由本人工作区文件拥有，不引入数据库 schema。直接编辑 frontmatter 声明的失效依赖会显示在原始文件中，但运行时会过滤；已发布共享版本仍由审核快照拥有，其依赖需要重新申请才能更新。

## 验证

- 个人 frontmatter、运行时过滤与 HTTP 文件写入相关 unit/integration：26 passed。
- 个人 Skill 的无效工具依赖返回 400 且文件未变；跨用户编辑返回 404。
- 前端 ESLint 与 Vite build：通过；本地 Yuxi 的个人 Skill 页面显示“配置 → 运行依赖”，工具列表可选择。
- 已发布共享版本原地更新拒绝沿用 `update_skill_dependencies` 的现有边界；本变更未新增该边界的独立复测。
