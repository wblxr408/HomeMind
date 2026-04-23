"""
RAG 知识库模块
ChromaDB 本地向量数据库 + all-MiniLM-L6-v2
承担双重角色：
  1. BSR 层的历史召回（query）
  2. LLM 决策的上下文增强（get_context_prompt）
"""

import os
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from core.utils.embedding import get_model, encode

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
    """
    本地知识库，支持 ChromaDB 和内存回退两种模式
    知识来源：
      - 预置知识库（冷启动用）
      - 用户积累知识库（持续更新）
    """

    def __init__(self, persist_dir: str = os.path.join(DATA_DIR, "chroma_db"), embedding_fn=None):
        self.persist_dir = persist_dir
        self.embedding_fn = embedding_fn
        self.preset_knowledge = self._init_preset_kb()
        self.memory_store: List[Dict] = []
        self._client = None
        self._collection = None
        self._init_chroma()
        
        # 延迟导入加密存储（避免循环导入）
        from core.security import get_encrypted_storage
        self._storage = get_encrypted_storage()

    def _init_chroma(self):
        if not CHROMA_AVAILABLE:
            logger.warning("ChromaDB 未安装，使用内存存储模式")
            return
        try:
            self._client = chromadb.PersistentClient(path=self.persist_dir)
            self._collection = self._client.get_or_create_collection(
                name="homemind_kb",
                metadata={"description": "HomeMind RAG knowledge base"}
            )
            logger.info(f"ChromaDB 初始化成功: {self.persist_dir}")
        except Exception as e:
            logger.warning(f"ChromaDB 初始化失败: {e}，使用内存存储模式")
            self._client = None
            self._collection = None

    def _init_preset_kb(self) -> List[Dict]:
        ""预置知识库：家用电器使用常识、健康建议、家庭常见场景规则""
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
        ""检索相关知识，优先用户积累 > 预置知识""
        results = []

        user_results = self._search_memory(text, top_k)
        results.extend(user_results)

        if len(results) < top_k:
            preset_results = self._search_preset(text, top_k - len(results), category)
            results.extend(preset_results)

        return results[:top_k]

    def _search_memory(self, text: str, top_k: int) -> List[Dict]:
        ""在用户积累知识中检索""
        if self._collection is not None:
            try:
                emb = self._get_embedding(text)
                results = self._collection.query(query_embeddings=[emb], n_results=top_k)
                docs = results.get("documents", [[]])[0]
                metas = results.get("metadatas", [[]])[0]
                return [{"content": d, **m} for d, m in zip(docs, metas)]
            except Exception as e:
                logger.warning(f"ChromaDB 检索失败: {e}")

        model = get_model()
        if model is not None:
            return self._vector_search_memory(text, top_k, model)
        return self._keyword_search(self.memory_store, text, top_k)

    def _vector_search_memory(self, text: str, top_k: int, model) -> List[Dict]:
        ""基于 MiniLM 向量相似度搜索用户积累""
        import numpy as np
        if not self.memory_store:
            return []
        texts = [item["content"] for item in self.memory_store]
        query_emb = encode(text)
        if isinstance(query_emb, list):
            query_emb = np.array(query_emb)
        doc_embs = encode(texts)
        sims = np.dot(doc_embs, query_emb)
        top_indices = np.argsort(sims)[-top_k:][::-1]
        return [self.memory_store[i] for i in top_indices if sims[i] > 0.1]

    def _search_preset(self, text: str, top_k: int, category: Optional[str] = None) -> List[Dict]:
        ""在预置知识库中检索""
        model = get_model()
        if model is not None:
            return self._vector_search_preset(text, top_k, category, model)
        return self._keyword_search(self.preset_knowledge, text, top_k, category)

    def _vector_search_preset(self, text: str, top_k: int, category: Optional[str], model) -> List[Dict]:
        ""基于 MiniLM 向量相似度搜索预置知识""
        import numpy as np
        pool = [item for item in self.preset_knowledge
                if category is None or item.get("category") == category]
        if not pool:
            return []
        texts = [item["content"] for item in pool]
        query_emb = encode(text)
        if isinstance(query_emb, list):
            query_emb = np.array(query_emb)
        doc_embs = encode(texts)
        sims = np.dot(doc_embs, query_emb)
        top_indices = np.argsort(sims)[-top_k:][::-1]
        return [pool[i] for i in top_indices if sims[i] > 0.1]

    def _keyword_search(self, pool: List[Dict], text: str, top_k: int,
                        category: Optional[str] = None) -> List[Dict]:
        ""关键词精确匹配兜底搜索""
        scored = []
        text_lower = text.lower()
        for item in pool:
            if category and item.get("category") != category:
                continue
            score = sum(1 for kw in text_lower if kw in item.get("content", "").lower())
            if score > 0:
                scored.append((score, item))
        scored.sort(key=lambda x: (x[0], id(x[1])), reverse=True)
        return [item for _, item in scored[:top_k]]

    def _get_embedding(self, text: str) -> List[float]:
        ""获取文本向量（使用统一 Embedding 服务）""
        emb = encode(text)
        if isinstance(emb, list):
            return emb
        return emb.tolist()

    def add(self, content: str, category: str = "用户习惯", accepted: bool = True, **metadata) -> bool:
        ""添加新知识到积累库""
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
                    ids=[f"user_{datetime.now().timestamp()}"]
                )
            except Exception as e:
                logger.warning(f"ChromaDB 添加失败: {e}")

        self.memory_store.append(record)
        return True

    def update_feedback(self, original_query: str, action: str, feedback: str) -> bool:
        ""更新反馈：用户纠正记录写入知识库，形成 RAG 闭环""
        feedback_map = {
            "接受": "positive",
            "忽略": "neutral",
            "拒绝": "negative",
            "纠正": "negative",
        }
        sentiment = feedback_map.get(feedback, "neutral")
        content = f"用户输入「{original_query}」后执行了「{action}」，用户反馈「{feedback}」"
        self.add(content, category="用户反馈", accepted=(sentiment == "positive"), sentiment=sentiment)
        logger.info(f"RAG 知识库更新: {content}")
        return True

    def get_context_prompt(self, user_query: str, context) -> str:
        ""
        构建 RAG 增强的上下文提示，供 LLM 决策使用
        将检索到的相关知识拼入 Prompt，提升回答可信度
        ""
        knowledge = self.query(user_query, top_k=3)

        if not knowledge:
            return ""

        context_lines = []
        for k in knowledge:
            sentiment = k.get("sentiment", "neutral")
            tag = f"[{k.get('category', '知识')}]"
            context_lines.append(f"{tag} {k['content']}")

        return "\n".join(context_lines)

    def get_user_preference_score(self, candidate_action: str, context) -> float:
        ""
        获取用户历史偏好得分（供 LSR 使用）
        返回 0.0~1.0 的偏好置信度
        ""
        score = 0.5
        history = self.query(candidate_action, top_k=3, category="用户习惯")
        if history:
            accepted_count = sum(1 for h in history if h.get("accepted"))
            score = min(1.0, 0.5 + accepted_count * 0.25)
        return score

    def count(self) -> int:
        return len(self.memory_store) + len(self.preset_knowledge)

    def backup(self, path: str = None) -> bool:
        ""加密备份知识库""
        if path is None:
            path = os.path.join(DATA_DIR, "kb_backup.enc")
        data = {
            "memory_store": self.memory_store,
            "timestamp": datetime.now().isoformat(),
        }
        success = self._storage.save_pickle(data, path)
        if success:
            logger.info(f"知识库已加密备份: {path}")
        return success

    def restore(self, path: str = None) -> bool:
        ""从加密备份恢复知识库""
        if path is None:
            path = os.path.join(DATA_DIR, "kb_backup.enc")
        data = self._storage.load_pickle(path)
        if data and "memory_store" in data:
            self.memory_store = data["memory_store"]
            logger.info(f"知识库已从备份恢复，共 {len(self.memory_store)} 条记录")
            return True
        logger.warning(f"知识库恢复失败或备份文件不存在: {path}")
        return False
