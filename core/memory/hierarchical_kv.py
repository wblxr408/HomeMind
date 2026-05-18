"""
分层 KV Store — L1热 / L2温 / L3冷 三级存储

基于 FlexKV / Strata 架构设计：
- L1 (热): Python dict，TTL 5分钟，O(1) 访问
- L2 (温): SQLite/SSD，TTL 1小时
- L3 (冷): ChromaDB，持久化归档

写入时数据先入 L1，L1 满了 LRU 淘汰到 L2，
L2 满了再淘汰到 L3。
读取时按 L1 → L2 → L3 逐层查找，命中后升级到热层。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from core.config import KV_CONFIG

logger = logging.getLogger(__name__)

# ── Tier 定义 ────────────────────────────────────────────────────────────────

class Tier(Enum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


@dataclass
class KVEntry:
    key: str
    value: Any
    tier: Tier
    size_bytes: int
    last_access: float
    access_count: int = 0

    def touch(self):
        self.access_count += 1
        self.last_access = time.time()


# ── 分层 KV Store ────────────────────────────────────────────────────────────

class HierarchicalKV:
    """
    分层 KV Store

    容量配置（可按设备性能调整）:
        L1_MAX_BYTES: 热层最大内存占用（默认 200KB）
        L2_MAX_BYTES: 温层最大磁盘占用（默认 10MB）
        L1_TTL_SECONDS: 热层条目 TTL（默认 5 分钟）
        L2_TTL_SECONDS: 温层条目 TTL（默认 1 小时）
    """

    L1_MAX_BYTES: int
    L2_MAX_BYTES: int
    L1_TTL_SECONDS: float
    L2_TTL_SECONDS: float

    def __init__(self, l2_path: str = None):
        cfg = KV_CONFIG
        self.L1_MAX_BYTES = cfg.get("l1_max_bytes", 200 * 1024)
        self.L2_MAX_BYTES = cfg.get("l2_max_bytes", 10 * 1024 * 1024)
        self.L1_TTL_SECONDS = cfg.get("l1_ttl_seconds", 300.0)
        self.L2_TTL_SECONDS = cfg.get("l2_ttl_seconds", 3600.0)
        if l2_path is None:
            l2_path = cfg.get("l2_path", "data/kv_warm.db")
        self._l1: dict[str, KVEntry] = {}
        self._l1_size_bytes: int = 0
        self._l2_path = l2_path
        self._l2_initialized = False
        self._lock = asyncio.Lock()

        # 后台清理任务
        self._cleanup_task: Optional[asyncio.Task] = None

    # ── 公开 API ────────────────────────────────────────────────────────────

    async def get(self, key: str, default: Any = None) -> Any:
        """
        按 key 查找值。

        查找顺序：L1 → L2 → L3
        命中后数据自动升级到 L1 热层。
        """
        async with self._lock:
            # L1 查询
            if entry := self._l1.get(key):
                if time.time() - entry.last_access < self.L1_TTL_SECONDS:
                    entry.touch()
                    return entry.value
                else:
                    # TTL 过期，淘汰
                    self._l1_size_bytes -= entry.size_bytes
                    del self._l1[key]

            # L2 查询
            if val := await self._l2_get(key):
                await self._promote(key, val, Tier.WARM)
                return val

            # L3 查询（ChromaDB path）
            if val := await self._l3_get(key):
                await self._promote(key, val, Tier.COLD)
                return val

            return default

    async def set(self, key: str, value: Any) -> None:
        """
        写入 KV 数据。

        写入路径：始终写入 L1 热层，L1 满了触发 LRU 淘汰到 L2。
        """
        async with self._lock:
            size = self._estimate_size(value)
            now = time.time()

            # 替换已有条目：先释放旧空间
            if key in self._l1:
                self._l1_size_bytes -= self._l1[key].size_bytes
                del self._l1[key]

            entry = KVEntry(
                key=key,
                value=value,
                tier=Tier.HOT,
                size_bytes=size,
                last_access=now,
            )
            self._l1[key] = entry
            self._l1_size_bytes += size

            # L1 满了 → LRU 淘汰到 L2
            while self._l1_size_bytes > self.L1_MAX_BYTES and self._l1:
                await self._evict_lru_l1()

    async def delete(self, key: str) -> bool:
        """删除 key，从所有层级清除。"""
        async with self._lock:
            removed = False
            if key in self._l1:
                self._l1_size_bytes -= self._l1[key].size_bytes
                del self._l1[key]
                removed = True
            await self._l2_delete(key)
            await self._l3_delete(key)
            return removed

    async def contains(self, key: str) -> bool:
        """检查 key 是否存在于任意层级。"""
        async with self._lock:
            if key in self._l1:
                return True
            return await self._l2_contains(key)

    def keys(self) -> list[str]:
        """返回 L1 + L2 所有 key（不含 L3）。"""
        async def _keys():
            async with self._lock:
                k2_keys = await self._l2_keys()
                return list(self._l1.keys()) + k2_keys
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, _keys())
                    return future.result()
            else:
                return asyncio.run(_keys())
        except Exception:
            return list(self._l1.keys())

    async def start_cleanup(self, interval: float = None) -> None:
        """启动后台 TTL 清理任务。"""
        if interval is None:
            interval = KV_CONFIG.get("cleanup_interval_seconds", 60.0)
        if self._cleanup_task is not None:
            return

        async def _cleanup_loop():
            while True:
                await asyncio.sleep(interval)
                try:
                    await self._cleanup_expired()
                except Exception as exc:
                    logger.warning("KV cleanup failed: %s", exc)

        self._cleanup_task = asyncio.create_task(_cleanup_loop())
        logger.info("KV cleanup task started, interval=%.0fs", interval)

    async def stop_cleanup(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
            self._cleanup_task = None

    # ── L1 操作 ────────────────────────────────────────────────────────────

    def _evict_lru_l1(self) -> None:
        """从 L1 淘汰最久未访问的条目到 L2（需在 _lock 内调用）。"""
        if not self._l1:
            return
        lru_key = min(self._l1, key=lambda k: self._l1[k].last_access)
        entry = self._l1.pop(lru_key)
        self._l1_size_bytes -= entry.size_bytes

        # 同步写入 L2（无需 await，在 lock 内同步写入）
        try:
            import sqlite3
            self._ensure_l2_schema()
            conn = sqlite3.connect(self._l2_path)
            conn.execute(
                "INSERT OR REPLACE INTO kv (key, value, tier, size_bytes, last_access, access_count) VALUES (?, ?, ?, ?, ?, ?)",
                (entry.key, json.dumps(entry.value, ensure_ascii=False), "warm", entry.size_bytes, entry.last_access, entry.access_count),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("L2 evict failed for key=%s: %s", lru_key, exc)

    # ── L2 操作 (SQLite) ──────────────────────────────────────────────────

    def _ensure_l2_schema(self) -> None:
        """初始化 L2 SQLite schema。"""
        if self._l2_initialized:
            return
        Path(self._l2_path).parent.mkdir(parents=True, exist_ok=True)
        import sqlite3
        conn = sqlite3.connect(self._l2_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS kv ("
            "key TEXT PRIMARY KEY, value TEXT, tier TEXT, "
            "size_bytes INTEGER, last_access REAL, access_count INTEGER)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_last_access ON kv(last_access)"
        )
        conn.commit()
        conn.close()
        self._l2_initialized = True

    async def _l2_get(self, key: str) -> Optional[Any]:
        """从 L2 SQLite 读取。"""
        def _sync_get():
            try:
                import sqlite3
                self._ensure_l2_schema()
                conn = sqlite3.connect(self._l2_path)
                row = conn.execute(
                    "SELECT value, last_access FROM kv WHERE key = ?",
                    (key,),
                ).fetchone()
                conn.close()
                if row is None:
                    return None
                # 检查 TTL
                if time.time() - row[1] > self.L2_TTL_SECONDS:
                    return None
                return json.loads(row[0])
            except Exception as exc:
                logger.warning("L2 get failed for key=%s: %s", key, exc)
                return None

        return await asyncio.to_thread(_sync_get)

    async def _l2_set(self, key: str, value: Any) -> None:
        """同步写入 L2 SQLite。"""
        def _sync_set():
            try:
                import sqlite3
                self._ensure_l2_schema()
                size = self._estimate_size(value)
                now = time.time()
                conn = sqlite3.connect(self._l2_path)
                conn.execute(
                    "INSERT OR REPLACE INTO kv (key, value, tier, size_bytes, last_access, access_count) VALUES (?, ?, ?, ?, ?, ?)",
                    (key, json.dumps(value, ensure_ascii=False), "warm", size, now, 1),
                )
                conn.commit()
                conn.close()
            except Exception as exc:
                logger.warning("L2 set failed for key=%s: %s", key, exc)

        await asyncio.to_thread(_sync_set)

    async def _l2_delete(self, key: str) -> None:
        def _sync_del():
            try:
                import sqlite3
                conn = sqlite3.connect(self._l2_path)
                conn.execute("DELETE FROM kv WHERE key = ?", (key,))
                conn.commit()
                conn.close()
            except Exception:
                pass
        await asyncio.to_thread(_sync_del)

    async def _l2_contains(self, key: str) -> bool:
        def _sync_contains():
            try:
                import sqlite3
                conn = sqlite3.connect(self._l2_path)
                count = conn.execute(
                    "SELECT COUNT(*) FROM kv WHERE key = ?", (key,)
                ).fetchone()[0]
                conn.close()
                return count > 0
            except Exception:
                return False
        return await asyncio.to_thread(_sync_contains)

    async def _l2_keys(self) -> list[str]:
        def _sync_keys():
            try:
                import sqlite3
                conn = sqlite3.connect(self._l2_path)
                rows = conn.execute("SELECT key FROM kv").fetchall()
                conn.close()
                return [r[0] for r in rows]
            except Exception:
                return []
        return await asyncio.to_thread(_sync_keys)

    # ── L3 操作 (ChromaDB path — 预留) ────────────────────────────────────

    async def _l3_get(self, key: str) -> Optional[Any]:
        """L3: 预留 ChromaDB 接口，当前返回 None。"""
        return None

    async def _l3_delete(self, key: str) -> None:
        pass

    # ── 层级升级 ──────────────────────────────────────────────────────────

    async def _promote(self, key: str, value: Any, from_tier: Tier) -> None:
        """将数据从冷层升级到热层。"""
        async with self._lock:
            # 先从源层删除
            if from_tier == Tier.WARM:
                await self._l2_delete(key)
            elif from_tier == Tier.COLD:
                await self._l3_delete(key)

            # 写入 L1
            size = self._estimate_size(value)
            if self._l1_size_bytes + size > self.L1_MAX_BYTES:
                await self._evict_lru_l1()

            entry = KVEntry(
                key=key,
                value=value,
                tier=Tier.HOT,
                size_bytes=size,
                last_access=time.time(),
                access_count=1,
            )
            self._l1[key] = entry
            self._l1_size_bytes += size

    # ── 清理过期条目 ──────────────────────────────────────────────────────

    async def _cleanup_expired(self) -> None:
        """清理所有层级的过期条目。"""
        async with self._lock:
            now = time.time()
            # 清理 L1 TTL
            expired_keys = [
                k for k, e in self._l1.items()
                if now - e.last_access > self.L1_TTL_SECONDS
            ]
            for k in expired_keys:
                self._l1_size_bytes -= self._l1[k].size_bytes
                del self._l1[k]

            if expired_keys:
                logger.debug("L1 cleanup: removed %d expired entries", len(expired_keys))

        # 清理 L2 TTL（独立事务，避免锁竞争）
        def _sync_cleanup_l2():
            try:
                import sqlite3
                self._ensure_l2_schema()
                conn = sqlite3.connect(self._l2_path)
                cutoff = now - self.L2_TTL_SECONDS
                cur = conn.execute(
                    "DELETE FROM kv WHERE last_access < ?", (cutoff,)
                )
                conn.commit()
                conn.close()
                if cur.rowcount > 0:
                    logger.debug("L2 cleanup: removed %d expired entries", cur.rowcount)
            except Exception as exc:
                logger.warning("L2 cleanup failed: %s", exc)

        await asyncio.to_thread(_sync_cleanup_l2)

    # ── 辅助方法 ──────────────────────────────────────────────────────────

    def _estimate_size(self, value: Any) -> int:
        try:
            return len(json.dumps(value, ensure_ascii=False).encode())
        except Exception:
            return 256

    def get_stats(self) -> dict:
        """返回各层级统计信息。"""
        import sqlite3
        l2_count = 0
        try:
            if Path(self._l2_path).exists():
                conn = sqlite3.connect(self._l2_path)
                l2_count = conn.execute("SELECT COUNT(*) FROM kv").fetchone()[0]
                conn.close()
        except Exception:
            pass

        return {
            "l1_entries": len(self._l1),
            "l1_size_bytes": self._l1_size_bytes,
            "l1_max_bytes": self.L1_MAX_BYTES,
            "l2_entries": l2_count,
            "l2_path": self._l2_path,
        }

    def snapshot(self) -> dict:
        """返回 L1 所有数据（用于调试/快照）。"""
        return {k: e.value for k, e in self._l1.items()}


# ── 全局单例 ────────────────────────────────────────────────────────────────

_hkv: Optional[HierarchicalKV] = None


def get_hkv() -> HierarchicalKV:
    global _hkv
    if _hkv is None:
        _hkv = HierarchicalKV()
    return _hkv
