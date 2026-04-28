"""
HomeMind Web 服务 - 中央指令器
提供 REST API 和 WebSocket 接口，连接智能家居 Agent 与前端控制面板
"""
import inspect
import json
import threading
import time
from copy import deepcopy
from datetime import datetime
import os
import sys
import types

try:
    import asyncio as _asyncio_probe
    getattr(_asyncio_probe, "iscoroutinefunction")
    ASYNCIO_AVAILABLE = True
except Exception:
    asyncio_stub = types.ModuleType("asyncio")
    asyncio_stub.iscoroutinefunction = inspect.iscoroutinefunction
    sys.modules["asyncio"] = asyncio_stub
    ASYNCIO_AVAILABLE = False

from flask import Flask, request, jsonify
try:
    from flask_cors import CORS
except ImportError:
    def CORS(app, resources=None):
        return app
try:
    if not ASYNCIO_AVAILABLE:
        raise ImportError("asyncio unavailable")
    from flask_socketio import SocketIO, emit
except Exception:
    class SocketIO:
        def __init__(self, app, cors_allowed_origins="*", async_mode="threading"):
            self.app = app

        def emit(self, event, data=None):
            return None

        def on(self, event):
            def decorator(func):
                return func
            return decorator

        def run(self, app, host="127.0.0.1", port=5000, debug=False, allow_unsafe_werkzeug=True):
            return app.run(host=host, port=port, debug=debug)

    def emit(event, data=None):
        return None
from queue import Queue

from core.bsr.candidate_recall import BSRecall
from core.automation import NLToTAPConverter, SceneStore, TAPEngine, TAPRuleStore
from core.execution import CommandValidator
from core.lsr.precision_ranking import LSRecify as PrecisionRanking
from core.llm.decision import LLMDecider as LLMWrapper
from core.dqn.policy import DQNPolicy
from core.rag.knowledge_base import KnowledgeBase
from core.utils.embedding import get_model as get_embedding_model
from core.language.normalizer import LanguageNormalizer
from core.memory import PreferenceStore, SessionStore
from core.privacy import PrivacyRedactor
from core.router import InferenceRouter
from core.voice.vosk_asr import VoskASR
from core.voice.feedback_store import VoiceFeedbackStore
from core.constants import SCENE_INDEX_MAP, SCENE_NAMES
from demo.context import HomeContext
from demo.device_simulator import DeviceSimulator
import tools.device_control as device_ctrl
import tools.scene_switch as scene_switch
import tools.info_query as info_query
import tools.kb_write as kb_writer
import tools.dqn_feedback
from tools.dqn_feedback import DQNFeedback as DQNFeedbackTool

# Web 服务配置
app = Flask(__name__, static_folder='client', static_url_path='/web/client')
CORS(app, resources={r"/api/*": {"origins": "*"}})
app.config['SECRET_KEY'] = 'homemind-secret-key-2024'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# 全局消息队列（Agent ↔ Web 前端）
agent_queue = Queue()

# 全局 Agent 实例
agent = None
device_simulator = None
protocol_gateway = None
voice_asr = VoskASR()
voice_feedback_store = VoiceFeedbackStore()
language_normalizer = LanguageNormalizer(feedback_store=voice_feedback_store)


class HomeMindWebAgent:
    """支持 Web 接口的 HomeMind Agent"""
    
    def __init__(self, protocol_gateway=None):
        self._gateway = protocol_gateway
        self.confidence_threshold = 0.75
        self._interaction_records = {}
        self._message_counter = 0
        self._init_components()
        self._start_agent_loop()
        self._start_scheduler_loop()
    
    def _init_components(self):
        """初始化所有组件"""
        print("[初始化] HomeMind Web Agent 组件...")

        self.session_store = SessionStore()
        self.preference_store = PreferenceStore()
        self.privacy_redactor = PrivacyRedactor()
        self.router = InferenceRouter()
        self.tap_engine = TAPEngine()
        self.tap_rule_store = TAPRuleStore()
        self.scene_store = SceneStore()
        self.nl_to_tap = NLToTAPConverter()
        self.command_validator = CommandValidator(scene_store=self.scene_store)
        self.last_cloud_context = {}
        self.last_route_info = {}
        self.scheduler_enabled = True
        self.scheduler_interval = float(os.environ.get("HOMEMIND_RULE_SCHEDULER_INTERVAL", "5"))
        self.dqn_scheduler_interval = float(os.environ.get("HOMEMIND_DQN_SCHEDULER_INTERVAL", "300"))
        self._last_rule_fire = {}
        self._last_dqn_recommend_at = 0.0
        
        # 初始化上下文
        self.context = HomeContext()
        self.context.current_scene = "sleep"
        self.context.temperature = 25.0
        self.context.humidity = 60.0
        self.context.members_home = 1
        
        # 初始化设备模拟器
        self.device_simulator = DeviceSimulator()
        self.simulator = self.device_simulator
        
        # 初始化工具（传入协议网关）
        self.device_control = device_ctrl.DeviceController(protocol_gateway=self._gateway)
        self.info_query = info_query.InfoQuery()
        self.scene_switcher = scene_switch.SceneSwitcher(self.device_control, scene_store=self.scene_store)
        self.language_normalizer = language_normalizer
        
        # 尝试初始化 Embedding 和知识库
        self.embedding_model = None
        self.kb = None
        self.kb_writer = None
        try:
            self.embedding_model = get_embedding_model()
            embedding_fn = self.embedding_model.encode if self.embedding_model else None
            self.kb = KnowledgeBase(embedding_fn=embedding_fn)
            self.kb.preference_store = self.preference_store
            self.kb_writer = kb_writer.KBWriter(self.kb)
            print("[初始化] 知识库已加载")
        except Exception as e:
            print(f"[警告] 知识库初始化失败: {e}")
            self.kb = None
            self.kb_writer = None
        
        # 初始化 BSR/LLM/DQN（带降级处理）
        self.bsr = None
        self.lsr = None
        self.llm = None
        self.dqn = None
        self.dqn_fb = None
        
        if self.kb:
            try:
                self.bsr = BSRecall(kb=self.kb)
                print("[初始化] BSR 召回模块已加载")
            except Exception as e:
                print(f"[警告] BSR 初始化失败: {e}")
        
        try:
            self.lsr = PrecisionRanking()
            print("[初始化] LSR 精排模块已加载")
        except Exception as e:
            print(f"[警告] LSR 初始化失败: {e}")
        
        try:
            self.llm = LLMWrapper(
                backend=os.environ.get("LLM_BACKEND", "mock"),
                model_path=os.environ.get("LLM_MODEL_PATH", ""),
                api_base=os.environ.get("LLM_API_BASE", ""),
                api_key=os.environ.get("LLM_API_KEY", ""),
                cloud_model=os.environ.get("LLM_MODEL", ""),
            )
            print("[初始化] LLM 决策模块已加载")
        except Exception as e:
            print(f"[警告] LLM 初始化失败: {e}")
        
        try:
            self.dqn = DQNPolicy()
            self.dqn_fb = DQNFeedbackTool(self.dqn)
            print("[初始化] DQN 策略模块已加载")
        except Exception as e:
            print(f"[警告] DQN 初始化失败: {e}")
        
        self._restore_persisted_state()
        print("[初始化] 完成!")

    def _restore_persisted_state(self):
        current_scene = self.session_store.get_current_scene()
        if current_scene:
            self.context.current_scene = current_scene
            self.context.last_scene = SCENE_INDEX_MAP.get(current_scene, -1)

    def _record_query_context(self, raw_text: str, normalized_text: str):
        self.session_store.update_from_query(raw_text, normalized_text)
        if normalized_text and normalized_text != raw_text:
            self.preference_store.record_feedback(raw_text, normalized_text, "接受")

    def _next_message_id(self, prefix: str = "msg") -> str:
        self._message_counter += 1
        return f"{prefix}_{int(time.time() * 1000)}_{self._message_counter}"

    def _register_interaction(self, record: dict) -> dict:
        message_id = record.get("message_id") or self._next_message_id()
        stored = dict(record)
        stored["message_id"] = message_id
        self._interaction_records[message_id] = stored
        return stored

    def _build_message_metadata(
        self,
        target_type: str,
        original_input: str,
        normalized_input: str,
        decision_snapshot: dict | None = None,
        extra: dict | None = None,
        message_id: str = "",
    ) -> dict:
        record = {
            "message_id": message_id or self._next_message_id(target_type),
            "target_type": target_type,
            "original_input": original_input,
            "normalized_input": normalized_input,
            "decision_snapshot": dict(decision_snapshot or {}),
        }
        if extra:
            record.update(extra)
        return self._register_interaction(record)

    def _build_automation_proposal(self, raw_text: str, normalized_text: str) -> dict | None:
        rule = self.nl_to_tap.parse(raw_text) or self.nl_to_tap.parse(normalized_text or raw_text)
        if not rule:
            return None
        trigger = dict(rule.get("trigger", {}) or {})
        action = dict(rule.get("action", {}) or {})
        summary = self._summarize_automation_rule(trigger, action)
        proposal_id = self._next_message_id("proposal")
        rule_preview = {
            "name": rule.get("name", raw_text[:40]),
            "trigger": trigger,
            "action": action,
            "priority": rule.get("priority", 50),
        }
        message_id = self._next_message_id("automation")
        interaction = self._build_message_metadata(
            "automation_proposal",
            raw_text,
            normalized_text,
            decision_snapshot={"rule": rule_preview},
            extra={"proposal_id": proposal_id, "rule_preview": rule_preview},
            message_id=message_id,
        )
        proposal = {
            "message_id": interaction["message_id"],
            "proposal_id": proposal_id,
            "summary": summary,
            "rule_preview": rule_preview,
            "confirm_actions": ["accept", "change", "reject"],
        }
        self.session_store.set_pending_confirmation(
            "automation_proposal",
            proposal,
            feedback_target={"message_id": interaction["message_id"], "target_type": "automation_proposal"},
        )
        return proposal

    def _summarize_automation_rule(self, trigger: dict, action: dict) -> str:
        time_text = trigger.get("at", "--:--") if trigger.get("type") == "time" else "满足条件时"
        if action.get("type") == "scene_switch":
            action_text = f"切换到{action.get('scene', '指定场景')}"
        else:
            device = action.get("device", "设备")
            device_action = action.get("device_action", "执行动作")
            action_text = f"{device}{device_action}"
        return f"我理解成：每天 {time_text} {action_text}。要为你创建定时任务吗？"

    def _accept_automation_proposal(self, message_id: str, original_input: str, normalized_input: str) -> dict:
        pending = self.session_store.get_pending_confirmation()
        payload = dict(pending.get("payload", {}) or {})
        rule_preview = dict(payload.get("rule_preview", {}) or {})
        if not rule_preview:
            interaction = self._interaction_records.get(message_id, {})
            rule_preview = dict(interaction.get("rule_preview", {}) or interaction.get("decision_snapshot", {}).get("rule", {}) or {})
        if not rule_preview:
            raise ValueError("没有待确认的定时任务")
        created_rule = self.tap_rule_store.add_rule({
            "name": rule_preview.get("name", original_input[:40]),
            "enabled": True,
            "trigger": dict(rule_preview.get("trigger", {}) or {}),
            "conditions": [],
            "action": dict(rule_preview.get("action", {}) or {}),
            "priority": int(rule_preview.get("priority", 50)),
        })
        self.session_store.clear_pending_confirmation()
        self.preference_store.record_interaction_feedback("automation_proposal", "accept")
        if self.kb:
            self.kb.add(
                f"用户确认创建自动化：{original_input}",
                category="用户反馈",
                accepted=True,
                feedback="接受",
                target_type="automation_proposal",
            )
        created_message_id = self._next_message_id("automation_created")
        self._build_message_metadata(
            "automation_created",
            original_input,
            normalized_input,
            decision_snapshot={"rule": created_rule},
            message_id=created_message_id,
        )
        return {
            "status": "success",
            "response_type": "automation_created",
            "message_id": created_message_id,
            "response": f"好的，已为你创建定时任务：{created_rule.get('name', '未命名规则')}",
            "rule": created_rule,
        }

    def handle_interaction_feedback(self, payload: dict) -> dict:
        message_id = str(payload.get("message_id", "")).strip()
        feedback_type = str(payload.get("feedback_type", "")).strip()
        target_type = str(payload.get("target_type", "")).strip() or "decision"
        original_input = str(payload.get("original_input", "")).strip()
        normalized_input = str(payload.get("normalized_input", "")).strip()
        correction = str(payload.get("correction", "")).strip()
        decision_snapshot = dict(payload.get("decision_snapshot", {}) or {})

        interaction = self._interaction_records.get(message_id, {})
        if interaction:
            target_type = target_type or interaction.get("target_type", "decision")
            original_input = original_input or interaction.get("original_input", "")
            normalized_input = normalized_input or interaction.get("normalized_input", "")
            decision_snapshot = decision_snapshot or dict(interaction.get("decision_snapshot", {}) or {})

        self.preference_store.record_interaction_feedback(target_type, feedback_type)

        if feedback_type == "accept":
            if target_type == "automation_proposal":
                return self._accept_automation_proposal(message_id, original_input, normalized_input)
            if target_type == "recommendation":
                action = int(interaction.get("action", 5))
                if self.dqn_fb:
                    self.dqn_fb.record(self.context, action, "接受")
                self.preference_store.record_recommendation_feedback(SCENE_NAMES.get(action, ""), "接受")
            elif decision_snapshot:
                self.preference_store.record_action_accept(decision_snapshot, self.context)
            if self.kb:
                self.kb.add(
                    f"用户确认反馈：{original_input or normalized_input}",
                    category="用户反馈",
                    accepted=True,
                    feedback="接受",
                    target_type=target_type,
                )
            return {"status": "success", "message": "反馈已记录"}

        if feedback_type == "reject":
            if target_type == "recommendation":
                action = int(interaction.get("action", 5))
                if self.dqn_fb:
                    self.dqn_fb.record(self.context, action, "拒绝")
                self.preference_store.record_recommendation_feedback(SCENE_NAMES.get(action, ""), "拒绝")
            self.session_store.clear_pending_confirmation()
            if self.kb:
                self.kb.add(
                    f"用户拒绝反馈：{original_input or normalized_input}",
                    category="用户反馈",
                    accepted=False,
                    feedback="拒绝",
                    target_type=target_type,
                )
            return {"status": "success", "message": "已记录拒绝反馈"}

        if feedback_type == "change":
            corrected = correction or normalized_input
            if original_input and corrected:
                self.preference_store.record_feedback(original_input, corrected, "纠正")
            if self.kb:
                self.kb.add(
                    f"用户纠正反馈：原始输入「{original_input}」，纠正为「{corrected}」",
                    category="用户反馈",
                    accepted=True,
                    feedback="纠正",
                    target_type=target_type,
                )
            if target_type == "automation_proposal" and corrected:
                proposal = self._build_automation_proposal(original_input or corrected, corrected)
                if proposal:
                    return {
                        "status": "success",
                        "response_type": "automation_proposal",
                        "message": proposal["summary"],
                        "proposal": proposal,
                    }
            return {"status": "success", "message": "纠正反馈已记录"}

        return {"status": "success", "message": "反馈已记录"}

    def _build_cloud_context(self, candidates):
        payload = self.privacy_redactor.build_cloud_context(
            self.context,
            candidates,
            session_store=self.session_store,
            preference_store=self.preference_store,
        )
        self.last_cloud_context = payload
        return payload

    def _record_success(self, decision: dict, result_text: str):
        route = "cloud" if getattr(self.llm, "backend", "mock") == "openai" else "local"
        self.session_store.update_from_decision(decision, route=route, result=result_text)
        self.preference_store.record_action_accept(decision, self.context)

    def _validate_decision(self, decision: dict):
        return self.command_validator.validate(decision)

    def _detect_unsupported_request(self, raw_text: str, normalized_text: str = ""):
        unsupported = self.router.detect_unsupported_request(raw_text, normalized_query=normalized_text)
        if unsupported:
            self.last_route_info = unsupported
            self.session_store.update_clarification(unsupported["message"])
        return unsupported

    def _execute_structured_command(self, command: dict, route: str = "tap") -> dict:
        validation = self._validate_decision(command)
        if not validation["valid"]:
            return {"status": "invalid", "errors": validation["errors"], "command": command}
        if validation["requires_confirmation"]:
            return {"status": "confirmation_required", "command": validation["normalized_command"]}

        normalized = validation["normalized_command"]
        action_type = normalized.get("action", "")
        device = normalized.get("device", "")
        device_action = normalized.get("device_action", "")
        scene = normalized.get("scene", "")
        params = normalized.get("params", {})

        if action_type == "设备控制" and device and device_action:
            message = self.device_control.execute(device, device_action, params)
            self.session_store.update_from_decision(normalized, route=route, result=message)
            self.preference_store.record_action_accept(normalized, self.context)
            return {
                "status": "success",
                "action": f"{device}_{device_action}",
                "response": message,
                "command": normalized,
            }
        if action_type == "场景切换" and scene:
            message = self.scene_switcher.execute(scene)
            self.context.current_scene = scene
            self.context.last_scene = SCENE_INDEX_MAP.get(scene, -1)
            self.session_store.update_scene(scene)
            self.session_store.update_from_decision(normalized, route=route, result=message)
            self.preference_store.record_action_accept(normalized, self.context)
            return {
                "status": "success",
                "action": "scene_switch",
                "response": message,
                "command": normalized,
            }
        if action_type == "信息查询":
            result = self.info_query.execute(normalized.get("query_type", "status"), params)
            return {
                "status": "success",
                "action": "info_query",
                "response": result,
                "command": normalized,
            }
        return {"status": "no_action", "command": normalized}

    def _context_snapshot(self):
        snapshot = HomeContext()
        snapshot.hour = self.context.hour
        snapshot.temperature = self.context.temperature
        snapshot.humidity = self.context.humidity
        snapshot.members_home = self.context.members_home
        snapshot.day_of_week = self.context.day_of_week
        snapshot.last_scene = self.context.last_scene
        snapshot.devices = deepcopy(getattr(self.context, "devices", {}))
        snapshot.current_scene = getattr(self.context, "current_scene", "")
        return snapshot

    def evaluate_rules(self, execute: bool = False, context_overrides: dict = None, now: datetime = None) -> dict:
        evaluation_context = self._context_snapshot()
        for key, value in (context_overrides or {}).items():
            if hasattr(evaluation_context, key):
                setattr(evaluation_context, key, value)
            elif key == "current_scene":
                evaluation_context.current_scene = value

        matches = self.tap_engine.evaluate(
            evaluation_context,
            self.tap_rule_store.list_rules(),
            now=now,
        )
        results = []
        for item in matches:
            payload = {
                "rule": item["rule"],
                "command": item["command"],
            }
            if execute:
                payload["execution"] = self._execute_structured_command(item["command"], route="tap")
            results.append(payload)
        return {"status": "success", "matches": results}

    def _scheduler_fire_key(self, rule: dict, now: datetime) -> str:
        return f"{rule.get('id', '')}:{now.strftime('%Y-%m-%d %H:%M')}"

    def _scheduler_tick(self, now: datetime = None) -> dict:
        now = now or datetime.now()
        if not self.scheduler_enabled:
            return {"status": "disabled", "executed": []}

        evaluated = self.evaluate_rules(execute=False, now=now)
        executed = []
        for item in evaluated.get("matches", []):
            rule = item.get("rule", {})
            fire_key = self._scheduler_fire_key(rule, now)
            if fire_key in self._last_rule_fire:
                continue
            execution = self._execute_structured_command(item.get("command", {}), route="tap")
            self._last_rule_fire[fire_key] = now.isoformat()
            executed.append({
                "rule": rule,
                "command": item.get("command", {}),
                "execution": execution,
            })
            if execution.get("status") == "success":
                socketio.emit("message", {
                    "type": "automation_update",
                    "data": {
                        "rule": rule.get("name", ""),
                        "result": execution.get("response", ""),
                    }
                })

        stale = []
        for key, timestamp in self._last_rule_fire.items():
            try:
                fired_at = datetime.fromisoformat(timestamp)
            except ValueError:
                stale.append(key)
                continue
            if (now - fired_at).total_seconds() > 3600:
                stale.append(key)
        for key in stale:
            self._last_rule_fire.pop(key, None)

        dqn_recommendation = self._dqn_proactive_recommend(now)
        if dqn_recommendation:
            executed.append({"type": "dqn_recommendation", "recommendation": dqn_recommendation})

        return {"status": "success", "executed": executed}

    def _dqn_proactive_recommend(self, now: datetime = None) -> dict:
        if not self.dqn:
            return {}
        current = time.time()
        if current - self._last_dqn_recommend_at < self.dqn_scheduler_interval:
            return {}

        self.context.hour = (now or datetime.now()).hour
        action_idx, confidence = self.dqn.recommend(self.context)
        if action_idx == 5:
            self._last_dqn_recommend_at = current
            return {}

        scene_name = SCENE_NAMES.get(action_idx, "")
        if not scene_name:
            return {}

        recommendation = {
            "id": f"dqn_{action_idx}",
            "scene": scene_name,
            "scene_id": self._scene_id_from_name(scene_name),
            "reason": f"基于当前环境状态推荐{scene_name}",
            "confidence": confidence,
        }
        interaction = self._build_message_metadata(
            "recommendation",
            recommendation["reason"],
            recommendation["scene"],
            decision_snapshot={"scene": scene_name, "confidence": confidence},
            extra={"action": action_idx},
        )
        recommendation["message_id"] = interaction["message_id"]
        self._last_dqn_recommend_at = current
        socketio.emit("message", {
            "type": "dqn_recommendation",
            "data": recommendation,
        })
        return recommendation

    def _start_scheduler_loop(self):
        def scheduler_worker():
            while True:
                try:
                    self.context.hour = datetime.now().hour
                    self._scheduler_tick()
                    time.sleep(max(1.0, self.scheduler_interval))
                except Exception as e:
                    print(f"[Scheduler Error] {e}")
                    time.sleep(2)

        self.scheduler_thread = threading.Thread(target=scheduler_worker, daemon=True)
        self.scheduler_thread.start()

    def get_scheduler_status(self) -> dict:
        return {
            "status": "success",
            "enabled": self.scheduler_enabled,
            "interval_seconds": self.scheduler_interval,
            "dqn_interval_seconds": self.dqn_scheduler_interval,
            "rule_count": len(self.tap_rule_store.list_rules()),
        }

    def set_scheduler_enabled(self, enabled: bool) -> dict:
        self.scheduler_enabled = bool(enabled)
        return self.get_scheduler_status()

    def get_preference_snapshot(self) -> dict:
        return self.preference_store.snapshot()

    def get_memory_summary(self) -> dict:
        session = self.session_store.get_runtime_context()
        preferences = self.preference_store.snapshot()
        recent_memory = []
        if self.kb:
            try:
                recent_memory = self.kb.memory_store[-5:]
            except Exception:
                recent_memory = []
        return {
            "status": "success",
            "session": {
                "current_scene": session.get("current_scene", ""),
                "last_user_input": session.get("last_user_input", ""),
                "last_normalized_input": session.get("last_normalized_input", ""),
                "last_action": session.get("last_action", {}),
                "last_clarification": session.get("last_clarification", {}),
                "recent_turns": session.get("recent_turns", []),
            },
            "preferences": {
                "devices": preferences.get("devices", {}),
                "scenes": preferences.get("scenes", {}),
                "recommendation": preferences.get("recommendation", {}),
                "language": preferences.get("language", {}),
            },
            "recent_memory": recent_memory,
        }

    def get_privacy_status(self) -> dict:
        backend = getattr(self.llm, "backend", "mock")
        cloud_enabled = backend == "openai" and self.llm.is_cloud_available()
        session = self.session_store.get_runtime_context()
        route = session.get("last_route", self.last_route_info.get("route", "local"))
        return {
            "status": "success",
            "cloud_enabled": cloud_enabled,
            "llm_backend": backend,
            "last_route": route,
            "last_route_reason": self.last_route_info.get("reason", ""),
            "last_cloud_context": self.last_cloud_context,
            "minimal_fields": ["hour", "temperature", "humidity", "occupancy", "scene", "top_candidates", "preference_summary"],
        }
    
    def _start_agent_loop(self):
        """启动 Agent 处理循环（后台线程）"""
        def agent_worker():
            while True:
                try:
                    if not agent_queue.empty():
                        message = agent_queue.get()
                        self._handle_message(message)
                    time.sleep(0.1)
                except Exception as e:
                    print(f"[Agent Loop Error] {e}")
                    time.sleep(1)
        
        self.agent_thread = threading.Thread(target=agent_worker, daemon=True)
        self.agent_thread.start()
    
    def _handle_message(self, message: dict):
        """处理来自 Web 前端的消息"""
        msg_type = message.get("type")
        data = message.get("data", {})
        
        if msg_type == "user_input":
            self._process_user_input(data)
        elif msg_type == "device_control":
            self._handle_device_control(data)
        elif msg_type == "scene_switch":
            self._handle_scene_switch(data)
        elif msg_type == "dqn_recommendation_response":
            self._handle_dqn_response(data)
    
    def _process_user_input(self, data: dict):
        """处理用户自然语言输入"""
        user_text = data.get("text", "")
        normalized = self.language_normalizer.normalize(user_text)
        query_text = normalized.normalized or user_text
        self._record_query_context(user_text, query_text)
        query_id = f"q_{int(time.time() * 1000)}"
        print(f"[Agent] 收到用户输入: {user_text}")

        # 更新上下文时间
        self.context.hour = datetime.now().hour

        # 初始化流水线状态
        pipeline = {
            "query_id": query_id,
            "query": user_text,
            "normalized_query": normalized.to_dict(),
            "steps": {
                "bsr": {"status": "pending", "candidates": []},
                "lsr": {"status": "pending", "ranked": []},
                "llm": {"status": "pending", "decision": None},
                "exec": {"status": "pending", "result": None},
            }
        }
        socketio.emit("pipeline_update", {"type": "pipeline_start", "data": pipeline})

        intent_info = self.router.classify_intent(user_text, normalized_query=query_text)
        if intent_info["route"] == "reply":
            interaction = self._build_message_metadata(
                "decision",
                user_text,
                query_text,
                decision_snapshot={"intent_type": intent_info["intent_type"], "route": "reply"},
            )
            payload = {
                "action": "chat_reply",
                "result": intent_info["reply_message"],
                "status": "success",
                "response_type": "chat",
                "message_id": interaction["message_id"],
                "query_id": query_id,
                "route": intent_info["route"],
                "route_reason": intent_info["reason"],
                "feedback_target": {"message_id": interaction["message_id"], "target_type": "decision"},
            }
            pipeline["steps"]["exec"] = {"status": "done", "result": payload}
            socketio.emit("pipeline_update", {"type": "pipeline_step", "data": {
                "query_id": query_id, "step": "exec", "data": pipeline["steps"]["exec"]
            }})
            socketio.emit("message", {"type": "agent_response", "data": payload})
            return

        if intent_info["route"] == "automation":
            proposal = self._build_automation_proposal(user_text, query_text)
            if proposal:
                payload = {
                    "action": "automation_proposal",
                    "result": proposal["summary"],
                    "status": "success",
                    "response_type": "automation_proposal",
                    "message_id": proposal["message_id"],
                    "proposal": proposal,
                    "query_id": query_id,
                    "route": intent_info["route"],
                    "route_reason": intent_info["reason"],
                    "feedback_target": {"message_id": proposal["message_id"], "target_type": "automation_proposal"},
                }
                pipeline["steps"]["exec"] = {"status": "done", "result": payload}
                socketio.emit("pipeline_update", {"type": "pipeline_step", "data": {
                    "query_id": query_id, "step": "exec", "data": pipeline["steps"]["exec"]
                }})
                socketio.emit("message", {"type": "agent_response", "data": payload})
                return

        if intent_info["route"] == "unsupported":
            result = {
                "status": "unsupported",
                "action": "unsupported",
                "message": intent_info["message"],
                "target": intent_info["target"],
            }
            pipeline["steps"]["exec"] = {"status": "done", "result": result}
            socketio.emit("pipeline_update", {"type": "pipeline_step", "data": {
                "query_id": query_id, "step": "exec", "data": pipeline["steps"]["exec"]
            }})
            socketio.emit("message", {
                "type": "agent_response",
                "data": {
                    "action": "unsupported",
                    "result": intent_info["message"],
                    "status": "unsupported",
                    "response_type": "clarification",
                    "target": intent_info["target"],
                    "query_id": query_id,
                    "route": intent_info["route"],
                    "route_reason": intent_info["reason"],
                }
            })
            return

        # 尝试使用完整流程，否则使用简单规则匹配
        if self.bsr and self.lsr and self.llm:
            try:
                # Step 1: BSR 召回
                candidates = self.bsr.recall(query_text, self.context)
                pipeline["steps"]["bsr"] = {
                    "status": "done",
                    "candidates": [
                        {"id": i, "action": c.get("action", ""), "score": float(c.get("score", 0))}
                        for i, c in enumerate(candidates)
                    ]
                }
                socketio.emit("pipeline_update", {"type": "pipeline_step", "data": {
                    "query_id": query_id, "step": "bsr", "data": pipeline["steps"]["bsr"]
                }})

                if not candidates:
                    self._emit_fallback(query_id, query_text)
                    return

                # Step 2: LSR 精排
                ranked = self.lsr.rank(
                    query_text,
                    candidates,
                    self.context,
                    kb=self.kb,
                    session_store=self.session_store,
                )
                pipeline["steps"]["lsr"] = {
                    "status": "done",
                    "ranked": [
                        {"id": i, "action": r.get("action", ""), "score": float(r.get("final_score", 0))}
                        for i, r in enumerate(ranked)
                    ]
                }
                socketio.emit("pipeline_update", {"type": "pipeline_step", "data": {
                    "query_id": query_id, "step": "lsr", "data": pipeline["steps"]["lsr"]
                }})

                if not ranked:
                    self._emit_fallback(query_id, query_text)
                    return

                route_info = self.router.decide_route(
                    user_text,
                    ranked,
                    normalized_query=query_text,
                    cloud_available=self.llm.is_cloud_available(),
                )
                self.last_route_info = route_info

                # Step 3: LLM 决策
                cloud_context = self._build_cloud_context(ranked[:3])
                rag_context = self.kb.get_context_prompt(query_text, self.context) if self.kb else ""
                print(f"[Privacy] 云端最小上下文: {cloud_context}")
                if route_info["route"] == "clarify":
                    question = self.llm.ask_clarification(query_text, ranked)
                    self.session_store.update_clarification(question)
                    socketio.emit("message", {
                        "type": "agent_clarification",
                        "data": {
                            "question": question,
                            "candidates": route_info["top_candidates"],
                            "query_id": query_id
                        }
                    })
                    return
                if route_info["route"] == "cloud":
                    decision = self.llm.decide_cloud(
                        query_text,
                        ranked,
                        self.context,
                        rag_context=rag_context,
                        context_summary=cloud_context,
                    )
                else:
                    decision = self.llm.decide_local(
                        query_text,
                        ranked,
                        self.context,
                        rag_context=rag_context,
                    )
                action_type = decision.get("action", "")
                device = decision.get("device", "")
                device_action = decision.get("device_action", "")
                scene = decision.get("scene", "")
                params = decision.get("params", {})
                confidence = decision.get("confidence", 0.9)
                reasoning = decision.get("reasoning", "")

                pipeline["steps"]["llm"] = {
                    "status": "done",
                    "decision": {
                        "device": device,
                        "device_action": device_action,
                        "params": params,
                        "confidence": float(confidence),
                        "reasoning": reasoning,
                        "route": route_info["route"],
                        "route_reason": route_info["reason"],
                    }
                }
                socketio.emit("pipeline_update", {"type": "pipeline_step", "data": {
                    "query_id": query_id, "step": "llm", "data": pipeline["steps"]["llm"]
                }})

                if confidence < self.confidence_threshold:
                    question = self.llm.ask_clarification(query_text, ranked)
                    self.session_store.update_clarification(question)
                    socketio.emit("message", {
                        "type": "agent_clarification",
                        "data": {
                            "question": question,
                            "candidates": route_info["top_candidates"],
                            "query_id": query_id
                        }
                    })
                    return

                validation = self._validate_decision(decision)
                if not validation["valid"]:
                    message = "我暂时不能执行这个指令：" + "；".join(validation["errors"])
                    self.session_store.update_clarification(message)
                    socketio.emit("message", {
                        "type": "agent_clarification",
                        "data": {
                            "question": message,
                            "candidates": route_info["top_candidates"],
                            "query_id": query_id
                        }
                    })
                    return
                if validation["requires_confirmation"]:
                    message = "这个操作风险较高，需要二次确认后再执行。"
                    self.session_store.update_clarification(message)
                    socketio.emit("message", {
                        "type": "agent_clarification",
                        "data": {
                            "question": message,
                            "candidates": route_info["top_candidates"],
                            "query_id": query_id
                        }
                    })
                    return
                decision = validation["normalized_command"]
                action_type = decision.get("action", "")
                device = decision.get("device", "")
                device_action = decision.get("device_action", "")
                scene = decision.get("scene", "")
                params = decision.get("params", {})

                # Step 4: 执行
                if action_type == "设备控制" and device and device_action:
                    message = self.device_control.execute(device, device_action, params)
                    result = {
                        "status": "success",
                        "action": f"{device}_{device_action}",
                        "device": device,
                        "device_action": device_action,
                        "params": params,
                        "message": message,
                    }
                elif action_type == "场景切换" and scene:
                    message = self.scene_switcher.execute(scene)
                    self.context.current_scene = scene
                    self.context.last_scene = SCENE_INDEX_MAP.get(scene, -1)
                    result = {
                        "status": "success",
                        "action": "scene_switch",
                        "scene": scene,
                        "params": params,
                        "message": message,
                    }
                else:
                    result = {"status": "no_action", "candidates": [r["action"] for r in ranked[:3]]}

                pipeline["steps"]["exec"] = {"status": "done", "result": result}
                socketio.emit("pipeline_update", {"type": "pipeline_step", "data": {
                    "query_id": query_id, "step": "exec", "data": pipeline["steps"]["exec"]
                }})

                # 最终响应
                if result["status"] == "success":
                    result_text = result.get("message", f"已执行: {device} {device_action}")
                    self.session_store.update_from_decision(decision, route=route_info["route"], result=result_text)
                    self.preference_store.record_action_accept(decision, self.context)
                    interaction = self._build_message_metadata(
                        "execution",
                        user_text,
                        query_text,
                        decision_snapshot=decision,
                    )
                    socketio.emit("message", {
                        "type": "agent_response",
                        "data": {
                            "action": result["action"],
                            "result": result_text,
                            "confidence": confidence,
                            "response_type": "execution_result",
                            "message_id": interaction["message_id"],
                            "feedback_target": {"message_id": interaction["message_id"], "target_type": "execution"},
                            "scene": self.context.current_scene,
                            "query_id": query_id,
                            "route": route_info["route"],
                            "route_reason": route_info["reason"],
                        }
                    })
                else:
                    self.session_store.update_clarification("我需要更多信息")
                    socketio.emit("message", {
                        "type": "agent_clarification",
                        "data": {
                            "question": "我需要更多信息",
                            "candidates": result["candidates"],
                            "query_id": query_id
                        }
                    })

            except Exception as e:
                print(f"[Agent] 处理出错: {e}")
                import traceback
                traceback.print_exc()
                self._emit_pipeline_error(query_id, str(e))
                self._simple_process(query_text)
        else:
            self._emit_pipeline_error(query_id, "AI 模块未加载，降级为规则匹配")
            self._simple_process(query_text)

    def _emit_pipeline_error(self, query_id: str, error: str):
        pipeline = {
            "query_id": query_id,
            "steps": {
                "bsr": {"status": "error", "error": error},
                "lsr": {"status": "error", "error": error},
                "llm": {"status": "error", "error": error},
                "exec": {"status": "error", "error": error},
            }
        }
        socketio.emit("pipeline_update", {"type": "pipeline_error", "data": pipeline})

    def _emit_fallback(self, query_id: str, user_text: str):
        self.session_store.update_clarification(f"未找到合适候选动作: {user_text}")
        for step in ["bsr", "lsr", "llm", "exec"]:
            socketio.emit("pipeline_update", {"type": "pipeline_step", "data": {
                "query_id": query_id, "step": step,
                "data": {"status": "done", "result": {"status": "no_candidates", "candidates": []}}
            }})
    
    def _simple_process(self, user_text: str):
        """简单规则处理（当 AI 模块不可用时）"""
        user_text_lower = user_text.lower()
        action_taken = False
        
        if "开" in user_text and "灯" in user_text:
            self.device_control.execute("灯光", "on", {})
            action_taken = True
        elif "关" in user_text and "灯" in user_text:
            self.device_control.execute("灯光", "off", {})
            action_taken = True
        elif "空调" in user_text:
            if "开" in user_text:
                temp = 26
                if "度" in user_text:
                    import re
                    match = re.search(r'(\d+)度', user_text)
                    if match:
                        temp = int(match.group(1))
                self.device_control.execute("空调", "on", {"temperature": temp})
                action_taken = True
            elif "关" in user_text:
                self.device_control.execute("空调", "off", {})
                action_taken = True
        elif "电视" in user_text or "tv" in user_text_lower:
            if "开" in user_text:
                self.device_control.execute("电视", "on", {})
                action_taken = True
            elif "关" in user_text:
                self.device_control.execute("电视", "off", {})
                action_taken = True
        
        if action_taken:
            self.session_store.update_from_decision(
                {
                    "action": "简单规则",
                    "device": "",
                    "scene": "",
                    "device_action": "",
                    "params": {},
                    "confidence": 1.0,
                },
                route="local",
                result="已处理您的指令",
            )
            socketio.emit("message", {
                "type": "agent_response",
                "data": {
                    "action": "simple_command",
                    "result": "已处理您的指令",
                    "confidence": 1.0,
                    "scene": self.context.current_scene
                }
            })
        else:
            socketio.emit("message", {
                "type": "agent_clarification",
                "data": {
                    "question": "抱歉，我未能理解您的指令。请尝试：打开灯光、关闭空调等",
                    "candidates": []
                }
            })
    
    def _handle_device_control(self, data: dict):
        """处理设备控制请求"""
        device_id = data.get("device")
        action = data.get("action")
        params = data.get("params", {})
        
        dev_name = self._resolve_device(device_id)
        result = self.device_control.execute(dev_name, action, params)
        
        socketio.emit("message", {
            "type": "device_update",
            "data": {
                "device": device_id,
                "state": self._get_device_state(device_id),
                "result": result
            }
        })
    
    def _get_device_state(self, device_id: str) -> dict:
        """获取单个设备状态（前端格式）"""
        dev_name = self._resolve_device(device_id)
        raw = self.device_control.get_state(dev_name)
        is_on = raw.get("status") == "开"
        return {
            "is_on": is_on,
            **{k: v for k, v in raw.items() if k != "status"}
        }
    
    def _handle_scene_switch(self, data: dict):
        """处理场景切换请求"""
        scene_id = data.get("scene")
        scene = self.SCENE_ID_MAP.get(scene_id, scene_id)
        
        if self.bsr:
            self.bsr.recall(f"切换到{scene}场景", self.context)
        
        self.scene_switcher.execute(scene)
        self.context.current_scene = scene_id
        self.session_store.update_scene(scene)
        
        socketio.emit("message", {
            "type": "scene_update",
            "data": {
                "scene": scene_id,
                "devices": self.get_all_states()["devices"]
            }
        })
    
    def _handle_dqn_response(self, data: dict):
        """处理用户对 DQN 推荐的响应"""
        recommendation_id = data.get("id", "")
        response = data.get("response", "")
        user_input = data.get("user_input", "")
        
        action = 5
        parts = recommendation_id.rsplit("_", 1)
        if len(parts) == 2:
            try:
                action = int(parts[1])
            except ValueError:
                pass
        
        if self.dqn_fb:
            self.dqn_fb.record(self.context, action, response)
        self.preference_store.record_recommendation_feedback(SCENE_NAMES.get(action, ""), response)
    
    def _execute_action(self, action: str):
        """执行动作"""
        if "ac" in action:
            if "on" in action:
                temp = int(action.split("_")[-1]) if "_" in action else 26
                self.device_control.execute("空调", "on", {"temperature": temp})
        elif "light" in action:
            self.device_control.execute("灯光", "on", {})
        elif "scene" in action:
            scene_name = action.replace("scene_", "")
            self.scene_switcher.execute(scene_name)
    
    # 设备英文ID → 中文名映射
    DEVICE_ID_MAP = {
        "air_conditioner": "空调",
        "light": "灯光",
        "tv": "电视",
        "water_heater": "热水器",
        "fan": "风扇",
        "speaker": "音响",
        "window": "窗户",
    }
    DEVICE_IDS = list(DEVICE_ID_MAP.keys())

    # 场景英文ID → 中文名映射
    SCENE_ID_MAP = {
        "sleep": "睡眠模式",
        "entertainment": "观影模式",
        "work": "工作模式",
        "away": "离家模式",
        "morning": "早安模式",
        "evening": "晚归模式",
    }

    def _scene_id_from_name(self, scene_name: str) -> str:
        for scene_id, name in self.SCENE_ID_MAP.items():
            if name == scene_name:
                return scene_id
        return scene_name

    def get_all_states(self) -> dict:
        """获取所有状态，返回前端统一的设备格式"""
        raw_states = self.device_control.get_all_state()
        devices = {}
        for dev_id, dev_name in self.DEVICE_ID_MAP.items():
            raw = raw_states.get(dev_name, {})
            is_on = raw.get("status") == "开"
            devices[dev_id] = {
                "is_on": is_on,
                **{k: v for k, v in raw.items() if k != "status"}
            }
        return {
            "context": {
                "scene": self.context.current_scene,
                "temperature": self.context.temperature,
                "humidity": self.context.humidity,
                "occupancy": self.context.members_home,
                "hour": datetime.now().hour
            },
            "devices": devices
        }
    
    def _resolve_device(self, device_id: str) -> str:
        """将英文设备ID解析为中文设备名"""
        return self.DEVICE_ID_MAP.get(device_id, device_id)
    
    def process_query(self, query: str) -> dict:
        """处理自然语言查询（供 API 调用）"""
        self.context.hour = datetime.now().hour
        normalized = self.language_normalizer.normalize(query)
        query_for_ai = normalized.normalized or query
        self._record_query_context(query, query_for_ai)

        intent_info = self.router.classify_intent(query, normalized_query=query_for_ai)
        if intent_info["route"] == "reply":
            interaction = self._build_message_metadata(
                "decision",
                query,
                query_for_ai,
                decision_snapshot={"intent_type": intent_info["intent_type"], "route": "reply"},
            )
            return {
                "status": "success",
                "response_type": "chat",
                "message_id": interaction["message_id"],
                "response": intent_info["reply_message"],
                "route": intent_info["route"],
                "route_reason": intent_info["reason"],
                "normalized_query": normalized.to_dict(),
                "feedback_target": {"message_id": interaction["message_id"], "target_type": "decision"},
            }

        if intent_info["route"] == "automation":
            proposal = self._build_automation_proposal(query, query_for_ai)
            if proposal:
                return {
                    "status": "success",
                    "response_type": "automation_proposal",
                    "message_id": proposal["message_id"],
                    "response": proposal["summary"],
                    "proposal": proposal,
                    "route": intent_info["route"],
                    "route_reason": intent_info["reason"],
                    "normalized_query": normalized.to_dict(),
                    "feedback_target": {"message_id": proposal["message_id"], "target_type": "automation_proposal"},
                }

        if intent_info["route"] == "unsupported":
            return {
                "status": "unsupported",
                "response_type": "clarification",
                "target": intent_info["target"],
                "response": intent_info["message"],
                "route": intent_info["route"],
                "route_reason": intent_info["reason"],
                "normalized_query": normalized.to_dict(),
            }
        
        if not self.bsr or not self.lsr or not self.llm:
            return {"status": "no_action", "message": "AI 模块未加载"}
        
        try:
            candidates = self.bsr.recall(query_for_ai, self.context)
            ranked = self.lsr.rank(
                query_for_ai,
                candidates,
                self.context,
                kb=self.kb,
                session_store=self.session_store,
            )
            
            if ranked:
                route_info = self.router.decide_route(
                    query,
                    ranked,
                    normalized_query=query_for_ai,
                    cloud_available=self.llm.is_cloud_available(),
                )
                self.last_route_info = route_info
                if route_info["route"] == "clarify":
                    question = self.llm.ask_clarification(query_for_ai, ranked)
                    self.session_store.update_clarification(question)
                    return {
                        "status": "clarification",
                        "response_type": "clarification",
                        "question": question,
                        "candidates": route_info["top_candidates"],
                        "route": route_info["route"],
                        "route_reason": route_info["reason"],
                        "normalized_query": normalized.to_dict(),
                    }
                cloud_context = self._build_cloud_context(ranked[:3])
                rag_context = self.kb.get_context_prompt(query_for_ai, self.context) if self.kb else ""
                print(f"[Privacy] 云端最小上下文: {cloud_context}")
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
                if decision.get("confidence", 0.0) < self.confidence_threshold:
                    question = self.llm.ask_clarification(query_for_ai, ranked)
                    self.session_store.update_clarification(question)
                    return {
                        "status": "clarification",
                        "response_type": "clarification",
                        "question": question,
                        "candidates": route_info["top_candidates"],
                        "route": route_info["route"],
                        "route_reason": route_info["reason"],
                        "normalized_query": normalized.to_dict(),
                    }
                validation = self._validate_decision(decision)
                if not validation["valid"]:
                    message = "我暂时不能执行这个指令：" + "；".join(validation["errors"])
                    self.session_store.update_clarification(message)
                    return {
                        "status": "clarification",
                        "response_type": "clarification",
                        "question": message,
                        "candidates": route_info["top_candidates"],
                        "route": route_info["route"],
                        "route_reason": route_info["reason"],
                        "normalized_query": normalized.to_dict(),
                    }
                if validation["requires_confirmation"]:
                    message = "这个操作风险较高，需要二次确认后再执行。"
                    self.session_store.update_clarification(message)
                    return {
                        "status": "clarification",
                        "response_type": "clarification",
                        "question": message,
                        "candidates": route_info["top_candidates"],
                        "route": route_info["route"],
                        "route_reason": route_info["reason"],
                        "normalized_query": normalized.to_dict(),
                    }
                decision = validation["normalized_command"]
                
                action_type = decision.get("action", "")
                device = decision.get("device", "")
                device_action = decision.get("device_action", "")
                scene = decision.get("scene", "")
                params = decision.get("params", {})
                
                if action_type == "设备控制" and device and device_action:
                    message = self.device_control.execute(device, device_action, params)
                    self.session_store.update_from_decision(decision, route=route_info["route"], result=message)
                    self.preference_store.record_action_accept(decision, self.context)
                    interaction = self._build_message_metadata(
                        "execution",
                        query,
                        query_for_ai,
                        decision_snapshot=decision,
                    )
                    return {
                        "status": "success",
                        "response_type": "execution_result",
                        "message_id": interaction["message_id"],
                        "action": f"{device}_{device_action}",
                        "response": message,
                        "confidence": decision.get("confidence", 0.9),
                        "route": route_info["route"],
                        "route_reason": route_info["reason"],
                        "normalized_query": normalized.to_dict(),
                        "feedback_target": {"message_id": interaction["message_id"], "target_type": "execution"},
                    }
                elif action_type == "场景切换" and scene:
                    message = self.scene_switcher.execute(scene)
                    self.context.current_scene = scene
                    self.context.last_scene = SCENE_INDEX_MAP.get(scene, -1)
                    self.session_store.update_from_decision(decision, route=route_info["route"], result=message)
                    self.preference_store.record_action_accept(decision, self.context)
                    interaction = self._build_message_metadata(
                        "execution",
                        query,
                        query_for_ai,
                        decision_snapshot=decision,
                    )
                    return {
                        "status": "success",
                        "response_type": "execution_result",
                        "message_id": interaction["message_id"],
                        "action": "scene_switch",
                        "response": message,
                        "confidence": decision.get("confidence", 0.9),
                        "route": route_info["route"],
                        "route_reason": route_info["reason"],
                        "normalized_query": normalized.to_dict(),
                        "feedback_target": {"message_id": interaction["message_id"], "target_type": "execution"},
                    }
                else:
                    self.session_store.update_clarification("我需要更多信息")
                    return {
                        "status": "clarification",
                        "response_type": "clarification",
                        "question": "我需要更多信息",
                        "candidates": [r["action"] for r in ranked[:3]],
                        "route": route_info["route"],
                        "route_reason": route_info["reason"],
                        "normalized_query": normalized.to_dict(),
                    }
        except Exception as e:
            print(f"[Agent] 处理出错: {e}")
        
        return {"status": "no_action", "message": "无法理解您的请求"}


# ==================== REST API ====================

@app.route("/api/status", methods=["GET"])
def get_status():
    """获取系统状态"""
    if agent:
        return jsonify(agent.get_all_states())
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/query", methods=["POST"])
def query():
    """自然语言查询接口"""
    data = request.get_json()
    query_text = data.get("query", "")
    
    if not query_text:
        return jsonify({"error": "query 不能为空"}), 400
    
    if agent:
        result = agent.process_query(query_text)
        return jsonify(result)
    
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/interaction/feedback", methods=["POST"])
def interaction_feedback():
    """Unified interaction feedback endpoint."""
    data = request.get_json(silent=True) or {}
    if not agent:
        return jsonify({"error": "Agent 未初始化"}), 500
    if not data.get("message_id"):
        return jsonify({"status": "error", "error": "message_id is required"}), 400
    if not data.get("feedback_type"):
        return jsonify({"status": "error", "error": "feedback_type is required"}), 400
    try:
        result = agent.handle_interaction_feedback(data)
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"status": "error", "error": str(exc)}), 400


@app.route("/api/devices/<device>/control", methods=["POST"])
def control_device(device):
    """设备控制接口"""
    data = request.get_json()
    action = data.get("action", "on")
    params = data.get("params", {})
    
    if agent:
        dev_name = agent._resolve_device(device)
        result = agent.device_control.execute(dev_name, action, params)
        return jsonify({
            "status": "success",
            "device": device,
            "result": result,
            "state": agent._get_device_state(device)
        })
    
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/scenes", methods=["GET"])
def list_scenes():
    """场景列表接口"""
    if agent:
        return jsonify({"status": "success", "scenes": agent.scene_store.list_scenes()})
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/scenes", methods=["POST"])
def create_scene():
    """场景创建接口"""
    data = request.get_json(silent=True) or {}
    if not data.get("name"):
        return jsonify({"status": "error", "error": "name is required"}), 400
    if agent:
        try:
            scene = agent.scene_store.add_scene(data["name"], data.get("config", {}))
        except ValueError as exc:
            return jsonify({"status": "error", "error": str(exc)}), 400
        return jsonify({"status": "success", "name": data["name"], "scene": scene})
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/scenes/from-nl", methods=["POST"])
def create_scene_from_nl():
    """自然语言创建场景接口"""
    data = request.get_json(silent=True) or {}
    if agent:
        parsed = agent.nl_to_tap.parse_scene_creation(data.get("text", ""))
        if not parsed:
            return jsonify({"status": "error", "error": "无法从自然语言解析场景"}), 400
        scene = agent.scene_store.add_scene(parsed["name"], parsed["config"])
        return jsonify({"status": "success", "name": parsed["name"], "scene": scene})
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/scenes/<scene_name>", methods=["GET"])
def get_scene(scene_name):
    """场景详情接口"""
    if agent:
        scene = agent.scene_store.get_scene(scene_name)
        if scene is None:
            return jsonify({"status": "error", "error": "场景不存在"}), 404
        return jsonify({"status": "success", "name": scene_name, "scene": scene})
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/scenes/<scene_name>", methods=["PUT"])
def update_scene(scene_name):
    """场景更新接口"""
    data = request.get_json(silent=True) or {}
    if agent:
        scene = agent.scene_store.update_scene(scene_name, data.get("config", {}))
        if scene is None:
            return jsonify({"status": "error", "error": "场景不存在"}), 404
        return jsonify({"status": "success", "name": scene_name, "scene": scene})
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/scenes/<scene_name>", methods=["DELETE"])
def delete_scene(scene_name):
    """场景删除接口"""
    if agent:
        deleted = agent.scene_store.delete_scene(scene_name)
        if not deleted:
            return jsonify({"status": "error", "error": "场景不存在"}), 404
        return jsonify({"status": "success"})
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/scenes/<scene>/switch", methods=["POST"])
def switch_scene(scene):
    """场景切换接口"""
    if agent:
        scene_name = agent.SCENE_ID_MAP.get(scene, scene)
        result = agent.scene_switcher.execute(scene_name)
        agent.context.current_scene = scene
        agent.context.last_scene = SCENE_INDEX_MAP.get(scene_name, -1)
        agent.session_store.update_scene(scene_name)
        return jsonify({
            "status": "success",
            "scene": scene,
            "result": result,
            "devices": agent.get_all_states()["devices"]
        })
    
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/info/<info_type>", methods=["GET"])
def query_info(info_type):
    """信息查询接口"""
    if agent:
        result = agent.info_query.execute(info_type)
        return jsonify({
            "status": "success",
            "type": info_type,
            "result": result
        })
    
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/dqn/recommend", methods=["GET"])
def dqn_recommend():
    """DQN 主动推荐"""
    if agent and agent.dqn:
        agent.context.hour = datetime.now().hour
        action_idx, confidence = agent.dqn.recommend(agent.context)
        
        if action_idx != 5:
            scene_name = SCENE_NAMES.get(action_idx, "")
            recommended_scene = agent._scene_id_from_name(scene_name)
            
            return jsonify({
                "status": "success",
                "recommendation": {
                    "id": f"dqn_{action_idx}",
                    "scene": recommended_scene,
                    "reason": f"基于当前环境状态推荐{scene_name}",
                    "confidence": confidence,
                }
            })
        
        return jsonify({"status": "no_recommendation"})
    
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/dqn/feedback", methods=["POST"])
def dqn_feedback():
    """DQN 反馈接口"""
    data = request.get_json()
    rec_id = data.get("id")
    response = data.get("response")
    
    if agent and agent.dqn_fb:
        action = 5
        parts = str(rec_id or "").rsplit("_", 1)
        if len(parts) == 2:
            try:
                action = int(parts[1])
            except ValueError:
                pass
        agent.dqn_fb.record(agent.context, action, response)
        return jsonify({"status": "success"})
    
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/kb/query", methods=["POST"])
def kb_query():
    """知识库查询"""
    data = request.get_json()
    query_text = data.get("query", "")
    top_k = data.get("top_k", 3)
    
    if agent and agent.kb:
        results = agent.kb.query(query_text, top_k=top_k)
        return jsonify({
            "status": "success",
            "results": results
        })
    
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/kb/add", methods=["POST"])
def kb_add():
    """添加知识"""
    data = request.get_json()
    text = data.get("text", "")
    category = data.get("category", "general")
    
    if agent and agent.kb:
        agent.kb.add(text, category=category, accepted=True)
        return jsonify({"status": "success"})
    
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/preferences", methods=["GET"])
def preferences():
    """读取结构化偏好快照"""
    if agent:
        return jsonify({"status": "success", "preferences": agent.get_preference_snapshot()})
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/memory/summary", methods=["GET"])
def memory_summary():
    """读取记忆与上下文摘要"""
    if agent:
        return jsonify(agent.get_memory_summary())
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/privacy/status", methods=["GET"])
def privacy_status():
    """读取隐私与云调用状态"""
    if agent:
        return jsonify(agent.get_privacy_status())
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/rules/scheduler", methods=["GET"])
def rule_scheduler_status():
    """读取规则调度器状态"""
    if agent:
        return jsonify(agent.get_scheduler_status())
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/rules/scheduler", methods=["POST"])
def rule_scheduler_toggle():
    """启停规则调度器"""
    data = request.get_json(silent=True) or {}
    if agent:
        enabled = data.get("enabled", True)
        return jsonify(agent.set_scheduler_enabled(bool(enabled)))
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/rules", methods=["GET"])
def list_rules():
    """列出所有 TAP 规则"""
    if agent:
        return jsonify({"status": "success", "rules": agent.tap_rule_store.list_rules()})
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/rules", methods=["POST"])
def create_rule():
    """创建 TAP 规则"""
    data = request.get_json(silent=True) or {}
    if agent:
        rule = agent.tap_rule_store.add_rule(data)
        return jsonify({"status": "success", "rule": rule})
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/rules/from-nl", methods=["POST"])
def create_rule_from_nl():
    """自然语言创建 TAP 规则"""
    data = request.get_json(silent=True) or {}
    if agent:
        rule_data = agent.nl_to_tap.parse(data.get("text", ""))
        if not rule_data:
            return jsonify({"status": "error", "error": "无法从自然语言解析 TAP 规则"}), 400
        rule = agent.tap_rule_store.add_rule(rule_data)
        return jsonify({"status": "success", "rule": rule})
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/rules/<rule_id>", methods=["PUT"])
def update_rule(rule_id):
    """更新 TAP 规则"""
    data = request.get_json(silent=True) or {}
    if agent:
        updated = agent.tap_rule_store.update_rule(rule_id, data)
        if updated is None:
            return jsonify({"error": "规则不存在"}), 404
        return jsonify({"status": "success", "rule": updated})
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/rules/<rule_id>", methods=["DELETE"])
def delete_rule(rule_id):
    """删除 TAP 规则"""
    if agent:
        deleted = agent.tap_rule_store.delete_rule(rule_id)
        if not deleted:
            return jsonify({"error": "规则不存在"}), 404
        return jsonify({"status": "success"})
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/rules/<rule_id>/toggle", methods=["POST"])
def toggle_rule(rule_id):
    """启用或禁用 TAP 规则"""
    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled")
    if agent:
        updated = agent.tap_rule_store.toggle_rule(rule_id, enabled=enabled)
        if updated is None:
            return jsonify({"error": "规则不存在"}), 404
        return jsonify({"status": "success", "rule": updated})
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/rules/evaluate", methods=["POST"])
def evaluate_rules():
    """评估当前 TAP 规则，可选择直接执行"""
    data = request.get_json(silent=True) or {}
    if agent:
        execute = bool(data.get("execute", False))
        overrides = dict(data.get("context", {}) or {})
        now = None
        at = data.get("time")
        if at:
            try:
                now = datetime.strptime(str(at), "%H:%M")
            except ValueError:
                return jsonify({"error": "time 格式必须为 HH:MM"}), 400
        result = agent.evaluate_rules(execute=execute, context_overrides=overrides, now=now)
        return jsonify(result)
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/gateway/status", methods=["GET"])
def gateway_status():
    """获取协议网关状态"""
    if protocol_gateway:
        return jsonify({
            "status": "success",
            "gateway": protocol_gateway.get_status_info()
        })
    return jsonify({
        "status": "success",
        "gateway": {"connected": False, "mode": "simulated"}
    })



@app.route("/api/voice/transcribe", methods=["POST"])
def voice_transcribe():
    """语音转文字接口：优先使用本地 Vosk small 模型。"""
    audio = request.files.get("audio")
    lang = request.form.get("lang", "auto")
    if not audio:
        return jsonify({"status": "error", "error": "audio file is required"}), 400

    result = voice_asr.transcribe_bytes(
        audio.read(),
        filename=audio.filename or "voice.webm",
        lang=lang,
    ).to_dict()

    if result["status"] != "success":
        return jsonify(result), 503

    normalized = language_normalizer.normalize(
        str(result.get("text", "")),
        language=str(result.get("language", "auto")),
    )
    result["normalized"] = normalized.normalized
    result["normalization"] = normalized.to_dict()
    return jsonify(result)


@app.route("/api/voice/feedback", methods=["POST"])
def voice_feedback():
    """Record user feedback for ASR text and normalization results."""
    data = request.get_json(silent=True) or {}
    asr_text = str(data.get("asr_text", "")).strip()
    normalized = str(data.get("normalized", "")).strip()
    corrected_text = str(data.get("corrected_text", "")).strip()
    corrected_normalized = str(data.get("corrected_normalized", "")).strip()
    feedback = str(data.get("feedback", "accepted")).strip()

    if not asr_text and not normalized:
        return jsonify({"status": "error", "error": "asr_text or normalized is required"}), 400

    if feedback == "corrected" and not corrected_normalized:
        source = corrected_text or asr_text
        corrected_normalized = language_normalizer.normalize(source).normalized

    record = voice_feedback_store.add({
        "asr_text": asr_text,
        "normalized": normalized,
        "corrected_text": corrected_text,
        "corrected_normalized": corrected_normalized,
        "language": data.get("language", "unknown"),
        "confidence": data.get("confidence", 0.0),
        "feedback": feedback,
        "engine": data.get("engine", "vosk"),
    })

    if agent:
        final_text = corrected_normalized or normalized
        agent.preference_store.record_feedback(asr_text, final_text, feedback)
    if agent and agent.kb:
        final_text = corrected_normalized or normalized
        content = (
            f"语音识别反馈：ASR文本「{asr_text}」，归一化「{normalized}」，"
            f"用户反馈「{feedback}」"
        )
        if corrected_text or corrected_normalized:
            content += f"，纠正文本「{corrected_text}」，纠正归一化「{final_text}」"
        agent.kb.add(
            content,
            category="语音反馈",
            accepted=(feedback in ("accepted", "corrected")),
            asr_text=asr_text,
            normalized=normalized,
            corrected_normalized=final_text,
            feedback=feedback,
        )

    return jsonify({"status": "success", "record": record})
# ==================== WebSocket 事件 ====================

@socketio.on("connect")
def on_connect():
    """客户端连接"""
    print(f"[WebSocket] 客户端连接: {request.sid}")
    if not agent:
        init_agent()
    if agent:
        emit("message", {
            "type": "connected",
            "data": agent.get_all_states()
        })


@socketio.on("disconnect")
def on_disconnect():
    """客户端断开"""
    print(f"[WebSocket] 客户端断开: {request.sid}")


@socketio.on("message")
def on_message(data):
    """处理前端消息"""
    print(f"[WebSocket] 收到消息: {data}")
    if not agent:
        init_agent()
    if agent:
        agent_queue.put(data)
        if data.get("type") == "device_control":
            device_id = data.get("data", {}).get("device")
            emit("message", {
                "type": "device_update",
                "data": {
                    "device": device_id,
                    "state": agent._get_device_state(device_id)
                }
            })


@socketio.on("user_input")
def on_user_input(data):
    """处理用户自然语言输入"""
    if not agent:
        init_agent()
    if agent:
        agent_queue.put({"type": "user_input", "data": data})


# ==================== 主程序 ====================

def init_agent(mode: str = None, protocol_gateway=None):
    """初始化全局 Agent"""
    global agent, device_simulator
    globals()["protocol_gateway"] = protocol_gateway

    # 从环境变量读取模式（如果未指定）
    if mode is None:
        mode = os.environ.get("HOMEMIND_MODE", "simulated")


    print(f"[初始化] Agent 模式: {mode}")

    agent = HomeMindWebAgent(protocol_gateway=protocol_gateway)
    device_simulator = agent.device_simulator


@app.route("/")
def index():
    """首页"""
    return """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>HomeMind</title>
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=IBM+Plex+Mono:wght@400;500&family=Cormorant+Garamond:ital,wght@0,400;1,400&display=swap" rel="stylesheet">
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            :root {
                --paper: #f5f2ec;
                --paper-dark: #ebe7df;
                --paper-line: #d4cfc5;
                --amber: #c4873a;
                --amber-light: #d4a55a;
                --cyan: #2a8a7e;
                --text-dark: #2c2418;
                --text-mid: #5a5040;
                --text-light: #8a8070;
                --font-title: 'DM Serif Display', serif;
                --font-mono: 'IBM Plex Mono', monospace;
                --font-narrative: 'Cormorant Garamond', serif;
            }
            body {
                font-family: var(--font-title);
                background: var(--paper);
                color: var(--text-dark);
                min-height: 100vh;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                background-image: repeating-linear-gradient(
                    0deg, transparent, transparent 28px,
                    var(--paper-line) 28px, var(--paper-line) 29px
                );
            }
            .container {
                text-align: center;
                padding: 64px;
                border: 0.5px solid var(--paper-line);
                background: var(--paper);
                max-width: 640px;
            }
            .label {
                font-family: var(--font-mono);
                font-size: 10px;
                letter-spacing: 3px;
                text-transform: uppercase;
                color: var(--text-light);
                margin-bottom: 24px;
            }
            h1 {
                font-family: var(--font-title);
                font-size: 72px;
                font-weight: 400;
                letter-spacing: -2px;
                margin-bottom: 16px;
                color: var(--text-dark);
            }
            .subtitle {
                font-family: var(--font-narrative);
                font-style: italic;
                font-size: 22px;
                color: var(--text-mid);
                margin-bottom: 56px;
                line-height: 1.6;
            }
            .divider {
                width: 48px;
                height: 1px;
                background: var(--amber);
                margin: 0 auto 48px;
            }
            .enter-btn {
                display: inline-block;
                padding: 18px 48px;
                border: 0.5px solid var(--text-dark);
                background: var(--text-dark);
                color: var(--paper);
                font-family: var(--font-mono);
                font-size: 12px;
                letter-spacing: 2px;
                text-transform: uppercase;
                text-decoration: none;
                transition: all 0.2s;
            }
            .enter-btn:hover {
                background: var(--amber);
                border-color: var(--amber);
            }
            .meta {
                font-family: var(--font-mono);
                font-size: 10px;
                color: var(--text-light);
                margin-top: 32px;
                letter-spacing: 1px;
            }
            .sensor-bar {
                display: flex;
                justify-content: center;
                gap: 32px;
                margin-top: 40px;
                padding-top: 40px;
                border-top: 0.5px solid var(--paper-line);
            }
            .sensor-item {
                text-align: center;
            }
            .sensor-label {
                font-family: var(--font-mono);
                font-size: 9px;
                letter-spacing: 1px;
                color: var(--text-light);
                margin-bottom: 8px;
            }
            .sensor-value {
                font-family: var(--font-mono);
                font-size: 24px;
                font-weight: 500;
                color: var(--cyan);
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="label">HomeMind Central Controller</div>
            <h1>HomeMind</h1>
            <p class="subtitle">智能家居中央指令器<br>安静地感知，精准地响应</p>
            <div class="divider"></div>
            <a href="/web/client/index.html" class="enter-btn">进入控制台</a>
            <div class="sensor-bar">
                <div class="sensor-item">
                    <div class="sensor-label">TEMPERATURE</div>
                    <div class="sensor-value">25°C</div>
                </div>
                <div class="sensor-item">
                    <div class="sensor-label">HUMIDITY</div>
                    <div class="sensor-value">60%</div>
                </div>
                <div class="sensor-item">
                    <div class="sensor-label">STATUS</div>
                    <div class="sensor-value">ONLINE</div>
                </div>
            </div>
            <div class="meta">v2.4.0 // EST. 2024</div>
        </div>
    </body>
    </html>
    """


if __name__ == "__main__":
    print("=" * 50)
    print("  HomeMind 中央指令器")
    print("  Web 控制面板 + 智能家居协议支持")
    print("=" * 50)
    
    # 从环境变量读取模式
    mode = os.environ.get("HOMEMIND_MODE", "simulated")
    
    # 初始化 Agent
    init_agent(mode=mode)
    
    # 启动服务
    print("\n[启动] Web 服务运行在 http://localhost:5000")
    print("[启动] API 文档: http://localhost:5000/api/status")
    print("[启动] 控制面板: 打开 web/client/index.html")
    print("\n按 Ctrl+C 停止服务\n")
    
    socketio.run(app, host="127.0.0.1", port=5000, debug=True, allow_unsafe_werkzeug=True)
