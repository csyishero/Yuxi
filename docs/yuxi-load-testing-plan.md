# Yuxi 内网离线环境性能压测技术方案

> 适用版本：Yuxi 0.7.x（docker-compose.yml 为部署事实来源）
> 目标环境：无法访问外网的内网开发环境
> 日期：2026-09-17

---

## 1. 背景与目标

Yuxi 将在内网开发环境完成一次完整部署，需要在**无外网**条件下验证系统在高并发下的性能表现与稳定性瓶颈，为后续容量规划、参数调优和正式上线提供量化依据。

### 1.1 压测目标

| 类别 | 目标 |
|---|---|
| 容量 | 测出 API / worker / PostgreSQL / Redis / MinIO 各层在目标并发下的吞吐上限与拐点 |
| 链路 | 覆盖"登录 → 提交 Agent 请求 → FIFO 排队 → worker 执行 → SSE 推送 → 终态落库"全链路 |
| 文件 | 覆盖多并发文件上传（Agent 附件、知识库文件）的吞吐、失败率与对象存储压力 |
| 稳定性 | 30 分钟及以上稳态压测，验证内存泄漏、连接池耗尽、lease 漂移、队列积压恢复 |
| 产出 | 各场景性能报告、瓶颈定位、调优建议（连接池、ARQ 并发、PG max_connections 等） |

### 1.2 非目标

- 不压测外部 LLM 厂商 API 本身的容量（网内模型服务单独出报告）。
- 不验证业务正确性（由 E2E 测试负责），压测只关心性能与稳定性指标。
- GPU 文档解析（mineru / paddlex）只作为混合场景的一部分，不单独做 GPU 极限压测。

---

## 2. 被测系统架构与关键约束

### 2.1 拓扑（来自 docker-compose.yml）

```
压测机(k6) ──► web(5173, 可跳过) ──► api(5050, FastAPI/uvicorn)
                                        │  写 PG Request/Message → 投 ARQ(Redis)
                                        ▼
                              worker(ARQ, ARQ_MAX_JOBS=140)
                                        │  AgentRun lease/heartbeat
                                        ├──► postgres:16 (max_connections=600)
                                        ├──► redis:7.4 (ARQ 投递 + Stream 事件 + 取消)
                                        ├──► minio (附件/知识库对象)
                                        ├──► milvus + etcd (向量检索)
                                        ├──► neo4j (图谱)
                                        └──► sandbox-provisioner ──► 沙盒容器(惰性创建)
```

### 2.2 压测设计必须遵守的系统事实

1. **FIFO 串行语义**：同一用户 + 同一 Agent + 同一线程的普通请求按 FIFO 串行派发。
   压测并发度 = 用户数 × 每用户线程数。要测 200 并发聊天，就必须准备 200 个独立
   （用户, 线程）组合（或确认 reject/steer 队列策略行为）。
2. **两阶段提交**：请求先持久化到 PostgreSQL 再投递 ARQ。压测指标必须区分
   "API 接收延迟"与"Run 完整生命周期耗时"。
3. **SSE 事件流**：排队阶段走 `GET /api/agent/requests/{id}/events`，派发后走
   `GET /api/agent/runs/{id}/events`。SSE 长连接本身也是压测对象（连接数、
   断开重连风暴）。
4. **资源天花板（默认值，压前确认）**：
   - PostgreSQL `max_connections=600`；API 池 120+40，worker 池 120+40，
     多实例部署时总和不得超过 600。
   - worker `ARQ_MAX_JOBS=140`（执行槽）；Durable Task 的 PG claim 上限为 4。
   - `SANDBOX_IDLE_TIMEOUT_SECONDS=120`、`SANDBOX_EXEC_TIMEOUT_SECONDS=180`、
     `SANDBOX_MAX_OUTPUT_BYTES=256KB`。
5. **启动门禁**：API 以 `/api/system/ready`（非 `/health`）作为接流量前置条件，
   压测前置条件检查必须调用 ready。

---

## 3. 内网离线环境准备（前置条件，压测前 1~2 周完成）

### 3.1 镜像离线导入

外网机器执行，内网通过 U 盘/内网仓库分发：

```bash
# 外网：拉取并打包全部镜像
docker compose pull
docker compose build   # build 的服务需先构建
docker save $(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E 'yuxi|postgres:16|redis:7|minio|milvus|etcd|neo4j') \
  -o yuxi-images.tar

# 内网：导入（或先推送到内网 Harbor 再改 image 名）
docker load -i yuxi-images.tar
```

必须包含的镜像清单：yuxi-api、yuxi-web、yuxi-sandbox-provisioner、postgres:16、
redis:7.4.10-alpine、quay.io/minio/minio、milvusdb/milvus:v2.5.6、
quay.io/coreos/etcd:v3.5.5、neo4j:5.26.29，以及沙盒运行时镜像
`enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:1.11.0`
（sandbox-provisioner 运行时按需拉取，离线时必须提前 `docker load`）。

> 建议：内网部署 Harbor，所有镜像改名推送一次，之后 `docker pull` 走内网仓库，
> 免去逐台 load。

### 3.2 模型服务（LLM / Embedding / Rerank）

Yuxi 的 Agent Run 强依赖模型供应商（providers 存在 PostgreSQL，经 Redis 缓存）。
**网内必须提供可达的模型端点**，两种方案：

| 方案 | 说明 | 适用 |
|---|---|---|
| A. 真实网内模型 | 用 vLLM / Ollama / Xinference 在网内部署 Qwen 等开源模型，在 Yuxi 管理界面注册为模型供应商 | 端到端压测（推荐作为主方案） |
| B. 模型 Stub | 写一个静态/可编程 HTTP 服务，模拟 OpenAI 兼容接口返回固定 token 流 | 纯平台容量压测（排除模型延迟，测 Yuxi 自身） |

建议 **A + B 结合**：B 用于测平台容量拐点，A 用于出端到端真实报告。

### 3.3 模型缓存与 GPU 解析服务

- `mineru-api` / `paddlex`（profile `all`）需要 GPU 与模型权重。离线环境需在外网
  预下载模型目录，作为 volume 挂入（参考 `RAPIDOCR_MODEL_DIR=/home/yuxi/.cache/rapidocr/models`）。
  若内网无 GPU，这两个服务不启动，文件解析压测只覆盖文本类附件。
- Neo4j 插件、Milvus 无额外外网依赖，随镜像启动即可。

### 3.4 压测工具离线安装

推荐 **k6**（单二进制、内置 HTTP/SSE 支持、可执行机隔离部署）：

```bash
# 外网下载对应平台二进制，拷入内网压测机即可，无依赖
# https://github.com/grafana/k6/releases  →  k6-vX.Y.Z-macos-amd64.zip / linux-amd64.tar.gz
k6 version
```

备选：Locust（需 Python + pip 离线 wheel 包，内网可配 pip 镜像或 `pip download` 打包）。
不推荐 JMeter：JVM 依赖重，脚本维护成本高。

### 3.5 监控组件（离线）

**现状核对（2026-09，基于主分支代码）**：Yuxi 自身**没有**暴露 Prometheus
`/metrics`（无 prometheus-client 依赖、无埋点中间件），compose 中也没有
Prometheus/Grafana/exporter。因此监控分两层准备：

#### A. 零代码改动层（压测基线，必须做）

| 组件 | 用途 | 离线方式 |
|---|---|---|
| `docker stats` 采样脚本（见第 8 节 sampler.sh） | 容器 CPU/内存/网络/IO | 无依赖，直接用 |
| PG `pg_stat_statements` | 慢 SQL Top、QPS | postgres:16 镜像自带 contrib，执行一次 `CREATE EXTENSION pg_stat_statements;` |
| PG `pg_stat_activity` 采样 | 连接数、锁等待 | 无新组件，SQL 采样 |
| Redis `INFO` 采样 | 内存、客户端连接 | `redis-cli INFO`，无新组件 |
| k6 输出 + uvicorn 访问日志 | API 延迟/错误率/RPS | `k6 run --out json`；`docker logs api` |
| MinIO 指标 | 对象存储延迟/吞吐 | MinIO 自带 `/minio/v2/metrics/cluster`，有 Prometheus 时直接 scrape |

#### B. 需额外部署的组件（建议做，均为单容器）

| 组件 | 用途 | 离线方式 | 备注 |
|---|---|---|---|
| Prometheus | 抓取 minio / milvus / exporter | `docker save prom/prometheus` 单容器导入 | scrape 配置见下 |
| postgres_exporter | PG 指标（连接、复制、锁） | `docker save prometheuscommunity/postgres-exporter` | 连接串指向 postgres 服务 |
| redis_exporter | Redis 指标 | `docker save oliver006/redis_exporter` | 指向 redis:6379 |
| Grafana（可选） | 看板 | `docker save grafana/grafana` | 没有也可，用 Prometheus 表达式查询 |

建议写成独立 override 文件（不进主仓库 compose，放内网运维仓库）：

```yaml
# docker-compose.monitoring.yml（离线环境）
services:
  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./loadtest/prometheus.yml:/etc/prometheus/prometheus.yml:ro
    ports:
      - "127.0.0.1:9090:9090"
    networks: [app-network]
  postgres-exporter:
    image: prometheuscommunity/postgres-exporter:latest
    environment:
      DATA_SOURCE_NAME: "postgresql://postgres:postgres@postgres:5432/yuxi?sslmode=disable"
    networks: [app-network]
  redis-exporter:
    image: oliver006/redis_exporter:latest
    environment:
      REDIS_ADDR: "redis://redis:6379"
    networks: [app-network]
```

```yaml
# loadtest/prometheus.yml 片段
scrape_configs:
  - job_name: minio
    metrics_path: /minio/v2/metrics/cluster
    static_configs: [{ targets: ["minio:9000"] }]
  - job_name: milvus
    static_configs: [{ targets: ["milvus:9091"] }]
  - job_name: postgres
    static_configs: [{ targets: ["postgres-exporter:9187"] }]
  - job_name: redis
    static_configs: [{ targets: ["redis-exporter:9121"] }]
```

Milvus 指标端口 9091 在 compose 中已暴露，无需改动。

#### C. 可选改动层（需要提 PR + 离线 wheel，非必须）

如需 api/worker 进程级指标（HTTP 直方图、ARQ 队列深度、Run 状态计数），需新增
`prometheus-client` 依赖和一个薄 `/metrics` 中间件；内网安装需提前在外网
`pip download prometheus-client -d wheels/` 打包导入。压测第一轮回合可先用
路径 A+B，若瓶颈定位困难再做此项。

> Langfuse 说明：代码内置 `langfuse_service.py`（env 驱动、可选），它是 Run 链路
> trace 而非基础设施指标；内网自部署 Langfuse 成本高，压测期间建议跳过，
> 用 PG 中的 Run 阶段时间点派生耗时即可。

### 3.6 可选增强：内网自部署 Langfuse 的附加验证

如果内网具备条件（Langfuse v3 自托管依赖 Postgres + ClickHouse + Redis + S3，
镜像离线导入工作量较大），可以在真实链路轮次（S4/S9）打开 Langfuse，
获得以下 PG 指标给不出的分析能力：

**接入方式（零代码改动）**：Yuxi 通过 LangChain `CallbackHandler` 接入，
api 与 worker 只需注入环境变量即可启用：

```bash
LANGFUSE_ENABLED=true
LANGFUSE_BASE_URL=http://langfuse:3000      # 网内自部署地址
LANGFUSE_PUBLIC_KEY=pk-xxx
LANGFUSE_SECRET_KEY=sk-xxx
```

每个 AgentRun 一条 trace；LangGraph 每个节点、每次模型 generation、每次工具调用
均为 span。`trace_id` 由 `request_id` 确定性派生，可从 k6 的请求 ID 直接定位 trace。

| 附加验证项 | 内容 | 对应用途 |
|---|---|---|
| 模型层耗时分解 | 每次 generation 的 TTFT、总耗时、prompt/completion token | 区分"Run 慢在平台排队还是慢在模型推理"；真实 vLLM 场景看并发下模型延迟退化 |
| 节点级耗时 | 摘要、记忆、知识检索、工具、审批等节点的 span 耗时 | 定位高并发下最先退化的 middleware/节点 |
| 工具与沙盒 | 每工具调用次数、延迟、错误率；沙盒冷启动耗时就体现在首个沙盒工具 span | S9 混合场景瓶颈分析，免翻 provisioner 日志 |
| FIFO 可视化 | `session_id = thread_id`，同 session trace 时间线应严格串行 | S5 场景的直观旁证 |
| tracing 自身开销 | 对比 `LANGFUSE_ENABLED=true/false` 两轮的 Run 端到端耗时 | 量化观测开销，为生产是否开启提供数据 |
| tracing 可靠性 | Langfuse trace 数 vs PG completed Run 数对账 | 压测下 trace 丢失率；worker 终态 `flush_langfuse` 批量上报的稳定性 |
| 失败归因 | 失败 Run 的 trace 直接显示失败 span | 免在 worker 日志中排查 |

**执行建议**：第一轮容量压测（S1~S3 找拐点）保持 Langfuse 关闭，排除变量；
S4/S9 真实链路轮次再开启做上表分析。所有内网部署的 Langfuse 数据仅用于压测分析，
不作为业务事实来源（Run 终态仍以 PostgreSQL 为准）。

---

## 4. 测试数据与账号准备

### 4.1 账号

- 首次部署通过 `POST /api/auth/initialize` 创建管理员，拿到 Bearer Token。
- 压测需要 N 个普通用户（N = 目标并发数 ÷ 每用户线程数）。推荐 k6 阶段只用一个
  管理员 + **为每个虚拟用户动态创建独立线程**，因为 FIFO 粒度是
  （用户, Agent, 线程），不同线程之间天然并行。

### 4.2 数据脚本（一次性初始化）

```python
# 伪代码：init_data.py
# 1. POST /api/auth/token 获取 token
# 2. POST /api/agent 创建压测专用 Agent（绑定 Stub 模型，关掉工具/沙盒，
#    使 Run 耗时可控；另建一个开启知识库工具的 Agent 测混合场景）
# 3. 为每个并发槽位 POST /api/agent/runs 之前的 thread：
#    直接调用提交接口时使用全新 thread_id 即可自动建线程
# 4. 知识库场景：POST /api/knowledge/files/upload 预置 100~500 篇文档并完成解析
```

### 4.3 测试文件集

| 文件 | 大小 | 用途 |
|---|---|---|
| text-1k.txt | 1 KB | 附件基准 |
| text-1m.txt | 1 MB | 附件常规 |
| pdf-10m.pdf | 10 MB | 大附件 / 知识库上传 |
| pdf-100m.pdf | 100 MB | 上传极限场景（观察 MinIO 与临时目录） |
| image-5m.png | 5 MB | 图片消息（`/api/chat/image/upload`） |

在网内生成，避免外网下载：
`dd if=/dev/urandom of=pdf-100m.pdf bs=1M count=100`（或脚本生成合法 PDF）。

---

## 5. 压测场景设计

### 5.1 场景总览

| 编号 | 场景 | 并发模型 | 核心指标 | 目的 |
|---|---|---|---|---|
| S1 | API 只读冒烟 | 50 VU / 5 min | P95 < 200ms，错误率 < 0.1% | 验证环境可用性基线 |
| S2 | 登录 | 100 VU  Ramp | 登录 TPS、Token 签发延迟 | 认证瓶颈 |
| S3 | Agent 聊天提交（Stub 模型，不等待完成） | 100/300/600 VU 阶梯 | 提交接口 P95/P99、PG 写入 TPS | API 接入层容量 |
| S4 | Agent 全链路（Stub 模型，等待终态） | 50/100/200 并发线程 | 端到端耗时、Run 成功率、worker 吞吐（Run/min） | 全链路拐点 |
| S5 | FIFO 语义验证 | 单线程连续 200 请求 | 严格串行完成、无并发 Run | 架构不变量回归 |
| S6 | SSE 事件流 | 200 长连接 30 min | 连接保持率、事件到达延迟、重连次数 | 事件推送容量 |
| S7 | 附件上传 | 50/100 VU × 混合文件 | 上传吞吐 MB/s、P95、MinIO 延迟 | 文件上传容量 |
| S8 | 知识库上传+解析（Durable Task） | 20/50 并发文件 | 解析吞吐、Task 完成时间、PG claim 上限验证 | 后台任务容量 |
| S9 | 混合场景（聊天 70% + 上传 20% + 知识库 10%） | 300 VU / 30 min | 稳态资源曲线、无内存泄漏 | 生产形态验证 |
| S10 | 恢复/稳定性 | S9 稳态后 kill worker | 失联 Run 收敛为 failed（lease 过期）、无悬挂 running | 崩溃恢复 |

### 5.2 关键场景说明

**S3/S4 并发换算**：目标 200 个并行 Run → 200 个独立 thread_id。k6 中每个 VU 使用
自己的 `thread_id`（预先创建或首请求自动生成后复用）。

**S4 终态判定**：提交后轮询 `GET /api/agent/runs/{run_id}` 直到 `status` 为终态
（completed/failed），记录 wall time。SSE 等待亦可，但轮询更简单可靠；
SSE 的压力由 S6 单独覆盖。

**S8 注意**：Durable Task 的 PG claim 上限为 4（代码约束），解析吞吐受解析服务
（mineru/paddlex/GPU）限制，期望曲线是"提交快、完成慢"，验收点是 Task 最终全部
收敛、文件状态与 Task 终态一致，而不是提交接口的 TPS。

---

## 6. k6 脚本（契约已对照源码验证）

### 6.0 接口契约核对结论

以下契约均来自当前源码，内网部署后可直接使用；脚本按此契约编写。

| 接口 | 方法/路径 | 请求 | 响应要点 |
|---|---|---|---|
| 登录 | `POST /api/auth/token` | **form-urlencoded**，字段 `username`/`password`（非 JSON） | `{access_token, token_type: "bearer", ...}`；有 IP 限速与失败账号锁定，**不适合高频压测** |
| API Key 认证 | 任意接口 | `Authorization: Bearer yxkey_...` | 无过期、无锁定，**压测推荐**（测试前在用户设置中创建） |
| 建线程 | `POST /api/chat/thread` | JSON `{agent_id, title?}` | `{id, ...}`，`id` 即 thread_id；**提交前必须先建**，未知 thread_id 返回 404（`create_conversation` 默认 False） |
| 提交请求 | `POST /api/agent/runs` | JSON `{agent_slug, thread_id, query, queue_policy}` | `{request_id, status, queue_position, run_id, stream_url, request_events_url}`；**排队时 `run_id` 为 null**，需轮询请求查询获取 |
| 请求查询 | `GET /api/agent/requests/{request_id}` | Bearer | 同上结构，派发后出现 `run_id` |
| Run 查询 | `GET /api/agent/runs/{run_id}` | Bearer | 含 `status`，终态 `completed` / `failed` / `cancelled` |
| 事件流 | `GET /api/agent/runs/{run_id}/events`、`/requests/{id}/events` | Bearer + `Accept: text/event-stream`，支持 `Last-Event-ID` 断线续传 | SSE |
| 附件上传 | `POST /api/chat/attachments/tmp` | multipart，字段名 **`file`**，无需线程 | 写入 MinIO tmp，压测首选 |
| Viewer 上传 | `POST /api/filesystem/upload` | multipart 字段 `files`（列表）+ `thread_id` + `parent_path` | 依赖已有线程与目录 |
| 知识库上传 | `POST /api/knowledge/files/upload?kb_id=...` | multipart 字段 `file`；**仅管理员**；≤100 MB | **按内容哈希查重，重复内容 409**——压测文件必须内容各异 |

源码依据：`auth_router.py:220`（表单登录）、`auth_middleware.py:91`（`yxkey_` 前缀）、
`chat_router.py:286`（建线程）、`agent_request_service.py:67,155`（create_conversation 默认 False）、
`agent_request_queue_service.py:453`（提交响应投影）、`agent_router.py:446`（Run events SSE）、
`chat_router.py:391`（tmp 附件）、`filesystem_router.py:93`（viewer 上传）、
`knowledge_router.py:1612`（知识库上传）。

### 6.1 环境变量约定

```bash
export BASE_URL=http://<内网IP>:5050
export API_KEY=yxkey-xxxxxxxx        # 压测用户的 API Key（推荐，替代 JWT）
export AGENT_SLUG=<压测 Agent slug>  # 即 Agent 的 slug/agent_id
```

> 压测前一次性准备：管理员登录 → 为压测用户在用户设置中创建 API Key →
> 用 `POST /api/chat/thread` 验证 `AGENT_SLUG` 可用。JWT 会过期且登录有锁定与限速，
> 压测全程使用 API Key。

### 6.2 S3：聊天提交压测（submit_only.js）

> 注意：内网无法访问 `https://jslib.k6.io` CDN，uuid 用下方内联函数生成，
> 不要 import 在线 jslib。

```javascript
import http from 'k6/http';
import { check } from 'k6';

function uuidv4() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}

export const options = {
  scenarios: {
    ramp: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '2m', target: 100 },
        { duration: '3m', target: 300 },
        { duration: '3m', target: 600 },
        { duration: '2m', target: 0 },
      ],
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<1000'],
  },
};

const BASE = __ENV.BASE_URL;
const HEADERS = { Authorization: `Bearer ${__ENV.API_KEY}` };
const AGENT = __ENV.AGENT_SLUG;

// 真实契约要求先建线程；每个 VU 一个独立线程（不同线程并行，绕开 FIFO 串行）
let threadId = null;

function ensureThread() {
  if (threadId) return threadId;
  const res = http.post(
    `${BASE}/api/chat/thread`,
    JSON.stringify({ agent_id: AGENT, title: `load-${__VU}` }),
    { headers: { ...HEADERS, 'Content-Type': 'application/json' } }
  );
  check(res, { 'thread created': (r) => r.status === 200 });
  threadId = JSON.parse(res.body).id;
  return threadId;
}

export default function () {
  const res = http.post(
    `${BASE}/api/agent/runs`,
    JSON.stringify({
      agent_slug: AGENT,
      thread_id: ensureThread(),
      query: `压测问题 ${__VU}-${__ITER}`,
      queue_policy: 'enqueue',
    }),
    { headers: { ...HEADERS, 'Content-Type': 'application/json' }, timeout: '10s' }
  );
  check(res, { 'submit accepted': (r) => r.status === 200 });
}
```

> 说明：提交后立即返回（不等 Run 完成），队列会积压。需配合 S4 逐步加压找到
> "提交仍正常但排队时延陡增"的拐点；600 VU 纯提交最终必然打满 ARQ/PG，
> 用于观察背压行为（reject 策略、ready 探针）而非"通过/失败"。

### 6.3 S4：全链路（chat_e2e.js，等待终态）

```javascript
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend } from 'k6/metrics';

function uuidv4() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}

const e2e = new Trend('run_e2e_seconds', true);

export const options = {
  vus: 100,
  duration: '10m',
  thresholds: {
    http_req_failed: ['rate<0.01'],
    'run_e2e_seconds': ['p(95)<120'],
  },
};

const BASE = __ENV.BASE_URL;
const HEADERS = { Authorization: `Bearer ${__ENV.API_KEY}` };
const AGENT = __ENV.AGENT_SLUG;

let threadId = null;

function ensureThread() {
  if (threadId) return threadId;
  const res = http.post(
    `${BASE}/api/chat/thread`,
    JSON.stringify({ agent_id: AGENT, title: `load-e2e-${__VU}` }),
    { headers: { ...HEADERS, 'Content-Type': 'application/json' } }
  );
  check(res, { 'thread created': (r) => r.status === 200 });
  threadId = JSON.parse(res.body).id;
  return threadId;
}

// 排队时提交响应的 run_id 为 null，需轮询请求查询等派发
function waitForRunId(requestId, maxAttempts = 60) {
  for (let i = 0; i < maxAttempts; i++) {
    sleep(1);
    const res = http.get(`${BASE}/api/agent/requests/${requestId}`, { headers: HEADERS });
    if (res.status === 200) {
      const runId = JSON.parse(res.body).run_id;
      if (runId) return runId;
    }
  }
  return null;
}

export default function () {
  const submit = http.post(
    `${BASE}/api/agent/runs`,
    JSON.stringify({
      agent_slug: AGENT,
      thread_id: ensureThread(),   // 每 VU 独立线程 → 并发 Run，绕开 FIFO 串行
      query: `请回复 ok：${uuidv4()}`,
      queue_policy: 'enqueue',
    }),
    { headers: { ...HEADERS, 'Content-Type': 'application/json' } }
  );
  if (submit.status !== 200) return;
  const body = JSON.parse(submit.body);
  const requestId = body.request_id;
  check(submit, { 'request accepted': () => !!requestId });

  const start = Date.now();
  const runId = body.run_id || waitForRunId(requestId);
  check(submit, { 'run dispatched': () => !!runId });
  if (!runId) return;

  let terminal = false;
  for (let i = 0; i < 240 && !terminal; i++) {   // 最多等 8 min
    sleep(2);
    const st = http.get(`${BASE}/api/agent/runs/${runId}`, { headers: HEADERS });
    if (st.status === 200) {
      const s = JSON.parse(st.body).status;
      terminal = ['completed', 'failed', 'cancelled'].includes(s);
      if (terminal) {
        e2e.add((Date.now() - start) / 1000);
        check(s, { 'run completed': (v) => v === 'completed' });
      }
    }
  }
  check(terminal, { 'run reached terminal': (v) => v === true });
}
```

### 6.4 S7：文件上传（upload.js）

用 `/api/chat/attachments/tmp`（字段 `file`，无需线程，直接写 MinIO tmp）。
若改为 knowledge/files/upload：需要管理员 API Key、`kb_id` 查询参数，
且**文件内容必须互不相同**（内容哈希查重，重复返回 409）。

```javascript
import http from 'k6/http';
import { check } from 'k6';
import { SharedArray } from 'k6/data';

const files = new SharedArray('files', () => [
  open('./testdata/text-1k.txt', 'b'),
  open('./testdata/text-1m.txt', 'b'),
  open('./testdata/pdf-10m.pdf', 'b'),
]);

export const options = {
  vus: 100,
  duration: '10m',
  thresholds: { http_req_failed: ['rate<0.01'] },
};

const BASE = __ENV.BASE_URL;
const HEADERS = { Authorization: `Bearer ${__ENV.API_KEY}` };

export default function () {
  const idx = (__VU + __ITER) % files.length;
  const data = {
    // 真实契约：字段名是单数 file（/api/chat/attachments/tmp）
    file: http.file(files[idx], `load-${__VU}-${__ITER}.bin`),
  };
  const res = http.post(`${BASE}/api/chat/attachments/tmp`, data, {
    headers: HEADERS,
    timeout: '120s',
  });
  check(res, { 'upload ok': (r) => r.status === 200 });
}
```

> Viewer 上传（`/api/filesystem/upload`，字段 `files` 列表 + `thread_id` + `parent_path`）
> 适合作为 S9 混合场景的补充：先 `ensureThread()` 建线程，再用 `parent_path: "/"`
> 上传到线程根目录。

### 6.5 S6：SSE 长连接（sse.js）

SSE 端点走标准 `Authorization: Bearer` 头（k6 可设置头；浏览器 EventSource
无法设头，前端用 fetch 流实现，压测无需关心）。下面演示完整流程：建线程 →
提交请求 → 按 `request_events_url` 持流等待派发 → 切 `stream_url` 持流到终态。

```javascript
import http from 'k6/http';
import { check } from 'k6';

export const options = { vus: 200, duration: '30m' };

const BASE = __ENV.BASE_URL;
const HEADERS = { Authorization: `Bearer ${__ENV.API_KEY}` };
const AGENT = __ENV.AGENT_SLUG;

export default function () {
  // 1. 建线程
  const t = http.post(
    `${BASE}/api/chat/thread`,
    JSON.stringify({ agent_id: AGENT }),
    { headers: { ...HEADERS, 'Content-Type': 'application/json' } }
  );
  const threadId = JSON.parse(t.body).id;

  // 2. 提交请求
  const s = http.post(
    `${BASE}/api/agent/runs`,
    JSON.stringify({ agent_slug: AGENT, thread_id: threadId, query: 'sse 压测', queue_policy: 'enqueue' }),
    { headers: { ...HEADERS, 'Content-Type': 'application/json' } }
  );
  const body = JSON.parse(s.body);

  // 3. 持流：k6 收到完整响应才返回，长连接场景下用超长 timeout 让请求挂起，
  //    每 VU 保持一个 pending 流连接，同样占用服务端 SSE 连接与订阅资源。
  const streamUrl = body.stream_url || body.request_events_url;
  const res = http.get(`${BASE}${streamUrl}`, {
    headers: { ...HEADERS, Accept: 'text/event-stream' },
    responseType: 'text',
    timeout: '30m',
  });
  check(res, { 'stream held or cleanly closed': (r) => r.status < 500 });
}
```

---

## 7. 执行计划

| 阶段 | 内容 | 时长 | 通过条件 |
|---|---|---|---|
| D0 | 离线环境部署、ready 探针通过、Stub 模型注册、数据初始化 | 0.5 天 | `/api/system/ready` 200 |
| D1 | S1 冒烟 + S2 登录 | 0.5 天 | 基线达标 |
| D2 | S3 提交阶梯 + S4 全链路阶梯（50→100→200→400） | 1 天 | 找到拐点 |
| D3 | S5 FIFO 回归 + S6 SSE | 0.5 天 | 串行语义正确、连接稳定 |
| D4 | S7 上传 + S8 知识库解析 | 1 天 | 吞吐与收敛达标 |
| D5 | S9 混合稳态 30 min + S10 worker kill 恢复 | 1 天 | 稳态无泄漏、恢复正确 |
| D6 | 数据汇总、报告、调优复测 | 1 天 | 报告评审通过 |

每轮压测之间留 10 分钟冷却，并清空无关积压（重启 worker / 清理测试 Conversation），
避免上一轮队列影响下一轮。

---

## 8. 监控指标与采集方式

### 8.1 压测侧（k6 输出）

- RPS、P50/P95/P99、错误率、VU 数 —— `k6 run --out json=result.json`。
- 自定义 Trend：`run_e2e_seconds`（Run 端到端耗时）。

### 8.2 系统侧

> 各组件现状与离线准备方式见 3.5 节；本节只列采集清单。

| 层 | 指标 | 采集命令/来源 |
|---|---|---|
| 容器 | CPU / 内存 / 网络 / IO | `docker stats --no-stream` 每 5s 采样（脚本写入 CSV） |
| API | 请求延迟分布、5xx、连接数 | k6 + uvicorn 日志；`docker logs api` |
| worker | ARQ 执行槽占用、Run/min、heartbeat 失败 | worker 日志关键词采样 |
| PG | 连接数、活跃/空闲事务、锁等待、慢查询 | `SELECT count(*) FROM pg_stat_activity;`、`pg_stat_statements` |
| Redis | 内存、客户端连接、Stream 长度 | `redis-cli INFO memory` / `XLEN <run events stream>` |
| MinIO | 磁盘 IO、对象数 | `mc admin info` 或宿主机 `iostat` |
| 沙盒 | 容器创建耗时、并发容器数 | sandbox-provisioner 日志 |

### 8.3 采样脚本示例

```bash
#!/bin/bash
# 压测期间后台运行：sampler.sh
while true; do
  ts=$(date +%s)
  docker stats --no-stream --format "{{.Name}},{{.CPUPerc}},{{.MemUsage}}" >> stats.csv
  docker compose exec postgres psql -U postgres -d yuxi -Atc \
    "SELECT state, count(*) FROM pg_stat_activity GROUP BY state" >> pg.csv
  docker compose exec redis redis-cli INFO clients | grep connected_clients >> redis.csv
  sleep 5
done
```

---

## 9. 验收指标（建议值，按内网硬件调整）

| 指标 | 目标 | 说明 |
|---|---|---|
| S1 只读 P95 | < 200 ms | 环境基线 |
| S3 提交 P99（600 VU 阶梯） | < 2 s | 仅接收与入队 |
| S4 端到端 P95（Stub 模型 100 并发） | < 30 s | 不含模型真实延迟 |
| Run 成功率 | ≥ 99.5% | 排除主动 cancel |
| S5 FIFO | 同线程 Run 严格串行，无并发 running | 架构不变量 |
| S6 SSE | 200 连接 30 min 保持率 ≥ 99% | 无服务端主动断流 |
| S7 上传（10 MB 文件，100 并发） | P95 < 10 s，错误率 < 0.5% | 网卡吞吐另记录 |
| S8 解析 | 全部 Task 终态收敛，文件状态一致 | 吞吐以解析服务为准 |
| S9 稳态 | api/worker 内存涨幅 < 10%，PG 连接无耗尽 | 无泄漏 |
| S10 恢复 | kill worker 后 5 min 内失联 Run 收敛为 failed，无悬挂 running | lease 语义正确 |

---

## 10. 风险与注意事项

1. **模型延迟掩盖平台问题**：Stub 模型必须能模拟流式输出（SSE chunk），否则
   测不出真实事件路径。同时给 Stub 加可控延迟（如固定 2s 首 token + 流式吐 100 token），
   使 Run 时长接近真实。
2. **线程与数据膨胀**：大量压测 Conversation/Message 会撑大 PG 与 `user-data`
   volume。压测后执行清理脚本（删除测试用户的 Conversation / Project）。
3. **沙盒冷启动**：首次 Sandbox 操作会惰性创建容器（`SANDBOX_HEALTH_TIMEOUT_SECONDS=300`），
   涉及工具的混合场景要为冷启动留预算，或预热若干沙盒。
4. **GPU 资源争抢**：mineru 与 paddlex 默认共用 GPU 0，S8 与 Agent 工具场景叠加时
   可能互相拖垮，需错峰或分配多卡。
5. **PG 连接池打满**：API(120+40) + worker(120+40) + migrator + 监控组件
   逼近 `max_connections=600`。压测中若出现 `FATAL: sorry, too many clients`，
   按报告调优小节调整。
6. **SSE 代理断连**：若压测机到 API 之间有内网 LB/Nginx，确认其支持 SSE
   （关闭 buffering、调大 read timeout），否则 S6 结果无效。
7. **时间同步**：内网多台机器部署时保证 NTP，否则 lease/heartbeat 时序指标失真。

---

## 11. 输出物

1. **压测报告**（每场景一节）：并发模型 → 指标曲线 → 拐点 → 瓶颈定位 → 证据
   （k6 JSON、stats.csv、pg.csv、关键日志片段）。
2. **调优建议表**：如 `POSTGRES_POOL_SIZE`、`ARQ_MAX_JOBS`、`max_connections`、
   uvicorn workers、MinIO 磁盘类型的调整建议与复测结果。
3. **可复用资产**：k6 脚本、`init_data.py`、采样脚本、清理脚本，归档到内网仓库
   `<repo>/loadtest/`，与部署文档一同维护。

---

## 附：快速检查清单（压测前逐项确认）

- [ ] 所有镜像已导入内网（含 `all-in-one-sandbox:1.11.0`）
- [ ] `docker compose up -d` 全绿，`/api/system/ready` 返回 200
- [ ] 模型供应商已注册且连通（Stub 或网内 vLLM）
- [ ] 压测 Agent 已创建，approval/tools 按场景配置
- [ ] k6 二进制可用，`BASE_URL/API_KEY/AGENT_SLUG` 环境变量就绪
- [ ] 压测用户 API Key 已创建（`yxkey_` 前缀，Bearer 头可用）
- [ ] 冒烟四连调通：建线程 → 提交请求 → 查询请求/Run → 附件上传
- [ ] 测试文件已生成并放到 `testdata/`
- [ ] 采样脚本已启动，磁盘空间足够（建议 > 50 GB 余量）
- [ ] 压测后清理脚本已备好
