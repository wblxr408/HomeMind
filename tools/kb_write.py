"""
知识库写入工具
将用户纠正/偏好写入 ChromaDB
由学习层调用，非用户直接触发
"""

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


class KBWriter:
    """知识库写入器，封装 RAG 更新逻辑"""

    def __init__(self, kb):
        self.kb = kb

    def write_feedback(self, original_query: str, decision: Dict[str, Any], feedback: str) -> bool:
        """
        将用户反馈写入知识库，形成 RAG 闭环
        """
        action = decision.get("action", "")
        params = decision.get("params", {})
        content = f"用户输入「{original_query}」，系统执行了「{action}」"

        if params:
            content += f"，参数为{params}"

        category_map = {
            "接受": "用户习惯",
            "忽略": "中性反馈",
            "拒绝": "纠正记录",
            "纠正": "纠正记录",
        }
        category = category_map.get(feedback, "用户反馈")
        accepted = (feedback == "接受")

        self.kb.add(content, category=category, accepted=accepted, feedback=feedback)
        logger.info(f"知识库写入: [{category}] {content}")
        return True

    def write_preference(self, condition: str, preference: str, action: str) -> bool:
        """写入用户偏好记录"""
        content = f"当{condition}时，用户偏好{preference}，对应动作{action}"
        self.kb.add(content, category="用户偏好", accepted=True)
        logger.info(f"偏好写入: {content}")
        return True
