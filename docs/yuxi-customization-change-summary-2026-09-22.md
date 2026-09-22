# Yuxi 定制改动明细（截至 2026-09-22）

## 1. 文档范围

本文用于交接当前 `test` 分支相对 2026-09-16 上游基线后的定制改动，内容以 Git 历史和当前源码为准，不以聊天记录作为实现事实。

- 对比基线：`9d5b91fa`
- 已提交版本：`17c34b06`（个人密码修改 UI 交互）
- 当前分支：`test`
- 工作区补充：部门编辑表单校验修复尚未提交，见第 22 节
- 已提交代码的净变更规模：181 个文件，约 12,361 行新增、3,076 行删除（不含第 22 节工作区修复）
- 统计范围包含已经合入 `main` 的门户、Langfuse、压测等改造，以及 `test` 分支后续的权限、部署、评估和改密改造

本文只记录代码和配置的行为变化。`.env.prod` 中的具体凭据、内部地址和业务数据不在本文展示。

## 2. 改动总览

| 模块 | 主要功能变化 | 用户或运维影响 | 数据库影响 | 生产部署影响 |
| --- | --- | --- | --- | --- |
| 对话与侧栏 | 续跑消息合并展示；项目对话分批展开 | 审批/续跑不再显示为割裂的新会话；大项目侧栏更轻 | 无 | 重建 Web |
| Compose 部署 | 环境文件、项目名、状态目录和端口可配置；生产源码挂载已移除 | 支持同机多环境；生产镜像成为代码事实来源 | 无 | 更新 Compose 配置并重建容器 |
| 错误处理 | 业务 HTTP 状态码和 `detail` 不再被通用异常覆盖；底层失败不再伪装成功 | 前端可显示真实 400/403/404，未知错误保持通用 500 | 无 | 重建 API/Worker |
| 容器调试 | API 与 Worker 提供独立 DAP 调试入口 | PyCharm 可分别命中路由和 Agent 执行断点 | 无 | 仅调试覆盖文件；生产不暴露端口 |
| Langfuse | 稳定 trace 名称、Run/Thread 关联、环境与发布标签、凭据遮蔽 | 可按 `run_id`、线程和环境排查；减少凭据泄露 | 无 | 重建 API/Worker 并配置环境变量 |
| 压测与 Sandbox 指标 | 记录 8 个 Sandbox 生命周期耗时；新增压测方案 | 可区分模型慢、排队慢和沙盒冷启动慢 | Schema 升到 8，新增 JSONB | 先运行 storage-migrator，再重建 API/Worker/Provisioner |
| Workspace | 首次创建嵌套文件夹时自动创建父路径 | 新工作区第一次建子目录不再失败 | 无 | 重建 API/Worker |
| 门户与品牌 | 联合智擎开屏、登录、侧栏和聊天首页；移除文档中心/GitHub 入口 | 品牌与信息架构统一；原有认证和业务流程保留 | 无 | 重建 Web；部分品牌默认值需重建 API/Worker |
| Skill/MCP 精简 | 移除推荐区、`image-gen`、`mysql-reporter` 和内置 chart MCP | 页面与运行时都不再提供这些内置能力 | 清理旧内置记录，不改 Schema | 重建 Web/API/Worker并重启同步 |
| 知识库权限 | READ/EDIT/MANAGE、部门归属、系统继承、可编辑能力树 | 普通用户可按能力上传/解析/入库；删除与授权可独立控制 | 复用 `share_config` JSON，无结构迁移 | API/Worker/Web 必须同批发布 |
| 图标与细节 | 浏览器标签页替换为联合银行图标；时间统一转上海时区 | 标签页和会话审计时间更符合内网使用 | 无 | 重建 Web |
| Rerank | 请求模型不再强制携带 `max_chunks_per_doc` | 兼容不接受该字段的重排服务 | 无 | 重建 API/Worker |
| 用户改密 | 用户校验旧密码后修改本人密码；弹窗式入口 | 不再依赖管理员；成功后当前浏览器退出 | 无 | 重建 API 和 Web |
| 评估基准 | 可编辑问题/Gold Answer；Gold Chunk 定位真实文件 | 可直接修订题目并打开源文件核对 | 无 | 重建 API 和 Web |
| 内网管理入口 | Neo4j、API Docs、MinIO、Milvus 使用当前访问主机 | 从内网 IP/域名访问时不再跳到用户电脑的 localhost | 无 | 重建 API/Web，按需开放受控端口 |
| 部门编辑 | 管理员账号资料校验仅在创建部门时执行 | 编辑部门名称、描述时不再误报“请输入管理员UID”；原授权限制保留 | 无 | 仅重建 Web |

## 3. 对话续跑与侧栏交互

### 3.1 功能变化

- 具有明确父子 Run 关系的 `resume` 续跑在展示层合并到原处理过程，不再拆成多个看似独立的机器人回答。
- 合并后的耗时使用原 Run 与续跑 Run 的总耗时，消息 key 优先使用稳定的 Run 标识，降低流式刷新中的 DOM 错位。
- 发起续跑后、连接首个流事件前刷新一次已持久化历史，以获得后端记录的续跑关系；刷新失败不会把已创建的 Run 当成创建失败。
- 项目侧栏初始仅显示 5 条对话，点击“展开显示”后每次增加 10 条，避免项目对话很多时一次渲染全部记录。

### 3.2 代码位置

- 展示分组算法：`web/src/utils/conversationProcessGrouping.js`
- 主聊天页面：`web/src/components/AgentChatComponent.vue`
- 线程消息列表：`web/src/components/ThreadMessageList.vue`
- 项目侧栏：`web/src/components/ConversationNavSection.vue`

### 3.3 影响点

- 只改变前端展示和刷新时序，不改变 Run、Message、Thread 的后端状态模型。
- 只有具有明确 `created_by_run_id` 且不包含新用户消息的相邻续跑会被合并，避免把普通新一轮对话错误拼接。

## 4. Docker Compose、环境隔离与生产镜像

### 4.1 环境隔离

- 开发和生产 Compose 分别默认读取 `.env` 与 `.env.prod`，也可通过 `YUXI_ENV_FILE` 切换容器注入文件。
- Compose 插值仍需通过 `--env-file` 指定，`env_file` 与 `--env-file` 解决的是两个不同阶段的问题。
- `COMPOSE_PROJECT_NAME` 控制项目、镜像、网络及动态 Sandbox 名称，`YUXI_STATE_DIR` 控制宿主持久化目录。
- 固定 `container_name` 已移除，使同机多套环境可以由 Compose 项目名隔离。
- API、Web、PostgreSQL、Redis、Neo4j、MinIO、Milvus、Sandbox、OCR 等宿主端口均支持环境变量覆盖。
- MinIO 容器数据路径统一为 `/data`，Neo4j 日志路径统一为 `/logs`。
- Provisioner 使用 `gw_priority` 保留管理网络默认路由，因此部署要求 Docker Engine 28.0+、Compose 2.33.1+。

### 4.2 Sandbox 与发布脚本

- `SANDBOX_ENV_FILE` 可为不同部署选择独立 Sandbox 环境文件。
- 生产 Provisioner 显式使用 Uvicorn 启动命令，避免镜像 CMD 不一致造成启动失败。
- 版本升级脚本适配带 Compose 项目前缀的镜像名和新增的 Provisioner 镜像。

### 4.3 生产源码挂载的最终状态

生产 Compose 曾临时加入：

```yaml
- ./backend/package:/app/package
```

该挂载已经从 API 和 Worker 中移除。当前最终行为是：

- 生产容器只执行镜像内构建时复制的后端代码。
- 修改后端 Python 后，单纯 `restart` 不会生效，必须重新构建 API 镜像并重建 API/Worker 容器。
- 开发 Compose 仍可保留面向开发的源码挂载行为；不要用开发挂载推断生产发布结果。

### 4.4 代码位置

- 开发拓扑：`docker-compose.yml`
- 生产拓扑：`docker-compose.prod.yml`
- 部署说明：[deployment.md](advanced/deployment.md)
- 版本脚本：`scripts/bump-version.sh`
- 部署决策：[compose-environment-isolation.md](develop-guides/decisions/implemented/2026-09-16-compose-environment-isolation.md)

### 4.5 影响点与风险

- 更换项目名但复用同一数据目录会导致两套服务竞争同一数据，必须同时隔离 `COMPOSE_PROJECT_NAME`、`YUXI_STATE_DIR` 和端口。
- `.env.prod` 已加入当前分支，包含多类部署凭据变量。该文件不应发布到公共仓库或发给无关人员；如其中使用过真实密钥，应迁移到服务器私有配置并轮换密钥。

## 5. 后端错误语义与失败可见性

### 5.1 功能变化

- 路由捕获异常时先透传 FastAPI `HTTPException`，保留业务层已经确定的 400、401、403、404、409 等状态码和用户可读 `detail`。
- 未知异常写入服务端日志并返回稳定的通用错误，不再把内部堆栈或凭据相关细节直接透给前端。
- 获取列表、解析文件等路径不再用“HTTP 200 + 空列表/失败消息”掩盖真正的服务异常。
- Milvus 查询、图谱/向量删除、知识库删除失败不再静默吞掉。
- 删除外部向量/图数据成功后才删除 PostgreSQL Chunk 事实，失败时保留可重试元数据。
- 思维导图保存失败或知识库不存在时显式报错，不再仅记录日志后返回成功。

### 5.2 代码位置

- 路由层：`backend/server/routers`
- Milvus 实现：`backend/package/yuxi/knowledge/implementations/milvus.py`
- 思维导图：`backend/package/yuxi/knowledge/utils/mindmap_utils.py`
- 附件服务：`backend/package/yuxi/services/attachment_service.py`

### 5.3 影响点

- 前端可以使用后端 `detail` 展示“文件过大”“无权限”“资源不存在”等具体原因。
- 原来依赖空结果作为失败降级的调用方会收到真实异常，需要按 HTTP 状态处理。
- 删除失败更容易被用户感知，但避免了数据库记录已删、Milvus/Neo4j 残留的不可恢复不一致。

## 6. PyCharm 容器调试

### 6.1 功能变化

- 新增独立 `docker-compose.debug.yml`。
- API 使用 DAP 端口 5678，Worker 使用 5679；两者均通过 `python -m debugpy` 启动。
- 调试配置不启用 Uvicorn reload/watchfiles，防止 IDE 连接监督进程但业务运行在另一个子进程。
- 普通开发和生产 Compose 不暴露调试端口，业务代码也不主动加载调试器。

### 6.2 影响点

- 路由断点需要附加 API；Agent、模型、工具和知识库运行链路断点通常需要附加 Worker。
- 使用 `--wait-for-client` 时，健康检查会在 IDE 附加前暂时保持 starting，这是预期行为。
- 容器 `/app` 与本地 `backend` 需要正确配置路径映射。

详细设计见：[container-dap-debugging.md](develop-guides/decisions/implemented/2026-09-15-container-dap-debugging.md)。

## 7. Langfuse 链路追踪

### 7.1 功能变化

- 普通 AgentRun 使用稳定根名称 `execute-agent-run`，恢复执行使用 `resume-agent-run`。
- `thread_id` 映射为 Langfuse session，opaque `uid` 映射为 user，metadata/tags 包含 Run、Request、Agent、Backend 和操作类型等排障维度。
- 支持 `LANGFUSE_TRACING_ENVIRONMENT` 与 `LANGFUSE_RELEASE`，便于区分开发、生产和发布版本。
- 增加递归 mask，对 Authorization、Cookie、Password、Secret、Token、API Key 等字段遮蔽。
- 用户名、登录账号和部门等个人信息不再写入 Langfuse metadata。
- Langfuse 保持可选观测能力：SDK 缺失、未配置或远端失败不会决定 AgentRun 的业务终态。

### 7.2 代码位置

- Trace 装配：`backend/package/yuxi/services/langfuse_service.py`
- Agent callback 注入：`backend/package/yuxi/agents/base.py`
- Chat 上下文：`backend/package/yuxi/services/chat_service.py`
- 环境模板：`.env.template`
- 使用说明：[langfuse-integration.md](advanced/langfuse-integration.md)

### 7.3 影响点与风险

- 可以在 Yuxi 中拿到 `run_id` 后，通过 trace metadata、session 或确定性 trace ID 定位整个模型/工具/检索链路。
- mask 只默认保护常见凭据键；Prompt、模型输出和工具业务参数仍可能包含业务数据，必须限制 Langfuse 访问、导出和保留周期。
- Docker 访问宿主机自托管 Langfuse 使用 `host.docker.internal`；内网服务器需要配置容器可达地址。

详细设计见：[langfuse-agent-run-tracing.md](develop-guides/decisions/implemented/2026-09-17-langfuse-agent-run-tracing.md)。

## 8. 压测方案与 Sandbox 阶段耗时

### 8.1 压测方案

- 新增 [yuxi-load-testing-plan.md](yuxi-load-testing-plan.md)，覆盖 readiness、鉴权、Agent/Thread 准备、并发请求、SSE 事件、首 Token、总耗时和资源采集。
- 压测按独立 Thread 模拟并发用户，走完整 Agent 流程，不把单纯 HTTP 空接口当作 Agent 性能。
- 报告区分提交耗时、线程队列、首 Run 事件、提交到模型、首 Token 和总耗时。

### 8.2 Sandbox 指标

每个 AgentRun 可记录以下 8 个指标：

- `sandbox_discover_ms`
- `sandbox_create_container_ms`
- `sandbox_create_network_ms`
- `sandbox_wait_ready_ms`
- `sandbox_execute_request_ms`
- `sandbox_release_ms`
- `sandbox_delete_container_ms`
- `sandbox_delete_network_ms`

`sandbox_command_ms` 未加入，因为当前协议不能在不改变命令退出码、信号、引用和超时语义的前提下可靠获得容器内命令净耗时。

### 8.3 代码位置

- 压测入口：`backend/test/performance/load.py`
- Sandbox Provider：`backend/package/yuxi/agents/backends/sandbox/provider.py`
- Provisioner：`docker/sandbox_provisioner/app.py`
- Worker 终态落库：`backend/package/yuxi/services/run_worker.py`
- Run 结果输出：`backend/package/yuxi/services/agent_run_service.py`
- 持久化模型：`backend/package/yuxi/storage/postgres/models_business.py`
- 迁移入口：`backend/package/yuxi/storage_migration.py`

### 8.4 数据和部署影响

- 业务 Schema 升到 8，`agent_runs` 增加非空 JSONB 的 Sandbox timing 投影。
- 升级必须先让 storage-migrator 完成，再启动依赖新列的 API/Worker。
- 未执行或因进程故障未持久化的阶段保持“缺失”，不能解释为 0。
- `sandbox_execute_request_ms` 是 Worker 发请求到 Sandbox 返回完整响应的整体时间，不等于 `sleep 8` 的纯命令耗时；还会包含容器内执行框架和响应传输。

详细设计见：[sandbox-stage-timing.md](develop-guides/decisions/implemented/2026-09-17-sandbox-stage-timing.md)。

## 9. Workspace 首次创建目录

### 9.1 功能变化

Workspace 创建子文件夹时以 `create=True` 打开父路径。首次进入尚不存在的用户工作区并创建嵌套目录时，父目录会先安全创建，不再直接报错。

### 9.2 代码位置

- `backend/package/yuxi/workspace/filesystem.py`

### 9.3 影响点

- 只改变缺失父目录的创建路径，不放宽路径越界校验。
- 生产源码挂载已经移除，因此该修复必须通过重建后端镜像发布。

## 10. 联合智擎门户与品牌 UI

### 10.1 功能变化

- 使用 UI 交付资源重做开屏页、登录页、展开侧栏和新建对话首屏。
- 登录页继续保留首次初始化、账号密码、用户协议、OIDC、账号锁定、健康检查和错误反馈；没有引入静态模拟登录。
- 侧栏继续使用真实路由、Thread、Project、搜索、任务中心和用户数据，只替换视觉和信息层级。
- 默认品牌名称和模型自我介绍统一为“联合智擎”，但保留 Python 包名、环境变量、协议 Header、持久化键和仓库兼容标识。
- 用户菜单移除“文档中心”。
- 主侧栏和设置页移除 GitHub Star 入口及外部请求。
- 设置页保留账户、API Key、环境变量及管理员能力。
- 会话完整时间使用 Asia/Shanghai 展示，后端 UTC 时间会转换为上海时区。

### 10.2 资源和代码位置

- 开屏图：`web/public/united-intelligence-splash.jpg`
- 品牌 Logo：`web/public/united-intelligence-logo.png`
- 开屏页：`web/src/views/HomeView.vue`
- 登录页：`web/src/views/LoginView.vue`
- 全局布局：`web/src/layouts/AppLayout.vue`
- 设置弹窗：`web/src/components/SettingsModal.vue`
- 用户菜单：`web/src/components/UserInfoComponent.vue`
- 聊天首屏：`web/src/components/AgentChatComponent.vue`
- 浅色/暗色 token：`web/src/assets/css/base.css`、`web/src/assets/css/base.dark.css`

### 10.3 影响点

- 业务 API、Store、权限和路由仍由原 Yuxi 实现负责，UI 原型没有成为第二套业务逻辑。
- 开屏图是位图，修改图片内栅格化文案需要重新提供或编辑图片资源。
- Web 资源构建进 Nginx 镜像，生产环境每次修改 Vue/CSS/图片都需要重建 Web 镜像。

详细设计见：[united-intelligence-portal-ui.md](develop-guides/decisions/implemented/2026-09-18-united-intelligence-portal-ui.md)。

## 11. Skill 与 MCP 能力精简

### 11.1 Skill

- Skill 页面移除整个固定“推荐”分组，不再显示 MiniMax 办公文档套件和 Skill 能力与进化套件。
- 远程安装、上传、个人 Skill、共享 Skill 和搜索继续保留。
- 内置 Skill 移除 `image-gen` 与 `mysql-reporter`。
- 保留 `knowledge-base`、`deep-research`、`html-preview`。
- Worker 同步内置 Skill 时会清理已经退出注册表的系统内置记录、源目录和只读投影；用户上传或远程安装的同名能力不按系统记录清理。

### 11.2 MCP

- 内置 `mcp-server-chart` 退出默认注册表。
- 启动同步只删除由 `system` 创建的旧记录，管理员自行创建的同名远程 MCP 不受影响。

### 11.3 代码位置

- 内置 Skill 注册表：`backend/package/yuxi/agents/skills/buildin/__init__.py`
- Skill 同步：`backend/package/yuxi/agents/skills/service.py`
- Skill 页面：`web/src/components/extensions/SkillCardList.vue`
- MCP 同步：`backend/package/yuxi/agents/mcp/service.py`

### 11.4 影响点

- 历史 Agent 如果显式引用退役 slug，不会自动替换为其它 Skill，升级前应检查自定义 Agent 配置。
- 只有重启 Worker或执行管理员内置同步后，数据库中的旧系统记录才完成后端退役；Web 过滤用于滚动发布期间避免旧卡片短暂出现。

详细设计见：[trim-skill-catalog.md](develop-guides/decisions/implemented/2026-09-18-trim-skill-catalog.md) 和 [retire-mcp-server-chart.md](develop-guides/decisions/implemented/2026-09-18-retire-mcp-server-chart.md)。

## 12. 知识库权限体系

这是本轮改动范围最大、需要前后端同时发布的模块。

### 12.1 权限等级

知识库资源权限从两档扩展为：

```text
NONE < READ < EDIT < MANAGE
```

- READ：读取知识库、文件、检索与问答。
- EDIT：继承 READ，并可上传、维护元数据、解析和入库。
- MANAGE：继承 READ/EDIT，并可配置、授权及执行高风险删除。

普通用户可访问“知识库 · 技能”中的知识库页，但列表和详情由后端按有效权限过滤。前端按钮隐藏只负责体验，后端依赖和 repository 可见性查询才是授权边界。

### 12.2 部门归属与系统继承

- 知识库由部门持有，`created_by` 只作为审计字段，不再天然授予管理权。
- 超级管理员继承所有知识库 MANAGE。
- 知识库所属部门的部门管理员继承该知识库 MANAGE。
- 只有超级管理员和部门管理员可以创建知识库；部门管理员只能选择本部门，超级管理员可选择有效部门。
- `owner_department_id` 保存到现有 `share_config` JSON，客户端更新共享配置时不能篡改该归属。
- 历史知识库没有归属字段时，按创建人当前部门计算兼容归属；下一次保存后固化。
- 系统继承管理员不进入“指定协管成员”候选，避免重复授权。

### 12.3 快捷授权与能力树

快捷授权决定成员范围，高级权限树决定该档位能做什么。两者共同写入当前知识库的 `share_config`：

| 档位 | 能力键 | 默认行为 |
| --- | --- | --- |
| READ | `view` | 查看目录和文档内容 |
| READ | `search` | 检索、问答和 Agent 知识库搜索 |
| READ | `download` | 下载源文件或导出 |
| EDIT | `upload` | 上传、抓取或导入文件 |
| EDIT | `metadata` | 创建、改名、移动目录和文件 |
| EDIT | `parse` | 把文档转换为可处理文本 |
| EDIT | `index` | 入库、重建索引并进入检索范围 |
| MANAGE | `configure` | 调整模型、检索、图谱和维护配置 |
| MANAGE | `share` | 调整全局、部门或指定成员范围 |
| MANAGE | `grant` | 修改成员档位与能力树 |
| MANAGE | `delete-document` | 删除文档、分段和索引 |
| MANAGE | `delete-knowledge-base` | 删除知识库及其关联数据 |

能力树按知识库保存，不是全局模板。用户命中多条授权时取最高档位，并按当前知识库策略计算有效能力。

### 12.4 额外知识库管理员

- 普通用户可以通过指定成员 `manage_scope` 成为单个知识库的额外管理员。
- 该身份只影响当前知识库，不会获得创建知识库、管理部门或管理其它知识库的权限。
- 额外管理员获得 MANAGE 档能力，可以在高级权限树中修改当前档位和下级档位能力；后端仍校验 `grant`/`configure` 等具体能力。
- 全局或部门级 MANAGE 不对普通用户生效，避免一次配置扩大到大量知识库。

### 12.5 兼容性

- 不新增权限表或列，所有范围和能力策略复用 `share_config` JSON。
- 历史数据缺少 `edit_scope` 时保持原读/管语义。
- 历史数据缺少 `capability_policy` 时自动采用默认能力模板。
- 系统继承管理员始终拥有完整能力，不会因错误能力树配置被锁死。
- Agent 与 Skill 继续使用原创建人资源所有权，不套用知识库部门归属规则。

### 12.6 代码位置

- 权限计算 Owner：`backend/package/yuxi/permissions/resource_permission.py`
- 路由依赖：`backend/server/utils/knowledge_permissions.py`
- 知识库路由：`backend/server/routers/knowledge_router.py`
- Agent 知识库工具：`backend/package/yuxi/agents/toolkits/kbs/tools.py`
- 知识库 Manager：`backend/package/yuxi/knowledge/manager.py`
- 快捷授权与能力树：`web/src/components/ShareConfigForm.vue`
- 创建流程：`web/src/components/knowledge/DatabaseCreateFlowModal.vue`
- 列表与详情：`web/src/views/DataBaseView.vue`、`web/src/views/DataBaseInfoView.vue`
- 前端策略工具：`web/src/utils/shareConfig.js`

详细设计见：

- [knowledge-base-edit-permission.md](develop-guides/decisions/implemented/2026-09-18-knowledge-base-edit-permission.md)
- [department-owned-knowledge-base-permissions.md](develop-guides/decisions/implemented/2026-09-18-department-owned-knowledge-base-permissions.md)
- [editable-knowledge-base-capabilities.md](develop-guides/decisions/implemented/2026-09-18-editable-knowledge-base-capabilities.md)

## 13. 浏览器标签页图标

- 使用杭州联合银行图标生成 `web/public/favicon.png`。
- `web/index.html` 的 favicon 引用切换到该文件。
- 只影响浏览器标签页、书签和部分快捷方式缓存；浏览器可能需要硬刷新或清理 favicon 缓存才显示新图标。
- 生产环境需要重建 Web 镜像。

## 14. Rerank 请求兼容

- `backend/package/yuxi/models/rerank.py` 不再把 `max_chunks_per_doc` 作为固定请求体字段发送给重排服务。
- 影响所有使用该模型结构发起的 Rerank 调用，目的是兼容不接受该扩展字段的服务端。
- 不改变知识库的候选文档生成和最终重排结果结构。

## 15. 用户自助修改本人密码

### 15.1 后端行为

- 新增 `PUT /api/auth/password`，只接受 `current_password` 和 `new_password`。
- 目标账号只能来自当前认证上下文，请求不能指定其他用户 ID。
- 服务校验当前密码、拒绝新旧密码相同，使用现有 Argon2 规则写入新摘要。
- 密码修改与操作日志在同一事务中提交，审计失败会回滚密码更新。
- 管理员原有“替用户重置/修改密码”接口保持不变。

### 15.2 前端行为

- 账户设置顶部提供按钮式“修改密码”入口。
- 点击后打开弹窗，包含当前密码、新密码、确认密码和显隐控制。
- 当前密码字段使用 `autocomplete="current-password"`，新密码字段使用 `autocomplete="new-password"`；浏览器密码管理器可能自动填入已保存的当前账号密码，这是浏览器行为，不是前端默认值。
- 修改成功后清除当前浏览器登录状态并跳转登录页。

### 15.3 代码位置

- 业务服务：`backend/package/yuxi/services/auth_service.py`
- API 路由：`backend/server/routers/auth_router.py`
- 前端 API：`web/src/apis/auth_api.js`
- 用户 Store：`web/src/stores/user.js`
- 账户设置：`web/src/components/AccountSettingsComponent.vue`

### 15.4 影响点与限制

- 普通用户、部门管理员和超级管理员均可修改本人密码。
- 不需要数据库迁移。
- 当前实现不会立即吊销其它设备已经签发的 JWT；其它设备会在令牌自然过期前继续有效。
- OIDC 用户如果不知道随机生成的本地密码，仍应在身份提供方维护登录凭据。

详细设计见：[self-service-password-change.md](develop-guides/decisions/implemented/2026-09-20-self-service-password-change.md)。

## 16. 评估问答编辑与 Gold Chunk 文件定位

### 16.1 问答编辑

- 拥有当前知识库 `configure` 能力的用户可逐题编辑问题和 Gold Answer。
- 问题不能为空；Gold Answer 留空表示移除标准答案。
- Repository 在同一事务中更新题目和数据集 `has_gold_chunks`/`has_gold_answers` 汇总状态。
- 已经执行的评估结果保留启动时快照，不回写；编辑只影响后续评估。

### 16.2 Gold Chunk

- 数据集详情在保留原 `gold_chunk_ids` 的同时增加 `gold_chunks` 展示模型。
- 页面显示真实文件名、片段序号、字符位置、内容摘要和内部 chunk ID。
- 有效引用可以打开 `FileDetailModal` 查看真实文件。
- 已删除、跨知识库或旧数据中的失效引用显示“未找到源文件”，不会自动删除或替换评估标准。
- 后端按当前页批量读取 Chunk 和 File，避免每个片段单独请求形成 N+1。

### 16.3 代码位置

- 评估 Service：`backend/package/yuxi/knowledge/eval/service.py`
- Repository：`backend/package/yuxi/repositories/evaluation_repository.py`
- API 路由：`backend/server/routers/knowledge_eval_router.py`
- 前端 API：`web/src/apis/knowledge_api.js`
- 详情页面：`web/src/views/EvaluationBenchmarkDetailView.vue`

### 16.4 影响点

- 不需要数据库迁移，新增字段是详情响应的兼容性增量。
- Web 已更新但 API 容器仍是旧镜像时，点击保存会收到 404；生产发布必须同时重建 API 和 Web。
- 显示“未找到源文件”通常代表数据集保存的是历史/外部 chunk ID，当前知识库中不存在相应 Chunk 或 File，不代表页面查询本身失败。

详细设计见：[editable-evaluation-benchmark-items.md](develop-guides/decisions/implemented/2026-09-21-editable-evaluation-benchmark-items.md)。

## 17. 内网管理服务链接

### 17.1 功能变化

- Neo4j Browser、API 文档、MinIO Console 和 Milvus WebUI 不再使用硬编码 `localhost`。
- API 的系统配置返回管理服务的外部端口与路径。
- Web 使用当前页面的协议和主机名，再拼接服务端返回端口和路径。例如通过 `10.0.0.8` 访问 Yuxi 时，管理入口也指向 `10.0.0.8`。
- `YUXI_MANAGEMENT_BIND_HOST` 控制 Neo4j HTTP/Bolt、MinIO Console 和 Milvus WebUI 的宿主监听地址。
- 默认仍为 `127.0.0.1`；设置为 `0.0.0.0` 才对内网开放。
- MinIO API 和 Milvus 数据端口不会随该开关一并对外开放。

### 17.2 代码位置

- 系统配置接口：`backend/server/routers/system_router.py`
- 地址生成：`web/src/utils/serviceLinks.js`
- 设置页面：`web/src/components/BasicSettingsSection.vue`
- Compose：`docker-compose.yml`、`docker-compose.prod.yml`

### 17.3 影响点与安全边界

- 生产默认端口比开发默认端口高 1000 属于当前 Compose 的默认约定，实际页面读取 API 运行环境中的端口，不再把前端构建写死。
- 对内网开放管理端口后，必须通过主机防火墙、安全组或反向代理限制来源，并为 Neo4j、MinIO 配置强密码。
- 如果主站使用 HTTPS，而管理服务仍是纯 HTTP，浏览器会因协议不匹配无法打开；此类环境应为管理服务配置受控 HTTPS 反向代理。

详细设计见：[intranet-management-service-links.md](develop-guides/decisions/implemented/2026-09-21-intranet-management-service-links.md)。

## 18. 数据库和历史数据兼容性

| 改动 | 是否需要结构迁移 | 历史数据行为 |
| --- | --- | --- |
| Sandbox 阶段耗时 | 是，Schema 8 | 旧 Run 的计时缺失保持未知；新 Run 写入 JSONB |
| 知识库 READ/EDIT/MANAGE | 否 | 缺少 `edit_scope` 时保持旧读/管行为 |
| 可编辑能力树 | 否 | 缺少 `capability_policy` 时使用默认模板 |
| 知识库部门归属 | 否 | 缺少 `owner_department_id` 时暂按创建人当前部门解析，保存后固化 |
| 用户自助改密 | 否 | 继续使用现有用户密码摘要字段 |
| 评估题目编辑 | 否 | 修改现有题目；历史评估 Run 保留快照 |
| Langfuse 增强 | 否 | 只影响新产生的 trace，历史 trace 不回填 |
| Skill/MCP 退役 | 否 | 重启同步后清理系统内置记录；自定义记录保留 |

## 19. 生产环境重建矩阵

当前生产 Compose 已移除源码挂载，按以下规则发布：

| 变更类型 | 需要构建 | 需要重建/重启的服务 |
| --- | --- | --- |
| Vue、CSS、前端图片、`web/index.html` | `web` 镜像 | `web` |
| 后端 Python、API、权限、Service、Repository | `api` 镜像 | `storage-migrator`（有迁移时）、`api`、`worker` |
| `docker/sandbox_provisioner` | `sandbox-provisioner` 镜像 | `sandbox-provisioner` |
| 仅 `.env.prod` 或 Compose 环境变量 | 通常无需构建 | 重建受影响容器，使新环境变量进入进程 |
| 仅端口映射或 bind host | 无需构建 | 重建对应 Compose 服务 |
| 仅 Markdown 文档 | 无 | 无业务容器需要重启 |

推荐生产发布顺序：

1. 备份 `.env.prod` 和持久数据，确认不会把真实密钥提交或传播。
2. 构建 API、Web；Provisioner 代码有变化时同时构建 Provisioner。
3. 先运行并确认 storage-migrator 成功。
4. 重建 API、Worker、Provisioner、Web，不只执行 restart。

## 20. 已知风险与建议清理项

### 20.1 高优先级

1. `.env.prod` 已被 Git 跟踪。提交或共享仓库前应确认其中没有真实密码/Token；如已经使用过，应立即轮换并改为服务器私有文件或密钥管理系统。
2. 内网开放 Neo4j、MinIO、Milvus 管理入口会扩大攻击面。不要只因为是内网就省略防火墙、强密码和访问审计。
3. 知识库权限改动同时覆盖 Web、API、Worker 和 Agent 工具，不能只更新前端。版本不一致会出现按钮可见但接口 403/404，或 Agent 与页面权限不一致。

### 20.2 中优先级

1. 历史知识库在首次保存前的归属依赖创建人当前部门；如准备批量调整用户部门，建议先固化历史知识库归属。
2. 用户改密没有全设备令牌吊销；如安全策略要求立即失效，需要新增 token version/password changed time 等持久化机制。
3. Gold Chunk 失效引用当前只展示、不自动修复；应由管理员确认后重新选择/生成评估数据集，避免静默修改基准。
4. Langfuse trace 可能包含业务 Prompt 与输出，应配置保留周期、访问控制和脱敏策略。

### 20.3 仓库清理建议

1. 检查根目录和 `web/` 下的 `.pnpm-store` 标记/数据库文件是否属于误提交的构建缓存；缓存不应作为产品源码长期维护。
2. `backend/package/uv.lock` 是子项目依赖锁文件，需确认团队是否正式采用该项目边界；若采用，应在开发文档统一安装命令，避免与 `backend/uv.lock` 混淆。

## 21. 提交索引

| 提交 | 主题 |
| --- | --- |
| `429bdeae` | 优化续跑消息展示与侧边栏交互 |
| `fa390d8d` | 对齐 Compose 环境隔离与部署配置 |
| `313d1d7a` | 优化后端错误捕获与失败语义 |
| `9c938a9d` | 支持 Sandbox 独立环境配置并修正版本脚本 |
| `34af1308` | Langfuse 增强、DAP 调试与压测方案 |
| `03da70ff` | 修复 Workspace 首次创建子文件夹失败 |
| `fe488b8c` | 联合智擎门户 UI、Sandbox 指标与能力精简的一部分 |
| `f804b627` | 知识库权限树、部门归属与相关前后端能力升级 |
| `0653b8fd` | 移除生产 API/Worker 后端源码挂载 |
| `4e57f18f` | 替换浏览器标签页图标 |
| `f32ce4fc` | 删除 Rerank 请求体中的 `max_chunks_per_doc` 字段 |
| `7a9fb463` | 增加用户修改本人密码能力 |
| `1fe697d2` | 增加生产环境配置文件 |
| `1f25a333` | 支持评估问答编辑、Gold Chunk 文件定位和内网管理链接 |
| `17c34b06` | 调整改密为按钮入口和弹窗交互 |

合并提交 `f9bfc0fb` 与 `00119e3b` 用于汇合并行分支，不单独代表新的业务功能。

## 22. 部门编辑表单校验修复（工作区补充）

### 22.1 功能变化

- 修复编辑部门时误触发隐藏管理员字段校验、提示“请输入管理员UID”而无法保存的问题。
- 管理员 UID 格式、长度及可用性错误、密码与确认密码、可选手机号格式等前端校验，仅在创建部门（`!editMode`）时执行。
- 编辑部门仍校验部门名称非空且至少 2 个字符，只提交部门名称和描述；保存成功后刷新列表并关闭弹窗。
- 创建部门仍要求填写管理员 UID 和密码，并保留随新部门创建管理员的流程。

### 22.2 代码位置

- `web/src/components/DepartmentManagementComponent.vue`：`handleDepartmentFormSubmit` 根据 `departmentManagement.editMode` 区分创建与编辑的表单校验。

### 22.3 影响点

- 仅修复前端表单校验范围，不修改后端 API、部门数据结构或已有管理员账号。
- 修改部门的超级管理员授权限制保持不变；不向普通用户或部门管理员新增部门编辑权限。
- 不需要数据库迁移，也无需重建 API/Worker；生产部署重建 Web 镜像及容器后生效。
