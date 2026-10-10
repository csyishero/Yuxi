<template>
  <div class="skill-cards-page extension-page-root">
    <PageShoulder search-placeholder="搜索技能..." v-model:search="searchQuery">
      <template #actions>
        <template v-if="!isBatchDeleteMode">
          <a-button v-if="userStore.isAdmin" @click="openShareRequests" :disabled="loading">
            共享申请<span v-if="pendingShareRequestCount"> ({{ pendingShareRequestCount }})</span>
          </a-button>
          <a-button
            @click="isBatchDeleteMode = true"
            :disabled="loading || importing || filteredDeletableSkills.length === 0"
            class="lucide-icon-btn"
          >
            <span>批量管理</span>
          </a-button>
          <a-button
            @click="handleOpenRemoteInstall"
            :disabled="loading || importing"
            class="lucide-icon-btn"
          >
            <Computer :size="14" />
            <span>远程安装</span>
          </a-button>
          <a-upload
            accept=".zip,.md"
            :show-upload-list="false"
            :custom-request="handleImportUpload"
            :before-upload="beforeSkillUpload"
            :disabled="loading || importing"
          >
            <a-button type="primary" :loading="importing" class="lucide-icon-btn">
              <Upload :size="14" />
              <span>上传 Skill</span>
            </a-button>
          </a-upload>
          <a-button
            class="lucide-icon-btn"
            aria-label="刷新 Skills"
            :disabled="loading"
            @click="fetchSkills({ refreshPersonal: true })"
          >
            <RefreshCw :size="14" />
          </a-button>
        </template>
        <template v-else>
          <a-button size="small" type="link" @click="handleBatchSelectAll">全选</a-button>
          <a-button size="small" type="link" @click="handleBatchSelectInvert">反选</a-button>
          <a-button size="small" type="link" @click="handleBatchSelectNone">清空</a-button>
          <a-button
            type="primary"
            danger
            :disabled="selectedCardSlugs.length === 0"
            :loading="loading"
            @click="handleBatchDelete"
          >
            批量删除 ({{ selectedCardSlugs.length }})
          </a-button>
          <a-button :disabled="loading" @click="exitBatchDeleteMode">退出管理</a-button>
        </template>
      </template>
    </PageShoulder>

    <div v-if="userStore.isAdmin && !isBatchDeleteMode" class="skill-catalog-filters">
      <a-select
        v-if="userStore.isSuperAdmin"
        v-model:value="catalogDepartmentId"
        class="skill-catalog-department"
        placeholder="全部部门"
        allow-clear
        :options="departmentOptions"
        @change="applyCatalogFilters"
      />
      <span v-else class="skill-catalog-scope">本部门：{{ userStore.departmentName || '未分配部门' }}</span>
      <a-input
        v-model:value="ownerSearch"
        class="skill-catalog-owner"
        placeholder="搜索姓名或账号"
        allow-clear
        @pressEnter="applyCatalogFilters"
      />
      <a-button :loading="loading || catalogLoading" @click="applyCatalogFilters">搜索人员</a-button>
    </div>

    <div
      v-if="visibleSkillGroups.length === 0"
      class="extension-card-grid-empty-state skill-empty-state"
    >
      <div class="skill-empty-card">
        <div class="skill-empty-icon">
          <WandSparkles :size="22" />
        </div>
        <div class="skill-empty-title">
          {{ searchQuery ? '没有匹配的 Skill' : '还没有添加 Skill' }}
        </div>
        <div class="skill-empty-desc">
          {{
            searchQuery
              ? '换个关键词试试，或清空搜索条件。'
              : '可以从远程仓库安装，或上传本地 Skill 文件。'
          }}
        </div>
      </div>
    </div>

    <template v-else>
      <template v-for="group in visibleSkillGroups" :key="group.key">
        <div class="extension-section-header">{{ group.title }}</div>
        <ExtensionCardGrid :min-width="280">
          <div
            v-for="skill in group.skills"
            :key="`${group.key}:${skill.owner_uid || ''}:${skill.slug || skill.id}`"
            class="card-wrapper"
            :class="{
              selected: selectedCardSlugs.includes(skill.slug),
              'batch-mode': isBatchDeleteMode
            }"
          >
            <a-checkbox
              v-if="
                isBatchDeleteMode &&
                canManageSkill(skill) &&
                skill.sourceType !== 'builtin' &&
                skill.sourceScope !== 'personal'
              "
              :checked="selectedCardSlugs.includes(skill.slug)"
              @change="handleToggleCardSelect(skill.slug)"
              class="card-select-checkbox"
            />
            <InfoCard
              variant="default"
              :title="formatExtensionCardTitle(skill.name)"
              :subtitle="skill.slug"
              :description="skill.description || '暂无描述'"
              :tags="skillCardTags(skill)"
              :default-icon="getSkillIcon(skill.slug)"
              @click="handleCardClick(skill)"
              :class="{ 'card-clickable-select': isBatchDeleteMode }"
            >
              <template #actions>
                <button
                  v-if="skill.sourceScope !== 'personal'"
                  type="button"
                  class="skill-enabled-action"
                  :class="{ enabled: skill.enabled !== false }"
                  :disabled="!canManageSkill(skill) || isSkillToggling(skill.slug)"
                  :aria-label="skill.enabled === false ? '启用 Skill' : '禁用 Skill'"
                  @click.stop="handleToggleSkillEnabled(skill)"
                >
                  <Plus v-if="skill.enabled === false" :size="15" class="action-icon" />
                  <template v-else>
                    <Check :size="15" class="action-icon action-icon-check" />
                    <Minus :size="15" class="action-icon action-icon-minus" />
                  </template>
                </button>
              </template>
            </InfoCard>
          </div>
        </ExtensionCardGrid>
        <div v-if="group.alwaysShow && !group.skills.length" class="skill-catalog-empty">
          {{ catalogLoading ? '正在加载个人 Skill…' : '当前筛选下没有个人 Skill' }}
        </div>
        <div v-if="group.key === 'personal' && catalogNextOffset !== null" class="skill-catalog-more">
          <a-button :loading="catalogLoading" @click="fetchCatalog()">加载更多人员的 Skill</a-button>
        </div>
      </template>
    </template>

    <a-modal
      v-model:open="skillPreviewVisible"
      class="skill-preview-modal"
      :footer="null"
      width="680px"
      :closable="false"
      :destroy-on-close="true"
      @cancel="closeSkillPreview"
    >
      <div v-if="previewSkill" class="skill-preview-panel">
        <div class="skill-preview-header">
          <div class="skill-preview-title-area">
            <div class="skill-preview-icon">
              <component :is="getSkillIcon(previewSkill.slug)" :size="18" />
            </div>
            <div class="skill-preview-title-text">
              <div class="skill-preview-title">
                {{ formatExtensionCardTitle(previewSkill.name) }}
              </div>
              <div class="skill-preview-meta">
                <span
                  >{{
                    sourceTypeLabel(previewSkill.sourceType || previewSkill.source_type)
                  }}
                  Skill</span
                >
                <span v-if="previewSkill.enabled === false" class="skill-preview-disabled-tag">
                  已禁用
                </span>
                <span v-if="previewSkill.owner_uid">
                  {{ previewSkill.owner_name || previewSkill.owner_uid }} ·
                  {{ previewSkill.owner_department_name || '未分配部门' }}
                </span>
              </div>
            </div>
          </div>
          <div class="skill-preview-actions">
            <a-switch
              v-if="previewSkill.sourceScope !== 'personal'"
              :checked="previewSkill.enabled !== false"
              :disabled="!canManageSkill(previewSkill) || isSkillToggling(previewSkill.slug)"
              :loading="isSkillToggling(previewSkill.slug)"
              size="small"
              @change="handlePreviewToggle"
            />
          </div>
        </div>

        <div class="skill-preview-body">
          <a-alert
            v-if="previewSkill.overrides_shared"
            type="warning"
            show-icon
            message="当前个人 Skill 与共享 Skill 同名；你使用的是个人版本，团队成员使用共享版本。"
            class="skill-share-warning"
          />
          <a-alert
            v-if="previewSkill.shadowed_by_personal"
            type="warning"
            show-icon
            message="你有同名个人 Skill；当前共享版本不会在你的对话中生效。"
            class="skill-share-warning"
          />
          <a-alert
            v-if="previewSkill.sourceScope === 'personal' && previewShareRequest"
            :type="previewShareRequest.status === 'rejected' ? 'warning' : 'info'"
            show-icon
            :message="`共享申请：${shareRequestStatusLabel(previewShareRequest.status)}${previewShareRequest.published_slug ? ` · ${previewShareRequest.published_slug}` : ''}${previewShareRequest.review_note ? ` · ${previewShareRequest.review_note}` : ''}`"
            class="skill-share-warning"
          />
          <div v-if="skillPreviewLoading" class="skill-preview-loading">
            <a-spin />
          </div>
          <MarkdownPreview
            v-else-if="skillPreviewMarkdown"
            :content="skillPreviewMarkdown"
            :compact="true"
          />
          <a-empty v-else :description="skillPreviewError || '未读取到 SKILL.md'" />
        </div>

        <div class="skill-preview-footer">
          <div class="skill-preview-footer-left">
            <a-button
              v-if="canDeletePreviewSkill"
              danger
              :loading="deletingPreviewSkill"
              @click="confirmDeletePreviewSkill"
            >
              卸载
            </a-button>
          </div>
          <div class="skill-preview-footer-right">
            <a-button @click="closeSkillPreview">关闭</a-button>
            <a-button
              v-if="!userStore.isAdmin && previewSkill.sourceScope === 'personal' && !isOtherPersonalSkill(previewSkill)"
              type="primary"
              :disabled="previewShareRequest?.status === 'pending'"
              :loading="submittingShareRequest"
              @click="submitPreviewShareRequest"
            >
              {{ previewShareRequest?.status === 'pending' ? '等待审核' : '申请共享' }}
            </a-button>
            <a-button
              v-if="userStore.isAdmin && previewSkill.sourceScope === 'personal'"
              type="primary"
              :disabled="skillPreviewLoading || !!skillPreviewError || previewShareRequest?.status === 'pending'"
              @click="openDirectPublish"
            >
              {{ previewShareRequest?.status === 'pending' ? '已有待审申请' : '直接转为共享' }}
            </a-button>
            <a-button
              :type="previewSkill.sourceScope === 'personal' ? 'default' : 'primary'"
              v-if="!isOtherPersonalSkill(previewSkill)"
              class="lucide-icon-btn"
              @click="goToPreviewSkillManagement"
            >
              <span>{{ previewSkill.sourceScope === 'personal' ? '管理个人 Skill' : '去管理' }}</span>
            </a-button>
          </div>
        </div>
      </div>
    </a-modal>

    <a-modal
      v-model:open="directPublishVisible"
      title="直接发布共享 Skill"
      width="760px"
      ok-text="确认发布"
      :confirm-loading="publishingPersonalSkill"
      @ok="confirmDirectPublish"
    >
      <p>将 {{ directPublishSkill?.owner_name || directPublishSkill?.owner_uid || userStore.uid }} 的「{{ directPublishSkill?.name }}」当前内容复制为共享版本，个人原件保留。</p>
      <ShareConfigForm
        v-model="directPublishShareConfig"
        :require-read-scope="true"
        :show-manage-scope="false"
        :allowed-access-levels="['department', 'user']"
        :available-departments="userStore.isSuperAdmin ? null : ownDepartmentOptions"
        :available-users="shareUsers"
        :disabled="shareUsersLoading"
      />
      <a-input v-model:value="directPublishNote" class="skill-share-note" placeholder="发布说明（可选）" :maxlength="2000" />
    </a-modal>

    <a-modal
      v-model:open="shareRequestsVisible"
      title="Skill 共享申请"
      width="760px"
      :footer="null"
      :destroy-on-close="true"
      @cancel="closeShareRequests"
    >
      <a-spin :spinning="shareRequestsLoading">
        <a-empty v-if="!shareRequests.length" description="暂无共享申请" />
        <div v-for="request in shareRequests" :key="request.id" class="skill-share-request">
          <div class="skill-share-request-heading">
            <strong>{{ request.name }}</strong>
            <a-tag :color="request.status === 'pending' ? 'blue' : request.status === 'approved' ? 'green' : 'default'">
              {{ shareRequestStatusLabel(request.status) }}
            </a-tag>
          </div>
          <div class="skill-share-request-meta">
            {{ request.personal_slug }} · 申请人 {{ request.owner_uid }}
            <span v-if="request.published_slug"> · 已发布为 {{ request.published_slug }}</span>
          </div>
          <div class="skill-share-request-meta">内容摘要 {{ request.content_hash.slice(0, 12) }}</div>
          <p v-if="request.review_note">审核意见：{{ request.review_note }}</p>
          <a-button size="small" @click="openShareSnapshot(request)">查看提交内容</a-button>
          <template v-if="userStore.isAdmin && request.status === 'pending'">
            <ShareConfigForm
              v-model="reviewShareConfigs[request.id]"
              :require-read-scope="true"
              :show-manage-scope="false"
              :allowed-access-levels="['department', 'user']"
              :available-departments="userStore.isSuperAdmin ? null : ownDepartmentOptions"
              :available-users="shareUsers"
              :disabled="shareUsersLoading || reviewingRequestId === request.id"
            />
            <div class="skill-share-review-actions">
              <a-input v-model:value="reviewNotes[request.id]" placeholder="审核意见（可选）" :maxlength="2000" />
              <a-button type="primary" :loading="reviewingRequestId === request.id" @click="reviewShareRequest(request, 'approve')">批准并发布</a-button>
              <a-button danger :loading="reviewingRequestId === request.id" @click="reviewShareRequest(request, 'reject')">驳回</a-button>
            </div>
          </template>
        </div>
      </a-spin>
    </a-modal>

    <a-modal
      v-model:open="shareSnapshotVisible"
      title="提交时的完整 Skill 快照"
      width="840px"
      :footer="null"
      @cancel="closeShareSnapshot"
    >
      <div class="skill-share-snapshot-actions">
        <span>审核快照中的全部文件；发布会复制这一份内容。</span>
        <a-button v-if="userStore.isAdmin" size="small" @click="downloadShareSnapshot">下载完整快照</a-button>
      </div>
      <div class="skill-share-snapshot-layout">
        <aside class="skill-share-snapshot-tree" aria-label="快照文件">
          <button
            v-for="entry in flatShareSnapshotTree"
            :key="entry.path"
            type="button"
            :class="{ active: shareSnapshotPath === entry.path, directory: entry.is_dir }"
            :style="{ paddingLeft: `${12 + entry.depth * 16}px` }"
            :disabled="entry.is_dir"
            @click="openShareSnapshotFile(entry.path)"
          >
            {{ entry.is_dir ? '▾' : '·' }} {{ entry.name }}
          </button>
        </aside>
        <div class="skill-share-snapshot-content">
          <strong>{{ shareSnapshotPath || '选择文件' }}</strong>
          <a-spin :spinning="shareSnapshotLoading">
            <a-alert v-if="shareSnapshotError" type="warning" :message="shareSnapshotError" show-icon />
            <MarkdownPreview
              v-else-if="shareSnapshotMarkdown && shareSnapshotPath.toLowerCase().endsWith('.md')"
              :content="shareSnapshotMarkdown"
              :compact="true"
            />
            <pre v-else-if="shareSnapshotMarkdown" class="skill-share-snapshot-code">{{ shareSnapshotMarkdown }}</pre>
          </a-spin>
        </div>
      </div>
    </a-modal>

    <SkillInstallFlowModal
      :open="installFlowOpen"
      :flow="installFlow"
      @close="closeInstallFlow"
      @completed="handleInstallFlowCompleted"
    >
      <template #selection>
        <div class="remote-install-panel">
          <a-tabs v-model:activeKey="activeTab" class="install-tabs">
            <!-- Tab 1: 按仓库拉取 -->
            <a-tab-pane key="repo" tab="按仓库拉取">
              <div class="tab-content-wrapper">
                <a-form layout="vertical" class="remote-install-form">
                  <div class="repo-input-row">
                    <div class="repo-input-field">
                      <a-input
                        v-model:value="remoteInstallForm.source"
                        placeholder="来源仓库或 Skill 地址，如 owner/repo"
                      >
                        <template #suffix>
                          <a-dropdown
                            :trigger="['click']"
                            placement="bottomRight"
                            overlay-class-name="history-dropdown-menu"
                          >
                            <div class="history-trigger-wrapper">
                              <a-tooltip title="历史仓库">
                                <History
                                  :size="14"
                                  class="history-icon-trigger"
                                  :class="{ 'has-history': repoHistory.length > 0 }"
                                />
                              </a-tooltip>
                            </div>
                            <template #overlay>
                              <a-menu @click="handleSelectHistory">
                                <a-menu-item v-if="repoHistory.length === 0" disabled>
                                  <span class="history-empty-text">暂无使用历史</span>
                                </a-menu-item>
                                <template v-else>
                                  <a-menu-item v-for="item in repoHistory" :key="item">
                                    <div class="history-item-menu-row">
                                      <span class="history-item-text" :title="item">{{
                                        item
                                      }}</span>
                                      <span
                                        class="history-item-del-btn"
                                        @click.stop="deleteHistoryItem(item)"
                                      >
                                        <Trash2 :size="12" />
                                      </span>
                                    </div>
                                  </a-menu-item>
                                  <a-menu-divider />
                                  <a-menu-item
                                    key="clear-all-history"
                                    class="clear-history-menu-item"
                                  >
                                    <div class="clear-history-btn-content">
                                      <Trash2 :size="12" class="clear-icon" />
                                      <span>清空历史记录</span>
                                    </div>
                                  </a-menu-item>
                                </template>
                              </a-menu>
                            </template>
                          </a-dropdown>
                        </template>
                      </a-input>
                    </div>
                    <a-button
                      type="primary"
                      :loading="listingRemoteSkills"
                      @click="handleListRemoteSkills"
                    >
                      拉取技能
                    </a-button>
                  </div>
                  <div class="repo-hint-text">
                    支持 `owner/repo` 或 GitHub URL。可前往
                    <a href="https://skills.sh/" target="_blank" rel="noopener noreferrer"
                      >skills.sh</a
                    >
                    查询开源 skills。 也支持 ModelScope 单个 Skill
                    地址，每次仅限安装一个：`https://modelscope.cn/skills/&lt;skill-id&gt;`。 Skill
                    ID 可在
                    <a href="https://modelscope.cn/skills" target="_blank" rel="noopener noreferrer"
                      >ModelScope Skill 市场</a
                    >
                    进入详情后从地址栏获取。
                  </div>

                  <!-- 仓库技能多选列表 -->
                  <div v-if="remoteSkillOptions.length" class="skills-list-section">
                    <template v-if="hasSingleRepoSkill">
                      <div class="single-remote-skill-card">
                        <div class="single-remote-skill-name">{{ singleRepoSkill.name }}</div>
                        <div class="single-remote-skill-meta">
                          {{ singleRepoSkill.description || '暂无描述' }}
                        </div>
                      </div>
                    </template>
                    <template v-else>
                      <div class="list-operations-bar">
                        <div class="op-buttons">
                          <a-button size="small" type="link" @click="handleRepoSelectAll"
                            >全选</a-button
                          >
                          <a-button size="small" type="link" @click="handleRepoSelectInvert"
                            >反选</a-button
                          >
                          <a-button size="small" type="link" @click="handleRepoSelectNone"
                            >清空</a-button
                          >
                        </div>
                        <a-input
                          v-model:value="repoFilterKeyword"
                          placeholder="本地过滤检索..."
                          size="small"
                          style="width: 180px"
                          allow-clear
                        />
                      </div>
                      <div class="skills-list-viewport">
                        <div class="remote-skills-list-container">
                          <div
                            v-for="item in filteredRepoSkills"
                            :key="item.name"
                            class="remote-skill-row"
                            :class="{ selected: selectedRepoSkills.includes(item.name) }"
                            role="checkbox"
                            tabindex="0"
                            :aria-checked="selectedRepoSkills.includes(item.name)"
                            @click="toggleRepoSkillFromRow(item.name)"
                            @keydown.enter.prevent="toggleRepoSkillFromRow(item.name)"
                            @keydown.space.prevent="toggleRepoSkillFromRow(item.name)"
                          >
                            <a-checkbox
                              class="remote-row-checkbox"
                              :checked="selectedRepoSkills.includes(item.name)"
                              :tabindex="-1"
                              aria-hidden="true"
                            />
                            <div class="remote-skill-row-content">
                              <span class="skill-item-name">{{ item.name }}</span>
                              <span class="skill-item-desc">{{
                                item.description || '暂无描述'
                              }}</span>
                            </div>
                          </div>
                        </div>
                      </div>
                    </template>
                  </div>
                </a-form>
              </div>
            </a-tab-pane>

            <!-- Tab 2: 全局搜索发现 -->
            <a-tab-pane key="search" tab="全局搜索发现">
              <div class="tab-content-wrapper">
                <a-form layout="vertical" class="remote-install-form">
                  <div class="repo-input-row">
                    <div class="repo-input-field">
                      <a-input
                        v-model:value="searchKeyword"
                        placeholder="输入 web、python 等关键字进行全局查找"
                        @pressEnter="handleSearchRemoteSkills"
                      />
                    </div>
                    <a-button
                      type="primary"
                      :loading="searchingRemoteSkills"
                      @click="handleSearchRemoteSkills"
                    >
                      查找技能
                    </a-button>
                  </div>
                  <div class="repo-hint-text">
                    直接输入关键字检索 skills.sh 上的开源 Skills 并批量拉取安装。
                  </div>

                  <!-- 搜索结果列表 -->
                  <div v-if="searchedSkills.length" class="skills-list-section">
                    <template v-if="hasSingleSearchedSkill">
                      <div class="single-remote-skill-card">
                        <div class="single-remote-skill-header">
                          <div class="single-remote-skill-name">
                            {{ singleSearchedSkill.name }}
                          </div>
                          <a-tag v-if="singleSearchedSkill.installs" class="skill-item-installs">
                            {{ singleSearchedSkill.installs }}
                          </a-tag>
                        </div>
                        <div class="single-remote-skill-meta">
                          {{ singleSearchedSkill.source }}
                        </div>
                      </div>
                    </template>
                    <template v-else>
                      <div class="list-operations-bar">
                        <div class="op-buttons">
                          <a-button size="small" type="link" @click="handleSearchSelectAll"
                            >全选</a-button
                          >
                          <a-button size="small" type="link" @click="handleSearchSelectInvert"
                            >反选</a-button
                          >
                          <a-button size="small" type="link" @click="handleSearchSelectNone"
                            >清空</a-button
                          >
                        </div>
                      </div>
                      <div class="skills-list-viewport">
                        <div class="remote-skills-list-container">
                          <div
                            v-for="item in searchedSkills"
                            :key="`${item.source}:${item.name}`"
                            class="remote-skill-row"
                            :class="{ selected: isSearchSkillSelected(item) }"
                            role="checkbox"
                            tabindex="0"
                            :aria-checked="isSearchSkillSelected(item)"
                            @click="toggleSearchSkillFromRow(item)"
                            @keydown.enter.prevent="toggleSearchSkillFromRow(item)"
                            @keydown.space.prevent="toggleSearchSkillFromRow(item)"
                          >
                            <a-checkbox
                              class="remote-row-checkbox"
                              :checked="isSearchSkillSelected(item)"
                              :tabindex="-1"
                              aria-hidden="true"
                            />
                            <div class="remote-skill-row-content">
                              <span class="skill-item-name">{{ item.name }}</span>
                              <span class="skill-item-desc">{{ item.source }}</span>
                            </div>
                            <span v-if="item.installs" class="skill-install-count">
                              {{ item.installs }}
                            </span>
                          </div>
                        </div>
                      </div>
                    </template>
                  </div>
                </a-form>
              </div>
            </a-tab-pane>
          </a-tabs>
        </div>
      </template>

      <template #selection-footer>
        <span class="modal-footer-summary">{{ remoteSelectionSummary }}</span>
        <div class="modal-footer-buttons">
          <a-button @click="closeInstallFlow">取消</a-button>
          <a-button
            type="primary"
            :disabled="
              activeTab === 'repo'
                ? selectedRepoSkills.length === 0
                : selectedSearchSkills.length === 0
            "
            @click="startInstallRemoteSkills"
          >
            解析并确认
          </a-button>
        </div>
      </template>
    </SkillInstallFlowModal>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import {
  RefreshCw,
  Upload,
  Computer,
  WandSparkles,
  History,
  Trash2,
  Check,
  Plus,
  Minus
} from '@lucide/vue'
import { skillApi } from '@/apis/skill_api'
import ExtensionCardGrid from './ExtensionCardGrid.vue'
import SkillInstallFlowModal from './SkillInstallFlowModal.vue'
import InfoCard from '@/components/shared/InfoCard.vue'
import PageShoulder from '@/components/shared/PageShoulder.vue'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import ShareConfigForm from '@/components/ShareConfigForm.vue'
import { departmentApi } from '@/apis/department_api'
import { authApi } from '@/apis/auth_api'
import { useUserStore } from '@/stores/user'
import { formatExtensionCardTitle } from '@/utils/extensionDisplayName'
import { getShareConfigLabel } from '@/utils/shareConfig'
import { getSkillIcon } from '@/utils/skill_icon_utils'

const RETIRED_BUILTIN_SKILL_SLUGS = new Set(['image-gen', 'mysql-reporter'])

const router = useRouter()
const userStore = useUserStore()

const loading = ref(false)
const importing = ref(false)
const listingRemoteSkills = ref(false)
const searchQuery = ref('')

const isBatchDeleteMode = ref(false)
const selectedCardSlugs = ref([])
const togglingSkillSlugs = ref([])

const skills = ref([])
const catalogCards = ref([])
const catalogDepartmentId = ref(undefined)
const ownerSearch = ref('')
const appliedDepartmentId = ref(undefined)
const appliedOwnerSearch = ref('')
const catalogNextOffset = ref(null)
const catalogLoading = ref(false)
let catalogRequestSeq = 0
const skillPreviewVisible = ref(false)
const previewSkill = ref(null)
const skillPreviewMarkdown = ref('')
const skillPreviewLoading = ref(false)
const skillPreviewError = ref('')
const deletingPreviewSkill = ref(false)
const submittingShareRequest = ref(false)
const directPublishVisible = ref(false)
const directPublishSkill = ref(null)
const directPublishShareConfig = ref(null)
const directPublishNote = ref('')
const publishingPersonalSkill = ref(false)
const shareRequests = ref([])
const shareRequestsVisible = ref(false)
const shareRequestsLoading = ref(false)
const shareSnapshotVisible = ref(false)
const shareSnapshotLoading = ref(false)
const shareSnapshotMarkdown = ref('')
const shareSnapshotTree = ref([])
const shareSnapshotPath = ref('')
const shareSnapshotRequest = ref(null)
const shareSnapshotError = ref('')
let shareSnapshotRequestSeq = 0
const reviewingRequestId = ref('')
const reviewShareConfigs = reactive({})
const reviewNotes = reactive({})
const departmentOptions = ref([])
const shareUsers = ref([])
const shareUsersLoading = ref(false)
const ownDepartmentOptions = computed(() =>
  userStore.departmentId
    ? [{ id: Number(userStore.departmentId), name: userStore.departmentName || '本部门' }]
    : []
)
let previewRequestSeq = 0
const installFlowOpen = ref(false)
const installFlow = ref(null)

const activeTab = ref('repo') // 'repo' 或 'search'

const remoteInstallForm = reactive({
  source: '',
  skills: []
})
const remoteSkillOptions = ref([])
const repoFilterKeyword = ref('')
const selectedRepoSkills = ref([])
const hasSingleRepoSkill = computed(() => remoteSkillOptions.value.length === 1)
const singleRepoSkill = computed(() => remoteSkillOptions.value[0] || null)

const searchKeyword = ref('')
const searchingRemoteSkills = ref(false)
const searchedSkills = ref([])
const selectedSearchSkills = ref([])
const hasSingleSearchedSkill = computed(() => searchedSkills.value.length === 1)
const singleSearchedSkill = computed(() => searchedSkills.value[0] || null)
const remoteSelectionSummary = computed(() => {
  if (activeTab.value === 'repo') {
    if (!remoteSkillOptions.value.length) return '请先拉取仓库中的 Skill'
    return `已选 ${selectedRepoSkills.value.length} / 共发现 ${remoteSkillOptions.value.length} 个 Skill`
  }

  if (!searchedSkills.value.length) return '请输入关键词查找 Skill'
  return `已选 ${selectedSearchSkills.value.length} / 共找到 ${searchedSkills.value.length} 个 Skill`
})

const repoHistory = ref([])

const matchesSearch = (skill) => {
  if (!searchQuery.value) return true
  const q = searchQuery.value.toLowerCase()
  const text = [
    skill.name,
    skill.slug,
    skill.description,
    skill.owner_name,
    skill.owner_uid,
    skill.owner_department_name,
    ...(skill.skills || []).flatMap((item) => [item.name, item.slug, item.description])
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
  return text.includes(q)
}

const installedSkillCards = computed(() =>
  (skills.value || [])
    .filter(
      (skill) => skill.source_type !== 'builtin' || !RETIRED_BUILTIN_SKILL_SLUGS.has(skill.slug)
    )
    .map((skill) => ({
      ...skill,
      sourceType: skill.source_type || 'upload',
      sourceScope: skill.source_scope
    }))
)

const filteredInstalledSkills = computed(() => installedSkillCards.value.filter(matchesSearch))
const catalogPersonalSkills = computed(() =>
  catalogCards.value
    .map((skill) => ({ ...skill, sourceType: 'personal', sourceScope: 'personal' }))
    .filter(matchesSearch)
)
const getReadScope = (skill) =>
  skill?.share_config?.version === 2
    ? skill.share_config.read_scope
    : skill?.share_config
const isDepartmentShared = (skill) => getReadScope(skill)?.access_level === 'department'
const isPersonShared = (skill) => getReadScope(skill)?.access_level === 'user'
const skillGroups = computed(() => [
  {
    key: 'builtin',
    title: '全局 · 内置 Skill',
    skills: filteredInstalledSkills.value.filter((skill) => skill.sourceType === 'builtin')
  },
  {
    key: 'department-shared',
    title: '部门共享 Skill',
    skills: filteredInstalledSkills.value.filter(
      (skill) =>
        skill.sourceType !== 'builtin' &&
        skill.sourceScope !== 'personal' &&
        isDepartmentShared(skill)
    )
  },
  {
    key: 'person-shared',
    title: '指定人员共享 Skill',
    skills: filteredInstalledSkills.value.filter(
      (skill) =>
        skill.sourceType !== 'builtin' &&
        skill.sourceScope !== 'personal' &&
        isPersonShared(skill)
    )
  },
  {
    key: 'other-shared',
    title: '其他共享范围',
    skills: filteredInstalledSkills.value.filter(
      (skill) =>
        skill.sourceType !== 'builtin' &&
        skill.sourceScope !== 'personal' &&
        !isDepartmentShared(skill) &&
        !isPersonShared(skill)
    )
  },
  {
    key: 'personal',
    title: userStore.isSuperAdmin
      ? '全员个人 Skill'
      : userStore.isAdmin
        ? '本部门个人 Skill'
        : '我的个人 Skill',
    alwaysShow: userStore.isAdmin && !isBatchDeleteMode.value,
    skills: isBatchDeleteMode.value
      ? []
      : userStore.isAdmin
        ? catalogPersonalSkills.value
        : filteredInstalledSkills.value.filter((skill) => skill.sourceScope === 'personal')
  }
])
const visibleSkillGroups = computed(() =>
  skillGroups.value.filter((group) => group.skills.length || group.alwaysShow)
)
const filteredDeletableSkills = computed(() =>
  filteredInstalledSkills.value.filter(
    (skill) =>
      canManageSkill(skill) && skill.sourceType !== 'builtin' && skill.sourceScope !== 'personal'
  )
)
const canDeletePreviewSkill = computed(
  () =>
    !!previewSkill.value &&
    canManageSkill(previewSkill.value) &&
    !isOtherPersonalSkill(previewSkill.value) &&
    previewSkill.value.sourceType !== 'builtin'
)
const pendingShareRequestCount = computed(
  () => shareRequests.value.filter((request) => request.status === 'pending').length
)
const previewShareRequest = computed(() => {
  if (previewSkill.value?.sourceScope !== 'personal') return null
  const ownerUid = previewSkill.value.owner_uid || userStore.uid
  return shareRequests.value.find(
    (request) => request.personal_slug === previewSkill.value.slug && request.owner_uid === ownerUid
  )
})
const flatShareSnapshotTree = computed(() => {
  /** 按原有目录层级展示快照文件。 */
  const flatten = (entries, depth = 0) =>
    entries.flatMap((entry) => [
      { ...entry, depth },
      ...(entry.children ? flatten(entry.children, depth + 1) : [])
    ])
  return flatten(shareSnapshotTree.value)
})

/** 返回共享申请的中文状态。 */
const shareRequestStatusLabel = (status) =>
  ({ pending: '待审核', approved: '已发布', rejected: '已驳回' })[status] || status

/** 读取当前用户可见的共享申请。 */
const fetchShareRequests = async () => {
  const result = await skillApi.listSkillShareRequests()
  shareRequests.value = result?.data || []
}

/** 加载管理员有权指定的共享人员。 */
const loadShareUsers = async () => {
  if (shareUsers.value.length || shareUsersLoading.value) return
  shareUsersLoading.value = true
  try {
    shareUsers.value = await authApi.getUserAccessOptions()
  } catch (error) {
    message.error(error?.response?.data?.detail || '加载可共享人员失败')
  } finally {
    shareUsersLoading.value = false
  }
}

/** 从个人 Skill 预览提交独立的审核快照。 */
const submitPreviewShareRequest = async () => {
  if (userStore.isAdmin || !previewSkill.value?.slug || submittingShareRequest.value) return
  submittingShareRequest.value = true
  try {
    await skillApi.submitSkillShareRequest(previewSkill.value.slug)
    await fetchShareRequests()
    message.success('共享申请已提交，等待管理员审核')
  } catch (error) {
    message.error(error?.response?.data?.detail || '提交共享申请失败')
  } finally {
    submittingShareRequest.value = false
  }
}

/** 将当前预览的个人 Skill 交给管理员直接发布。 */
const openDirectPublish = () => {
  if (
    !userStore.isAdmin ||
    previewSkill.value?.sourceScope !== 'personal' ||
    previewShareRequest.value?.status === 'pending'
  ) return
  directPublishSkill.value = previewSkill.value
  directPublishShareConfig.value = {
    version: 2,
    read_scope: {
      access_level: 'department',
      department_ids: userStore.departmentId ? [Number(userStore.departmentId)] : [],
      user_uids: []
    },
    manage_scope: null
  }
  directPublishNote.value = ''
  skillPreviewVisible.value = false
  directPublishVisible.value = true
  loadShareUsers()
}

/** 发布当前个人原件的独立快照并刷新目录。 */
const confirmDirectPublish = async () => {
  const skill = directPublishSkill.value
  if (!skill || publishingPersonalSkill.value) return
  const readScope = directPublishShareConfig.value?.read_scope
  if (!readScope) return
  if (!(readScope.access_level === 'user' ? readScope.user_uids : readScope.department_ids).length) {
    message.warning(readScope.access_level === 'user' ? '请选择可使用的人员' : '请选择发布部门')
    return
  }
  publishingPersonalSkill.value = true
  try {
    await skillApi.publishPersonalSkillDirectly(
      skill.owner_uid || userStore.uid,
      skill.slug,
      readScope,
      directPublishNote.value
    )
    directPublishVisible.value = false
    message.success('共享 Skill 已发布，个人原件仍保留')
  } catch (error) {
    message.error(error?.response?.data?.detail || '发布失败')
    return
  } finally {
    publishingPersonalSkill.value = false
  }
  try {
    await Promise.all([fetchSkills({ refreshPersonal: true }), fetchShareRequests()])
  } catch (error) {
    message.warning(error?.response?.data?.detail || '已发布，但刷新共享记录失败')
  }
}

/** 打开管理员审核列表并加载可发布部门。 */
const openShareRequests = async () => {
  shareRequestsVisible.value = true
  shareRequestsLoading.value = true
  try {
    await fetchShareRequests()
    await loadShareUsers()
    if (userStore.isSuperAdmin) {
      const departments = await departmentApi.getDepartments()
      departmentOptions.value = (departments?.departments || departments || []).map((dept) => ({
        value: dept.id,
        label: dept.name
      }))
    } else {
      departmentOptions.value = [{ value: userStore.departmentId, label: userStore.departmentName }]
    }
    for (const request of shareRequests.value) {
      if (request.status === 'pending' && !reviewShareConfigs[request.id]) {
        reviewShareConfigs[request.id] = {
          version: 2,
          read_scope: {
            access_level: 'department',
            department_ids: userStore.departmentId ? [Number(userStore.departmentId)] : [],
            user_uids: []
          },
          manage_scope: null
        }
      }
    }
  } catch (error) {
    message.error(error?.response?.data?.detail || '加载共享申请失败')
  } finally {
    shareRequestsLoading.value = false
  }
}

/** 关闭审核列表。 */
const closeShareRequests = () => {
  shareRequestsVisible.value = false
}

/** 显示提交时保留的内容，避免审核个人目录的后续改动。 */
const openShareSnapshot = async (request) => {
  const requestSeq = ++shareSnapshotRequestSeq
  shareRequestsVisible.value = false
  shareSnapshotVisible.value = true
  shareSnapshotLoading.value = true
  shareSnapshotRequest.value = request
  shareSnapshotMarkdown.value = ''
  shareSnapshotError.value = ''
  shareSnapshotPath.value = 'SKILL.md'
  shareSnapshotTree.value = []
  try {
    const [treeResult, contentResult] = await Promise.all([
      skillApi.getSkillShareSnapshotTree(request.id),
      skillApi.getSkillShareSnapshot(request.id)
    ])
    if (requestSeq !== shareSnapshotRequestSeq || !shareSnapshotVisible.value) return
    shareSnapshotTree.value = treeResult?.data || []
    shareSnapshotMarkdown.value = contentResult?.data?.content || ''
  } catch (error) {
    if (requestSeq !== shareSnapshotRequestSeq || !shareSnapshotVisible.value) return
    shareSnapshotError.value = error?.response?.data?.detail || '读取审核快照失败'
  } finally {
    if (requestSeq === shareSnapshotRequestSeq) shareSnapshotLoading.value = false
  }
}

/** 返回审核列表。 */
const closeShareSnapshot = () => {
  shareSnapshotRequestSeq++
  shareSnapshotVisible.value = false
  shareRequestsVisible.value = true
}

/** 逐项检查提交时的脚本、提示词或配置文件。 */
const openShareSnapshotFile = async (path) => {
  if (!shareSnapshotRequest.value) return
  const requestId = shareSnapshotRequest.value.id
  const requestSeq = ++shareSnapshotRequestSeq
  shareSnapshotPath.value = path
  shareSnapshotMarkdown.value = ''
  shareSnapshotError.value = ''
  shareSnapshotLoading.value = true
  try {
    const result = await skillApi.getSkillShareSnapshot(requestId, path)
    if (requestSeq !== shareSnapshotRequestSeq || !shareSnapshotVisible.value) return
    shareSnapshotMarkdown.value = result?.data?.content || ''
  } catch (error) {
    if (requestSeq !== shareSnapshotRequestSeq || !shareSnapshotVisible.value) return
    shareSnapshotError.value = error?.response?.data?.detail || '无法预览此文件，可下载完整快照检查'
  } finally {
    if (requestSeq === shareSnapshotRequestSeq) shareSnapshotLoading.value = false
  }
}

/** 下载包含非文本附件的原始审核快照。 */
const downloadShareSnapshot = async () => {
  if (!shareSnapshotRequest.value) return
  try {
    const response = await skillApi.exportSkillShareSnapshot(shareSnapshotRequest.value.id)
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = `skill-share-${shareSnapshotRequest.value.id}.zip`
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    message.error(error?.response?.data?.detail || '下载审核快照失败')
  }
}

/** 审核并刷新共享 Skill 与申请状态。 */
const reviewShareRequest = async (request, action) => {
  if (reviewingRequestId.value) return
  const readScope = reviewShareConfigs[request.id]?.read_scope
  if (action === 'approve' && !readScope) return
  if (action === 'approve' && !(readScope.access_level === 'user' ? readScope.user_uids : readScope.department_ids).length) {
    message.warning(readScope.access_level === 'user' ? '请选择可使用的人员' : '请选择发布部门')
    return
  }
  reviewingRequestId.value = request.id
  try {
    if (action === 'approve') {
      await skillApi.approveSkillShareRequest(request.id, readScope, reviewNotes[request.id] || '')
      message.success('共享 Skill 已发布，个人原件仍保留')
    } else {
      await skillApi.rejectSkillShareRequest(request.id, reviewNotes[request.id] || '')
      message.success('申请已驳回')
    }
  } catch (error) {
    message.error(error?.response?.data?.detail || '审核失败')
    reviewingRequestId.value = ''
    return
  }
  try {
    await Promise.all([fetchShareRequests(), fetchSkills({ refreshPersonal: true })])
  } catch (error) {
    message.error(error?.response?.data?.detail || '审核已完成，但刷新申请状态失败')
  } finally {
    reviewingRequestId.value = ''
  }
}

// 仓库拉取的技能列表过滤
const filteredRepoSkills = computed(() => {
  if (!repoFilterKeyword.value.trim()) return remoteSkillOptions.value
  const kw = repoFilterKeyword.value.trim().toLowerCase()
  return remoteSkillOptions.value.filter(
    (item) =>
      item.name.toLowerCase().includes(kw) ||
      (item.description && item.description.toLowerCase().includes(kw))
  )
})

// 批量选择/反选/清空管理
const handleRepoSelectAll = () => {
  selectedRepoSkills.value = filteredRepoSkills.value.map((item) => item.name)
}
const handleRepoSelectNone = () => {
  selectedRepoSkills.value = []
}
const handleRepoSelectInvert = () => {
  const currentSelected = new Set(selectedRepoSkills.value)
  const newSelected = []
  filteredRepoSkills.value.forEach((item) => {
    if (!currentSelected.has(item.name)) {
      newSelected.push(item.name)
    }
  })
  selectedRepoSkills.value = newSelected
}

const handleSearchSelectAll = () => {
  selectedSearchSkills.value = [...searchedSkills.value]
}
const handleSearchSelectNone = () => {
  selectedSearchSkills.value = []
}
const handleSearchSelectInvert = () => {
  const newSelected = []
  searchedSkills.value.forEach((item) => {
    const isSelected = selectedSearchSkills.value.some(
      (s) => s.name === item.name && s.source === item.source
    )
    if (!isSelected) {
      newSelected.push(item)
    }
  })
  selectedSearchSkills.value = newSelected
}

const handleToggleRepoSkill = (name, checked) => {
  if (checked) {
    if (!selectedRepoSkills.value.includes(name)) {
      selectedRepoSkills.value.push(name)
    }
  } else {
    selectedRepoSkills.value = selectedRepoSkills.value.filter((n) => n !== name)
  }
}

const toggleRepoSkillFromRow = (name) => {
  handleToggleRepoSkill(name, !selectedRepoSkills.value.includes(name))
}

const isSearchSkillSelected = (item) =>
  selectedSearchSkills.value.some(
    (skill) => skill.name === item.name && skill.source === item.source
  )

const handleToggleSearchSkill = (item, checked) => {
  if (checked) {
    const isExist = selectedSearchSkills.value.some(
      (s) => s.name === item.name && s.source === item.source
    )
    if (!isExist) {
      selectedSearchSkills.value.push(item)
    }
  } else {
    selectedSearchSkills.value = selectedSearchSkills.value.filter(
      (s) => !(s.name === item.name && s.source === item.source)
    )
  }
}

const toggleSearchSkillFromRow = (item) => {
  handleToggleSearchSkill(item, !isSearchSkillSelected(item))
}

const sourceTypeLabel = (sourceType) => {
  if (sourceType === 'personal') return '个人技能'
  if (sourceType === 'personal_share') return '个人共享'
  if (sourceType === 'builtin') return '内置'
  if (sourceType === 'remote') return '远程'
  return '上传'
}

/** 返回真实的共享阅读范围。 */
const getSkillShareLabel = (skill) => {
  if (skill.sourceType === 'builtin') return '全局内置'
  const scope = getReadScope(skill)
  if (scope?.access_level === 'department') {
    if (skill.read_department_names?.length) return skill.read_department_names.join('、')
    const names = (scope.department_ids || []).map((id) =>
      departmentOptions.value.find((option) => Number(option.value) === Number(id))?.label || `部门 #${id}`
    )
    return names.length ? names.join('、') : '部门共享'
  }
  if (scope?.access_level === 'user' && skill.read_user_names?.length) {
    return `只读：${skill.read_user_names.join('、')}`
  }
  return getShareConfigLabel(skill?.share_config)
}

const skillCardTags = (skill) => {
  if (skill.sourceScope === 'personal') {
    const request = isOtherPersonalSkill(skill)
      ? null
      : shareRequests.value.find(
          (item) => item.personal_slug === skill.slug && item.owner_uid === userStore.uid
        )
    return [
      { name: '个人技能', color: 'gray' },
      ...(skill.owner_uid
        ? [
            { name: skill.owner_name || skill.owner_uid, color: 'blue' },
            { name: skill.owner_department_name || '未分配部门', color: 'gray' }
          ]
        : []),
      ...(request ? [{ name: shareRequestStatusLabel(request.status), color: 'blue' }] : []),
      ...(skill.overrides_shared ? [{ name: '覆盖共享版本', color: 'orange' }] : [])
    ]
  }
  return [
    { name: getSkillShareLabel(skill), color: 'gray' },
    ...(skill.sourceType === 'personal_share' && skill.version
      ? [{ name: `v${skill.version}`, color: 'blue' }]
      : []),
    ...(skill.shadowed_by_personal ? [{ name: '已被个人版本覆盖', color: 'orange' }] : [])
  ]
}

const isOtherPersonalSkill = (skill) =>
  skill?.sourceScope === 'personal' &&
  !!skill?.owner_uid &&
  skill.owner_uid !== userStore.uid
const canManageSkill = (skill) => !!skill && !isOtherPersonalSkill(skill) && skill.can_manage !== false
const isSkillToggling = (slug) => togglingSkillSlugs.value.includes(slug)
const navigateToDetail = (skill) => {
  router.push({
    path: `/extensions/skill/${encodeURIComponent(skill.slug)}`,
    ...(skill?.sourceScope === 'personal' ? { query: { scope: 'personal' } } : {})
  })
}

const closeSkillPreview = () => {
  skillPreviewVisible.value = false
}

const openSkillPreview = async (skill) => {
  if (!skill?.slug) return
  const requestSeq = ++previewRequestSeq
  previewSkill.value = skill
  skillPreviewMarkdown.value = ''
  skillPreviewError.value = ''
  skillPreviewLoading.value = true
  skillPreviewVisible.value = true
  try {
    const result = isOtherPersonalSkill(skill)
      ? await skillApi.getPersonalSkillCatalogFile(skill.owner_uid, skill.slug, 'SKILL.md')
      : skill.sourceScope === 'personal'
        ? await skillApi.getPersonalSkillFile(skill.slug, 'SKILL.md')
        : await skillApi.getSkillFile(skill.slug, 'SKILL.md')
    if (requestSeq !== previewRequestSeq) return
    skillPreviewMarkdown.value = result?.data?.content || ''
  } catch (error) {
    if (requestSeq !== previewRequestSeq) return
    skillPreviewError.value = error?.response?.data?.detail || error.message || '读取 SKILL.md 失败'
  } finally {
    if (requestSeq === previewRequestSeq) skillPreviewLoading.value = false
  }
}

const goToPreviewSkillManagement = () => {
  if (!previewSkill.value) return
  navigateToDetail(previewSkill.value)
  closeSkillPreview()
}

const handleCardClick = (skill) => {
  if (isBatchDeleteMode.value) {
    handleToggleCardSelect(skill.slug)
  } else {
    openSkillPreview(skill)
  }
}

const handleToggleCardSelect = (slug) => {
  const target = installedSkillCards.value.find(
    (skill) => skill.slug === slug && skill.sourceScope !== 'personal'
  )
  if (!canManageSkill(target) || target?.sourceType === 'builtin') return
  const idx = selectedCardSlugs.value.indexOf(slug)
  if (idx > -1) {
    selectedCardSlugs.value.splice(idx, 1)
  } else {
    selectedCardSlugs.value.push(slug)
  }
}

const handleToggleSkillEnabled = async (skill) => {
  if (!skill || !canManageSkill(skill) || isSkillToggling(skill.slug)) return
  const enabled = skill.enabled === false
  togglingSkillSlugs.value.push(skill.slug)
  try {
    const result = await skillApi.updateSkillEnabled(skill.slug, enabled)
    const updatedSkill = result?.data
    const index = skills.value.findIndex(
      (item) => item.slug === skill.slug && item.source_scope !== 'personal'
    )
    if (updatedSkill && index > -1) {
      skills.value[index] = updatedSkill
    } else {
      await fetchSkills()
    }
    if (previewSkill.value?.slug === skill.slug) {
      previewSkill.value = updatedSkill
        ? { ...updatedSkill, sourceType: updatedSkill.source_type || 'upload' }
        : { ...previewSkill.value, enabled }
    }
    message.success(`Skill 已${enabled ? '启用' : '禁用'}`)
  } catch (error) {
    message.error(error?.response?.data?.detail || error.message || '更新 Skill 启用状态失败')
  } finally {
    togglingSkillSlugs.value = togglingSkillSlugs.value.filter((slug) => slug !== skill.slug)
  }
}

const handlePreviewToggle = () => {
  if (!previewSkill.value) return
  handleToggleSkillEnabled(previewSkill.value)
}

const confirmDeletePreviewSkill = () => {
  const target = previewSkill.value
  if (!target || !canDeletePreviewSkill.value || deletingPreviewSkill.value) return

  Modal.confirm({
    title: `卸载 ${target.name || target.slug}`,
    content:
      target.sourceScope === 'personal'
        ? '卸载后会删除个人 Skill；如有同名共享版本，Agent 将恢复使用共享版本。'
        : '卸载后会删除该 Skill 的数据库记录和本地文件，操作不可恢复。',
    okText: '卸载',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      deletingPreviewSkill.value = true
      try {
        if (target.sourceScope === 'personal') {
          await skillApi.deletePersonalSkill(target.slug)
        } else {
          await skillApi.deleteSkill(target.slug)
        }
        message.success('Skill 已卸载')
        closeSkillPreview()
        previewSkill.value = null
        await fetchSkills()
      } catch (error) {
        message.error(error?.response?.data?.detail || error.message || '卸载 Skill 失败')
      } finally {
        deletingPreviewSkill.value = false
      }
    }
  })
}

const handleBatchSelectAll = () => {
  selectedCardSlugs.value = filteredDeletableSkills.value.map((skill) => skill.slug)
}

const handleBatchSelectNone = () => {
  selectedCardSlugs.value = []
}

const handleBatchSelectInvert = () => {
  const currentSet = new Set(selectedCardSlugs.value)
  selectedCardSlugs.value = filteredDeletableSkills.value
    .filter((skill) => !currentSet.has(skill.slug))
    .map((skill) => skill.slug)
}

const exitBatchDeleteMode = () => {
  isBatchDeleteMode.value = false
  selectedCardSlugs.value = []
}

const handleBatchDelete = () => {
  const deletableSlugs = selectedCardSlugs.value.filter((slug) => {
    const target = installedSkillCards.value.find(
      (skill) => skill.slug === slug && skill.sourceScope !== 'personal'
    )
    return (
      canManageSkill(target) &&
      target?.sourceType !== 'builtin' &&
      target?.sourceScope !== 'personal'
    )
  })
  if (deletableSlugs.length === 0) return

  Modal.confirm({
    title: '确定要批量删除选中的技能吗？',
    content: `您已选中了 ${deletableSlugs.length} 个技能。该操作将从数据库和物理磁盘中彻底删除这些技能包，且不可恢复！`,
    okText: '确定删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      loading.value = true
      try {
        const res = await skillApi.deleteSkillsBatch(deletableSlugs)
        const results = res?.data || []
        const successList = results.filter((r) => r.success)
        const failList = results.filter((r) => !r.success)

        if (failList.length === 0) {
          message.success(`批量删除成功，已删除 ${successList.length} 个技能`)
        } else {
          message.warning(`批量删除完成：成功 ${successList.length} 个，失败 ${failList.length} 个`)
        }

        exitBatchDeleteMode()
        await fetchSkills()
      } catch (error) {
        message.error(error?.response?.data?.detail || error.message || '批量删除失败')
      } finally {
        loading.value = false
      }
    }
  })
}

/** 加载按人员分页的个人 Skill 目录。 */
const fetchCatalog = async ({ reset = false } = {}) => {
  if (!userStore.isAdmin || (catalogLoading.value && !reset)) return
  const requestSeq = ++catalogRequestSeq
  const offset = reset ? 0 : catalogNextOffset.value
  if (offset === null) return
  catalogLoading.value = true
  try {
    const result = await skillApi.listPersonalSkillCatalog({
      departmentId: appliedDepartmentId.value,
      ownerSearch: appliedOwnerSearch.value,
      offset
    })
    if (requestSeq !== catalogRequestSeq) return
    catalogCards.value = reset ? result.data.items : [...catalogCards.value, ...result.data.items]
    catalogNextOffset.value = result.data.next_offset
  } catch (error) {
    message.error(error?.response?.data?.detail || '加载个人 Skill 目录失败')
  } finally {
    if (requestSeq === catalogRequestSeq) catalogLoading.value = false
  }
}

/** 按当前部门和人员条件重新查询。 */
const applyCatalogFilters = () => {
  appliedDepartmentId.value = catalogDepartmentId.value
  appliedOwnerSearch.value = ownerSearch.value.trim()
  catalogCards.value = []
  catalogNextOffset.value = 0
  fetchSkills()
}

/** 获取部门名称，供共享范围和管理员筛选使用。 */
const loadDepartments = async () => {
  if (!userStore.isAdmin) return
  try {
    const result = await departmentApi.getDepartments()
    departmentOptions.value = (result?.departments || result || []).map((dept) => ({
      value: dept.id,
      label: dept.name
    }))
  } catch {
    message.error('加载部门列表失败')
  }
}

const fetchSkills = async ({ refreshPersonal = false } = {}) => {
  loading.value = true
  try {
    const skillResult = await skillApi.listSkillCards({
      refreshPersonal,
      audienceSearch: userStore.isAdmin ? appliedOwnerSearch.value : undefined,
      audienceDepartmentId: userStore.isAdmin ? appliedDepartmentId.value : undefined
    })
    skills.value = skillResult?.data || []
    if (userStore.isAdmin) await fetchCatalog({ reset: true })
  } catch {
    message.error('加载失败')
  } finally {
    loading.value = false
  }
}

const beforeSkillUpload = (file) => {
  const lower = file.name.toLowerCase()
  if (!lower.endsWith('.zip') && lower !== 'skill.md') {
    message.error('仅支持上传 .zip 文件或 SKILL.md 文件')
    return false
  }
  return true
}

const openInstallFlow = (flow) => {
  installFlow.value = flow
  installFlowOpen.value = true
}

const resetRemoteSelection = () => {
  selectedRepoSkills.value = []
  selectedSearchSkills.value = []
  remoteSkillOptions.value = []
  searchedSkills.value = []
  repoFilterKeyword.value = ''
  searchKeyword.value = ''
}

const closeInstallFlow = () => {
  const wasRemoteFlow = installFlow.value?.kind === 'remote'
  installFlowOpen.value = false
  installFlow.value = null
  if (wasRemoteFlow) resetRemoteSelection()
}

const handleInstallFlowCompleted = async ({ success, failed }) => {
  if (failed === 0) message.success(`已添加 ${success} 个 Skill`)
  else message.warning(`安装完成：成功 ${success} 个，失败 ${failed} 个`)
  await fetchSkills()
}

const handleImportUpload = async ({ file, onSuccess, onError }) => {
  importing.value = true
  try {
    const result = await skillApi.prepareSkillUpload(file)
    openInstallFlow({
      kind: 'draft',
      title: `安装 ${file.name}`,
      description: '检查 Skill 依赖与生效范围，然后完成安装。',
      drafts: [result?.data]
    })
    onSuccess?.(result)
  } catch (e) {
    message.error(e?.response?.data?.detail || e.message || '解析 Skill 失败')
    onError?.(e)
  } finally {
    importing.value = false
  }
}

const handleOpenRemoteInstall = () => {
  resetRemoteSelection()
  openInstallFlow({
    kind: 'remote',
    title: '远程安装 Skill',
    description: '按仓库拉取或全局搜索，选择需要安装的 Skill。'
  })
}

const rememberRemoteSource = (source) => {
  let history = [...repoHistory.value]
  history = history.filter((item) => item !== source)
  history.unshift(source)
  if (history.length > 10) {
    history = history.slice(0, 10)
  }
  repoHistory.value = history
  localStorage.setItem('yuxi_remote_repo_history', JSON.stringify(history))
}

const handleListRemoteSkills = async () => {
  const source = remoteInstallForm.source.trim()
  if (!source) {
    message.warning('请输入来源仓库')
    return
  }
  listingRemoteSkills.value = true
  try {
    const result = await skillApi.listRemoteSkills(source)
    remoteSkillOptions.value = result?.data || []
    selectedRepoSkills.value =
      remoteSkillOptions.value.length === 1 ? [remoteSkillOptions.value[0].name] : []
    if (!remoteSkillOptions.value.length) {
      message.warning('未发现可安装的 Skills')
      return
    }
    if (remoteSkillOptions.value.length === 1) {
      message.success('已发现 1 个 Skill，已自动选中')
    } else {
      message.success(`已发现 ${remoteSkillOptions.value.length} 个 Skills`)
    }

    rememberRemoteSource(source)
  } catch (error) {
    message.error(error?.response?.data?.detail || error.message || '获取远程 Skills 失败')
  } finally {
    listingRemoteSkills.value = false
  }
}

const loadHistory = () => {
  try {
    const raw = localStorage.getItem('yuxi_remote_repo_history')
    if (raw) {
      repoHistory.value = JSON.parse(raw)
    }
  } catch (e) {
    console.error('Failed to load repo history', e)
  }
}

const deleteHistoryItem = (item) => {
  repoHistory.value = repoHistory.value.filter((h) => h !== item)
  localStorage.setItem('yuxi_remote_repo_history', JSON.stringify(repoHistory.value))
}

const clearAllHistory = () => {
  repoHistory.value = []
  localStorage.removeItem('yuxi_remote_repo_history')
  message.success('历史记录已清空')
}

const handleSelectHistory = ({ key }) => {
  if (key === 'clear-all-history') {
    clearAllHistory()
    return
  }
  remoteInstallForm.source = key
}

const handleSearchRemoteSkills = async () => {
  const query = searchKeyword.value.trim()
  if (!query) {
    message.warning('请输入搜索关键字')
    return
  }
  searchingRemoteSkills.value = true
  try {
    const result = await skillApi.searchRemoteSkills(query)
    searchedSkills.value = result?.data || []
    selectedSearchSkills.value = searchedSkills.value.length === 1 ? [...searchedSkills.value] : []
    if (!searchedSkills.value.length) {
      message.warning('未搜索到相关的 Skills')
    } else if (searchedSkills.value.length === 1) {
      message.success('搜索到 1 个 Skill，已自动选中')
    } else {
      message.success(`搜索到 ${searchedSkills.value.length} 个 Skills`)
    }
  } catch (error) {
    message.error(error?.response?.data?.detail || error.message || '搜索远程 Skills 失败')
  } finally {
    searchingRemoteSkills.value = false
  }
}

const startInstallRemoteSkills = () => {
  const requests = []
  if (activeTab.value === 'repo') {
    requests.push({
      source: remoteInstallForm.source.trim(),
      skills: [...selectedRepoSkills.value],
      skillDetails: remoteSkillOptions.value
        .filter((item) => selectedRepoSkills.value.includes(item.name))
        .map((item) => ({ ...item, slug: item.name }))
    })
  } else {
    const groups = new Map()
    selectedSearchSkills.value.forEach((item) => {
      if (!groups.has(item.source)) groups.set(item.source, [])
      groups.get(item.source).push(item)
    })
    groups.forEach((items, source) => {
      requests.push({
        source,
        skills: items.map((item) => item.name),
        skillDetails: items.map((item) => ({ ...item, slug: item.name }))
      })
    })
  }

  openInstallFlow({
    kind: 'remote',
    title: '远程安装 Skill',
    description: `${requests.length} 个来源 · ${requests.reduce((total, item) => total + item.skills.length, 0)} 个 Skill`,
    requests
  })
}

watch(activeTab, () => {
  selectedRepoSkills.value = []
  selectedSearchSkills.value = []
})

onMounted(() => {
  fetchSkills()
  loadDepartments()
  fetchShareRequests().catch(() => message.error('加载共享申请状态失败'))
  loadHistory()
})

defineExpose({
  fetchSkills,
  handleImportUpload,
  openRemoteInstallModal: handleOpenRemoteInstall,
  loading
})
</script>

<style lang="less" scoped>
@import '@/assets/css/extensions.less';
</style>

<style lang="less" scoped>
.skill-catalog-filters {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  padding: 12px var(--page-padding);
  border-bottom: 1px solid var(--gray-100);
}

.skill-catalog-department {
  width: 180px;
}

.skill-catalog-scope {
  color: var(--color-text-secondary);
}

.skill-catalog-owner {
  width: 220px;
}

.skill-catalog-empty,
.skill-catalog-more {
  padding: 12px var(--page-padding);
  color: var(--color-text-secondary);
}

@media (max-width: 600px) {
  .skill-cards-page :deep(.page-shoulder) {
    flex-wrap: wrap;
  }

  .skill-cards-page :deep(.page-shoulder-left),
  .skill-cards-page :deep(.page-shoulder-right),
  .skill-cards-page :deep(.search-input) {
    width: 100%;
  }

  .skill-cards-page :deep(.page-shoulder-right) {
    justify-content: flex-start;
    flex-wrap: wrap;
  }

  .skill-catalog-department,
  .skill-catalog-owner {
    flex: 1 1 150px;
    width: auto;
  }
}

.skill-share-warning {
  margin-bottom: 12px;
}

.skill-share-request {
  padding: 16px 0;
  border-bottom: 1px solid var(--gray-100);
}

.skill-share-request-heading,
.skill-share-review-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.skill-share-request-meta {
  margin: 6px 0;
  color: var(--color-text-secondary);
  overflow-wrap: anywhere;
}

.skill-share-review-actions {
  margin-top: 12px;
}

.skill-share-note {
  margin-top: 12px;
}

.skill-share-review-actions .ant-input {
  flex: 1 1 200px;
}

.skill-share-snapshot-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
  color: var(--color-text-secondary);
}

.skill-share-snapshot-layout {
  display: grid;
  grid-template-columns: minmax(150px, 220px) minmax(0, 1fr);
  gap: 12px;
  min-height: 300px;
}

.skill-share-snapshot-tree {
  border: 1px solid var(--gray-100);
  border-radius: 8px;
  overflow: auto;
}

.skill-share-snapshot-tree button {
  display: block;
  width: 100%;
  padding: 7px 10px;
  border: 0;
  background: transparent;
  color: var(--color-text);
  text-align: left;
  overflow-wrap: anywhere;
  cursor: pointer;
}

.skill-share-snapshot-tree button.active {
  background: var(--main-50);
}

.skill-share-snapshot-tree button.directory {
  font-weight: 600;
  cursor: default;
}

.skill-share-snapshot-content {
  min-width: 0;
  max-height: 55vh;
  overflow: auto;
}

.skill-share-snapshot-code {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

@media (max-width: 600px) {
  .skill-share-snapshot-layout {
    grid-template-columns: 1fr;
  }

  .skill-share-snapshot-tree {
    max-height: 160px;
  }
}

.skill-empty-state {
  width: 100%;
  min-height: 280px;
  padding: 40px var(--page-padding);
}

.skill-empty-card {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  min-height: 220px;
  flex-direction: column;
  border: 1px dashed var(--gray-150);
  border-radius: 16px;
  background: linear-gradient(180deg, var(--gray-0) 0%, var(--gray-25) 100%);
  color: var(--gray-500);
  text-align: center;
}

.skill-empty-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  margin-bottom: 12px;
  border-radius: 14px;
  background: var(--gray-50);
  color: var(--gray-600);
}

.skill-empty-title {
  color: var(--gray-800);
  font-size: 15px;
  font-weight: 700;
  line-height: 22px;
}

.skill-empty-desc {
  margin-top: 4px;
  color: var(--gray-500);
  font-size: 13px;
  line-height: 20px;
}

.card-wrapper {
  position: relative;

  :deep(.info-card-mini-desc) {
    display: -webkit-box;
    min-height: 36px;
    color: var(--gray-700);
    white-space: normal;
    line-clamp: 2;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
  }

  :deep(.info-card-mini .info-card-icon) {
    align-self: flex-start;
  }

  &.batch-mode {
    :deep(.info-card) {
      cursor: pointer;
      border-color: var(--gray-200);

      &:hover {
        border-color: var(--gray-300);
      }
    }

    :deep(.info-card-status),
    :deep(.info-card-mini-action) {
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.2s ease;
    }

    :deep(.info-card-mini .info-card-info) {
      padding-right: 28px;
    }
  }

  &.selected {
    :deep(.info-card) {
      border-color: var(--gray-500) !important;
      background: var(--gray-25) !important;
    }
  }

  .card-select-checkbox {
    position: absolute;
    top: 16px;
    right: 16px;
    z-index: 10;
  }
}

.skill-enabled-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
  color: var(--gray-600);
  font-size: 18px;
  font-weight: 600;
  line-height: 1;
  cursor: pointer;
  transition:
    border-color 0.18s ease,
    background-color 0.18s ease,
    color 0.18s ease;

  &:hover,
  &:focus {
    outline: none;
    border-color: var(--gray-300);
    background: var(--gray-50);
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.45;
  }

  &.loading:disabled {
    cursor: wait;
    opacity: 1;
  }

  &.enabled {
    color: var(--color-success-700);

    .action-icon-minus {
      display: none;
    }

    &:hover,
    &:focus {
      border-color: var(--color-error-200, #ffccc7);
      background: var(--color-error-50, #fff2f0);
      color: var(--color-error-700, #cf1322);

      .action-icon-check {
        display: none;
      }

      .action-icon-minus {
        display: block;
      }
    }
  }
}

.action-icon {
  flex-shrink: 0;
}

.skill-preview-panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.skill-preview-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.skill-preview-title-area {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 10px;
}

.skill-preview-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border-radius: 9px;
  background: var(--gray-50);
  color: var(--gray-600);
}

.skill-preview-title-text {
  min-width: 0;
}

.skill-preview-title {
  overflow: hidden;
  color: var(--gray-900);
  font-size: 16px;
  font-weight: 700;
  line-height: 22px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skill-preview-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 2px;
  color: var(--gray-500);
  font-size: 12px;
  line-height: 18px;
}

.skill-preview-disabled-tag {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--gray-100);
  color: var(--gray-600);
  font-size: 11px;
  font-weight: 600;
}

.skill-preview-actions {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
  gap: 8px;
  padding-top: 2px;
}

.skill-preview-body {
  min-height: 260px;
  max-height: min(56vh, 520px);
  padding: 14px 16px;
  overflow-y: auto;
  border: 1px solid var(--gray-150);
  border-radius: 12px;
  background: var(--gray-25);

  :deep(.yk-markdown-preview) {
    background: transparent;
  }
}

.skill-preview-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 220px;
}

.skill-preview-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
}

.skill-preview-footer-left,
.skill-preview-footer-right {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.remote-install-panel {
  :deep(.install-tabs > .ant-tabs-nav .ant-tabs-nav-wrap) {
    justify-content: center;
  }

  .repo-input-row {
    display: flex;
    gap: 8px;
    align-items: center;
    margin-bottom: 4px;

    .repo-input-field {
      flex: 1;
      min-width: 0;
    }
  }

  .history-trigger-wrapper {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 24px;
    height: 24px;
    cursor: pointer;
    outline: none;
    margin-right: -4px;
    border-radius: 4px;
    transition: background-color 0.2s ease;

    &:hover {
      background-color: var(--gray-100);
    }

    &:focus,
    &:focus-visible {
      outline: none;
    }
  }

  .history-icon-trigger {
    color: var(--gray-400);
    transition: color 0.2s ease;
    outline: none;

    &:hover {
      color: var(--gray-700);
    }

    &.has-history {
      color: var(--gray-500);

      &:hover {
        color: var(--gray-700);
      }
    }
  }

  .repo-hint-text {
    font-size: 12px;
    color: var(--gray-400);
    margin-bottom: 12px;
    line-height: 1.4;

    a {
      color: var(--gray-700);
      text-decoration: underline;
    }
  }

  .tab-content-wrapper {
    padding: 4px 0 8px 0;
  }

  .skills-list-section {
    margin-top: 12px;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    overflow: hidden;
    background: var(--gray-0);
  }

  .list-operations-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid var(--gray-150);
    padding: 8px 10px;

    .op-buttons {
      display: flex;
      gap: 2px;

      .ant-btn {
        padding: 0 4px;
        height: auto;
        font-size: 12px;
        color: var(--gray-600);

        &:hover {
          color: var(--gray-900);
        }
      }
    }
  }

  .skills-list-viewport {
    max-height: min(42vh, 420px);
    overflow-y: auto;
    overscroll-behavior: contain;
    background: var(--gray-0);
  }

  .remote-skills-list-container {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 1px;
    background: var(--gray-100);
  }

  .remote-skill-row {
    display: flex;
    align-items: center;
    min-height: 46px;
    padding: 6px 10px;
    gap: 8px;
    background: var(--gray-0);
    cursor: pointer;
    transition: background-color 0.18s ease;

    &:hover,
    &.selected {
      background: var(--gray-25);
    }

    &:focus-visible {
      outline: 2px solid var(--gray-400);
      outline-offset: -2px;
    }

    &[aria-disabled='true'] {
      cursor: not-allowed;
    }
  }

  .remote-row-checkbox {
    pointer-events: none;
  }

  .single-remote-skill-card {
    border: 1px solid var(--gray-150);
    border-radius: 6px;
    background: var(--gray-0);
    padding: 12px;
  }

  .single-remote-skill-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    min-width: 0;

    .ant-tag {
      flex-shrink: 0;
      margin-inline-end: 0;
    }
  }

  .single-remote-skill-name {
    line-height: 20px;
    font-weight: 600;
    color: var(--gray-900);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .single-remote-skill-meta {
    margin-top: 2px;
    font-size: 12px;
    line-height: 18px;
    color: var(--gray-500);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .remote-skill-row-content {
    display: flex;
    min-width: 0;
    flex: 1;
    flex-direction: column;

    .skill-item-name {
      font-weight: 600;
      color: var(--gray-900);
    }

    .skill-item-desc {
      display: block;
      font-size: 12px;
      color: var(--gray-500);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
  }

  .skill-install-count {
    flex-shrink: 0;
    color: var(--gray-500);
    font-size: 11px;
  }
}

@media (max-width: 600px) {
  .remote-install-panel {
    .skills-list-viewport {
      max-height: 40vh;
      overflow-y: auto;
    }

    .remote-skills-list-container {
      grid-template-columns: 1fr;
    }
  }
}

.modal-footer-summary {
  color: var(--gray-500);
  font-size: 12px;
  line-height: 18px;
}

.modal-footer-buttons {
  display: flex;
  margin-left: auto;
  gap: 8px;
}
</style>

<!-- NOTE: unscoped style block 用于 dropdown overlay 样式穿透 teleport -->
<style lang="less">
/* Ant Design Dropdown overlay 通过 teleport 挂载到 body，
   scoped CSS 无法穿透，因此必须使用 unscoped 样式。
   使用 .history-dropdown-menu 作为 overlayClassName 命名空间。 */
.history-dropdown-menu {
  min-width: 280px;

  .ant-dropdown-menu {
    padding: 4px;
  }

  .ant-dropdown-menu-item {
    padding: 8px 12px;
    border-radius: 6px;

    .ant-dropdown-menu-title-content {
      display: flex;
      align-items: center;
      width: 100%;
    }
  }
}

/* 历史记录行：仓库地址 + 删除按钮 */
.history-item-menu-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  gap: 12px;

  .history-item-text {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 13px;
    color: var(--gray-800);
    line-height: 1;
  }

  .history-item-del-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    color: var(--gray-400);
    cursor: pointer;
    border-radius: 4px;
    transition: all 0.2s ease;
    flex-shrink: 0;

    svg {
      display: block;
    }

    &:hover {
      color: var(--color-error-500, #ff4d4f);
      background: var(--color-error-10, rgba(255, 77, 79, 0.1));
    }
  }
}

.history-empty-text {
  color: var(--gray-400);
  font-size: 12px;
}

/* 清空历史记录按钮内容 — 图标在左文字在右，水平居中 */
.clear-history-btn-content {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: var(--color-error-500, #ff4d4f);
  font-weight: 500;
  font-size: 13px;
  width: 100%;

  .clear-icon {
    display: flex;
    align-items: center;
  }
}
</style>
