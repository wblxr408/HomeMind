"""
HomeMind Web 服务 - 中央指令器
提供 REST API 和 WebSocket 接口，连接智能家居 Agent 与前端控制面板
"""
import json
import threading
import time
from datetime import datetime
import os
import hashlib
import math
import re
from pathlib import Path

from flask import Flask, request, jsonify, Response, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from queue import Queue

from core.bsr.candidate_recall import BSRecall
from core.lsr.precision_ranking import LSRecify as PrecisionRanking
from core.llm.decision import LLMDecider as LLMWrapper
from core.dqn.policy import DQNPolicy
from core.automation import TapRuleEngine
from core.rag.knowledge_base import KnowledgeBase
from core.schema import (
    validate_device_command,
    validate_device_mapping_payload,
    validate_tap_code as validate_tap_code_payload,
    validate_tap_rule_payload,
)
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
storage_lock = threading.Lock()

BASE_DIR = Path(__file__).resolve().parents[1]
HAUSRAGEN_ROOT = Path(r"D:\HausRAGen-main")
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = BASE_DIR / "uploads"
FLOOR_PLAN_UPLOAD_DIR = UPLOADS_DIR / "floor-plans"
FLOOR_PLAN_STORE_PATH = DATA_DIR / "floor-plans.json"
DEVICE_MAPPING_STORE_PATH = DATA_DIR / "devices.json"
TAP_RULE_STORE_PATH = DATA_DIR / "tap-rules.json"
EXTERNAL_FLOOR_PLAN_STORE_PATH = HAUSRAGEN_ROOT / "data" / "floor-plans.json"
EXTERNAL_DEVICE_MAPPING_STORE_PATH = HAUSRAGEN_ROOT / "data" / "devices.json"
EXTERNAL_FLOOR_PLAN_UPLOAD_DIR = HAUSRAGEN_ROOT / "uploads" / "floor-plans"
SVG_DIMENSION_FALLBACK = {"width": 640.0, "height": 660.0}
DEFAULT_ROOM_AREAS = {
    "living_room": {"x1": 8.0, "y1": 14.0, "x2": 42.0, "y2": 44.0},
    "bedroom": {"x1": 50.0, "y1": 12.0, "x2": 84.0, "y2": 42.0},
    "bedroom2": {"x1": 50.0, "y1": 46.0, "x2": 84.0, "y2": 76.0},
    "kitchen": {"x1": 8.0, "y1": 50.0, "x2": 28.0, "y2": 76.0},
    "bathroom": {"x1": 32.0, "y1": 50.0, "x2": 46.0, "y2": 74.0},
    "study": {"x1": 8.0, "y1": 78.0, "x2": 30.0, "y2": 94.0},
    "dining_room": {"x1": 34.0, "y1": 50.0, "x2": 60.0, "y2": 76.0},
    "entrance": {"x1": 62.0, "y1": 78.0, "x2": 84.0, "y2": 94.0},
    "balcony": {"x1": 8.0, "y1": 4.0, "x2": 84.0, "y2": 12.0},
}
tap_rule_engine = TapRuleEngine()
SUPPORTED_DEVICE_TYPES = {
    "light",
    "air_conditioner",
    "tv",
    "water_heater",
    "fan",
    "speaker",
    "window",
    "switch",
    "sensor",
    "camera",
    "plug",
    "door_sensor",
    "doorbell",
    "door_window_sensor",
    "motion_sensor",
    "robot_vacuum",
    "temperature_humidity_sensor",
}


def configure_spatial_storage(base_dir: Path | str | None = None):
    """Allow tests to isolate spatial storage from the repo workspace."""
    global DATA_DIR, UPLOADS_DIR, FLOOR_PLAN_UPLOAD_DIR, FLOOR_PLAN_STORE_PATH, DEVICE_MAPPING_STORE_PATH, TAP_RULE_STORE_PATH

    if base_dir is None:
        root = BASE_DIR
    else:
        root = Path(base_dir)

    DATA_DIR = root / "data"
    UPLOADS_DIR = root / "uploads"
    FLOOR_PLAN_UPLOAD_DIR = UPLOADS_DIR / "floor-plans"
    FLOOR_PLAN_STORE_PATH = DATA_DIR / "floor-plans.json"
    DEVICE_MAPPING_STORE_PATH = DATA_DIR / "devices.json"
    TAP_RULE_STORE_PATH = DATA_DIR / "tap-rules.json"
    ensure_spatial_storage()


def ensure_spatial_storage():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FLOOR_PLAN_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    for path in (FLOOR_PLAN_STORE_PATH, DEVICE_MAPPING_STORE_PATH, TAP_RULE_STORE_PATH):
        if not path.exists():
            path.write_text("[]", encoding="utf-8")


def load_json_store(path: Path):
    ensure_spatial_storage()
    target = path
    fallback = None
    if path == FLOOR_PLAN_STORE_PATH:
        fallback = EXTERNAL_FLOOR_PLAN_STORE_PATH
    elif path == DEVICE_MAPPING_STORE_PATH:
        fallback = EXTERNAL_DEVICE_MAPPING_STORE_PATH

    if not target.exists() and fallback and fallback.exists():
        target = fallback
    elif target.exists():
        raw = target.read_text(encoding="utf-8").strip()
        if raw in {"", "[]"} and fallback and fallback.exists():
            target = fallback
    elif not target.exists():
        return []
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def save_json_store(path: Path, payload):
    ensure_spatial_storage()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_svg_dimension(raw: str | None) -> float | None:
    if not raw:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", raw)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def extract_svg_dimensions(svg_content: str) -> tuple[float, float]:
    width = SVG_DIMENSION_FALLBACK["width"]
    height = SVG_DIMENSION_FALLBACK["height"]

    view_box_match = re.search(r'viewBox=["\']([^"\']+)["\']', svg_content, flags=re.IGNORECASE)
    if view_box_match:
        parts = re.split(r"\s+", view_box_match.group(1).strip())
        if len(parts) == 4:
            width = _parse_svg_dimension(parts[2]) or width
            height = _parse_svg_dimension(parts[3]) or height
            return width, height

    width_match = re.search(r'width=["\']([^"\']+)["\']', svg_content, flags=re.IGNORECASE)
    height_match = re.search(r'height=["\']([^"\']+)["\']', svg_content, flags=re.IGNORECASE)
    width = _parse_svg_dimension(width_match.group(1) if width_match else None) or width
    height = _parse_svg_dimension(height_match.group(1) if height_match else None) or height
    return width, height


def normalize_room_name(name: str) -> str:
    value = str(name or "").strip().lower()
    return re.sub(r"[^a-z0-9_]+", "_", value)


def humanize_entity_name(entity_id: str) -> str:
    entity = str(entity_id or "").strip()
    if "." in entity:
        entity = entity.split(".", 1)[1]
    return entity.replace("_", " ").strip() or "unknown"


def normalize_custom_rooms(raw_rooms) -> dict[str, dict[str, float]]:
    normalized = {}
    if not raw_rooms:
        return normalized
    iterable = raw_rooms.values() if isinstance(raw_rooms, dict) else raw_rooms
    for item in iterable:
        if not isinstance(item, dict):
            continue
        area = normalize_room_name(item.get("area") or item.get("name") or item.get("room"))
        if not area:
            continue
        try:
            x1 = float(item.get("x1", item.get("left", 0)))
            y1 = float(item.get("y1", item.get("top", 0)))
            x2 = float(item.get("x2", item.get("right", 100)))
            y2 = float(item.get("y2", item.get("bottom", 100)))
        except (TypeError, ValueError):
            continue
        if x2 <= x1 or y2 <= y1:
            continue
        normalized[area] = {
            "x1": max(0.0, min(100.0, x1)),
            "y1": max(0.0, min(100.0, y1)),
            "x2": max(0.0, min(100.0, x2)),
            "y2": max(0.0, min(100.0, y2)),
        }
    return normalized


def fallback_room_bounds(area: str) -> dict[str, float]:
    digest = hashlib.md5(area.encode("utf-8")).digest()
    x1 = 6.0 + (digest[0] % 50)
    y1 = 10.0 + (digest[1] % 58)
    width = 18.0 + (digest[2] % 12)
    height = 14.0 + (digest[3] % 10)
    x2 = min(94.0, x1 + width)
    y2 = min(94.0, y1 + height)
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}


def normalize_device_mapping_to_tuples(input_data):
    return validate_device_mapping_payload(
        input_data,
        supported_device_types=SUPPORTED_DEVICE_TYPES,
    ).to_legacy_response()


def compute_device_positions(raw_devices, custom_rooms=None):
    room_areas = {**DEFAULT_ROOM_AREAS, **normalize_custom_rooms(custom_rooms)}
    grouped = {}
    for entity_id, area, device_type in raw_devices:
        key = normalize_room_name(area) or "unknown"
        grouped.setdefault(key, []).append((entity_id, area, device_type))

    positioned = []
    for area_key, entries in grouped.items():
        bounds = room_areas.get(area_key, fallback_room_bounds(area_key))
        total = len(entries)
        cols = max(1, min(3, math.ceil(math.sqrt(total))))
        rows = max(1, math.ceil(total / cols))
        width = max(6.0, bounds["x2"] - bounds["x1"])
        height = max(6.0, bounds["y2"] - bounds["y1"])

        for idx, (entity_id, area, device_type) in enumerate(entries):
            row = idx // cols
            col = idx % cols
            x = bounds["x1"] + (col + 1) * width / (cols + 1)
            y = bounds["y1"] + (row + 1) * height / (rows + 1)
            normalized_type = normalize_room_name(device_type) or "light"
            positioned.append({
                "id": entity_id,
                "entity_id": entity_id,
                "area": area,
                "type": normalized_type,
                "name": humanize_entity_name(entity_id),
                "x": round(x, 2),
                "y": round(y, 2),
            })
    return positioned


def send_sse(data) -> str:
    if isinstance(data, str):
        return f"data: {data}\n\n"
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def validate_tap_code(code: str, auto_fix: bool = True) -> dict:
    return validate_tap_code_payload(code, auto_fix=auto_fix)


def compress_text_content(text: str, options: dict | None = None) -> dict:
    options = options or {}
    original = str(text or "")
    lines = original.splitlines()
    deduped_lines = []
    seen = set()
    removed_comments = 0

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("//"):
            removed_comments += len(line)
            continue
        compact = re.sub(r"\s+", " ", stripped)
        if compact and compact not in seen:
            deduped_lines.append(compact)
            seen.add(compact)

    compressed = "\n".join(deduped_lines)
    compressed = re.sub(r"\n{3,}", "\n\n", compressed)
    compressed = re.sub(r"[ \t]{2,}", " ", compressed)

    if options.get("aggressive"):
        compressed = compressed.replace("\n", " ")
        compressed = re.sub(r"\s{2,}", " ", compressed).strip()

    original_len = len(original)
    final_len = len(compressed)
    ratio = round((1 - final_len / original_len) * 100, 1) if original_len else 0.0
    return {
        "compressed": compressed.strip(),
        "report": {
            "originalLength": original_len,
            "compressedLength": final_len,
            "compressionRate": f"{ratio}%",
            "commentsRemoved": removed_comments,
            "deduplicatedLines": max(0, len(lines) - len(deduped_lines)),
            "aggressive": bool(options.get("aggressive")),
        },
    }


def build_tap_plan(message: str, device_mapping=None) -> tuple[str, dict]:
    raw_message = str(message or "").strip()
    lowered = raw_message.lower()
    normalized = normalize_device_mapping_to_tuples(device_mapping or [])
    tuples = normalized.get("tuples", []) if normalized.get("ok") else []
    first_entity = tuples[0][0] if tuples else "switch.default"
    first_area = tuples[0][1] if tuples else "living_room"
    first_type = normalize_room_name(tuples[0][2]) if tuples else "switch"

    if any(word in raw_message for word in ("睡", "晚安", "休息")):
        alias = "睡眠模式自动化"
        trigger = '  - platform: time\n    at: "22:30:00"'
        condition = 'condition:\n  - condition: state\n    entity_id: group.family\n    state: home'
        action = 'action:\n  - service: scene.turn_on\n    target:\n      entity_id: scene.sleep_mode'
        summary = {"trigger": "22:30 定时触发", "actions": ["切换到睡眠模式"]}
    elif any(word in raw_message for word in ("热", "闷", "空调", "温度")):
        alias = "高温自动开启空调"
        trigger = '  - platform: numeric_state\n    entity_id: sensor.indoor_temperature\n    above: 30'
        condition = 'condition:\n  - condition: state\n    entity_id: group.family\n    state: home'
        action = f'action:\n  - service: climate.set_temperature\n    target:\n      entity_id: {first_entity}\n    data:\n      temperature: 26'
        summary = {"trigger": "室内温度高于 30°C", "actions": [f"开启 {first_type} 并设定 26°C"]}
    else:
        alias = "自定义设备自动化"
        trigger = '  - platform: state\n    entity_id: input_boolean.homemind_trigger\n    to: "on"'
        condition = f'condition:\n  - condition: template\n    value_template: "{{{{ true }}}}"'
        action = f'action:\n  - service: homeassistant.turn_on\n    target:\n      entity_id: {first_entity}'
        summary = {"trigger": "自定义触发开关打开", "actions": [f"控制 {first_area} 区域设备 {first_entity}"]}

    yaml_code = f"alias: {alias}\ntrigger:\n{trigger}\n{condition}\n{action}\n"
    return yaml_code, summary


def dump_tap_rule_yaml(node, indent: int = 0) -> str:
    prefix = " " * indent
    lines = []
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, list):
                lines.append(f"{prefix}{key}:")
                lines.append(dump_tap_rule_yaml(value, indent + 2))
            elif isinstance(value, dict):
                lines.append(f"{prefix}{key}:")
                lines.append(dump_tap_rule_yaml(value, indent + 2))
            else:
                rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, str) else value
                lines.append(f"{prefix}{key}: {rendered}")
        return "\n".join(lines)
    if isinstance(node, list):
        for item in node:
            if isinstance(item, dict):
                item_lines = list(item.items())
                for idx, (key, value) in enumerate(item_lines):
                    item_prefix = f"{prefix}- " if idx == 0 else f"{prefix}  "
                    if isinstance(value, list):
                        lines.append(f"{item_prefix}{key}:")
                        lines.append(dump_tap_rule_yaml(value, indent + 4))
                    elif isinstance(value, dict):
                        lines.append(f"{item_prefix}{key}:")
                        lines.append(dump_tap_rule_yaml(value, indent + 4))
                    else:
                        rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, str) else value
                        lines.append(f"{item_prefix}{key}: {rendered}")
            else:
                rendered = json.dumps(item, ensure_ascii=False) if isinstance(item, str) else item
                lines.append(f"{prefix}- {rendered}")
        return "\n".join(lines)
    rendered = json.dumps(node, ensure_ascii=False) if isinstance(node, str) else node
    return f"{prefix}{rendered}"


def build_tap_plan(message: str, device_mapping=None) -> tuple[str, dict]:
    raw_message = str(message or "").strip()
    lowered = raw_message.lower()
    normalized = normalize_device_mapping_to_tuples(device_mapping or [])
    tuples = normalized.get("tuples", []) if normalized.get("ok") else []
    first_entity = tuples[0][0] if tuples else "switch.default"
    first_area = tuples[0][1] if tuples else "living_room"
    first_type = normalize_room_name(tuples[0][2]) if tuples else "switch"

    if any(word in raw_message for word in ("睡", "晚安", "休息")):
        rule = {
            "alias": "睡眠模式自动化",
            "description": "夜间到点后切换睡眠场景",
            "trigger": [{"platform": "time", "at": "22:30:00"}],
            "condition": [{"condition": "state", "entity_id": "group.family", "state": "home"}],
            "action": [{"service": "scene.turn_on", "target": {"entity_id": "scene.sleep_mode"}}],
        }
        summary = {"trigger": "22:30 定时触发", "actions": ["切换到睡眠模式"]}
    elif any(word in lowered for word in ("hot", "warm", "temperature")) or any(word in raw_message for word in ("热", "闷", "空调", "温度")):
        rule = {
            "alias": "高温自动开启空调",
            "description": "室内温度过高时自动降温",
            "trigger": [{"platform": "numeric_state", "entity_id": "sensor.indoor_temperature", "above": 30}],
            "condition": [{"condition": "state", "entity_id": "group.family", "state": "home"}],
            "action": [{
                "service": "climate.set_temperature",
                "target": {"entity_id": first_entity},
                "data": {"temperature": 26},
            }],
        }
        summary = {"trigger": "室内温度高于 30°C", "actions": [f"控制 {first_type} 到 26°C"]}
    else:
        rule = {
            "alias": "自定义设备自动化",
            "description": "保底自动化模板",
            "trigger": [{"platform": "state", "entity_id": "input_boolean.homemind_trigger", "to": "on"}],
            "condition": [{"condition": "template", "value_template": "{{ true }}"}],
            "action": [{"service": "homeassistant.turn_on", "target": {"entity_id": first_entity}}],
        }
        summary = {"trigger": "自定义触发开关打开", "actions": [f"控制 {first_area} 区域设备 {first_entity}"]}

    yaml_code = dump_tap_rule_yaml(rule).strip() + "\n"
    return yaml_code, summary


def load_tap_rules():
    with storage_lock:
        return load_json_store(TAP_RULE_STORE_PATH)


def save_tap_rules(rules):
    with storage_lock:
        save_json_store(TAP_RULE_STORE_PATH, rules)


def build_automation_snapshot() -> dict:
    snapshot = {
        "devices": {},
        "context": {
            "scene": "sleep",
            "temperature": 25.0,
            "humidity": 60.0,
            "occupancy": 1,
            "time": datetime.now().strftime("%H:%M:%S"),
        },
    }
    if not agent:
        return snapshot
    states = agent.get_all_states()
    snapshot["devices"] = states.get("devices", {})
    snapshot["context"] = {
        "scene": states.get("context", {}).get("scene", "sleep"),
        "temperature": states.get("context", {}).get("temperature", 25.0),
        "humidity": states.get("context", {}).get("humidity", 60.0),
        "occupancy": states.get("context", {}).get("occupancy", 1),
        "time": datetime.now().strftime("%H:%M:%S"),
    }
    return snapshot


def resolve_tap_target(target_entity_id: str) -> tuple[str | None, str | None]:
    entity = str(target_entity_id or "").strip()
    if not entity:
        return None, None
    if entity.startswith("scene."):
        return "scene", entity.split(".", 1)[1].replace("_mode", "")
    if "." in entity:
        domain, object_id = entity.split(".", 1)
        object_id = object_id.lower()
        domain_map = {
            "light": "light",
            "switch": "light" if "light" in object_id else "tv",
            "climate": "air_conditioner",
            "fan": "fan",
            "window": "window",
            "cover": "window",
            "speaker": "speaker",
            "media_player": "tv",
        }
        resolved = domain_map.get(domain)
        if resolved:
            return "device", resolved
    if entity in getattr(agent, "DEVICE_IDS", []):
        return "device", entity
    return "device", entity


def execute_tap_action(action: dict) -> dict:
    service = str(action.get("service") or "").strip()
    target = action.get("target") or {}
    entity_id = target.get("entity_id") if isinstance(target, dict) else None
    data = action.get("data") if isinstance(action.get("data"), dict) else {}
    kind, resolved = resolve_tap_target(entity_id)

    if service == "notify.notify":
        return {"status": "success", "service": service, "message": str(data.get("message") or "notification queued")}

    if kind == "scene" and resolved and service == "scene.turn_on" and agent:
        scene_name = agent.SCENE_ID_MAP.get(resolved, resolved)
        agent.scene_switcher.execute(scene_name)
        agent.context.current_scene = resolved
        return {"status": "success", "service": service, "scene": resolved}

    if kind == "device" and resolved and agent:
        route_device = resolved
        dev_name = agent._resolve_device(route_device)
        command = "on"
        params = {}
        if service.endswith(".turn_off"):
            command = "off"
        elif service == "climate.set_temperature":
            command = "adjust"
            if "temperature" in data:
                params["temperature"] = data["temperature"]
        elif service.endswith(".turn_on"):
            command = "on"
        result_text = agent.device_control.execute(dev_name, command, params)
        return {
            "status": "success",
            "service": service,
            "device": route_device,
            "result": result_text,
            "state": agent._get_device_state(route_device),
        }

    return {"status": "skipped", "service": service, "reason": "unsupported_target"}


def evaluate_automation_event(event: dict) -> dict:
    rules = load_tap_rules()
    result = tap_rule_engine.evaluate(
        rules,
        event=event,
        snapshot=build_automation_snapshot(),
        executor=execute_tap_action,
    )
    return result.to_dict()


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
        self.context.members_home = 1
        
        # 初始化设备模拟器
        self.device_simulator = DeviceSimulator()
        self.simulator = self.device_simulator
        
        # 初始化工具（传入协议网关）
        self.device_control = device_ctrl.DeviceController(protocol_gateway=self._gateway)
        self.info_query = info_query.InfoQuery()
        self.scene_switcher = scene_switch.SceneSwitcher(self.device_control)
        
        # 启动阶段不强制初始化 embedding，避免首次模型加载阻塞 Web 服务。
        self.embedding_model = None
        self.kb = None
        self.kb_writer = None
        try:
            self.kb = KnowledgeBase()
            self.kb_writer = kb_writer.KBWriter(self.kb)
            print("[初始化] 知识库已加载（embedding 按需初始化）")
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
                candidates = self.bsr.recall(user_text, self.context)
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
                ranked = self.lsr.rank(user_text, candidates, self.context, kb=self.kb)
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
                    self._emit_fallback(query_id, user_text)
                    return

                # Step 3: LLM 决策
                decision = self.llm.decide(user_text, ranked, self.context, rag_context="")
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
                        "reasoning": reasoning
                    }
                }
                socketio.emit("pipeline_update", {"type": "pipeline_step", "data": {
                    "query_id": query_id, "step": "llm", "data": pipeline["steps"]["llm"]
                }})

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
                    socketio.emit("message", {
                        "type": "agent_response",
                        "data": {
                            "action": result["action"],
                            "result": result_text,
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
        
        if not self.bsr or not self.lsr or not self.llm:
            return {"status": "no_action", "message": "AI 模块未加载"}
        
        try:
            candidates = self.bsr.recall(query, self.context)
            ranked = self.lsr.rank(query, candidates, self.context, kb=self.kb)
            
            if ranked:
                decision = self.llm.decide(query, ranked, self.context, rag_context="")
                
                action_type = decision.get("action", "")
                device = decision.get("device", "")
                device_action = decision.get("device_action", "")
                scene = decision.get("scene", "")
                params = decision.get("params", {})
                
                if action_type == "设备控制" and device and device_action:
                    message = self.device_control.execute(device, device_action, params)
                    return {
                        "status": "success",
                        "action": f"{device}_{device_action}",
                        "response": message,
                        "confidence": decision.get("confidence", 0.9)
                    }
                elif action_type == "场景切换" and scene:
                    message = self.scene_switcher.execute(scene)
                    self.context.current_scene = scene
                    return {
                        "status": "success",
                        "action": "scene_switch",
                        "response": message,
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


def _patched_handle_device_control(self, data: dict):
    device_id = data.get("device")
    validation = validate_device_command(device_id, {"action": data.get("action"), "params": data.get("params", {})})
    if not validation.valid:
        socketio.emit("message", {
            "type": "agent_clarification",
            "data": {
                "question": validation.errors[0].message,
                "candidates": [item.message for item in validation.errors],
            }
        })
        return

    dev_name = self._resolve_device(device_id)
    previous_state = "on" if self._get_device_state(device_id).get("is_on") else "off"
    result = self.device_control.execute(dev_name, validation.action, validation.params)
    automation = evaluate_automation_event({
        "platform": "state",
        "entity_id": device_id,
        "from": previous_state,
        "to": "on" if self._get_device_state(device_id).get("is_on") else "off",
    })

    socketio.emit("message", {
        "type": "device_update",
        "data": {
            "device": device_id,
            "state": self._get_device_state(device_id),
            "result": result,
            "automation": automation,
        }
    })


def _patched_handle_scene_switch(self, data: dict):
    scene_id = data.get("scene")
    scene = self.SCENE_ID_MAP.get(scene_id, scene_id)

    if self.bsr:
        self.bsr.recall(f"切换到{scene}场景", self.context)

    self.scene_switcher.execute(scene)
    self.context.current_scene = scene_id
    automation = evaluate_automation_event({"platform": "scene", "scene": scene_id})

    socketio.emit("message", {
        "type": "scene_update",
        "data": {
            "scene": scene_id,
            "devices": self.get_all_states()["devices"],
            "automation": automation,
        }
    })


HomeMindWebAgent._handle_device_control = _patched_handle_device_control
HomeMindWebAgent._handle_scene_switch = _patched_handle_scene_switch


# ==================== REST API ====================


@app.route("/api/floor-plans", methods=["GET"])
def list_floor_plans():
    """List uploaded floor plans."""
    with storage_lock:
        plans = load_json_store(FLOOR_PLAN_STORE_PATH)
    return jsonify({"success": True, "floorPlans": plans})


@app.route("/api/floor-plans/<plan_id>", methods=["GET"])
def get_floor_plan(plan_id):
    """Get floor plan metadata."""
    with storage_lock:
        plans = load_json_store(FLOOR_PLAN_STORE_PATH)
    plan = next((item for item in plans if item.get("id") == plan_id), None)
    if not plan:
        return jsonify({"error": "Floor plan not found"}), 404
    return jsonify({"success": True, "floorPlan": plan})


@app.route("/api/floor-plans", methods=["POST"])
def upload_floor_plan():
    """Upload an SVG floor plan and store its metadata."""
    uploaded = request.files.get("floorPlan")
    if not uploaded:
        return jsonify({"error": "No file uploaded"}), 400

    original_name = uploaded.filename or "floor-plan.svg"
    if not original_name.lower().endswith(".svg"):
        return jsonify({"error": "Only SVG format is supported for floor plans"}), 400

    mimetype = (uploaded.mimetype or "").lower()
    if mimetype and mimetype not in {"image/svg+xml", "text/xml", "application/xml"}:
        return jsonify({"error": "Invalid SVG MIME type"}), 400

    svg_bytes = uploaded.read()
    try:
        svg_content = svg_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return jsonify({"error": "SVG file must be UTF-8 encoded"}), 400

    if "<svg" not in svg_content.lower():
        return jsonify({"error": "Uploaded file is not valid SVG content"}), 400

    ensure_spatial_storage()
    file_id = f"floor-plan-{int(time.time() * 1000)}-{os.urandom(4).hex()}.svg"
    target = FLOOR_PLAN_UPLOAD_DIR / file_id
    target.write_text(svg_content, encoding="utf-8")

    width, height = extract_svg_dimensions(svg_content)
    entry = {
        "id": file_id,
        "name": request.form.get("name") or Path(original_name).stem,
        "description": request.form.get("description", ""),
        "filePath": str(target),
        "url": f"/uploads/floor-plans/{file_id}",
        "width": width,
        "height": height,
        "uploadedAt": datetime.utcnow().isoformat() + "Z",
    }

    with storage_lock:
        plans = load_json_store(FLOOR_PLAN_STORE_PATH)
        plans.append(entry)
        save_json_store(FLOOR_PLAN_STORE_PATH, plans)

    return jsonify({"success": True, "floorPlan": entry})


@app.route("/api/floor-plans/<plan_id>", methods=["PUT"])
def update_floor_plan(plan_id):
    """Update floor plan metadata."""
    payload = request.get_json(silent=True) or {}
    with storage_lock:
        plans = load_json_store(FLOOR_PLAN_STORE_PATH)
        index = next((idx for idx, item in enumerate(plans) if item.get("id") == plan_id), -1)
        if index < 0:
            return jsonify({"error": "Floor plan not found"}), 404
        updated = {
            **plans[index],
            "name": payload.get("name", plans[index].get("name", "")),
            "description": payload.get("description", plans[index].get("description", "")),
            "updatedAt": datetime.utcnow().isoformat() + "Z",
        }
        plans[index] = updated
        save_json_store(FLOOR_PLAN_STORE_PATH, plans)
    return jsonify({"success": True, "floorPlan": updated})


@app.route("/api/floor-plans/<plan_id>", methods=["DELETE"])
def delete_floor_plan(plan_id):
    """Delete a floor plan and its associated device mapping."""
    with storage_lock:
        plans = load_json_store(FLOOR_PLAN_STORE_PATH)
        index = next((idx for idx, item in enumerate(plans) if item.get("id") == plan_id), -1)
        if index < 0:
            return jsonify({"error": "Floor plan not found"}), 404

        removed = plans.pop(index)
        save_json_store(FLOOR_PLAN_STORE_PATH, plans)

        mappings = load_json_store(DEVICE_MAPPING_STORE_PATH)
        mappings = [item for item in mappings if item.get("floorPlanId") != plan_id]
        save_json_store(DEVICE_MAPPING_STORE_PATH, mappings)

    try:
        file_path = Path(removed.get("filePath", ""))
        if file_path.exists():
            file_path.unlink()
    except OSError:
        pass

    return jsonify({"success": True, "message": "Floor plan deleted"})


@app.route("/api/floor-plans/<plan_id>/svg", methods=["GET"])
def get_floor_plan_svg(plan_id):
    """Return raw SVG content for the requested floor plan."""
    with storage_lock:
        plans = load_json_store(FLOOR_PLAN_STORE_PATH)
    plan = next((item for item in plans if item.get("id") == plan_id), None)
    if not plan:
        return jsonify({"error": "Floor plan not found"}), 404
    file_path = Path(plan.get("filePath", ""))
    if not file_path.exists():
        external_candidate = EXTERNAL_FLOOR_PLAN_UPLOAD_DIR / plan_id
        if external_candidate.exists():
            file_path = external_candidate
    if not file_path.exists():
        return jsonify({"error": "SVG file not found"}), 404
    return Response(
        file_path.read_bytes(),
        mimetype="image/svg+xml",
        headers={"Content-Disposition": f'inline; filename="{file_path.name}"'},
    )


@app.route("/uploads/floor-plans/<path:filename>", methods=["GET"])
def serve_floor_plan_upload(filename):
    """Serve uploaded SVG assets."""
    local_file = FLOOR_PLAN_UPLOAD_DIR / filename
    if local_file.exists():
        return send_from_directory(FLOOR_PLAN_UPLOAD_DIR, filename)
    if EXTERNAL_FLOOR_PLAN_UPLOAD_DIR.joinpath(filename).exists():
        return send_from_directory(EXTERNAL_FLOOR_PLAN_UPLOAD_DIR, filename)
    return jsonify({"error": "SVG file not found"}), 404


@app.route("/api/devices", methods=["GET"])
def list_device_mappings():
    """List all floor-plan device mappings."""
    with storage_lock:
        mappings = load_json_store(DEVICE_MAPPING_STORE_PATH)
    return jsonify({"success": True, "deviceMappings": mappings})


@app.route("/api/devices/<floor_plan_id>", methods=["GET"])
def get_device_mapping(floor_plan_id):
    """Get a device mapping for a floor plan."""
    with storage_lock:
        mappings = load_json_store(DEVICE_MAPPING_STORE_PATH)
    mapping = next((item for item in mappings if item.get("floorPlanId") == floor_plan_id), None)
    if not mapping:
        return jsonify({"success": True, "deviceMapping": None, "devices": []})
    return jsonify({"success": True, "deviceMapping": mapping, "devices": mapping.get("devices", [])})


@app.route("/api/devices", methods=["POST"])
def save_device_mapping():
    """Create or update a device mapping for a floor plan."""
    payload = request.get_json(silent=True) or {}
    floor_plan_id = payload.get("floorPlanId")
    raw_input = payload.get("devices", payload.get("deviceMapping"))
    custom_rooms = payload.get("customRooms")

    if not floor_plan_id:
        return jsonify({"error": "floorPlanId is required"}), 400

    normalized = validate_device_mapping_payload(raw_input, supported_device_types=SUPPORTED_DEVICE_TYPES)
    normalized_payload = normalized.to_legacy_response()
    if not normalized_payload.get("ok"):
        return jsonify({
            "error": normalized_payload.get("error") or "Invalid device mapping",
            "hint": 'Use [["entity_id","area","type"],...] or { "devices": [ { "entity_id":"...", "area":"...", "device_type":"light" } ] }',
            "errors": normalized_payload.get("errors", []),
            "warnings": normalized_payload.get("warnings", []),
        }), 400

    tuples = normalized_payload.get("tuples", [])

    with storage_lock:
        plans = load_json_store(FLOOR_PLAN_STORE_PATH)
        if not any(item.get("id") == floor_plan_id for item in plans):
            return jsonify({"error": "Floor plan not found. Please upload it first."}), 400

        devices = compute_device_positions(tuples, custom_rooms=custom_rooms)
        mappings = load_json_store(DEVICE_MAPPING_STORE_PATH)
        entry = {
            "floorPlanId": floor_plan_id,
            "rawDevices": tuples,
            "devices": devices,
            "customRooms": normalize_custom_rooms(custom_rooms),
            "updatedAt": datetime.utcnow().isoformat() + "Z",
        }

        index = next((idx for idx, item in enumerate(mappings) if item.get("floorPlanId") == floor_plan_id), -1)
        if index >= 0:
            entry["createdAt"] = mappings[index].get("createdAt")
            mappings[index] = entry
        else:
            entry["createdAt"] = datetime.utcnow().isoformat() + "Z"
            mappings.append(entry)
        save_json_store(DEVICE_MAPPING_STORE_PATH, mappings)

    return jsonify({
        "success": True,
        "deviceMapping": entry,
        "deviceCount": len(devices),
        "validation": {
            "valid": True,
            "warnings": normalized_payload.get("warnings", []),
        },
    })


@app.route("/api/devices/<floor_plan_id>", methods=["DELETE"])
def delete_device_mapping(floor_plan_id):
    """Delete a device mapping for a floor plan."""
    with storage_lock:
        mappings = load_json_store(DEVICE_MAPPING_STORE_PATH)
        index = next((idx for idx, item in enumerate(mappings) if item.get("floorPlanId") == floor_plan_id), -1)
        if index < 0:
            return jsonify({"error": "Device mapping not found"}), 404
        mappings.pop(index)
        save_json_store(DEVICE_MAPPING_STORE_PATH, mappings)
    return jsonify({"success": True, "message": "Device mapping deleted"})


@app.route("/api/tap-rules", methods=["GET"])
def list_tap_rules():
    """List persisted TAP rules."""
    return jsonify({"success": True, "rules": load_tap_rules()})


@app.route("/api/tap-rules", methods=["POST"])
def create_tap_rule():
    """Create a TAP rule from structured JSON or TAP YAML."""
    payload = request.get_json(silent=True) or {}
    tap_code = str(payload.get("tapCode") or payload.get("code") or "").strip()
    if tap_code:
        validation = validate_tap_code(tap_code, auto_fix=bool(payload.get("autoFix", True)))
        if not validation["validation"]["valid"]:
            return jsonify(validation), 400
        parsed_rule = validation.get("parsedRule") or {}
    else:
        validation_result = validate_tap_rule_payload(payload)
        if not validation_result.valid:
            return jsonify(validation_result.to_api_response()), 400
        validation = validation_result.to_api_response()
        parsed_rule = validation_result.parsed or {}

    stored_rule = {
        "id": payload.get("id") or f"tap-rule-{int(time.time() * 1000)}",
        "alias": parsed_rule.get("alias"),
        "description": payload.get("description", parsed_rule.get("description", "")),
        "enabled": bool(payload.get("enabled", parsed_rule.get("enabled", True))),
        "trigger": parsed_rule.get("trigger", []),
        "condition": parsed_rule.get("condition", []),
        "action": parsed_rule.get("action", []),
        "tapCode": dump_tap_rule_yaml({
            "alias": parsed_rule.get("alias"),
            "description": parsed_rule.get("description", ""),
            "trigger": parsed_rule.get("trigger", []),
            "condition": parsed_rule.get("condition", []),
            "action": parsed_rule.get("action", []),
        }).strip()
        + "\n",
        "createdAt": datetime.utcnow().isoformat() + "Z",
        "updatedAt": datetime.utcnow().isoformat() + "Z",
    }

    rules = load_tap_rules()
    rules.append(stored_rule)
    save_tap_rules(rules)
    return jsonify({"success": True, "rule": stored_rule, "validation": validation.get("validation")})


@app.route("/api/tap-rules/<rule_id>", methods=["PUT"])
def update_tap_rule(rule_id):
    """Update a TAP rule by id."""
    payload = request.get_json(silent=True) or {}
    rules = load_tap_rules()
    index = next((idx for idx, item in enumerate(rules) if item.get("id") == rule_id), -1)
    if index < 0:
        return jsonify({"error": "Rule not found"}), 404

    merged = {**rules[index], **payload, "id": rule_id}
    validation_result = validate_tap_rule_payload(merged)
    if not validation_result.valid:
        return jsonify(validation_result.to_api_response()), 400

    parsed_rule = validation_result.parsed or {}
    updated = {
        **rules[index],
        "alias": parsed_rule.get("alias"),
        "description": parsed_rule.get("description", ""),
        "enabled": bool(parsed_rule.get("enabled", True)),
        "trigger": parsed_rule.get("trigger", []),
        "condition": parsed_rule.get("condition", []),
        "action": parsed_rule.get("action", []),
        "tapCode": dump_tap_rule_yaml({
            "alias": parsed_rule.get("alias"),
            "description": parsed_rule.get("description", ""),
            "trigger": parsed_rule.get("trigger", []),
            "condition": parsed_rule.get("condition", []),
            "action": parsed_rule.get("action", []),
        }).strip()
        + "\n",
        "updatedAt": datetime.utcnow().isoformat() + "Z",
    }
    rules[index] = updated
    save_tap_rules(rules)
    return jsonify({"success": True, "rule": updated, "validation": validation_result.to_api_response()["validation"]})


@app.route("/api/tap-rules/<rule_id>", methods=["DELETE"])
def delete_tap_rule(rule_id):
    """Delete a TAP rule."""
    rules = load_tap_rules()
    index = next((idx for idx, item in enumerate(rules) if item.get("id") == rule_id), -1)
    if index < 0:
        return jsonify({"error": "Rule not found"}), 404
    removed = rules.pop(index)
    save_tap_rules(rules)
    return jsonify({"success": True, "message": "Rule deleted", "rule": removed})


@app.route("/api/tap-rules/<rule_id>/toggle", methods=["POST"])
def toggle_tap_rule(rule_id):
    """Enable or disable a TAP rule."""
    payload = request.get_json(silent=True) or {}
    rules = load_tap_rules()
    index = next((idx for idx, item in enumerate(rules) if item.get("id") == rule_id), -1)
    if index < 0:
        return jsonify({"error": "Rule not found"}), 404
    rules[index]["enabled"] = bool(payload.get("enabled", not rules[index].get("enabled", True)))
    rules[index]["updatedAt"] = datetime.utcnow().isoformat() + "Z"
    save_tap_rules(rules)
    return jsonify({"success": True, "rule": rules[index]})


@app.route("/api/tap-rules/evaluate", methods=["POST"])
def evaluate_tap_rules():
    """Evaluate TAP rules against a provided event."""
    payload = request.get_json(silent=True) or {}
    event = payload.get("event") or {}
    if not isinstance(event, dict) or not event.get("platform"):
        return jsonify({"error": "event.platform is required"}), 400
    result = tap_rule_engine.evaluate(
        load_tap_rules(),
        event=event,
        snapshot=payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else build_automation_snapshot(),
        executor=execute_tap_action,
    )
    return jsonify({"success": True, "evaluation": result.to_dict()})


@app.route("/api/check-code", methods=["POST"])
def check_code():
    """Validate TAP YAML structure."""
    payload = request.get_json(silent=True) or {}
    code = payload.get("code", "")
    auto_fix = bool(payload.get("autoFix", True))
    if not code:
        return jsonify({"error": "code is required"}), 400
    return jsonify(validate_tap_code(code, auto_fix=auto_fix))


@app.route("/api/compress-context", methods=["POST"])
@app.route("/api/compress-code", methods=["POST"])
def compress_context():
    """Compress context-like text payloads to reduce token cost."""
    payload = request.get_json(silent=True) or {}
    text = payload.get("text", payload.get("code", ""))
    if not text:
        return jsonify({"error": "text or code is required"}), 400
    result = compress_text_content(text, options=payload.get("options"))
    return jsonify({
        "success": True,
        "compressedText": result["compressed"],
        "compressedCode": result["compressed"],
        "report": result["report"],
    })


@app.route("/api/generate-tap", methods=["POST"])
def generate_tap():
    """Optional SSE endpoint for lightweight TAP generation."""
    payload = request.get_json(silent=True) or {}
    message = payload.get("message", "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    device_mapping = payload.get("deviceMapping") or {}
    compressed_context = compress_text_content(json.dumps(device_mapping, ensure_ascii=False), options={"aggressive": True})
    tap_yaml, summary = build_tap_plan(message, device_mapping=device_mapping)
    validation = validate_tap_code(tap_yaml, auto_fix=False)

    def event_stream():
        yield send_sse({"type": "step", "steps": [{"icon": "⚙", "text": "Collecting context..."}]})
        time.sleep(0.01)
        yield send_sse({"type": "step", "steps": [{"icon": "✂", "text": f"Compressed context to {compressed_context['report']['compressionRate']}"}]})
        time.sleep(0.01)
        yield send_sse({"type": "step", "steps": [{"icon": "🧩", "text": "Building TAP skeleton..."}]})

        cumulative = ""
        for line in tap_yaml.splitlines(True):
            cumulative += line
            yield send_sse({"type": "delta", "content": cumulative})
            time.sleep(0.005)

        yield send_sse({
            "type": "complete",
            "data": {
                "response": "TAP rule generated",
                "tapCode": tap_yaml,
                "intent": message,
                "plan": {
                    "trigger": summary["trigger"],
                    "actions": summary["actions"],
                },
                "processingSteps": [
                    {"icon": "⚙", "text": "Context collected"},
                    {"icon": "✂", "text": "Context compressed"},
                    {"icon": "✓", "text": "TAP validated" if validation["validation"]["valid"] else "TAP generated with validation warnings"},
                ],
                "stats": {
                    "tokens": max(1, math.ceil(len(tap_yaml) / 4)),
                    "price": 0,
                    "provider": "HomeMind Edge",
                    "model": "rule-based",
                },
                "validation": validation["validation"],
                "rag": False,
            },
        })
        yield send_sse("[DONE]")

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return Response(event_stream(), mimetype="text/event-stream", headers=headers)


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
        agent.scene_switcher.execute(scene_name)
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
            scene_map = {
                0: "sleep",
                1: "entertainment", 
                2: "away",
                3: "work",
                4: "morning",
                5: "evening"
            }
            recommended_scene = scene_map.get(action_idx, "sleep")
            
            return jsonify({
                "status": "success",
                "recommendation": {
                    "id": f"dqn_{action_idx}",
                    "scene": recommended_scene,
                    "reason": f"基于当前环境状态推荐{recommended_scene}场景",
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
    """语音转文字接口（预留，未来支持 faster-whisper）。"""
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
    globals()["protocol_gateway"] = protocol_gateway
    ensure_spatial_storage()

    # 从环境变量读取模式（如果未指定）
    if mode is None:
        mode = os.environ.get("HOMEMIND_MODE", "simulated")


    print(f"[初始化] Agent 模式: {mode}")

    agent = HomeMindWebAgent(protocol_gateway=protocol_gateway)
    device_simulator = agent.device_simulator


@app.route("/")
def index():
    """首页"""
    return send_from_directory(app.static_folder, "index.html")
    if False:
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

def _patched_control_device_route(device):
    data = request.get_json() or {}
    validation = validate_device_command(device, data)

    if agent:
        if not validation.valid:
            return jsonify({
                "error": validation.errors[0].message,
                "validation": {
                    "valid": False,
                    "errors": [item.to_dict() for item in validation.errors],
                    "warnings": [item.to_dict() for item in validation.warnings],
                },
            }), 400
        dev_name = agent._resolve_device(device)
        previous_state = "on" if agent._get_device_state(device).get("is_on") else "off"
        result = agent.device_control.execute(dev_name, validation.action, validation.params)
        automation = evaluate_automation_event({
            "platform": "state",
            "entity_id": device,
            "from": previous_state,
            "to": "on" if agent._get_device_state(device).get("is_on") else "off",
        })
        return jsonify({
            "status": "success",
            "device": device,
            "result": result,
            "state": agent._get_device_state(device),
            "automation": automation,
        })

    return jsonify({"error": "Agent 未初始化"}), 500


def _patched_switch_scene_route(scene):
    if agent:
        scene_name = agent.SCENE_ID_MAP.get(scene, scene)
        agent.scene_switcher.execute(scene_name)
        agent.context.current_scene = scene
        automation = evaluate_automation_event({"platform": "scene", "scene": scene})
        return jsonify({
            "status": "success",
            "scene": scene,
            "devices": agent.get_all_states()["devices"],
            "automation": automation,
        })

    return jsonify({"error": "Agent 未初始化"}), 500


app.view_functions["control_device"] = _patched_control_device_route
app.view_functions["switch_scene"] = _patched_switch_scene_route


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
