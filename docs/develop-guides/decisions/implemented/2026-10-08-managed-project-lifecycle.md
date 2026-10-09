# 新建项目使用专属目录

状态：implemented
类型：feature
Owner：backend/package/yuxi/services/project_service.py

## 问题

手动新建 Project 曾允许绑定 `projects` 等上层目录。多个 Project 可以共享该目录，对话文件视图因而包含其他项目的文件，删除 Project 时也无法确定文件归属。

## 决策

手动新建 Project 只接受 managed 模式，服务端在当前用户 UserWorkspace 下分配 `projects/<规范化项目名>_<Project ID 前 8 位>[-N]` 并物化目录。分配同时避开已有文件系统条目和数据库已登记路径；若 active 旧项目绑定新目录的祖先或子孙路径，则拒绝创建。项目重命名只更新显示名。implicit Project 沿用时间戳目录。旧 linked Project 继续读取、运行和默认删除；公开入口与内部创建用例均拒绝新 linked 绑定。

“从历史对话添加”入口退出新建流程。新项目不复制、不移动旧对话和旧文件；历史对话留在原 Project 和目录。

删除 Project 默认在同一事务中软删除它与全部 Conversation，并保留文件。用户明确勾选永久删除时，仅允许 managed Project 登记持久 `project_workdir_delete` 任务。提交后发布任务；worker 在用户级目录锁内再次验证目录属于该 Project、没有其他 Project 路径重叠、没有排队请求或未完成 Run，然后使用 no-follow 文件操作删除目录。已删除的旧项目若仅绑定目标目录的上层，在目标项目创建前就已删除，且没有仍在运行、排队或待清理的工作，则不视作重叠；同目录、下层目录、仍活跃或在目标项目创建后才删除的绑定继续阻止清理。取消或超时发生在文件线程执行期间时，worker 等待线程结束才释放目录锁并记录终态。任务状态由当前用户的 Project 接口查询，页面定期从持久任务列表恢复各项目最新的未完成或失败清理，网络中断后继续查找；失败或取消可重试。linked Project 无文件删除选项，也不能通过参数触发目录清理。

## 替代方案

- 保留 linked 作为新建默认方式：已有文件可原地使用，但上层目录误选和共享目录归属问题继续存在。
- 默认 managed 并保留高级 linked 入口：仍允许新项目引用共享目录，界面必须长期解释两种新建语义。
- 删除时一律保留文件：实现简单，但专属目录无法随项目清理。
- 清理时忽略全部已删除项目：旧项目可能与目标目录同时共享过文件，会失去归属保护。

## 后果

- 新项目与旧目录没有文件继承关系。需要旧文件时，用户可在工作区自行复制到新项目目录。
- 目录清理可能在 Project 已删除后失败；数据库任务保留失败状态且不参加通用终态剪枝，文件可能已部分删除，重试按缺失目录幂等完成。清理以 no-follow 方式移除目录内符号链接和特殊文件，不访问链接目标。
- 仍活跃或曾与目标项目同时共享目录的历史 linked 绑定会阻止 managed 目录清理。用户可以取消文件清理选项，先删除 Project 并保留文件。
- Project 的目录和 Viewer 范围更明确；同一用户 Agent 对整个 UserWorkspace 的现有访问边界保持不变。
- 旧的手动 linked 创建及历史目录选择决定由[Project 持久化与新建对话项目选择](./2026-08-22-project-persistence-and-selection.md)记录为历史；旧的默认保留目录决定由[Project 对话分组与生命周期管理](./2026-08-30-project-conversation-sidebar-management.md)记录为历史。

## 验证

本地 Project 路径与 service 单元测试覆盖中文目录名、名称清洗、冲突编号、旧时间戳与 UUID 路径、公开及内部 linked 创建拒绝、旧 linked 清理拒绝、目录归属、重叠绑定、活动执行与 symlink 负向案例。目录重叠回归测试覆盖旧上层绑定先删除后创建时放行，以及活跃、后删除、同路径、下层和仍有未完成 Run 的旧绑定继续拒绝；API integration 用例覆盖旧上层绑定先删除后的项目与文件夹清理，以及旧 Run 仍在执行时的拒绝。前端 API 单元测试、lint 和生产构建覆盖 managed 创建请求及删除选项请求。独立内存型 PostgreSQL 容器中的真实 HTTP 探针回读了 Project 与 Task 行和 POSIX 字节：managed 创建、linked 新建拒绝、祖先绑定拒绝、linked 文件清理拒绝、显式清理、兄弟目录保留、失败状态查询与重试均通过。真实 ARQ worker 投递和交互页面尚未在隔离环境中回读；运行中的生产容器不用于写入测试数据。
