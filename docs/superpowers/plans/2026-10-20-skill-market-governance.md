# Skill Market Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 管理员可下架市场 Skill，超级管理员可调整部门／全公司范围，受影响旧安装在新的 Agent 任务中立即失效并按规则恢复。

**Architecture:** `MarketSkill` 持久化当前上架状态、批次、批次版本下限和单调修订号，安装与申请记录关联批次；发布版本保持不可变。现有市场服务负责发布、安装、运行时解析，新治理用例位于 `yuxi.services`，数据库可见性和锁定查询由现有市场 repository 承担；HTTP 路由只做适配。投影撤销与 Agent 刷新共用 UID 事务锁，文件先移出可读目录再提交策略，失败时恢复。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy async、PostgreSQL、pytest、Vue 3、Vite、Vitest。

**Spec:** [技能市场下架与上架范围调整设计](../specs/2026-10-20-skill-market-governance-design.md)

## Global Constraints

- 下架保留稳定 `slug`、所有 `MarketSkillRelease`、`latest_release_id` 与安装记录；重新上架只能经作者新快照和审核产生递增版本。
- 下架后已安装用户的新 Agent 任务不可用；运行中的任务不主动取消；旧安装只有本人调用 `/upgrade` 确认当前批次后恢复，普通 `/install` 不得代替确认。
- 未经过下架的范围收窄／放宽不改变批次、版本、安装指针或历史申请；重入范围的旧安装自动恢复。
- 部门管理员只管理当前范围属于本部门的条目；超级管理员管理全部并独占范围修改；跨作者部门的续版审核仅超级管理员可通过。
- 全公司 → 部门必须给有效目标部门；依赖授权配置必须覆盖整个目标受众，工具存在，MCP／共享 Skill 可调用，不能只按操作者个人可见性判断。
- 普通目录、详情、排行、预览、安装、升级、点赞隐藏下架及范围外条目；本人旧安装只返回安全摘要及原因、卸载入口。
- 审核申请的 `read_scope`、`department_ids` 和既有发布目录保持不可变；市场条目的 `scope`／`department_id` 是当前策略。
- Schema v14 → v15 可重复执行，旧条目和安装回填首批次；只有迁移成功才记录版本。
- 锁顺序统一为市场条目 → 申请 → 按 UID 排序的投影锁；状态和操作日志同事务，撤销失败不得提交策略。
- 遵守根、`backend/`、`web/`、`docs/` 的 `AGENTS.md`；先写 tracked proposed decision，提交前做独立 Review；中文 Conventional Commit。
- 本 worktree 缺少 `.env`，后端测试使用挂载当前 `backend/` 的一次性 API 镜像和独立 PostgreSQL 数据库运行与所列 Compose 命令等价的 pytest；不得把共享 API 容器的结果算作本分支测试。

## Review Focus

1. 旧页面的状态在范围改动后又改回相同值：`state_revision` 仍须增加，旧写入返回 409；Task 4 的 `test_scope_aba_stale_revision`。
2. 用户已离职或当前无部门，但磁盘仍有旧市场投影：下架仍清理其目录；Task 4 的 `test_unpublish_revokes_orphaned_projection`。
3. 旧安装指向旧批次 v2，重新上架的 v4 包含以前同名文件：确认后只投影 v4 起的发布目录；Task 2 的 `test_relisted_projection_excludes_previous_generation`。
4. 全公司范围依赖仅授权一批指定 UID：即使超级管理员能调用也必须拒绝；Task 4 的 `test_global_scope_rejects_uid_only_dependency`。
5. 并发撤销到一半时文件移动失败：数据库和日志回滚，已移动目录恢复；Task 4 的 `test_partial_revocation_failure_restores_projection`。

---

### Task 1: Decision and v15 durable state

**Files:**
- Create: `docs/develop-guides/decisions/proposed/2026-10-20-skill-market-governance.md`
- Modify: `backend/package/yuxi/storage/postgres/models_business.py`
- Modify: `backend/package/yuxi/storage/postgres/manager.py`
- Modify: `backend/package/yuxi/storage_migration.py`
- Test: `backend/test/integration/services/test_schema_migration_version.py`
- Test: `backend/test/unit/services/test_storage_migration.py`

**Interfaces:**
- Produces: `MarketSkill.is_published: bool=True`, `publication_generation: int=1`, `active_from_version: int=1`, `state_revision: int=1`; `MarketSkillInstall.confirmed_generation: int=1`; `SkillShareRequest.market_generation: int|None` (`None` for shared channel).
- Produces: `BUSINESS_SCHEMA_VERSION = 15`; `ensure_skill_market_schema()` idempotently adds/backfills these fields after tables exist.

- [ ] **Step 1: Write the proposed decision** with problem, proposal, real alternatives, risks and the repository's acceptance evidence matrix; set Owner to the market service and identify schema, runtime and API owners in text.
- [ ] **Step 2: Write failing migration tests** `test_skill_market_v14_rows_backfill_first_generation` and `test_skill_market_v15_migration_is_idempotent`: assert old published listings/installs remain effective with 1/1/1/1, shared request generation stays null, repeated migration leaves release and install IDs untouched, failed migration does not record v15.
- [ ] **Step 3: Run the two tests** with `docker compose exec api uv run --group test pytest test/integration/services/test_schema_migration_version.py -q`; expect failure on absent columns/version.
- [ ] **Step 4: Add the exact columns and idempotent DDL/backfill** in the files above. Keep the migrator the sole Schema writer; do not rewrite release or historical scope rows.
- [ ] **Step 5: Re-run the migration file** with the same command; expect PASS. Run `git diff --check`; expect no whitespace errors.
- [ ] **Step 6: Commit** the decision, schema and tests with `feat(技能): 增加市场治理持久状态` after fresh independent review per AGENTS.

### Task 2: Public visibility and Agent eligibility

**Files:**
- Modify: `backend/package/yuxi/agents/skills/market_repository.py`
- Modify: `backend/package/yuxi/agents/skills/market.py`
- Test: `backend/test/integration/services/test_skill_market_http_postgres.py`
- Test: `backend/test/integration/services/test_skill_market_uninstall_http_postgres.py`
- Test: `backend/test/unit/agents/skills/test_skill_runtime.py`

**Interfaces:**
- Consumes: Task 1's state columns.
- Produces: `market_skill_visible(item: MarketSkill, user: User) -> bool` includes publication status; repository `visible*` and ranking queries filter `is_published`; `list_installed_market_skills()` and `market_projection_sources()` require current confirmed generation and `release.version >= active_from_version`.
- Produces: `list_user_market_installs()` safe entries with `unavailable_reason: "unpublished" | "out_of_scope" | "needs_confirmation" | None` and `visible`; for unavailable entries expose only stable slug, installed version, reason, and uninstall key. Visible relisted entries may include normal latest card data to enable explicit upgrade.
- Produces: `list_market_requests()` includes `existing_market_is_published` and current listing scope/status separately from each immutable request's `read_scope` and `department_ids`.

- [ ] **Step 1: Add failing tests** for every public read/write entry returning 404 or omission when withdrawn/out of scope, safe installed summaries, current listing status on author requests, runtime resolution and projection floor; `test_relisted_projection_excludes_previous_generation` must inspect actual projected source names.
- [ ] **Step 2: Run focused tests**: `docker compose exec api uv run --group test pytest test/integration/services/test_skill_market_http_postgres.py test/integration/services/test_skill_market_uninstall_http_postgres.py test/unit/agents/skills/test_skill_runtime.py -q`; expect new cases FAIL.
- [ ] **Step 3: Implement visibility and installation eligibility** in the two files. `published_slug_exists` must still reserve withdrawn slugs; `/uninstall` remains allowed through its direct lookup.
- [ ] **Step 4: Re-run focused tests** with the same command; expect PASS and verify database rows/files in tests.
- [ ] **Step 5: Commit** with `feat(技能): 阻断不可用市场技能的新任务` after independent review.

### Task 3: Serialized submission, approval and confirmation

**Files:**
- Modify: `backend/package/yuxi/agents/skills/sharing.py`
- Modify: `backend/package/yuxi/agents/skills/market.py`
- Modify: `backend/package/yuxi/agents/skills/market_repository.py`
- Test: `backend/test/integration/services/test_skill_market_http_postgres.py`
- Test: `backend/test/integration/services/test_user_skill_projection.py`
- Test: `backend/test/unit/services/test_skill_sharing.py` (direct market-helper fixture supplies explicit generation)

**Interfaces:**
- Consumes: Task 1 generation fields and Task 2 visibility.
- Produces: `submit_market_skill()` locks existing listing before snapshot copy, writes `request.market_generation = listing.publication_generation`, defaults new listing generation to 1; shared submissions stay unchanged.
- Produces: `review_market_skill()` acquires listing before request lock, rejects mismatched generation, on new listing or relist sets `is_published=True`, `active_from_version` to new release version for relist, increments `state_revision`; retains existing listing scope and stable slug.
- Produces: `install_market_skill(upgrade=True)` locks listing before install, writes `confirmed_generation` and latest release; `upgrade=False` with existing install never advances confirmation.

- [ ] **Step 1: Add failing tests** `test_old_pending_request_cannot_relist`, `test_fresh_snapshot_relists_same_slug_next_version`, `test_install_cannot_confirm_relisted_generation`, `test_upgrade_confirms_relisted_generation`; construct withdrawn listing rows directly in the fixture until Task 4 provides the operation. Override unrelated live API/sandbox cleanup autouse fixtures locally in `test_user_skill_projection.py` as `test_schema_migration_version.py` does, so this isolated suite can run.
- [ ] **Step 2: Run** `docker compose exec api uv run --group test pytest test/integration/services/test_skill_market_http_postgres.py test/integration/services/test_user_skill_projection.py -q`; expect new cases FAIL.
- [ ] **Step 3: Refactor shared snapshot submission only at the market seam** to accept a prelocked listing/generation and keep one transaction through copy + request insert; preserve direct shared-channel behavior. Make market approval lock order listing → request, with no publishing of stale generation.
- [ ] **Step 4: Implement installation confirmation** and re-run the focused command; expect PASS.
- [ ] **Step 5: Commit** with `feat(技能): 串行市场续版与安装确认` after independent review.

### Task 4: Governance service, target dependency checks and revocation

**Files:**
- Create: `backend/package/yuxi/services/skill_market_governance_service.py`
- Modify: `backend/package/yuxi/agents/skills/market_repository.py`
- Modify: `backend/package/yuxi/agents/skills/service.py`
- Test: `backend/test/integration/services/test_skill_market_http_postgres.py`
- Test: `backend/test/integration/services/test_user_skill_projection.py`

**Interfaces:**
- Produces: `list_manageable_market_skills(db: AsyncSession, *, operator: User) -> list[dict]`, `get_manageable_market_skill(db, *, slug: str, operator: User) -> dict`, `unpublish_market_skill(db, *, slug: str, expected_revision: int, operator: User) -> dict`, `change_market_skill_scope(db, *, slug: str, scope: str, department_id: int | None, expected_revision: int, operator: User) -> dict`.
- Produces: `MarketConflictError(ValueError)` for stale revision and `MarketScopeValidationError(ValueError)` with controlled `code`, optional dependency type/slug for scope validation. Scope codes are `invalid_scope`, `target_department_required`, `target_department_not_found`, `dependency_unavailable`, `dependency_scope`; dependency type is only `tool`, `mcp`, or `skill`. Other domain refusals remain ValueError. Management dict includes `slug`, `is_published`, `scope`, `department_id`, `publication_generation`, `state_revision`, `latest_version`, current author and status only for authorized admins.
- Produces: a public projection helper in `service.py` that acquires the same sorted UID advisory locks as `refresh_user_skill_projection_async`, stages all affected `market-release-<release_id>` directories outside user-visible roots and supports restoration before rollback, final cleanup after commit.

- [ ] **Step 1: Write failing real-PG tests** for admin/current-department authority, operation log, pending invalidation, revision conflict including `test_scope_aba_stale_revision`, valid scope widen/shrink, nonexistent target and structured scope-error codes, target audience dependency coverage including `test_global_scope_rejects_uid_only_dependency` across all current-generation releases, `test_unpublish_revokes_orphaned_projection`, controlled submit/downlist and upgrade/downlist lock interleavings and `test_partial_revocation_failure_restores_projection`.
- [ ] **Step 2: Run** `docker compose exec api uv run --group test pytest test/integration/services/test_skill_market_http_postgres.py test/integration/services/test_user_skill_projection.py -q`; expect new cases FAIL.
- [ ] **Step 3: Implement authorized management queries** in the repository and read use cases in the new service. Department admins filter by listing's current department, even when author belongs elsewhere; withdrawn entries remain in management queries.
- [ ] **Step 4: Implement target audience dependency validation** for every release in the current publication generation (`version >= active_from_version`), because new tasks may project earlier releases in that batch. Validate each dependency's stored scope against the entire target audience, including enabled/callable state. Reject before changing any policy or projection.
- [ ] **Step 5: Implement downlist and scope change** under listing lock with `expected_revision`, revision increment, operation log and old pending request rejection for downlist. Do not alter historical `read_scope`/`department_ids`.
- [ ] **Step 6: Implement reversible projection revocation** and call it before policy commit while locks are held; restore staged files on any error. Re-run the focused command; expect PASS, including file/DB assertions and concurrency cases.
- [ ] **Step 7: Commit** with `feat(技能): 支持下架与调整市场范围` after independent review.

### Task 5: HTTP contract and end-to-end backend proof

**Files:**
- Modify: `backend/server/routers/skill_market_router.py`
- Test: `backend/test/integration/services/test_skill_market_http_postgres.py`
- Test: `backend/test/integration/services/test_skill_market_uninstall_http_postgres.py`
- Test: `backend/test/e2e/test_skill_market_governance.py` (create only if the existing E2E fixture can execute a real Agent Run)

**Interfaces:**
- Consumes: Task 4 governance signatures and `MarketConflictError`.
- Produces: `GET /api/skill-market/-/management`, `GET /api/skill-market/-/management/{slug}`, `POST /api/skill-market/{slug}/unpublish` body `{expected_revision}`, `PUT /api/skill-market/{slug}/scope` body `{scope, department_id, expected_revision}`. Map stale state to 409, unauthorized to 403, inaccessible public listing to 404. Map scope validation to HTTP 400 `detail: {code, dependency_type?, dependency_slug?}` from the controlled Task 4 error; never place arbitrary exception text in this response.

- [ ] **Step 1: Add failing ASGI HTTP tests** covering the four new endpoints, each role, explicit department target, controlled structured 400 scope errors, 409, public leak paths and safe own-install/uninstall; query real PostgreSQL state and projection directories after each write.
- [ ] **Step 2: Run** `docker compose exec api uv run --group test pytest test/integration/services/test_skill_market_http_postgres.py test/integration/services/test_skill_market_uninstall_http_postgres.py -q`; expect new cases FAIL.
- [ ] **Step 3: Add thin Pydantic models and routes** importing Task 4 service functions; keep dynamic `/{slug}` routes after the fixed management paths.
- [ ] **Step 4: Run the focused HTTP command**; expect PASS. Run an existing Agent Run E2E with a withdrawn install; assert the new run never resolves the Skill and a previously running Run is not forcibly cancelled. If the E2E fixture is unavailable, record the precise environment limit and keep the integration/runtime oracle.
- [ ] **Step 5: Commit** with `feat(技能): 开放市场治理管理接口` after independent review.

### Task 6: Vue management and old-install flows

**Files:**
- Modify: `web/src/apis/skill_api.js`
- Modify: `web/src/components/extensions/SkillMarket.vue`
- Modify: `web/src/components/extensions/PublicationRecords.vue`
- Modify: `web/src/components/extensions/PersonalSkillPublish.vue`
- Modify: `web/src/components/extensions/publicationHistory.js`
- Modify: `web/src/components/extensions/skillHubCatalog.js`
- Test: `web/test/unit/skillMarketHome.test.js`
- Test: `web/test/unit/publicationHistory.test.js`
- Test: `web/test/unit/PersonalSkillPublish.test.js`
- Test: `web/test/unit/skillHubCatalog.test.js`

**Interfaces:**
- Consumes: Tasks 2 and 5 API fields and routes.
- Produces: API wrappers `listManagedMarketSkills`, `getManagedMarketSkill`, `unpublishMarketSkill`, `changeMarketSkillScope`; administrator management list/detail and confirmation actions; out-of-scope/withdrawn installed safe row; relisted stale install explicit `/upgrade` action; historical review scope separate from current listing scope/status. The request layer maps only known structured scope-validation codes to safe actionable text and sanitizes optional dependency slug; arbitrary HTTP 400 strings remain generic.

- [ ] **Step 1: Add failing UI/helper tests** for admin permissions, withdrawn management row, scope selector and stale revision refresh, old install safe summary and uninstall, relisted confirmation, author historic/current labels, and asynchronous detail race.
- [ ] **Step 2: Run** `cd web && pnpm run test:unit`; expect new cases FAIL.
- [ ] **Step 3: Add API wrappers and focused UI behavior** using existing design tokens/components; keep market catalog hidden entries absent and avoid an extra Agent-new-task panel.
- [ ] **Step 4: Run** `cd web && pnpm run lint:check && pnpm run test:unit && pnpm run build`; expect PASS. Open real page with admin, author and out-of-scope member states; save final screenshots in an allowed artifact path and inspect desktop/mobile appearance.
- [ ] **Step 5: Commit** with `feat(技能): 展示市场下架与范围管理` after independent review.

### Task 7: Current mechanism docs and final verification

**Files:**
- Move/rewrite: `docs/develop-guides/decisions/proposed/2026-10-20-skill-market-governance.md` → `docs/develop-guides/decisions/implemented/2026-10-20-skill-market-governance.md`
- Modify: `docs/agents/skills-management.md`

**Interfaces:**
- Consumes: implemented semantics and actual verification outputs from Tasks 1–6.
- Produces: present-tense decision with problem, decision, alternatives, consequences, verification; current mechanism page explains admin, user-install and runtime states, linking to source owners.

- [ ] **Step 1: Update current docs** only after the code owner and tests agree; verify relative links and avoid copying the spec as current documentation.
- [ ] **Step 2: Run focused integration/runtime tests and required gates**: `python3 scripts/verify_engineering_contracts.py`; `python3 -m unittest scripts.test_verify_engineering_contracts`; `docker compose exec api uv run --group test pytest test/unit -m 'not slow'`; focused PostgreSQL/HTTP/E2E tests; `docker compose exec api uv run ruff check package`; `docker compose exec api uv run ruff format package --check`; `cd web && pnpm run lint:check && pnpm run test:unit && pnpm run build`; `cd docs && pnpm run build`; `git diff --check`. Record actual pass/fail and distinguish pre-existing baseline issues.
- [ ] **Step 3: Dispatch a fresh whole-branch reviewer** with spec, decision, full diff and exact verification evidence; fix all material findings and rerun affected tests.
- [ ] **Step 4: Commit** docs and fixes with Chinese Conventional Commit only after the required independent review; hand over branch, tests and any environment limits without merging or pushing.
