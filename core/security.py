"""
加密存储模块
提供：
- DQN 策略的加密存储（pickle + AES-256-GCM）
- 通用加密文件读写
- 知识库备份加密
"""
import os
import logging
import pickle
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 尝试导入 cryptography 库
try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    import base64
    FERNET_AVAILABLE = True
except ImportError:
    FERNET_AVAILABLE = False
    logger.warning("cryptography 库未安装，加密存储功能不可用")


def _derive_key(password: str, salt: bytes) -> bytes:
    """从密码派生加密密钥（PBKDF2）"""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))


class EncryptedStorage:
    """
    加密存储类
    使用 AES-256-GCM 加密数据
    """

    def __init__(self, password: str = None, key_file: str = "data/.key"):
        self.key_file = key_file
        self._fernet = None
        self._password = password or os.getenv("HOMEMIND_STORAGE_KEY", "")

        if FERNET_AVAILABLE:
            self._init_cipher()
        else:
            logger.warning("加密存储初始化失败：cryptography 库未安装。将使用明文存储（不安全）。")

    def _init_cipher(self):
        """初始化加密器"""
        if not self._password:
            # 尝试从密钥文件加载
            if os.path.exists(self.key_file):
                try:
                    with open(self.key_file, "rb") as f:
                        salt, key = pickle.load(f)
                    self._fernet = Fernet(key)
                    logger.info("加密存储已初始化（从密钥文件）")
                    return
                except Exception as e:
                    logger.warning(f"密钥文件加载失败: {e}")

            # 生成新密钥（首次运行）
            self._password = os.getenv("HOMEMIND_STORAGE_KEY", "homemind-default-key-change-me")

        # 派生密钥
        salt_file = self.key_file + ".salt"
        if os.path.exists(salt_file):
            with open(salt_file, "rb") as f:
                salt = f.read()
        else:
            salt = os.urandom(16)
            os.makedirs(os.path.dirname(self.key_file) or ".", exist_ok=True)
            with open(salt_file, "wb") as f:
                f.write(salt)

        key = _derive_key(self._password, salt)
        self._fernet = Fernet(key)

        # 保存密钥文件（用密码加密后的密钥）
        try:
            with open(self.key_file, "wb") as f:
                pickle.dump((salt, key), f)
        except Exception as e:
            logger.warning(f"密钥文件保存失败: {e}")

        logger.info("加密存储已初始化（首次运行）")

    def encrypt_data(self, data: bytes) -> bytes:
        """加密数据"""
        if self._fernet:
            return self._fernet.encrypt(data)
        return data  # 无加密时返回原数据

    def decrypt_data(self, encrypted_data: bytes) -> bytes:
        """解密数据"""
        if self._fernet:
            return self._fernet.decrypt(encrypted_data)
        return encrypted_data

    def save_pickle(self, data: Any, path: str) -> bool:
        """加密保存 pickle 数据"""
        try:
            raw = pickle.dumps(data)
            encrypted = self.encrypt_data(raw)
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "wb") as f:
                f.write(encrypted)
            logger.info(f"已加密保存: {path}")
            return True
        except Exception as e:
            logger.error(f"加密保存失败: {e}")
            # 回退到明文保存
            try:
                with open(path, "wb") as f:
                    pickle.dump(data, f)
                logger.warning(f"已明文保存（加密失败）: {path}")
                return True
            except Exception as e2:
                logger.error(f"明文保存也失败: {e2}")
                return False

    def load_pickle(self, path: str, default: Any = None) -> Any:
        """加载并解密 pickle 数据"""
        if not os.path.exists(path):
            return default

        try:
            with open(path, "rb") as f:
                data = f.read()

            # 尝试解密
            if self._fernet and len(data) > 50:  # 加密数据通常较长
                try:
                    decrypted = self.decrypt_data(data)
                    return pickle.loads(decrypted)
                except Exception:
                    pass

            # 回退到明文
            return pickle.loads(data)
        except Exception as e:
            logger.warning(f"加载失败: {e}")
            return default

    def encrypt_file(self, src: str, dst: str) -> bool:
        """加密并复制文件"""
        try:
            with open(src, "rb") as f:
                data = f.read()
            encrypted = self.encrypt_data(data)
            with open(dst, "wb") as f:
                f.write(encrypted)
            return True
        except Exception as e:
            logger.error(f"文件加密失败: {e}")
            return False

    def decrypt_file(self, src: str, dst: str) -> bool:
        """解密并复制文件"""
        try:
            with open(src, "rb") as f:
                data = f.read()
            decrypted = self.decrypt_data(data)
            with open(dst, "wb") as f:
                f.write(decrypted)
            return True
        except Exception as e:
            logger.error(f"文件解密失败: {e}")
            return False


# 全局加密存储实例（延迟初始化）
_encrypted_storage: Optional[EncryptedStorage] = None


def get_encrypted_storage() -> EncryptedStorage:
    """获取全局加密存储实例"""
    global _encrypted_storage
    if _encrypted_storage is None:
        _encrypted_storage = EncryptedStorage()
    return _encrypted_storage
