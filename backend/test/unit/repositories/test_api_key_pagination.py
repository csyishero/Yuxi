"""API Key 列表在相同创建时间下的翻页测试。"""

from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories.api_key_repository import APIKeyRepository
from yuxi.storage.postgres.models_business import APIKey


@pytest.mark.asyncio
async def test_api_key_pages_are_stable_and_preserve_visibility() -> None:
    """同一时间创建的密钥不会在翻页时遗漏，普通用户只看到自己的记录。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(APIKey.__table__.create)
        await conn.execute(
            APIKey.__table__.insert(),
            [
                {
                    "key_hash": f"hash-{index:03d}",
                    "key_prefix": "yxkey_test",
                    "name": f"key-{index}",
                    "user_id": 1 if index < 25 else 2,
                    "created_by": "test",
                    "created_at": datetime(2026, 1, 1),
                    "revoked_at": datetime(2026, 1, 2) if index == 30 else None,
                }
                for index in range(31)
            ],
        )

    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            repository = APIKeyRepository(session)
            first, total = await repository.list_visible(requester_user_id=1, is_superadmin=False, skip=0, limit=20)
            second, next_total = await repository.list_visible(
                requester_user_id=1, is_superadmin=False, skip=20, limit=20
            )
            assert total == next_total == 25
            assert [key.id for key in first] == list(range(25, 5, -1))
            assert [key.id for key in second] == list(range(5, 0, -1))
    finally:
        await engine.dispose()
