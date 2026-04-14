"""
DQN 反馈记录工具
记录用户对推荐场景的反馈，写入经验回放池
触发条件：用户对 DQN 主动推荐做出响应
"""

import logging

logger = logging.getLogger(__name__)


class DQNFeedback:
    """DQN 反馈记录器，封装 DQN 经验写入逻辑"""

    def __init__(self, dqn_policy):
        self.dqn = dqn_policy

    def record(self, context, action: int, user_response: str) -> bool:
        """
        记录用户对 DQN 推荐场景的反馈，写入经验回放池
        user_response: 接受 / 忽略 / 拒绝 / 纠正
        """
        self.dqn.record_feedback(context, action, user_response)
        logger.info(f"DQN 反馈已记录: action={action}, response={user_response}")
        return True
