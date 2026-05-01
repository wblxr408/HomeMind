"""结构化审计日志。

每条 Agent 决策记录完整审计链：
时间戳 / 用户ID / 意图 / 决策理由 / 执行动作 / 结果 / 延迟。
加密存储在 SQLite，90 天自动清理。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import SECURITY_CONFIG, STORAGE_CONFIG

logger = logging.getLogger(__name__)

_audit_lock = threading.Lock()


class AuditLogger:
    """Agent 决策审计日志记录器。"""

    def __init__(
        self,
        db_path: str = None,
        retention_days: int = None,
    ):
        self._db_path = Path(db_path or os.path.join(STORAGE_CONFIG["data_dir"], "audit.db"))
        self._retention_days = retention_days or SECURITY_CONFIG["audit_retention_days"]
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self):
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT,
                    timestamp TEXT NOT NULL,
                    user_id TEXT DEFAULT 'default',
                    query TEXT,
                    intent_type TEXT,
                    routing_reason TEXT,
                    route TEXT,
                    decision_action TEXT,
                    decision_device TEXT,
                    decision_scene TEXT,
                    decision_params TEXT,
                    decision_confidence REAL,
                    reasoning TEXT,
                    execution_result TEXT,
                    execution_latency_ms REAL,
                    validation_valid INTEGER,
                    validation_errors TEXT,
                    rule_triggered TEXT,
                    error_message TEXT,
                    llm_backend TEXT,
                    llm_tokens INTEGER DEFAULT 0,
                    session_id TEXT,
                    metadata TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON audit_log(timestamp)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_trace_id ON audit_log(trace_id)")
            self._conn.commit()
            logger.info("AuditLogger: initialized at %s", self._db_path)
        except Exception as exc:
            logger.error("AuditLogger: failed to init DB: %s", exc)
            self._conn = None

    def log(
        self,
        trace_id: str = "",
        user_id: str = "default",
        query: str = "",
        intent_type: str = "",
        routing_reason: str = "",
        route: str = "",
        decision: Dict[str, Any] = None,
        execution_result: str = "",
        execution_latency_ms: float = 0.0,
        validation: Dict[str, Any] = None,
        rule_triggered: str = "",
        error: str = "",
        llm_backend: str = "",
        llm_tokens: int = 0,
        session_id: str = "",
        metadata: Dict[str, Any] = None,
    ) -> bool:
        """记录一条完整的审计事件。"""
        if self._conn is None:
            return False

        decision = decision or {}
        validation = validation or {}

        try:
            with _audit_lock:
                self._conn.execute(
                    """
                    INSERT INTO audit_log (
                        trace_id, timestamp, user_id, query,
                        intent_type, routing_reason, route,
                        decision_action, decision_device, decision_scene,
                        decision_params, decision_confidence, reasoning,
                        execution_result, execution_latency_ms,
                        validation_valid, validation_errors,
                        rule_triggered, error_message,
                        llm_backend, llm_tokens, session_id, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trace_id,
                        datetime.now().astimezone().isoformat(),
                        user_id,
                        query,
                        intent_type,
                        routing_reason,
                        route,
                        decision.get("action", ""),
                        decision.get("device", ""),
                        decision.get("scene", ""),
                        json.dumps(decision.get("params", {}), ensure_ascii=False),
                        float(decision.get("confidence", 0.0)),
                        decision.get("reasoning", ""),
                        execution_result,
                        execution_latency_ms,
                        int(validation.get("valid", True)),
                        json.dumps(validation.get("errors", []), ensure_ascii=False),
                        rule_triggered,
                        error,
                        llm_backend,
                        llm_tokens,
                        session_id,
                        json.dumps(metadata or {}, ensure_ascii=False),
                    ),
                )
                self._conn.commit()
            return True
        except Exception as exc:
            logger.warning("AuditLogger: insert failed: %s", exc)
            return False

    def query(
        self,
        since: datetime = None,
        until: datetime = None,
        user_id: str = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """查询审计记录。"""
        if self._conn is None:
            return []

        conditions = []
        params = []

        if since:
            conditions.append("timestamp >= ?")
            params.append(since.astimezone().isoformat())
        if until:
            conditions.append("timestamp <= ?")
            params.append(until.astimezone().isoformat())
        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)

        where = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)

        try:
            with _audit_lock:
                cursor = self._conn.execute(
                    f"""
                    SELECT trace_id, timestamp, user_id, query, intent_type,
                           routing_reason, route, decision_action, decision_device,
                           decision_scene, decision_params, decision_confidence,
                           reasoning, execution_result, execution_latency_ms,
                           validation_valid, validation_errors, rule_triggered,
                           error_message, llm_backend, llm_tokens, session_id
                    FROM audit_log
                    WHERE {where}
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    params,
                )
                cols = [desc[0] for desc in cursor.description]
                return [dict(zip(cols, row)) for row in cursor.fetchall()]
        except Exception as exc:
            logger.warning("AuditLogger: query failed: %s", exc)
            return []

    def prune(self) -> int:
        """删除超过 retention_days 的旧记录。"""
        if self._conn is None:
            return 0

        cutoff = (datetime.now() - timedelta(days=self._retention_days)).astimezone().isoformat()
        try:
            with _audit_lock:
                cursor = self._conn.execute(
                    "DELETE FROM audit_log WHERE timestamp < ?",
                    (cutoff,),
                )
                self._conn.commit()
                deleted = cursor.rowcount
                if deleted > 0:
                    logger.info("AuditLogger: pruned %d old records (before %s)", deleted, cutoff)
                return deleted
        except Exception as exc:
            logger.warning("AuditLogger: prune failed: %s", exc)
            return 0

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
