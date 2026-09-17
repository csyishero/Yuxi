# Langfuse AgentRun tracing 元数据收敛

状态：implemented
类型：feature
Owner：backend/package/yuxi/services/langfuse_service.py

## 问题

Yuxi 已通过 LangGraph callback 为 AgentRun 创建 Langfuse trace，并在模型执行前把确定性 trace ID 固化到 AgentRun；但 trace 使用框架默认名称，部署环境和发布版本未显式区分，metadata 还包含排障不需要的用户名、登录用户和部门字段。自托管部署也缺少 Docker 访问宿主机 Langfuse 的明确配置示例，导致 trace 难以稳定筛选，并扩大了远端观测数据面。

本改动属于小而完整的 observability 配置收敛，沿用既有 LangGraph callback、AgentRun trace ID 与失败降级边界，没有新的状态、队列、事务或外部依赖方案需要先行裁决，因此直接记录为 implemented。

## 决策

一次普通 AgentRun 使用稳定 trace/root run 名称 `execute-agent-run`，恢复执行使用 `resume-agent-run`。Yuxi 继续以 opaque uid 作为 `user_id`、以 `thread_id` 作为 `session_id`，并把 `run_id`、`run_type`、request、agent、backend、operation 和去重 tags 交给 LangGraph callback；模型、工具和检索 observation 仍由官方 LangChain/LangGraph 集成自动生成。

Langfuse 客户端接受可选的 `LANGFUSE_TRACING_ENVIRONMENT` 和 `LANGFUSE_RELEASE`，并通过 SDK mask hook 递归遮蔽 Authorization、Cookie、密码、Secret、Token 与 API Key 字段。用户名、登录用户和部门不再进入 Langfuse metadata。mask 保留 prompt、业务结构和 token usage，避免把凭证保护误做成整条 trace 不可诊断。

Langfuse 仍是 optional observability：SDK 缺失、未配置或远端失败不会拥有 AgentRun 终态。密钥只存在部署环境；Docker 中访问宿主机自托管服务使用 `host.docker.internal`。

## 替代方案

- 保留框架默认 trace 名称：实现最少，但名称随图或框架变化，不利于跨版本仪表盘和告警。
- 自建一套手工 span 包裹模型与工具调用：会复制官方 callback 已拥有的生命周期、usage 和错误采集，扩大维护面。
- 把全部 prompt 和 metadata 统一删除：降低泄露面，但无法排查模型输入、工具参数和输出问题；本决定只默认遮蔽凭证键，具体用户数据留存仍由部署方策略负责。
- 保留用户名和部门便于搜索：使用方便，但 opaque uid、session、run 和 request 已足够关联，不值得扩大观测服务中的个人信息范围。

## 后果

Langfuse 可以按稳定名称、环境、发布版本、用户、会话、Agent 和 Run 筛选；同一线程多轮请求在一个 session 下展示，每个 AgentRun 保持独立 trace。trace 仍可能包含用户 prompt、模型输出和工具业务参数，部署方必须限制 Langfuse 访问、保留周期和导出权限；默认 mask 只承诺阻止常见凭证字段，不是通用数据防泄漏系统。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
| --- | --- | --- | --- | --- | --- |
| AgentRun 使用稳定 trace 名称并关联 user/session/run/tags | 框架默认名称或特殊 metadata 未传播 | `langfuse_service.py`、`BaseAgent` | Langfuse/BaseAgent/chat service unit | resume 名称与普通执行不同；metadata 缺失时不设置 run name | Passed：45 个相关 unit |
| 凭证字段被遮蔽但 token usage 和 prompt 保留 | 过宽规则隐藏 usage，或嵌套凭证泄露 | `langfuse_service.py` SDK mask | `test_mask_langfuse_data_redacts_credentials_without_hiding_usage` | 嵌套 `access_token` 被遮蔽，`input_tokens` 保留 | Passed |
| 用户名和部门不进入远端 metadata | 个人字段继续从 chat service 透传 | `chat_service.py`、Langfuse unit | `test_build_run_context_uses_resume_trace_name_without_personal_metadata` | 显式断言字段不存在 | Passed |
| 本地自托管 Langfuse 收到真实 trace 和子 observations | 容器地址、密钥或 SDK ingestion 失败 | Compose `.env`、worker、Langfuse API | 无模型费用 LangChain 探针后，通过 `/api/public/v2/observations?traceId=...` 回读 | 初始 MinIO 密码不一致时 ingestion 返回 500；修正后同一检查转绿 | Passed：trace `39b1a099d64753d5ed1ec0fe67b03f23` 含 1 个逻辑根和 2 个子 observation，复数形式 access/refresh token 均已遮蔽且模型 token usage 保留 |
