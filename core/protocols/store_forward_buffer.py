"""Store-and-Forward 缓冲区。

网络断连时将命令缓存在 SQLite，
恢复后按 sequence 顺序重放，
确保命令不丢失且顺序一致。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)
_buffer_lock = threading.Lock()


class StoreForwardBuffer:
    """离线命令缓冲与重放。"""

    MAX_BUFFER_SIZE = 1000  # 内存缓冲上限，超出后丢弃最旧条目

    def __init__(self, db_path: str = "data/s2f_buffer.db"):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._connected = True
        self._seq = 0
        self._init_db()

    def _init_db(self):
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS command_buffer (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seq INTEGER NOT NULL UNIQUE,
                topic TEXT,
                payload TEXT NOT NULL,
                device TEXT DEFAULT '',
                action TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                replayed_at TEXT
            )
        """)
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON command_buffer(status)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_seq ON command_buffer(seq)")
        self._conn.commit()

        # 恢复上次最大 seq
        try:
            cursor = self._conn.execute("SELECT MAX(seq) FROM command_buffer")
            row = cursor.fetchone()
            self._seq = int(row[0] or 0)
        except Exception:
            self._seq = 0

        logger.info("StoreForwardBuffer: initialized, current seq=%d", self._seq)

    def is_connected(self) -> bool:
        return self._connected

    def set_connected(self, connected: bool):
        """设置连接状态，连接恢复时触发重放。"""
        was_disconnected = not self._connected
        self._connected = connected

        if connected and was_disconnected:
            logger.info("StoreForwardBuffer: connection restored, triggering replay")
            return self.replay_pending()

        if not connected:
            logger.info("StoreForwardBuffer: connection lost, commands will be buffered")

    def push(
        self,
        topic: str,
        payload: dict,
        device: str = "",
        action: str = "",
    ) -> bool:
        """缓存一条命令。断连时写入 SQLite，连接时直接执行。"""
        if self._connected:
            return True  # 连接正常，无需缓冲

        self._seq += 1
        now = datetime.now().astimezone().isoformat()

        try:
            with _buffer_lock:
                self._conn.execute(
                    "INSERT INTO command_buffer (seq, topic, payload, device, action, created_at, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (self._seq, topic, json.dumps(payload, ensure_ascii=False), device, action, now, "pending"),
                )
                self._conn.commit()
            logger.debug("StoreForwardBuffer: buffered seq=%d topic=%s", self._seq, topic)
            return True
        except Exception as exc:
            logger.warning("StoreForwardBuffer: failed to buffer command: %s", exc)
            return False

    def replay_pending(self) -> Dict[str, Any]:
        """重放所有待重发的命令。"""
        if not self._conn:
            return {"replayed": 0, "failed": 0}

        with _buffer_lock:
            cursor = self._conn.execute(
                "SELECT id, seq, topic, payload, device, action FROM command_buffer WHERE status='pending' ORDER BY seq ASC"
            )
            rows = cursor.fetchall()

        replayed = 0
        failed = 0
        for row in rows:
            record_id, seq, topic, payload_str, device, action = row
            payload = json.loads(payload_str)

            try:
                success = self._deliver(topic, payload, device, action)
                with _buffer_lock:
                    if success:
                        self._conn.execute(
                            "UPDATE command_buffer SET status='replayed', replayed_at=? WHERE id=?",
                            (datetime.now().astimezone().isoformat(), record_id),
                        )
                        replayed += 1
                    else:
                        self._conn.execute(
                            "UPDATE command_buffer SET status='failed' WHERE id=?",
                            (record_id,),
                        )
                        failed += 1
                    self._conn.commit()
            except Exception as exc:
                logger.warning("StoreForwardBuffer: replay failed for seq=%d: %s", seq, exc)
                failed += 1

        logger.info("StoreForwardBuffer: replayed=%d failed=%d", replayed, failed)

        # 清理已重发的记录（保留最近 100 条以供审计）
        with _buffer_lock:
            self._conn.execute(
                """
                DELETE FROM command_buffer
                WHERE status IN ('replayed', 'failed')
                AND id NOT IN (
                    SELECT id FROM command_buffer
                    WHERE status IN ('replayed', 'failed')
                    ORDER BY id DESC LIMIT 100
                )
                """
            )
            self._conn.commit()

        return {"replayed": replayed, "failed": failed}

    def _deliver(self, topic: str, payload: dict, device: str, action: str) -> bool:
        """实际发送命令到 MQTT/HA。子类或外部注入实现。"""
        logger.debug("StoreForwardBuffer._deliver: topic=%s device=%s action=%s", topic, device, action)
        return True

    def pending_count(self) -> int:
        """返回待重发命令数量。"""
        if not self._conn:
            return 0
        cursor = self._conn.execute("SELECT COUNT(*) FROM command_buffer WHERE status='pending'")
        return cursor.fetchone()[0]

    def get_pending(self) -> List[Dict]:
        """返回所有待重发的命令（供 UI 显示）。"""
        if not self._conn:
            return []
        cursor = self._conn.execute(
            "SELECT seq, topic, device, action, created_at FROM command_buffer WHERE status='pending' ORDER BY seq ASC"
        )
        return [
            {"seq": r[0], "topic": r[1], "device": r[2], "action": r[3], "created_at": r[4]}
            for r in cursor.fetchall()
        ]

    def clear_all(self):
        """清空所有缓冲命令（慎用）。"""
        if not self._conn:
            return
        with _buffer_lock:
            self._conn.execute("DELETE FROM command_buffer")
            self._conn.commit()
            self._seq = 0
        logger.info("StoreForwardBuffer: cleared all buffered commands")

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
