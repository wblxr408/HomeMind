"""
HomeMind Web 服务器
提供 SSE 流式输出接口 + REST API
支持前端实时展示推理流程
"""

import json
import logging
import threading
import time
from typing import Optional, Generator, Dict, Any
from dataclasses import dataclass, asdict

from flask import Flask, Response, request, jsonify, render_template
from flask_cors import CORS

from main import HomeMindAgent
from demo.simulator import HomeSimulator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)

agent: Optional[HomeMindAgent] = None


def init_agent():
    """延迟初始化 Agent，避免启动时加载全部模型"""
    global agent
    if agent is None:
        logger.info("初始化 HomeMind Agent...")
        sim = HomeSimulator()
        agent = HomeMindAgent()
        agent.attach_simulator(sim)
        logger.info("HomeMind Agent 初始化完成")


@dataclass
class PipelineStep:
    """流水线步骤状态"""
    stage: str
    status: str
    message: str
    data: Optional[Dict] = None

    def to_sse(self) -> str:
        return f"data: {json.dumps(asdict(self), ensure_ascii=False)}\n\n"


def run_pipeline_stream(query: str) -> Generator[str, None, None]:
    """运行完整流水线，以 SSE 形式流式输出每个步骤"""
    global agent

    if agent is None:
        init_agent()

    context = agent._simulator.get_context() if agent._simulator else agent.context

    def emit(stage: str, status: str, message: str, data: Optional[Dict] = None):
        step = PipelineStep(stage=stage, status=status, message=message, data=data)
        yield step.to_sse()

    yield from emit("input", "processing", f"收到输入：{query}")

    time.sleep(0.1)
    yield from emit("bsr", "processing", "正在召回候选动作...")

    candidates = agent.bsr.recall(query, context)
    yield from emit(
        "bsr", "done",
        f"BSR 召回完成，共 {len(candidates)} 个候选",
        {"candidates": [c.get("action", "") for c in candidates]}
    )

    time.sleep(0.1)
    yield from emit("lsr", "processing", "正在进行轻量精排...")

    ranked = agent.lsr.rank(query, candidates, context, kb=agent.kb)
    top = ranked[0] if ranked else {}
    yield from emit(
        "lsr", "done",
        f"精排 Top-1：{top.get('action', '')} (score={top.get('final_score', 0):.3f})",
        {"ranked": [{"action": r.get("action"), "score": r.get("final_score")} for r in ranked[:3]]}
    )

    time.sleep(0.1)
    yield from emit("llm", "processing", "LLM 正在决策...")

    rag_context = agent.kb.get_context_prompt(query, context)
    decision = agent.llm.decide(query, ranked, context, rag_context=rag_context)
    reasoning = decision.get("reasoning", "")
    yield from emit(
        "llm", "done",
        f"决策完成，置信度={decision.get('confidence', 0):.2f}",
        {"decision": decision, "reasoning": reasoning}
    )

    time.sleep(0.1)
    yield from emit("action", "processing", "正在执行动作...")

    result = ""
    action = decision.get("action", "")

    if decision.get("confidence", 0) < agent.confidence_threshold:
        clarification = agent.llm.ask_clarification(query, ranked)
        yield from emit("action", "done", "需要澄清", {"result": clarification, "needs_clarify": True})
    elif action == "设备控制":
        device = decision.get("device", "")
        device_action = decision.get("device_action", "")
        params = decision.get("params", {})
        result = agent.device_ctrl.execute(device, device_action, params)
        agent._sync_devices_from_controller()
        yield from emit("action", "done", f"设备控制完成", {"result": result, "device": device, "device_action": device_action})
    elif action == "场景切换":
        scene = decision.get("scene", "")
        result = agent.scene_switcher.execute(scene)
        agent._sync_scene_to_simulator(scene)
        agent.context.last_scene = agent._scene_to_index(scene)
        yield from emit("action", "done", f"场景切换完成", {"result": result, "scene": scene})
    elif action == "信息查询":
        query_type = decision.get("query_type", "")
        result = agent.info_query.execute(query_type, decision.get("params", {}))
        yield from emit("action", "done", "信息查询完成", {"result": result})
    else:
        result = f"执行了: {action}，参数: {decision.get('params', {})}"

    if action and decision.get("confidence", 0) >= agent.confidence_threshold:
        agent.kb_writer.write_feedback(query, decision, "接受")

    yield from emit("rag", "processing", "正在写入知识库...")
    yield from emit("rag", "done", "知识库更新完成", {"kb_count": agent.kb.count()})

    time.sleep(0.1)
    yield from emit("done", "done", "处理完成", {
        "result": result,
        "final_result": result,
        "confidence": decision.get("confidence", 0),
        "kb_count": agent.kb.count(),
    })

    yield "data: [DONE]\n\n"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    """SSE 流式聊天接口"""
    data = request.get_json() or {}
    query = data.get("query", "").strip()

    if not query:
        return jsonify({"error": "输入不能为空"}), 400

    init_agent()
    return Response(
        run_pipeline_stream(query),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


@app.route("/api/dqn/recommend", methods=["GET"])
def dqn_recommend():
    """DQN 主动推荐接口"""
    init_agent()
    result = agent.proactive_recommend()
    return jsonify({"recommendation": result, "context": agent.context.to_state_dict()})


@app.route("/api/dqn/respond", methods=["POST"])
def dqn_respond():
    """用户对 DQN 推荐的响应"""
    init_agent()
    data = request.get_json() or {}
    response = data.get("response", "")
    result = agent.respond_to_recommendation(response)
    return jsonify({"result": result})


@app.route("/api/context", methods=["GET"])
def get_context():
    """获取当前环境上下文"""
    init_agent()
    ctx = agent.context
    return jsonify({
        "hour": ctx.hour,
        "temperature": ctx.temperature,
        "humidity": ctx.humidity,
        "members_home": ctx.members_home,
        "devices": ctx.devices,
        "last_scene": ctx.last_scene,
    })


@app.route("/api/context", methods=["PUT"])
def update_context():
    """更新环境上下文"""
    init_agent()
    data = request.get_json() or {}
    agent.update_context(**data)
    return jsonify({"success": True, "context": agent.context.to_state_dict()})


@app.route("/api/kb/count", methods=["GET"])
def kb_count():
    """获取知识库记录数"""
    init_agent()
    return jsonify({"count": agent.kb.count()})


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    init_agent()
    print("\n" + "=" * 60)
    print("  HomeMind Web 服务已启动")
    print("  访问地址: http://127.0.0.1:5000")
    print("  SSE 接口: POST /api/chat")
    print("=" * 60 + "\n")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
