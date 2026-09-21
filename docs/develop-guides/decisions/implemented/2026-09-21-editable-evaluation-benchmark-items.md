# 评估基准题目编辑与 Gold Chunk 文件定位

状态：implemented
类型：feature
Owner：`web/src/views/EvaluationBenchmarkDetailView.vue`

## 问题

评估基准详情只能查看题目和 Gold Answer，发现问题后必须重新制作并上传整个 JSONL。Gold Chunks 只展示内部 `chunk_id`，用户无法判断片段来自哪个真实文件，也不能从评估页面直接打开源文件核对内容。

## 决策

拥有知识库 `configure` 能力的管理员可以逐题修改问题与 Gold Answer。写入接口以数据集题目为资源边界执行权限校验，问题和答案规范化后，由 repository 在同一事务内更新题目及数据集的 `has_gold_chunks`、`has_gold_answers` 汇总状态。空问题被拒绝，空 Gold Answer 表示移除标注。已有评估运行继续保留启动时复制到 `evaluation_run_items` 的快照，编辑只影响后续运行。

数据集详情批量解析当前页涉及的 Gold Chunk，并在原 `gold_chunk_ids` 之外附加 `gold_chunks` 展示模型，包含真实文件 ID、文件名、片段序号、字符位置、内容摘要及有效性状态。查询按页聚合 chunk ID 和 file ID，分别执行一次批量查询，不把 N+1 查询转移到前端。

前端把每个 Gold Chunk 展示为文件卡片，真实文件名是主信息，片段序号与内容摘要用于核对，内部 chunk ID 作为次要排障信息。有效文件点击后复用 `FileDetailModal` 打开真实文件；已删除、跨知识库或失效引用明确展示为“未找到”，但不阻断整页加载。编辑入口只在前端确认 `configure` 能力后展示，后端权限仍是最终边界。

## 替代方案

- 继续要求下载、修改并重新上传 JSONL：操作成本高，会产生重复数据集，无法保留同一基准身份，不采用。
- 前端按每个 chunk 单独请求详情：会形成 N+1 请求，分页越大越明显，不采用。
- 只把 chunk ID 改成文件名文本：仍不能核对源文件内容，也无法识别失效引用，不采用。
- 同时开放 Gold Chunk 引用编辑：需要新增片段搜索、跨知识库校验和引用重排交互，本次“问答编辑”没有要求，暂不扩大写入面。

## 后果

评估题目成为可变配置，后续运行会读取最新版本；历史运行结果保持不可变快照。数据表结构没有变化，不需要数据库迁移。详情响应保留旧字段并增量增加 `gold_chunks`，旧客户端继续兼容。

数据集详情新增两次有上限的批量读取，换取可理解的文件身份和摘要。失效引用会暴露在界面供修复，但本次不自动修改或删除 Gold Chunk ID，避免静默改变评估标准。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 当前页 Gold Chunk 能定位真实文件且无 N+1 请求 | 只显示内部 ID、逐条查询或跨知识库泄露 | evaluation service | Gold Chunk 解析 unit、前端生产构建 | 缺失 chunk 与跨知识库 chunk 返回失效状态 | Passed |
| 有配置权限的管理员可以编辑问答，历史结果不被回写 | 空问题落库、无权限写入或修改历史运行 | evaluation router/service/repository | service unit、权限依赖、UI source unit | 空问题拒绝；普通用户写接口为 403 | Unit passed；真实 HTTP integration 因本地 integration 依赖服务未启动而未运行 |
| 数据集标注汇总与题目更新原子一致 | 题目保存成功但 `has_gold_answers` 仍为旧值 | evaluation repository | 单事务实现、integration 用例 | 清空最后一个答案后汇总应变为 false | Implementation verified；真实 PostgreSQL integration 未运行 |
| 前端可编辑并打开真实文件，且构建不出错 | 权限入口泄露、模板或弹窗装配失败 | `EvaluationBenchmarkDetailView.vue` | 定向 source unit、ESLint、Vite production build | 失效引用按钮禁用并保留 ID | Passed |
