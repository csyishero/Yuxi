"""知识库文件对象的删除辅助函数。"""

from yuxi.knowledge.utils.kb_utils import is_minio_url, parse_minio_url
from yuxi.storage.minio.client import get_minio_client
from yuxi.utils import logger


async def delete_document_storage_objects(kb_id: str, doc_id: str, file_path: str) -> None:
    """删除原始对象、解析 Markdown 和预览 PDF。"""
    minio_client = get_minio_client()

    if is_minio_url(file_path):
        try:
            bucket_name, object_name = parse_minio_url(file_path)
            await minio_client.adelete_file(bucket_name, object_name)
        except Exception as minio_error:
            logger.warning(f"从MinIO删除原始文件失败: {minio_error}")

    try:
        await minio_client.adelete_file(minio_client.KB_BUCKETS["parsed"], f"{kb_id}/parsed/{doc_id}.md")
    except Exception as minio_error:
        logger.warning(f"从MinIO删除解析结果失败: {minio_error}")

    try:
        await minio_client.adelete_file(minio_client.KB_BUCKETS["parsed"], f"{kb_id}/preview/{doc_id}.pdf")
    except Exception as minio_error:
        logger.warning(f"从MinIO删除预览 PDF 失败: {minio_error}")
