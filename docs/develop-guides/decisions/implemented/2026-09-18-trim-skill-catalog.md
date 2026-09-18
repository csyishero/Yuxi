# 精简 Skill 展示与内置能力

状态：implemented
类型：feature
Owner：backend/package/yuxi/agents/skills/buildin/__init__.py

## 问题

Skill 页面同时展示固定推荐套件、个人 Skill、共享 Skill 和五个内置 Skill。当前部署不需要固定推荐位，也不再提供内置图片生成和 MySQL 报表能力；仅在前端隐藏会让旧数据库记录在其它入口和运行时继续生效，并可能在刷新或重启后重新出现。

## 决策

- 移除 Skill 页面固定的“推荐”分组及其 MiniMax、Skill 能力与进化套件数据；远程安装、全局搜索和上传入口继续保留。
- 内置注册表移除 `image-gen` 和 `mysql-reporter`，保留 `knowledge-base`、`deep-research` 与 `html-preview`。
- 前端在 worker 完成退役同步前也过滤这两个旧内置记录，确保滚动发布期间不会短暂重新展示。
- worker 启动或管理员手动同步内置 Skill 时，以代码注册表为准清理已退出注册表的内置记录、共享源目录和用户只读投影；上传、远程及个人 Skill 不参与清理。
- 删除两个退役 Skill 的仓库内置文件和仅服务于它们的单元测试；确定性 E2E 改用保留的 `knowledge-base` Skill 验证预加载与工具调用链。

## 后果

- Skill 页面不再包含运营推荐区，远程安装对话框也不再默认填入某个推荐仓库。
- `image-gen` 与 `mysql-reporter` 不再作为平台内置能力提供，现有环境会在下一次 worker 初始化时完成退役清理。
- 如仍需图片生成或 MySQL 报表能力，可以通过上传或远程安装部署方审核过的替代 Skill。
- 其它三个内置 Skill 的同步、权限、只读投影和启停行为保持不变。

## 替代方案

- 只在前端过滤两个卡片：旧记录仍会进入运行时资源快照，不能满足能力下线要求，拒绝采用。
- 清空全部内置 Skill：会同时移除知识库、深度研究和 HTML 预览能力，超出本次范围，拒绝采用。
- 关闭两个 Skill 而不退役：管理员仍能看到并重新启用，不符合精简产品目录的目标。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 结果 |
|---|---|---|---|---|---|
| 推荐区不再渲染 | 固定推荐套件仍出现在页面 | `web/src/components/extensions/SkillCardList.vue` | `node --test test/unit/skillCatalog.test.js test/unit/skill_detail_layout.test.js`、`vite build` | 远程安装与上传入口必须继续存在 | Passed |
| 仅退役两个指定内置 Skill | 误删其它内置或保留退役注册项 | `backend/package/yuxi/agents/skills/buildin/__init__.py` | `pytest backend/test/unit/services/test_skill_service.py -q` | `knowledge-base`、`deep-research`、`html-preview` 仍存在 | Passed |
| 旧部署数据随同步安全清理 | 旧卡片重现、投影残留或误删自定义 Skill | `backend/package/yuxi/agents/skills/service.py` | 同一 Skill 服务测试中的退役清理用例 | 上传 Skill 与仍注册内置 Skill 不被删除 | Passed |

## 风险

- 仍引用退役 slug 的历史 Agent 配置不会自动替换为其它能力；运行前应检查依赖这两个 Skill 的自定义 Agent。
- worker 未重启且未执行手动内置同步前，旧数据库记录仍可能短暂存在；前端发布和 worker 初始化应在同一发布窗口完成。
