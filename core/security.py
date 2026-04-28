"""
Encrypted local storage utilities.

This module provides strict symmetric encryption based on `cryptography.fernet`.
It only uses externally supplied key material and never falls back to a default
password or local `.key` / `.salt` files.
"""

from __future__ import annotations

import base64
import logging
import os
import pickle
from typing import Any, Optional

logger = logging.getLogger(__name__)

ENCRYPTED_PICKLE_MAGIC = b"HMS1"
KDF_SALT = b"HomeMind::EncryptedStorage::v2"

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    FERNET_AVAILABLE = True
except ImportError:
    FERNET_AVAILABLE = False
    logger.warning("cryptography 库未安装，加密存储功能不可用")


def _derive_key(password: str, salt: bytes) -> bytes:
    """Derive a Fernet key from an external password."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


class EncryptedStorage:
    """Strict symmetric encryption wrapper for local sensitive persistence."""

    def __init__(self, password: str = None):
        self._fernet = None
        self._password = str(password or os.getenv("HOMEMIND_STORAGE_KEY", "")).strip()
        self._backend = "cryptography.fernet" if FERNET_AVAILABLE else "unavailable"
        self._reason = ""
        self._key_source = "explicit" if password else ("environment" if self._password else "missing")

        if not FERNET_AVAILABLE:
            self._reason = "missing_backend"
            logger.warning("加密存储初始化失败：cryptography 库未安装")
            return

        if not self._password:
            self._reason = "missing_key"
            logger.warning("加密存储已禁用：缺少 HOMEMIND_STORAGE_KEY")
            return

        try:
            key = _derive_key(self._password, KDF_SALT)
            self._fernet = Fernet(key)
            logger.info("加密存储已初始化（使用外部密钥）")
        except Exception as exc:
            self._reason = f"init_failed:{exc}"
            logger.error("加密存储初始化失败: %s", exc)

    def is_available(self) -> bool:
        return self._fernet is not None

    def status(self) -> dict:
        return {
            "enabled": self.is_available(),
            "reason": self._reason if not self.is_available() else "",
            "backend": self._backend,
            "key_source": self._key_source,
        }

    def encrypt_data(self, data: bytes) -> bytes:
        if not self._fernet:
            raise RuntimeError(f"encrypted storage unavailable: {self._reason or 'missing_key'}")
        return self._fernet.encrypt(data)

    def decrypt_data(self, encrypted_data: bytes) -> bytes:
        if not self._fernet:
            raise RuntimeError(f"encrypted storage unavailable: {self._reason or 'missing_key'}")
        return self._fernet.decrypt(encrypted_data)

    def save_pickle(self, data: Any, path: str) -> bool:
        """Persist encrypted pickle bytes without plaintext fallback."""
        if not self.is_available():
            logger.error("敏感存储写入已禁用: %s", self.status())
            return False
        try:
            raw = pickle.dumps(data)
            encrypted = self.encrypt_data(raw)
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(ENCRYPTED_PICKLE_MAGIC + encrypted)
            logger.info("已加密保存: %s", path)
            return True
        except Exception as exc:
            logger.error("加密保存失败: %s", exc)
            return False

    def load_pickle(self, path: str, default: Any = None, allow_legacy_plaintext: bool = False) -> Any:
        """Load encrypted pickle data, with optional legacy plaintext compatibility."""
        if not os.path.exists(path):
            return default
        try:
            with open(path, "rb") as handle:
                data = handle.read()
            if data.startswith(ENCRYPTED_PICKLE_MAGIC):
                if not self.is_available():
                    logger.warning("检测到加密文件，但当前缺少密钥: %s", path)
                    return default
                decrypted = self.decrypt_data(data[len(ENCRYPTED_PICKLE_MAGIC):])
                return pickle.loads(decrypted)

            if self.is_available():
                try:
                    decrypted = self.decrypt_data(data)
                    return pickle.loads(decrypted)
                except Exception:
                    pass

            if allow_legacy_plaintext:
                return pickle.loads(data)
        except Exception as exc:
            logger.warning("加密加载失败: %s", exc)
        return default

    def encrypt_file(self, src: str, dst: str) -> bool:
        if not self.is_available():
            logger.error("敏感文件加密已禁用: %s", self.status())
            return False
        try:
            with open(src, "rb") as handle:
                data = handle.read()
            encrypted = self.encrypt_data(data)
            with open(dst, "wb") as handle:
                handle.write(encrypted)
            return True
        except Exception as exc:
            logger.error("文件加密失败: %s", exc)
            return False

    def decrypt_file(self, src: str, dst: str) -> bool:
        if not self.is_available():
            logger.error("敏感文件解密已禁用: %s", self.status())
            return False
        try:
            with open(src, "rb") as handle:
                data = handle.read()
            decrypted = self.decrypt_data(data)
            with open(dst, "wb") as handle:
                handle.write(decrypted)
            return True
        except Exception as exc:
            logger.error("文件解密失败: %s", exc)
            return False


_encrypted_storage: Optional[EncryptedStorage] = None


def reset_encrypted_storage() -> None:
    """Reset the global encrypted storage singleton, mainly for tests."""
    global _encrypted_storage
    _encrypted_storage = None


def get_encrypted_storage() -> EncryptedStorage:
    """Get the global encrypted storage instance."""
    global _encrypted_storage
    if _encrypted_storage is None:
        _encrypted_storage = EncryptedStorage()
    return _encrypted_storage
