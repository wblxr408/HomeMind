"""Source-aware local knowledge base for HomeMind RAG."""

from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.config import RAG_CONFIG
from core.rag.semantic_compressor import SemanticCompressor
from core.utils.embedding import encode, get_model

logger = logging.getLogger(__name__)

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)
DEFAULT_COLLECTION_NAME = "homemind_kb"
DEFAULT_BACKUP_PATH = os.getenv("HOMEMIND_KB_BACKUP_PATH", os.path.join(DATA_DIR, "kb_backup.enc"))

CHROMA_AVAILABLE = False
try:
    import chromadb

    CHROMA_AVAILABLE = True
except ImportError:
    pass


class KnowledgeBase:
    """Local KB with source buckets, trust scoring, conflicts, and time-series summaries."""

    DEFAULT_TRUST = {
        "设备说明书": 0.98,
        "场景规则": 0.90,
        "时序摘要": 0.88,
        "用户习惯": 0.72,
        "用户反馈": 0.68,
        "健康建议": 0.60,
        "纠正记录": 0.85,
    }

    CATEGORY_TO_BUCKET = {
        "健康建议": "manuals",
        "场景规则": "rules",
        "用户习惯": "preferences",
        "用户反馈": "feedback",
        "纠正记录": "corrections",
        "时序摘要": "timeseries",
    }

    def __init__(
        self,
        persist_dir: str = os.path.join(DATA_DIR, "chroma_db"),
        embedding_fn=None,
        max_records: int = 500,
        backup_path: str = None,
    ):
        self.persist_dir = persist_dir
        os.makedirs(self.persist_dir, exist_ok=True)
        self.embedding_fn = embedding_fn
        self.max_records = max(1, int(max_records))
        self.backup_path = backup_path or os.getenv("HOMEMIND_KB_BACKUP_PATH", DEFAULT_BACKUP_PATH)
        self.preference_store = None
        self.semantic_compressor = SemanticCompressor()
        self.preset_knowledge = self._init_preset_kb()
        self.memory_store: List[Dict[str, Any]] = []
        self.time_series_store: List[Dict[str, Any]] = []
        self._client = None
        self._collection = None
        self._collection_name = DEFAULT_COLLECTION_NAME
        self._init_chroma()

        from core.security import get_encrypted_storage

        self._storage = get_encrypted_storage()

    def _init_chroma(self) -> None:
        if not CHROMA_AVAILABLE:
            logger.warning("ChromaDB not installed; using in-memory knowledge store")
            return
        try:
            self._client = chromadb.PersistentClient(path=self.persist_dir)
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name,
                metadata={"description": "HomeMind RAG knowledge base"},
            )
            logger.info("ChromaDB initialized: %s", self.persist_dir)
        except Exception as exc:
            logger.warning("ChromaDB init failed: %s; using in-memory store", exc)
            self._client = None
            self._collection = None

    def _init_preset_kb(self) -> List[Dict[str, Any]]:
        raw = [
            ("preset_01", "室内温度超过28°C时，打开空调降温效果最好", "健康建议"),
            ("preset_02", "湿度超过70%时人会感到闷热不适，应开启除湿或制冷", "健康建议"),
            ("preset_03", "晚上22:00后大多数家庭成员进入睡眠，应切换睡眠模式", "场景规则"),
            ("preset_04", "有客人来访时应调亮灯光、调节空调温度至舒适范围、播放背景音乐", "场景规则"),
            ("preset_05", "用户离开家时应关闭所有不必要的电器，节能安全", "场景规则"),
            ("preset_06", "观影模式：灯光调暗至30%以下，空调调至舒适温度，电视开启", "场景规则"),
            ("preset_07", "起床模式：灯光渐亮，窗帘打开，背景音乐轻柔播放", "场景规则"),
            ("preset_08", "夏天室内闷热主要原因是温度和湿度偏高，开空调最有效", "健康建议"),
            ("preset_09", "晚上觉得灯光太亮时应调暗而非直接关闭，以保持基本照明", "健康建议"),
            ("preset_10", "“有点闷”在温度28°C以上时，优先推荐开空调降温", "用户习惯"),
        ]
        return [
            self._normalize_record(
                {
                    "record_id": record_id,
                    "content": content,
                    "category": category,
                    "accepted": True,
                    "source_name": "preset",
                }
            )
            for record_id, content, category in raw
        ]

    def _source_bucket_for(self, category: str, metadata: Dict[str, Any]) -> str:
        explicit = str(metadata.get("source_bucket", "")).strip()
        if explicit:
            return explicit
        return self.CATEGORY_TO_BUCKET.get(str(category or "").strip(), "memory")

    def _trust_for(self, category: str, metadata: Dict[str, Any]) -> float:
        if metadata.get("trust_score") is not None:
            return max(0.0, min(1.0, float(metadata.get("trust_score", 0.0))))
        return self.DEFAULT_TRUST.get(str(category or "").strip(), 0.65)

    def _trust_level(self, score: float) -> str:
        if score >= 0.9:
            return "high"
        if score >= 0.75:
            return "medium"
        return "low"

    def _normalize_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        item = dict(record or {})
        now = datetime.now().isoformat()
        category = str(item.get("category", "用户习惯")).strip() or "用户习惯"
        source_bucket = self._source_bucket_for(category, item)
        trust_score = self._trust_for(category, item)
        normalized = {
            "content": str(item.get("content", "")).strip(),
            "category": category,
            "accepted": bool(item.get("accepted", True)),
            "timestamp": item.get("timestamp") or now,
            "first_seen": item.get("first_seen") or item.get("timestamp") or now,
            "last_seen": item.get("last_seen") or item.get("timestamp") or now,
            "count": int(item.get("count", 1) or 1),
            "value_score": float(item.get("value_score", 1.0) or 1.0),
            "memory_key": str(item.get("memory_key", "") or "").strip(),
            "record_id": str(item.get("record_id", "") or item.get("memory_key") or f"user_{uuid.uuid4().hex}"),
            "source_bucket": source_bucket,
            "source_name": str(item.get("source_name", source_bucket)).strip() or source_bucket,
            "trust_score": trust_score,
            "trust_level": self._trust_level(trust_score),
            "conflict": bool(item.get("conflict", False)),
            "conflict_reason": str(item.get("conflict_reason", "")).strip(),
            **{k: v for k, v in item.items() if k not in {"content", "category", "accepted", "timestamp", "first_seen", "last_seen", "count", "value_score", "memory_key", "record_id", "source_bucket", "source_name", "trust_score", "trust_level", "conflict", "conflict_reason"}},
        }
        return normalized

    def _collection_count(self) -> int:
        if self._collection is None:
            return 0
        try:
            return int(self._collection.count())
        except Exception as exc:
            logger.warning("ChromaDB count failed: %s", exc)
            return 0

    def _get_embedding(self, text: str) -> List[float]:
        emb = self.embedding_fn(text) if self.embedding_fn is not None else encode(text)
        return emb if isinstance(emb, list) else emb.tolist()

    def _as_array(self, emb):
        import numpy as np

        return np.array(emb) if isinstance(emb, list) else emb

    def _upsert_collection_record(self, record: Dict[str, Any]) -> None:
        if self._collection is None:
            return
        try:
            payload = dict(record)
            emb = self._get_embedding(payload["content"])
            if hasattr(self._collection, "upsert"):
                self._collection.upsert(
                    embeddings=[emb],
                    documents=[payload["content"]],
                    metadatas=[payload],
                    ids=[payload["record_id"]],
                )
            else:
                try:
                    self._collection.delete(ids=[payload["record_id"]])
                except Exception:
                    pass
                self._collection.add(
                    embeddings=[emb],
                    documents=[payload["content"]],
                    metadatas=[payload],
                    ids=[payload["record_id"]],
                )
        except Exception as exc:
            logger.warning("ChromaDB add failed for record_id=%s: %s", record.get("record_id"), exc)

    def _rehydrate_collection(self, records: Optional[List[Dict[str, Any]]] = None, clear_existing: bool = False) -> int:
        if self._collection is None:
            return 0
        records = list(records if records is not None else self.memory_store)
        if clear_existing:
            try:
                existing = self._collection.get(include=[])
                ids = list(existing.get("ids", []) or [])
                if ids:
                    self._collection.delete(ids=ids)
            except Exception:
                pass
        restored = 0
        for record in records:
            self._upsert_collection_record(record)
            restored += 1
        return restored

    def _search_pool(self, text: str, pool: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
        model = get_model()
        if model is not None and pool:
            import numpy as np

            texts = [item["content"] for item in pool]
            query_emb = self._as_array(encode(text))
            doc_embs = self._as_array(encode(texts))
            if doc_embs.ndim == 1:
                doc_embs = doc_embs.reshape(1, -1)
            doc_norms = np.linalg.norm(doc_embs, axis=1, keepdims=True)
            doc_norms[doc_norms == 0] = 1.0
            query_norm = np.linalg.norm(query_emb)
            if query_norm != 0:
                sims = np.dot(doc_embs / doc_norms, query_emb / query_norm)
                top_indices = np.argsort(sims)[-top_k:][::-1]
                return [pool[index] for index in top_indices if sims[index] > 0.1]

        scored = []
        lowered = str(text or "").lower()
        ascii_terms = [term for term in re.findall(r"[a-z0-9_]+", lowered) if len(term) >= 2]
        cjk_chars = [char for char in lowered if "\u4e00" <= char <= "\u9fff"]
        for item in pool:
            content_lower = item.get("content", "").lower()
            has_ascii_overlap = any(term in content_lower for term in ascii_terms)
            has_cjk_overlap = any(char in content_lower for char in cjk_chars)
            if (ascii_terms or cjk_chars) and not (has_ascii_overlap or has_cjk_overlap):
                continue

            score = sum(1 for term in ascii_terms if term in content_lower) * 4
            score += sum(1 for char in cjk_chars if char in content_lower)
            score += int(item.get("trust_score", 0.0) * 10)
            if item.get("conflict"):
                score -= 3
            if score > 0:
                scored.append((score, item))
        scored.sort(key=lambda pair: (pair[0], pair[1].get("last_seen", "")), reverse=True)
        return [item for _, item in scored[:top_k]]

    def _selected_pool(
        self,
        category: Optional[str] = None,
        source_buckets: Optional[List[str]] = None,
        min_trust_score: float = 0.0,
        include_conflicted: bool = True,
    ) -> List[Dict[str, Any]]:
        buckets = set(source_buckets or [])
        pool = list(self.memory_store) + list(self.time_series_store) + list(self.preset_knowledge)
        result = []
        for item in pool:
            if category is not None and item.get("category") != category:
                continue
            if buckets and item.get("source_bucket") not in buckets:
                continue
            if float(item.get("trust_score", 0.0) or 0.0) < min_trust_score:
                continue
            if not include_conflicted and item.get("conflict"):
                continue
            result.append(item)
        return result

    def query(
        self,
        text: str,
        top_k: int = 3,
        category: Optional[str] = None,
        source_buckets: Optional[List[str]] = None,
        min_trust_score: float = 0.0,
        include_conflicted: bool = True,
    ) -> List[Dict[str, Any]]:
        pool = self._selected_pool(
            category=category,
            source_buckets=source_buckets,
            min_trust_score=min_trust_score,
            include_conflicted=include_conflicted,
        )
        if not pool:
            return []
        return self._search_pool(text, pool, top_k)

    def _find_memory_record(self, memory_key: str) -> Optional[Dict[str, Any]]:
        if not memory_key:
            return None
        for record in self.memory_store:
            if record.get("memory_key") == memory_key:
                return record
        return None

    def _apply_conflict_detection(self, record: Dict[str, Any]) -> None:
        fact_key = str(record.get("fact_key", "") or "").strip()
        fact_value = record.get("fact_value")
        if not fact_key:
            return
        for existing in self.memory_store:
            if existing.get("fact_key") != fact_key or existing.get("record_id") == record.get("record_id"):
                continue
            if existing.get("fact_value") != fact_value:
                existing["conflict"] = True
                existing["conflict_reason"] = f"fact_key={fact_key} has multiple values"
                record["conflict"] = True
                record["conflict_reason"] = existing["conflict_reason"]

    def _prune_memory_store(self) -> None:
        if len(self.memory_store) <= self.max_records:
            return
        ranked = sorted(
            self.memory_store,
            key=lambda item: (
                float(item.get("trust_score", 0.0) or 0.0),
                float(item.get("value_score", 0.0) or 0.0),
                int(item.get("count", 1) or 1),
                str(item.get("last_seen", item.get("timestamp", "")) or ""),
            ),
            reverse=True,
        )
        kept = ranked[:self.max_records]
        removed = ranked[self.max_records:]
        self.memory_store = sorted(kept, key=lambda item: str(item.get("last_seen", "")))
        if self._collection is not None and removed:
            removed_ids = [item.get("record_id") for item in removed if item.get("record_id")]
            if removed_ids:
                try:
                    self._collection.delete(ids=removed_ids)
                except Exception as exc:
                    logger.warning("ChromaDB prune failed: %s", exc)

    def add(self, content: str, category: str = "用户习惯", accepted: bool = True, **metadata) -> bool:
        memory_key = str(metadata.pop("memory_key", "") or "").strip()
        value_score = float(metadata.pop("value_score", 1.0) or 1.0)
        now = datetime.now().isoformat()
        existing = self._find_memory_record(memory_key)

        if existing is not None:
            existing["content"] = content
            existing["accepted"] = bool(existing.get("accepted", False) or accepted)
            existing["last_seen"] = now
            existing["timestamp"] = now
            existing["count"] = int(existing.get("count", 1) or 1) + 1
            existing["value_score"] = max(float(existing.get("value_score", 0.0) or 0.0), value_score)
            existing.update(metadata)
            existing["trust_score"] = self._trust_for(category, existing)
            existing["trust_level"] = self._trust_level(existing["trust_score"])
            self._apply_conflict_detection(existing)
            self._upsert_collection_record(existing)
            self._prune_memory_store()
            return True

        record = self._normalize_record(
            {
                "content": content,
                "category": category,
                "accepted": accepted,
                "timestamp": now,
                "first_seen": now,
                "last_seen": now,
                "count": 1,
                "value_score": value_score,
                "memory_key": memory_key,
                **metadata,
            }
        )
        self._apply_conflict_detection(record)
        self.memory_store.append(record)
        self._upsert_collection_record(record)
        self._prune_memory_store()
        return True

    def add_timeseries_summary(
        self,
        summary_text: str,
        *,
        source_name: str = "simulator",
        trust_score: float = 0.88,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        now = datetime.now().isoformat()
        record = self._normalize_record(
            {
                "content": summary_text,
                "category": "时序摘要",
                "accepted": True,
                "timestamp": now,
                "first_seen": now,
                "last_seen": now,
                "count": 1,
                "value_score": 1.5,
                "record_id": f"ts_{datetime.now().timestamp()}",
                "source_name": source_name,
                "source_bucket": "timeseries",
                "trust_score": trust_score,
                **(metadata or {}),
            }
        )
        self.time_series_store.append(record)
        if len(self.time_series_store) > 120:
            self.time_series_store = self.time_series_store[-120:]
        return True

    def record_timeseries_summary(self, context, device_states: Optional[Dict[str, Dict[str, Any]]] = None, trigger: str = "", route: str = "") -> bool:
        scene = getattr(context, "current_scene", "") or "无场景"
        temp = getattr(context, "temperature", 0.0)
        humidity = getattr(context, "humidity", 0.0)
        device_parts = []
        for name, state in list((device_states or {}).items())[:4]:
            status = state.get("status", "")
            if status:
                device_parts.append(f"{name}:{status}")
        summary = f"场景={scene} 温度={temp} 湿度={humidity} 触发={trigger or 'query'} 路由={route or 'local'} 设备={'/'.join(device_parts) or '无'}"
        return self.add_timeseries_summary(summary, metadata={"trigger": trigger, "route": route})

    def update_feedback(self, original_query: str, action: str, feedback: str) -> bool:
        feedback_map = {"接受": "positive", "忽略": "neutral", "拒绝": "negative", "纠正": "negative"}
        sentiment = feedback_map.get(feedback, "neutral")
        content = f"用户输入「{original_query}」后执行了「{action}」，用户反馈「{feedback}」"
        self.add(content, category="用户反馈", accepted=(sentiment == "positive"), sentiment=sentiment, feedback=feedback)
        return True

    def get_context_prompt(self, user_query: str, context) -> str:
        retrieved = self.query(
            user_query,
            top_k=RAG_CONFIG["top_k"],
            include_conflicted=False,
            min_trust_score=0.6,
        )
        if not retrieved:
            return ""

        live_context = {
            "content": f"当前环境：时间={getattr(context, 'hour', 0)}点 温度={getattr(context, 'temperature', 0)} 湿度={getattr(context, 'humidity', 0)} 场景={getattr(context, 'current_scene', '') or '无'}",
            "category": "时序摘要",
            "source_bucket": "timeseries",
            "source_name": "live_context",
            "trust_score": 0.92,
        }
        chunks = [live_context] + retrieved
        compressed = self.semantic_compressor.compress(chunks, max_total_chars=RAG_CONFIG["max_context_tokens"] * 4)
        return self.semantic_compressor.to_context_string(compressed)

    def get_user_preference_score(self, candidate_action: str, context) -> float:
        score = 0.5
        if self.preference_store is not None:
            try:
                score = max(score, float(self.preference_store.get_preference_boost(candidate_action, context)))
            except Exception as exc:
                logger.warning("PreferenceStore score lookup failed: %s", exc)

        history = self.query(candidate_action, top_k=5, category="用户习惯")
        if history:
            accepted_count = sum(1 for item in history if item.get("accepted"))
            score = min(1.0, 0.5 + accepted_count * 0.2)

        feedback_history = self.query(candidate_action, top_k=5, category="用户反馈")
        if feedback_history:
            accepted_count = sum(1 for item in feedback_history if item.get("feedback") == "接受" or item.get("accepted"))
            score = max(score, min(1.0, 0.5 + accepted_count * 0.15))
        return score

    def list_conflicts(self) -> List[Dict[str, Any]]:
        return [dict(item) for item in self.memory_store if item.get("conflict")]

    def get_status(self) -> Dict[str, Any]:
        return {
            "chromadb_importable": bool(CHROMA_AVAILABLE),
            "chromadb_enabled": self._collection is not None,
            "collection_name": self._collection_name,
            "persist_dir": self.persist_dir,
            "collection_count": self._collection_count(),
            "memory_store_count": len(self.memory_store),
            "preset_knowledge_count": len(self.preset_knowledge),
            "timeseries_count": len(self.time_series_store),
            "conflict_count": len(self.list_conflicts()),
            "source_buckets": sorted({item.get("source_bucket", "") for item in self.memory_store + self.preset_knowledge + self.time_series_store}),
            "max_records": self.max_records,
        }

    def count(self) -> int:
        return len(self.memory_store) + len(self.preset_knowledge)

    def backup(self, path: str = None) -> bool:
        if path is None:
            path = self.backup_path
        data = {
            "memory_store": self.memory_store,
            "time_series_store": self.time_series_store,
            "timestamp": datetime.now().isoformat(),
        }
        success = self._storage.save_pickle(data, path)
        if success:
            logger.info("Knowledge base encrypted backup written: %s", path)
        return success

    def restore(self, path: str = None) -> bool:
        if path is None:
            path = self.backup_path
        data = self._storage.load_pickle(path)
        if data and "memory_store" in data:
            self.memory_store = [self._normalize_record(item) for item in list(data.get("memory_store", []) or [])]
            self.time_series_store = [self._normalize_record(item) for item in list(data.get("time_series_store", []) or [])]
            self._prune_memory_store()
            restored_to_collection = self._rehydrate_collection(clear_existing=True)
            logger.info("Knowledge base restored, records=%s", len(self.memory_store))
            if self._collection is not None:
                logger.info("Knowledge base ChromaDB sync complete, collection_records=%s", restored_to_collection)
            return True
        logger.warning("Knowledge base restore failed or backup missing: %s", path)
        return False
