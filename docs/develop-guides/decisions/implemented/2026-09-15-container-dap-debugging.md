# 容器内 API 与 Worker 的 DAP 调试入口

状态：implemented
类型：bug-fix
Owner：docker-compose.debug.yml

## 问题

本地失败配置曾只把宿主机 `5678` 映射到 API 容器，并把 `debugpy.listen()` 放在 `server.main` 的 `if __name__ == "__main__"` 分支。基础 Compose 直接导入 `server.main:app` 启动 Uvicorn，该分支不会执行，因此端口映射存在而容器内没有 DAP 监听器，PyCharm 无法连接；同时 AgentRun 在独立 worker 中执行，只调试 API 无法命中运行链路断点。

## 决策

使用独立的 `docker-compose.debug.yml` 覆盖文件，由 `python -m debugpy` 分别启动 API 和 worker。API 默认使用宿主机端口 `5678`，worker 默认使用 `5679`，并允许并行工作区覆盖宿主机端口；调试命令不启用 Uvicorn reload 或 watchfiles，避免监督进程与业务子进程分离造成断点漂移。基础 Compose 不暴露 DAP 端口，应用代码也不直接导入调试器。

## 替代方案

- 在 `server.main` 导入阶段调用 `debugpy.listen()`：普通运行也会产生调试副作用，并在 reload 子进程中遇到重复监听。
- 使用 `pydevd_pycharm.settrace()` 反向连接 PyCharm Debug Server：可用，但与用户选择的 Attach to DAP 连接模型相反，并引入第二套调试协议和配置。
- 只调试 API：不能覆盖实际执行 AgentRun 的 worker。

## 后果

- `--wait-for-client` 让 API/worker 在 IDE 附加前保持等待，调试启动期间健康检查暂时处于 `starting`。
- debugpy 作为后端运行依赖进入由同一 Dockerfile 构建的开发和生产镜像；生产 Compose 不启动监听器，也不暴露端口，但仍承担该依赖的镜像体积和供应链维护成本。
- API 与 worker 需要分别建立 DAP 会话，才能覆盖完整请求与 AgentRun 执行链路。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 调试配置启动后 API 在容器 `5678` 监听 | 只有 Docker 端口映射，没有进程监听 | `docker-compose.debug.yml` | Compose 合并配置、容器命令与 `/proc/net/tcp` 监听状态 | 移除 API 的 debugpy 启动包装后端口拒绝连接 | Passed |
| worker 可通过独立 `5679` 调试 | Agent 断点实际在 worker 中执行但只能连接 API | `docker-compose.debug.yml` | Compose 合并配置、容器命令与 `/proc/net/tcp` 监听状态 | 恢复 watchfiles 命令后 `5679` 不再监听 | Passed |
| 普通启动不暴露 DAP 端口或加载调试器 | 基础服务意外占用调试端口 | `docker-compose.yml`、`backend/server/main.py` | `docker compose config`、源码搜索与 `git diff --check` | 在基础 Compose 或应用入口重新加入调试监听会被搜索发现 | Passed |
| PyCharm 能绑定容器源码断点 | 本地与容器路径不一致导致空心断点 | `docs/develop-guides/contributing.md` | 按文档配置 `/backend` 到 `/app` 的路径映射并人工附加 | 删除路径映射后容器模块无法对应本地源码 | Not run |

- `docker compose -f docker-compose.yml -f docker-compose.debug.yml config --quiet` 通过；重建容器后确认 API `5678` 与 worker `5679` 均处于监听状态，且进程命令进入对应 debugpy 入口。
- `python3 scripts/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.test_verify_engineering_contracts` 通过。
- 后端非 slow unit 共 `1979 passed, 53 skipped`；跳过项依赖容器中未挂载的仓库根目录。
- 文档构建因执行环境无法从 npm registry 获取项目指定的 pnpm 版本而未完成；工程信任检查已验证文档结构与链接契约。
