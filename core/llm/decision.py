"""
LLM decision layer.

The decider now works in two stages:
1. `plan_intent` decides whether the input is chat, executable command,
   clarification, or automation.
2. `decide_local` / `decide_cloud` select a structured command from recalled
   candidates for executable requests.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional


from core.config import (
    DECISION_CONFIDENCE,
    LLAMA_CPP_CONFIG,
    LLM_DECISION_RULES,
    OLLAMA_CONFIG,
    OPENAI_CONFIG,
    PROMPT_TEMPLATES,
    REACT_CONFIG,
)
from core.observability import get_metrics
from core.safety import detect_safety_sensitive_request

from .cloud_client import CloudClient

logger = logging.getLogger(__name__)

DEVICE_ACTION_MAP = {
    key: (
        value.get("action", ""),
        value.get("device", ""),
        value.get("device_action", ""),
        dict(value.get("params", {}) or {}),
    )
    for key, value in (LLM_DECISION_RULES.get("device_action_map", {}) or {}).items()
}

SCENE_ACTION_MAP = {
    "切换睡眠模式": "睡眠模式",
    "切换待客模式": "待客模式",
    "切换离家模式": "离家模式",
    "切换观影模式": "观影模式",
    "切换起床模式": "起床模式",
    "切换回家模式": "回家模式",
    "切换工作模式": "工作模式",
    "切换早安模式": "早安模式",
    "切换晚归模式": "晚归模式",
}

CHAT_KEYWORDS = {
    "你好": "你好，我可以帮你控制设备、切换场景，或者创建简单定时任务。",
    "您好": "你好，我可以帮你控制设备、切换场景，或者创建简单定时任务。",
    "hello": "你好，我可以帮你控制设备、切换场景，或者创建简单定时任务。",
    "hi": "你好，我可以帮你控制设备、切换场景，或者创建简单定时任务。",
    "谢谢": "不客气，我在。",
    "thanks": "不客气，我在。",
    "thank you": "不客气，我在。",
    "再见": "好的，有需要随时叫我。",
    "bye": "好的，有需要随时叫我。",
    "拜拜": "好的，有需要随时叫我。",
}

AMBIGUOUS_PATTERNS = (
    "像昨天那样",
    "像之前那样",
    "和昨天一样",
    "你看着办",
    "随便",
)

AUTOMATION_TIME_PATTERNS = (
    r"\d{1,2}:\d{2}",
    r"(早上|上午|中午|下午|晚上|今晚|明早|明天早上)?\s*\d{1,2}点(?:半|\d{1,2}分)?",
    r"(节假日|元旦|春节|清明|五一|劳动节|端午|中秋|国庆|圣诞)",
)

ACTION_HINTS = (
    "\u518d\u6697",
    "\u6697\u4e00\u70b9",
    "\u518d\u4eae",
    "\u4eae\u4e00\u70b9",
    "\u5173\u6389\u5b83",
    "\u542f\u52a8",
    "打开",
    "关闭",
    "调高",
    "调低",
    "调亮",
    "调暗",
    "切换",
    "设置",
    "查询",
    "查看",
    "睡眠模式",
    "待客模式",
    "离家模式",
    "观影模式",
    "起床模式",
    "回家模式",
    "工作模式",
    "早安模式",
    "晚归模式",
    "空调",
    "灯光",
    "电视",
    "音响",
    "风扇",
    "窗户",
    "热水器",
)

SOFT_COMMAND_NORMALIZATIONS = (
    (("有点闷", "闷得很", "闷得慌", "屋里闷", "闷"), "打开空调"),
    (("有点热", "好热", "太热", "闷热", "热"), "打开空调"),
    (("有点冷", "好冷", "太冷", "冷"), "打开暖气"),
    (("太亮", "有点亮", "亮一点太多", "刺眼"), "调暗灯光"),
    (("太暗", "有点暗", "看不清"), "调亮灯光"),
    (("我要走了", "我走了", "出门", "离家", "准备走", "马上走"), "切换离家模式"),
    (("回家了", "我回来了", "到家了", "回家"), "切换回家模式"),
    (("睡觉", "困了", "睡了", "晚安"), "切换睡眠模式"),
    (("看电影", "观影", "电影模式"), "切换观影模式"),
    (("起床", "早安"), "切换早安模式"),
    (("待客", "来客人", "客人来了"), "切换待客模式"),
)

SCENE_KEYWORDS = tuple(LLM_DECISION_RULES.get("scene_keywords", []) or [])
SWITCH_KEYWORDS = tuple(LLM_DECISION_RULES.get("switch_keywords", []) or [])
COMFORT_KEYWORDS = tuple(LLM_DECISION_RULES.get("comfort_keywords", []) or [])
EXPLICIT_DEVICES = tuple(LLM_DECISION_RULES.get("explicit_devices", []) or [])
EXPLICIT_VERBS = tuple(LLM_DECISION_RULES.get("explicit_verbs", []) or [])
SUPPORTED_DEVICES = tuple(LLM_DECISION_RULES.get("supported_devices", []) or [])
SUPPORTED_SCENES = tuple(LLM_DECISION_RULES.get("supported_scenes", []) or [])
DEFAULT_CLARIFICATION_REPLY = str(
    LLM_DECISION_RULES.get("clarification_reply", "请问你是想控制设备、切换场景，还是创建定时任务？") or ""
).strip()
COMFORT_DEFAULT_PROMPT = str(LLM_DECISION_RULES.get("comfort_default_prompt", "") or "").strip()


class LLMDecider:
    """Constrained decider supporting mock, llama.cpp, and OpenAI-compatible APIs."""

    def __init__(
        self,
        backend: str = "mock",
        model_path: str = "",
        api_base: str = "",
        api_key: str = "",
        cloud_model: str = "",
    ):
        self.backend = backend
        self.model_path = model_path
        self.api_base = api_base
        self.api_key = api_key
        self.cloud_model = cloud_model
        self._llm = None
        self._cloud_client = None
        self._init_backend()

    def _init_backend(self):
        if self.backend == "mock":
            logger.info("LLMDecider initialized in mock mode")
        elif self.backend == "llama_cpp":
            try:
                from llama_cpp import Llama

                self._llm = Llama(model_path=self.model_path, n_ctx=2048, n_threads=4)
                logger.info("LLMDecider initialized with llama.cpp: %s", self.model_path)
            except ImportError:
                logger.warning("llama-cpp-python is not installed; falling back to mock")
                self.backend = "mock"
        elif self.backend == "openai":
            self._cloud_client = CloudClient(
                api_base=self.api_base,
                api_key=self.api_key,
                model=self.cloud_model,
            )
            if self._cloud_client.is_available():
                logger.info("LLMDecider initialized with OpenAI-compatible cloud backend")
            else:
                logger.warning("Cloud backend unavailable; falling back to mock")
                self.backend = "mock"

    def is_cloud_available(self) -> bool:
        return self.backend == "openai" and self._cloud_client is not None and self._cloud_client.is_available()

    def complete_json(self, prompt: str, max_tokens: int = 512) -> Dict[str, Any]:
        if not self.is_cloud_available():
            return {}
        try:
            text = self._cloud_client.complete(prompt, max_tokens=max_tokens)
        except Exception as exc:
            logger.warning("Cloud JSON completion failed: %s", exc)
            return {}
        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start != -1 and end > start:
                parsed = json.loads(text[start:end])
                return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            logger.warning("Cloud JSON parse failed: %s", text[:160])
        return {}

    def plan_intent(
        self,
        query: str,
        normalized_query: str = "",
        context=None,
        rag_context: str = "",
        context_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        route_text = str(normalized_query or query or "").strip()
        if self.backend == "llama_cpp" and self._llm is not None:
            prompt = self._build_intent_prompt(query, route_text, context_summary=context_summary)
            output = self._llm(prompt, max_tokens=256, stop=["```"])
            text = output.get("choices", [{}])[0].get("text", "") if isinstance(output, dict) else str(output)
            parsed = self._parse_intent_output(text)
            if parsed.get("decision_confidence", 0.0) > 0:
                return parsed
        if self.backend == "openai" and self.is_cloud_available():
            prompt = self._build_intent_prompt(query, route_text, context_summary=context_summary)
            try:
                text = self._cloud_client.complete(prompt, max_tokens=256)
                parsed = self._parse_intent_output(text)
                if parsed.get("decision_confidence", 0.0) > 0:
                    # 后处理：关键字覆盖 LLM 分类（确保舒适度/场景关键词不被误判为闲聊）
                    return self._post_process_intent(parsed, route_text or query)
            except Exception as exc:
                logger.warning("Cloud intent planning failed: %s; falling back to mock", exc)
        return self._mock_plan_intent(query, route_text, context)

    def decide_intent(
        self,
        query: str,
        normalized_query: str = "",
        context=None,
        rag_context: str = "",
        context_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self.plan_intent(
            query,
            normalized_query=normalized_query,
            context=context,
            rag_context=rag_context,
            context_summary=context_summary,
        )

    def decide_local(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        context,
        rag_context: str = "",
    ) -> Dict[str, Any]:
        if self.backend == "llama_cpp" and self._llm is not None:
            prompt = self._build_prompt(query, candidates, context, rag_context)
            output = self._llm(prompt, max_tokens=256, stop=["```"])
            text = output.get("choices", [{}])[0].get("text", "") if isinstance(output, dict) else str(output)
            parsed = self._parse_output(text)
            if parsed.get("confidence", 0.0) > 0:
                return parsed
        return self._mock_decide(query, candidates, context, rag_context=rag_context)

    def decide_cloud(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        context,
        rag_context: str = "",
        context_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self.is_cloud_available():
            return self.decide_local(query, candidates, context, rag_context=rag_context)
        prompt = self._build_prompt(query, candidates, context, rag_context, context_summary=context_summary)
        try:
            text = self._cloud_client.complete(prompt, max_tokens=256)
            parsed = self._parse_output(text)
            return self._post_process_decision(parsed, query, candidates, context)
        except Exception as exc:
            logger.warning("Cloud decision failed: %s; falling back to local", exc)
            return self.decide_local(query, candidates, context, rag_context=rag_context)

    def decide(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        context,
        rag_context: str = "",
        context_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if self.backend == "openai":
            return self.decide_cloud(query, candidates, context, rag_context=rag_context, context_summary=context_summary)
        return self.decide_local(query, candidates, context, rag_context=rag_context)

    def rescue_intent_with_cloud(
        self,
        query: str,
        normalized_query: str = "",
        context=None,
        context_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        route_text = str(normalized_query or query or "").strip()
        fallback = {
            "intent_type": "clarification_needed",
            "route": "clarify",
            "reply_message": DEFAULT_CLARIFICATION_REPLY,
            "normalized_goal": route_text,
            "requires_candidates": False,
            "requires_automation": False,
            "decision_confidence": 0.0,
            "reasoning": "cloud_rescue_unavailable",
        }
        if not self.is_cloud_available():
            return fallback
        prompt = self._build_cloud_rescue_intent_prompt(query, route_text, context_summary=context_summary)
        try:
            text = self._cloud_client.complete(prompt, max_tokens=320)
            parsed = self._parse_intent_output(text)
            if parsed.get("intent_type"):
                return self._post_process_intent(parsed, parsed.get("normalized_goal") or route_text or query)
        except Exception as exc:
            logger.warning("Cloud rescue intent failed: %s", exc)
        return fallback

    def rescue_decision_with_cloud(
        self,
        query: str,
        context,
        rag_context: str = "",
        context_summary: Optional[Dict[str, Any]] = None,
        candidate_actions: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        fallback = {
            "action": "无法理解",
            "device": "",
            "scene": "",
            "device_action": "",
            "params": {},
            "confidence": 0.0,
            "reasoning": "cloud_rescue_unavailable",
        }
        if not self.is_cloud_available():
            return fallback
        prompt = self._build_cloud_rescue_decision_prompt(
            query,
            rag_context=rag_context,
            context_summary=context_summary,
            candidate_actions=candidate_actions,
        )
        try:
            text = self._cloud_client.complete(prompt, max_tokens=320)
            parsed = self._parse_output(text)
            return self._normalize_rescue_command(parsed)
        except Exception as exc:
            logger.warning("Cloud rescue decision failed: %s", exc)
        return fallback

    def _normalize_rescue_command(self, command: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(command or {})
        action_aliases = {
            "device_control": "设备控制",
            "scene_switch": "场景切换",
            "info_query": "信息查询",
        }
        device_action_aliases = {
            "turn_on": "on",
            "turn_off": "off",
        }
        action = str(normalized.get("action", "") or "").strip()
        normalized["action"] = action_aliases.get(action, action)
        device_action = str(normalized.get("device_action", "") or "").strip()
        normalized["device_action"] = device_action_aliases.get(device_action, device_action)
        normalized.setdefault("device", "")
        normalized.setdefault("scene", "")
        normalized.setdefault("params", {})
        normalized.setdefault("confidence", 0.0)
        normalized.setdefault("reasoning", "")
        if not isinstance(normalized["params"], dict):
            normalized["params"] = {}
        return normalized

    def _render_prompt_template(self, template_name: str, values: Dict[str, Any]) -> str:
        template = str(PROMPT_TEMPLATES.get(template_name, "") or "").strip()
        if not template:
            return ""
        rendered = template
        for key, value in values.items():
            rendered = rendered.replace(f"__{key}__", str(value))
        return rendered

    def _build_intent_prompt(
        self,
        query: str,
        normalized_query: str,
        context_summary: Optional[Dict[str, Any]] = None,
    ) -> str:
        return self._render_prompt_template(
            "intent",
            {
                "QUERY": query,
                "NORMALIZED_QUERY": normalized_query,
                "CONTEXT_SUMMARY": json.dumps(context_summary or {}, ensure_ascii=False),
            },
        )

    def _build_cloud_rescue_intent_prompt(
        self,
        query: str,
        normalized_query: str,
        context_summary: Optional[Dict[str, Any]] = None,
    ) -> str:
        return self._render_prompt_template(
            "cloud_rescue_intent",
            {
                "QUERY": query,
                "NORMALIZED_QUERY": normalized_query,
                "CONTEXT_SUMMARY": json.dumps(context_summary or {}, ensure_ascii=False),
                "SUPPORTED_DEVICES": "、".join(SUPPORTED_DEVICES),
                "SUPPORTED_SCENES": "、".join(SUPPORTED_SCENES),
            },
        )

    def _build_cloud_rescue_decision_prompt(
        self,
        query: str,
        rag_context: str = "",
        context_summary: Optional[Dict[str, Any]] = None,
        candidate_actions: Optional[List[str]] = None,
    ) -> str:
        candidate_block = ""
        if candidate_actions:
            candidate_block = "本地候选(仅供参考): " + "、".join(str(item) for item in candidate_actions if item) + "\n"
        rag_block = f"参考知识:\n{rag_context}\n" if rag_context else ""
        return self._render_prompt_template(
            "cloud_rescue_decision",
            {
                "QUERY": query,
                "CONTEXT_SUMMARY": json.dumps(context_summary or {}, ensure_ascii=False),
                "SUPPORTED_DEVICES": "、".join(SUPPORTED_DEVICES),
                "SUPPORTED_SCENES": "、".join(SUPPORTED_SCENES),
                "COMFORT_DEFAULT_PROMPT": COMFORT_DEFAULT_PROMPT,
                "CANDIDATE_BLOCK": candidate_block,
                "RAG_BLOCK": rag_block,
            },
        )

    def _build_prompt(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        context,
        rag_context: str = "",
        context_summary: Optional[Dict[str, Any]] = None,
    ) -> str:
        candidate_str = "\n".join(f"{idx + 1}. {item['action']}" for idx, item in enumerate(candidates))
        rag_block = f"\n参考知识:\n{rag_context}\n" if rag_context else ""
        cloud_context = context_summary or {
            "hour": getattr(context, "hour", 0),
            "temperature": getattr(context, "temperature", 0.0),
            "humidity": getattr(context, "humidity", 0.0),
            "occupancy": getattr(context, "members_home", 0),
            "scene": getattr(context, "current_scene", ""),
        }
        return self._render_prompt_template(
            "decision",
            {
                "QUERY": query,
                "CLOUD_CONTEXT": json.dumps(cloud_context, ensure_ascii=False, indent=2),
                "RAG_BLOCK": rag_block,
                "CANDIDATE_STR": candidate_str,
            },
        )

    def _mock_plan_intent(self, query: str, normalized_query: str, context) -> Dict[str, Any]:
        raw_text = str(query or "").strip()
        route_text = str(normalized_query or raw_text).strip()
        combined_text = " ".join(part for part in [raw_text, route_text] if part).strip()
        lowered = combined_text.lower()

        safety = detect_safety_sensitive_request(raw_text, normalized_query=route_text)
        if safety:
            return {
                "intent_type": "clarification_needed",
                "route": "clarify",
                "reply_message": safety["message"],
                "normalized_goal": route_text,
                "requires_candidates": False,
                "requires_automation": False,
                "decision_confidence": 1.0,
                "reasoning": safety["reason"],
            }

        # 先检查软命令归一化（优先于闲聊匹配）
        # 但如果原始文本包含时间条件（如"晚上7:00"或"五一"），则跳过
        has_time_condition = self._looks_like_automation_request(combined_text)
        soft_normalized = self._normalize_goal(route_text or raw_text)
        if soft_normalized and self._looks_like_action(soft_normalized) and not has_time_condition:
            return {
                "intent_type": "action_command",
                "route": "action",
                "reply_message": "",
                "normalized_goal": soft_normalized,
                "requires_candidates": True,
                "requires_automation": False,
                "decision_confidence": 0.92,
                "reasoning": "软命令归一化识别为可执行控制意图",
            }

        chat_reply = self._match_chat_reply(combined_text, lowered)
        if chat_reply:
            return {
                "intent_type": "chat_reply",
                "route": "reply",
                "reply_message": chat_reply,
                "normalized_goal": "",
                "requires_candidates": False,
                "requires_automation": False,
                "decision_confidence": DECISION_CONFIDENCE.get("chat_reply", 0.98),
                "reasoning": "识别为寒暄或礼貌回复",
            }

        if any(pattern in route_text for pattern in AMBIGUOUS_PATTERNS):
            return {
                "intent_type": "clarification_needed",
                "route": "clarify",
                "reply_message": "请问你是想延续之前的设备设置、切换某个场景，还是创建定时任务？",
                "normalized_goal": route_text,
                "requires_candidates": False,
                "requires_automation": False,
                "decision_confidence": DECISION_CONFIDENCE.get("ambiguous_clarify", 0.88),
                "reasoning": "输入包含指代性表达，缺少明确动作目标",
            }

        if self._looks_like_automation_request(combined_text):
            return {
                "intent_type": "automation_request",
                "route": "automation",
                "reply_message": "",
                "normalized_goal": raw_text or route_text,
                "requires_candidates": False,
                "requires_automation": True,
                "decision_confidence": DECISION_CONFIDENCE.get("automation_request", 0.96),
                "reasoning": "识别到时间条件与动作组合，判断为自动化请求",
            }

        normalized_goal = self._normalize_goal(route_text or raw_text)
        if normalized_goal and self._looks_like_action(normalized_goal):
            return {
                "intent_type": "action_command",
                "route": "action",
                "reply_message": "",
                "normalized_goal": normalized_goal,
                "requires_candidates": True,
                "requires_automation": False,
                "decision_confidence": DECISION_CONFIDENCE.get("action_command", 0.92),
                "reasoning": "识别为可执行控制意图",
            }

        if self._looks_like_action(route_text):
            return {
                "intent_type": "action_command",
                "route": "action",
                "reply_message": "",
                "normalized_goal": route_text,
                "requires_candidates": True,
                "requires_automation": False,
                "decision_confidence": DECISION_CONFIDENCE.get("action_command_weak", 0.82),
                "reasoning": "识别到控制动作，但需要候选约束进一步确认",
            }

        return {
            "intent_type": "clarification_needed",
            "route": "clarify",
            "reply_message": DEFAULT_CLARIFICATION_REPLY,
            "normalized_goal": route_text,
            "requires_candidates": False,
            "requires_automation": False,
            "decision_confidence": DECISION_CONFIDENCE.get("needs_clarification", 0.55),
            "reasoning": "未识别出稳定的执行目标，需要澄清",
        }

    def _mock_decide(self, query: str, candidates: List[Dict[str, Any]], context, rag_context: str = "") -> Dict[str, Any]:
        top = candidates[0]["action"] if candidates else ""
        confidence = candidates[0].get("final_score", 0.8) if candidates else 0.5
        confidence = max(confidence, 0.9) if top else confidence

        if top in DEVICE_ACTION_MAP:
            action, device, device_action, params = DEVICE_ACTION_MAP[top]
            return {
                "action": action,
                "device": device,
                "scene": "",
                "device_action": device_action,
                "params": params,
                "confidence": confidence,
                "reasoning": f"[CoT] 候选“{top}”直接匹配，映射到{device}的{device_action}操作",
            }

        if top in SCENE_ACTION_MAP:
            scene = SCENE_ACTION_MAP[top]
            return {
                "action": "场景切换",
                "device": "",
                "scene": scene,
                "device_action": "scene",
                "params": {},
                "confidence": confidence,
                "reasoning": f"[CoT] 候选“{top}”直接匹配，切换到{scene}",
            }

        if "睡眠" in query or "困" in query or "睡觉" in query:
            return self._scene_decision("睡眠模式", confidence, "睡眠相关关键词")
        if "离家" in query or "出门" in query:
            return self._scene_decision("离家模式", confidence, "离家相关关键词")
        if "待客" in query or "客人" in query:
            return self._scene_decision("待客模式", confidence, "待客相关关键词")
        if "观影" in query or "看电影" in query:
            return self._scene_decision("观影模式", confidence, "观影相关关键词")
        if "起床" in query or "早安" in query:
            return self._scene_decision("早安模式", confidence, "起床相关关键词")
        if "热" in query or "闷" in query:
            return {
                "action": "设备控制",
                "device": "空调",
                "scene": "",
                "device_action": "on",
                "params": {"temperature": DECISION_CONFIDENCE.get("ac_temp_hot", 26)},
                "confidence": confidence,
                "reasoning": f"[CoT] 舒适度关键词结合温度{getattr(context, 'temperature', DECISION_CONFIDENCE.get('ac_temp_hot', 26))}°C，优先开启空调",
            }
        if "冷" in query:
            return {
                "action": "设备控制",
                "device": "空调",
                "scene": "",
                "device_action": "on",
                "params": {"temperature": DECISION_CONFIDENCE.get("ac_temp_cold", 28)},
                "confidence": confidence,
                "reasoning": "[CoT] 冷感关键词，开启空调制热升温",
            }
        if "亮" in query and "暗" not in query:
            return {
                "action": "设备控制",
                "device": "灯光",
                "scene": "",
                "device_action": "adjust",
                "params": {"brightness": 100},
                "confidence": confidence,
                "reasoning": "[CoT] 亮度增强关键词，调亮灯光",
            }
        if "暗" in query:
            return {
                "action": "设备控制",
                "device": "灯光",
                "scene": "",
                "device_action": "adjust",
                "params": {"brightness": 30},
                "confidence": confidence,
                "reasoning": "[CoT] 亮度降低关键词，调暗灯光",
            }

        return {
            "action": top or "无法理解",
            "device": "",
            "scene": "",
            "device_action": "",
            "params": {},
            "confidence": confidence,
            "reasoning": "[CoT] 未命中明确规则，返回候选动作或兜底结果",
        }

    def _scene_decision(self, scene: str, confidence: float, reason: str) -> Dict[str, Any]:
        return {
            "action": "场景切换",
            "device": "",
            "scene": scene,
            "device_action": "scene",
            "params": {},
            "confidence": confidence,
            "reasoning": f"[CoT] {reason}，切换到{scene}",
        }

    def _post_process_decision(
        self,
        parsed: Dict[str, Any],
        query: str,
        candidates: List[Dict[str, Any]],
        context,
    ) -> Dict[str, Any]:
        """
        后处理 Cloud LLM 返回的决策结果。

        只在 action 完全无效（空或"无法理解"）时才 fallback 到本地 mock。
        有效的 action（如 "设备控制"、"场景切换"）原样返回，
        由调用方（web server / CLI）负责校验和归一化。
        """
        action = parsed.get("action", "").strip()
        # action 为空或明显无效时 fallback
        if not action or action in ("无法理解", "None", "null", ""):
            logger.warning("Cloud decision returned empty/invalid action=%r; falling back to mock", action)
            return self._mock_decide(query, candidates, context)
        return parsed

    def _parse_intent_output(self, output: str) -> Dict[str, Any]:
        try:
            start = output.find("{")
            end = output.rfind("}") + 1
            if start != -1 and end > start:
                result = json.loads(output[start:end])
                result.setdefault("intent_type", "clarification_needed")
                result.setdefault("route", "clarify")
                result.setdefault("reply_message", "")
                result.setdefault("normalized_goal", "")
                result.setdefault("requires_candidates", result["intent_type"] == "action_command")
                result.setdefault("requires_automation", result["intent_type"] == "automation_request")
                result.setdefault("decision_confidence", 0.8)
                result.setdefault("reasoning", "")
                return result
        except json.JSONDecodeError:
            logger.warning("LLM intent output parse failed: %s", output[:120])
        return {
            "intent_type": "clarification_needed",
            "route": "clarify",
            "reply_message": DEFAULT_CLARIFICATION_REPLY,
            "normalized_goal": "",
            "requires_candidates": False,
            "requires_automation": False,
            "decision_confidence": 0.0,
            "reasoning": "JSON 解析失败",
        }

    def _parse_output(self, output: str) -> Dict[str, Any]:
        try:
            start = output.find("{")
            end = output.rfind("}") + 1
            if start != -1 and end > start:
                result = json.loads(output[start:end])
                result.setdefault("action", "无法理解")
                result.setdefault("device", "")
                result.setdefault("scene", "")
                result.setdefault("device_action", "")
                result.setdefault("params", {})
                result.setdefault("confidence", 0.8)
                result.setdefault("reasoning", "")
                return result
        except json.JSONDecodeError:
            logger.warning("LLM output parse failed: %s", output[:120])
        return {"action": "无法理解", "confidence": 0.0, "reasoning": "[CoT] JSON解析失败"}

    def ask_clarification(self, query: str, candidates: List[Dict[str, Any]]) -> str:
        device_options = []
        clarification_keywords = list(SUPPORTED_DEVICES) + ["模式"]
        for candidate in candidates:
            action = candidate.get("action", "")
            for keyword in clarification_keywords:
                if keyword in action and keyword not in device_options:
                    device_options.append("场景" if keyword == "模式" else keyword)

        if not device_options:
            default_options = list(SUPPORTED_DEVICES[:3]) if SUPPORTED_DEVICES else ["空调", "灯光", "电视"]
            device_options = default_options + ["场景"]
        options = "、".join(device_options[:4])
        return f"请问你想调节哪个目标？{options}？"

    def _match_chat_reply(self, text: str, lowered: str) -> str:
        compact = re.sub(r"\s+", "", lowered)
        for keyword, reply in CHAT_KEYWORDS.items():
            if keyword in text or keyword in compact:
                return reply
        return ""

    def _looks_like_automation_request(self, text: str) -> bool:
        if not text:
            return False
        has_time = any(re.search(pattern, text) for pattern in AUTOMATION_TIME_PATTERNS)
        return has_time and self._looks_like_action(text)

    def _looks_like_action(self, text: str) -> bool:
        text = str(text or "").strip()
        if not text:
            return False
        return any(hint in text for hint in ACTION_HINTS) or text in DEVICE_ACTION_MAP or text in SCENE_ACTION_MAP

    def _looks_like_scene_switch_request(self, text: str) -> bool:
        """Recognize implicit scene switch requests like '切换到睡眠模式'."""
        text = str(text or "").strip()
        if not text:
            return False
        return any(sk in text for sk in SCENE_KEYWORDS) and any(sw in text for sw in SWITCH_KEYWORDS)

    def _looks_like_comfort_request(self, text: str) -> bool:
        """Recognize implicit comfort/environment adjustment requests like '有点热'."""
        text = str(text or "").strip()
        if not text:
            return False
        return any(ck in text for ck in COMFORT_KEYWORDS)

    def _post_process_intent(self, parsed: Dict[str, Any], query: str) -> Dict[str, Any]:
        """
        Override LLM intent classification when clear comfort/action keywords are present.
        This ensures consistent behavior across cloud providers.
        Also applies soft-command normalization to the normalized_goal.
        """
        if parsed.get("intent_type") == "chat_reply" and self._looks_like_comfort_request(query):
            normalized = self._normalize_goal(query)
            return {
                "intent_type": "action_command",
                "route": "action",
                "reply_message": "",
                "normalized_goal": normalized or query,
                "requires_candidates": True,
                "requires_automation": False,
                "decision_confidence": 0.85,
                "reasoning": "识别为舒适度/环境调节请求（后处理覆盖LLM分类）",
            }
        if parsed.get("intent_type") == "chat_reply" and self._looks_like_scene_switch_request(query):
            normalized = self._normalize_goal(query)
            return {
                "intent_type": "action_command",
                "route": "action",
                "reply_message": "",
                "normalized_goal": normalized or query,
                "requires_candidates": True,
                "requires_automation": False,
                "decision_confidence": DECISION_CONFIDENCE.get("ambiguous_clarify", 0.88),
                "reasoning": "识别为场景切换请求（后处理覆盖LLM分类）",
            }
        if parsed.get("intent_type") == "action_command" and parsed.get("route") == "clarify":
            parsed["route"] = "action"
            parsed["requires_automation"] = False
            parsed["requires_candidates"] = True
            # 归一化 normalized_goal（如"热"→"打开空调"）
            ng = parsed.get("normalized_goal", "")
            if ng:
                normalized = self._normalize_goal(ng)
                if normalized and self._looks_like_action(normalized):
                    parsed["normalized_goal"] = normalized
        return parsed

    def _normalize_goal(self, text: str) -> str:
        text = str(text or "").strip()
        if not text:
            return ""
        if any(device in text for device in EXPLICIT_DEVICES) and any(verb in text for verb in EXPLICIT_VERBS):
            return text
        for keywords, normalized in SOFT_COMMAND_NORMALIZATIONS:
            if any(keyword in text for keyword in keywords):
                return normalized
        return text
