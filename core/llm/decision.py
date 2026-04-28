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
)

ACTION_HINTS = (
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

    def _normalize_goal(self, text: str) -> str:
        text = str(text or "").strip()
        if not text:
            return ""
        for keywords, normalized in SOFT_COMMAND_NORMALIZATIONS:
            if any(keyword in text for keyword in keywords):
                return normalized
        return text
