# 内网管理服务链接使用当前访问主机

状态：implemented
类型：bug-fix
Owner：web/src/components/BasicSettingsSection.vue

## 问题

基础设置中的 Neo4j、API 文档、MinIO 和 Milvus 入口把地址写死为 `localhost`。用户通过内网 IP 或域名访问 Yuxi 时，浏览器会错误地访问用户自己电脑上的服务，而不是 Yuxi 部署服务器。生产 Compose 同时把基础设施端口固定绑定到服务器回环地址，无法按部署需要显式开放给受信内网。

## 决策

- 服务端系统配置返回四个管理入口的外部端口和路径，Compose 将现有 `YUXI_*_PORT` 显式注入 API，避免只参与端口插值却没有进入运行进程。
- 前端使用浏览器当前访问 Yuxi 的协议和主机名，结合服务端返回的端口、路径生成链接，不读取或信任 HTTP Host 回显。
- Compose 使用 `YUXI_MANAGEMENT_BIND_HOST` 控制 Neo4j Browser（含 Bolt）、MinIO Console 和 Milvus WebUI 的宿主监听地址；默认仍为 `127.0.0.1`，只有受信内网部署显式设置为 `0.0.0.0` 时才对外监听。MinIO API 与 Milvus 数据端口继续只监听回环地址。
- API 端口沿用现有公开绑定；基础设施管理入口仍只对管理员展示。

## 替代方案

- 继续使用固定的 `localhost`：只适用于浏览器与服务部署在同一台机器的本地开发，无法满足内网服务器部署。
- 直接把服务器 IP 写入前端环境变量：需要为每个部署环境重新构建前端，也无法自然兼容域名访问。
- 通过主站反向代理四个管理服务：可以统一域名与 HTTPS，但需要补充路径重写、WebSocket、认证和访问控制，超出本次修复范围。

## 后果

用户通过内网 IP 或域名访问 Yuxi 时，四个入口会自动复用该 IP 或域名，并切换到对应服务端口。部署无需把具体服务器地址固化进前端构建产物；修改外部端口后，只需让 API 进程获得相同的 `YUXI_*_PORT` 配置。

管理服务默认仍只绑定 `127.0.0.1`。设置 `YUXI_MANAGEMENT_BIND_HOST=0.0.0.0` 后，Neo4j HTTP/Bolt、MinIO Console 和 Milvus WebUI 端口会监听所有宿主网络接口，部署方必须使用防火墙或安全组限制来源，并为 Neo4j、MinIO 配置强密码。MinIO API 与 Milvus 数据端口不会随此开关开放。

当前链接沿用访问 Yuxi 时的协议。如果主站使用 HTTPS，而管理服务只提供 HTTP，浏览器将无法通过生成的 HTTPS 地址连接；此类部署应在外部反向代理层为管理服务提供受保护的 HTTPS 域名。

## 验证

| 主张 | 语义 Owner | 证据 | 负向案例 | 结果 |
| --- | --- | --- | --- | --- |
| 通过内网 IP 或域名访问时四个入口复用当前主机，而不是 `localhost` | `BasicSettingsSection.vue`、`serviceLinks.js` | `web/test/unit/serviceLinks.test.js`、Web build | 无效端口或相对路径被拒绝 | Passed |
| 开发、生产默认端口以及自定义端口来自 API 运行环境 | `system_router.py` | `backend/test/unit/routers/test_system_router.py` | 自定义端口必须覆盖环境默认值 | Passed |
| 基础设施端口只在显式配置后监听内网接口 | Compose 与 `.env.template` | 开发/生产 `docker compose config` | 未设置变量时保持 `127.0.0.1` | Passed |

新增前端链接单测 3 项、后端系统路由单测 4 项通过；Web lint、build，Backend Ruff check/format，Docs build 和 `git diff --check` 通过。完整 Web unit 共 348 项，其中 346 项通过；另两项现有知识库评估与权限可访问名称断言失败，与本次管理链接变更无关。未在真实内网服务器和防火墙环境执行浏览器点击验证。
