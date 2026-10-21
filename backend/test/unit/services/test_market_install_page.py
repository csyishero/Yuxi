"""已安装市场技能分页的权限摘要。"""

from types import SimpleNamespace

import pytest
from yuxi.agents.skills import market
from yuxi.storage.postgres.models_business import User


@pytest.mark.asyncio
async def test_install_page_keeps_hidden_release_private(monkeypatch):
    """后续页仍只向失去可见权限的用户暴露安装标识和版本。"""
    listing = SimpleNamespace(
        slug="hidden-skill",
        is_published=True,
        latest_release_id="release-1",
        scope="department",
        department_id=2,
        owner_uid="another-user",
        personal_slug="source-skill",
    )
    release = SimpleNamespace(name="Internal name", description="Internal details", category="office", version=3)

    class FakeRepository:
        def __init__(self, _db):
            pass

        async def page_installed_releases_for_user(self, uid, *, user, offset, limit, search):
            assert (uid, user.uid, offset, limit, search) == ("viewer", "viewer", 30, 30, "")
            return [(listing, release, SimpleNamespace(confirmed_generation=1))], 31

    monkeypatch.setattr(market, "MarketSkillRepository", FakeRepository)
    viewer = User(uid="viewer", username="viewer", password_hash="x", role="user", department_id=1)

    cards, total = await market.page_user_market_installs(None, user=viewer, offset=30, limit=30)

    assert total == 31
    assert cards == [
        {
            "slug": "hidden-skill",
            "installed_version": 3,
            "visible": False,
            "unavailable_reason": "out_of_scope",
        }
    ]
