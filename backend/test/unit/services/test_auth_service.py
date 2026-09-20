from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.services import auth_service
from yuxi.storage.postgres.models_business import User
from yuxi.utils.auth_utils import AuthUtils


def _make_user(password: str = "current-password") -> User:
    return User(
        id=7,
        username="test-user",
        uid="test-user",
        role="user",
        password_hash=AuthUtils.hash_password(password),
    )


def _install_service_mocks(monkeypatch, *, log_side_effect=None):
    repository = SimpleNamespace(save=AsyncMock())
    log_operation = AsyncMock(side_effect=log_side_effect)
    monkeypatch.setattr(auth_service, "UserRepository", lambda _db: repository)
    monkeypatch.setattr(auth_service, "log_operation", log_operation)
    return repository, log_operation


@pytest.mark.asyncio
async def test_change_current_user_password_verifies_and_commits(monkeypatch):
    user = _make_user()
    original_hash = user.password_hash
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    repository, log_operation = _install_service_mocks(monkeypatch)

    await auth_service.change_current_user_password(
        db,
        user=user,
        current_password="current-password",
        new_password="new-password",
    )

    assert user.password_hash != original_hash
    assert AuthUtils.verify_password(user.password_hash, "new-password")
    repository.save.assert_awaited_once_with(user)
    log_operation.assert_awaited_once_with(db, user.id, "修改本人密码", "用户修改本人登录密码", None)
    db.commit.assert_awaited_once_with()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_change_current_user_password_rejects_wrong_current_password(monkeypatch):
    user = _make_user()
    original_hash = user.password_hash
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    repository, log_operation = _install_service_mocks(monkeypatch)

    with pytest.raises(auth_service.CurrentPasswordMismatchError):
        await auth_service.change_current_user_password(
            db,
            user=user,
            current_password="wrong-password",
            new_password="new-password",
        )

    assert user.password_hash == original_hash
    repository.save.assert_not_awaited()
    log_operation.assert_not_awaited()
    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_change_current_user_password_rejects_password_reuse(monkeypatch):
    user = _make_user()
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    repository, log_operation = _install_service_mocks(monkeypatch)

    with pytest.raises(auth_service.PasswordReuseError):
        await auth_service.change_current_user_password(
            db,
            user=user,
            current_password="current-password",
            new_password="current-password",
        )

    repository.save.assert_not_awaited()
    log_operation.assert_not_awaited()
    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_change_current_user_password_rolls_back_when_audit_fails(monkeypatch):
    user = _make_user()
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    repository, _log_operation = _install_service_mocks(monkeypatch, log_side_effect=RuntimeError("audit failed"))

    with pytest.raises(RuntimeError, match="audit failed"):
        await auth_service.change_current_user_password(
            db,
            user=user,
            current_password="current-password",
            new_password="new-password",
        )

    repository.save.assert_awaited_once_with(user)
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once_with()
