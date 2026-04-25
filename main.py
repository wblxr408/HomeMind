"""
HomeMind 主入口
按照 design.md 的五层架构组织：
  交互层 → BSR → LSR → 理解层(LLM/DQN) → 执行层 → 学习层
"""

import logging
import os
import sys
from typing import Optional

from demo.context import HomeContext
from core.bsr.candidate_recall import BSRecall
from core.lsr.precision_ranking import LSRecify
from core.llm.decision import LLMDecider
from core.dqn.policy import DQNPolicy
from core.rag.knowledge_base import KnowledgeBase
from core.constants import SCENE_INDEX_MAP, SCENE_NAMES
from core.execution import CommandValidator
from core.language.normalizer import LanguageNormalizer
from core.memory import PreferenceStore, SessionStore
from core.privacy import PrivacyRedactor
from core.router import InferenceRouter
from tools.device_control import DeviceController
from tools.info_query import InfoQuery
from tools.scene_switch import SceneSwitcher
from tools.kb_write import KBWriter
from tools.dqn_feedback import DQNFeedback
from demo.simulator import HomeSimulator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


class HomeMindAgent:
    """
    HomeMind 智能体主类
    聚合五层架构的所有组件，协调完整推理流程。
    """

    def __init__(self, confidence_threshold: float = 0.75):
        self.confidence_threshold = confidence_threshold

        self.session_store = SessionStore()
        self.preference_store = PreferenceStore()
        self.privacy_redactor = PrivacyRedactor()
        self.router = InferenceRouter()
        self.command_validator = CommandValidator()
        self.kb = KnowledgeBase()
        self.kb.preference_store = self.preference_store
        self.bsr = BSRecall(self.kb)
        self.lsr = LSRecify()
        self.llm = LLMDecider(
            backend=os.getenv("LLM_BACKEND", "mock"),
            model_path=os.getenv("LLM_MODEL_PATH", ""),
            api_base=os.getenv("LLM_API_BASE", ""),
            api_key=os.getenv("LLM_API_KEY", ""),
            cloud_model=os.getenv("LLM_MODEL", ""),
        )
        self.dqn = DQNPolicy()
        self.device_ctrl = DeviceController()
        self.info_query = InfoQuery()
        self.scene_switcher = SceneSwitcher(self.device_ctrl)
        self.kb_writer = KBWriter(self.kb)
        self.dqn_feedback = DQNFeedback(self.dqn)
        self.language_normalizer = LanguageNormalizer()

        self.context = HomeContext()
        self.context.current_scene = ""
        self._simulator: Optional[HomeSimulator] = None
        self._last_dqn_action: Optional[int] = None
        self._restore_persisted_state()
        logger.info("HomeMind 初始化完成")

    def attach_simulator(self, sim: HomeSimulator):
        """挂载仿真器（演示环境）"""
        self._simulator = sim

    def run(self):
        """交互入口，演示/调试循环"""
        logger.info("HomeMind 启动，输入 'quit' 退出")
        self._print_context()

        while True:
            try:
                user_input = input("\n[用户] ").strip()
            except (EOFError, KeyboardInterrupt):
                self._shutdown()
                break

            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "退出"):
                self._shutdown()
                break

            response = self.process(user_input)
            print(f"\n[HomeMind] {response}")

    def _print_context(self):
        """打印当前环境上下文"""
        print(f"\n[HomeMind] 当前环境: {self.context.hour:02d}:00, "
              f"{self.context.temperature}°C, 湿度{self.context.humidity}%, "
              f"在家{self.context.members_home}人")

    def _shutdown(self):
        """优雅退出，保存 DQN 策略"""
        logger.info("退出 HomeMind")
        try:
            self.dqn.save()
            self.session_store.save()
            self.preference_store.save()
        except Exception as e:
            logger.warning(f"DQN 保存失败: {e}")

    def _restore_persisted_state(self):
        state = self.session_store.get_runtime_context()
        current_scene = state.get("current_scene", "")
        if current_scene:
            self.context.current_scene = current_scene
            self.context.last_scene = SCENE_INDEX_MAP.get(current_scene, -1)

    def process(self, user_input: str) -> str:
        """
        处理用户输入，完整流程：
        1. BSR 候选召回
        2. LSR 轻量精排（RAG 偏好特征注入）
        3. LLM 决策（RAG 上下文注入 + 置信度评估）
        4. 执行工具调用（设备状态同步）
        5. 学习层写回知识库（RAG 闭环）
        """
        if self._simulator:
            self.context = self._simulator.get_context()
            self.context.current_scene = self.session_store.get_current_scene()

        normalized = self.language_normalizer.normalize(user_input)
        query_for_ai = normalized.normalized or user_input
        self.session_store.update_from_query(user_input, query_for_ai)
        logger.info(f"收到输入: {user_input}")
        if query_for_ai != user_input:
            logger.info(f"归一化输入: {query_for_ai}")
            self.preference_store.record_feedback(user_input, query_for_ai, "接受")

        candidates = self.bsr.recall(query_for_ai, self.context)
        logger.info(f"BSR 召回 {len(candidates)} 个候选: {[c['action'] for c in candidates]}")

        ranked = self.lsr.rank(query_for_ai, candidates, self.context, kb=self.kb)
        if ranked:
            logger.info(f"LSR 精排 Top: {ranked[0]['action']} (score={ranked[0].get('final_score', 0):.3f})")
        else:
            clarification = "我暂时没有找到合适的候选动作，请换一种说法试试。"
            self.session_store.update_clarification(clarification)
            return clarification

        route_info = self.router.decide_route(
            user_input,
            ranked,
            normalized_query=query_for_ai,
            cloud_available=self.llm.is_cloud_available(),
        )
        logger.info(f"路由结果: {route_info}")

        rag_context = self.kb.get_context_prompt(query_for_ai, self.context)
        cloud_context = self.privacy_redactor.build_cloud_context(
            self.context,
            ranked[:3],
            session_store=self.session_store,
            preference_store=self.preference_store,
        )
        logger.info(f"云端最小上下文: {cloud_context}")
        if route_info["route"] == "clarify":
            clarification = self.llm.ask_clarification(query_for_ai, ranked)
            self.session_store.update_clarification(clarification)
            return clarification
        if route_info["route"] == "cloud":
            decision = self.llm.decide_cloud(
                query_for_ai,
                ranked,
                self.context,
                rag_context=rag_context,
                context_summary=cloud_context,
            )
        else:
            decision = self.llm.decide_local(
                query_for_ai,
                ranked,
                self.context,
                rag_context=rag_context,
            )
        logger.info(f"LLM 决策: confidence={decision.get('confidence', 0):.3f}, {decision}")

        if decision.get("confidence", 0) < self.confidence_threshold:
            clarification = self.llm.ask_clarification(query_for_ai, ranked)
            self.session_store.update_clarification(clarification)
            return clarification

        validation = self.command_validator.validate(decision)
        logger.info(f"命令校验: {validation}")
        if not validation["valid"]:
            message = "我暂时不能执行这个指令：" + "；".join(validation["errors"])
            self.session_store.update_clarification(message)
            return message
        if validation["requires_confirmation"]:
            message = "这个操作风险较高，需要二次确认后再执行。"
            self.session_store.update_clarification(message)
            return message
        decision = validation["normalized_command"]

        action = decision.get("action", "")
        params = decision.get("params", {})
        self._last_dqn_action = None
        route = route_info["route"]

        if action == "设备控制":
            device = decision.get("device", "")
            device_action = decision.get("device_action", "")
            try:
                result = self.device_ctrl.execute(device, device_action, params)
                self._sync_devices_from_controller()
            except Exception as e:
                logger.error(f"设备控制失败: {e}")
                result = f"设备控制失败，请稍后重试"

        elif action == "场景切换":
            scene = decision.get("scene", "")
            try:
                result = self.scene_switcher.execute(scene)
                self._sync_scene_to_simulator(scene)
                self.context.current_scene = scene
                self.context.last_scene = SCENE_INDEX_MAP.get(scene, -1)
                self.session_store.update_scene(scene)
            except Exception as e:
                logger.error(f"场景切换失败: {e}")
                result = f"场景切换失败，请稍后重试"

        elif action == "信息查询":
            query_type = decision.get("query_type", "")
            try:
                result = self.info_query.execute(query_type, params)
            except Exception as e:
                logger.error(f"信息查询失败: {e}")
                result = f"信息查询失败"

        else:
            result = f"执行了: {action}，参数: {params}"

        # 根据置信度决定反馈：高于阈值记录"接受"，否则记录"忽略"
        confidence = decision.get("confidence", 0)
        feedback = "接受" if confidence >= 0.85 else "忽略"
        self.session_store.update_from_decision(decision, route=route, result=result)
        self.preference_store.record_feedback(user_input, query_for_ai, feedback)
        if feedback == "接受":
            self.preference_store.record_action_accept(decision, self.context)
        self.kb_writer.write_feedback(user_input, decision, feedback)

        return result

    def _sync_devices_from_controller(self):
        """将 DeviceController 的状态同步到 simulator"""
        if self._simulator:
            for dev, state in self.device_ctrl.get_all_state().items():
                status = state.get("status", "关")
                self._simulator.device_sim.update(dev, status)

    def _sync_scene_to_simulator(self, scene: str):
        """将场景切换结果同步到 simulator"""
        if self._simulator:
            self._simulator.apply_scene(scene)

    def _scene_to_index(self, scene: str) -> int:
        scene_map = {"睡眠模式": 0, "待客模式": 1, "离家模式": 2,
                    "观影模式": 3, "起床模式": 4, "回家模式": 1}
        return scene_map.get(scene, -1)

    def proactive_recommend(self) -> Optional[str]:
        """
        DQN 主动推荐（独立于用户指令的流程）
        由定时器或环境感知触发，推荐后等待用户响应
        """
        if self._simulator:
            self.context = self._simulator.get_context()

        recommended_scene_idx, confidence = self.dqn.recommend(self.context)
        self._last_dqn_action = recommended_scene_idx

        if recommended_scene_idx == 5:
            return None

        scene_name = SCENE_NAMES[recommended_scene_idx]

        if confidence > 0.8:
            self.scene_switcher.execute(scene_name)
            self._sync_scene_to_simulator(scene_name)
            self.context.current_scene = scene_name
            self.context.last_scene = recommended_scene_idx
            reply = f"已为您自动切换到{scene_name}。"
            self.session_store.update_scene(scene_name)
            self.session_store.update_from_decision(
                {
                    "action": "场景切换",
                    "scene": scene_name,
                    "device_action": "scene",
                    "params": {},
                    "confidence": confidence,
                },
                route="local",
                result=reply,
            )
        else:
            reply = f"现在是{self.context.hour}点，要切换到{scene_name}吗？"

        return reply

    def respond_to_recommendation(self, user_response: str) -> str:
        """
        用户对 DQN 主动推荐的响应
        接受 → 执行确认；拒绝/忽略 → 记录负反馈
        """
        if self._last_dqn_action is None:
            return "（无待确认的推荐）"

        if user_response in ("好", "是", "好的", "可以", "接受"):
            self.dqn_feedback.record(self.context, self._last_dqn_action, "接受")
            scene_name = SCENE_NAMES.get(self._last_dqn_action, "")
            self.preference_store.record_recommendation_feedback(scene_name, "接受")
            return f"已确认{scene_name}。"
        elif user_response in ("不要", "否", "不用", "拒绝"):
            self.dqn_feedback.record(self.context, self._last_dqn_action, "拒绝")
            scene_name = SCENE_NAMES.get(self._last_dqn_action, "")
            self.preference_store.record_recommendation_feedback(scene_name, "拒绝")
            return "好的，不做更改。"
        else:
            self.dqn_feedback.record(self.context, self._last_dqn_action, "忽略")
            scene_name = SCENE_NAMES.get(self._last_dqn_action, "")
            self.preference_store.record_recommendation_feedback(scene_name, "忽略")
            return "好的。"

    def update_context(self, **kwargs):
        """更新环境上下文"""
        for key, value in kwargs.items():
            if hasattr(self.context, key):
                setattr(self.context, key, value)


def main():
    agent = HomeMindAgent()
    sim = HomeSimulator()
    agent.attach_simulator(sim)
    agent.run()


if __name__ == "__main__":
    main()
