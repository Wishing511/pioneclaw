"""
文件服务 API — 提供媒体文件访问

GET /api/files — 根据路径返回文件内容
支持 image/video/audio/document 等类型
"""

import mimetypes
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.api.auth import get_current_active_user
from app.models import User

router = APIRouter(prefix="/files", tags=["files"])

# 允许的扩展名白名单
_ALLOWED_EXTENSIONS = {
    # 图片
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".webp",
    ".svg",
    ".ico",
    # 音频
    ".mp3",
    ".wav",
    ".ogg",
    ".flac",
    ".aac",
    ".m4a",
    ".wma",
    # 视频
    ".mp4",
    ".webm",
    ".avi",
    ".mov",
    ".mkv",
    ".flv",
    ".wmv",
    # 文档
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".xml",
    ".html",
}


@router.get("")
async def serve_file(
    path: str = Query(..., description="文件路径（绝对或相对路径）"),
    current_user: User = Depends(get_current_active_user),
):
    """
    根据路径返回文件内容。

    安全限制：
    - 仅允许白名单中的文件扩展名
    - 路径必须存在且为普通文件
    """
    file_path = os.path.abspath(path)

    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail=f"文件不存在: {path}")

    ext = os.path.splitext(file_path)[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=403, detail=f"不支持的文件类型: {ext}")

    # 限制文件大小 (100MB)
    if os.path.getsize(file_path) > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件过大 (最大 100MB)")

    media_type, _ = mimetypes.guess_type(file_path)
    if media_type is None:
        media_type = "application/octet-stream"

    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=os.path.basename(file_path),
    )
