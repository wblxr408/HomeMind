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
        device = decision.get("device", "")
        scene = decision.get("scene", "")
        device_action = decision.get("device_action", "")
        params = decision.get("params", {})
        raw_query = str(original_query or "").strip()
        normalized_feedback = str(feedback or "").strip()

        # 普通成功流水只更新结构化偏好，不进入长期事件库。
        if normalized_feedback in ("接受", "忽略"):
            logger.info("知识库写入已跳过低价值事件: feedback=%s query=%s", normalized_feedback, raw_query)
            return False

        content = f"用户输入「{raw_query}」，系统原本准备执行「{action}」"
        if device:
            content += f"（设备：{device}"
            if device_action:
                content += f"/{device_action}"
            content += "）"
        if scene:
            content += f"（场景：{scene}）"
        if params:
            content += f"，参数为{params}"
        content += f"，用户反馈为「{normalized_feedback}」"

        category_map = {
            "拒绝": "用户反馈",
            "纠正": "纠正记录",
        }
        category = category_map.get(normalized_feedback, "用户反馈")
        memory_key = "|".join(
            [
                category,
                raw_query,
                str(action or ""),
                str(device or ""),
                str(device_action or ""),
                str(scene or ""),
                str(sorted((params or {}).items())),
            ]
        )
        value_score = 3.0 if category == "纠正记录" else 2.0

        self.kb.add(
            content,
            category=category,
            accepted=False,
            feedback=normalized_feedback,
            memory_key=memory_key,
            value_score=value_score,
            original_query=raw_query,
            action=action,
            device=device,
            device_action=device_action,
            scene=scene,
            params=dict(params or {}),
        )
        logger.info(f"知识库写入: [{category}] {content}")
        return True

    def write_preference(self, condition: str, preference: str, action: str) -> bool:
        """写入用户偏好记录"""
        content = f"当{condition}时，用户偏好{preference}，对应动作{action}"
        self.kb.add(
            content,
            category="用户偏好",
            accepted=True,
            memory_key=f"偏好|{condition}|{preference}|{action}",
            value_score=2.5,
            condition=condition,
            preference=preference,
            action=action,
        )
        logger.info(f"偏好写入: {content}")
        return True
