"""
LLM decision layer.

The cloud/local LLM is constrained to choose from recalled candidates and return
a structured command. Mock mode remains deterministic for demos and tests.
"""

import json
import logging
from typing import Any, Dict, List

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
}


class LLMDecider:
    """Constrained decider supporting mock, llama.cpp, and OpenAI-compatible APIs."""

    def __init__(self, backend: str = "mock", model_path: str = "", api_base: str = "", api_key: str = ""):
        self.backend = backend
        self.model_path = model_path
        self.api_base = api_base
        self.api_key = api_key
        self._llm = None
        self._client = None
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
            try:
                import openai

                self._client = openai.OpenAI(api_key=self.api_key, base_url=self.api_base)
                logger.info("LLMDecider initialized with OpenAI-compatible API: %s", self.api_base)
            except ImportError:
                logger.warning("openai package is not installed; falling back to mock")
                self.backend = "mock"

    def decide(self, query: str, candidates: List[Dict[str, Any]], context, rag_context: str = "") -> Dict[str, Any]:
        if self.backend == "mock":
            return self._mock_decide(query, candidates, context, rag_context=rag_context)

        prompt = self._build_prompt(query, candidates, context, rag_context)

        if self.backend == "llama_cpp":
            output = self._llm(prompt, max_tokens=256, stop=["```"])
            text = output.get("choices", [{}])[0].get("text", "") if isinstance(output, dict) else str(output)
        elif self.backend == "openai":
            resp = self._client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=256,
            )
            text = resp.choices[0].message.content
        else:
            return self._mock_decide(query, candidates, context, rag_context=rag_context)

        return self._parse_output(text)

    def _build_prompt(self, query: str, candidates: List[Dict[str, Any]], context, rag_context: str = "") -> str:
        candidate_str = "\n".join(f"{idx + 1}. {item['action']}" for idx, item in enumerate(candidates))
        rag_block = f"\n参考知识:\n{rag_context}\n" if rag_context else ""
        return (
            f"当前环境:\n"
            f"时间: {context.hour}:00\n"
            f"温度: {context.temperature}°C\n"
            f"湿度: {context.humidity}%\n"
            f"在家人数: {context.members_home}\n"
            f"{rag_block}"
            f"用户输入: {query}\n\n"
            f"候选动作:\n{candidate_str}\n\n"
            f"请只从候选动作中选择最合适的一项，并输出固定 JSON。"
            f"必须包含 action, device, scene, device_action, params, confidence, reasoning 字段。\n"
            f'{{"action": "", "device": "", "scene": "", "device_action": "", "params": {{}}, '
            f'"confidence": 0.0, "reasoning": ""}}'
        )

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
                "reasoning": f"[CoT] 候选「{top}」直接匹配，映射到{device}的{device_action}操作",
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
                "reasoning": f"[CoT] 候选「{top}」直接匹配，切换到{scene}",
            }

        if "睡眠" in query or "困" in query or "睡觉" in query:
            return self._scene_decision("睡眠模式", confidence, "睡眠相关关键词")
        if "离家" in query or "出门" in query:
            return self._scene_decision("离家模式", confidence, "离家相关关键词")
        if "待客" in query or "客人" in query:
            return self._scene_decision("待客模式", confidence, "待客相关关键词")
        if "观影" in query or "看电影" in query:
            return self._scene_decision("观影模式", confidence, "观影相关关键词")
        if "起床" in query:
            return self._scene_decision("起床模式", confidence, "起床相关关键词")
        if "闷" in query or "热" in query:
            return {
                "action": "设备控制",
                "device": "空调",
                "scene": "",
                "device_action": "on",
                "params": {"temperature": 26},
                "confidence": confidence,
                "reasoning": f"[CoT] 舒适度关键词结合温度{context.temperature}°C，优先开启空调",
            }
        if "冷" in query:
            return {
                "action": "设备控制",
                "device": "空调",
                "scene": "",
                "device_action": "on",
                "params": {"temperature": 28},
                "confidence": confidence,
                "reasoning": "[CoT] 冷感关键词，开启空调制热/升温",
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
            logger.warning("LLM output parse failed: %s", output[:100])
        return {"action": "无法理解", "confidence": 0.0, "reasoning": "[CoT] JSON解析失败"}

    def ask_clarification(self, query: str, candidates: List[Dict[str, Any]]) -> str:
        device_options = []
        for candidate in candidates:
            action = candidate.get("action", "")
            for keyword in ["空调", "灯光", "电视", "风扇", "窗户", "音响", "模式"]:
                if keyword in action:
                    device = keyword if keyword != "模式" else "场景"
                    if device not in device_options:
                        device_options.append(device)

        if not device_options:
            device_options = ["空调", "灯光", "电视", "音量"]
        options = "、".join(device_options[:4])
        return f"请问您想调节哪个设备？{options}？"
