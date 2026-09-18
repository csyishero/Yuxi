# 记录 Sandbox 生命周期阶段耗时

状态：implemented
类型：feature
Owner：backend/package/yuxi/agents/backends/sandbox/provider.py

## 问题

AgentRun 原先只能观察到完整工具耗时，无法区分 Sandbox 发现、网络创建、容器创建、就绪等待、命令请求和终态清理。开发环境压测因此不能判断并发退化来自模型、Sandbox 冷启动还是容器销毁。

## 决策

Provisioner 使用单调时钟记录其拥有的网络、容器和就绪阶段，并通过现有管理 API 返回；Worker 累计管理发现、命令请求和释放耗时，在 runtime cleanup 的 PostgreSQL 事务中把 8 个指标写入当前 AgentRun。Run 结果、Langfuse 当前工具 observation 和轻量压测报告只读取该投影。

不修改 Sandbox 创建、执行、释放顺序，不包装用户命令，不新增重试、超时或采样开关。当前 Agent Sandbox 协议不提供容器内命令执行时长，因此不记录 `sandbox_command_ms`。

## 后果

- AgentRun 的 `timing` 投影和轻量压测汇总可以直接展示 8 个 Sandbox 阶段指标。
- Provisioner 管理响应增加可选 timing 数据，业务库升级到 Schema 8 并新增非空 JSONB 列。
- Langfuse 当前工具 observation 可获得执行结束前已采集的 Sandbox timing；完整释放与删除指标以 PostgreSQL AgentRun 为权威事实。
- 未执行或因进程故障未持久化的阶段保持缺失，调用方必须把缺失解释为未知而不是 0。

## 替代方案

- 只在压测客户端测量：无法观察 Provisioner 内部阶段，不能定位冷启动瓶颈。
- 只记录 Provisioner 日志：难以按 Run 关联，也不能形成可重复统计。
- 在每个 Sandbox 中安装 Langfuse SDK：需要分布式 trace context、凭据和额外依赖，超出当前纯观测增量范围。
- 包装 Shell 命令测量命令时长：会改变退出码、信号、引用和超时语义，拒绝采用。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 结果 |
|---|---|---|---|---|---|
| Docker/Kubernetes Provisioner 返回实际执行阶段的耗时，跳过阶段不写 0 | 把未执行阶段误报为 0，或混入 wall clock | `docker/sandbox_provisioner/app.py` | 容器内运行 `test/unit/backends/test_sandbox_provisioner_config.py` | 复用已有 Sandbox 时不得出现容器创建耗时 | Passed |
| Worker 累计 8 个允许的数值指标且不接受任意 wire 字段 | 非数值或未知字段污染持久结果 | `backend/package/yuxi/agents/backends/sandbox/provider.py` | 容器内运行 `test/unit/backends/test_sandbox_backends.py`、`test/unit/backends/test_sandbox_provisioner_client.py` | 返回未知字段时最终 timing 不包含它 | Passed |
| cleanup 成功后计时与 cleanup fence 在同一事务持久化 | 客户端已看到 end 但 release 指标未落库 | `backend/package/yuxi/services/run_worker.py` | 容器内运行 `test/unit/services/test_run_worker.py` | cleanup 失败不得清 fence 或发布终态计时 | Passed |
| Run Result 和压测报告输出沙箱 P95，不改变既有成功判定 | 指标存在但用户无法查询，或缺失计时导致请求失败 | `backend/package/yuxi/services/agent_run_service.py`、`backend/test/performance/load.py` | 容器内运行 `test/unit/storage/test_agent_run_timing.py`、`test/unit/performance/test_load.py` | 没有 Sandbox 的 chat Run 仍正常且指标为空 | Passed |
| 已有数据库由 storage-migrator 幂等增加计时列 | 当前版本数据库启动后缺列 | `backend/package/yuxi/storage/postgres/manager.py` | 容器内运行 `test/integration/services/test_schema_migration_version.py` | 从旧版本升级两次仍只有一个兼容列 | Passed |

相关单元测试共 319 项通过，Schema 迁移集成测试 8 项通过，工程信任检查与其 62 项测试通过；本地业务 Schema 已从 7 升到 8，`agent_runs.sandbox_timing` 为非空 JSONB，API、Worker 和 Provisioner 重启后均为 healthy。

真实 Agent smoke test 在认证阶段以 HTTP 401 停止：当前容器未配置 `YUXI_LOAD_API_KEY` 或压测用户凭据。没有绕过认证生成令牌；待提供合法凭据后可补充 Run Result 与 Langfuse trace 的在线证据。

## 风险

- Worker 或 Provisioner 在 cleanup 前重启时，尚未持久化的进程内阶段计时可能缺失；缺失保持未知，不伪造数值。
- `sandbox_execute_request_ms` 是 Worker 到 Sandbox 返回完整响应的整体时间，不等同于容器内命令执行时间。
- 增加内部管理 API 响应字段和 AgentRun JSONB 列，需要 Schema 版本 8 与兼容迁移。
