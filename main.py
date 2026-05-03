"""
HomeMind 主入口
按照 design.md 的五层架构组织：
  交互层 → BSR → LSR → 理解层(LLM/DQN) → 执行层 → 学习层
"""

import json
import logging
import os
import sys
import argparse
import time
from typing import Optional

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from demo.context import HomeContext
from core.bsr.candidate_recall import BSRecall
from core.automation import NLToTAPConverter, TAPRuleStore
from core.automation.tap_engine import TAPEngine
from core.lsr.precision_ranking import LSRecify
from core.llm.decision import LLMDecider
from core.dqn.policy import DQNPolicy
from core.rag.knowledge_base import KnowledgeBase
from core.constants import SCENE_INDEX_MAP, SCENE_NAMES
from core.execution import CommandValidator
from core.language.normalizer import LanguageNormalizer
from core.memory import PreferenceStore, SessionStore
from core.observability import get_metrics
from core.privacy import PrivacyRedactor
from core.router import InferenceRouter
from core.governance import AuditLogger, PolicyEngine
from core.config import SECURITY_CONFIG, DQN_CONFIG, REACT_CONFIG
from core.execution.transaction_manager import ExecutionTransactionManager
from core.sec import InjectionDetector
from core.sec.autonomy_manager import AutonomyManager
from core.sec.runtime_security import RuntimeSecurityChain
from core.tools import ToolRegistry
from tools.device_control import DeviceController
from tools.info_query import InfoQuery
from tools.scene_switch import SceneSwitcher
from tools.kb_write import KBWriter
from tools.dqn_feedback import DQNFeedback
from demo.simulator import HomeSimulator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
_DLL_DIRECTORY_HANDLES = []


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
        self.metrics = get_metrics()
        self.injection_detector = InjectionDetector()
        self.audit_logger = AuditLogger()
        self.policy_engine = PolicyEngine()
        self.autonomy_manager = AutonomyManager(
            success_threshold=SECURITY_CONFIG["autonomy_success_threshold"],
            confirms_to_advance=SECURITY_CONFIG["autonomy_high_risk_confirms"],
        )
        self.runtime_security = RuntimeSecurityChain(
            policy_engine=self.policy_engine,
            autonomy_manager=self.autonomy_manager,
        )
        self.command_validator = CommandValidator(
            scene_store=None,
            rate_limit_window_s=SECURITY_CONFIG["rate_limit_window_s"],
            rate_limit_max_ops=SECURITY_CONFIG["rate_limit_max_ops"],
        )
        self.tap_rule_store = TAPRuleStore()
        self.tap_engine = TAPEngine()
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
        self.dqn = DQNPolicy(
            model_dir=os.getenv("DQN_MODEL_DIR", "models"),
            seed=int(os.getenv("DQN_SEED", "42")),
        )
        self.device_ctrl = DeviceController()
        self.info_query = InfoQuery()
        self.scene_switcher = SceneSwitcher(self.device_ctrl)
        self.kb_writer = KBWriter(self.kb)
        self.dqn_feedback = DQNFeedback(self.dqn)
        self.language_normalizer = LanguageNormalizer()
        self.nl_to_tap = NLToTAPConverter()
        self.tool_registry = ToolRegistry()
        self.tool_registry.bind_many(
            {
                "device_control": self.device_ctrl,
                "scene_switcher": self.scene_switcher,
                "info_query": self.info_query,
            }
        )
        self.transaction_manager = ExecutionTransactionManager(
            self.tool_registry,
            device_controller=self.device_ctrl,
            session_store=self.session_store,
            context=self.context if hasattr(self, "context") else None,
        )

        # 运行时注入 scene_store，解决 CommandValidator 初始化时为 None 的问题
        from core.automation.scene_store import SceneStore
        scene_store = SceneStore()
        self.command_validator.set_scene_store(scene_store)

        self.context = HomeContext()
        self.context.current_scene = ""
        self.transaction_manager.context = self.context
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
        if self.kb and os.path.exists(self.kb.backup_path):
            self.kb.restore()

    def _build_identity_context(self, route: str = "local"):
        runtime = self.session_store.get_runtime_context()
        metadata = {"route": route, "mode": "simulated"}
        return self.runtime_security.identity_manager.issue(
            user_id=runtime.get("user_id", "default"),
            session_id=runtime.get("last_updated_at", "") or "session_default",
            metadata=metadata,
        )

    def _execute_registered_command(self, decision: dict, route: str = "local") -> dict:
        execution = self.transaction_manager.execute(decision)
        if execution.get("status") == "success":
            if decision.get("action") == "场景切换":
                scene = decision.get("scene", "")
                self._sync_scene_to_simulator(scene)
                self.context.current_scene = scene
                self.context.last_scene = SCENE_INDEX_MAP.get(scene, -1)
                self.session_store.update_scene(scene)
            elif decision.get("action") == "设备控制":
                self._sync_devices_from_controller()
            if self.kb:
                self.kb.record_timeseries_summary(
                    self.context,
                    self.device_ctrl.get_all_state(),
                    trigger=decision.get("action", ""),
                    route=route,
                )
        return execution

    def _audit_event(
        self,
        *,
        trace_id: str,
        query: str,
        route: str,
        routing_reason: str,
        decision: Optional[dict] = None,
        validation=None,
        execution_result: str = "",
        error: str = "",
        started_at: float = 0.0,
    ) -> None:
        validation_payload = {
            "valid": getattr(validation, "valid", True),
            "errors": list(getattr(validation, "errors", []) or []),
        }
        latency_ms = round((time.time() - started_at) * 1000, 2) if started_at else 0.0
        self.audit_logger.log(
            trace_id=trace_id,
            user_id=self.session_store.get_runtime_context().get("user_id", "default"),
            query=query,
            intent_type=str((decision or {}).get("action", "")),
            routing_reason=routing_reason,
            route=route,
            decision=decision or {},
            execution_result=execution_result,
            execution_latency_ms=latency_ms,
            validation=validation_payload,
            error=error,
            llm_backend=getattr(self.llm, "backend", ""),
            session_id=self.session_store.get_runtime_context().get("last_updated_at", ""),
        )
        self.metrics.record_latency(latency_ms)
        if error:
            self.metrics.record_error()

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

        # TAP 自动化规则评估（每次处理用户输入时主动检查）
        matched_rules = self.tap_engine.evaluate(
            self.context,
            self.tap_rule_store.list_rules(),
        )
        if matched_rules:
            logger.info("TAP 规则触发 %d 条: %s", len(matched_rules), [r["rule"].get("name") for r in matched_rules])
            # 按优先级执行触发规则，低于当前置信度阈值的规则不执行
            for rule_match in matched_rules:
                cmd = rule_match["command"]
                if cmd.get("confidence", 1.0) >= self.confidence_threshold:
                    logger.info("执行 TAP 规则: %s", rule_match["rule"].get("name", "unnamed"))
                    self._execute_command_from_dict(cmd)

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
        logger.info(
            "云端最小上下文字段: keys=%s bytes=%d",
            sorted(cloud_context.keys()),
            len(json.dumps(cloud_context, ensure_ascii=False)),
        )
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
        if not validation.valid:
            message = "我暂时不能执行这个指令：" + ";".join(validation.errors)
            self.session_store.update_clarification(message)
            return message
        if validation.requires_confirmation:
            message = "这个操作风险较高，需要二次确认后再执行。"
            self.session_store.update_clarification(message)
            return message
        decision = validation.normalized_command

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

        # DQN 在线学习反馈回路（每次执行后记录）
        self.dqn.record_feedback(self.context, int(self._last_dqn_action or 0), feedback)
        # 持久化 DQN 事件到 PreferenceStore
        scene_name = SCENE_NAMES.get(int(self._last_dqn_action or 0), "") if self._last_dqn_action else ""
        if scene_name:
            self.preference_store.record_dqn_recommendation(
                scene=scene_name,
                action=int(self._last_dqn_action or 0),
                confidence=confidence,
                reason=f"feedback={feedback}",
                source="process",
            )
            self.preference_store.record_dqn_feedback(
                scene=scene_name,
                action=int(self._last_dqn_action or 0),
                feedback=feedback,
                reward=DQNPolicy.REWARD_MAP.get(feedback, 0.0),
                updated=False,
                buffer_size=len(self.dqn.replay),
                source="process",
            )

        # 定期触发 DQN 在线学习（每 DQN_CONFIG["update_freq"] 次记录后触发一次更新）
        if self.dqn.update_counter > 0 and self.dqn.update_counter % DQN_CONFIG["update_freq"] == 0:
            update_result = self.dqn.daily_incremental_update(min_replay=DQN_CONFIG["batch_size"])
            if update_result.get("status") == "updated":
                self.preference_store.record_dqn_learning(update_result, source="periodic")

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

    def _execute_command_from_dict(self, cmd: dict) -> str:
        """执行来自 TAP 规则或 ReAct 循环的结构化命令。"""
        action = cmd.get("action", "")
        device = cmd.get("device", "")
        device_action = cmd.get("device_action", "")
        params = cmd.get("params", {})
        scene = cmd.get("scene", "")

        if action == "设备控制":
            try:
                result = self.device_ctrl.execute(device, device_action, params)
                self._sync_devices_from_controller()
                return result
            except Exception as e:
                logger.error("TAP 设备控制失败: %s", e)
                return f"设备控制失败: {e}"

        if action == "场景切换":
            try:
                result = self.scene_switcher.execute(scene)
                self._sync_scene_to_simulator(scene)
                self.context.current_scene = scene
                self.context.last_scene = SCENE_INDEX_MAP.get(scene, -1)
                self.session_store.update_scene(scene)
                return result
            except Exception as e:
                logger.error("TAP 场景切换失败: %s", e)
                return f"场景切换失败: {e}"

        if action == "信息查询":
            try:
                return self.info_query.execute(cmd.get("query_type", "status"), params)
            except Exception as e:
                logger.error("TAP 信息查询失败: %s", e)
                return f"信息查询失败: {e}"

        return f"未知动作类型: {action}"

    def _execute_command_from_dict(self, cmd: dict) -> str:
        """Execute structured commands from TAP or future multi-step loops."""
        try:
            execution = self._execute_registered_command(cmd, route="tap")
            if execution.get("status") == "success":
                return execution.get("response", "")
            return execution.get("error") or execution.get("response") or f"未知动作类型: {cmd.get('action', '')}"
        except Exception as exc:
            logger.error("TAP structured command failed: %s", exc)
            return f"执行失败: {exc}"

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
    logger.info(
        "云端最小上下文字段: keys=%s bytes=%d",
        sorted(cloud_context.keys()),
        len(json.dumps(cloud_context, ensure_ascii=False)),
    )

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
    if not validation.valid:
        message = "我暂时不能执行这个指令：" + ";".join(validation.errors)
        self.session_store.update_clarification(message)
        return message
    if validation.requires_confirmation:
        message = "这个操作风险较高，需要二次确认后再执行。"
        self.session_store.update_clarification(message)
        return message
    decision = validation.normalized_command

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


def _main_try_cloud_rescue_intent(self: HomeMindAgent, user_input: str, query_for_ai: str) -> Optional[dict]:
    if not self.llm.is_cloud_available():
        return None
    cloud_context = self.privacy_redactor.build_cloud_context(
        self.context,
        [],
        session_store=self.session_store,
        preference_store=self.preference_store,
    )
    rescued = self.llm.rescue_intent_with_cloud(
        user_input,
        normalized_query=query_for_ai,
        context=self.context,
        context_summary=cloud_context,
    )
    if rescued.get("intent_type") == "clarification_needed":
        return None
    return rescued


def _main_try_cloud_rescue_decision(
    self: HomeMindAgent,
    user_input: str,
    goal_query: str,
    reason: str,
    ranked: Optional[list] = None,
) -> Optional[dict]:
    if not self.llm.is_cloud_available():
        return None
    ranked = list(ranked or [])
    rag_context = self.kb.get_context_prompt(goal_query, self.context)
    cloud_context = self.privacy_redactor.build_cloud_context(
        self.context,
        ranked[:3],
        session_store=self.session_store,
        preference_store=self.preference_store,
    )
    decision = self.llm.rescue_decision_with_cloud(
        goal_query,
        self.context,
        rag_context=rag_context,
        context_summary=cloud_context,
        candidate_actions=[item.get("action", "") for item in ranked[:3]],
    )
    if decision.get("confidence", 0.0) < self.confidence_threshold:
        return None

    validation = self.command_validator.validate(decision)
    if not validation.valid or validation.requires_confirmation:
        return None

    decision = validation.normalized_command
    identity = self._build_identity_context(route="cloud")
    guard = self.runtime_security.evaluate(
        decision,
        validation,
        identity,
        runtime_context={"hour": self.context.hour, "route": "cloud"},
    )
    if not guard.get("allowed", False) or guard.get("effect") == "confirm":
        return None

    execution = self._execute_registered_command(decision, route="cloud")
    if execution.get("status") != "success":
        self.runtime_security.record_outcome(decision, success=False, confirmed=False)
        return None

    result = execution.get("response", "")
    self.session_store.update_from_decision(decision, route="cloud", result=result)
    self.preference_store.record_feedback(user_input, goal_query, "接受")
    self.preference_store.record_action_accept(decision, self.context)
    self.kb_writer.write_feedback(user_input, decision, "接受")
    self.runtime_security.record_outcome(decision, success=True, confirmed=False)
    return {
        "decision": decision,
        "validation": validation,
        "result": result,
        "route": "cloud",
        "reason": reason,
    }


def _llm_first_process_v2(self: HomeMindAgent, user_input: str) -> str:
    started_at = time.time()
    trace_id = f"cli_{int(started_at * 1000)}"
    if not self.llm:
        return "AI 模块未加载"

    runtime = self.session_store.get_runtime_context()
    if not runtime.get("recent_turns"):
        try:
            self.command_validator._rate_limiter.reset()
        except Exception:
            pass
        try:
            self.runtime_security._ops.clear()
        except Exception:
            pass

    if self._simulator:
        self.context = self._simulator.get_context()
        self.context.current_scene = self.session_store.get_current_scene()
        self.transaction_manager.context = self.context

    pending = self.session_store.get_pending_confirmation()
    if pending:
        pending_reply = self._handle_pending_confirmation(user_input, pending)
        if pending_reply:
            return pending_reply

    injection = self.injection_detector.check_and_log(user_input)
    if injection.detected:
        self.session_store.update_clarification(injection.message)
        self._audit_event(
            trace_id=trace_id,
            query=user_input,
            route="clarify",
            routing_reason=injection.pattern or "prompt_injection",
            execution_result=injection.message,
            error="prompt_injection_detected",
            started_at=started_at,
        )
        return injection.message

    normalized = self.language_normalizer.normalize(user_input)
    query_for_ai = normalized.normalized or user_input
    self.session_store.update_from_query(user_input, query_for_ai)
    if query_for_ai != user_input:
        self.preference_store.record_feedback(user_input, query_for_ai, "接受")

    intent_plan = self.llm.plan_intent(user_input, normalized_query=query_for_ai, context=self.context)
    if intent_plan["intent_type"] == "chat_reply":
        message = intent_plan.get("reply_message") or "你好，我在。"
        self.session_store.append_turn("assistant", message)
        self.session_store.save()
        self._audit_event(
            trace_id=trace_id,
            query=user_input,
            route="reply",
            routing_reason=intent_plan.get("reasoning", ""),
            execution_result=message,
            started_at=started_at,
        )
        return message

    if intent_plan["intent_type"] == "clarification_needed":
        rescued_intent = _main_try_cloud_rescue_intent(self, user_input, query_for_ai)
        if rescued_intent is not None:
            intent_plan = rescued_intent
        else:
            message = intent_plan.get("reply_message") or "请问你是想控制设备、切换场景，还是创建定时任务？"
            self.session_store.update_clarification(message)
            self._audit_event(
                trace_id=trace_id,
                query=user_input,
                route="clarify",
                routing_reason=intent_plan.get("reasoning", ""),
                execution_result=message,
                started_at=started_at,
            )
            return message

    if intent_plan["intent_type"] == "chat_reply":
        message = intent_plan.get("reply_message") or "你好，我在。"
        self.session_store.append_turn("assistant", message)
        self.session_store.save()
        self._audit_event(
            trace_id=trace_id,
            query=user_input,
            route="reply",
            routing_reason=intent_plan.get("reasoning", ""),
            execution_result=message,
            started_at=started_at,
        )
        return message

    if intent_plan["intent_type"] == "automation_request":
        proposal = self._build_automation_confirmation(user_input, query_for_ai)
        if proposal:
            self.session_store.append_turn("assistant", proposal)
            self.session_store.save()
            self._audit_event(
                trace_id=trace_id,
                query=user_input,
                route="automation",
                routing_reason=intent_plan.get("reasoning", ""),
                execution_result=proposal,
                started_at=started_at,
            )
            return proposal
        message = "我理解到你想创建定时任务，但还缺少明确的时间或动作。你可以试试“晚上7:00打开空调”。"
        self.session_store.update_clarification(message)
        return message

    goal_query = intent_plan.get("normalized_goal") or query_for_ai
    unsupported = self.router.detect_unsupported_request(user_input, normalized_query=goal_query)
    if unsupported:
        message = unsupported["message"]
        self.session_store.update_clarification(message)
        self._audit_event(
            trace_id=trace_id,
            query=user_input,
            route=unsupported.get("route", "unsupported"),
            routing_reason=unsupported.get("reason", "unsupported"),
            execution_result=message,
            started_at=started_at,
        )
        return message

    candidates = self.bsr.recall(goal_query, self.context)
    ranked = self.lsr.rank(
        goal_query,
        candidates,
        self.context,
        kb=self.kb,
        session_store=self.session_store,
    )
    if not ranked:
        rescued = _main_try_cloud_rescue_decision(
            self,
            user_input,
            goal_query,
            reason="cloud_rescue_no_candidates",
            ranked=[],
        )
        if rescued is not None:
            self._audit_event(
                trace_id=trace_id,
                query=user_input,
                route=rescued["route"],
                routing_reason=rescued["reason"],
                decision=rescued["decision"],
                validation=rescued["validation"],
                execution_result=rescued["result"],
                started_at=started_at,
            )
            return rescued["result"]
        clarification = self.llm.ask_clarification(goal_query, candidates)
        self.session_store.update_clarification(clarification)
        return clarification

    route_info = self.router.decide_route(
        user_input,
        ranked,
        normalized_query=goal_query,
        cloud_available=self.llm.is_cloud_available(),
    )
    route = route_info["route"]
    if route == "clarify":
        rescued = _main_try_cloud_rescue_decision(
            self,
            user_input,
            goal_query,
            reason="cloud_rescue_from_clarify",
            ranked=ranked,
        )
        if rescued is not None:
            self._audit_event(
                trace_id=trace_id,
                query=user_input,
                route=rescued["route"],
                routing_reason=rescued["reason"],
                decision=rescued["decision"],
                validation=rescued["validation"],
                execution_result=rescued["result"],
                started_at=started_at,
            )
            return rescued["result"]
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
    if route == "cloud":
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

    if decision.get("confidence", 0.0) < self.confidence_threshold:
        rescued = None
        if route != "cloud":
            rescued = _main_try_cloud_rescue_decision(
                self,
                user_input,
                goal_query,
                reason="cloud_rescue_low_confidence",
                ranked=ranked,
            )
        if rescued is not None:
            self._audit_event(
                trace_id=trace_id,
                query=user_input,
                route=rescued["route"],
                routing_reason=rescued["reason"],
                decision=rescued["decision"],
                validation=rescued["validation"],
                execution_result=rescued["result"],
                started_at=started_at,
            )
            return rescued["result"]
        clarification = self.llm.ask_clarification(goal_query, ranked)
        self.session_store.update_clarification(clarification)
        self._audit_event(
            trace_id=trace_id,
            query=user_input,
            route=route,
            routing_reason="low_decision_confidence",
            decision=decision,
            execution_result=clarification,
            started_at=started_at,
        )
        return clarification

    validation = self.command_validator.validate(decision)
    if not validation.valid:
        message = "我暂时不能执行这个指令：" + ";".join(validation.errors)
        self.session_store.update_clarification(message)
        self._audit_event(
            trace_id=trace_id,
            query=user_input,
            route=route,
            routing_reason=route_info.get("reason", ""),
            decision=decision,
            validation=validation,
            execution_result=message,
            started_at=started_at,
        )
        return message

    decision = validation.normalized_command
    identity = self._build_identity_context(route=route)
    guard = self.runtime_security.evaluate(
        decision,
        validation,
        identity,
        runtime_context={"hour": self.context.hour, "route": route},
    )
    if not guard.get("allowed", False):
        message = "我暂时不能执行这个指令：" + guard.get("reason", "runtime_guard_denied")
        self.session_store.update_clarification(message)
        self._audit_event(
            trace_id=trace_id,
            query=user_input,
            route=route,
            routing_reason=guard.get("reason", ""),
            decision=decision,
            validation=validation,
            execution_result=message,
            error="runtime_guard_denied",
            started_at=started_at,
        )
        return message
    if guard.get("effect") == "confirm":
        message = "这个操作需要确认后再执行。"
        self.session_store.update_clarification(message)
        self._audit_event(
            trace_id=trace_id,
            query=user_input,
            route=route,
            routing_reason=guard.get("reason", ""),
            decision=decision,
            validation=validation,
            execution_result=message,
            started_at=started_at,
        )
        return message

    execution = self._execute_registered_command(decision, route=route)
    if execution.get("status") != "success":
        message = execution.get("error") or execution.get("response") or "执行失败"
        self.session_store.update_clarification(message)
        self.runtime_security.record_outcome(decision, success=False, confirmed=False)
        self._audit_event(
            trace_id=trace_id,
            query=user_input,
            route=route,
            routing_reason=route_info.get("reason", ""),
            decision=decision,
            validation=validation,
            execution_result=message,
            error="execution_failed",
            started_at=started_at,
        )
        return message

    result = execution.get("response", "")
    feedback = "接受" if decision.get("confidence", 0.0) >= 0.85 else "忽略"
    self.session_store.update_from_decision(decision, route=route, result=result)
    self.preference_store.record_feedback(user_input, query_for_ai, feedback)
    if feedback == "接受":
        self.preference_store.record_action_accept(decision, self.context)
    self.kb_writer.write_feedback(user_input, decision, feedback)
    self.runtime_security.record_outcome(decision, success=True, confirmed=False)
    self._audit_event(
        trace_id=trace_id,
        query=user_input,
        route=route,
        routing_reason=route_info.get("reason", ""),
        decision=decision,
        validation=validation,
        execution_result=result,
        started_at=started_at,
    )
    return result


HomeMindAgent.process = _llm_first_process_v2


def _prepend_runtime_path(path: str):
    current = os.environ.get("PATH", "")
    parts = [p for p in current.split(os.pathsep) if p]
    normalized = {os.path.normcase(os.path.normpath(p)) for p in parts}
    target = os.path.normcase(os.path.normpath(path))
    if target in normalized:
        return
    os.environ["PATH"] = path if not current else f"{path}{os.pathsep}{current}"


def _bootstrap_windows_cuda_runtime():
    """Prepare DLL lookup paths for CUDA-backed local inference on Windows."""
    if os.name != "nt":
        return

    conda_prefix = os.environ.get("CONDA_PREFIX") or sys.prefix
    candidates = [
        os.path.join(conda_prefix, "Lib", "site-packages", "torch", "lib"),
        os.path.join(conda_prefix, "bin"),
    ]
    prepared = []

    for path in candidates:
        if not os.path.isdir(path):
            continue
        _prepend_runtime_path(path)
        if hasattr(os, "add_dll_directory"):
            try:
                handle = os.add_dll_directory(path)
                _DLL_DIRECTORY_HANDLES.append(handle)
            except OSError as exc:
                logger.warning("Failed to add DLL directory %s: %s", path, exc)
        prepared.append(path)

    if prepared:
        logger.info("Prepared CUDA runtime search paths: %s", prepared)


def run_cli():
    """Start the interactive CLI agent."""
    _bootstrap_windows_cuda_runtime()
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
    _bootstrap_windows_cuda_runtime()
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
