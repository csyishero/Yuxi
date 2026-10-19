from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.routers import auth_router


@pytest.mark.asyncio
async def test_access_option_search_finds_later_user_only_inside_admin_department(monkeypatch) -> None:
    class FakeUserRepository:
        def __init__(self, _db):
            pass

        async def list_page_with_department(self, *, offset, limit, department_id, search):
            assert (offset, limit, department_id, search) == (1000, 51, 3, "late")
            user = SimpleNamespace(uid="late-1001", username="late user", role="user", department_id=3)
            return [(user, "开发部")], 1001

    monkeypatch.setattr(auth_router, "UserRepository", FakeUserRepository)

    options = await auth_router.read_user_access_options(
        skip=1000,
        limit=51,
        search="late",
        current_user=SimpleNamespace(role="admin", department_id=3),
        db=object(),
    )

    assert options == [
        {"uid": "late-1001", "username": "late user", "role": "user", "department_id": 3, "department_name": "开发部"}
    ]


@pytest.mark.asyncio
async def test_access_options_do_not_expand_departmentless_admin_to_company(monkeypatch) -> None:
    class UnexpectedRepository:
        def __init__(self, _db):
            raise AssertionError("departmentless admin must not query all users")

    monkeypatch.setattr(auth_router, "UserRepository", UnexpectedRepository)

    options = await auth_router.read_user_access_options(
        skip=0,
        limit=51,
        search="",
        current_user=SimpleNamespace(role="admin", department_id=None),
        db=object(),
    )

    assert options == []


@pytest.mark.asyncio
async def test_selected_access_options_lookup_preserves_admin_department(monkeypatch) -> None:
    class FakeUserRepository:
        def __init__(self, _db):
            pass

        async def list_with_department_by_uids(self, *, uids, department_id):
            assert (uids, department_id) == (["selected", "outside"], 3)
            return [(SimpleNamespace(uid="selected", username="Selected User", role="user", department_id=3), "开发部")]

    monkeypatch.setattr(auth_router, "UserRepository", FakeUserRepository)

    options = await auth_router.read_user_access_options_by_uids(
        uids=["selected", "outside"],
        current_user=SimpleNamespace(role="admin", department_id=3),
        db=object(),
    )

    assert options == [
        {
            "uid": "selected",
            "username": "Selected User",
            "role": "user",
            "department_id": 3,
            "department_name": "开发部",
        }
    ]


@pytest.mark.asyncio
async def test_selected_access_options_lookup_bounds_uids_and_departmentless_admin(monkeypatch) -> None:
    class UnexpectedRepository:
        def __init__(self, _db):
            raise AssertionError("invalid or departmentless lookups must not reach the repository")

    monkeypatch.setattr(auth_router, "UserRepository", UnexpectedRepository)

    with pytest.raises(HTTPException) as exc_info:
        await auth_router.read_user_access_options_by_uids(
            uids=[f"user-{index}" for index in range(51)],
            current_user=SimpleNamespace(role="superadmin", department_id=None),
            db=object(),
        )
    assert exc_info.value.status_code == 422

    options = await auth_router.read_user_access_options_by_uids(
        uids=["selected"],
        current_user=SimpleNamespace(role="admin", department_id=None),
        db=object(),
    )
    assert options == []
