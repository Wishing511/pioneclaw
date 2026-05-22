"""
API Key 加密/解密工具

使用 Fernet (AES-128-CBC + HMAC-SHA256) 加密存储的 API Key。
当 ENCRYPTION_KEY 未配置时，明文透传（开发环境兼容模式）。

迁移策略:
- 已加密数据: 以 Fernet 格式存储（gAAAAA... 前缀），直接解密
- 明文数据: 以 "PLAINTEXT:" 前缀存储，解密时自动识别并透传
- 这使得现有数据库无需迁移即可正常工作
"""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

logger = logging.getLogger(__name__)

_PLAINTEXT_PREFIX = b"PLAINTEXT:"


def _get_fernet() -> Fernet | None:
    """获取 Fernet 实例。未配置密钥时返回 None（透传模式）。"""
    key = settings.ENCRYPTION_KEY
    if not key:
        return None
    # 将用户提供的任意长度密钥派生为 32 字节 Fernet 密钥
    derived = hashlib.sha256(key.encode("utf-8")).digest()
    fernet_key = base64.urlsafe_b64encode(derived)
    return Fernet(fernet_key)


def encrypt(plaintext: str) -> str:
    """
    加密字符串。

    如果未配置 ENCRYPTION_KEY，以 PLAINTEXT: 前缀存储（开发兼容模式）。
    """
    if not plaintext:
        return plaintext
    f = _get_fernet()
    if f is None:
        logger.warning("ENCRYPTION_KEY not set — storing API key as plaintext")
        return (_PLAINTEXT_PREFIX + plaintext.encode("utf-8")).decode("utf-8")
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: str) -> str:
    """
    解密字符串。

    自动识别明文（PLAINTEXT: 前缀）和密文（Fernet 格式）。
    """
    if not ciphertext:
        return ciphertext
    encoded = ciphertext.encode("utf-8")
    # 明文透传（开发模式或迁移期数据）
    if encoded.startswith(_PLAINTEXT_PREFIX):
        return encoded[len(_PLAINTEXT_PREFIX) :].decode("utf-8")
    f = _get_fernet()
    if f is None:
        # 未配置密钥但遇到非明文数据 → 可能是旧明文数据，直接返回
        logger.warning("ENCRYPTION_KEY not set — returning ciphertext as-is")
        return ciphertext
    try:
        return f.decrypt(encoded).decode("utf-8")
    except InvalidToken:
        # 可能是未加密的旧数据，直接返回原文
        logger.warning(
            "Failed to decrypt — returning value as-is (possibly legacy plaintext)"
        )
        return ciphertext
