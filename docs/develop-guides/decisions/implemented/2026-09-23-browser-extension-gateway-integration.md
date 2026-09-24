# Browser Extension Gateway 接入

状态：implemented
类型：feature
Owner：backend/package/yuxi/browser_gateway/service.py

## 问题

Yuxi 尚未接入独立 Browser MCP Gateway。已登录用户不能在产品页面检测或配对 Browser Extension，真实 AgentRun 也没有 current-device assertion，因此 Agent 无法在不接收模型可控身份参数的前提下使用 `browser_whoami`、标签页、页面快照、提取和截图能力。

AgentRunRequest 与 AgentRun 是不同状态对象。排队请求只有派发后才产生 `run_id`；如果用前端 request ID、恢复页当前浏览器或同账号任意在线设备代替真实 Run 绑定，会破坏队列状态模型和 current-device 安全边界。JWT、pairing token、assertion 与页面内容也不能进入日志或由模型参数传入。

本次目标是让用户在“智能体扩展”页面完成检测与配对，在立即 Run、当前页面创建的排队 Run 和 resume Run 三条路径中绑定当前设备，并由顶层 Agent 使用 Browser Gateway P0-P2 只读工具。浏览器写操作、生产 KMS、Gateway 多实例路由、子智能体设备继承和 Extension 商店发布不在本次范围。

## 决策

Yuxi 后端增加 Browser Gateway 应用边界。API 代理 pairing token 创建，并使用 P-256 私有 JWK 为每个 Gateway 请求签发 request-scoped ES256 JWT。JWT 的 `sub/run_id/thread_id/request_id` 来自登录用户或 `ToolRuntime.context`，工具输入 Schema 不暴露身份字段。配置不完整或密钥文件不可读取时禁用 Browser 工具，不阻断普通 Agent 启动。

前端新增“智能体扩展 > 浏览器”。真实 Run 创建后，页面向扩展申请一次性 current-device assertion，再提交给 `/api/browser/runs/{run_id}/assertion`。后端先用 PostgreSQL 校验 Run 归属，以 Redis 原子 first-writer-wins 保存 assertion，并立即用该 Run 身份调用 `browser_current_device` 固化 Gateway 绑定；网络没有到达 Gateway 时保留短期 assertion 供 Tool 回退消费。排队恢复和观察流不提交断言，只有当前页面主动创建并确认进入队列的 request 才能在 `run_created` 时绑定设备。

顶层 Agent 注册 `browser_whoami`、`browser_current_device`、`browser_list_tabs`、`browser_snapshot`、`browser_extract` 和 `browser_screenshot` 六个只读工具。子智能体没有独立 current-device 契约，因此在配置、构图、模型调用和工具执行四层隐藏并拒绝这些工具。截图使用同一 Run 的短期 JWT 下载 Gateway artifact，经相对路径、媒体类型、5 MiB 上限和 PNG 文件头校验后，以模型图片内容块返回。

Extension 在加载或打包前同时生成唯一 Yuxi origin 与 Gateway origin。Service Worker 校验 external sender、pair URL、challenge origin、WebSocket 协议/host/path，并只获得精确 Gateway host permission；目标业务网站仍由用户逐站授权。页面命令幂等结果仅在 Service Worker 内存中保留最多100项、60秒。DOM、语义属性、表单值或 open shadow root 变化通过 MutationObserver 与输入事件使旧 snapshot ref 失效。

P-256 私有 JWK 只提供给 API 与 Worker；使用同一 `env_file` 的 storage-migrator 和 MinerU 容器显式遮蔽私钥变量。现有 Tool 生命周期继续拥有 Langfuse 层级，不创建重复 trace，也不记录 JWT、token、assertion、截图或页面值。

## 替代方案

- 让前端直接获取 Gateway JWT会扩大凭据暴露面，因此 pairing 和 MCP 均由 Yuxi 后端代理。
- 把 request ID 当作 Gateway `run_id` 会混淆 AgentRunRequest 与 AgentRun，因此排队请求等待权威 `run_created`。
- Gateway 自动选择同账号在线设备可能把命令发往用户未声明的浏览器，因此只接受 current-device assertion。
- 把 Gateway 作为普通可配置 MCP Server 无法为每次 ToolRuntime 注入真实 Run 身份与一次性 assertion，因此使用专用薄客户端。
- 让子 Run 自动继承父 Run 设备会引入未定义的授权传播，因此首期对子智能体禁用 Browser 工具。

## 后果

Browser 能力保持可选：扩展缺失、离线或 Gateway 不可用不会阻断普通 Run 创建，但 Browser Tool 会返回公开错误。Gateway 正常可达时，Run 创建阶段即完成设备绑定，不受 assertion 60秒有效期和模型工具选择延迟影响；瞬时网络失败仍有短期 Tool 回退窗口。

同一 Run 的设备不可被恢复页或其他浏览器覆盖。生产部署必须让 Yuxi、Gateway 与 Extension 的 issuer、audience、Yuxi origin、Gateway public origin 和 Extension ID 一致，并在加载扩展前运行来源配置脚本。截图二进制会作为模型图片输入参与当前 Agent 状态，但不会进入应用日志。

真实 MV3 浏览器、Gateway、PostgreSQL、Redis 与 Yuxi 登录态的整链 E2E 仍需在服务全部启动的部署环境执行；本地 Playwright 尝试因缺少可用的扩展浏览器运行时而未完成。全量仓库测试中的 PostgreSQL 环境失败、既有 Compose 基线断言与既有前端 `skill_detail_layout` 失败不由本变更引入。

## 验证

| 验收主张 | 失败面 | 直接证据 | 负向案例 | 结果 |
|---|---|---|---|---|
| JWT 身份来自真实 Run，模型不能覆盖 | 算法、claims、TTL 或 Schema 暴露身份 | Browser client/tool unit | 错误 issuer、未知 kid、身份字段进入工具参数 | Passed |
| assertion 立即建立 Gateway 绑定且不能被其他页面覆盖 | 首次 Tool 超过60秒、观察页抢占或 Redis 覆盖 | Browser service unit、Web queue unit | 非本人 Run、第二次写入、恢复页 `run_created` | Passed |
| Extension 只信任构建时 Yuxi 与 Gateway | 任意 HTTPS 页面或 Gateway 重配扩展 | Extension 5项来源/缓存/SPA contract unit | wildcard、跨来源 challenge/WSS、非精确 host permission | Passed |
| 页面读取结果不持久化且 SPA 旧 ref 失效 | 敏感内容进入 storage 或同 URL 读取旧 ref | Extension contract unit、源码审查 | `storage.session.set`、DOM/Shadow DOM语义变化 | Passed |
| 截图可被模型消费且下载受限 | 只返回元数据、任意 URL、超大或伪造图片 | Browser client/tool unit | 非 artifact 路径、非 PNG、超过5 MiB | Passed |
| Browser 私钥和子智能体权限保持最小化 | 私钥进入迁移/解析容器，子 Run选择不可用工具 | Compose contract、subagent tool filter unit | env_file 泄漏、显式历史 tool call | Passed |
| 构建与静态门禁成立 | 装配、格式或文档错误 | Ruff、ESLint、Prettier、Vite、VitePress、Compose config、`git diff --check` | 两份 Compose 与生产构建 | Passed |

定向结果：后端 Browser 与子智能体29项通过；前端 Browser/Queue 28项通过；Extension 5项通过；工程 verifier 自测62项通过。前端和文档生产构建通过，保留既有大 chunk 警告。独立 Reviewer 的三轮审查发现的来源劫持、持久敏感缓存、短期断言、设备竞态、截图不可消费、env_file 私钥泄漏、坏密钥阻断启动、子智能体不可用工具、SPA stale ref 与旧 ZIP 问题均已修正；最终复核未发现对应绕过路径。
