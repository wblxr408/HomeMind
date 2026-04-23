"""
RAG knowledge base.

Uses ChromaDB when available and falls back to in-memory storage with keyword or
embedding search. It supports BSR history recall and LLM context prompts.
"""

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.utils.embedding import encode, get_model

logger = logging.getLogger(__name__)

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

CHROMA_AVAILABLE = False
try:
    import chromadb

    CHROMA_AVAILABLE = True
except ImportError:
    pass


class KnowledgeBase:
    """Local knowledge base with ChromaDB and in-memory fallback."""

    def __init__(self, persist_dir: str = os.path.join(DATA_DIR, "chroma_db"), embedding_fn=None):
        self.persist_dir = persist_dir
        self.embedding_fn = embedding_fn
        self.preset_knowledge = self._init_preset_kb()
        self.memory_store: List[Dict] = []
        self._client = None
        self._collection = None
        self._init_chroma()

        from core.security import get_encrypted_storage

        self._storage = get_encrypted_storage()

    def _init_chroma(self):
        if not CHROMA_AVAILABLE:
            logger.warning("ChromaDB not installed; using in-memory knowledge store")
            return
        try:
            self._client = chromadb.PersistentClient(path=self.persist_dir)
            self._collection = self._client.get_or_create_collection(
                name="homemind_kb",
                metadata={"description": "HomeMind RAG knowledge base"},
            )
            logger.info("ChromaDB initialized: %s", self.persist_dir)
        except Exception as exc:
            logger.warning("ChromaDB init failed: %s; using in-memory store", exc)
            self._client = None
            self._collection = None

    def _init_preset_kb(self) -> List[Dict]:
        return [
            {"id": "preset_01", "content": "室内温度超过28°C时，打开空调降温效果最好", "category": "健康建议", "accepted": True},
            {"id": "preset_02", "content": "湿度超过70%时人会感到闷热不适，应开启除湿或制冷", "category": "健康建议", "accepted": True},
            {"id": "preset_03", "content": "晚上22:00后大多数家庭成员进入睡眠，应切换睡眠模式", "category": "场景规则", "accepted": True},
            {"id": "preset_04", "content": "有客人来访时应调亮灯光、调节空调温度至舒适范围、播放背景音乐", "category": "场景规则", "accepted": True},
            {"id": "preset_05", "content": "用户离开家时应关闭所有不必要的电器，节能安全", "category": "场景规则", "accepted": True},
            {"id": "preset_06", "content": "观影模式：灯光调暗至30%以下，空调调至舒适温度，电视开启", "category": "场景规则", "accepted": True},
            {"id": "preset_07", "content": "起床模式：灯光渐亮，窗帘打开，背景音乐轻柔播放", "category": "场景规则", "accepted": True},
            {"id": "preset_08", "content": "夏天室内闷热主要原因是温度和湿度偏高，开空调最有效", "category": "健康建议", "accepted": True},
            {"id": "preset_09", "content": "晚上觉得灯光太亮时应调暗而非直接关闭，以保持基本照明", "category": "健康建议", "accepted": True},
            {"id": "preset_10", "content": "\"有点闷\" 在温度28°C以上时，优先推荐开空调降温", "category": "用户习惯", "accepted": True},
        ]

    def query(self, text: str, top_k: int = 3, category: Optional[str] = None) -> List[Dict]:
        results = []

        user_results = self._search_memory(text, top_k, category)
        results.extend(user_results)

        if len(results) < top_k:
            preset_results = self._search_preset(text, top_k - len(results), category)
            results.extend(preset_results)

        return results[:top_k]

    def _search_memory(self, text: str, top_k: int, category: Optional[str] = None) -> List[Dict]:
        if self._collection is not None:
            try:
                emb = self._get_embedding(text)
                results = self._collection.query(query_embeddings=[emb], n_results=top_k)
                docs = results.get("documents", [[]])[0]
                metas = results.get("metadatas", [[]])[0]
                records = [{"content": doc, **meta} for doc, meta in zip(docs, metas)]
                if category is not None:
                    records = [record for record in records if record.get("category") == category]
                return records[:top_k]
            except Exception as exc:
                logger.warning("ChromaDB search failed: %s", exc)

        model = get_model()
        if model is not None:
            return self._vector_search_memory(text, top_k, category)
        return self._keyword_search(self.memory_store, text, top_k, category)

    def _vector_search_memory(self, text: str, top_k: int, category: Optional[str] = None) -> List[Dict]:
        pool = [
            item for item in self.memory_store
            if category is None or item.get("category") == category
        ]
        return self._vector_search_pool(text, pool, top_k)

    def _search_preset(self, text: str, top_k: int, category: Optional[str] = None) -> List[Dict]:
        model = get_model()
        pool = [
            item for item in self.preset_knowledge
            if category is None or item.get("category") == category
        ]
        if model is not None:
            return self._vector_search_pool(text, pool, top_k)
        return self._keyword_search(pool, text, top_k)

    def _vector_search_pool(self, text: str, pool: List[Dict], top_k: int) -> List[Dict]:
        import numpy as np

        if not pool:
            return []
        texts = [item["content"] for item in pool]
        query_emb = self._as_array(encode(text))
        doc_embs = self._as_array(encode(texts))
        if doc_embs.ndim == 1:
            doc_embs = doc_embs.reshape(1, -1)

        doc_norms = np.linalg.norm(doc_embs, axis=1, keepdims=True)
        doc_norms[doc_norms == 0] = 1.0
        query_norm = np.linalg.norm(query_emb)
        if query_norm == 0:
            return []

        sims = np.dot(doc_embs / doc_norms, query_emb / query_norm)
        top_indices = np.argsort(sims)[-top_k:][::-1]
        return [pool[index] for index in top_indices if sims[index] > 0.1]

    def _keyword_search(self, pool: List[Dict], text: str, top_k: int, category: Optional[str] = None) -> List[Dict]:
        scored = []
        text_lower = text.lower()
        for item in pool:
            if category and item.get("category") != category:
                continue
            score = sum(1 for char in text_lower if char in item.get("content", "").lower())
            if score > 0:
                scored.append((score, item))
        scored.sort(key=lambda pair: (pair[0], id(pair[1])), reverse=True)
        return [item for _, item in scored[:top_k]]

    def _get_embedding(self, text: str) -> List[float]:
        if self.embedding_fn is not None:
            emb = self.embedding_fn(text)
        else:
            emb = encode(text)
        if isinstance(emb, list):
            return emb
        return emb.tolist()

    def _as_array(self, emb):
        import numpy as np

        if isinstance(emb, list):
            return np.array(emb)
        return emb

    def add(self, content: str, category: str = "用户习惯", accepted: bool = True, **metadata) -> bool:
        record = {
            "content": content,
            "category": category,
            "accepted": accepted,
            "timestamp": datetime.now().isoformat(),
            **metadata,
        }

        if self._collection is not None:
            try:
                emb = self._get_embedding(content)
                self._collection.add(
                    embeddings=[emb],
                    documents=[content],
                    metadatas=[record],
                    ids=[f"user_{datetime.now().timestamp()}"],
                )
            except Exception as exc:
                logger.warning("ChromaDB add failed: %s", exc)

        self.memory_store.append(record)
        return True

    def update_feedback(self, original_query: str, action: str, feedback: str) -> bool:
        feedback_map = {
            "接受": "positive",
            "忽略": "neutral",
            "拒绝": "negative",
            "纠正": "negative",
        }
        sentiment = feedback_map.get(feedback, "neutral")
        content = f"用户输入「{original_query}」后执行了「{action}」，用户反馈「{feedback}」"
        self.add(content, category="用户反馈", accepted=(sentiment == "positive"), sentiment=sentiment, feedback=feedback)
        logger.info("RAG feedback updated: %s", content)
        return True

    def get_context_prompt(self, user_query: str, context) -> str:
        knowledge = self.query(user_query, top_k=3)
        if not knowledge:
            return ""
        return "\n".join(f"[{item.get('category', '知识')}] {item['content']}" for item in knowledge)

    def get_user_preference_score(self, candidate_action: str, context) -> float:
        score = 0.5
        history = self.query(candidate_action, top_k=5, category="用户习惯")
        if history:
            accepted_count = sum(1 for item in history if item.get("accepted"))
            score = min(1.0, 0.5 + accepted_count * 0.2)

        feedback_history = self.query(candidate_action, top_k=5, category="用户反馈")
        if feedback_history:
            accepted_count = sum(
                1 for item in feedback_history
                if item.get("feedback") == "接受" or item.get("accepted")
            )
            score = max(score, min(1.0, 0.5 + accepted_count * 0.15))
        return score

    def count(self) -> int:
        return len(self.memory_store) + len(self.preset_knowledge)

    def backup(self, path: str = None) -> bool:
        if path is None:
            path = os.path.join(DATA_DIR, "kb_backup.enc")
        data = {
            "memory_store": self.memory_store,
            "timestamp": datetime.now().isoformat(),
        }
        success = self._storage.save_pickle(data, path)
        if success:
            logger.info("Knowledge base encrypted backup written: %s", path)
        return success

    def restore(self, path: str = None) -> bool:
        if path is None:
            path = os.path.join(DATA_DIR, "kb_backup.enc")
        data = self._storage.load_pickle(path)
        if data and "memory_store" in data:
            self.memory_store = data["memory_store"]
            logger.info("Knowledge base restored, records=%s", len(self.memory_store))
            return True
        logger.warning("Knowledge base restore failed or backup missing: %s", path)
        return False
