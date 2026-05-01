"""Five-layer memory system with SQLite-backed episodic search."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)
_memory_lock = threading.Lock()


@dataclass
class MemoryEntry:
    layer: str
    key: str
    value: Any
    confidence: float = 1.0
    created_at: str = ""
    last_accessed: str = ""
    expires_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class HierarchicalMemory:
    LAYER_TTL = {
        "core": 0,
        "learned": 30,
        "episodic": 7,
        "working": 0,
        "procedural": 180,
    }

    def __init__(self, db_path: str = "data/memory.db"):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._working: Dict[str, MemoryEntry] = {}
        self._decay_enabled = True
        self._init_sqlite()

    def _init_sqlite(self) -> None:
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(
            """
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
            CREATE INDEX IF NOT EXISTS idx_memory_layer ON memory(layer);
            CREATE INDEX IF NOT EXISTS idx_memory_key ON memory(mkey);
            DROP TABLE IF EXISTS episodic_fts;
            CREATE VIRTUAL TABLE episodic_fts USING fts5(
                mkey,
                content,
                created_at UNINDEXED,
                metadata
            );
            """
        )
        self._conn.commit()
        logger.info("HierarchicalMemory: initialized at %s", self._db_path)

    def read(self, key: str, layer: str = "") -> Optional[Any]:
        if layer == "working":
            entry = self._working.get(key)
            return self._entry_value(entry) if entry else None

        if layer in ("core", "learned", "episodic", "procedural"):
            entry = self._read_sqlite(key, layer)
            if entry:
                self._touch(key, layer)
            return self._entry_value(entry)

        for priority_layer in ("working", "learned", "episodic", "core"):
            entry = self._working.get(key) if priority_layer == "working" else self._read_sqlite(key, priority_layer)
            if entry:
                if priority_layer != "working":
                    self._touch(key, priority_layer)
                return self._entry_value(entry)
        return None

    def write(
        self,
        key: str,
        value: Any,
        layer: str = "learned",
        ttl_days: int = None,
        confidence: float = 1.0,
        metadata: dict = None,
    ) -> None:
        now = datetime.now().astimezone().isoformat()
        expires_at = ""
        if ttl_days and ttl_days > 0:
            expires_at = (datetime.now().astimezone() + timedelta(days=ttl_days)).isoformat()
        elif layer in self.LAYER_TTL and self.LAYER_TTL[layer] > 0:
            expires_at = (datetime.now().astimezone() + timedelta(days=self.LAYER_TTL[layer])).isoformat()

        entry = MemoryEntry(
            layer=layer,
            key=key,
            value=value,
            confidence=confidence,
            created_at=now,
            last_accessed=now,
            expires_at=expires_at,
            metadata=dict(metadata or {}),
        )

        if layer == "working":
            self._working[key] = entry
            return

        with _memory_lock:
            self._conn.execute(
                """
                INSERT INTO memory (layer, mkey, value, confidence, created_at, last_accessed, expires_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(layer, mkey) DO UPDATE SET
                    value = excluded.value,
                    confidence = excluded.confidence,
                    created_at = excluded.created_at,
                    last_accessed = excluded.last_accessed,
                    expires_at = excluded.expires_at,
                    metadata = excluded.metadata
                """,
                (
                    layer,
                    key,
                    json.dumps(value, ensure_ascii=False),
                    confidence,
                    now,
                    now,
                    expires_at,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            if layer == "episodic":
                self._sync_episodic_fts(key, value, now, metadata or {})
            self._conn.commit()

        if layer == "episodic":
            self._maybe_summarize()

    def delete(self, key: str, layer: str = "") -> None:
        if layer == "working":
            self._working.pop(key, None)
            return
        if not layer:
            return
        with _memory_lock:
            self._conn.execute("DELETE FROM memory WHERE layer=? AND mkey=?", (layer, key))
            if layer == "episodic":
                self._conn.execute("DELETE FROM episodic_fts WHERE mkey=?", (key,))
            self._conn.commit()

    def search(self, query: str, layers: List[str] = None, top_k: int = 5) -> List[Dict[str, Any]]:
        layers = list(layers or ["episodic", "learned", "core"])
        results: List[Dict[str, Any]] = []
        if not query or not self._conn:
            return results

        if "episodic" in layers:
            try:
                cursor = self._conn.execute(
                    """
                    SELECT m.mkey, m.value, m.confidence, m.created_at, m.metadata
                    FROM episodic_fts f
                    JOIN memory m ON m.mkey = f.mkey AND m.layer='episodic'
                    WHERE episodic_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (query, top_k),
                )
                for row in cursor.fetchall():
                    results.append(
                        {
                            "layer": "episodic",
                            "key": row[0],
                            "value": json.loads(row[1]),
                            "confidence": row[2],
                            "timestamp": row[3],
                            "metadata": json.loads(row[4] or "{}"),
                        }
                    )
            except Exception as exc:
                logger.warning("HierarchicalMemory FTS search failed: %s", exc)

        if len(results) < top_k:
            like = f"%{query}%"
            cursor = self._conn.execute(
                """
                SELECT layer, mkey, value, confidence, created_at, metadata
                FROM memory
                WHERE layer IN ({})
                  AND (value LIKE ? OR mkey LIKE ? OR metadata LIKE ?)
                ORDER BY confidence DESC, created_at DESC
                LIMIT ?
                """.format(",".join("?" for _ in layers)),
                [*layers, like, like, like, top_k - len(results)],
            )
            seen = {(item["layer"], item["key"]) for item in results}
            for row in cursor.fetchall():
                key = (row[0], row[1])
                if key in seen:
                    continue
                results.append(
                    {
                        "layer": row[0],
                        "key": row[1],
                        "value": json.loads(row[2]),
                        "confidence": row[3],
                        "timestamp": row[4],
                        "metadata": json.loads(row[5] or "{}"),
                    }
                )
                if len(results) >= top_k:
                    break
        return results

    def clear_working(self) -> None:
        self._working.clear()

    def _read_sqlite(self, key: str, layer: str) -> Optional[MemoryEntry]:
        try:
            cursor = self._conn.execute(
                "SELECT value, confidence, created_at, last_accessed, expires_at, metadata FROM memory WHERE layer=? AND mkey=?",
                (layer, key),
            )
            row = cursor.fetchone()
            if row:
                return MemoryEntry(
                    layer=layer,
                    key=key,
                    value=json.loads(row[0]),
                    confidence=row[1],
                    created_at=row[2],
                    last_accessed=row[3] or row[2],
                    expires_at=row[4] or "",
                    metadata=json.loads(row[5] or "{}"),
                )
        except Exception as exc:
            logger.warning("HierarchicalMemory read failed for %s/%s: %s", layer, key, exc)
        return None

    def _touch(self, key: str, layer: str) -> None:
        now = datetime.now().astimezone().isoformat()
        with _memory_lock:
            self._conn.execute(
                "UPDATE memory SET last_accessed=? WHERE layer=? AND mkey=?",
                (now, layer, key),
            )
            self._conn.commit()

    def _sync_episodic_fts(self, key: str, value: Any, created_at: str, metadata: Dict[str, Any]) -> None:
        payload = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        self._conn.execute("DELETE FROM episodic_fts WHERE mkey=?", (key,))
        self._conn.execute(
            "INSERT INTO episodic_fts (mkey, content, created_at, metadata) VALUES (?, ?, ?, ?)",
            (key, payload, created_at, json.dumps(metadata or {}, ensure_ascii=False)),
        )

    def _entry_value(self, entry: Optional[MemoryEntry]) -> Any:
        if entry is None:
            return None
        if entry.expires_at:
            try:
                if datetime.now().astimezone() > datetime.fromisoformat(entry.expires_at):
                    self.delete(entry.key, entry.layer)
                    return None
            except Exception:
                pass
        return deepcopy(entry.value)

    def _maybe_summarize(self) -> None:
        cursor = self._conn.execute("SELECT COUNT(*) FROM memory WHERE layer='episodic'")
        count = int(cursor.fetchone()[0])
        if count > 100:
            self._summarize_episodic()

    def _summarize_episodic(self) -> None:
        with _memory_lock:
            cursor = self._conn.execute(
                "SELECT id, mkey, value FROM memory WHERE layer='episodic' ORDER BY last_accessed DESC LIMIT 100"
            )
            rows = cursor.fetchall()
            if len(rows) <= 5:
                return
            keep = rows[:5]
            summary_value = {
                "type": "episodic_summary",
                "summarized_count": len(rows) - len(keep),
                "summarized_at": datetime.now().astimezone().isoformat(),
                "keys": [row[1] for row in rows[5:]],
            }
            keep_ids = [str(row[0]) for row in keep]
            self._conn.execute(
                "DELETE FROM memory WHERE layer='episodic' AND id NOT IN ({})".format(",".join("?" for _ in keep_ids)),
                keep_ids,
            )
            self._conn.execute("DELETE FROM episodic_fts")
            for row in keep:
                self._sync_episodic_fts(row[1], json.loads(row[2]), datetime.now().astimezone().isoformat(), {})
            summary_key = f"summary_{int(time.time())}"
            now = datetime.now().astimezone().isoformat()
            self._conn.execute(
                """
                INSERT INTO memory (layer, mkey, value, confidence, created_at, last_accessed, expires_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "episodic",
                    summary_key,
                    json.dumps(summary_value, ensure_ascii=False),
                    0.5,
                    now,
                    now,
                    "",
                    json.dumps({"auto_generated": True}, ensure_ascii=False),
                ),
            )
            self._sync_episodic_fts(summary_key, summary_value, now, {"auto_generated": True})
            self._conn.commit()

    def apply_decay(self) -> int:
        if not self._conn or not self._decay_enabled:
            return 0
        cutoff = (datetime.now().astimezone() - timedelta(days=self.LAYER_TTL["learned"])).isoformat()
        with _memory_lock:
            cursor = self._conn.execute(
                "SELECT COUNT(*) FROM memory WHERE layer='learned' AND last_accessed < ?",
                (cutoff,),
            )
            count = int(cursor.fetchone()[0])
            if count:
                self._conn.execute(
                    "UPDATE memory SET confidence = confidence * 0.5 WHERE layer='learned' AND last_accessed < ?",
                    (cutoff,),
                )
                self._conn.commit()
            return count

    def get_status(self) -> Dict[str, Any]:
        status = {"working": len(self._working)}
        if self._conn:
            for layer in ("core", "learned", "episodic", "procedural"):
                cursor = self._conn.execute("SELECT COUNT(*) FROM memory WHERE layer=?", (layer,))
                status[layer] = int(cursor.fetchone()[0])
        return status

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
