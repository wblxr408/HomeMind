"""
BSR: Broad Stage Recall.

Recall candidate smart-home actions from three sources:
- rule recall
- vector recall
- user history from RAG
"""

import logging
from typing import Any, Dict, List, Optional

from core.constants import ACTION_POOL
from core.utils.embedding import encode, get_model

logger = logging.getLogger(__name__)

_ACTION_EMBS: Optional[Any] = None


def _get_action_embeddings():
    """Pre-compute action pool embeddings once."""
    global _ACTION_EMBS
    if _ACTION_EMBS is not None:
        return ACTION_POOL, _ACTION_EMBS

    model = get_model()
    _ACTION_EMBS = None
    if model is not None:
        _ACTION_EMBS = encode(ACTION_POOL)
        logger.info("action_pool embeddings initialized, count=%s", len(ACTION_POOL))
    return ACTION_POOL, _ACTION_EMBS


class BSRecall:
    def __init__(self, kb, top_k: int = 5):
        self.kb = kb
        self.top_k = top_k
        self._init_rules()

    def _init_rules(self):
        """High-precision keyword rules for common smart-home commands."""
        self.rule_map = {
            "闷": ["打开空调", "打开风扇", "打开窗户"],
            "热": ["打开空调", "打开风扇"],
            "冷": ["调高空调温度", "打开暖气"],
            "调亮灯光": ["调亮灯光", "打开灯光"],
            "调暗灯光": ["调暗灯光", "关闭部分灯光"],
            "亮点": ["调亮灯光", "打开灯光"],
            "暗点": ["调暗灯光", "关闭部分灯光"],
            "暗": ["打开灯光", "调亮灯光"],
            "亮": ["调暗灯光", "关闭部分灯光"],
            "吵": ["调低音量", "关闭音响"],
            "安静": ["关闭电视", "调低音量"],
            "困": ["切换睡眠模式"],
            "睡眠": ["切换睡眠模式"],
            "睡眠模式": ["切换睡眠模式"],
            "睡觉": ["切换睡眠模式", "关闭灯光"],
            "待客模式": ["切换待客模式"],
            "出门": ["切换离家模式"],
            "离家模式": ["切换离家模式"],
            "回家": ["切换回家模式"],
            "回家模式": ["切换回家模式"],
            "客人": ["切换待客模式"],
            "观影": ["切换观影模式"],
            "观影模式": ["切换观影模式"],
            "起床模式": ["切换起床模式"],
            "电视": ["打开电视", "关闭电视"],
            "空调": ["打开空调", "关闭空调"],
            "灯光": ["打开灯光", "关闭灯光"],
            "窗户": ["打开窗户", "关闭窗户"],
            "风扇": ["打开风扇", "关闭风扇"],
            "音响": ["打开音响", "关闭音响"],
        }

    def recall(self, query: str, context) -> List[Dict[str, Any]]:
        """
        Merge candidates from rule, vector, and history recall.
        """
        candidates = []
        seen = set()

        for route_cands in [
            self._rule_recall(query),
            self._vector_recall(query),
            self._history_recall(query),
        ]:
            for candidate in route_cands:
                action = candidate["action"]
                if action not in seen:
                    seen.add(action)
                    candidates.append(candidate)

        if not candidates:
            candidates.append({"action": "无法理解", "source": "fallback", "score": 0.0})

        return candidates[:self.top_k]

    def _rule_recall(self, query: str) -> List[Dict[str, Any]]:
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
        results = []
        action_pool, action_embs = _get_action_embeddings()

        if action_embs is None:
            return results

        try:
            import numpy as np

            model = get_model()
            if model is None:
                return results

            query_emb = encode(query)
            sims = np.dot(action_embs, query_emb)
            top_indices = np.argsort(sims)[-4:][::-1]

            for idx in top_indices:
                if sims[idx] > 0.25:
                    results.append({
                        "action": action_pool[idx],
                        "source": "vector",
                        "score": float(np.clip(sims[idx], 0, 1)),
                    })
        except Exception as exc:
            logger.warning("vector recall failed: %s", exc)

        return results

    def _history_recall(self, query: str) -> List[Dict[str, Any]]:
        results = []
        history_records = self.kb.query(query, top_k=3, category="用户习惯")
        for record in history_records:
            action = self._extract_action_from_content(record.get("content", ""))
            if action:
                accepted = record.get("accepted", False)
                results.append({
                    "action": action,
                    "source": "history",
                    "score": 0.95 if accepted else 0.60,
                })
        return results

    def _extract_action_from_content(self, content: str) -> str:
        for action in ACTION_POOL:
            if action in content:
                return action
        return ""
