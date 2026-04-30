"""
HomeMind 主入口
按照 design.md 的五层架构组织：
  交互层 → BSR → LSR → 理解层(LLM/DQN) → 执行层 → 学习层
"""

import logging
import os
import sys
import argparse
from typing import Optional

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from demo.context import HomeContext
from core.bsr.candidate_recall import BSRecall
from core.automation import NLToTAPConverter, TAPRuleStore
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
        self.tap_rule_store = TAPRuleStore()
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
        self.nl_to_tap = NLToTAPConverter()

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
        if self.kb and os.path.exists(os.path.join("data", "kb_backup.enc")):
            self.kb.restore()

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

        pending = self.session_store.get_pending_confirmation()
        if pending:
            pending_reply = self._handle_pending_confirmation(user_input, pending)
            if pending_reply:
                return pending_reply

        normalized = self.language_normalizer.normalize(user_input)
        query_for_ai = normalized.normalized or user_input
        self.session_store.update_from_query(user_input, query_for_ai)
        logger.info(f"收到输入: {user_input}")
        if query_for_ai != user_input:
            logger.info(f"归一化输入: {query_for_ai}")
            self.preference_store.record_feedback(user_input, query_for_ai, "接受")

        intent_info = self.llm.plan_intent(user_input, normalized_query=query_for_ai, context=self.context)
        if intent_info["intent_type"] == "automation_request":
            logger.info(f"LLM intent plan: {intent_info}")
            proposal = self._build_automation_confirmation(user_input, query_for_ai)
            if proposal:
                self.session_store.append_turn("assistant", proposal)
                self.session_store.save()
                return proposal
            message = "我理解到你想创建定时任务，但还缺少明确的时间或动作。你可以试试“晚上7:00打开空调”。"
            self.session_store.append_turn("assistant", message)
            self.session_store.save()
            return message
        if intent_info["intent_type"] == "chat_reply":
            logger.info(f"Reply route: {intent_info}")
            message = intent_info.get("reply_message") or "你好，我在。"
            self.session_store.update_clarification(message)
            return message
        if intent_info["intent_type"] == "clarification_needed":
            logger.info(f"Clarification route: {intent_info}")
            message = intent_info.get("reply_message") or "你好，我在。"
            self.session_store.append_turn("assistant", message)
            self.session_store.save()
            return message
        if False and intent_info["route"] == "reply":
            logger.info(f"路由结果: {unsupported}")
            message = unsupported["message"]
            self.session_store.update_clarification(message)
            return message

        candidates = self.bsr.recall(query_for_ai, self.context)
        logger.info(f"BSR 召回 {len(candidates)} 个候选: {[c['action'] for c in candidates]}")

        ranked = self.lsr.rank(
            query_for_ai,
            candidates,
            self.context,
            kb=self.kb,
            session_store=self.session_store,
        )
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

    def _handle_pending_confirmation(self, user_input: str, pending: dict) -> Optional[str]:
        normalized = str(user_input or "").strip().lower()
        normalized = normalized.replace("，", "").replace("。", "").replace("！", "").replace("!", "")
        accept_tokens = {"好", "好的", "是", "是的", "确认", "确认创建", "可以", "接受", "yes", "y"}
        reject_tokens = {"不", "不用", "不要", "取消", "先不用", "拒绝", "no", "n"}

        if normalized in accept_tokens and pending.get("action_type") == "automation_proposal":
            payload = pending.get("payload", {}) or {}
            rule = payload.get("rule")
            if not rule:
                self.session_store.clear_pending_confirmation()
                return "这个待确认的定时任务已经失效了，请重新说一次。"
            created_rule = self.tap_rule_store.add_rule(rule)
            self.session_store.clear_pending_confirmation()
            summary = payload.get("summary") or self._summarize_rule(created_rule)
            reply = f"已为你创建定时任务：{summary}。"
            self.session_store.append_turn("assistant", reply)
            self.session_store.save()
            return reply

        if normalized in reject_tokens:
            self.session_store.clear_pending_confirmation()
            reply = "好的，先不创建这个定时任务。"
            self.session_store.append_turn("assistant", reply)
            self.session_store.save()
            return reply

        return None

    def _build_automation_confirmation(self, raw_text: str, normalized_text: str) -> Optional[str]:
        rule = self.nl_to_tap.parse(raw_text) or self.nl_to_tap.parse(normalized_text)
        if not rule:
            return None
        summary = self._summarize_rule(rule)
        self.session_store.set_pending_confirmation(
            "automation_proposal",
            {
                "rule": rule,
                "summary": summary,
                "original_input": raw_text,
                "normalized_input": normalized_text,
            },
        )
        return f"我理解成：{summary}。要为你创建定时任务吗？"

    def _summarize_rule(self, rule: dict) -> str:
        trigger = dict(rule.get("trigger", {}) or {})
        action = dict(rule.get("action", {}) or {})
        at_time = trigger.get("at", "08:00")
        if action.get("type") == "scene_switch":
            return f"每天 {at_time} 切换到{action.get('scene', '目标场景')}"
        if action.get("type") == "device_control":
            action_map = {"on": "打开", "off": "关闭", "adjust": "调整"}
            verb = action_map.get(action.get("device_action"), "执行")
            return f"每天 {at_time} {verb}{action.get('device', '设备')}"
        return f"每天 {at_time} 执行自动化任务"

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

def _llm_first_process(self: HomeMindAgent, user_input: str) -> str:
    if not self.llm:
        return "AI 模块未加载"

    if self._simulator:
        self.context = self._simulator.get_context()
        self.context.current_scene = self.session_store.get_current_scene()

    pending = self.session_store.get_pending_confirmation()
    if pending:
        pending_reply = self._handle_pending_confirmation(user_input, pending)
        if pending_reply:
            return pending_reply

    normalized = self.language_normalizer.normalize(user_input)
    query_for_ai = normalized.normalized or user_input
    self.session_store.update_from_query(user_input, query_for_ai)
    logger.info("收到输入: %s", user_input)
    if query_for_ai != user_input:
        logger.info("归一化输入: %s", query_for_ai)
        self.preference_store.record_feedback(user_input, query_for_ai, "接受")

    intent_plan = self.llm.plan_intent(user_input, normalized_query=query_for_ai, context=self.context)
    logger.info("LLM 主判定: %s", intent_plan)

    if intent_plan["intent_type"] == "chat_reply":
        message = intent_plan.get("reply_message") or "你好，我在。"
        self.session_store.append_turn("assistant", message)
        self.session_store.save()
        return message

    if intent_plan["intent_type"] == "clarification_needed":
        message = intent_plan.get("reply_message") or "请问你是想控制设备、切换场景，还是创建定时任务？"
        self.session_store.update_clarification(message)
        return message

    if intent_plan["intent_type"] == "automation_request":
        proposal = self._build_automation_confirmation(user_input, query_for_ai)
        if proposal:
            self.session_store.append_turn("assistant", proposal)
            self.session_store.save()
            return proposal
        message = "我理解到你想创建定时任务，但还缺少明确的时间或动作。你可以试试“晚上7:00打开空调”。"
        self.session_store.update_clarification(message)
        return message

    goal_query = intent_plan.get("normalized_goal") or query_for_ai
    unsupported = self.router.detect_unsupported_request(user_input, normalized_query=goal_query)
    if unsupported:
        logger.info("后置能力校验拒绝: %s", unsupported)
        message = unsupported["message"]
        self.session_store.update_clarification(message)
        return message

    candidates = self.bsr.recall(goal_query, self.context)
    logger.info("BSR 召回 %s 个候选: %s", len(candidates), [c["action"] for c in candidates])

    ranked = self.lsr.rank(
        goal_query,
        candidates,
        self.context,
        kb=self.kb,
        session_store=self.session_store,
    )
    if not ranked:
        clarification = self.llm.ask_clarification(goal_query, candidates)
        self.session_store.update_clarification(clarification)
        return clarification

    route_info = self.router.decide_route(
        user_input,
        ranked,
        normalized_query=goal_query,
        cloud_available=self.llm.is_cloud_available(),
    )
    logger.info("候选路由建议: %s", route_info)
    if route_info["route"] == "clarify":
        clarification = self.llm.ask_clarification(goal_query, ranked)
        self.session_store.update_clarification(clarification)
        return clarification

    rag_context = self.kb.get_context_prompt(goal_query, self.context)
    cloud_context = self.privacy_redactor.build_cloud_context(
        self.context,
        ranked[:3],
        session_store=self.session_store,
        preference_store=self.preference_store,
    )
    logger.info("云端最小上下文: %s", cloud_context)

    if route_info["route"] == "cloud":
        decision = self.llm.decide_cloud(
            goal_query,
            ranked,
            self.context,
            rag_context=rag_context,
            context_summary=cloud_context,
        )
    else:
        decision = self.llm.decide_local(
            goal_query,
            ranked,
            self.context,
            rag_context=rag_context,
        )
    logger.info("LLM 结构化决策: confidence=%.3f, %s", decision.get("confidence", 0), decision)

    if decision.get("confidence", 0) < self.confidence_threshold:
        clarification = self.llm.ask_clarification(goal_query, ranked)
        self.session_store.update_clarification(clarification)
        return clarification

    validation = self.command_validator.validate(decision)
    logger.info("命令校验: %s", validation)
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
        except Exception as exc:
            logger.error("设备控制失败: %s", exc)
            result = "设备控制失败，请稍后重试"
    elif action == "场景切换":
        scene = decision.get("scene", "")
        try:
            result = self.scene_switcher.execute(scene)
            self._sync_scene_to_simulator(scene)
            self.context.current_scene = scene
            self.context.last_scene = SCENE_INDEX_MAP.get(scene, -1)
            self.session_store.update_scene(scene)
        except Exception as exc:
            logger.error("场景切换失败: %s", exc)
            result = "场景切换失败，请稍后重试"
    elif action == "信息查询":
        query_type = decision.get("query_type", "")
        try:
            result = self.info_query.execute(query_type, params)
        except Exception as exc:
            logger.error("信息查询失败: %s", exc)
            result = "信息查询失败"
    else:
        result = f"执行了 {action}，参数 {params}"

    confidence = decision.get("confidence", 0)
    feedback = "接受" if confidence >= 0.85 else "忽略"
    self.session_store.update_from_decision(decision, route=route, result=result)
    self.preference_store.record_feedback(user_input, query_for_ai, feedback)
    if feedback == "接受":
        self.preference_store.record_action_accept(decision, self.context)
    self.kb_writer.write_feedback(user_input, decision, feedback)
    return result


HomeMindAgent.process = _llm_first_process


def run_cli():
    """Start the interactive CLI agent."""
    agent = HomeMindAgent()
    sim = HomeSimulator()
    agent.attach_simulator(sim)
    agent.run()


def _init_protocol_gateway(mode: str):
    """Initialize the web protocol gateway based on startup mode."""
    if mode == "real":
        from core.protocols.smart_home_gateway import SmartHomeGateway

        try:
            gateway = SmartHomeGateway()
            gateway.discover_devices()
            return gateway
        except Exception as exc:
            print(f"[警告] 真实设备网关初始化失败: {exc}")
            print("[回退] 使用模拟设备模式")

    from demo.device_simulator import DeviceSimulator
    return DeviceSimulator()


def run(argv: Optional[list[str]] = None):
    """Program entrypoint required by deployment: main.run."""
    parser = argparse.ArgumentParser(description="HomeMind 中央指令器")
    parser.add_argument("--host", default="127.0.0.1", help="服务地址")
    parser.add_argument("--port", type=int, default=5000, help="服务端口")
    parser.add_argument("--debug", action="store_true", help="调试模式")
    parser.add_argument(
        "--mode",
        choices=["simulated", "real"],
        default="simulated",
        help="运行模式: simulated=模拟设备, real=真实设备",
    )
    parser.add_argument("--cli", action="store_true", help="启动交互式 CLI，而不是 Web 服务")
    args = parser.parse_args(argv)

    if args.cli:
        return run_cli()

    os.environ["HOMEMIND_MODE"] = args.mode

    from web.server import app, socketio, init_agent

    print("=" * 50)
    print("  HomeMind 中央指令器")
    print("=" * 50)
    print()
    print(f"  模式: {'模拟设备' if args.mode == 'simulated' else '真实设备'}")
    print(f"  地址: http://{args.host}:{args.port}")
    print()

    protocol_gateway = _init_protocol_gateway(args.mode)
    init_agent(mode=args.mode, protocol_gateway=protocol_gateway, init_reason="main.run")

    print()
    print(f"  控制面板: http://{args.host}:{args.port}")
    print(f"  API 状态:  http://{args.host}:{args.port}/api/status")
    print()
    print("  按 Ctrl+C 停止服务")
    print()

    socketio.run(
        app,
        host=args.host,
        port=args.port,
        debug=args.debug,
        allow_unsafe_werkzeug=True,
    )


def main():
    """Backward-compatible alias for older imports."""
    return run()


if __name__ == "__main__":
    run()
