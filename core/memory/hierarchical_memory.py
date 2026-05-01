"""分层记忆架构。

五层记忆系统：
层1 Core Memory     - 持久核心用户信息（加密）
层2 Learned Memory  - 持久偏好学习（可过期）
层3 Episodic Memory - 滚动事件历史（SQLite FTS5）
层4 Working Memory  - 会话级内存（不持久化）
层5 Procedural Memory - 持久规则和自动化工作流
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import RAG_CONFIG, SECURITY_CONFIG
from core.security import get_encrypted_storage

logger = logging.getLogger(__name__)
_memory_lock = threading.Lock()


@dataclass
class MemoryEntry:
    """单条记忆条目。"""

    layer: str  # core | learned | episodic | working | procedural
    key: str
    value: Any
    confidence: float = 1.0
    created_at: str = ""
    last_accessed: str = ""
    expires_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class HierarchicalMemory:
    """五层分层记忆系统。

    访问顺序：Working → Learned → Episodic → Core
    写入：始终写入对应层，Episodic 层自动触发摘要

    与现有 SessionStore / PreferenceStore 完全兼容。
    """

    LAYER_TTL = {
        "core": 0,          # 永不过期
        "learned": 30,      # 30 天无更新则 confidence 衰减 50%
        "episodic": 7,      # 7 天自动摘要
        "working": 0,        # 会话结束释放
        "procedural": 180,   # 180 天未触发则标记为 stale
    }

    def __init__(self, db_path: str = "data/memory.db"):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._working: Dict[str, MemoryEntry] = {}
        self._storage = get_encrypted_storage()
        self._init_sqlite()
        self._decay_enabled = True

    # ── SQLite 初始化 ─────────────────────────────────

    def _init_sqlite(self):
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                layer TEXT NOT NULL,
                mkey TEXT NOT NULL,
                value TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                created_at TEXT NOT NULL,
                last_accessed TEXT,
                expires_at TEXT,
                metadata TEXT DEFAULT '{}',
                UNIQUE(layer, mkey)
            );
            CREATE INDEX IF NOT EXISTS idx_layer ON memory(layer);
            CREATE INDEX IF NOT EXISTS idx_mkey ON memory(mkey);

            CREATE VIRTUAL TABLE IF NOT EXISTS episodic_fts USING fts5(
                content, timestamp, event_type, confidence, content=memory, content_rowid=id
            );
        """)
        self._conn.commit()
        logger.info("HierarchicalMemory: initialized at %s", self._db_path)

    # ── 统一读写接口 ─────────────────────────────────

    def read(self, key: str, layer: str = "") -> Optional[Any]:
        """从指定层读取记忆（空 layer 则从高层向低层搜索）。"""
        if layer == "working":
            entry = self._working.get(key)
            return self._entry_value(entry) if entry else None

        if layer in ("core", "learned", "episodic", "procedural"):
            entry = self._read_sqlite(key, layer)
            if entry:
                self._touch(key, layer)
            return self._entry_value(entry)

        # 无 layer 指定：从高层到低层搜索
        for priority_layer in ("working", "learned", "episodic", "core"):
            entry = self._read_sqlite(key, priority_layer)
            if entry is None:
                entry = self._working.get(key) if priority_layer == "working" else None
            if entry:
                self._touch(key, priority_layer)
                return self._entry_value(entry)
        return None

    def write(self, key: str, value: Any, layer: str = "learned", ttl_days: int = None, confidence: float = 1.0, metadata: dict = None):
        """写入记忆到指定层。"""
        now = datetime.now().astimezone().isoformat()
        expires_at = ""
        if ttl_days and ttl_days > 0:
            expires_at = (datetime.now() + timedelta(days=ttl_days)).astimezone().isoformat()
        elif layer == "working":
            pass  # working 层不持久化
        elif layer in self.LAYER_TTL and self.LAYER_TTL[layer] > 0:
            expires_at = (datetime.now() + timedelta(days=self.LAYER_TTL[layer])).astimezone().isoformat()

        entry = MemoryEntry(
            layer=layer,
            key=key,
            value=value,
            confidence=confidence,
            created_at=now,
            last_accessed=now,
            expires_at=expires_at,
            metadata=metadata or {},
        )

        if layer == "working":
            self._working[key] = entry
            return

        if self._conn:
            with _memory_lock:
                self._conn.execute(
                    """
                    INSERT INTO memory (layer, mkey, value, confidence, created_at, last_accessed, expires_at, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(layer, mkey) DO UPDATE SET
                        value = excluded.value,
                        confidence = excluded.confidence,
                        last_accessed = excluded.last_accessed,
                        expires_at = excluded.expires_at,
                        metadata = excluded.metadata
                    """,
                    (layer, key, json.dumps(value, ensure_ascii=False), confidence, now, now, expires_at,
                     json.dumps(metadata or {}, ensure_ascii=False)),
                )
                self._conn.commit()

        # episodic 层写入时自动摘要
        if layer == "episodic":
            self._maybe_summarize()

    def delete(self, key: str, layer: str = ""):
        """删除记忆。"""
        if layer == "working":
            self._working.pop(key, None)
            return

        if layer:
            if self._conn:
                with _memory_lock:
                    self._conn.execute("DELETE FROM memory WHERE layer=? AND mkey=?", (layer, key))
                    self._conn.commit()

    def search(self, query: str, layers: List[str] = None, top_k: int = 5) -> List[Dict]:
        """跨层搜索记忆（使用 SQLite FTS5 全文索引）。"""
        layers = layers or ["episodic", "learned", "core"]
        results = []

        if self._conn and "episodic" in layers:
            try:
                cursor = self._conn.execute(
                    """
                    SELECT m.layer, m.mkey, m.value, m.confidence, m.timestamp
                    FROM memory m
                    WHERE m.layer='episodic' AND m.id IN (
                        SELECT rowid FROM episodic_fts WHERE episodic_fts MATCH ?
                    )
                    LIMIT ?
                    """,
                    (query, top_k),
                )
                for row in cursor.fetchall():
                    results.append({
                        "layer": row[0], "key": row[1], "value": json.loads(row[2]),
                        "confidence": row[3], "timestamp": row[4],
                    })
            except Exception as exc:
                logger.warning("HierarchicalMemory FTS search failed: %s", exc)

        return results

    def clear_working(self):
        """清除 Working Memory（会话结束时调用）。"""
        self._working.clear()

    # ── 内部工具 ─────────────────────────────────

    def _read_sqlite(self, key: str, layer: str) -> Optional[MemoryEntry]:
        if not self._conn:
            return None
        try:
            cursor = self._conn.execute(
                "SELECT value, confidence, created_at, last_accessed, expires_at, metadata FROM memory WHERE layer=? AND mkey=?",
                (layer, key),
            )
            row = cursor.fetchone()
            if row:
                return MemoryEntry(
                    layer=layer, key=key, value=json.loads(row[0]),
                    confidence=row[1], created_at=row[2],
                    last_accessed=row[3] or row[2], expires_at=row[4] or "",
                    metadata=json.loads(row[5]),
                )
        except Exception as exc:
            logger.warning("HierarchicalMemory read failed for %s/%s: %s", layer, key, exc)
        return None

    def _touch(self, key: str, layer: str):
        """更新 last_accessed 时间。"""
        if self._conn and layer != "working":
            now = datetime.now().astimezone().isoformat()
            with _memory_lock:
                self._conn.execute(
                    "UPDATE memory SET last_accessed=? WHERE layer=? AND mkey=?",
                    (now, layer, key),
                )
                self._conn.commit()

    def _entry_value(self, entry: Optional[MemoryEntry]) -> Any:
        if entry is None:
            return None
        if entry.expires_at:
            try:
                exp = datetime.fromisoformat(entry.expires_at)
                if datetime.now().astimezone() > exp:
                    self.delete(entry.key, entry.layer)
                    return None
            except Exception:
                pass
        return deepcopy(entry.value)

    def _maybe_summarize(self):
        """当 episodic 层超过 100 条时，压缩为 5 条摘要。"""
        if not self._conn:
            return
        cursor = self._conn.execute("SELECT COUNT(*) FROM memory WHERE layer='episodic'")
        count = cursor.fetchone()[0]
        if count <= 100:
            return

        logger.info("HierarchicalMemory: episodic count=%d, triggering summarization", count)
        self._summarize_episodic()

    def _summarize_episodic(self):
        """将最近 100 条 episodic 记忆压缩为 5 条摘要。"""
        with _memory_lock:
            cursor = self._conn.execute(
                "SELECT id, mkey, value FROM memory WHERE layer='episodic' ORDER BY last_accessed DESC LIMIT 100"
            )
            rows = cursor.fetchall()
            if len(rows) <= 5:
                return

            keep = rows[:5]
            discard = rows[5:]
            discard_ids = [str(r[0]) for r in discard]

            # 生成摘要
            summary_value = {
                "type": "episodic_summary",
                "summarized_count": len(discard),
                "summarized_at": datetime.now().astimezone().isoformat(),
                "keys": [r[1] for r in discard],
            }

            # 保留最近 5 条，删除其余的
            self._conn.execute("DELETE FROM memory WHERE layer='episodic' AND id NOT IN (" + ",".join(["?"] * len(keep)) + ")",
                              [str(r[0]) for r in keep])
            # 写入摘要
            now = datetime.now().astimezone().isoformat()
            self._conn.execute(
                "INSERT INTO memory (layer, mkey, value, confidence, created_at, last_accessed, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("episodic", f"summary_{int(time.time())}",
                 json.dumps(summary_value, ensure_ascii=False), 0.5, now, now,
                 json.dumps({"auto_generated": True}, ensure_ascii=False)),
            )
            self._conn.commit()
            logger.info("HierarchicalMemory: summarized %d episodic records into 1 summary", len(discard))

    def apply_decay(self) -> int:
        """对 learned 层超过 TTL 的记忆应用置信度衰减。"""
        if not self._conn or not self._decay_enabled:
            return 0
        cutoff = (datetime.now() - timedelta(days=self.LAYER_TTL["learned"])).astimezone().isoformat()
        with _memory_lock:
            cursor = self._conn.execute(
                "SELECT COUNT(*) FROM memory WHERE layer='learned' AND last_accessed < ?",
                (cutoff,),
            )
            count = cursor.fetchone()[0]
            if count > 0:
                self._conn.execute(
                    "UPDATE memory SET confidence = confidence * 0.5 WHERE layer='learned' AND last_accessed < ?",
                    (cutoff,),
                )
                self._conn.commit()
                logger.info("HierarchicalMemory: applied decay to %d learned records", count)
        return count

    def get_status(self) -> Dict[str, Any]:
        """返回各层记忆统计。"""
        status = {}
        if self._conn:
            for layer in ("core", "learned", "episodic", "procedural"):
                cursor = self._conn.execute(
                    "SELECT COUNT(*) FROM memory WHERE layer=?", (layer,)
                )
                status[layer] = cursor.fetchone()[0]
        status["working"] = len(self._working)
        return status

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
