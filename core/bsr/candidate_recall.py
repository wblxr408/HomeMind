"""
BSR: Broad Stage Recall（广召回）
三路融合召回候选动作：规则召回 + 向量召回 + 用户历史(RAG)
"""

import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

_EMBEDDING_MODEL: Optional[Any] = None
_ACTION_POOL: Optional[List[str]] = None
_ACTION_EMBS: Optional[Any] = None

# 设备与动作关键词的映射（用于从内容中提取设备）
_DEVICE_ACTION_KEYWORDS = [
    "打开空调", "关闭空调", "调高空调温度", "调低空调温度",
    "打开灯光", "关闭灯光", "调亮灯光", "调暗灯光",
    "打开电视", "关闭电视",
    "打开风扇", "关闭风扇",
    "打开窗户", "关闭窗户",
    "打开音响", "关闭音响",
    "打开暖气", "打开热水器",
    "切换睡眠模式", "切换待客模式", "切换离家模式",
    "切换观影模式", "切换起床模式", "切换回家模式",
]


def _get_embedding_model():
    """Embedding 模型单例，避免每次推理重复加载"""
    global _EMBEDDING_MODEL
    if _EMBEDDING_MODEL is None:
        try:
            from sentence_transformers import SentenceTransformer
            _EMBEDDING_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("MiniLM-L6-v2 加载完成")
        except Exception as e:
            logger.warning(f"MiniLM-L6-v2 加载失败: {e}")
    return _EMBEDDING_MODEL


def _get_action_embeddings():
    """预计算 action_pool 向量，单例缓存"""
    global _ACTION_POOL, _ACTION_EMBS, _EMBEDDING_MODEL
    if _ACTION_POOL is not None:
        return _ACTION_POOL, _ACTION_EMBS

    _ACTION_POOL = [
        "打开空调", "关闭空调", "调高空调温度", "调低空调温度",
        "打开灯光", "关闭灯光", "调亮灯光", "调暗灯光",
        "打开电视", "关闭电视",
        "打开风扇", "关闭风扇",
        "打开窗户", "关闭窗户",
        "打开音响", "关闭音响",
        "切换睡眠模式", "切换待客模式", "切换离家模式",
        "切换观影模式", "切换起床模式", "切换回家模式",
        "打开暖气", "打开热水器",
    ]

    model = _get_embedding_model()
    _ACTION_EMBS = None
    if model is not None:
        import numpy as np
        _ACTION_EMBS = model.encode(_ACTION_POOL)
        logger.info(f"action_pool 向量预计算完成，共 {len(_ACTION_POOL)} 个动作")
    return _ACTION_POOL, _ACTION_EMBS


class BSRecall:
    def __init__(self, kb, top_k: int = 5):
        self.kb = kb
        self.top_k = top_k
        self._init_rules()

    def _init_rules(self):
        """基于设备能力和专家知识的规则映射（零成本、极稳定）"""
        self.rule_map = {
            "闷": ["打开空调", "打开风扇", "打开窗户"],
            "热": ["打开空调", "打开风扇"],
            "冷": ["调高空调温度", "打开暖气"],
            "暗": ["打开灯光", "调亮灯光"],
            "亮": ["调暗灯光", "关闭部分灯光"],
            "吵": ["调低音量", "关闭音响"],
            "安静": ["关闭电视", "调低音量"],
            "困": ["切换睡眠模式"],
            "睡觉": ["切换睡眠模式", "关闭灯光"],
            "出门": ["切换离家模式"],
            "回家": ["切换回家模式"],
            "客人": ["切换待客模式"],
            "观影": ["切换观影模式"],
            "电视": ["打开电视", "关闭电视"],
            "空调": ["打开空调", "关闭空调"],
            "灯光": ["打开灯光", "关闭灯光"],
            "窗户": ["打开窗户", "关闭窗户"],
            "风扇": ["打开风扇", "关闭风扇"],
            "音响": ["打开音响", "关闭音响"],
        }

    def recall(self, query: str, context) -> List[Dict[str, Any]]:
        """
        三路融合召回
        1. 规则召回（最高优先级，权重 0.9）
        2. 向量召回（MiniLM，权重来自相似度）
        3. 用户历史（RAG，权重来自历史接受率）
        """
        candidates = []
        seen = set()

        for route_cands in [
            self._rule_recall(query),
            self._vector_recall(query),
            self._history_recall(query),
        ]:
            for c in route_cands:
                if c["action"] not in seen:
                    seen.add(c["action"])
                    candidates.append(c)

        if not candidates:
            candidates.append({"action": "无法理解", "source": "fallback", "score": 0.0})

        return candidates[:self.top_k]

    def _rule_recall(self, query: str) -> List[Dict[str, Any]]:
        """规则召回：基于关键词匹配，优先级最高"""
        results = []
        for keyword, actions in self.rule_map.items():
            if keyword in query:
                for action in actions:
                    results.append({
                        "action": action,
                        "source": "rule",
                        "keyword": keyword,
                        "score": 0.9,
                    })
        return results

    def _vector_recall(self, query: str) -> List[Dict[str, Any]]:
        """向量召回：基于语义相似度（MiniLM），使用预计算向量"""
        results = []
        action_pool, action_embs = _get_action_embeddings()

        if action_embs is None:
            return results

        try:
            import numpy as np
            model = _get_embedding_model()
            if model is None:
                return results

            query_emb = model.encode(query)
            sims = np.dot(action_embs, query_emb)
            top_indices = np.argsort(sims)[-4:][::-1]

            for idx in top_indices:
                if sims[idx] > 0.25:
                    results.append({
                        "action": action_pool[idx],
                        "source": "vector",
                        "score": float(np.clip(sims[idx], 0, 1)),
                    })
        except Exception as e:
            logger.warning(f"向量召回失败: {e}")

        return results

    def _history_recall(self, query: str) -> List[Dict[str, Any]]:
        """用户历史召回：基于 RAG 检索，获取历史接受率作为权重"""
        results = []
        history_records = self.kb.query(query, top_k=3, category="用户习惯")
        for rec in history_records:
            action_from_content = self._extract_action_from_content(rec.get("content", ""))
            if action_from_content:
                accepted = rec.get("accepted", False)
                results.append({
                    "action": action_from_content,
                    "source": "history",
                    "score": 0.9 if accepted else 0.6,
                })
        return results

    def _extract_action_from_content(self, content: str) -> str:
        """从知识库记录内容中提取动作名称"""
        for action in _DEVICE_ACTION_KEYWORDS:
            if action in content:
                return action
        return ""
