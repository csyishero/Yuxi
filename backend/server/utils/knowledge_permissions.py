"""将知识库领域权限校验适配为 FastAPI 依赖。"""

from fastapi import Depends, HTTPException

from server.utils.auth_middleware import get_required_user
from yuxi.knowledge.read_models import KnowledgeBaseDetail
from yuxi.knowledge.runtime import knowledge_base
from yuxi.permissions import (
    KnowledgeBaseCapability,
    ResourcePermission,
    ResourcePermissionDenied,
    require_knowledge_base_capability,
    require_knowledge_base_permission,
)
from yuxi.storage.postgres.models_business import User


async def ensure_knowledge_base_permission(
    kb_id: str,
    current_user: User,
    required: ResourcePermission,
) -> KnowledgeBaseDetail:
    """加载知识库并校验当前用户的有效资源权限。"""

    db_info = await knowledge_base.get_database_info(kb_id)
    if not db_info:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")

    try:
        require_knowledge_base_permission(current_user, db_info, required)
    except ResourcePermissionDenied as error:
        raise HTTPException(status_code=403, detail="无权操作该知识库") from error
    return db_info


async def ensure_knowledge_base_capability(
    kb_id: str,
    current_user: User,
    required: KnowledgeBaseCapability,
) -> KnowledgeBaseDetail:
    """加载知识库并校验当前用户的细粒度能力。"""

    db_info = await knowledge_base.get_database_info(kb_id)
    if not db_info:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")
    try:
        require_knowledge_base_capability(current_user, db_info, required)
    except ResourcePermissionDenied as error:
        raise HTTPException(status_code=403, detail=f"无权执行该操作（缺少 {required.value} 权限）") from error
    return db_info


def _capability_dependency(required: KnowledgeBaseCapability):
    async def dependency(
        kb_id: str,
        current_user: User = Depends(get_required_user),
    ) -> User:
        await ensure_knowledge_base_capability(kb_id, current_user, required)
        return current_user

    return dependency


require_knowledge_base_view = _capability_dependency(KnowledgeBaseCapability.VIEW)
require_knowledge_base_search = _capability_dependency(KnowledgeBaseCapability.SEARCH)
require_knowledge_base_download = _capability_dependency(KnowledgeBaseCapability.DOWNLOAD)
require_knowledge_base_upload = _capability_dependency(KnowledgeBaseCapability.UPLOAD)
require_knowledge_base_metadata = _capability_dependency(KnowledgeBaseCapability.METADATA)
require_knowledge_base_parse = _capability_dependency(KnowledgeBaseCapability.PARSE)
require_knowledge_base_index = _capability_dependency(KnowledgeBaseCapability.INDEX)
require_knowledge_base_configure = _capability_dependency(KnowledgeBaseCapability.CONFIGURE)
require_knowledge_base_share = _capability_dependency(KnowledgeBaseCapability.SHARE)
require_knowledge_base_grant = _capability_dependency(KnowledgeBaseCapability.GRANT)
require_knowledge_base_delete_document = _capability_dependency(KnowledgeBaseCapability.DELETE_DOCUMENT)
require_knowledge_base_delete = _capability_dependency(KnowledgeBaseCapability.DELETE_KNOWLEDGE_BASE)


async def require_knowledge_base_read(
    kb_id: str,
    current_user: User = Depends(get_required_user),
) -> User:
    """校验当前用户对指定知识库的读取权限。"""

    await ensure_knowledge_base_permission(kb_id, current_user, ResourcePermission.READ)
    return current_user


async def require_knowledge_base_edit(
    kb_id: str,
    current_user: User = Depends(get_required_user),
) -> User:
    """校验当前用户对指定知识库的文档贡献权限。"""

    await ensure_knowledge_base_permission(kb_id, current_user, ResourcePermission.EDIT)
    return current_user


async def require_knowledge_base_manage(
    kb_id: str,
    current_user: User = Depends(get_required_user),
) -> User:
    """校验当前用户对指定知识库的管理权限。"""

    await ensure_knowledge_base_permission(kb_id, current_user, ResourcePermission.MANAGE)
    return current_user
