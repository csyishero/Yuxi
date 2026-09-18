"""统一解析 Agent、Skill 与知识库的共享权限。"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class ResourcePermission(StrEnum):
    """资源权限等级，数值顺序用于判断权限是否足够。"""

    NONE = "none"
    READ = "read"
    EDIT = "edit"
    MANAGE = "manage"


class ResourcePermissionDenied(PermissionError):
    """当前用户的资源权限不足。"""


class KnowledgeBaseCapability(StrEnum):
    """知识库可配置的细粒度能力。"""

    VIEW = "view"
    SEARCH = "search"
    DOWNLOAD = "download"
    UPLOAD = "upload"
    METADATA = "metadata"
    PARSE = "parse"
    INDEX = "index"
    CONFIGURE = "configure"
    SHARE = "share"
    GRANT = "grant"
    DELETE_DOCUMENT = "delete-document"
    DELETE_KNOWLEDGE_BASE = "delete-knowledge-base"


class ShareableResource(Protocol):
    """声明可通过共享配置进行权限解析的资源字段。"""

    created_by: str | None
    share_config: dict | None


def knowledge_base_owner_department_id(resource: Any) -> int | None:
    """读取知识库所属部门；优先使用读取模型字段，并兼容 JSON 持久化字段。"""

    raw_value = _value(resource, "owner_department_id")
    if raw_value is None:
        share_config = _value(resource, "share_config")
        if isinstance(share_config, Mapping):
            raw_value = share_config.get("owner_department_id")
    try:
        return int(raw_value) if raw_value is not None else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ResourcePermissionPolicy:
    """声明资源类型允许的角色上限，不包含共享范围匹配逻辑。"""

    role_ceiling: dict[str, ResourcePermission]


RESOURCE_PERMISSION_ORDER = {
    ResourcePermission.NONE: 0,
    ResourcePermission.READ: 1,
    ResourcePermission.EDIT: 2,
    ResourcePermission.MANAGE: 3,
}

DEFAULT_SCOPE = {"access_level": "global", "department_ids": [], "user_uids": []}
KNOWLEDGE_BASE_PERMISSION_POLICY = ResourcePermissionPolicy(
    role_ceiling={
        # 普通用户可以被显式委派为某一个知识库的管理员；创建知识库、
        # 部门管理等系统权限仍由各自入口单独校验，不会随此上限放开。
        "user": ResourcePermission.MANAGE,
        "admin": ResourcePermission.MANAGE,
        "superadmin": ResourcePermission.MANAGE,
    }
)
AGENT_PERMISSION_POLICY = ResourcePermissionPolicy(
    role_ceiling={
        "user": ResourcePermission.MANAGE,
        "admin": ResourcePermission.MANAGE,
        "superadmin": ResourcePermission.MANAGE,
    }
)
SKILL_PERMISSION_POLICY = AGENT_PERMISSION_POLICY

KNOWLEDGE_BASE_CAPABILITIES_BY_LEVEL = {
    ResourcePermission.READ: frozenset(
        {
            KnowledgeBaseCapability.VIEW,
            KnowledgeBaseCapability.SEARCH,
            KnowledgeBaseCapability.DOWNLOAD,
        }
    ),
    ResourcePermission.EDIT: frozenset(
        {
            KnowledgeBaseCapability.UPLOAD,
            KnowledgeBaseCapability.METADATA,
            KnowledgeBaseCapability.PARSE,
            KnowledgeBaseCapability.INDEX,
        }
    ),
    ResourcePermission.MANAGE: frozenset(
        {
            KnowledgeBaseCapability.CONFIGURE,
            KnowledgeBaseCapability.SHARE,
            KnowledgeBaseCapability.GRANT,
            KnowledgeBaseCapability.DELETE_DOCUMENT,
            KnowledgeBaseCapability.DELETE_KNOWLEDGE_BASE,
        }
    ),
}
ALL_KNOWLEDGE_BASE_CAPABILITIES = frozenset().union(*KNOWLEDGE_BASE_CAPABILITIES_BY_LEVEL.values())
DEFAULT_KNOWLEDGE_BASE_CAPABILITY_POLICY = {
    level.value: sorted(capability.value for capability in capabilities)
    for level, capabilities in KNOWLEDGE_BASE_CAPABILITIES_BY_LEVEL.items()
}


def normalize_knowledge_base_capability_policy(policy: dict | None, *, strict: bool = False) -> dict:
    """规范化知识库能力策略；缺失策略沿用兼容默认模板。"""

    if policy is None:
        return {level: list(capabilities) for level, capabilities in DEFAULT_KNOWLEDGE_BASE_CAPABILITY_POLICY.items()}
    if not isinstance(policy, dict):
        raise ValueError("知识库能力策略必须是对象")

    normalized: dict[str, list[str]] = {}
    known_levels = {level.value for level in KNOWLEDGE_BASE_CAPABILITIES_BY_LEVEL}
    if strict and set(policy) - known_levels:
        raise ValueError("知识库能力策略包含未知权限档位")

    for level, allowed in KNOWLEDGE_BASE_CAPABILITIES_BY_LEVEL.items():
        raw_capabilities = policy.get(level.value, [])
        if not isinstance(raw_capabilities, list):
            raise ValueError(f"{level.value} 能力配置必须是数组")
        try:
            capabilities = {KnowledgeBaseCapability(str(value)) for value in raw_capabilities}
        except ValueError as error:
            raise ValueError(f"{level.value} 能力配置包含未知能力") from error
        invalid = capabilities - allowed
        if invalid:
            invalid_labels = ", ".join(sorted(capability.value for capability in invalid))
            raise ValueError(f"{level.value} 档位不能配置能力: {invalid_labels}")
        normalized[level.value] = sorted(capability.value for capability in capabilities)
    return normalized


def _normalize_scope(scope: dict | None) -> dict | None:
    """规范化共享范围并校验其访问级别与成员列表。"""

    if scope is None:
        return None
    if not isinstance(scope, dict):
        raise ValueError("权限范围必须是对象")

    access_level = scope.get("access_level") or "global"
    if access_level not in {"global", "department", "user"}:
        raise ValueError("无效的资源权限范围")

    if access_level == "global":
        return DEFAULT_SCOPE.copy()
    if access_level == "department":
        department_ids = sorted({int(value) for value in scope.get("department_ids") or []})
        if not department_ids:
            raise ValueError("部门权限至少需要选择一个部门")
        return {"access_level": access_level, "department_ids": department_ids, "user_uids": []}

    user_uids = sorted({str(value).strip() for value in scope.get("user_uids") or [] if str(value).strip()})
    if not user_uids:
        raise ValueError("指定用户权限至少需要选择一个用户")
    return {"access_level": access_level, "department_ids": [], "user_uids": user_uids}


def _validate_nested_scope(
    parent_scope: dict | None,
    child_scope: dict | None,
    *,
    message: str,
) -> None:
    """确保高权限范围不会超出其低权限范围。

    部门级父范围允许使用指定用户作为更窄的子范围。此纯配置层无法查询用户部门，
    运行时仍会同时匹配父、子范围，因此部门外用户不会获得额外权限。
    """

    if not parent_scope or not child_scope or parent_scope["access_level"] == "global":
        return

    parent_level = parent_scope["access_level"]
    child_level = child_scope["access_level"]
    if parent_level == "department" and child_level == "user":
        return
    if child_level != parent_level:
        raise ValueError(message)
    if parent_level == child_level == "department":
        if not set(child_scope["department_ids"]).issubset(parent_scope["department_ids"]):
            raise ValueError(message)
    elif parent_level == child_level == "user":
        if not set(child_scope["user_uids"]).issubset(parent_scope["user_uids"]):
            raise ValueError(message)


def normalize_permission_config(
    share_config: dict | None,
    *,
    allowed_access_levels: Collection[str] | None = None,
    unauthorized_access_level_message: str = "当前用户无权使用该资源共享范围",
    strict: bool = False,
) -> dict:
    """规范化并校验 v2 共享配置。"""

    config = share_config if isinstance(share_config, dict) else {}
    if config.get("version") == 2:
        read_scope = _normalize_scope(config.get("read_scope"))
        edit_scope = _normalize_scope(config.get("edit_scope")) if "edit_scope" in config else None
        manage_scope = _normalize_scope(config.get("manage_scope"))
        try:
            _validate_nested_scope(read_scope, edit_scope, message="编辑范围必须包含在读取范围内")
            if edit_scope is not None:
                _validate_nested_scope(edit_scope, manage_scope, message="管理范围必须包含在编辑范围内")
            else:
                _validate_nested_scope(read_scope, manage_scope, message="管理范围必须包含在读取范围内")
        except ValueError:
            if strict:
                raise
            # 读取历史配置时保持原值；保存时由 strict 校验拒绝越界配置。
        normalized = {
            "version": 2,
            "read_scope": read_scope,
            "manage_scope": manage_scope,
        }
        if "edit_scope" in config:
            normalized["edit_scope"] = edit_scope
        if "capability_policy" in config:
            normalized["capability_policy"] = normalize_knowledge_base_capability_policy(
                config.get("capability_policy"),
                strict=strict,
            )
        if allowed_access_levels is not None:
            for scope in (normalized["read_scope"], normalized.get("edit_scope"), normalized["manage_scope"]):
                if scope and scope["access_level"] not in allowed_access_levels:
                    raise ValueError(unauthorized_access_level_message)
        return normalized
    raise ValueError("资源共享配置必须使用 version 2")


def scope_matches(user: Any, scope: dict | None) -> bool:
    """判断用户是否命中一个共享范围。"""

    if not scope:
        return False
    access_level = scope.get("access_level")
    if access_level == "global":
        return True
    if access_level == "department":
        department_id = _value(user, "department_id")
        try:
            return department_id is not None and int(department_id) in scope.get("department_ids", [])
        except (TypeError, ValueError):
            return False
    if access_level == "user":
        return str(_value(user, "uid", "") or "") in scope.get("user_uids", [])
    return False


def _value(source: Any, key: str, default: Any = None) -> Any:
    """从字典或对象读取属性，统一权限解析的输入访问方式。"""

    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _minimum_permission(left: ResourcePermission, right: ResourcePermission) -> ResourcePermission:
    """按权限等级顺序返回两者中更低的权限。"""

    return left if RESOURCE_PERMISSION_ORDER[left] <= RESOURCE_PERMISSION_ORDER[right] else right


def resolve_resource_permission(
    user: Any,
    resource: ShareableResource,
    policy: ResourcePermissionPolicy,
    *,
    owner_grants_manage: bool = True,
) -> ResourcePermission:
    """解析资源所有权、共享范围和角色上限后的有效权限。"""

    if _value(user, "role") == "superadmin":
        return ResourcePermission.MANAGE

    raw_share_config = _value(resource, "share_config")
    config = normalize_permission_config(
        raw_share_config,
    )
    if owner_grants_manage and str(_value(resource, "created_by", "") or "") == str(
        _value(user, "uid", "") or ""
    ):
        granted = ResourcePermission.MANAGE
    elif (
        scope_matches(user, config["manage_scope"])
        and (config.get("edit_scope") is None or scope_matches(user, config.get("edit_scope")))
        and (config["read_scope"] is None or scope_matches(user, config["read_scope"]))
    ):
        granted = ResourcePermission.MANAGE
    elif scope_matches(user, config.get("edit_scope")) and (
        config["read_scope"] is None or scope_matches(user, config["read_scope"])
    ):
        granted = ResourcePermission.EDIT
    elif scope_matches(user, config["read_scope"]):
        granted = ResourcePermission.READ
    else:
        granted = ResourcePermission.NONE

    ceiling = policy.role_ceiling.get(_value(user, "role"), ResourcePermission.READ)
    return _minimum_permission(granted, ceiling)


def require_resource_permission(
    actual: ResourcePermission,
    required: ResourcePermission,
) -> None:
    """在权限不足时显式失败。"""

    if RESOURCE_PERMISSION_ORDER[actual] < RESOURCE_PERMISSION_ORDER[required]:
        raise ResourcePermissionDenied(f"需要 {required.value} 权限，当前为 {actual.value}")


def resolve_knowledge_base_permission(user: Any, resource: ShareableResource) -> ResourcePermission:
    """解析部门知识库权限，创建人字段仅用于审计。"""

    if _value(user, "role") == "superadmin":
        return ResourcePermission.MANAGE

    owner_department_id = knowledge_base_owner_department_id(resource)
    user_department_id = _value(user, "department_id")
    try:
        manages_owner_department = (
            _value(user, "role") == "admin"
            and owner_department_id is not None
            and user_department_id is not None
            and int(user_department_id) == owner_department_id
        )
    except (TypeError, ValueError):
        manages_owner_department = False
    if manages_owner_department:
        return ResourcePermission.MANAGE

    # 额外知识库管理员只能逐人委派。历史配置中的全局/部门 manage_scope
    # 不再产生管理权限，避免在提高普通用户角色上限后扩大既有授权范围。
    config = normalize_permission_config(_value(resource, "share_config"))
    manage_scope = config.get("manage_scope")
    if manage_scope and manage_scope.get("access_level") != "user":
        config = {**config, "manage_scope": None}

    return resolve_resource_permission(
        user,
        {
            "created_by": _value(resource, "created_by"),
            "share_config": config,
        },
        KNOWLEDGE_BASE_PERMISSION_POLICY,
        owner_grants_manage=False,
    )


def _is_system_inherited_knowledge_base_manager(user: Any, resource: ShareableResource) -> bool:
    """判断用户是否由系统身份规则继承完整知识库管理能力。"""

    if _value(user, "role") == "superadmin":
        return True
    if _value(user, "role") != "admin":
        return False
    owner_department_id = knowledge_base_owner_department_id(resource)
    user_department_id = _value(user, "department_id")
    try:
        return (
            owner_department_id is not None
            and user_department_id is not None
            and int(user_department_id) == owner_department_id
        )
    except (TypeError, ValueError):
        return False


def resolve_knowledge_base_capabilities(
    user: Any,
    resource: ShareableResource,
) -> frozenset[KnowledgeBaseCapability]:
    """按有效权限档位解析用户拥有的知识库能力集合。"""

    permission = resolve_knowledge_base_permission(user, resource)
    if permission == ResourcePermission.NONE:
        return frozenset()
    if _is_system_inherited_knowledge_base_manager(user, resource):
        return ALL_KNOWLEDGE_BASE_CAPABILITIES

    raw_share_config = _value(resource, "share_config")
    raw_policy = raw_share_config.get("capability_policy") if isinstance(raw_share_config, Mapping) else None
    policy = normalize_knowledge_base_capability_policy(raw_policy)
    capabilities: set[KnowledgeBaseCapability] = set()
    for level in (ResourcePermission.READ, ResourcePermission.EDIT, ResourcePermission.MANAGE):
        if RESOURCE_PERMISSION_ORDER[level] > RESOURCE_PERMISSION_ORDER[permission]:
            break
        capabilities.update(KnowledgeBaseCapability(value) for value in policy[level.value])
    return frozenset(capabilities)


def require_knowledge_base_capability(
    user: Any,
    resource: ShareableResource,
    required: KnowledgeBaseCapability,
) -> frozenset[KnowledgeBaseCapability]:
    """校验用户是否具备指定知识库能力，并返回有效能力集合。"""

    capabilities = resolve_knowledge_base_capabilities(user, resource)
    if required not in capabilities:
        raise ResourcePermissionDenied(f"缺少知识库能力: {required.value}")
    return capabilities


def require_knowledge_base_permission(
    user: Any,
    resource: ShareableResource,
    required: ResourcePermission,
) -> ResourcePermission:
    """校验用户是否具备知识库所需权限，并返回实际权限。"""

    actual = resolve_knowledge_base_permission(user, resource)
    require_resource_permission(actual, required)
    return actual


def resolve_agent_permission(user: Any, resource: ShareableResource) -> ResourcePermission:
    """解析 Agent 权限。"""

    return resolve_resource_permission(
        user,
        resource,
        AGENT_PERMISSION_POLICY,
    )


def resolve_skill_permission(user: Any, resource: ShareableResource) -> ResourcePermission:
    """解析 Skill 权限。"""

    if _value(resource, "source_scope") == "personal":
        if str(_value(resource, "created_by", "") or "") == str(_value(user, "uid", "") or ""):
            return ResourcePermission.MANAGE
        return ResourcePermission.NONE

    return resolve_resource_permission(
        user,
        resource,
        SKILL_PERMISSION_POLICY,
    )
