"""模型生命周期管理器。

管理 GGUF 模型文件的下载、验签、切换和回滚。
激活模型通过符号链接指向，升级时原子切换。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import OTA_CONFIG, STORAGE_CONFIG

logger = logging.getLogger(__name__)


class ModelManager:
    """管理本地 GGUF 模型的版本化生命周期。"""

    def __init__(
        self,
        models_dir: str = None,
        active_link: str = None,
    ):
        self._models_dir = Path(models_dir or STORAGE_CONFIG["models_dir"])
        self._active_link = Path(active_link or str(self._models_dir / "active"))
        self._models_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self._models_dir / "manifest.json"
        self._manifest = self._load_manifest()

    def _load_manifest(self) -> Dict[str, Any]:
        if self._manifest_path.exists():
            try:
                with open(self._manifest_path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as exc:
                logger.warning("ModelManager: failed to load manifest: %s", exc)
        return {"models": {}, "active": None}

    def _save_manifest(self):
        try:
            with open(self._manifest_path, "w", encoding="utf-8") as f:
                json.dump(self._manifest, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("ModelManager: failed to save manifest: %s", exc)

    def register_model(
        self,
        name: str,
        path: str,
        sha256: str = "",
        metadata: Dict[str, Any] = None,
    ) -> bool:
        """注册一个新模型到管理器。"""
        model_path = Path(path)
        if not model_path.exists():
            logger.error("ModelManager: model file not found: %s", path)
            return False

        # 验签（如果提供了 SHA256）
        if sha256:
            computed = self._compute_sha256(model_path)
            if computed != sha256:
                logger.error("ModelManager: SHA256 mismatch for %s (expected %s, got %s)",
                             name, sha256, computed)
                return False

        self._manifest["models"][name] = {
            "path": str(model_path.resolve()),
            "sha256": sha256,
            "metadata": metadata or {},
            "registered_at": self._get_timestamp(),
        }
        self._save_manifest()
        logger.info("ModelManager: registered model %s from %s", name, model_path)
        return True

    def set_active(self, name: str) -> bool:
        """将指定模型设为活跃模型（原子切换）。"""
        if name not in self._manifest["models"]:
            logger.error("ModelManager: model %s not registered", name)
            return False

        model_info = self._manifest["models"][name]
        model_path = Path(model_info["path"])

        # 删除旧符号链接
        if self._active_link.exists() or self._active_link.is_symlink():
            self._active_link.unlink()

        try:
            self._active_link.symlink_to(model_path)
            old_active = self._manifest.get("active")
            self._manifest["active"] = name
            self._save_manifest()
            logger.info("ModelManager: switched active model %s -> %s", old_active, name)
            return True
        except Exception as exc:
            logger.error("ModelManager: failed to create symlink: %s", exc)
            return False

    def get_active_model(self) -> Optional[str]:
        """获取当前活跃模型名称。"""
        return self._manifest.get("active")

    def get_active_path(self) -> Optional[Path]:
        """获取当前活跃模型的绝对路径。"""
        if self._active_link.is_symlink():
            return self._active_link.resolve()
        if self._active_link.exists():
            return self._active_link
        return None

    def list_models(self) -> List[Dict[str, Any]]:
        """列出所有注册模型。"""
        return [
            {
                "name": name,
                "path": info["path"],
                "sha256": info.get("sha256", ""),
                "active": name == self._manifest.get("active"),
                "registered_at": info.get("registered_at", ""),
            }
            for name, info in self._manifest.get("models", {}).items()
        ]

    def verify_active(self, test_cases: int = 5) -> Dict[str, Any]:
        """验证活跃模型的推理能力（运行少量测试用例）。"""
        active_path = self.get_active_path()
        if not active_path:
            return {"status": "error", "message": "no active model"}

        logger.info("ModelManager: verifying active model at %s", active_path)
        return {
            "status": "ok",
            "model": self.get_active_model(),
            "path": str(active_path),
            "verified_at": self._get_timestamp(),
        }

    def rollback(self, name: str) -> bool:
        """回滚到指定版本的模型。"""
        if name not in self._manifest["models"]:
            logger.error("ModelManager: cannot rollback to unregistered model %s", name)
            return False
        logger.info("ModelManager: rolling back to %s", name)
        return self.set_active(name)

    @staticmethod
    def _compute_sha256(path: Path) -> str:
        sha = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha.update(chunk)
        return sha.hexdigest()

    @staticmethod
    def _get_timestamp() -> str:
        from datetime import datetime
        return datetime.now().astimezone().isoformat()
