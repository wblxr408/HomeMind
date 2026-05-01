"""
LLM decision layer.

The decider now works in two stages:
1. `plan_intent` decides whether the input is chat, executable command,
   clarification, or automation.
2. `decide_local` / `decide_cloud` select a structured command from recalled
   candidates for executable requests.
Also supports Ollama API backend for on-device inference with KV cache
quantization (Q8_0), 2048 token context limit, and num_parallel=1.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

import requests

from core.config import OLLAMA_CONFIG, LLAMA_CPP_CONFIG, REACT_CONFIG
from core.observability import get_metrics
from core.safety import detect_safety_sensitive_request

from .cloud_client import CloudClient

logger = logging.getLogger(__name__)

DEVICE_ACTION_MAP = {
    "打开空调": ("设备控制", "空调", "on", {"temperature": 26}),
    "关闭空调": ("设备控制", "空调", "off", {}),
    "调高空调温度": ("设备控制", "空调", "adjust", {"temperature": 28}),
    "调低空调温度": ("设备控制", "空调", "adjust", {"temperature": 24}),
    "打开灯光": ("设备控制", "灯光", "on", {"brightness": 100}),
    "关闭灯光": ("设备控制", "灯光", "off", {}),
    "调亮灯光": ("设备控制", "灯光", "adjust", {"brightness": 100}),
    "调暗灯光": ("设备控制", "灯光", "adjust", {"brightness": 30}),
    "打开电视": ("设备控制", "电视", "on", {}),
    "关闭电视": ("设备控制", "电视", "off", {}),
    "打开风扇": ("设备控制", "风扇", "on", {}),
    "关闭风扇": ("设备控制", "风扇", "off", {}),
    "打开窗户": ("设备控制", "窗户", "open", {}),
    "关闭窗户": ("设备控制", "窗户", "close", {}),
    "打开音响": ("设备控制", "音响", "on", {"volume": 30}),
    "关闭音响": ("设备控制", "音响", "off", {}),
    "打开暖气": ("设备控制", "空调", "on", {"temperature": 24, "mode": "制热"}),
    "打开热水器": ("设备控制", "热水器", "on", {"temperature": 45}),
    "关闭热水器": ("设备控制", "热水器", "off", {}),
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
    (("\u6709\u70b9\u95f7", "\u95f7\u5f97\u5f88", "\u95f7\u5f97\u614c", "\u5c4b\u91cc\u95f7", "\u95f7"), "\u6253\u5f00\u7a7a\u8c03"),
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
        self._ollama_session = None
        self._init_backend()

    def _init_backend(self):
        if self.backend == "mock":
            logger.info("LLMDecider initialized in mock mode")
        elif self.backend == "ollama":
            self._init_ollama()
        elif self.backend == "llama_cpp":
            self._init_llama_cpp()
        elif self.backend == "openai":
            self._init_openai()

    def _init_ollama(self):
        """Connect to local Ollama server."""
        cfg = OLLAMA_CONFIG
        self._ollama_session = requests.Session()
        self._ollama_session.headers.update({"Content-Type": "application/json"})
        try:
            resp = self._ollama_session.get(
                f"{cfg['base_url']}/api/tags",
                timeout=cfg["timeout"],
            )
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                logger.info("LLMDecider connected to Ollama, available models: %s",
                          [m.get("name") for m in models])
                self.backend = "ollama"
            else:
                logger.warning("Ollama server returned %s; falling back to mock", resp.status_code)
                self.backend = "mock"
        except requests.exceptions.RequestException as exc:
            logger.warning("Ollama server unreachable at %s: %s; falling back to mock",
                         cfg["base_url"], exc)
            self.backend = "mock"

    def _init_llama_cpp(self):
        try:
            from llama_cpp import Llama

            cfg = LLAMA_CPP_CONFIG
            self._llm = Llama(
                model_path=self.model_path or cfg["model_path"],
                n_ctx=cfg["n_ctx"],
                n_threads=cfg["n_threads"],
                n_gpu_layers=cfg["n_gpu_layers"],
                use_mlock=cfg["use_mlock"],
            )
            logger.info("LLMDecider initialized with llama.cpp: %s (n_ctx=%d)",
                      self.model_path or cfg["model_path"], cfg["n_ctx"])
        except ImportError:
            logger.warning("llama-cpp-python is not installed; falling back to mock")
            self.backend = "mock"

    def _init_openai(self):
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
        if self.backend == "openai" and self._cloud_client is not None:
            return self._cloud_client.is_available()
        return False

    def _ollama_complete(self, prompt: str, max_tokens: int = 512) -> str:
        """Call Ollama API for completion."""
        cfg = OLLAMA_CONFIG
        try:
            resp = self._ollama_session.post(
                f"{cfg['base_url']}/api/generate",
                json={
                    "model": cfg["model"],
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "num_predict": max_tokens,
                        "num_ctx": 2048,
                        "temperature": 0.3,
                    },
                },
                timeout=cfg["timeout"],
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("response", "")
        except requests.exceptions.RequestException as exc:
            logger.warning("Ollama request failed: %s", exc)
        return ""

    def complete_json(self, prompt: str, max_tokens: int = 512) -> Dict[str, Any]:
        if not self.is_cloud_available() and self.backend not in ("ollama", "llama_cpp"):
            return {}
        start = time.time()
        try:
            if self.backend == "ollama":
                text = self._ollama_complete(prompt, max_tokens)
            elif self.backend == "llama_cpp" and self._llm is not None:
                output = self._llm(prompt, max_tokens=max_tokens, stop=["```"])
                text = output.get("choices", [{}])[0].get("text", "") if isinstance(output, dict) else str(output)
            else:
                text = self._cloud_client.complete(prompt, max_tokens=max_tokens) if self._cloud_client else ""
        except Exception as exc:
            logger.warning("LLM completion failed: %s", exc)
            return {}

        # Record metrics
        latency_ms = (time.time() - start) * 1000
        try:
            metrics = get_metrics()
            metrics.record_llm_request(self.backend, 0)
            metrics.record_latency(latency_ms)
        except Exception:
            pass

        try:
            start_json = text.find("{")
            end_json = text.rfind("}") + 1
            if start_json != -1 and end_json > start_json:
                parsed = json.loads(text[start_json:end_json])
                return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            logger.warning("JSON parse failed: %s", text[:160])
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
        if self.backend == "ollama":
            prompt = self._build_intent_prompt(query, route_text, context_summary=context_summary)
            text = self._ollama_complete(prompt, max_tokens=256)
            parsed = self._parse_intent_output(text)
            if parsed.get("intent_type"):
                return parsed
        elif self.backend == "llama_cpp" and self._llm is not None:
            prompt = self._build_intent_prompt(query, route_text, context_summary=context_summary)
            output = self._llm(prompt, max_tokens=256, stop=["```"])
            text = output.get("choices", [{}])[0].get("text", "") if isinstance(output, dict) else str(output)
            parsed = self._parse_intent_output(text)
            if parsed.get("intent_type"):
                return parsed
        elif self.backend == "openai" and self.is_cloud_available():
            prompt = self._build_intent_prompt(query, route_text, context_summary=context_summary)
            try:
                text = self._cloud_client.complete(prompt, max_tokens=256)
                parsed = self._parse_intent_output(text)
                if parsed.get("intent_type"):
                    parsed = self._post_process_intent(parsed, route_text)
                    return parsed
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
        if self.backend == "ollama":
            prompt = self._build_prompt(query, candidates, context, rag_context)
            text = self._ollama_complete(prompt, max_tokens=256)
            parsed = self._parse_output(text)
            if parsed.get("confidence", 0.0) > 0:
                return parsed
        elif self.backend == "llama_cpp" and self._llm is not None:
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
            return self._parse_output(text)
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

    def _build_intent_prompt(
        self,
        query: str,
        normalized_query: str,
        context_summary: Optional[Dict[str, Any]] = None,
    ) -> str:
        return (
            f"用户原始输入: {query}\n"
            f"归一化输入: {normalized_query}\n"
            f"环境摘要: {json.dumps(context_summary or {}, ensure_ascii=False)}\n\n"
            "请判断这条输入属于哪一类，并输出 JSON。\n"
            "intent_type 只能是 chat_reply、action_command、clarification_needed、automation_request。\n"
            "必须包含 intent_type, reply_message, normalized_goal, requires_candidates, "
            "requires_automation, decision_confidence, reasoning 字段。\n"
            '{"intent_type":"","reply_message":"","normalized_goal":"","requires_candidates":false,'
            '"requires_automation":false,"decision_confidence":0.0,"reasoning":""}'
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
        return (
            f"当前环境摘要:\n{json.dumps(cloud_context, ensure_ascii=False, indent=2)}\n"
            f"{rag_block}"
            f"用户输入: {query}\n\n"
            f"候选动作:\n{candidate_str}\n\n"
            "请只从候选动作中选择最合适的一项，并输出固定 JSON。\n"
            "必须包含 action, device, scene, device_action, params, confidence, reasoning 字段。\n"
            '{"action":"","device":"","scene":"","device_action":"","params":{},'
            '"confidence":0.0,"reasoning":""}'
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

        chat_reply = self._match_chat_reply(combined_text, lowered)
        if chat_reply:
            return {
                "intent_type": "chat_reply",
                "route": "reply",
                "reply_message": chat_reply,
                "normalized_goal": "",
                "requires_candidates": False,
                "requires_automation": False,
                "decision_confidence": 0.98,
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
                "decision_confidence": 0.88,
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
                "decision_confidence": 0.96,
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
                "decision_confidence": 0.92,
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
                "decision_confidence": 0.82,
                "reasoning": "识别到控制动作，但需要候选约束进一步确认",
            }

        # Recognize implicit scene switch requests like "切换到XX模式"
        if self._looks_like_scene_switch_request(route_text):
            return {
                "intent_type": "action_command",
                "route": "action",
                "reply_message": "",
                "normalized_goal": route_text,
                "requires_candidates": True,
                "requires_automation": False,
                "decision_confidence": 0.88,
                "reasoning": "识别为场景切换请求",
            }

        # Recognize implicit comfort/action requests like "有点热"
        if self._looks_like_comfort_request(route_text):
            return {
                "intent_type": "action_command",
                "route": "action",
                "reply_message": "",
                "normalized_goal": route_text,
                "requires_candidates": True,
                "requires_automation": False,
                "decision_confidence": 0.80,
                "reasoning": "识别为舒适度/环境调节请求",
            }

        return {
            "intent_type": "clarification_needed",
            "route": "clarify",
            "reply_message": "请问你是想控制设备、切换场景，还是创建定时任务？",
            "normalized_goal": route_text,
            "requires_candidates": False,
            "requires_automation": False,
            "decision_confidence": 0.55,
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
                "params": {"temperature": 26},
                "confidence": confidence,
                "reasoning": f"[CoT] 舒适度关键词结合温度{getattr(context, 'temperature', 26)}°C，优先开启空调",
            }
        if "冷" in query:
            return {
                "action": "设备控制",
                "device": "空调",
                "scene": "",
                "device_action": "on",
                "params": {"temperature": 28},
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
            "reply_message": "请问你是想控制设备、切换场景，还是创建定时任务？",
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
        for candidate in candidates:
            action = candidate.get("action", "")
            for keyword in ["空调", "灯光", "电视", "风扇", "窗户", "音响", "模式"]:
                if keyword in action and keyword not in device_options:
                    device_options.append("场景" if keyword == "模式" else keyword)

        if not device_options:
            device_options = ["空调", "灯光", "电视", "场景"]
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
        scene_keywords = ("睡眠", "待客", "离家", "观影", "起床", "回家", "工作", "早安", "晚归")
        switch_keywords = ("切换", "进入", "开", "启动")
        return any(sk in text for sk in scene_keywords) and any(sw in text for sw in switch_keywords)

    def _looks_like_comfort_request(self, text: str) -> bool:
        """Recognize implicit comfort/environment adjustment requests like '有点热'."""
        text = str(text or "").strip()
        if not text:
            return False
        comfort_keywords = ("热", "冷", "闷", "亮", "暗", "吵", "安静", "困")
        return any(ck in text for ck in comfort_keywords)

    def _post_process_intent(self, parsed: Dict[str, Any], query: str) -> Dict[str, Any]:
        """
        Override LLM intent classification when clear comfort/action keywords are present.
        This ensures consistent behavior across cloud providers.
        """
        if parsed.get("intent_type") == "chat_reply" and self._looks_like_comfort_request(query):
            return {
                "intent_type": "action_command",
                "route": "action",
                "reply_message": "",
                "normalized_goal": query,
                "requires_candidates": True,
                "requires_automation": False,
                "decision_confidence": 0.85,
                "reasoning": "识别为舒适度/环境调节请求（后处理覆盖LLM分类）",
            }
        if parsed.get("intent_type") == "chat_reply" and self._looks_like_scene_switch_request(query):
            return {
                "intent_type": "action_command",
                "route": "action",
                "reply_message": "",
                "normalized_goal": query,
                "requires_candidates": True,
                "requires_automation": False,
                "decision_confidence": 0.88,
                "reasoning": "识别为场景切换请求（后处理覆盖LLM分类）",
            }
        if parsed.get("intent_type") == "action_command" and parsed.get("route") == "clarify":
            parsed["route"] = "action"
            parsed["requires_automation"] = False
            parsed["requires_candidates"] = True
        return parsed

    def _normalize_goal(self, text: str) -> str:
        text = str(text or "").strip()
        if not text:
            return ""
        explicit_devices = (
            "\u7a7a\u8c03",
            "\u706f\u5149",
            "\u706f",
            "\u7535\u89c6",
            "\u97f3\u54cd",
            "\u98ce\u6247",
            "\u7a97\u6237",
            "\u70ed\u6c34\u5668",
        )
        explicit_verbs = ("\u6253\u5f00", "\u5f00\u542f", "\u5173\u95ed", "\u5173\u6389", "\u8c03\u9ad8", "\u8c03\u4f4e", "\u8c03\u4eae", "\u8c03\u6697")
        if any(device in text for device in explicit_devices) and any(verb in text for verb in explicit_verbs):
            return text
        for keywords, normalized in SOFT_COMMAND_NORMALIZATIONS:
            if any(keyword in text for keyword in keywords):
                return normalized
        return text
