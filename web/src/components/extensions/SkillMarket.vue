<template>
  <section class="skill-market">
    <nav class="skill-view-tabs" role="tablist" aria-label="技能页面">
      <button type="button" role="tab" :aria-selected="activeView === 'personal'" aria-controls="skill-personal-view" @click="selectView('personal')">我的技能</button>
      <button type="button" role="tab" :aria-selected="activeView === 'market'" aria-controls="skill-market-view" @click="selectView('market')">技能市场</button>
    </nav>

    <section v-if="activeView === 'personal'" id="skill-personal-view" role="tabpanel" class="skill-view-content">
      <a-alert v-if="personalError" type="error" :message="personalError" show-icon class="market-error"><template #action><a-button size="small" @click="refresh">重试</a-button></template></a-alert>
      <a-alert v-if="skillSummaryError && !managementOpen" type="warning" message="技能数量暂不可用" show-icon class="market-error"><template #action><a-button size="small" @click="fetchSkillSummary">重试统计</a-button></template></a-alert>
      <template v-if="managementOpen">
        <div class="skill-page-heading">
          <div class="skill-page-title"><div class="skill-page-icon"><Blocks :size="25" /></div><div><h2>技能治理</h2><p>管理共享技能、人员技能与批量操作。</p></div></div>
          <a-button @click="managementOpen = false">返回我的技能</a-button>
        </div>
        <SkillCardList governance-only :market-installs="installedCards" @open-market="openManagementMarket" @uninstall-market="confirmUninstall" @updated="refreshHome" />
      </template>
      <template v-else>
        <div class="skill-page-heading">
          <div class="skill-page-title"><div class="skill-page-icon"><BookOpen :size="25" /></div><div><h2>我的技能</h2><p>创建、安装和可直接使用的技能，都在这里管理。</p></div></div>
          <div class="skill-page-actions">
            <a-button @click="selectView('market')">去市场找技能</a-button>
            <a-button v-if="userStore.isAdmin" @click="openRequests">市场审核<span v-if="pendingRequestCount"> ({{ pendingRequestCount }})</span></a-button>
            <a-button v-if="userStore.isAdmin" @click="openMarketManagement">管理市场上架</a-button>
            <a-button v-if="userStore.isAdmin || hasManageableSharedSkills || legacyTotals.shared > legacySharedCards.length" @click="managementOpen = true">技能治理</a-button>
            <SkillCardList toolbar-only :market-installs="installedCards" @updated="refreshHome" />
          </div>
        </div>
        <div v-if="!personalError && !legacyLoading && !installedLoading" class="skill-overview" aria-label="我的技能概况">
          <div><strong>{{ skillSummaryError ? '—' : skillSummary?.created_total ?? '…' }}</strong><span>我创建的</span></div>
          <div><strong>{{ installedTotal }}</strong><span>市场已安装</span></div>
          <div><strong>{{ legacyTotals.shared + legacyTotals.builtin }}</strong><span>团队与系统可用</span></div>
          <div v-if="pendingRequestCount"><strong>{{ pendingRequestCount }}</strong><span>市场待审核</span></div>
        </div>
        <div class="skill-list-heading">
          <div><h3>{{ releaseView ? '发布与审核记录' : '技能列表' }}</h3><p>{{ releaseView ? '查看已提交的共享与市场申请、审核结果和当时的文件快照。' : '直接查看、发布和管理技能。' }}</p></div>
          <a-button type="link" @click="releaseView = !releaseView">{{ releaseView ? '返回技能列表' : '查看发布记录' }}</a-button>
        </div>
        <PublicationRecords v-if="releaseView" :personal-skills="personalError || legacyLoading || legacyHasMore.personal || legacyPages.personal !== 1 || appliedPersonalQuery ? null : ownedSkills" />
        <template v-else>
          <div class="skill-list-toolbar">
            <nav class="personal-source-filters" aria-label="我的技能来源">
              <button v-for="filter in personalSourceFilters" :key="filter.key" type="button" :class="{ active: personalSource === filter.key }" :aria-current="personalSource === filter.key ? 'page' : undefined" @click="selectPersonalSource(filter.key)">
                {{ filter.label }}<span :title="['all', 'created'].includes(filter.key) ? skillSummaryError || undefined : undefined">{{ personalFilterCount(filter.key) }}</span>
              </button>
            </nav>
            <a-input v-model:value="personalQuery" allow-clear aria-label="搜索我的技能" placeholder="搜索我的技能" class="personal-search" />
          </div>
          <a-spin :spinning="personalBusy">
            <div v-if="!filteredPersonalItems.length && !personalBusy && !personalError" class="personal-empty">{{ appliedPersonalQuery ? '没有找到匹配的技能。' : '这里还没有技能。可以上传或从市场安装。' }}</div>
            <div v-else-if="filteredPersonalItems.length" class="skill-list">
              <div class="skill-list-header"><span>技能</span><span>来源</span><span>状态</span><span>操作</span></div>
              <div v-for="item in filteredPersonalItems" :key="`${item.kind}:${item.slug}`" class="skill-row">
                <div class="skill-row-name"><span class="skill-row-icon"><Sparkles v-if="item.kind === 'personal'" :size="18" /><BookOpen v-else :size="18" /></span><span><button type="button" :disabled="item.kind === 'market' && item.visible === false" @click="openPersonalItem(item)">{{ item.name }}</button><small>{{ item.description || item.slug }}</small></span></div>
                <div class="skill-row-origin">{{ personalItemOrigin(item) }}<small>{{ skillCategoryLabel(item.category) }}</small></div>
                <div class="skill-row-status" :class="{ 'is-unavailable': item.visible === false || item.market_visible === false || item.shadowed_by_personal }">{{ personalItemStatus(item) }}</div>
                <div class="skill-row-actions">
                  <template v-if="item.kind === 'personal'"><a-button size="small" @click="openPersonalItem(item)">查看</a-button><a-button size="small" @click="openPublish(item)">发布技能</a-button><a-button v-if="item.market_install?.unavailable_reason === 'needs_confirmation'" size="small" :loading="busySlug === item.market_slug" @click="confirmUpgrade(item.market_install)">确认升级</a-button><a-button v-if="item.market_installed_version" size="small" :danger="item.market_visible === false" @click="item.market_visible === false || item.market_install?.unavailable_reason ? confirmUninstall(item.market_install) : openDetail(item.market_slug)">{{ item.market_visible === false || item.market_install?.unavailable_reason ? '卸载旧安装' : '管理安装' }}</a-button></template>
                  <template v-else-if="item.kind === 'market'"><a-button v-if="item.unavailable_reason === 'needs_confirmation'" size="small" :loading="busySlug === item.slug" @click="confirmUpgrade(item)">确认升级</a-button><a-button v-else-if="item.visible !== false && !item.unavailable_reason" size="small" @click="openPersonalItem(item)">查看</a-button><a-button size="small" danger @click="confirmUninstall(item)">{{ item.visible === false || item.unavailable_reason ? '卸载旧安装' : '卸载' }}</a-button></template>
                  <a-button v-else size="small" @click="openPersonalItem(item)">{{ canManageSkillRecord(item) ? '管理技能' : '查看' }}</a-button>
                </div>
              </div>
            </div>
            <div v-if="!personalBusy && !personalError" class="personal-pages">
              <div v-if="['all', 'created'].includes(personalSource) && (legacyPages.personal > 1 || legacyHasMore.personal)" class="personal-page-group" aria-label="我创建的技能分页">
                <span>我创建的 · 第 {{ legacyPages.personal }} 页</span>
                <a-button size="small" :disabled="legacyPages.personal === 1" @click="changeLegacyPage('personal', legacyPages.personal - 1)">上一页</a-button>
                <a-button size="small" :disabled="!legacyHasMore.personal" @click="changeLegacyPage('personal', legacyPages.personal + 1)">下一页</a-button>
              </div>
              <div v-if="['all', 'installed'].includes(personalSource) && (personalSource === 'all' ? allInstalledTotal : installedTotal) > installedPageSize" class="personal-page-group" aria-label="市场已安装技能分页">
                <span>市场已安装 · 共 {{ personalSource === 'all' ? allInstalledTotal : installedTotal }} 项</span>
                <a-pagination :current="personalSource === 'all' ? allInstalledPage : installedPage" :page-size="installedPageSize" :total="personalSource === 'all' ? allInstalledTotal : installedTotal" :page-size-options="['20', '30', '50']" show-size-changer size="small" @change="changeInstalledPage" />
              </div>
              <div v-if="['all', 'shared'].includes(personalSource) && (personalSource === 'all' ? legacyTotals.allShared : legacyTotals.shared) > legacyPageSizes.shared" class="personal-page-group" aria-label="团队共享技能分页">
                <span>团队共享 · 共 {{ personalSource === 'all' ? legacyTotals.allShared : legacyTotals.shared }} 项</span>
                <a-pagination :current="legacyPages[personalSource === 'all' ? 'allShared' : 'shared']" :page-size="legacyPageSizes.shared" :total="personalSource === 'all' ? legacyTotals.allShared : legacyTotals.shared" :page-size-options="['20', '30', '50']" show-size-changer size="small" @change="(page, size) => changeLegacyPage(personalSource === 'all' ? 'allShared' : 'shared', page, size)" />
              </div>
              <div v-if="['all', 'system'].includes(personalSource) && (personalSource === 'all' ? legacyTotals.allBuiltin : legacyTotals.builtin) > legacyPageSizes.builtin" class="personal-page-group" aria-label="系统内置技能分页">
                <span>系统内置 · 共 {{ personalSource === 'all' ? legacyTotals.allBuiltin : legacyTotals.builtin }} 项</span>
                <a-pagination :current="legacyPages[personalSource === 'all' ? 'allBuiltin' : 'builtin']" :page-size="legacyPageSizes.builtin" :total="personalSource === 'all' ? legacyTotals.allBuiltin : legacyTotals.builtin" :page-size-options="['20', '30', '50']" show-size-changer size="small" @change="(page, size) => changeLegacyPage(personalSource === 'all' ? 'allBuiltin' : 'builtin', page, size)" />
              </div>
            </div>
          </a-spin>
          <p class="skill-list-hint">可用的共享、系统和市场技能，可在智能体中选择使用。</p>
        </template>
      </template>
    </section>

    <section v-else id="skill-market-view" role="tabpanel" class="skill-view-content">
      <div class="skill-page-heading">
        <div class="skill-page-title"><div class="skill-page-icon"><Sparkles :size="25" /></div><div><h2>技能市场</h2><p>发现已发布的技能，查看适用范围并安装到自己的空间。</p></div></div>
        <div class="skill-page-actions"><a-button v-if="userStore.isAdmin" @click="openMarketManagement">管理市场上架</a-button><a-button class="market-rankings-button" @click="openRankings"><ChartNoAxesColumn :size="16" /> 热门榜</a-button></div>
      </div>
      <div class="market-toolbar"><a-input-search v-model:value="query" placeholder="搜索技能名称或用途" allow-clear class="market-search" @search="searchMarket" @pressEnter="searchMarket" /><span v-if="!marketError && !loading" class="market-result-count">共 {{ marketTotal }} 项</span></div>
      <nav class="market-categories" aria-label="技能方向筛选">
        <button type="button" :class="{ active: selectedCategory === 'all' }" :aria-current="selectedCategory === 'all' ? 'page' : undefined" @click="selectCategory('all')">全部方向</button>
        <button v-for="category in skillCategories" :key="category.value" type="button" :class="{ active: selectedCategory === category.value }" :aria-current="selectedCategory === category.value ? 'page' : undefined" @click="selectCategory(category.value)">{{ category.label }}</button>
      </nav>
      <a-alert v-if="marketError" type="error" :message="marketError" show-icon class="market-error"><template #action><a-button size="small" @click="fetchMarketPage(marketPage)">重试</a-button></template></a-alert>
      <a-spin :spinning="loading">
        <div v-if="!visibleCards.length && !loading && !marketError" class="market-empty"><Store :size="28" /><strong>{{ catalogEmptyState.title }}</strong><span>{{ catalogEmptyState.hint }}</span></div>
        <div v-else-if="visibleCards.length" class="market-grid">
          <article v-for="item in visibleCards" :key="item.slug" class="market-card">
            <div class="market-card-top"><div class="market-icon"><Sparkles :size="20" /></div><div class="market-card-labels"><span class="market-category-label">{{ skillCategoryLabel(item.category) }}</span><span class="market-scope">{{ sourceLabel(item) }}</span></div></div>
            <button type="button" class="market-card-title" @click="openCard(item)">{{ item.name }}</button>
            <p>{{ item.description || '暂无描述' }}</p>
            <div class="market-card-author">由 {{ item.owner_name }} 发布 · v{{ item.latest_version }}</div>
            <div v-if="!item.installed_version && item.install_conflict === 'personal'" class="market-shared-note">已有同名个人技能</div>
            <div v-else-if="!item.installed_version && item.install_conflict === 'reserved'" class="market-shared-note">技能标识已被占用，暂无法安装</div>
            <div v-else-if="item.shared_available_slug && !item.installed_version && !ownPersonalCandidate(item)" class="market-shared-note">共享版本已可用</div>
            <div class="market-card-bottom">
              <button type="button" class="market-like" :class="{ liked: item.liked }" :aria-label="item.liked ? '取消点赞' : '点赞'" :disabled="busySlug === item.slug" @click="like(item)"><Heart :size="16" :fill="item.liked ? 'currentColor' : 'none'" /> {{ item.likes_count }}</button>
              <span v-if="ownPersonalCandidate(item) && !item.installed_version && item.install_conflict !== 'reserved' && item.install_conflict !== 'personal'" class="market-available">个人技能已可用</span><span v-else class="market-install-count"><Download :size="15" /> {{ item.install_count }} 人安装</span>
              <a-button v-if="item.installed_version || ownPersonalCandidate(item) || item.install_conflict !== 'reserved'" size="small" :loading="busySlug === item.slug" @click="act(item)">{{ item.upgrade_available ? '升级' : item.installed_version ? '查看' : item.install_conflict === 'personal' ? '查看已有技能' : ownPersonalCandidate(item) ? '查看个人技能' : item.shared_available_slug ? '安装市场版' : '安装' }}</a-button>
            </div>
          </article>
        </div>
        <div v-if="marketTotal > marketPageSize && !loading && !marketError" class="market-pages"><a-pagination :current="marketPage" :page-size="marketPageSize" :total="marketTotal" :page-size-options="['12', '24', '48']" show-size-changer @change="changeMarketPage" /></div>
      </a-spin>
      <p class="skill-list-hint">市场只展示当前账号有权查看的已上架技能。</p>
    </section>

    <PersonalSkillPublish v-if="publishOpen" v-model:open="publishOpen" :skill="publishSkill" @published="refreshHome" />

    <a-drawer v-model:open="rankingsOpen" title="市场热门榜" width="min(520px, 96vw)" class="skill-rankings-drawer">
      <div class="ranking-controls">
        <div class="ranking-metric-tabs" role="group" aria-label="榜单类型">
          <button v-for="option in rankingMetricOptions" :key="option.value" type="button" :class="{ active: rankingMetric === option.value }" :aria-pressed="rankingMetric === option.value" @click="selectRankingMetric(option.value)">{{ option.label }}</button>
        </div>
        <div class="ranking-audience">
          <span>统计成员</span>
          <a-select :value="rankingAudience" aria-label="榜单统计成员" :options="rankingAudienceOptions" :loading="departmentLoading && userStore.isSuperAdmin" @change="selectRankingAudience" />
        </div>
      </div>
      <a-alert v-if="userStore.isSuperAdmin && departmentError" type="error" :message="`部门列表加载失败：${departmentError}`" show-icon class="market-error">
        <template #action><a-button size="small" @click="fetchDepartmentNames">重试</a-button></template>
      </a-alert>
      <p class="ranking-hint">{{ rankingScopeHint }}的{{ rankingMetric === 'installs' ? '安装人数' : '点赞人数' }}排序，仅展示你可查看的市场技能。</p>
      <a-alert v-if="rankingError" type="error" :message="rankingError" show-icon class="market-error">
        <template #action><a-button size="small" @click="fetchRankings">重试</a-button></template>
      </a-alert>
      <a-spin :spinning="rankingsLoading">
        <div v-if="!rankings.length && !rankingsLoading && !rankingError" class="personal-empty">暂无{{ rankingMetric === 'installs' ? '安装' : '点赞' }}记录</div>
        <ol v-else-if="rankings.length" class="ranking-list">
          <li v-for="(item, index) in rankings" :key="item.slug">
            <span class="ranking-position">{{ index + 1 }}</span>
            <span class="ranking-copy"><strong>{{ item.name }}</strong><small>{{ marketScopeLabel(item) }}发布</small></span>
            <span class="ranking-count">{{ rankingMetric === 'installs' ? item.install_count : item.likes_count }} {{ rankingMetric === 'installs' ? '安装' : '点赞' }}</span>
            <a-button size="small" @click="openDetail(item.slug)">查看</a-button>
          </li>
        </ol>
      </a-spin>
    </a-drawer>

    <a-modal v-model:open="detailOpen" :footer="null" width="760px" title="Skill 详情">
      <a-spin :spinning="detailLoading">
        <div v-if="detail" class="market-detail">
          <div class="market-detail-heading">
            <div><h3>{{ detail.name }}</h3><span>技能标识：{{ detail.slug }} · 由 {{ detail.owner_name }} 发布</span></div>
            <a-tag>{{ marketScopeLabel(detail) }}</a-tag>
          </div>
          <p>{{ detail.description }}</p>
          <div class="market-detail-meta">
            <span>方向 · {{ skillCategoryLabel(detail.category) }}</span>
            <span>当前版本 v{{ detail.latest_version }}</span>
            <span v-if="ownPersonalCandidate(detail) && !detail.installed_version && !detail.install_conflict">个人技能已可用</span>
            <span v-else>已安装 {{ detail.installed_version ? `v${detail.installed_version}` : '—' }}</span>
            <span>{{ detail.install_count }} 人安装</span>
          </div>
          <a-alert v-if="detail.install_conflict === 'personal' && !detail.installed_version" type="info" show-icon message="你已有同标识的个人技能，Agent 会优先使用个人版本。当前不能同时安装此市场版本。" />
          <a-alert v-else-if="detail.install_conflict === 'reserved' && !detail.installed_version" type="warning" show-icon message="该技能标识已被占用，当前不能安装此市场版本。" />
          <a-alert v-else-if="ownPersonalCandidate(detail) && !detail.installed_version" type="info" show-icon message="你的个人技能已可在 Agent 中使用，无需再次安装。" />
          <a-alert v-if="detail.shared_available_slug && !detail.installed_version && !detail.install_conflict && !ownPersonalCandidate(detail)" type="info" show-icon message="共享版本已可直接使用；安装市场版可使用当前审核版本。" />
          <a-alert v-if="detail.installed_version" type="info" show-icon message="市场版本由发布者维护。你可以在有编辑权限的 Agent 中选择是否使用，也可以卸载自己的安装。" />
          <a-alert v-if="detail.upgrade_available" type="info" show-icon message="新版本已发布。升级需由你手动确认。" />
          <div v-if="dependencyLabels.length" class="market-dependencies">
            <strong>依赖</strong><a-tag v-for="dependency in dependencyLabels" :key="dependency">{{ dependency }}</a-tag>
          </div>
          <div class="market-files">
            <div class="market-files-list">
              <strong>发布文件</strong>
              <button v-for="file in files" :key="file.path" type="button" :class="{ active: file.path === selectedFile }" @click="selectFile(file.path)">{{ file.path }}<span v-if="file.previewable === false" class="market-file-unpreviewable"> · 不可预览</span></button>
            </div>
            <div class="market-file-content">
              <a-spin :spinning="fileLoading"><MarkdownPreview v-if="fileContent && isMarkdownFile(selectedFile)" :content="fileContent" :compact="true" /><pre v-else-if="fileContent" class="market-code-preview"><code>{{ fileContent }}</code></pre><a-empty v-else :description="fileError || '选择文件预览'" /></a-spin>
            </div>
          </div>
          <div class="market-detail-actions">
            <a-button v-if="detail.installed_version" danger :loading="busySlug === detail.slug" @click="confirmUninstall(detail)">卸载</a-button>
            <a-button v-if="canManageMarketListing(detail)" @click="openMarketManagement(detail.slug)">管理上架</a-button>
            <a-button v-if="detail.upgrade_available || (!detail.installed_version && (ownPersonalCandidate(detail) || detail.install_conflict !== 'reserved'))" type="primary" :loading="busySlug === detail.slug" @click="act(detail)">{{ detail.upgrade_available ? '升级到最新版本' : detail.install_conflict === 'personal' ? '查看已有技能' : ownPersonalCandidate(detail) ? '查看个人技能' : detail.shared_available_slug ? '安装市场版' : '安装 Skill' }}</a-button>
          </div>
        </div>
      </a-spin>
    </a-modal>

    <a-drawer v-model:open="marketManagementOpen" title="市场上架管理" width="min(900px, 96vw)" @close="closeMarketManagement">
      <p class="managed-market-intro">管理当前有权处理的市场条目。已下架技能保留原有版本和审核记录。</p>
      <div class="managed-market-toolbar">
        <a-input-search v-model:value="managedMarketQuery" allow-clear aria-label="搜索市场管理技能" placeholder="搜索名称或技能标识" class="managed-market-search" @search="searchManagedMarket" />
        <div class="managed-market-filters">
          <a-select :value="managedMarketStatus" aria-label="市场管理状态筛选" :options="managedMarketStatusOptions" @change="changeManagedMarketStatus" />
          <a-select v-if="userStore.isSuperAdmin" :value="managedMarketScope" aria-label="市场管理范围筛选" :options="managedMarketScopeOptions" @change="changeManagedMarketScope" />
        </div>
        <span v-if="!managedMarketLoading && !managedMarketError" class="managed-market-count">共 {{ managedMarketTotal }} 项</span>
      </div>
      <a-alert v-if="managedMarketError" type="error" :message="managedMarketError" show-icon class="market-error"><template #action><a-button size="small" @click="fetchManagedMarket">重试</a-button></template></a-alert>
      <a-alert v-if="managedActionError" type="warning" :message="managedActionError" show-icon class="market-error" />
      <a-spin :spinning="managedMarketLoading">
        <div v-if="!managedMarketLoading && !managedMarketError && !managedMarketItems.length" class="personal-empty">{{ managedMarketAppliedQuery || managedMarketStatus !== 'all' || managedMarketScope !== 'all' ? '没有符合条件的市场技能。' : '暂无可管理的市场技能。' }}</div>
        <div v-else-if="!managedMarketError" class="managed-market-layout">
          <div class="managed-market-list" aria-label="市场上架管理清单">
            <article v-for="item in managedMarketItems" :key="item.slug" :data-managed-slug="item.slug" class="managed-market-row" :class="{ selected: selectedManagedSlug === item.slug }">
              <div class="managed-market-row-copy"><strong>{{ item.name || item.slug }}</strong><small>{{ item.slug }} · v{{ item.latest_version || '—' }} · {{ managedScopeLabel(item) }}</small></div>
              <span class="managed-market-status" :class="{ withdrawn: !item.is_published }">{{ item.is_published ? '已上架' : '已下架' }}</span>
              <a-button size="small" @click="openManagedDetail(item.slug)">管理</a-button>
            </article>
          </div>
          <section v-if="selectedManagedSlug" class="managed-market-detail" aria-label="市场条目管理详情">
            <a-spin :spinning="managedDetailLoading">
              <div v-if="managedDetailError" role="alert" class="managed-detail-feedback">{{ managedDetailError }} <a-button size="small" @click="openManagedDetail(selectedManagedSlug)">重试</a-button></div>
              <template v-else-if="managedDetail">
                <div class="managed-market-detail-heading"><h3>{{ managedDetail.name || managedDetail.slug }}</h3><span class="managed-market-status" :class="{ withdrawn: !managedDetail.is_published }">{{ managedDetail.is_published ? '已上架' : '已下架' }}</span></div>
                <p class="managed-market-meta">{{ managedDetail.slug }} · 最新审核版本 v{{ managedDetail.latest_version || '—' }}</p>
                <p>当前范围：{{ managedScopeLabel(managedDetail) }}</p>
                <p v-if="!managedDetail.is_published" class="managed-market-notice">作者需从个人技能重新提交快照，审核通过后才会再次上架。</p>
                <template v-else>
                  <div v-if="userStore.isSuperAdmin" class="managed-market-scope">
                    <label for="managed-market-scope">当前上架范围</label>
                    <a-select id="managed-market-scope" v-model:value="managedScopeChoice" aria-label="当前上架范围" :options="managedScopeOptions" :loading="departmentLoading" />
                    <span>调整后，范围外成员的旧安装将暂不可用；再次进入范围会自动恢复。</span>
                    <a-button :disabled="!canSaveManagedScope" :loading="managedActionBusy" @click="confirmManagedScope">保存范围</a-button>
                  </div>
                  <div class="managed-market-danger"><a-button danger :loading="managedActionBusy" @click="confirmManagedUnpublish">下架技能</a-button></div>
                </template>
              </template>
            </a-spin>
          </section>
          <div v-else class="managed-market-placeholder">选择一项查看当前状态和可用操作。</div>
        </div>
      </a-spin>
      <div v-if="managedMarketTotal > managedMarketPageSize && !managedMarketLoading && !managedMarketError" class="managed-market-pages">
        <a-pagination :current="managedMarketPage" :page-size="managedMarketPageSize" :total="managedMarketTotal" :show-size-changer="false" size="small" aria-label="市场上架管理分页" @change="changeManagedMarketPage" />
      </div>
    </a-drawer>

    <a-modal v-model:open="requestsOpen" title="市场审核申请" :footer="null" width="760px">
      <div class="market-request-toolbar">
        <span>状态</span>
        <a-select v-model:value="requestStatus" aria-label="市场申请状态" :options="requestStatusOptions" @change="changeRequestStatus" />
        <span>共 {{ requestsTotal }} 条</span>
      </div>
      <a-spin :spinning="requestsLoading">
        <a-empty v-if="!requests.length && !requestsLoading" description="暂无申请" />
        <div v-for="request in requests" :key="request.id" class="market-request">
          <div class="market-request-title"><strong>{{ request.name }}</strong><a-tag>{{ request.status === 'pending' ? '待审核' : request.status === 'approved' ? '已发布' : '已拒绝' }}</a-tag></div>
          <p>{{ request.application_reason }}</p>
          <div class="market-request-meta">{{ request.owner_uid }} · {{ request.personal_slug }} · {{ skillCategoryLabel(request.category) }} <span v-if="request.status === 'approved'">· {{ requestScopeLabel(request) }}</span><span v-if="request.review_note"> · {{ request.review_note }}</span></div>
          <div v-if="request.status === 'pending' && userStore.isAdmin" class="market-request-actions">
            <a-button size="small" @click="previewRequest(request)">查看提交快照</a-button>
            <a-button size="small" type="primary" :disabled="Boolean(approvalBlockReason(request))" @click="openReview(request, true)">批准</a-button>
            <a-button size="small" danger @click="openReview(request, false)">拒绝</a-button>
          </div>
          <p v-if="request.status === 'pending' && userStore.isAdmin && approvalBlockReason(request)" class="market-request-review-reason">{{ approvalBlockReason(request) }}</p>
        </div>
      </a-spin>
      <div v-if="requestsTotal > requestsPageSize" class="market-request-pages">
        <a-button :disabled="requestsPage === 1 || requestsLoading" @click="changeRequestsPage(requestsPage - 1)">上一页</a-button>
        <span>第 {{ requestsPage }} / {{ Math.ceil(requestsTotal / requestsPageSize) }} 页</span>
        <a-button :disabled="requestsPage * requestsPageSize >= requestsTotal || requestsLoading" @click="changeRequestsPage(requestsPage + 1)">下一页</a-button>
      </div>
    </a-modal>

    <a-modal v-model:open="snapshotOpen" title="提交快照" :footer="null" width="740px" :body-style="{ maxHeight: 'calc(100vh - 160px)', overflowY: 'auto' }">
      <a-button class="market-snapshot-download" @click="downloadSnapshot">下载完整快照（含附件）</a-button>
      <div class="market-files snapshot-files"><div class="market-files-list"><strong>文件</strong><button v-for="file in snapshotFiles" :key="file.path" type="button" :class="{ active: file.path === snapshotSelected }" @click="selectSnapshotFile(file.path)">{{ file.path }}<span v-if="file.previewable === false" class="market-file-unpreviewable"> · 不可预览</span></button></div><div class="market-file-content"><a-spin :spinning="snapshotLoading"><MarkdownPreview v-if="snapshotContent && isMarkdownFile(snapshotSelected)" :content="snapshotContent" :compact="true" /><pre v-else-if="snapshotContent" class="market-code-preview"><code>{{ snapshotContent }}</code></pre><a-empty v-else :description="snapshotError || '选择文件预览'" /></a-spin></div></div>
    </a-modal>

    <a-modal v-model:open="reviewOpen" :title="reviewApproved ? '批准发布' : '拒绝申请'" :ok-text="reviewApproved ? '确认发布' : '确认拒绝'" :confirm-loading="reviewLoading" @ok="review">
      <p>{{ reviewRequest?.name }} · {{ reviewRequest?.personal_slug }}</p>
      <a-radio-group v-if="reviewApproved" v-model:value="reviewScope" class="market-review-scope" :disabled="Boolean(reviewRequest?.existing_market_scope)">
        <a-radio value="department">{{ reviewDepartmentLabel }}（{{ reviewDepartmentName }}）</a-radio><a-radio v-if="userStore.isSuperAdmin" value="global">全公司</a-radio>
      </a-radio-group>
      <a-alert v-if="reviewApproved && reviewScopeNotice" type="info" show-icon :message="reviewScopeNotice" />
      <a-textarea v-model:value="reviewNote" placeholder="审核意见（可选）" :rows="3" :maxlength="2000" />
    </a-modal>
  </section>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { Blocks, BookOpen, ChartNoAxesColumn, Download, Heart, Sparkles, Store } from '@lucide/vue'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import PersonalSkillPublish from '@/components/extensions/PersonalSkillPublish.vue'
import PublicationRecords from '@/components/extensions/PublicationRecords.vue'
import SkillCardList from '@/components/extensions/SkillCardList.vue'
import { canManageSkillRecord } from '@/components/extensions/skillManagementAccess'
import { combineSkillCards, filterSkillCards, marketInstallManagementItems, ownPersonalSkill, personalSpaceSkillGroups, skillDetailRoute } from '@/components/extensions/skillHubCatalog'
import { skillCategories, skillCategoryLabel } from '@/components/extensions/skillCategories'
import { useUserStore } from '@/stores/user'
import { departmentApi } from '@/apis/department_api'
import {
  getMarketSkill,
  getMarketSkillFile,
  getMarketSkillTree,
  getPersonalSkillSummary,
  getManagedMarketSkill,
  installMarketSkill,
  listManagedMarketSkills,
  listMarketInstalls,
  uninstallMarketSkill,
  listMarketRequests,
  listMarketRankings,
  listMarketSkills,
  listSkillCards,
  getPersonalSkillDetail,
  getSkillShareSnapshotTree,
  getSkillShareSnapshot,
  exportSkillShareSnapshot,
  reviewMarketSkill,
  toggleMarketSkillLike,
  unpublishMarketSkill,
  changeMarketSkillScope,
  upgradeMarketSkill
} from '@/apis/skill_api'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const rankingMetricOptions = [
  { label: '安装最多', value: 'installs' },
  { label: '点赞最多', value: 'likes' }
]
const personalSourceFilters = [
  { key: 'all', label: '全部' },
  { key: 'created', label: '我创建的' },
  { key: 'installed', label: '市场已安装' },
  { key: 'shared', label: '团队共享' },
  { key: 'system', label: '系统内置' }
]
/** 前两个来源由独立统计请求提供完整去重总数，其余来源沿用数据库总数。 */
function personalFilterCount(source) {
  if (source === 'all' || source === 'created') {
    if (skillSummaryLoading.value) return '…'
    return skillSummary.value?.[source === 'all' ? 'all_total' : 'created_total'] ?? '—'
  }
  if (source === 'installed') return installedTotal.value
  return source === 'shared' ? legacyTotals.value.shared : legacyTotals.value.builtin
}
const query = ref('')
const selectedCategory = ref('all')
const activeView = ref(route.query.view === 'market' || route.query.tab === 'market' ? 'market' : 'personal')
const rankingsOpen = ref(false)
const rankingMetric = ref('installs')
const rankingScope = ref('company')
const rankingDepartmentId = ref(userStore.departmentId ? Number(userStore.departmentId) : undefined)
const departmentNames = ref({})
const departmentLoading = ref(false)
const departmentError = ref('')
const rankingDepartmentOptions = computed(() => Object.entries(departmentNames.value).map(([id, name]) => ({ value: Number(id), label: name })))
const rankingAudience = computed({
  get: () => rankingScope.value === 'company'
    ? 'company'
    : userStore.isSuperAdmin ? `department:${rankingDepartmentId.value}` : 'department',
  set: (value) => {
    rankingScope.value = value === 'company' ? 'company' : 'department'
    if (userStore.isSuperAdmin && value !== 'company') rankingDepartmentId.value = Number(value.split(':')[1])
  }
})
const rankingAudienceOptions = computed(() => [
  { label: '全公司', value: 'company' },
  ...(userStore.isSuperAdmin
    ? rankingDepartmentOptions.value.map(({ value, label }) => ({ label, value: `department:${value}` }))
    : userStore.departmentId ? [{ label: '本部门', value: 'department' }] : [])
])
const rankingScopeHint = computed(() => {
  if (rankingScope.value === 'company') return '按全公司当前成员'
  const departmentId = userStore.isSuperAdmin ? rankingDepartmentId.value : userStore.departmentId
  const departmentName = departmentNames.value[departmentId] || (Number(departmentId) === Number(userStore.departmentId) ? userStore.departmentName : '') || `部门 ${departmentId}`
  return `按${departmentName}当前成员`
})
const rankings = ref([])
const rankingsLoading = ref(false)
const rankingError = ref('')
let rankingRequestSequence = 0
const cards = ref([])
const installedCards = ref([])
const allInstalledCards = ref([])
const legacyPersonalCards = ref([])
const legacySharedCards = ref([])
const legacyAllSharedCards = ref([])
const legacyBuiltinCards = ref([])
const legacyAllBuiltinCards = ref([])
const legacyCards = computed(() => [
  ...legacyPersonalCards.value,
  ...(personalSource.value === 'all' ? legacyAllSharedCards.value : legacySharedCards.value),
  ...(personalSource.value === 'all' ? legacyAllBuiltinCards.value : legacyBuiltinCards.value)
])
const loading = ref(false)
const legacyLoading = ref(false)
const installedLoading = ref(false)
const allInstalledLoading = ref(false)
const legacyHasMore = ref({ personal: false })
const legacyPageLoading = ref({ personal: false, shared: false, allShared: false, builtin: false, allBuiltin: false })
const legacyPages = ref({ personal: 1, shared: 1, allShared: 1, builtin: 1, allBuiltin: 1 })
const legacyPageSizes = ref({ shared: 30, builtin: 30 })
const legacyTotals = ref({ shared: 0, allShared: 0, builtin: 0, allBuiltin: 0 })
const installedPage = ref(1)
const allInstalledPage = ref(1)
const installedPageSize = ref(30)
const installedTotal = ref(0)
const allInstalledTotal = ref(0)
const marketPage = ref(1)
const marketPageSize = ref(24)
const marketTotal = ref(0)
const appliedMarketQuery = ref('')
const personalPageSize = 30
let requestSequence = 0
const legacyRequestSequence = { personal: 0, shared: 0, allShared: 0, builtin: 0, allBuiltin: 0 }
let legacyRefreshSequence = 0
let installedRequestSequence = 0
let allInstalledRequestSequence = 0
let personalSearchTimer
const personalError = ref('')
const skillSummary = ref(null)
const skillSummaryLoading = ref(true)
const skillSummaryError = ref('')
let skillSummaryRequestSequence = 0
const missingPersonalSlugs = ref(new Set())
const marketError = ref('')
const busySlug = ref('')
const managementOpen = ref(false)
const marketManagementOpen = ref(false)
const managedMarketItems = ref([])
const managedMarketLoading = ref(false)
const managedMarketError = ref('')
const managedMarketQuery = ref('')
const managedMarketAppliedQuery = ref('')
const managedMarketExactSlug = ref('')
const managedMarketStatus = ref('all')
const managedMarketScope = ref('all')
const managedMarketPage = ref(1)
const managedMarketPageSize = 20
const managedMarketTotal = ref(0)
const managedMarketStatusOptions = [
  { value: 'all', label: '全部状态' },
  { value: 'published', label: '已上架' },
  { value: 'withdrawn', label: '已下架' }
]
const managedMarketScopeOptions = [
  { value: 'all', label: '全部范围' },
  { value: 'global', label: '全公司' },
  { value: 'department', label: '部门' }
]
const managedActionError = ref('')
const selectedManagedSlug = ref('')
const managedDetail = ref(null)
const managedDetailLoading = ref(false)
const managedDetailError = ref('')
const managedScopeChoice = ref('')
const managedActionBusy = ref(false)
let managedMarketSequence = 0
let managedDetailSequence = 0
const releaseView = ref(false)
const publishOpen = ref(false)
const publishSkill = ref(null)
const personalSource = ref('all')
const personalQuery = ref('')
const appliedPersonalQuery = ref('')
const combinedCards = computed(() => combineSkillCards(cards.value, legacyCards.value))
const visibleCards = computed(() => filterSkillCards(combinedCards.value, '', 'market'))
const catalogEmptyState = computed(() => {
  if (appliedMarketQuery.value) return { title: '没有找到匹配的技能', hint: '换个关键词试试。' }
  return selectedCategory.value === 'all'
    ? { title: '市场里还没有技能', hint: '可以先创建个人技能，再申请发布到市场。' }
    : { title: '这个方向还没有技能', hint: '切换方向查看其他技能。' }
})
/** 先按作者身份折叠安装，再隐藏失去可见性的市场详情。 */
function safePersonalItem(item) {
  if (item.kind === 'personal' && item.market_install?.visible === false) {
    return { ...item, market_install: marketInstallManagementItems([item.market_install])[0] }
  }
  if (item.kind === 'market' && item.visible === false) {
    return { ...marketInstallManagementItems([item])[0], kind: 'market' }
  }
  return item
}
const personalSpaceGroups = computed(() => {
  const groups = personalSpaceSkillGroups(legacyCards.value, personalSource.value === 'all' ? allInstalledCards.value : installedCards.value, userStore.uid)
  return Object.fromEntries(Object.entries(groups).map(([source, items]) => [source, items.map(safePersonalItem)]))
})
const personalSourceGroups = computed(() => {
  return {
    all: personalSpaceGroups.value.all,
    created: personalSpaceGroups.value.created,
    installed: personalSpaceGroups.value.installed,
    shared: personalSpaceGroups.value.shared,
    system: personalSpaceGroups.value.system
  }
})
const filteredPersonalItems = computed(() => personalSourceGroups.value[personalSource.value])
const ownedSkills = computed(() => personalSourceGroups.value.created)
const personalBusy = computed(() => legacyLoading.value || installedLoading.value || allInstalledLoading.value || Object.values(legacyPageLoading.value).some(Boolean))
const hasManageableSharedSkills = computed(() => legacySharedCards.value.some((item) => canManageSkillRecord(item)))
const pendingRequestCount = ref(0)
const detailOpen = ref(false)
const detailLoading = ref(false)
const detail = ref(null)
const files = ref([])
const selectedFile = ref('')
const fileContent = ref('')
const fileLoading = ref(false)
const fileError = ref('')
let detailRequestSequence = 0
let fileRequestSequence = 0
const dependencyLabels = computed(() => detail.value ? [
  ...detail.value.tool_dependencies.map((item) => `工具 · ${item}`),
  ...detail.value.mcp_dependencies.map((item) => `MCP · ${item}`),
  ...detail.value.skill_dependencies.map((item) => `Skill · ${item}`)
] : [])

const requestsOpen = ref(false)
const requestsLoading = ref(false)
const requests = ref([])
const requestsTotal = ref(0)
const requestsPage = ref(1)
const requestsPageSize = 10
const requestStatus = ref('pending')
const requestStatusOptions = [
  { value: 'pending', label: '待审核' }, { value: 'all', label: '全部' },
  { value: 'approved', label: '已发布' }, { value: 'rejected', label: '已拒绝' }
]
let requestsSequence = 0
const reviewOpen = ref(false)
const reviewLoading = ref(false)
const reviewApproved = ref(true)
const reviewRequest = ref(null)
const reviewScope = ref('department')
const reviewNote = ref('')
const reviewDepartmentId = computed(() => reviewRequest.value?.existing_market_scope === 'department'
  ? reviewRequest.value.existing_market_department_id
  : reviewRequest.value?.owner_department_id)
const reviewDepartmentName = computed(() => departmentNames.value[reviewDepartmentId.value] || `部门 ${reviewDepartmentId.value || '未分配'}`)
const reviewDepartmentLabel = computed(() => reviewRequest.value?.existing_market_scope === 'department' ? '原发布部门' : '申请提交时所属部门')
const reviewDepartmentNotice = computed(() => {
  const departmentId = reviewDepartmentId.value
  if (!departmentId) return '申请人未分配部门，无法按部门发布。'
  if (!userStore.isSuperAdmin && Number(userStore.departmentId) !== Number(departmentId)) return '申请提交时所属部门成员和超级管理员可见。你当前不属于该部门，发布后不会在你的市场目录出现。'
  return '申请提交时所属部门成员和超级管理员可在市场查看和安装。'
})
const reviewScopeNotice = computed(() => {
  const scope = reviewRequest.value?.existing_market_scope
  if (scope === 'global') return '新版本沿用原有发布范围（全公司可见），审核时不可更改。'
  if (scope === 'department') {
    return `新版本沿用原有发布范围（${reviewDepartmentName.value}及超级管理员可见），审核时不可更改。`
  }
  return reviewScope.value === 'department' ? reviewDepartmentNotice.value : ''
})
const snapshotOpen = ref(false)
const snapshotLoading = ref(false)
const snapshotFiles = ref([])
const snapshotSelected = ref('')
const snapshotContent = ref('')
const snapshotError = ref('')
const snapshotRequestId = ref('')
let snapshotRequestSequence = 0
let snapshotFileRequestSequence = 0

const errorText = (failure) => failure?.response?.data?.detail || failure?.message || '操作失败，请稍后重试'
const flattenFiles = (nodes) => nodes.flatMap((node) => node.is_dir ? flattenFiles(node.children || []) : [node])
const isMarkdownFile = (path) => path?.toLowerCase().endsWith('.md')
const marketScopeLabel = (item) => {
  if (item.scope === 'global') return '全公司'
  if (Number(item.department_id) === Number(userStore.departmentId)) return '本部门'
  return departmentNames.value[item.department_id] || `部门 ${item.department_id}`
}
const canManageMarketListing = (item) => userStore.isSuperAdmin || (
  userStore.isAdmin && item?.scope === 'department' && item.department_id != null &&
  Number(item.department_id) === Number(userStore.departmentId)
)
const sourceLabel = (item) => item.kind === 'market'
  ? `市场 · ${marketScopeLabel(item)}`
  : item.source_type === 'builtin' ? '系统内置' : '共享技能'
const personalItemOrigin = (item) => item.kind === 'personal'
  ? '我创建的'
  : item.kind === 'market' ? '市场已安装' : item.source_type === 'builtin' ? '系统内置' : '团队共享'
/** 个人空间同时说明原件、市场安装及过期授权。 */
const personalItemStatus = (item) => {
  if (item.kind === 'existing') {
    const source = item.source_type === 'builtin' ? '系统内置' : '共享技能'
    const version = item.source_type === 'personal_share' && item.version ? ` · v${item.version}` : ''
    return `${source}${version}${item.shadowed_by_personal ? ' · 个人版本优先' : ''}`
  }
  if (item.kind === 'market') {
    return item.unavailable_reason || item.visible === false
      ? `市场已安装 · ${marketInstallReason(item.unavailable_reason)} · v${item.installed_version}`
      : `市场已安装 · v${item.installed_version}`
  }
  if (!item.market_installed_version) return '个人技能'
  return item.market_visible === false || item.market_install?.unavailable_reason
    ? `个人技能 · 市场安装${item.market_install?.unavailable_reason ? marketInstallReason(item.market_install.unavailable_reason) : '已失效'} v${item.market_installed_version}`
    : `个人技能 · 市场已安装 v${item.market_installed_version}`
}
/** 把旧安装的服务端不可用原因转为安全的行动提示。 */
function marketInstallReason(reason) {
  return { unpublished: '已下架', out_of_scope: '当前不在使用范围', needs_confirmation: '需确认新版' }[reason] || '当前不可用'
}
const requestScopeLabel = (request) => {
  if (request.read_scope?.access_level === 'global') return '全公司可见'
  const departmentId = request.read_scope?.department_ids?.[0] ?? request.department_ids?.[0] ?? request.owner_department_id
  return `${departmentNames.value[departmentId] || `部门 ${departmentId || '未分配'}`}及超级管理员可见`
}
/** 已发布技能的新版本沿用原范围，跨部门及全公司续版由超级管理员审核。 */
function approvalBlockReason(request) {
  if (request.existing_market_scope === 'global' && !userStore.isSuperAdmin) return '此技能已向全公司发布，只有超级管理员可以批准新版本。'
  if (request.existing_market_scope === 'department' && Number(request.existing_market_department_id) !== Number(request.owner_department_id) && !userStore.isSuperAdmin) return '原发布部门与申请提交时所属部门不一致，仅超级管理员可以批准新版本。'
  return ''
}
/** 当前用户已有个人原件时让市场入口回到原件。 */
const ownPersonalSkillFor = (item) => ownPersonalSkill(item, legacyCards.value, userStore.uid)
const ownPersonalCandidate = (item) => Boolean(ownPersonalSkillFor(item) || (
  item?.owner_uid === userStore.uid && item.own_personal_slug && !missingPersonalSlugs.value.has(item.own_personal_slug)
))
/** 详情响应补上当前已授权共享副本的可用状态。 */
const marketCardWithSharedStatus = (item) => combineSkillCards([item], legacyCards.value)[0]

/** 页面切换写入路由，详情返回时默认恢复个人清单。 */
function selectView(view) {
  activeView.value = view
  managementOpen.value = false
  closeMarketManagement()
  releaseView.value = false
  const nextQuery = { ...route.query, tab: 'skills' }
  if (view === 'market') nextQuery.view = 'market'
  else delete nextQuery.view
  router.replace({ path: route.path, query: nextQuery })
}

function openPublish(item) {
  publishSkill.value = item
  publishOpen.value = true
}

function openCard(item) {
  if (item.kind === 'market') return openDetail(item.slug)
  router.push(skillDetailRoute(item))
}

function openPersonalItem(item) {
  if (item.kind === 'market') return openDetail(item.slug)
  router.push(skillDetailRoute(item))
}

function openManagementMarket(slug) {
  managementOpen.value = false
  openDetail(slug)
}

/** 打开独立管理清单，已下架条目也只能从管理接口读取。 */
async function openMarketManagement(slug) {
  if (!userStore.isAdmin) return
  marketManagementOpen.value = true
  managedActionError.value = ''
  managedMarketQuery.value = typeof slug === 'string' ? slug : ''
  managedMarketAppliedQuery.value = managedMarketQuery.value
  managedMarketExactSlug.value = typeof slug === 'string' ? slug : ''
  managedMarketStatus.value = 'all'
  managedMarketScope.value = 'all'
  managedMarketPage.value = 1
  clearManagedDetail()
  const loading = fetchManagedMarket()
  const sequence = managedMarketSequence
  await loading
  if (marketManagementOpen.value && sequence === managedMarketSequence && typeof slug === 'string' && managedMarketItems.value.some((item) => item.slug === slug)) {
    await openManagedDetail(slug)
  }
}

/** 使旧详情请求失效，避免筛选或翻页后重新显示范围外条目。 */
function clearManagedDetail() {
  managedDetailSequence++
  selectedManagedSlug.value = ''
  managedDetail.value = null
  managedDetailError.value = ''
  managedDetailLoading.value = false
}

/** 关闭管理面板并使旧列表、详情响应失效。 */
function closeMarketManagement() {
  marketManagementOpen.value = false
  managedMarketSequence++
  clearManagedDetail()
}

/** 搜索条件生效时从第一页重新读取清单。 */
function searchManagedMarket() {
  const query = managedMarketQuery.value.trim()
  if (query === managedMarketAppliedQuery.value && !managedMarketExactSlug.value) return
  managedMarketQuery.value = query
  managedMarketAppliedQuery.value = query
  managedMarketExactSlug.value = ''
  managedMarketPage.value = 1
  clearManagedDetail()
  fetchManagedMarket()
}

/** 状态变化后丢弃旧页和详情。 */
function changeManagedMarketStatus(status) {
  if (status === managedMarketStatus.value) return
  managedMarketAppliedQuery.value = managedMarketQuery.value.trim()
  managedMarketQuery.value = managedMarketAppliedQuery.value
  managedMarketExactSlug.value = ''
  managedMarketStatus.value = status
  managedMarketPage.value = 1
  clearManagedDetail()
  fetchManagedMarket()
}

/** 范围筛选仅由超级管理员使用。 */
function changeManagedMarketScope(scope) {
  if (!userStore.isSuperAdmin || scope === managedMarketScope.value) return
  managedMarketAppliedQuery.value = managedMarketQuery.value.trim()
  managedMarketQuery.value = managedMarketAppliedQuery.value
  managedMarketExactSlug.value = ''
  managedMarketScope.value = scope
  managedMarketPage.value = 1
  clearManagedDetail()
  fetchManagedMarket()
}

/** 翻页时清除不在目标页的详情。 */
function changeManagedMarketPage(page) {
  if (page === managedMarketPage.value) return
  managedMarketQuery.value = managedMarketAppliedQuery.value
  managedMarketPage.value = page
  clearManagedDetail()
  fetchManagedMarket()
}

/** 管理清单仅使用管理员专用接口。 */
async function fetchManagedMarket() {
  if (!userStore.isAdmin) return
  const sequence = ++managedMarketSequence
  managedMarketLoading.value = true
  managedMarketError.value = ''
  managedMarketItems.value = []
  try {
    const result = await listManagedMarketSkills({
      query: managedMarketAppliedQuery.value,
      slug: managedMarketExactSlug.value,
      status: managedMarketStatus.value,
      scope: userStore.isSuperAdmin ? managedMarketScope.value : 'all',
      offset: (managedMarketPage.value - 1) * managedMarketPageSize,
      limit: managedMarketPageSize
    })
    if (sequence !== managedMarketSequence || !marketManagementOpen.value) return
    const total = result.total ?? 0
    const lastPage = Math.max(1, Math.ceil(total / managedMarketPageSize))
    const rows = result.data || []
    if (managedMarketPage.value > 1 && (managedMarketPage.value > lastPage || !rows.length)) {
      managedMarketPage.value = rows.length ? lastPage : 1
      clearManagedDetail()
      return await fetchManagedMarket()
    }
    managedMarketItems.value = rows
    managedMarketTotal.value = total
    if (selectedManagedSlug.value && !managedMarketItems.value.some((item) => item.slug === selectedManagedSlug.value)) clearManagedDetail()
  } catch (failure) {
    if (sequence === managedMarketSequence) {
      managedMarketError.value = errorText(failure)
      managedMarketTotal.value = 0
      clearManagedDetail()
    }
  } finally {
    if (sequence === managedMarketSequence) managedMarketLoading.value = false
  }
}

/** 切换管理详情时丢弃先前条目的迟到响应。 */
async function openManagedDetail(slug) {
  if (!marketManagementOpen.value || !userStore.isAdmin || !slug) return
  const sequence = ++managedDetailSequence
  selectedManagedSlug.value = slug
  managedDetail.value = null
  managedDetailError.value = ''
  managedDetailLoading.value = true
  try {
    const item = (await getManagedMarketSkill(slug))?.data
    if (sequence !== managedDetailSequence) return
    managedDetail.value = item
    managedScopeChoice.value = item?.scope === 'global' ? 'global' : `department:${item?.department_id}`
  } catch (failure) {
    if (sequence === managedDetailSequence) managedDetailError.value = errorText(failure)
  } finally {
    if (sequence === managedDetailSequence) managedDetailLoading.value = false
  }
}

/** 管理页使用当前范围显示部门，不推断作者或历史审核范围。 */
function managedScopeLabel(item) {
  if (item.scope === 'global') return '全公司'
  const id = item.department_id
  return departmentNames.value[id] || (Number(id) === Number(userStore.departmentId) ? userStore.departmentName || '本部门' : `部门 ${id}`)
}

const managedScopeOptions = computed(() => {
  const ids = new Set(Object.keys(departmentNames.value).map(Number))
  if (managedDetail.value?.department_id != null) ids.add(Number(managedDetail.value.department_id))
  return [
    { value: 'global', label: '全公司' },
    ...[...ids].sort((a, b) => a - b).map((id) => ({ value: `department:${id}`, label: departmentNames.value[id] || `部门 ${id}` }))
  ]
})
const canSaveManagedScope = computed(() => {
  if (!userStore.isSuperAdmin || !managedDetail.value?.is_published || managedActionBusy.value) return false
  const current = managedDetail.value.scope === 'global' ? 'global' : `department:${managedDetail.value.department_id}`
  return managedScopeChoice.value !== current && managedScopeOptions.value.some((item) => item.value === managedScopeChoice.value)
})

/** 写入后回读管理状态，冲突时丢弃页面里的旧修订号。 */
async function runManagedAction(slug, write, success) {
  if (managedActionBusy.value) return
  managedActionBusy.value = true
  managedActionError.value = ''
  try {
    await write()
    if (selectedManagedSlug.value === slug) managedDetail.value = null
    await Promise.all([fetchManagedMarket(), refresh()])
    if (selectedManagedSlug.value === slug && managedMarketItems.value.some((item) => item.slug === slug)) await openManagedDetail(slug)
    message.success(success)
  } catch (failure) {
    managedActionError.value = failure?.status === 409
      ? '条目状态已变化，已刷新，请重新确认。'
      : errorText(failure)
    if (failure?.status === 409) {
      await fetchManagedMarket()
      if (selectedManagedSlug.value === slug && managedMarketItems.value.some((item) => item.slug === slug)) await openManagedDetail(slug)
    }
  } finally {
    managedActionBusy.value = false
  }
}

/** 下架前说明旧安装和再次上架的后果。 */
function confirmManagedUnpublish() {
  const item = managedDetail.value
  if (!item?.is_published || managedActionBusy.value) return
  Modal.confirm({
    title: `下架 ${item.name || item.slug}？`,
    content: '下架后不再供成员使用，旧安装保留但不可用。作者须重新提交快照并审核通过才能再次上架。',
    okText: '确认下架',
    okType: 'danger',
    cancelText: '取消',
    onOk: () => runManagedAction(item.slug, () => unpublishMarketSkill(item.slug, item.state_revision), '市场技能已下架')
  })
}

/** 超级管理员确认目标受众后才修改当前市场范围。 */
function confirmManagedScope() {
  if (!canSaveManagedScope.value) return
  const item = managedDetail.value
  const choice = managedScopeChoice.value
  const scope = choice === 'global' ? 'global' : 'department'
  const departmentId = scope === 'global' ? null : Number(choice.split(':')[1])
  const target = managedScopeOptions.value.find((option) => option.value === choice)?.label
  Modal.confirm({
    title: `调整 ${item.name || item.slug} 的上架范围？`,
    content: `将当前范围改为${target}？范围外成员的旧安装将暂不可用。`,
    okText: '确认调整',
    cancelText: '取消',
    onOk: () => runManagedAction(item.slug, () => changeMarketSkillScope(item.slug, scope, departmentId, item.state_revision), '市场范围已更新')
  })
}

/** 切换方向并重新加载该方向的市场分页。 */
function selectCategory(category) {
  if (selectedCategory.value === category) return
  selectedCategory.value = category
  fetchMarketPage(1)
}

/** 提交关键词后从市场第一页重新查询。 */
function searchMarket() {
  appliedMarketQuery.value = query.value.trim()
  fetchMarketPage(1)
}

/** 切换页码或每页条数，避免旧页的偏移进入新尺寸。 */
function changeMarketPage(page, size) {
  const sizeChanged = size !== marketPageSize.value
  marketPageSize.value = size
  fetchMarketPage(sizeChanged ? 1 : page)
}

function openRankings() {
  rankingsOpen.value = true
  if (userStore.isSuperAdmin && !rankingDepartmentOptions.value.length) fetchDepartmentNames()
  fetchRankings()
}

/** 仅在榜单指标变化时重新读取对应排名。 */
function selectRankingMetric(metric) {
  if (rankingMetric.value === metric) return
  rankingMetric.value = metric
  fetchRankings()
}

/** 选择公司或部门后使用同一个榜单接口刷新。 */
function selectRankingAudience(audience) {
  if (rankingAudience.value === audience) return
  rankingAudience.value = audience
  fetchRankings()
}

async function fetchRankings() {
  const requestId = ++rankingRequestSequence
  rankingsLoading.value = true
  rankingError.value = ''
  rankings.value = []
  try {
    const result = await listMarketRankings({
      metric: rankingMetric.value,
      scope: rankingScope.value,
      departmentId: rankingScope.value === 'department' && userStore.isSuperAdmin ? rankingDepartmentId.value : undefined,
      limit: 20
    })
    if (requestId === rankingRequestSequence) rankings.value = result?.data || []
  } catch (failure) {
    if (requestId === rankingRequestSequence) rankingError.value = errorText(failure)
  } finally {
    if (requestId === rankingRequestSequence) rankingsLoading.value = false
  }
}

/** 仅替换指定来源的当前页；个人文件来源无需扫描总数。 */
async function fetchLegacySource(source, page = legacyPages.value[source]) {
  const requestId = ++legacyRequestSequence[source]
  const search = appliedPersonalQuery.value
  const limit = source === 'personal' ? personalPageSize : legacyPageSizes.value[source === 'allShared' ? 'shared' : source === 'allBuiltin' ? 'builtin' : source]
  legacyPages.value[source] = page
  legacyPageLoading.value[source] = true
  try {
    const response = await listSkillCards({
      source: source === 'personal' ? 'personal' : 'shared',
      sharedKind: source === 'personal' ? undefined : source === 'allShared' ? 'shared' : source === 'allBuiltin' ? 'builtin' : source,
      catalogAll: source === 'allShared' || source === 'allBuiltin',
      limit,
      offset: (page - 1) * limit,
      search
    })
    if (requestId !== legacyRequestSequence[source] || search !== appliedPersonalQuery.value) return
    const items = response?.data || []
    const total = Number(response?.total || 0)
    if (page > 1 && !items.length) {
      const fallback = source === 'personal' ? page - 1 : Math.max(1, Math.min(page - 1, Math.ceil(total / limit)))
      await fetchLegacySource(source, fallback)
      return
    }
    if (source === 'personal') {
      legacyPersonalCards.value = items
      legacyHasMore.value.personal = Boolean(response?.has_more)
      missingPersonalSlugs.value = new Set()
    } else {
      if (source === 'shared') legacySharedCards.value = items
      else if (source === 'allShared') legacyAllSharedCards.value = items
      else if (source === 'allBuiltin') legacyAllBuiltinCards.value = items
      else legacyBuiltinCards.value = items
      legacyTotals.value[source] = total
    }
  } catch (failure) {
    if (requestId === legacyRequestSequence[source]) personalError.value = errorText(failure)
  } finally {
    if (requestId === legacyRequestSequence[source]) legacyPageLoading.value[source] = false
  }
}

async function fetchLegacy() {
  const refreshId = ++legacyRefreshSequence
  legacyLoading.value = true
  personalError.value = ''
  try {
    await Promise.all(['personal', 'shared', 'allShared', 'builtin', 'allBuiltin'].map((source) => fetchLegacySource(source)))
  } finally {
    if (refreshId === legacyRefreshSequence) legacyLoading.value = false
  }
}

/** 页码变化只重新请求对应来源，改变每页数量时回到第一页。 */
function changeLegacyPage(source, page, size) {
  const sizeKey = source === 'allShared' ? 'shared' : source === 'allBuiltin' ? 'builtin' : source
  if (source !== 'personal' && size !== legacyPageSizes.value[sizeKey]) {
    legacyPageSizes.value[sizeKey] = size
    page = 1
    if (source === 'allShared') legacyPages.value.shared = 1
    if (source === 'shared') legacyPages.value.allShared = 1
    if (source === 'allBuiltin') legacyPages.value.builtin = 1
    if (source === 'builtin') legacyPages.value.allBuiltin = 1
  }
  fetchLegacySource(source, page)
}

async function fetchInstalled(page = installedPage.value) {
  const requestId = ++installedRequestSequence
  const search = appliedPersonalQuery.value
  installedPage.value = page
  installedLoading.value = true
  try {
    while (true) {
      const response = await listMarketInstalls({
        limit: installedPageSize.value,
        offset: (page - 1) * installedPageSize.value,
        search
      })
      if (requestId !== installedRequestSequence || search !== appliedPersonalQuery.value) return
      const items = response?.data || []
      const total = Number(response?.total || 0)
      if (page > 1 && !items.length) {
        page = Math.max(1, Math.min(page - 1, Math.ceil(total / installedPageSize.value)))
        installedPage.value = page
        continue
      }
      installedCards.value = items
      installedTotal.value = total
      return
    }
  } catch (failure) {
    if (requestId === installedRequestSequence && search === appliedPersonalQuery.value) throw failure
  } finally {
    if (requestId === installedRequestSequence) installedLoading.value = false
  }
}

/** “全部”单独分页本人安装，在服务端先折叠跨页存在的个人原件。 */
async function fetchAllInstalled(page = allInstalledPage.value) {
  const requestId = ++allInstalledRequestSequence
  const search = appliedPersonalQuery.value
  allInstalledPage.value = page
  allInstalledLoading.value = true
  try {
    while (true) {
      const response = await listMarketInstalls({
        limit: installedPageSize.value,
        offset: (page - 1) * installedPageSize.value,
        search,
        catalogAll: true
      })
      if (requestId !== allInstalledRequestSequence || search !== appliedPersonalQuery.value) return
      const items = response?.data || []
      const total = Number(response?.total || 0)
      if (page > 1 && !items.length) {
        page = Math.max(1, Math.min(page - 1, Math.ceil(total / installedPageSize.value)))
        allInstalledPage.value = page
        continue
      }
      allInstalledCards.value = items
      allInstalledTotal.value = total
      return
    }
  } catch (failure) {
    if (requestId === allInstalledRequestSequence && search === appliedPersonalQuery.value) throw failure
  } finally {
    if (requestId === allInstalledRequestSequence) allInstalledLoading.value = false
  }
}

function changeInstalledPage(page, size) {
  if (size !== installedPageSize.value) {
    installedPageSize.value = size
    page = 1
    installedPage.value = 1
    allInstalledPage.value = 1
  }
  const request = personalSource.value === 'all' ? fetchAllInstalled(page) : fetchInstalled(page)
  request.catch((failure) => { personalError.value = errorText(failure) })
}

/** 来源切换重新从各来源第一页查询，避免“全部”的旧页被误当总数。 */
function resetPersonalPages(source = 'all') {
  if (source === 'all' || source === 'created' || source === 'installed') legacyPages.value.personal = 1
  if (source === 'all') legacyPages.value.allShared = 1
  if (source === 'all' || source === 'shared') legacyPages.value.shared = 1
  if (source === 'all') legacyPages.value.allBuiltin = 1
  if (source === 'all' || source === 'system') legacyPages.value.builtin = 1
  if (source === 'all' || source === 'installed') installedPage.value = 1
  if (source === 'all') allInstalledPage.value = 1
}

function selectPersonalSource(source) {
  if (personalSource.value === source) return
  personalSource.value = source
  resetPersonalPages(source)
  personalError.value = ''
  ++legacyRefreshSequence
  legacyLoading.value = false
  const needed = source === 'all' ? ['personal', 'shared', 'allShared', 'builtin', 'allBuiltin']
    : source === 'installed' ? ['personal']
      : [source === 'created' ? 'personal' : source === 'system' ? 'builtin' : 'shared']
  for (const candidate of ['personal', 'shared', 'allShared', 'builtin', 'allBuiltin']) {
    if (needed.includes(candidate)) continue
    ++legacyRequestSequence[candidate]
    legacyPageLoading.value[candidate] = false
  }
  if (source === 'all') fetchLegacy()
  else needed.forEach((candidate) => fetchLegacySource(candidate))
  if (source === 'all' || source === 'installed') {
    fetchInstalled().catch((failure) => { personalError.value = errorText(failure) })
    if (source === 'all') fetchAllInstalled().catch((failure) => { personalError.value = errorText(failure) })
    else {
      ++allInstalledRequestSequence
      allInstalledLoading.value = false
    }
  } else {
    ++installedRequestSequence
    installedLoading.value = false
    ++allInstalledRequestSequence
    allInstalledLoading.value = false
  }
}

async function fetchRequests() {
  const sequence = ++requestsSequence
  requestsLoading.value = true
  try {
    const page = (await listMarketRequests({ status: requestStatus.value === 'all' ? undefined : requestStatus.value,
      offset: (requestsPage.value - 1) * requestsPageSize, limit: requestsPageSize }))?.data
    if (sequence !== requestsSequence) return
    requests.value = page.items
    requestsTotal.value = page.total
    if (requestStatus.value === 'pending') pendingRequestCount.value = page.total
    else pendingRequestCount.value = (await listMarketRequests({ status: 'pending', limit: 1 }))?.data?.total || 0
    if (!page.items.length && page.total && requestsPage.value > 1) {
      requestsPage.value = Math.ceil(page.total / requestsPageSize)
      return fetchRequests()
    }
  } catch (failure) {
    if (sequence === requestsSequence) message.error(errorText(failure))
  } finally {
    if (sequence === requestsSequence) requestsLoading.value = false
  }
}

function changeRequestStatus() {
  requestsPage.value = 1
  fetchRequests()
}

function changeRequestsPage(page) {
  requestsPage.value = page
  fetchRequests()
}

async function fetchDepartmentNames() {
  if (!userStore.isAdmin || departmentLoading.value) return
  departmentLoading.value = true
  departmentError.value = ''
  try {
    const result = await departmentApi.getDepartments()
    departmentNames.value = Object.fromEntries((result?.departments || result || []).map((item) => [item.id, item.name]))
    if (userStore.isSuperAdmin && !rankingDepartmentOptions.value.some((item) => item.value === rankingDepartmentId.value)) {
      rankingDepartmentId.value = rankingDepartmentOptions.value[0]?.value
    }
    if (rankingsOpen.value && rankingScope.value === 'department') fetchRankings()
  } catch (failure) {
    departmentError.value = errorText(failure)
  } finally {
    departmentLoading.value = false
  }
}

async function fetchMarketPage(page) {
  const requestId = ++requestSequence
  marketPage.value = page
  loading.value = true
  marketError.value = ''
  cards.value = []
  try {
    const response = await listMarketSkills({
      query: appliedMarketQuery.value,
      category: selectedCategory.value,
      offset: (page - 1) * marketPageSize.value,
      limit: marketPageSize.value
    })
    if (requestId !== requestSequence) return
    const items = response?.data || []
    const total = response?.total || 0
    if (page > 1 && !items.length) {
      await fetchMarketPage(Math.max(1, Math.min(page - 1, Math.ceil(total / marketPageSize.value))))
      return
    }
    cards.value = items
    marketTotal.value = total
  } catch (failure) {
    if (requestId === requestSequence) marketError.value = errorText(failure)
  } finally {
    if (requestId === requestSequence) loading.value = false
  }
}

async function refresh() {
  personalError.value = ''
  appliedMarketQuery.value = query.value.trim()
  fetchSkillSummary()
  await Promise.all([
    fetchMarketPage(1),
    fetchLegacy(),
    fetchInstalled().catch((failure) => { personalError.value = errorText(failure) }),
    fetchAllInstalled().catch((failure) => { personalError.value = errorText(failure) })
  ])
}

/** 总量单独查询，避免个人技能翻页时反复扫描完整目录。 */
async function fetchSkillSummary() {
  const requestId = ++skillSummaryRequestSequence
  const search = appliedPersonalQuery.value
  skillSummary.value = null
  skillSummaryLoading.value = true
  skillSummaryError.value = ''
  try {
    const result = await getPersonalSkillSummary({ search })
    if (requestId === skillSummaryRequestSequence && search === appliedPersonalQuery.value) {
      skillSummary.value = result?.data || null
    }
  } catch (failure) {
    if (requestId === skillSummaryRequestSequence) skillSummaryError.value = errorText(failure)
  } finally {
    if (requestId === skillSummaryRequestSequence) skillSummaryLoading.value = false
  }
}

async function refreshHome() {
  await Promise.all([refresh(), fetchRequests()])
}

async function openDetail(slug) {
  const requestId = ++detailRequestSequence
  ++fileRequestSequence
  detailOpen.value = true
  detailLoading.value = true
  detail.value = null
  files.value = []
  selectedFile.value = ''
  fileContent.value = ''
  fileLoading.value = false
  fileError.value = ''
  try {
    const [info, tree] = await Promise.all([getMarketSkill(slug), getMarketSkillTree(slug)])
    if (requestId !== detailRequestSequence) return
    detail.value = marketCardWithSharedStatus(info.data)
    files.value = flattenFiles(tree.data || [])
    await selectFile('SKILL.md')
  } catch (failure) {
    if (requestId === detailRequestSequence) message.error(errorText(failure))
  } finally {
    if (requestId === detailRequestSequence) detailLoading.value = false
  }
}

async function selectFile(path) {
  const requestId = ++fileRequestSequence
  selectedFile.value = path
  fileContent.value = ''
  fileError.value = ''
  if (files.value.find((file) => file.path === path)?.previewable === false) {
    fileLoading.value = false
    fileError.value = '此文件不支持在线预览'
    return
  }
  fileLoading.value = true
  try {
    const content = (await getMarketSkillFile(detail.value.slug, path))?.data?.content || ''
    if (requestId === fileRequestSequence) fileContent.value = content
  } catch (failure) {
    if (requestId === fileRequestSequence) {
      fileContent.value = ''
      fileError.value = failure?.status === 400 ? '无法在线预览此文件' : errorText(failure)
    }
  } finally {
    if (requestId === fileRequestSequence) fileLoading.value = false
  }
}

async function act(item) {
  if (!item.installed_version && item.install_conflict === 'personal') {
    return openPersonalItem({ slug: item.slug, source_scope: 'personal' })
  }
  if (!item.installed_version && ownPersonalCandidate(item)) {
    const personal = ownPersonalSkillFor(item)
    if (personal) return openPersonalItem(personal)
    busySlug.value = item.slug
    try {
      const resolved = (await getPersonalSkillDetail(item.own_personal_slug))?.data
      if (resolved) return openPersonalItem({ ...resolved, kind: 'personal' })
    } catch (failure) {
      if (failure?.status === 404) {
        missingPersonalSlugs.value.add(item.own_personal_slug)
        message.warning(item.install_conflict === 'reserved' ? '个人原件已不存在，市场标识仍被占用，暂无法安装。' : '个人原件已不存在，可以安装市场版本。')
      } else message.error(errorText(failure))
      return
    } finally {
      busySlug.value = ''
    }
    message.error('个人技能读取失败，请重试。')
    return
  }
  if (!item.installed_version && item.install_conflict === 'reserved') {
    message.warning('该技能标识已被占用，当前不能安装市场版本。')
    return
  }
  if (item.installed_version && !item.upgrade_available) return openDetail(item.slug)
  busySlug.value = item.slug
  try {
    const result = item.installed_version ? await upgradeMarketSkill(item.slug) : await installMarketSkill(item.slug)
    cards.value = cards.value.map((card) => card.slug === item.slug ? result.data : card)
    installedCards.value = installedCards.value.filter((card) => card.slug !== item.slug).concat(result.data)
    resetPersonalPages()
    fetchLegacy()
    fetchInstalled(1).catch(() => message.warning('已安装，个人列表刷新失败，请稍后重试'))
    fetchAllInstalled(1).catch(() => message.warning('已安装，个人列表刷新失败，请稍后重试'))
    fetchSkillSummary()
    if (rankingsOpen.value) fetchRankings()
    if (detail.value?.slug === item.slug) detail.value = marketCardWithSharedStatus(result.data)
    message.success(item.installed_version ? '已升级到最新审核版本' : '已安装，可在 Agent 中使用')
  } catch (failure) {
    message.error(errorText(failure))
  } finally {
    busySlug.value = ''
  }
}

/** 重新上架后只由安装者明确确认当前审核版本。 */
function confirmUpgrade(item) {
  if (item?.unavailable_reason !== 'needs_confirmation' || busySlug.value) return
  Modal.confirm({
    title: `确认升级 ${item.slug}？`,
    content: '此市场技能已重新上架。确认新版本后，旧安装才会在新的 Agent 任务中恢复可用。',
    okText: '确认升级',
    cancelText: '取消',
    async onOk() {
      busySlug.value = item.slug
      try {
        await upgradeMarketSkill(item.slug)
        await refresh()
        message.success('已确认并升级到当前审核版本')
      } catch (failure) {
        await refresh()
        message.error(errorText(failure))
        throw failure
      } finally {
        busySlug.value = ''
      }
    }
  })
}

/** 仅卸载当前用户的市场安装，个人原件和发布版本均保留。 */
function confirmUninstall(item) {
  if (!item?.installed_version || busySlug.value) return
  Modal.confirm({
    title: `卸载 ${item.name || item.slug}？`,
    content: '只移除你安装的市场版本；市场发布和其他人的安装不受影响。固定选用此版本的 Agent 需要重新配置，正在使用该技能的任务可能受影响。',
    okText: '卸载',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      busySlug.value = item.slug
      try {
        await uninstallMarketSkill(item.slug)
        installedCards.value = installedCards.value.filter((card) => card.slug !== item.slug)
        cards.value = cards.value.map((card) => card.slug === item.slug ? { ...card, installed_version: null, upgrade_available: false } : card)
        detailOpen.value = false
        detail.value = null
        if (rankingsOpen.value) fetchRankings()
        await refresh()
        message.success('市场技能已卸载')
      } catch (failure) {
        message.error(errorText(failure))
        throw failure
      } finally {
        busySlug.value = ''
      }
    }
  })
}

async function like(item) {
  busySlug.value = item.slug
  try {
    const result = await toggleMarketSkillLike(item.slug)
    cards.value = cards.value.map((card) => card.slug === item.slug ? result.data : card)
    if (rankingsOpen.value) fetchRankings()
    if (detail.value?.slug === item.slug) detail.value = marketCardWithSharedStatus(result.data)
  } catch (failure) {
    message.error(errorText(failure))
  } finally {
    busySlug.value = ''
  }
}

async function openRequests() {
  requestsOpen.value = true
  await fetchRequests()
}

async function previewRequest(request) {
  const requestId = ++snapshotRequestSequence
  ++snapshotFileRequestSequence
  snapshotOpen.value = true
  snapshotLoading.value = true
  snapshotRequestId.value = request.id
  snapshotFiles.value = []
  snapshotSelected.value = ''
  snapshotContent.value = ''
  snapshotError.value = ''
  try {
    const files = flattenFiles((await getSkillShareSnapshotTree(request.id))?.data || [])
    if (requestId !== snapshotRequestSequence) return
    snapshotFiles.value = files
    await selectSnapshotFile('SKILL.md')
  } catch (failure) {
    if (requestId === snapshotRequestSequence) {
      snapshotLoading.value = false
      message.error(errorText(failure))
    }
  }
}

async function selectSnapshotFile(path) {
  const requestId = ++snapshotFileRequestSequence
  snapshotSelected.value = path
  snapshotContent.value = ''
  snapshotError.value = ''
  if (snapshotFiles.value.find((file) => file.path === path)?.previewable === false) {
    snapshotLoading.value = false
    snapshotError.value = '此文件不支持在线预览，请下载完整快照检查'
    return
  }
  snapshotLoading.value = true
  try {
    const content = (await getSkillShareSnapshot(snapshotRequestId.value, path))?.data?.content || ''
    if (requestId === snapshotFileRequestSequence) snapshotContent.value = content
  } catch {
    if (requestId === snapshotFileRequestSequence) {
      snapshotContent.value = ''
      snapshotError.value = '此文件无法在线预览，请下载完整快照检查'
    }
  } finally {
    if (requestId === snapshotFileRequestSequence) snapshotLoading.value = false
  }
}

async function downloadSnapshot() {
  if (!snapshotRequestId.value) return
  try {
    const response = await exportSkillShareSnapshot(snapshotRequestId.value)
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = `skill-market-${snapshotRequestId.value}.zip`
    link.click()
    URL.revokeObjectURL(url)
  } catch (failure) {
    message.error(errorText(failure))
  }
}

function openReview(request, approved) {
  if (approved && approvalBlockReason(request)) return
  reviewRequest.value = request
  reviewApproved.value = approved
  reviewScope.value = request.existing_market_scope || 'department'
  reviewNote.value = ''
  reviewOpen.value = true
}

async function review() {
  reviewLoading.value = true
  try {
    const scope = reviewRequest.value.existing_market_scope || reviewScope.value
    const result = await reviewMarketSkill(reviewRequest.value.id, reviewApproved.value, scope, reviewNote.value)
    reviewOpen.value = false
    const publishedDepartmentId = result.data.read_scope?.department_ids?.[0] ?? result.data.department_ids?.[0]
    const outsideDepartment = reviewApproved.value && scope === 'department' && !userStore.isSuperAdmin && publishedDepartmentId != null && Number(userStore.departmentId) !== Number(publishedDepartmentId)
    if (reviewApproved.value && !outsideDepartment) {
      query.value = ''
      selectView('market')
    }
    await Promise.all([openRequests(), refresh()])
    if (outsideDepartment) message.info(`已发布到${departmentNames.value[publishedDepartmentId] || `部门 ${publishedDepartmentId}`}；当前账号不在该部门，市场目录不会展示此技能`)
    else message.success(reviewApproved.value ? '已发布到市场，可在技能目录查看' : '已拒绝申请')
  } catch (failure) {
    message.error(errorText(failure))
  } finally {
    reviewLoading.value = false
  }
}

watch(managementOpen, (open) => { if (!open) refresh() })
watch(personalQuery, () => {
  clearTimeout(personalSearchTimer)
  personalSearchTimer = setTimeout(() => {
    appliedPersonalQuery.value = personalQuery.value.trim()
    resetPersonalPages()
    fetchSkillSummary()
    fetchLegacy()
    fetchInstalled().catch((failure) => { personalError.value = errorText(failure) })
    fetchAllInstalled().catch((failure) => { personalError.value = errorText(failure) })
  }, 220)
})
watch(() => route.query.view, (view, previousView) => {
  activeView.value = view === 'market' ? 'market' : 'personal'
  if (view !== previousView) {
    managementOpen.value = false
    releaseView.value = false
  }
})
onMounted(() => { refresh(); fetchRequests(); fetchDepartmentNames() })
onUnmounted(() => { clearTimeout(personalSearchTimer); ++skillSummaryRequestSequence })
defineExpose({ loading: computed(() => activeView.value === 'market' ? loading.value : legacyLoading.value || installedLoading.value || allInstalledLoading.value) })
</script>

<style scoped lang="less">
.skill-market { min-height: 100%; color: var(--color-text); }
.skill-view-tabs { display: flex; gap: 30px; margin: 0 var(--page-padding); border-bottom: 1px solid var(--gray-150); }
.skill-view-tabs button { margin-bottom: -1px; padding: 19px 3px 14px; border: 0; border-bottom: 3px solid transparent; background: none; color: var(--color-text-secondary); cursor: pointer; font-size: 17px; }
.skill-view-tabs button[aria-selected="true"] { border-bottom-color: var(--main-500); color: var(--main-600); font-weight: 700; }
.skill-view-tabs button:hover { color: var(--main-600); }
.skill-view-content { max-width: 1440px; margin: 0 auto; padding: 31px var(--page-padding) 60px; }
.skill-page-heading, .skill-page-title, .skill-page-actions, .market-toolbar { display: flex; align-items: center; gap: 14px; }
.skill-page-heading { justify-content: space-between; flex-wrap: wrap; margin-bottom: 25px; }
.skill-page-title { min-width: 0; }
.skill-page-icon { display: grid; flex: none; place-items: center; width: 54px; height: 54px; border-radius: 16px; background: var(--main-50); color: var(--main-600); }
.skill-page-title h2 { margin: 0 0 5px; font-size: 29px; line-height: 1.2; font-weight: 700; }
.skill-page-title p { margin: 0; color: var(--color-text-secondary); font-size: 13px; }
.skill-page-actions { justify-content: flex-end; flex-wrap: wrap; }
.market-toolbar { justify-content: space-between; flex-wrap: wrap; margin-bottom: 20px; }
.market-search { width: min(390px, 100%); flex: 1 1 230px; }
.market-result-count { color: var(--color-text-tertiary); font-size: 12px; }
.market-rankings-button { display: inline-flex; align-items: center; gap: 7px; }
.skill-overview { display: flex; flex-wrap: wrap; gap: 0; margin-bottom: 25px; border-block: 1px solid var(--gray-150); }
.skill-overview > div { display: flex; align-items: baseline; gap: 7px; padding: 17px 25px 17px 0; margin-right: 25px; color: var(--color-text-secondary); font-size: 13px; }
.skill-overview > div:not(:last-child) { border-right: 1px solid var(--gray-150); }
.skill-overview strong { color: var(--color-text); font-size: 23px; line-height: 1; }
.skill-list-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 18px; }
.skill-list-heading h3 { margin: 0 0 3px; font-size: 19px; }
.skill-list-heading p { margin: 0; color: var(--color-text-secondary); font-size: 12px; }
.skill-list-toolbar { display: flex; justify-content: space-between; align-items: flex-end; gap: 15px; flex-wrap: wrap; margin-bottom: 15px; }
.personal-source-filters { display: flex; gap: 16px; overflow-x: auto; max-width: 100%; border-bottom: 1px solid var(--gray-150); }
.personal-source-filters button { display: inline-flex; flex: none; align-items: center; gap: 5px; padding: 9px 2px; border: 0; border-bottom: 2px solid transparent; background: none; color: var(--color-text-secondary); cursor: pointer; font-size: 13px; }
.personal-source-filters button.active { border-bottom-color: var(--main-500); color: var(--main-600); font-weight: 650; }
.personal-source-filters button span { color: var(--color-text-tertiary); font-size: 11px; }
.personal-search { width: min(300px, 100%); }
.skill-list { overflow: hidden; border: 1px solid var(--gray-150); border-radius: 12px; background: var(--color-bg-container); }
/* 每行独立布局，操作列使用固定轨道，避免按钮数量改变其余列的位置。 */
.skill-list-header, .skill-row { display: grid; grid-template-columns: minmax(190px, 2fr) minmax(110px, 1fr) minmax(130px, 1fr) 240px; align-items: center; column-gap: 14px; }
.skill-list-header { padding: 12px 20px; background: var(--gray-0); color: var(--color-text-tertiary); font-size: 12px; }
.skill-list-header span:last-child { text-align: right; }
.skill-row { min-height: 76px; padding: 13px 20px; border-top: 1px solid var(--gray-150); }
.skill-row:first-child { border-top: 0; }
.skill-row-name { display: flex; align-items: center; gap: 12px; min-width: 0; }
.skill-row-name > span:last-child { min-width: 0; }
.skill-row-name button { display: block; overflow: hidden; max-width: 100%; padding: 0; border: 0; background: none; color: var(--color-text); text-align: left; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; font-size: 14px; font-weight: 650; }
.skill-row-name button:hover { color: var(--main-600); }
.skill-row-name strong { font-size: 14px; }
.skill-row-name small, .skill-row-origin small { display: block; overflow: hidden; max-width: 100%; margin-top: 3px; color: var(--color-text-tertiary); text-overflow: ellipsis; white-space: nowrap; font-size: 11px; }
.skill-row-icon { display: grid; flex: none; place-items: center; width: 39px; height: 39px; border-radius: 10px; background: var(--main-50); color: var(--main-600); }
.skill-row-origin, .skill-row-status { color: var(--color-text-secondary); font-size: 12px; }
.skill-row-status.is-unavailable { color: var(--color-warning-700); }
.skill-row-actions { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 6px; }
.personal-pages { display: grid; gap: 10px; justify-content: end; margin-top: 18px; }
.personal-page-group { display: flex; align-items: center; justify-content: flex-end; flex-wrap: wrap; gap: 8px; color: var(--color-text-secondary); font-size: 12px; }
.skill-list-hint { margin: 13px 0 0; color: var(--color-text-tertiary); font-size: 12px; }
.ranking-controls { margin-bottom: 10px; }
.ranking-metric-tabs { display: flex; gap: 16px; border-bottom: 1px solid var(--gray-150); }
.ranking-metric-tabs button { padding: 7px 2px 10px; border: 0; border-bottom: 2px solid transparent; background: none; color: var(--color-text-secondary); cursor: pointer; font-size: 14px; }
.ranking-metric-tabs button:hover, .ranking-metric-tabs button.active { color: var(--main-600); }
.ranking-metric-tabs button.active { border-bottom-color: var(--main-500); font-weight: 600; }
.ranking-metric-tabs button:focus-visible { outline: 2px solid var(--main-500); outline-offset: -2px; }
.ranking-audience { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 18px; color: var(--color-text-secondary); font-size: 13px; }
.ranking-audience :deep(.ant-select) { min-width: 180px; }
.ranking-hint { margin-bottom: 18px; color: var(--color-text-secondary); font-size: 12px; }
.ranking-list { display: grid; gap: 8px; margin: 0; padding: 0; list-style: none; }
.ranking-list li { display: flex; align-items: center; gap: 10px; min-width: 0; padding: 11px; border: 1px solid var(--gray-150); border-radius: 9px; }
.ranking-position { display: grid; flex: none; place-items: center; width: 30px; height: 30px; border-radius: 8px; background: var(--main-50); color: var(--main-600); font-weight: 700; }
.ranking-copy { display: grid; gap: 2px; min-width: 0; flex: 1; }
.ranking-copy strong, .ranking-copy small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ranking-copy small { color: var(--color-text-tertiary); font-size: 11px; }
.ranking-count { flex: none; color: var(--color-text-secondary); font-size: 12px; }
.market-categories { display: flex; gap: 8px; overflow-x: auto; padding: 0 0 14px; margin: 0 0 17px; scrollbar-width: thin; }
.market-categories button { flex: none; padding: 5px 11px; border: 1px solid var(--gray-150); border-radius: 999px; background: var(--color-bg-container); color: var(--color-text-secondary); cursor: pointer; font-size: 12px; }
.market-categories button:hover { border-color: var(--main-400); color: var(--main-600); }
.market-categories button.active { border-color: var(--main-500); background: var(--main-50); color: var(--main-600); font-weight: 600; }
.market-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(235px, 1fr)); gap: 14px; }
.market-pages { display: flex; justify-content: center; margin-top: 22px; }
.market-card { min-height: 235px; display: flex; flex-direction: column; padding: 18px; background: var(--color-bg-container); border: 1px solid var(--gray-150); border-radius: 12px; transition: border-color .2s, background-color .2s; }
.market-card:hover { border-color: var(--main-400); background: var(--gray-0); }
.market-card-top, .market-card-bottom, .market-detail-heading, .market-detail-actions, .market-request-title, .market-request-actions { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.market-icon { width: 38px; height: 38px; display: grid; place-items: center; border-radius: 10px; color: var(--main-600); background: var(--main-50); }
.market-card-labels { display: grid; justify-items: end; gap: 3px; min-width: 0; }
.market-category-label { color: var(--main-600); font-size: 12px; font-weight: 600; }
.market-scope { font-size: 12px; color: var(--color-text-secondary); }
.market-card-title { display: block; margin: 14px 0 1px; padding: 0; border: 0; background: none; color: inherit; text-align: left; font-size: 16px; font-weight: 650; cursor: pointer; }
.market-card-title:hover { color: var(--main-600); }
.market-card-slug, .market-card-author, .market-request-meta { color: var(--color-text-tertiary); font-size: 12px; }
.market-card p { margin: 11px 0; color: var(--color-text-secondary); line-height: 1.55; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; overflow: hidden; font-size: 13px; }
.market-card-author { margin-top: auto; }
.market-shared-note { margin-top: 5px; color: var(--main-600); font-size: 12px; }
.market-card-bottom { flex-wrap: wrap; border-top: 1px solid var(--gray-100); margin-top: 13px; padding-top: 11px; }
.market-card-bottom :deep(.ant-btn) { margin-left: auto; }
.market-like { display: flex; flex: none; align-items: center; gap: 5px; border: 0; background: none; color: var(--color-text-secondary); cursor: pointer; }
.market-like.liked { color: var(--color-error-500); }
.market-install-count { display: flex; flex: none; align-items: center; gap: 5px; color: var(--color-text-secondary); font-size: 12px; white-space: nowrap; }
.market-available { flex: none; color: var(--color-text-tertiary); font-size: 12px; white-space: nowrap; }
.market-empty { display: grid; justify-items: center; gap: 8px; padding: 80px 16px; color: var(--color-text-secondary); }
.market-empty strong { color: var(--color-text); }
.market-error { margin-bottom: 16px; }
.personal-empty { padding: 32px 12px; text-align: center; color: var(--color-text-secondary); font-size: 13px; }
.market-categories button:focus-visible, .skill-view-tabs button:focus-visible, .personal-source-filters button:focus-visible, .skill-row-name button:focus-visible, .market-card-title:focus-visible { outline: 2px solid var(--main-500); outline-offset: 2px; }
.market-detail-heading h3 { margin: 0 0 3px; font-size: 22px; }
.market-detail-heading span, .market-detail-meta { color: var(--color-text-secondary); font-size: 13px; }
.market-detail > p { margin: 18px 0; line-height: 1.6; }
.market-detail-meta, .market-dependencies { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin: 16px 0; }
.market-files { display: grid; grid-template-columns: 180px minmax(0, 1fr); grid-template-rows: minmax(0, 1fr); height: min(54vh, 400px); min-height: 260px; overflow: hidden; border: 1px solid var(--gray-150); border-radius: 10px; margin-top: 18px; }
.snapshot-files { height: min(58vh, 520px); }
.market-files-list { min-height: 0; overflow: auto; display: flex; flex-direction: column; padding: 10px; border-right: 1px solid var(--gray-150); }
.market-files-list strong { padding: 7px; }
.market-files-list button { border: 0; background: none; text-align: left; padding: 7px; border-radius: 6px; color: var(--color-text-secondary); cursor: pointer; overflow-wrap: anywhere; }
.market-files-list button.active { color: var(--color-primary-700); background: var(--color-primary-50); }
.market-file-unpreviewable { color: var(--color-text-tertiary); font-size: 12px; }
.market-file-content { min-height: 0; overflow: auto; padding: 16px; }
.market-code-preview { width: max-content; min-width: 100%; margin: 0; white-space: pre; tab-size: 4; font: 13px/1.6 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
.market-detail-actions { justify-content: flex-end; margin-top: 20px; }
.market-request { padding: 16px 0; border-bottom: 1px solid var(--gray-150); }
.market-request-toolbar, .market-request-pages { display: flex; align-items: center; gap: 10px; margin: 10px 0; }
.market-request-toolbar span:last-child { margin-left: auto; color: var(--color-text-tertiary); }
.market-request-toolbar :deep(.ant-select) { min-width: 120px; }
.market-request-pages { justify-content: center; padding-top: 12px; }
.market-request p { margin: 8px 0; color: var(--color-text-secondary); }
.market-request-actions { justify-content: flex-end; margin-top: 10px; }
.market-request-review-reason { color: var(--color-warning-900); font-size: 12px; }
.market-review-scope { display: block; margin: 18px 0; }
.managed-market-intro { margin: 0 0 16px; color: var(--color-text-secondary); font-size: 13px; line-height: 1.6; }
.managed-market-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 16px; }
.managed-market-search { flex: 1 1 220px; max-width: 340px; }
.managed-market-filters { display: flex; flex-wrap: wrap; gap: 10px; }
.managed-market-filters :deep(.ant-select) { min-width: 120px; }
.managed-market-count { margin-left: auto; color: var(--color-text-tertiary); font-size: 12px; white-space: nowrap; }
.managed-market-pages { display: flex; justify-content: flex-end; margin-top: 16px; }
.managed-market-layout { display: grid; grid-template-columns: minmax(250px, 36%) minmax(0, 1fr); min-height: 300px; border: 1px solid var(--gray-150); border-radius: 8px; overflow: hidden; }
.managed-market-list { max-height: 65vh; overflow: auto; border-right: 1px solid var(--gray-150); }
.managed-market-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 8px; padding: 13px 14px; border-bottom: 1px solid var(--gray-150); }
.managed-market-row:last-child { border-bottom: 0; }
.managed-market-row.selected { background: var(--main-10); }
.managed-market-row-copy { min-width: 0; }
.managed-market-row-copy strong, .managed-market-row-copy small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.managed-market-row-copy strong { color: var(--color-text); font-size: 13px; }
.managed-market-row-copy small { margin-top: 4px; color: var(--color-text-tertiary); font-size: 11px; }
.managed-market-status { display: inline-block; width: fit-content; padding: 2px 8px; border-radius: 999px; background: var(--color-success-50); color: var(--color-success-700); font-size: 12px; white-space: nowrap; }
.managed-market-status.withdrawn { background: var(--color-warning-50); color: var(--color-warning-900); }
.managed-market-detail, .managed-market-placeholder { min-width: 0; padding: 20px; }
.managed-market-detail-heading { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 8px; }
.managed-market-detail-heading h3 { margin: 0; overflow-wrap: anywhere; font-size: 18px; }
.managed-market-detail p { margin: 10px 0; color: var(--color-text-secondary); font-size: 13px; line-height: 1.6; }
.managed-market-detail .managed-market-meta { color: var(--color-text-tertiary); overflow-wrap: anywhere; }
.managed-market-detail .managed-market-notice { padding: 12px; border-radius: 6px; background: var(--color-warning-50); color: var(--color-warning-900); }
.managed-market-scope { display: grid; gap: 9px; margin-top: 24px; padding-top: 18px; border-top: 1px solid var(--gray-150); }
.managed-market-scope label { font-size: 13px; font-weight: 600; }
.managed-market-scope :deep(.ant-select) { width: 100%; }
.managed-market-scope span { color: var(--color-text-tertiary); font-size: 12px; line-height: 1.5; }
.managed-market-scope :deep(.ant-btn) { justify-self: start; }
.managed-market-danger { margin-top: 24px; padding-top: 18px; border-top: 1px solid var(--gray-150); }
.managed-market-placeholder, .managed-detail-feedback { color: var(--color-text-secondary); font-size: 13px; }
.managed-detail-feedback { display: flex; align-items: center; gap: 8px; }
@media (max-width: 1100px) { .skill-list-header, .skill-row { grid-template-columns: minmax(180px, 1.5fr) minmax(100px, 1fr) minmax(130px, 1fr); } .skill-list-header span:last-child { display: none; } .skill-row-actions { grid-column: 1 / -1; justify-content: flex-start; padding-left: 51px; } }
@media (max-width: 760px) { .skill-list-header { display: none; } .skill-row { grid-template-columns: minmax(0, 1fr); row-gap: 5px; padding: 14px; } .skill-row-name, .skill-row-origin, .skill-row-status, .skill-row-actions { grid-column: 1; } .skill-row-origin, .skill-row-status, .skill-row-actions { padding-left: 51px; } .skill-row-actions { grid-row: auto; flex-direction: row; justify-content: flex-start; margin-top: 5px; } }
@media (max-width: 680px) { .skill-view-tabs { margin-inline: 18px; } .skill-view-content { padding: 24px 18px 50px; } .skill-page-heading { align-items: stretch; flex-direction: column; } .skill-page-actions { justify-content: flex-start; } .skill-overview > div { padding-right: 14px; margin-right: 14px; } .skill-list-heading { align-items: flex-start; flex-direction: column; } .skill-list-toolbar { align-items: stretch; flex-direction: column-reverse; } .personal-search, .market-search { width: 100%; } .market-files { grid-template-columns: 1fr; grid-template-rows: auto minmax(0, 1fr); } .market-files-list { max-height: 120px; border-right: 0; border-bottom: 1px solid var(--gray-150); } .market-card-bottom { flex-wrap: wrap; } .managed-market-search { max-width: none; flex-basis: 100%; } .managed-market-count { margin-left: 0; } .managed-market-layout { grid-template-columns: 1fr; } .managed-market-list { max-height: 36vh; border-right: 0; border-bottom: 1px solid var(--gray-150); } }
@media (max-width: 480px) { .ranking-audience { align-items: stretch; flex-direction: column; gap: 6px; } .ranking-audience :deep(.ant-select) { width: 100%; } .ranking-list li { display: grid; grid-template-columns: 30px minmax(0, 1fr) auto; } .ranking-position { grid-column: 1; grid-row: 1; } .ranking-copy { grid-column: 2 / 4; grid-row: 1; } .ranking-count { grid-column: 2; grid-row: 2; } .ranking-list li :deep(.ant-btn) { grid-column: 3; grid-row: 2; } }
</style>
