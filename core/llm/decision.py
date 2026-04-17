"""
LLM 决策层
将 LLM 从"全盘理解"转变为"决策选择"
只做两件事：
  1. 在候选中选最合适的动作
  2. 生成参数
支持 llama.cpp / OpenAI 兼容接口 / 模拟三种模式
RAG 检索结果注入 Prompt，增强上下文可信度
引入 CoT（Chain of Thought）思维链，提升推理透明度和置信度评估
"""

import json
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# 设备与动作关键词的完整映射
DEVICE_ACTION_MAP = {
    "打开空调":     ("设备控制", "空调",    "on",     {"temperature": 26}),
    "关闭空调":     ("设备控制", "空调",    "off",    {}),
    "调高空调温度": ("设备控制", "空调",    "adjust", {"temperature": 28}),
    "调低空调温度": ("设备控制", "空调",    "adjust", {"temperature": 24}),
    "打开灯光":     ("设备控制", "灯光",    "on",     {"brightness": 100}),
    "关闭灯光":     ("设备控制", "灯光",    "off",    {}),
    "调亮灯光":     ("设备控制", "灯光",    "adjust", {"brightness": 100}),
    "调暗灯光":     ("设备控制", "灯光",    "adjust", {"brightness": 30}),
    "打开电视":     ("设备控制", "电视",    "on",     {}),
    "关闭电视":     ("设备控制", "电视",    "off",    {}),
    "打开风扇":     ("设备控制", "风扇",    "on",     {}),
    "关闭风扇":     ("设备控制", "风扇",    "off",    {}),
    "打开窗户":     ("设备控制", "窗户",    "open",   {}),
    "关闭窗户":     ("设备控制", "窗户",    "close",  {}),
    "打开音响":     ("设备控制", "音响",    "on",     {"volume": 30}),
    "关闭音响":     ("设备控制", "音响",    "off",    {}),
    "打开暖气":     ("设备控制", "空调",    "on",     {"temperature": 24, "mode": "制热"}),
    "打开热水器":   ("设备控制", "热水器",  "on",     {}),
    "关闭热水器":   ("设备控制", "热水器",  "off",    {}),
    "切换睡眠模式": ("场景切换", "睡眠模式", "",      {}),
    "切换待客模式": ("场景切换", "待客模式", "",      {}),
    "切换离家模式": ("场景切换", "离家模式", "",      {}),
    "切换观影模式": ("场景切换", "观影模式", "",      {}),
    "切换起床模式": ("场景切换", "起床模式", "",      {}),
    "切换回家模式": ("场景切换", "回家模式", "",      {}),
}


class LLMDecider:
    """
    LLM 决策器
    支持三种推理后端：
      - llama_cpp: 本地 llama.cpp C++ 后端
      - openai: OpenAI 兼容 API
      - mock: 开发/演示模式（默认）
    """

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
            logger.info("LLMDecider 初始化: mock 模式（开发/演示）")
        elif self.backend == "llama_cpp":
            logger.info(f"LLMDecider 初始化: llama.cpp ({self.model_path})")
            try:
                from llama_cpp import Llama
                self._llm = Llama(model_path=self.model_path, n_ctx=2048, n_threads=4)
            except ImportError:
                logger.warning("llama-cpp-python 未安装，fallback 到 mock")
                self.backend = "mock"
        elif self.backend == "openai":
            logger.info(f"LLMDecider 初始化: OpenAI 兼容 ({self.api_base})")
            try:
                import openai
                self._client = openai.OpenAI(api_key=self.api_key, base_url=self.api_base)
            except ImportError:
                logger.warning("openai 包未安装，fallback 到 mock")
                self.backend = "mock"

    def decide(self, query: str, candidates: List[Dict[str, Any]], context, rag_context: str = "") -> Dict[str, Any]:
        """
        决策：在候选中选最合适的动作，生成参数
        rag_context: RAG 检索增强的上下文知识
        返回结构化决策：
          {
            "action": "设备控制",
            "device": "空调",
            "device_action": "on",
            "params": {"temperature": 26},
            "confidence": 0.91
          }
        """
        if self.backend == "mock":
            return self._mock_decide(query, candidates, context)

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

        return self._parse_output(text)

    def _build_prompt(self, query: str, candidates: List[Dict[str, Any]],
                      context, rag_context: str = "") -> str:
        """构建包含环境上下文、RAG 知识和候选动作的完整 Prompt（带 CoT）"""
        candidate_str = "\n".join(
            f"{i+1}. {c['action']}" for i, c in enumerate(candidates)
        )

        rag_block = f"\n参考知识：\n{rag_context}\n" if rag_context else ""

        return (
            f"当前环境：\n"
            f"时间: {context.hour}:00\n"
            f"温度: {context.temperature}°C\n"
            f"湿度: {context.humidity}%\n"
            f"在家人数: {context.members_home}\n"
            f"{rag_block}"
            f"用户输入：{query}\n\n"
            f"候选动作：\n{candidate_str}\n\n"
            f"请按以下步骤思考（Chain of Thought）：\n"
            f"1. 分析用户意图：...（根据环境上下文推断）\n"
            f"2. 评估候选动作的合适性：...\n"
            f"3. 选择最合适的动作：...\n"
            f"4. 确定参数：...\n"
            f"5. 给出置信度：...\n\n"
            f"输出JSON（必须包含 action, device, device_action, params, confidence 字段）：\n"
            f'{{"action": "", "device": "", "device_action": "", "params": {{}}, "confidence": 0.0}}'
        )

    def _mock_decide(self, query: str, candidates: List[Dict[str, Any]], context) -> Dict[str, Any]:
        """演示模式：基于规则的模拟决策，带 CoT 推理链"""
        top = candidates[0]["action"] if candidates else ""
        confidence = candidates[0].get("final_score", 0.8) if candidates else 0.5

        if top in DEVICE_ACTION_MAP:
            action, device, device_action, params = DEVICE_ACTION_MAP[top]
            return {
                "action": action, "device": device,
                "device_action": device_action, "params": params,
                "confidence": confidence,
                "reasoning": f"[CoT] 候选「{top}」直接匹配，映射到{device}的{action}操作",
            }

        if "睡眠" in query or "困" in query or "睡觉" in query:
            return {"action": "场景切换", "scene": "睡眠模式", "params": {}, "confidence": confidence,
                    "reasoning": "[CoT] 关键词「睡眠/困/睡觉」直接指向睡眠模式"}
        if "离家" in query or "出门" in query:
            return {"action": "场景切换", "scene": "离家模式", "params": {}, "confidence": confidence,
                    "reasoning": "[CoT] 关键词「离家/出门」直接指向离家模式"}
        if "待客" in query or "客人" in query:
            return {"action": "场景切换", "scene": "待客模式", "params": {}, "confidence": confidence,
                    "reasoning": "[CoT] 关键词「待客/客人」直接指向待客模式"}
        if "观影" in query or "看电影" in query:
            return {"action": "场景切换", "scene": "观影模式", "params": {}, "confidence": confidence,
                    "reasoning": "[CoT] 关键词「观影/看电影」直接指向观影模式"}
        if "起床" in query:
            return {"action": "场景切换", "scene": "起床模式", "params": {}, "confidence": confidence,
                    "reasoning": "[CoT] 关键词「起床」直接指向起床模式"}
        if "闷" in query or "热" in query:
            return {"action": "设备控制", "device": "空调", "device_action": "on",
                    "params": {"temperature": 26}, "confidence": confidence,
                    "reasoning": f"[CoT] 关键词「闷/热」+ 环境温度{context.temperature}°C → 开启空调降温"}
        if "冷" in query:
            return {"action": "设备控制", "device": "空调", "device_action": "on",
                    "params": {"temperature": 28}, "confidence": confidence,
                    "reasoning": "[CoT] 关键词「冷」→ 开启空调制热升温"}
        if "亮" in query and "暗" not in query:
            return {"action": "设备控制", "device": "灯光", "device_action": "adjust",
                    "params": {"brightness": 100}, "confidence": confidence,
                    "reasoning": "[CoT] 关键词「亮」→ 调亮灯光"}
        if "暗" in query:
            return {"action": "设备控制", "device": "灯光", "device_action": "adjust",
                    "params": {"brightness": 30}, "confidence": confidence,
                    "reasoning": "[CoT] 关键词「暗」→ 调暗灯光"}
        if "凉" in query:
            return {"action": "设备控制", "device": "风扇", "device_action": "on",
                    "params": {}, "confidence": confidence,
                    "reasoning": "[CoT] 关键词「凉」→ 开启风扇通风"}
        if "风" in query or "通风" in query:
            return {"action": "设备控制", "device": "风扇", "device_action": "on",
                    "params": {}, "confidence": confidence,
                    "reasoning": "[CoT] 关键词「风/通风」→ 开启风扇"}

        return {"action": top, "device": "", "device_action": "", "params": {}, "confidence": confidence,
                "reasoning": "[CoT] 无明确关键词匹配，基于候选动作 fallback"}

    def _parse_output(self, output: str) -> Dict[str, Any]:
        """解析 LLM 输出为结构化决策，增加字段验证和 CoT 提取"""
        try:
            start = output.find("{")
            end = output.rfind("}") + 1
            if start != -1 and end > start:
                result = json.loads(output[start:end])
                if "confidence" not in result:
                    result["confidence"] = 0.8
                if "action" not in result:
                    result["action"] = "无法理解"
                if "device" not in result:
                    result["device"] = ""
                if "device_action" not in result:
                    result["device_action"] = ""
                if "params" not in result:
                    result["params"] = {}
                reasoning_lines = []
                for line in output[:start].split("\n"):
                    if "1." in line or "2." in line or "3." in line or "4." in line or "5." in line:
                        reasoning_lines.append(line.strip())
                if reasoning_lines:
                    result["reasoning"] = " | ".join(reasoning_lines)
                return result
        except json.JSONDecodeError:
            logger.warning(f"LLM 输出解析失败: {output[:100]}")
        return {"action": "无法理解", "confidence": 0.0, "reasoning": "[CoT] JSON解析失败，返回兜底结果"}

    def ask_clarification(self, query: str, candidates: List[Dict[str, Any]]) -> str:
        """置信度低于阈值时，主动向用户提问澄清"""
        device_options = []
        for c in candidates:
            a = c.get("action", "")
            for kw in ["空调", "灯光", "电视", "风扇", "窗户", "音响", "模式"]:
                if kw in a:
                    dev = kw if kw != "模式" else "场景"
                    if dev not in device_options:
                        device_options.append(dev)

        if not device_options:
            device_options = ["空调", "灯光", "电视", "音量"]
        options_str = "、".join(device_options[:4])
        return f"请问您想调节哪个设备？{options_str}？"
