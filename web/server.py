"""
HomeMind Web 服务 - 中央指令器
提供 REST API 和 WebSocket 接口，连接智能家居 Agent 与前端控制面板
"""
import asyncio
import json
import threading
import time
from datetime import datetime
import os

from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from queue import Queue

from core.bsr.candidate_recall import BSRecall
from core.lsr.precision_ranking import LSRecify as PrecisionRanking
from core.llm.decision import LLMDecider as LLMWrapper
from core.dqn.policy import DQNPolicy
from core.rag.knowledge_base import KnowledgeBase
from core.utils.embedding import get_model as get_embedding_model
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
frontend_queue = Queue()

# 全局 Agent 实例
agent = None
device_simulator = None
protocol_gateway = None


class HomeMindWebAgent:
    """支持 Web 接口的 HomeMind Agent"""
    
    def __init__(self, protocol_gateway=None):
        self._gateway = protocol_gateway
        self._init_components()
        self._start_agent_loop()
    
    def _init_components(self):
        """初始化所有组件"""
        print("[初始化] HomeMind Web Agent 组件...")
        
        # 初始化上下文
        self.context = HomeContext()
        self.context.current_scene = "sleep"
        self.context.temperature = 25.0
        self.context.humidity = 60.0
        self.context.occupancy = 1
        
        # 初始化设备模拟器
        self.device_simulator = DeviceSimulator()
        self.simulator = self.device_simulator
        
        # 初始化工具（传入协议网关）
        self.device_control = device_ctrl.DeviceController(protocol_gateway=self._gateway)
        self.info_query = info_query.InfoQuery()
        self.scene_switcher = scene_switch.SceneSwitcher(self.device_control)
        
        # 尝试初始化 Embedding 和知识库
        self.embedding_model = None
        self.kb = None
        try:
            self.embedding_model = get_embedding_model()
            if self.embedding_model:
                self.kb = KnowledgeBase(embedding_fn=self.embedding_model.encode)
                self.kb_writer = kb_writer.KBWriter(self.kb)
                print("[初始化] ChromaDB 知识库已加载")
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
            self.llm = LLMWrapper(backend="mock")
            print("[初始化] LLM 决策模块已加载")
        except Exception as e:
            print(f"[警告] LLM 初始化失败: {e}")
        
        try:
            self.dqn = DQNPolicy()
            self.dqn_fb = DQNFeedbackTool(self.dqn)
            print("[初始化] DQN 策略模块已加载")
        except Exception as e:
            print(f"[警告] DQN 初始化失败: {e}")
        
        print("[初始化] 完成!")
    
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
        query_id = f"q_{int(time.time() * 1000)}"
        print(f"[Agent] 收到用户输入: {user_text}")

        # 更新上下文时间
        self.context.hour = datetime.now().hour

        # 初始化流水线状态
        pipeline = {
            "query_id": query_id,
            "query": user_text,
            "steps": {
                "bsr": {"status": "pending", "candidates": []},
                "lsr": {"status": "pending", "ranked": []},
                "llm": {"status": "pending", "decision": None},
                "exec": {"status": "pending", "result": None},
            }
        }
        socketio.emit("pipeline_update", {"type": "pipeline_start", "data": pipeline})

        # 尝试使用完整流程，否则使用简单规则匹配
        if self.bsr and self.lsr and self.llm:
            try:
                # Step 1: BSR 召回
                candidates = self.bsr.recall(user_text, self.context, top_k=5)
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
                    self._emit_fallback(query_id, user_text)
                    return

                # Step 2: LSR 精排
                ranked = self.lsr.rank(candidates, user_text, self.context)
                pipeline["steps"]["lsr"] = {
                    "status": "done",
                    "ranked": [
                        {"id": i, "action": r.get("action", ""), "score": float(r.get("score", 0))}
                        for i, r in enumerate(ranked)
                    ]
                }
                socketio.emit("pipeline_update", {"type": "pipeline_step", "data": {
                    "query_id": query_id, "step": "lsr", "data": pipeline["steps"]["lsr"]
                }})

                if not ranked:
                    self._emit_fallback(query_id, user_text)
                    return

                # Step 3: LLM 决策
                decision = self.llm.decide(user_text, ranked, self.context, rag_context="")
                device = decision.get("device", "")
                device_action = decision.get("device_action", "")
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
                        "reasoning": reasoning
                    }
                }
                socketio.emit("pipeline_update", {"type": "pipeline_step", "data": {
                    "query_id": query_id, "step": "llm", "data": pipeline["steps"]["llm"]
                }})

                # Step 4: 执行
                if device and device_action:
                    self.device_control.execute(device, device_action, params)
                    result = {"status": "success", "action": f"{device}_{device_action}", "device": device, "device_action": device_action, "params": params}
                else:
                    result = {"status": "no_action", "candidates": [r["action"] for r in ranked[:3]]}

                pipeline["steps"]["exec"] = {"status": "done", "result": result}
                socketio.emit("pipeline_update", {"type": "pipeline_step", "data": {
                    "query_id": query_id, "step": "exec", "data": pipeline["steps"]["exec"]
                }})

                # 最终响应
                if result["status"] == "success":
                    socketio.emit("message", {
                        "type": "agent_response",
                        "data": {
                            "action": result["action"],
                            "result": f"已执行: {device} {device_action}",
                            "confidence": confidence,
                            "scene": self.context.current_scene,
                            "query_id": query_id
                        }
                    })
                else:
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
                self._simple_process(user_text)
        else:
            self._emit_pipeline_error(query_id, "AI 模块未加载，降级为规则匹配")
            self._simple_process(user_text)

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
            self.device_control.execute("light", "on", {})
            action_taken = True
        elif "关" in user_text and "灯" in user_text:
            self.device_control.execute("light", "off", {})
            action_taken = True
        elif "空调" in user_text:
            if "开" in user_text:
                temp = 26
                if "度" in user_text:
                    import re
                    match = re.search(r'(\d+)度', user_text)
                    if match:
                        temp = int(match.group(1))
                self.device_control.execute("air_conditioner", "on", {"temperature": temp})
                action_taken = True
            elif "关" in user_text:
                self.device_control.execute("air_conditioner", "off", {})
                action_taken = True
        elif "电视" in user_text or "tv" in user_text_lower:
            if "开" in user_text:
                self.device_control.execute("tv", "on", {})
                action_taken = True
            elif "关" in user_text:
                self.device_control.execute("tv", "off", {})
                action_taken = True
        
        if action_taken:
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
            self.bsr.recall(f"切换到{scene}场景", self.context, top_k=3)
        
        self.scene_switcher.switch(scene)
        self.context.current_scene = scene_id
        
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
    
    def _execute_action(self, action: str):
        """执行动作"""
        if "ac" in action:
            if "on" in action:
                temp = int(action.split("_")[-1]) if "_" in action else 26
                self.device_control.execute("air_conditioner", "on", {"temperature": temp})
        elif "light" in action:
            self.device_control.execute("light", "on", {})
        elif "scene" in action:
            scene_name = action.replace("scene_", "")
            self.scene_switcher.switch(scene_name)
    
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
                "occupancy": self.context.occupancy,
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
        
        if not self.bsr or not self.lsr or not self.llm:
            return {"status": "no_action", "message": "AI 模块未加载"}
        
        try:
            candidates = self.bsr.recall(query, self.context, top_k=5)
            ranked = self.lsr.rank(candidates, query, self.context)
            
            if ranked:
                decision = self.llm.decide(query, ranked, self.context, rag_context="")
                
                device = decision.get("device", "")
                device_action = decision.get("device_action", "")
                params = decision.get("params", {})
                
                if device and device_action:
                    self.device_control.execute(device, device_action, params)
                    return {
                        "status": "success",
                        "action": f"{device}_{device_action}",
                        "response": f"已执行: {device} {device_action}",
                        "confidence": decision.get("confidence", 0.9)
                    }
                else:
                    return {
                        "status": "clarification",
                        "question": "我需要更多信息",
                        "candidates": [r["action"] for r in ranked[:3]]
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


@app.route("/api/scenes/<scene>/switch", methods=["POST"])
def switch_scene(scene):
    """场景切换接口"""
    if agent:
        scene_name = agent.SCENE_ID_MAP.get(scene, scene)
        agent.scene_switcher.switch(scene_name)
        agent.context.current_scene = scene
        return jsonify({
            "status": "success",
            "scene": scene,
            "devices": agent.get_all_states()["devices"]
        })
    
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/info/<info_type>", methods=["GET"])
def query_info(info_type):
    """信息查询接口"""
    if agent:
        result = agent.info_query.query(info_type)
        return jsonify({
            "status": "success",
            "type": info_type,
            "result": result
        })
    
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/dqn/recommend", methods=["GET"])
def dqn_recommend():
    """DQN 主动推荐"""
    if agent:
        agent.context.hour = datetime.now().hour
        action = agent.dqn.recommend(agent.context)
        
        if action:
            scene_map = {
                0: "sleep",
                1: "entertainment", 
                2: "away",
                3: "work",
                4: "morning",
                5: "evening"
            }
            recommended_scene = scene_map.get(action, "sleep")
            
            return jsonify({
                "status": "success",
                "recommendation": {
                    "id": f"dqn_{int(time.time())}",
                    "scene": recommended_scene,
                    "reason": f"基于当前环境状态推荐{recommended_scene}场景"
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
    
    if agent:
        agent.dqn_fb.record(rec_id, response, "", agent.context)
        return jsonify({"status": "success"})
    
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/kb/query", methods=["POST"])
def kb_query():
    """知识库查询"""
    data = request.get_json()
    query_text = data.get("query", "")
    top_k = data.get("top_k", 3)
    
    if agent:
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
    
    if agent:
        agent.kb_writer.write(text, category)
        return jsonify({"status": "success"})
    
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
    """"语音转文字接口（预留，未来支持 faster-whisper）"""""
    # 目前使用浏览器端 Web Speech API 进行语音识别
    # 此接口为未来服务器端语音识别预留
    return jsonify({
        "error": "服务器端语音识别尚未配置",
        "hint": "前端已使用浏览器 Web Speech API 进行语音识别",
        "status": "browser_only"
    }), 501
# ==================== WebSocket 事件 ====================

@socketio.on("connect")
def on_connect():
    """客户端连接"""
    print(f"[WebSocket] 客户端连接: {request.sid}")
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
    if agent:
        agent_queue.put({"type": "user_input", "data": data})


# ==================== 主程序 ====================

def init_agent(mode: str = None, protocol_gateway=None):
    """初始化全局 Agent"""
    global agent, device_simulator

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
    
    socketio.run(app, host="0.0.0.0", port=5000, debug=True, allow_unsafe_werkzeug=True)
