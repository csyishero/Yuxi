from types import SimpleNamespace

import pytest

from yuxi.permissions import (
    ALL_KNOWLEDGE_BASE_CAPABILITIES,
    KnowledgeBaseCapability,
    ResourcePermission,
    ResourcePermissionDenied,
    normalize_permission_config,
    require_knowledge_base_capability,
    require_knowledge_base_permission,
    resolve_agent_permission,
    resolve_knowledge_base_capabilities,
    resolve_knowledge_base_permission,
    resolve_skill_permission,
)


def test_knowledge_base_capabilities_use_default_policy_for_existing_configs():
    resource = _resource(
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global"},
            "edit_scope": None,
            "manage_scope": None,
        }
    )

    assert resolve_knowledge_base_capabilities(_user(), resource) == {
        KnowledgeBaseCapability.VIEW,
        KnowledgeBaseCapability.SEARCH,
        KnowledgeBaseCapability.DOWNLOAD,
    }


def test_custom_capability_policy_is_inherited_by_higher_permission_levels():
    resource = _resource(
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global"},
            "edit_scope": {"access_level": "user", "user_uids": ["editor-1"]},
            "manage_scope": None,
            "capability_policy": {
                "read": ["view"],
                "edit": ["upload", "parse"],
                "manage": [],
            },
        }
    )

    editor = _user(uid="editor-1")
    assert resolve_knowledge_base_capabilities(editor, resource) == {
        KnowledgeBaseCapability.VIEW,
        KnowledgeBaseCapability.UPLOAD,
        KnowledgeBaseCapability.PARSE,
    }
    require_knowledge_base_capability(editor, resource, KnowledgeBaseCapability.PARSE)
    with pytest.raises(ResourcePermissionDenied):
        require_knowledge_base_capability(editor, resource, KnowledgeBaseCapability.INDEX)


def test_system_inherited_manager_keeps_all_capabilities_when_policy_is_restricted():
    resource = _resource(
        owner_department_id=7,
        share_config={
            "version": 2,
            "read_scope": None,
            "edit_scope": None,
            "manage_scope": None,
            "capability_policy": {"read": [], "edit": [], "manage": []},
        },
    )

    assert (
        resolve_knowledge_base_capabilities(_user(role="admin", department_id=7), resource)
        == ALL_KNOWLEDGE_BASE_CAPABILITIES
    )


def test_strict_capability_policy_rejects_unknown_or_cross_level_capabilities():
    with pytest.raises(ValueError, match="read 档位"):
        normalize_permission_config(
            {
                "version": 2,
                "read_scope": {"access_level": "global"},
                "manage_scope": None,
                "capability_policy": {"read": ["upload"], "edit": [], "manage": []},
            },
            strict=True,
        )


def _user(uid="user-1", role="user", department_id=1):
    return SimpleNamespace(uid=uid, role=role, department_id=department_id)


def _resource(created_by="owner", share_config=None, owner_department_id=None):
    return SimpleNamespace(
        created_by=created_by,
        share_config=share_config,
        owner_department_id=owner_department_id,
    )


def test_knowledge_base_legacy_department_manage_scope_does_not_grant_management():
    config = {
        "version": 2,
        "read_scope": {"access_level": "global"},
        "manage_scope": {"access_level": "department", "department_ids": [1]},
    }
    resource = _resource(share_config=config)

    assert resolve_knowledge_base_permission(_user(department_id=1), resource) == ResourcePermission.READ
    managing_admin = _user(uid="admin-1", role="admin", department_id=1)
    readonly_admin = _user(uid="other", role="admin", department_id=2)
    assert resolve_knowledge_base_permission(managing_admin, resource) == ResourcePermission.READ
    assert resolve_knowledge_base_permission(readonly_admin, resource) == ResourcePermission.READ


def test_invalid_v2_scope_does_not_expand_read_access_when_reading():
    resource = _resource(
        share_config={
            "version": 2,
            "read_scope": {"access_level": "department", "department_ids": [1]},
            "manage_scope": {"access_level": "global"},
        }
    )

    assert resolve_knowledge_base_permission(_user(role="admin", department_id=2), resource) == ResourcePermission.NONE


def test_strict_config_rejects_manage_scope_outside_read_scope():
    from yuxi.permissions import normalize_permission_config

    with pytest.raises(ValueError, match="管理范围"):
        normalize_permission_config(
            {
                "version": 2,
                "read_scope": {"access_level": "department", "department_ids": [1]},
                "manage_scope": {"access_level": "global"},
            },
            strict=True,
        )


def test_strict_config_accepts_user_manage_scope_under_department_read_scope():
    from yuxi.permissions import normalize_permission_config

    config = {
        "version": 2,
        "read_scope": {"access_level": "department", "department_ids": [1]},
        "manage_scope": {"access_level": "user", "user_uids": ["user-1"]},
    }
    assert normalize_permission_config(config, strict=True)["manage_scope"]["user_uids"] == ["user-1"]

    resource = _resource(share_config=config)
    assert (
        resolve_knowledge_base_permission(_user(uid="user-1", role="user", department_id=1), resource)
        == ResourcePermission.MANAGE
    )
    assert (
        resolve_knowledge_base_permission(_user(uid="user-1", role="user", department_id=2), resource)
        == ResourcePermission.NONE
    )


def test_department_read_scope_accepts_in_department_user_edit_scope():
    from yuxi.permissions import normalize_permission_config

    config = {
        "version": 2,
        "read_scope": {"access_level": "department", "department_ids": [1]},
        "edit_scope": {"access_level": "user", "user_uids": ["editor-1"]},
        "manage_scope": None,
    }
    normalize_permission_config(config, strict=True)

    resource = _resource(share_config=config)
    assert (
        resolve_knowledge_base_permission(_user(uid="editor-1", department_id=1), resource)
        == ResourcePermission.EDIT
    )
    assert (
        resolve_knowledge_base_permission(_user(uid="editor-1", department_id=2), resource)
        == ResourcePermission.NONE
    )


def test_global_agent_scope_preserves_admin_management():
    resource = _resource(
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global"},
            "manage_scope": {"access_level": "global"},
        }
    )

    assert resolve_agent_permission(_user(role="admin"), resource) == ResourcePermission.MANAGE


def test_user_agent_and_skill_scope_preserves_user_management():
    resource = _resource(
        share_config={
            "version": 2,
            "read_scope": {"access_level": "user", "user_uids": ["user-1"]},
            "manage_scope": {"access_level": "user", "user_uids": ["user-1"]},
        }
    )

    assert resolve_agent_permission(_user(), resource) == ResourcePermission.MANAGE
    assert resolve_skill_permission(_user(), resource) == ResourcePermission.MANAGE


def test_knowledge_base_creator_is_audit_only_and_owner_department_admin_manages():
    resource = _resource(
        created_by="owner",
        owner_department_id=7,
        share_config={"version": 2},
    )

    assert resolve_knowledge_base_permission(_user(uid="owner", department_id=7), resource) == ResourcePermission.NONE
    assert (
        resolve_knowledge_base_permission(_user(uid="owner", role="admin", department_id=8), resource)
        == ResourcePermission.NONE
    )
    assert (
        resolve_knowledge_base_permission(_user(uid="dept-admin", role="admin", department_id=7), resource)
        == ResourcePermission.MANAGE
    )
    assert resolve_knowledge_base_permission(_user(role="superadmin"), resource) == ResourcePermission.MANAGE


def test_agent_creator_still_has_manage_permission():
    resource = _resource(created_by="owner", share_config={"version": 2})

    assert resolve_agent_permission(_user(uid="owner"), resource) == ResourcePermission.MANAGE


def test_knowledge_base_owner_department_can_be_read_from_share_config():
    resource = _resource(
        share_config={"version": 2, "owner_department_id": "7"},
    )

    assert (
        resolve_knowledge_base_permission(_user(role="admin", department_id=7), resource)
        == ResourcePermission.MANAGE
    )
    assert (
        resolve_knowledge_base_permission(_user(role="admin", department_id=8), resource)
        == ResourcePermission.NONE
    )


def test_legacy_global_knowledge_base_manage_scope_is_ignored():
    resource = _resource(
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global"},
            "manage_scope": {"access_level": "global"},
        }
    )

    assert resolve_knowledge_base_permission(_user(role="admin"), resource) == ResourcePermission.READ
    assert resolve_knowledge_base_permission(_user(role="user"), resource) == ResourcePermission.READ


def test_legacy_permission_config_is_rejected_at_runtime():
    from yuxi.permissions import normalize_permission_config

    with pytest.raises(ValueError, match="version 2"):
        normalize_permission_config({"access_level": "department", "department_ids": [1]})


def test_agent_and_skill_use_shared_resolver_with_resource_policy():
    resource = _resource(share_config={"version": 2, "manage_scope": {"access_level": "user", "user_uids": ["user-2"]}})

    assert resolve_agent_permission(_user(uid="user-2"), resource) == ResourcePermission.MANAGE
    assert resolve_skill_permission(_user(uid="user-2"), resource) == ResourcePermission.MANAGE


def test_personal_skill_permission_is_limited_to_owner():
    resource = SimpleNamespace(source_scope="personal", created_by="user-1", share_config=None)

    assert resolve_skill_permission(_user(uid="user-1"), resource) == ResourcePermission.MANAGE
    assert resolve_skill_permission(_user(uid="user-2"), resource) == ResourcePermission.NONE


def test_legacy_broad_manage_scope_does_not_grant_implicit_read_or_edit():
    resource = _resource(
        share_config={
            "version": 2,
            "read_scope": None,
            "manage_scope": {"access_level": "department", "department_ids": [1]},
        }
    )

    assert resolve_knowledge_base_permission(_user(role="admin", department_id=1), resource) == ResourcePermission.NONE
    assert resolve_knowledge_base_permission(_user(department_id=1), resource) == ResourcePermission.NONE
    assert resolve_knowledge_base_permission(_user(role="admin", department_id=2), resource) == ResourcePermission.NONE


def test_require_permission_rejects_insufficient_access():
    from yuxi.permissions import require_resource_permission

    with pytest.raises(ResourcePermissionDenied):
        require_resource_permission(ResourcePermission.READ, ResourcePermission.MANAGE)


def test_knowledge_base_edit_scope_grants_document_edit_without_management():
    resource = _resource(
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global"},
            "edit_scope": {"access_level": "department", "department_ids": [1]},
            "manage_scope": {"access_level": "user", "user_uids": ["admin-1"]},
        }
    )

    assert resolve_knowledge_base_permission(_user(department_id=1), resource) == ResourcePermission.EDIT
    assert resolve_knowledge_base_permission(_user(department_id=2), resource) == ResourcePermission.READ
    assert (
        resolve_knowledge_base_permission(_user(uid="editor-admin", role="admin", department_id=1), resource)
        == ResourcePermission.EDIT
    )
    assert (
        resolve_knowledge_base_permission(_user(uid="admin-1", role="user", department_id=1), resource)
        == ResourcePermission.MANAGE
    )


def test_explicit_regular_user_manager_receives_configured_manage_capabilities():
    resource = _resource(
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global"},
            "edit_scope": {"access_level": "user", "user_uids": ["manager-1"]},
            "manage_scope": {"access_level": "user", "user_uids": ["manager-1"]},
            "capability_policy": {
                "read": ["view"],
                "edit": ["upload"],
                "manage": ["configure", "grant"],
            },
        }
    )

    manager = _user(uid="manager-1", role="user")
    assert resolve_knowledge_base_permission(manager, resource) == ResourcePermission.MANAGE
    assert resolve_knowledge_base_capabilities(manager, resource) == {
        KnowledgeBaseCapability.VIEW,
        KnowledgeBaseCapability.UPLOAD,
        KnowledgeBaseCapability.CONFIGURE,
        KnowledgeBaseCapability.GRANT,
    }


def test_strict_config_rejects_manage_scope_outside_edit_scope():
    from yuxi.permissions import normalize_permission_config

    with pytest.raises(ValueError, match="管理范围"):
        normalize_permission_config(
            {
                "version": 2,
                "read_scope": {"access_level": "global"},
                "edit_scope": {"access_level": "department", "department_ids": [1]},
                "manage_scope": {"access_level": "global"},
            },
            strict=True,
        )


def test_invalid_manage_scope_outside_edit_scope_does_not_expand_runtime_access():
    resource = _resource(
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global"},
            "edit_scope": {"access_level": "department", "department_ids": [1]},
            "manage_scope": {"access_level": "global"},
        }
    )

    assert resolve_knowledge_base_permission(_user(role="admin", department_id=2), resource) == ResourcePermission.READ


def test_require_knowledge_base_permission_uses_resolved_resource_permission():
    resource = _resource(
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global"},
            "manage_scope": None,
        }
    )

    assert (
        require_knowledge_base_permission(_user(role="admin"), resource, ResourcePermission.READ)
        == ResourcePermission.READ
    )
    with pytest.raises(ResourcePermissionDenied):
        require_knowledge_base_permission(_user(role="admin"), resource, ResourcePermission.MANAGE)


def test_v2_scope_validation_rejects_disallowed_access_level():
    from yuxi.permissions import normalize_permission_config

    with pytest.raises(ValueError, match="共享范围"):
        normalize_permission_config(
            {
                "version": 2,
                "read_scope": {"access_level": "global"},
                "manage_scope": None,
            },
            allowed_access_levels={"user"},
        )
    resolve_knowledge_base_capabilities,
