"""个人空间总数与治理安全摘要的兼容。"""

from types import SimpleNamespace

import pytest
from yuxi.services import skill_catalog_summary_service as summary
from yuxi.storage.postgres.models_business import User


@pytest.mark.asyncio
async def test_counts_keep_redacted_own_install_as_separate_uninstall_entry(monkeypatch):
    """隐藏的本人市场安装不含原件字段，也不能从全部视图的数量中消失。"""

    async def cards(_db, _user):
        return [
            SimpleNamespace(
                slug="original", name="我的原件", description="", source_scope="personal", source_type="upload"
            )
        ]

    async def installs(_db, **_kwargs):
        return [
            {"slug": "market-original", "installed_version": 1, "visible": False, "unavailable_reason": "unpublished"}
        ]

    async def own_installs(_db, **_kwargs):
        return {}

    class Repository:
        def __init__(self, _db):
            pass

        async def page_visible(self, _user, **_kwargs):
            return [], 0

    monkeypatch.setattr(summary, "list_skill_cards_for_user", cards)
    monkeypatch.setattr(summary, "list_user_market_installs", installs)
    monkeypatch.setattr(summary, "personal_market_install_summaries", own_installs)
    monkeypatch.setattr(summary, "SkillRepository", Repository)
    viewer = User(uid="viewer", username="viewer", password_hash="x", role="user", department_id=1)
    assert await summary.personal_space_skill_counts(None, viewer) == {"created_total": 1, "all_total": 2}
