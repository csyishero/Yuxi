# 生产环境端口

本文列出生产编排（`docker-compose.prod.yml`）发布到宿主机的全部端口、对应的环境变量、绑定地址与用途。开发编排的端口见[服务端口](./ports.md)。

## 端口映射

生产编排沿用开发端口 `+1000` 的约定，便于两套环境在同一台机器上并行运行。Web、MinerU、paddlex 三项例外，不遵循该约定。

| 变量 | 默认 | 容器内 | 服务 | 绑定地址 | 用途 |
| --- | --- | --- | --- | --- | --- |
| `YUXI_WEB_PORT` | 80 | 80 | `web` | 全部网卡 | Web 入口 |
| `YUXI_API_PORT` | 6050 | 5050 | `api` | 全部网卡 | API 与 Swagger 文档 |
| `YUXI_NEO4J_HTTP_PORT` | 8474 | 7474 | `graph` | 管理绑定地址 | Neo4j Browser |
| `YUXI_NEO4J_BOLT_PORT` | 8687 | 7687 | `graph` | 管理绑定地址 | Bolt 协议 |
| `YUXI_MINIO_CONSOLE_PORT` | 10001 | 9001 | `minio` | 管理绑定地址 | 管理控制台 |
| `YUXI_MILVUS_HEALTH_PORT` | 10091 | 9091 | `milvus` | 管理绑定地址 | 健康检查与 WebUI |
| `YUXI_MINIO_API_PORT` | 10000 | 9000 | `minio` | `127.0.0.1` | 对象 API |
| `YUXI_MILVUS_PORT` | 20530 | 19530 | `milvus` | `127.0.0.1` | gRPC |
| `YUXI_POSTGRES_PORT` | 6432 | 5432 | `postgres` | `127.0.0.1` | 数据库维护 |
| `YUXI_REDIS_PORT` | 7379 | 6379 | `redis` | `127.0.0.1` | 缓存与队列维护 |
| `YUXI_SANDBOX_PORT` | 9002 | 8002 | `sandbox-provisioner` | `127.0.0.1` | provisioner 排查 |
| `YUXI_MINERU_PORT` | 30001 | 30001 | `mineru-api` | `127.0.0.1` | MinerU `/file_parse` |
| `YUXI_PADDLEX_PORT` | 8080 | 8080 | `paddlex` | `127.0.0.1` | PP-Structure-V3 |

`worker`、`storage-migrator`、etcd 不发布任何端口，只在 Compose 网络内被其他服务访问。

`mineru-api` 与 `paddlex` 属于 `all` profile 的可选 OCR 服务，需要显式启用。

## 管理绑定地址

`YUXI_MANAGEMENT_BIND_HOST` 默认 `127.0.0.1`，只作用于上表标注「管理绑定地址」的 4 条映射。需要从内网其他机器访问管理界面时改为 `0.0.0.0`：

```bash
# .env.prod
YUXI_MANAGEMENT_BIND_HOST=0.0.0.0
```

修改后需要重建容器才能生效：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d graph minio milvus
```

对象 API、Milvus gRPC、PostgreSQL、Redis、provisioner 与两个 OCR 服务的端口固定绑定 `127.0.0.1`，不受该变量影响。

## 常用入口

以 `YUXI_MANAGEMENT_BIND_HOST=0.0.0.0`、主机地址 `<host>` 为例：

| 界面 | 地址 |
| --- | --- |
| Web | `http://<host>` |
| API 文档 | `http://<host>:6050/docs` |
| API 存活检查 | `http://<host>:6050/api/system/health` |
| API 就绪检查 | `http://<host>:6050/api/system/ready` |
| Neo4j Browser | `http://<host>:8474/browser/` |
| MinIO 控制台 | `http://<host>:10001` |
| Milvus WebUI | `http://<host>:10091/webui/` |
| provisioner | `http://127.0.0.1:9002/health`，仅宿主机可访问 |

Neo4j 的 Bolt 在宿主机上是 `8687`、容器内是 `7687`。Browser 打开后默认会尝试 `7687` 而连不上，需要手动把连接地址改成 `neo4j://<host>:8687`。

Milvus 的 `20530` 是 gRPC 端口，浏览器无法直接访问，需要 Attu、`pymilvus` 或 `milvus_cli` 这类客户端。

Swagger 文档页面依赖公网 CDN 加载静态资源，内网无外网时样式会丢失。

## 安全边界

管理界面没有 TLS 和额外认证层，只应在受信内网开放。公网部署保持默认的 `127.0.0.1`，改用 SSH 隧道访问：

```bash
ssh -N \
  -L 8474:127.0.0.1:8474 \
  -L 8687:127.0.0.1:8687 \
  -L 10001:127.0.0.1:10001 \
  <user>@<host>
```

MinIO 的访问密钥和 PostgreSQL 密码必须从默认值改掉后再对外暴露管理端口。生产入口、TLS、CORS 和密钥要求见[生产部署](./deployment.md)。
