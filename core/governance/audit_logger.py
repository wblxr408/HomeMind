"""结构化审计日志。

每条 Agent 决策记录完整审计链：
时间戳 / 用户ID / 意图 / 决策理由 / 执行动作 / 结果 / 延迟。
加密存储在 SQLite，90 天自动清理。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import SECURITY_CONFIG, STORAGE_CONFIG
from core.security import get_encrypted_storage

logger = logging.getLogger(__name__)

_audit_lock = threading.Lock()


SENSITIVE_AUDIT_FIELDS = {
    "query",
    "routing_reason",
    "decision_device",
    "decision_scene",
    "decision_params",
    "reasoning",
    "execution_result",
    "validation_errors",
    "error_message",
    "metadata",
}


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
        self._storage = get_encrypted_storage()
        self._encrypt_fields = bool(SECURITY_CONFIG.get("audit_encrypt_fields", True)) and self._storage.is_available()
        self._hash_chain = bool(SECURITY_CONFIG.get("audit_hash_chain", True)) and bool(os.getenv("HOMEMIND_STORAGE_KEY", ""))
        self._hmac_key = self._derive_hmac_key()
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
                    encrypted INTEGER DEFAULT 0,
                    prev_hash TEXT,
                    record_hash TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            self._migrate_schema()
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON audit_log(timestamp)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_trace_id ON audit_log(trace_id)")
            self._conn.commit()
            logger.info("AuditLogger: initialized at %s", self._db_path)
        except Exception as exc:
            logger.error("AuditLogger: failed to init DB: %s", exc)
            self._conn = None

    def _migrate_schema(self) -> None:
        existing = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(audit_log)").fetchall()
        }
        columns = {
            "encrypted": "INTEGER DEFAULT 0",
            "prev_hash": "TEXT",
            "record_hash": "TEXT",
        }
        for name, ddl in columns.items():
            if name not in existing:
                self._conn.execute(f"ALTER TABLE audit_log ADD COLUMN {name} {ddl}")

    def _derive_hmac_key(self) -> bytes:
        material = os.getenv("HOMEMIND_STORAGE_KEY", "").encode("utf-8")
        if not material:
            return b""
        return hmac.new(material, b"HomeMind::AuditHashChain::v1", hashlib.sha256).digest()

    def _encrypt_value(self, value: Any) -> str:
        raw = str(value or "").encode("utf-8")
        encrypted = self._storage.encrypt_data(raw)
        return "enc:" + base64.urlsafe_b64encode(encrypted).decode("ascii")

    def _decrypt_value(self, value: Any) -> str:
        text = str(value or "")
        if not text.startswith("enc:"):
            return text
        if not self._storage.is_available():
            return "[encrypted_unavailable]"
        try:
            encrypted = base64.urlsafe_b64decode(text[4:].encode("ascii"))
            return self._storage.decrypt_data(encrypted).decode("utf-8")
        except Exception as exc:
            logger.warning("AuditLogger: decrypt failed: %s", exc)
            return "[decrypt_failed]"

    def _protect_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        protected = dict(record)
        encrypted = 0
        if self._encrypt_fields:
            for field in SENSITIVE_AUDIT_FIELDS:
                protected[field] = self._encrypt_value(protected.get(field, ""))
            encrypted = 1
        protected["encrypted"] = encrypted
        return protected

    def _latest_hash(self) -> str:
        cursor = self._conn.execute(
            "SELECT record_hash FROM audit_log WHERE record_hash IS NOT NULL AND record_hash != '' ORDER BY id DESC LIMIT 1"
        )
        row = cursor.fetchone()
        return str(row[0] or "") if row else ""

    def _stored_chain_head(self) -> str:
        cursor = self._conn.execute("SELECT value FROM audit_meta WHERE key = 'chain_head'")
        row = cursor.fetchone()
        return str(row[0] or "") if row else ""

    def _store_chain_head(self, record_hash: str) -> None:
        if not record_hash:
            return
        self._conn.execute(
            """
            INSERT INTO audit_meta (key, value)
            VALUES ('chain_head', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (record_hash,),
        )

    def _hash_payload(self, record: Dict[str, Any], prev_hash: str) -> str:
        if not self._hash_chain or not self._hmac_key:
            return ""
        payload = {
            "prev_hash": prev_hash,
            "trace_id": record.get("trace_id", ""),
            "timestamp": record.get("timestamp", ""),
            "user_id": record.get("user_id", ""),
            "query": record.get("query", ""),
            "intent_type": record.get("intent_type", ""),
            "routing_reason": record.get("routing_reason", ""),
            "route": record.get("route", ""),
            "decision_action": record.get("decision_action", ""),
            "decision_device": record.get("decision_device", ""),
            "decision_scene": record.get("decision_scene", ""),
            "decision_params": record.get("decision_params", ""),
            "decision_confidence": record.get("decision_confidence", 0.0),
            "reasoning": record.get("reasoning", ""),
            "execution_result": record.get("execution_result", ""),
            "execution_latency_ms": record.get("execution_latency_ms", 0.0),
            "validation_valid": record.get("validation_valid", 1),
            "validation_errors": record.get("validation_errors", ""),
            "rule_triggered": record.get("rule_triggered", ""),
            "error_message": record.get("error_message", ""),
            "llm_backend": record.get("llm_backend", ""),
            "llm_tokens": record.get("llm_tokens", 0),
            "session_id": record.get("session_id", ""),
            "metadata": record.get("metadata", ""),
            "encrypted": record.get("encrypted", 0),
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hmac.new(self._hmac_key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()

    def _verify_record(self, stored: Dict[str, Any]) -> bool:
        record_hash = str(stored.get("record_hash") or "")
        if not record_hash:
            return False
        prev_hash = str(stored.get("prev_hash") or "")
        return hmac.compare_digest(record_hash, self._hash_payload(stored, prev_hash))

    def status(self) -> Dict[str, Any]:
        return {
            "encrypted_fields": self._encrypt_fields,
            "hash_chain": self._hash_chain,
            "storage": self._storage.status(),
        }

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
        timestamp = datetime.now().astimezone().isoformat()
        record = {
            "trace_id": trace_id,
            "timestamp": timestamp,
            "user_id": user_id,
            "query": query,
            "intent_type": intent_type,
            "routing_reason": routing_reason,
            "route": route,
            "decision_action": decision.get("action", ""),
            "decision_device": decision.get("device", ""),
            "decision_scene": decision.get("scene", ""),
            "decision_params": json.dumps(decision.get("params", {}), ensure_ascii=False),
            "decision_confidence": float(decision.get("confidence", 0.0)),
            "reasoning": decision.get("reasoning", ""),
            "execution_result": execution_result,
            "execution_latency_ms": execution_latency_ms,
            "validation_valid": int(validation.get("valid", True)),
            "validation_errors": json.dumps(validation.get("errors", []), ensure_ascii=False),
            "rule_triggered": rule_triggered,
            "error_message": error,
            "llm_backend": llm_backend,
            "llm_tokens": llm_tokens,
            "session_id": session_id,
            "metadata": json.dumps(metadata or {}, ensure_ascii=False),
        }

        try:
            with _audit_lock:
                protected = self._protect_record(record)
                prev_hash = self._latest_hash()
                record_hash = self._hash_payload(protected, prev_hash)
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
                        llm_backend, llm_tokens, session_id, metadata,
                        encrypted, prev_hash, record_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        protected["trace_id"],
                        protected["timestamp"],
                        protected["user_id"],
                        protected["query"],
                        protected["intent_type"],
                        protected["routing_reason"],
                        protected["route"],
                        protected["decision_action"],
                        protected["decision_device"],
                        protected["decision_scene"],
                        protected["decision_params"],
                        protected["decision_confidence"],
                        protected["reasoning"],
                        protected["execution_result"],
                        protected["execution_latency_ms"],
                        protected["validation_valid"],
                        protected["validation_errors"],
                        protected["rule_triggered"],
                        protected["error_message"],
                        protected["llm_backend"],
                        protected["llm_tokens"],
                        protected["session_id"],
                        protected["metadata"],
                        protected["encrypted"],
                        prev_hash,
                        record_hash,
                    ),
                )
                self._store_chain_head(record_hash)
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
                    SELECT id, trace_id, timestamp, user_id, query, intent_type,
                           routing_reason, route, decision_action, decision_device,
                           decision_scene, decision_params, decision_confidence,
                           reasoning, execution_result, execution_latency_ms,
                           validation_valid, validation_errors, rule_triggered,
                           error_message, llm_backend, llm_tokens, session_id,
                           metadata, encrypted, prev_hash, record_hash
                    FROM audit_log
                    WHERE {where}
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    params,
                )
                cols = [desc[0] for desc in cursor.description]
                records = []
                for row in cursor.fetchall():
                    stored = dict(zip(cols, row))
                    result = dict(stored)
                    encrypted = bool(result.get("encrypted", 0))
                    if encrypted:
                        for field in SENSITIVE_AUDIT_FIELDS:
                            if field in result:
                                result[field] = self._decrypt_value(result[field])
                    result["tamper_verified"] = self._verify_record(stored) if stored.get("record_hash") else False
                    records.append(result)
                return records
        except Exception as exc:
            logger.warning("AuditLogger: query failed: %s", exc)
            return []

    def verify_integrity(self) -> Dict[str, Any]:
        """Verify the append-only HMAC chain across all audit records."""
        if self._conn is None:
            return {"ok": False, "reason": "unavailable", "checked": 0}
        if not self._hash_chain or not self._hmac_key:
            return {"ok": False, "reason": "hash_chain_disabled", "checked": 0}
        cols = [
            "id", "trace_id", "timestamp", "user_id", "query", "intent_type",
            "routing_reason", "route", "decision_action", "decision_device",
            "decision_scene", "decision_params", "decision_confidence",
            "reasoning", "execution_result", "execution_latency_ms",
            "validation_valid", "validation_errors", "rule_triggered",
            "error_message", "llm_backend", "llm_tokens", "session_id",
            "metadata", "encrypted", "prev_hash", "record_hash",
        ]
        try:
            with _audit_lock:
                cursor = self._conn.execute(
                    f"SELECT {', '.join(cols)} FROM audit_log ORDER BY id ASC"
                )
                expected_prev = ""
                checked = 0
                for row in cursor.fetchall():
                    record = dict(zip(cols, row))
                    checked += 1
                    if str(record.get("prev_hash") or "") != expected_prev:
                        return {
                            "ok": False,
                            "reason": "prev_hash_mismatch",
                            "checked": checked,
                            "record_id": record.get("id"),
                        }
                    expected = self._hash_payload(record, expected_prev)
                    actual = str(record.get("record_hash") or "")
                    if not actual or not hmac.compare_digest(actual, expected):
                        return {
                            "ok": False,
                            "reason": "record_hash_mismatch",
                            "checked": checked,
                            "record_id": record.get("id"),
                        }
                    expected_prev = actual
                stored_head = self._stored_chain_head()
                if stored_head and stored_head != expected_prev:
                    return {
                        "ok": False,
                        "reason": "chain_head_mismatch",
                        "checked": checked,
                    }
                return {"ok": True, "reason": "verified", "checked": checked}
        except Exception as exc:
            logger.warning("AuditLogger: integrity check failed: %s", exc)
            return {"ok": False, "reason": f"error:{exc}", "checked": 0}

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
