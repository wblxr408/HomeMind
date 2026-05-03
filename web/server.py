"""
HomeMind Web 服务 - 中央指令器
提供 REST API 和 WebSocket 接口，连接智能家居 Agent 与前端控制面板
"""
import inspect
import json
import re
import threading
import time
from copy import deepcopy
from datetime import datetime
import os
import sys
import types
from pathlib import Path
from xml.etree import ElementTree

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

try:
    import asyncio as _asyncio_probe
    getattr(_asyncio_probe, "iscoroutinefunction")
    ASYNCIO_AVAILABLE = True
except Exception:
    asyncio_stub = types.ModuleType("asyncio")
    asyncio_stub.iscoroutinefunction = inspect.iscoroutinefunction
    sys.modules["asyncio"] = asyncio_stub
    ASYNCIO_AVAILABLE = False

from flask import Flask, request, jsonify, send_from_directory
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
from core.execution.transaction_manager import ExecutionTransactionManager
from core.governance import AuditLogger, PolicyEngine
from core.lsr.precision_ranking import LSRecify as PrecisionRanking
from core.llm.decision import LLMDecider as LLMWrapper
from core.dqn.policy import DQNPolicy
from core.rag.knowledge_base import KnowledgeBase
from core.observability import get_metrics
from core.sec import InjectionDetector
from core.sec.autonomy_manager import AutonomyManager
from core.sec.runtime_security import RuntimeSecurityChain
from core.tools import ToolRegistry
from core.utils.embedding import get_model as get_embedding_model
from core.language.normalizer import LanguageNormalizer
from core.memory import PreferenceStore, SessionStore
from core.privacy import PrivacyRedactor
from core.router import InferenceRouter
from core.voice.vosk_asr import VoskASR
from core.voice.feedback_store import VoiceFeedbackStore
from core.constants import SCENE_INDEX_MAP, SCENE_NAMES
from core.config import SECURITY_CONFIG
from core.security import get_encrypted_storage
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
agent_init_lock = threading.Lock()
agent_init_metrics = {
    "call_count": 0,
    "completed_count": 0,
    "last_reason": "",
    "last_mode": "",
    "last_started_at": "",
    "last_duration_ms": 0.0,
    "last_instance_id": "",
    "phases_ms": {},
}
voice_asr = VoskASR()
voice_feedback_store = VoiceFeedbackStore()
language_normalizer = LanguageNormalizer(feedback_store=voice_feedback_store)

UPLOAD_ROOT = Path(os.environ.get("HOMEMIND_UPLOAD_DIR", "uploads"))
FLOOR_PLAN_UPLOAD_DIR = UPLOAD_ROOT / "floor-plans"
FLOOR_PLAN_STORE_PATH = Path(os.environ.get("HOMEMIND_FLOOR_PLAN_STORE", "data/floor-plans.json"))
FLOOR_PLAN_DEVICE_STORE_PATH = Path(os.environ.get("HOMEMIND_FLOOR_PLAN_DEVICE_STORE", "data/devices.json"))
DEVICE_REGISTRY_PATH = Path(os.environ.get("HOMEMIND_DEVICE_REGISTRY_STORE", "data/device-registry.json"))
MAX_SVG_UPLOAD_BYTES = int(os.environ.get("HOMEMIND_MAX_SVG_UPLOAD_BYTES", str(2 * 1024 * 1024)))
DEFAULT_SVG_WIDTH = 640.0
DEFAULT_SVG_HEIGHT = 660.0
DEFAULT_DEVICE_REGISTRY = [
    {"id": "air_conditioner", "name": "空调", "type": "climate"},
    {"id": "light", "name": "灯光", "type": "light"},
    {"id": "tv", "name": "电视", "type": "media_player"},
    {"id": "water_heater", "name": "热水器", "type": "water_heater"},
    {"id": "fan", "name": "风扇", "type": "fan"},
    {"id": "speaker", "name": "音响", "type": "speaker"},
    {"id": "window", "name": "窗户", "type": "cover"},
]
IMMUTABLE_DEVICE_UPDATE_FIELDS = {
    "state",
    "status",
    "is_on",
    "isOn",
    "runtime",
    "current_state",
    "currentState",
    "last_state",
    "lastState",
    "createdAt",
    "updatedAt",
}

DEFAULT_ROOM_AREAS = {
    "living_room": {"coordinates": [{"x": 23, "y": 108}, {"x": 195, "y": 298}]},
    "bedroom": {"coordinates": [{"x": 284, "y": 132}, {"x": 406, "y": 296}]},
    "bedroom2": {"coordinates": [{"x": 422, "y": 104}, {"x": 616, "y": 304}]},
    "bathroom1": {"coordinates": [{"x": 344, "y": 431}, {"x": 406, "y": 544}]},
    "bathroom2": {"coordinates": [{"x": 495, "y": 335}, {"x": 591, "y": 424}]},
    "dining_room": {"coordinates": [{"x": 118, "y": 368}, {"x": 232, "y": 513}]},
}
DEFAULT_AREA_ALIASES = {
    "living_room": ["\u5ba2\u5385", "\u8d77\u5c45\u5ba4", "living room", "living_room"],
    "bedroom": ["\u5367\u5ba4", "\u4e3b\u5367", "bedroom"],
    "bedroom2": ["\u6b21\u5367", "\u5367\u5ba42", "bedroom2"],
    "kitchen": ["\u53a8\u623f", "kitchen"],
    "bathroom1": ["\u536b\u751f\u95f4", "\u4e3b\u536b", "bathroom1"],
    "bathroom2": ["\u6b21\u536b", "\u536b\u751f\u95f42", "bathroom2"],
    "dining_room": ["\u9910\u5385", "dining room", "dining_room"],
    "study": ["\u4e66\u623f", "study"],
    "balcony": ["\u9633\u53f0", "balcony"],
    "entrance": ["\u7384\u5173", "\u5165\u6237", "entrance"],
}
SEMANTIC_DEVICE_MATCHES = {
    "\u706f\u5149": {
        "types": {"light"},
        "tokens": {"light", "lamp", "\u706f", "\u706f\u5149"},
    },
    "\u7a7a\u8c03": {
        "types": {"climate", "air_conditioner", "hvac"},
        "tokens": {"climate", "air_conditioner", "air conditioner", "ac", "\u7a7a\u8c03"},
    },
    "\u7535\u89c6": {
        "types": {"tv", "television", "media_player"},
        "tokens": {"tv", "television", "\u7535\u89c6"},
    },
    "\u70ed\u6c34\u5668": {
        "types": {"water_heater"},
        "tokens": {"water_heater", "water heater", "\u70ed\u6c34\u5668"},
    },
    "\u98ce\u6247": {
        "types": {"fan"},
        "tokens": {"fan", "\u98ce\u6247"},
    },
    "\u97f3\u54cd": {
        "types": {"speaker", "audio", "media_player"},
        "tokens": {"speaker", "audio", "sound", "\u97f3\u54cd", "\u5587\u53ed"},
    },
    "\u7a97\u6237": {
        "types": {"cover", "window", "curtain"},
        "tokens": {"window", "curtain", "cover", "\u7a97", "\u7a97\u6237", "\u7a97\u5e18"},
    },
}


def _safe_svg_filename(filename: str) -> str:
    stem = Path(filename or "floor-plan.svg").stem.strip() or "floor-plan"
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", stem).strip(".-") or "floor-plan"
    return f"{stem[:80]}.svg"


def _validate_svg_upload(filename: str, data: bytes) -> tuple[bool, str]:
    if not filename.lower().endswith(".svg"):
        return False, "only .svg files are supported"
    if not data:
        return False, "svg file is empty"
    if len(data) > MAX_SVG_UPLOAD_BYTES:
        return False, "svg file is too large"
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return False, "invalid svg xml"

    root_name = root.tag.rsplit("}", 1)[-1].lower()
    if root_name != "svg":
        return False, "root element must be svg"

    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag in {"script", "foreignobject"}:
            return False, "unsafe svg content is not allowed"
        for attr, value in node.attrib.items():
            attr_name = attr.rsplit("}", 1)[-1].lower()
            attr_value = str(value or "").strip().lower()
            if attr_name.startswith("on") or attr_value.startswith("javascript:"):
                return False, "unsafe svg attributes are not allowed"
    return True, ""


def _read_json_list(path: Path) -> list:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_json_list(path: Path, data: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def _safe_device_id(raw_id: str, fallback_name: str = "") -> str:
    raw_id = str(raw_id or "").strip()
    candidate = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_id).strip("._-")
    if candidate:
        return candidate[:80]
    fallback = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(fallback_name or "")).strip("._-")
    if fallback:
        return fallback[:80]
    return f"device_{int(time.time() * 1000)}"


def _spatial_device_name(name: str, area_name: str = "") -> str:
    name = str(name or "").strip()
    area_name = str(area_name or "").strip()
    generic_names = {
        "\u706f",
        "\u706f\u5149",
        "\u7a7a\u8c03",
        "\u7535\u89c6",
        "\u97f3\u54cd",
        "\u98ce\u6247",
        "\u7a97\u6237",
        "\u7a97\u5e18",
        "\u70ed\u6c34\u5668",
        "light",
        "ac",
        "tv",
        "speaker",
        "fan",
        "window",
    }
    if area_name and name and name.lower() in generic_names and not name.startswith(area_name):
        return f"{area_name}{name}"
    return name


def _default_device_state(device_type: str = "") -> dict:
    state = {"status": "关"}
    if device_type == "climate":
        state.update({"temperature": 26, "mode": "制冷"})
    elif device_type == "light":
        state["brightness"] = 100
    elif device_type in {"media_player", "speaker"}:
        state["volume"] = 30
    elif device_type == "fan":
        state["speed"] = 2
    elif device_type == "water_heater":
        state["temperature"] = 45
    return state


def _normalize_device_registry_item(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or "").strip()
    device_id = _safe_device_id(item.get("id") or item.get("deviceId"), name)
    if not name:
        name = device_id
    device_type = str(item.get("type") or "switch").strip() or "switch"
    area = str(item.get("area") or item.get("room") or "").strip()
    area_name = str(item.get("areaName") or item.get("roomName") or "").strip()
    protocol = str(item.get("protocol") or "simulated").strip() or "simulated"
    normalized = {"id": device_id, "name": name, "type": device_type, "protocol": protocol}
    if area:
        normalized["area"] = area
    if area_name:
        normalized["areaName"] = area_name
    return normalized


def _load_device_registry() -> list:
    raw_items = _read_json_list(DEVICE_REGISTRY_PATH) if DEVICE_REGISTRY_PATH.exists() else DEFAULT_DEVICE_REGISTRY
    registry = []
    seen = set()
    for raw in raw_items:
        item = _normalize_device_registry_item(raw)
        if not item or item["id"] in seen:
            continue
        registry.append(item)
        seen.add(item["id"])
    if registry:
        return registry
    return [dict(item) for item in DEFAULT_DEVICE_REGISTRY]


def _save_device_registry(registry: list) -> None:
    _write_json_list(DEVICE_REGISTRY_PATH, registry)


def _parse_svg_dimensions(data: bytes) -> tuple[float, float]:
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return DEFAULT_SVG_WIDTH, DEFAULT_SVG_HEIGHT

    view_box = root.attrib.get("viewBox") or root.attrib.get("viewbox") or ""
    parts = [part for part in re.split(r"[\s,]+", view_box.strip()) if part]
    if len(parts) >= 4:
        try:
            return float(parts[2]) or DEFAULT_SVG_WIDTH, float(parts[3]) or DEFAULT_SVG_HEIGHT
        except ValueError:
            pass

    def numeric_attr(name: str, fallback: float) -> float:
        value = str(root.attrib.get(name, "")).strip()
        match = re.match(r"^([0-9]+(?:\.[0-9]+)?)", value)
        return float(match.group(1)) if match else fallback

    return numeric_attr("width", DEFAULT_SVG_WIDTH), numeric_attr("height", DEFAULT_SVG_HEIGHT)


def _floor_plan_id_exists(plan_id: str) -> bool:
    return any(plan.get("id") == plan_id for plan in _read_json_list(FLOOR_PLAN_STORE_PATH))


def _find_floor_plan(plan_id: str) -> dict | None:
    for plan in _read_json_list(FLOOR_PLAN_STORE_PATH):
        if plan.get("id") == plan_id:
            return plan
    return None


def _set_active_floor_plan(plan_id: str) -> dict | None:
    plans = _read_json_list(FLOOR_PLAN_STORE_PATH)
    updated_plan = None
    for index, plan in enumerate(plans):
        updated = dict(plan)
        updated["active"] = plan.get("id") == plan_id
        if updated["active"]:
            updated["updatedAt"] = datetime.now().astimezone().isoformat()
            updated_plan = updated
        plans[index] = updated
    if updated_plan:
        _write_json_list(FLOOR_PLAN_STORE_PATH, plans)
    return updated_plan


def _normalize_area_name(raw: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(raw or "").strip().lower())


def _extract_device_mapping_item(item) -> dict | None:
    if isinstance(item, list) and len(item) >= 2:
        entity_id = str(item[0] or "").strip()
        area = str(item[1] or "").strip()
        device_type = str(item[2] if len(item) >= 3 and item[2] is not None else "light").strip() or "light"
        device_name = str(item[3] if len(item) >= 4 and item[3] is not None else "").strip()
        area_name = str(item[4] if len(item) >= 5 and item[4] is not None else "").strip()
    elif isinstance(item, dict):
        entity_id = str(item.get("entity_id") or item.get("entityId") or item.get("id") or item.get("entity") or "").strip()
        area = str(item.get("area") or item.get("room") or item.get("zone") or "").strip()
        device_type = str(item.get("device_type") or item.get("deviceType") or item.get("type") or "light").strip() or "light"
        device_name = str(item.get("name") or item.get("displayName") or item.get("deviceName") or "").strip()
        area_name = str(
            item.get("areaName")
            or item.get("area_name")
            or item.get("roomName")
            or item.get("room_name")
            or item.get("displayArea")
            or item.get("display_area")
            or ""
        ).strip()
    else:
        return None

    if not entity_id or not area:
        return None
    if not device_name:
        device_name = entity_id.split(".", 1)[-1].replace("_", " ")
    return {
        "entity_id": entity_id,
        "area": area,
        "device_type": device_type,
        "name": device_name,
        "area_name": area_name,
    }


def _area_names_from_payload(data: dict, device_items: list) -> dict:
    area_names = {}
    nested = data.get("devices") if isinstance(data.get("devices"), dict) else {}
    raw_area_names = data.get("areaNames") or data.get("roomNames") or nested.get("areaNames") or nested.get("roomNames") or {}
    if isinstance(raw_area_names, dict):
        for area, name in raw_area_names.items():
            area = str(area or "").strip()
            name = str(name or "").strip()
            if area and name:
                area_names[area] = name
    custom_rooms = data.get("customRooms") if isinstance(data.get("customRooms"), dict) else nested.get("customRooms", {})
    for area, room in custom_rooms.items():
        if not isinstance(room, dict):
            continue
        name = str(room.get("name") or room.get("displayName") or room.get("areaName") or "").strip()
        if str(area or "").strip() and name:
            area_names[str(area).strip()] = name
    for item in device_items:
        area = item.get("area", "")
        area_name = item.get("area_name", "")
        if area and area_name:
            area_names[area] = area_name
    return area_names


def _normalize_device_mapping_to_tuples(raw_input) -> tuple[bool, str, list]:
    if isinstance(raw_input, str):
        try:
            raw_input = json.loads(raw_input)
        except json.JSONDecodeError:
            return False, "invalid device mapping json", []

    if isinstance(raw_input, dict):
        items = raw_input.get("devices") or raw_input.get("deviceMapping") or raw_input.get("mappings")
        if items is None:
            return False, 'expected "devices", "deviceMapping", or "mappings"', []
    elif isinstance(raw_input, list):
        items = raw_input
    else:
        return False, "invalid root type for device mapping", []

    tuples = []
    for item in items:
        normalized = _extract_device_mapping_item(item)
        if normalized:
            tuples.append(normalized)

    if not tuples and items:
        return False, "no valid devices found", []
    return True, "", tuples


def _compute_device_positions(device_mapping: list, plan: dict, custom_rooms: dict | None = None) -> list:
    rooms = custom_rooms or DEFAULT_ROOM_AREAS
    width = float(plan.get("width") or DEFAULT_SVG_WIDTH)
    height = float(plan.get("height") or DEFAULT_SVG_HEIGHT)
    grouped = {}
    for item in device_mapping:
        normalized = _extract_device_mapping_item(item)
        if not normalized:
            continue
        area = normalized["area"]
        grouped.setdefault(area, []).append(normalized)

    devices = []
    for area, room_devices in grouped.items():
        room_area = rooms.get(area) if isinstance(rooms, dict) else None
        coordinates = room_area.get("coordinates") if isinstance(room_area, dict) else None
        if not coordinates or len(coordinates) < 2:
            continue
        top_left, bottom_right = coordinates[0], coordinates[1]
        room_x = float(top_left.get("x", 0))
        room_y = float(top_left.get("y", 0))
        room_width = float(bottom_right.get("x", room_x) - room_x)
        room_height = float(bottom_right.get("y", room_y) - room_y)
        if room_width <= 0 or room_height <= 0:
            continue

        cols = max(1, int(len(room_devices) ** 0.5 + 0.999))
        rows = max(1, int((len(room_devices) + cols - 1) / cols))
        margin_x = room_width * 0.15
        margin_y = room_height * 0.15
        usable_width = max(1.0, room_width - 2 * margin_x)
        usable_height = max(1.0, room_height - 2 * margin_y)
        for index, device in enumerate(room_devices):
            row = index // cols
            col = index % cols
            abs_x = room_x + margin_x + (usable_width / (cols + 1)) * (col + 1)
            abs_y = room_y + margin_y + (usable_height / (rows + 1)) * (row + 1)
            entity_id = device["entity_id"]
            devices.append({
                "id": entity_id,
                "name": device.get("name") or entity_id.split(".", 1)[-1].replace("_", " "),
                "type": device["device_type"],
                "area": area,
                "areaName": device.get("area_name") or "",
                "x": round(max(0.0, min(100.0, abs_x / width * 100)), 2),
                "y": round(max(0.0, min(100.0, abs_y / height * 100)), 2),
                "icon": device["device_type"],
            })
    return devices


def _save_floor_plan_svg(file_storage, name: str = "", description: str = "") -> dict:
    original_name = file_storage.filename or "floor-plan.svg"
    data = file_storage.read()
    ok, error = _validate_svg_upload(original_name, data)
    if not ok:
        return {"status": "error", "error": error}

    FLOOR_PLAN_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = _safe_svg_filename(original_name)
    target = FLOOR_PLAN_UPLOAD_DIR / filename
    while target.exists() or _floor_plan_id_exists(filename):
        filename = f"{Path(filename).stem}-{int(time.time())}.svg"
        target = FLOOR_PLAN_UPLOAD_DIR / filename
    width, height = _parse_svg_dimensions(data)
    target.write_bytes(data)
    plans = _read_json_list(FLOOR_PLAN_STORE_PATH)
    entry = {
        "id": filename,
        "name": str(name or Path(original_name).stem or filename).strip(),
        "description": str(description or "").strip(),
        "filePath": str(target),
        "url": f"/uploads/floor-plans/{filename}",
        "width": width,
        "height": height,
        "uploadedAt": datetime.now().astimezone().isoformat(),
    }
    plans.append(entry)
    _write_json_list(FLOOR_PLAN_STORE_PATH, plans)
    return {
        "status": "success",
        "floorPlan": entry,
        "filename": filename,
        "size": len(data),
        "url": entry["url"],
    }


class HomeMindWebAgent:
    """支持 Web 接口的 HomeMind Agent"""
    
    def __init__(self, protocol_gateway=None):
        self._gateway = protocol_gateway
        self.instance_id = f"web_agent_{int(time.time() * 1000)}"
        self.confidence_threshold = 0.75
        self._interaction_records = {}
        self._message_counter = 0
        self._startup_metrics = {
            "instance_id": self.instance_id,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "total_duration_ms": 0.0,
            "phases_ms": {},
        }
        startup_begin = time.perf_counter()
        self._init_components()
        self._start_agent_loop()
        self._start_scheduler_loop()
        self._startup_metrics["total_duration_ms"] = round((time.perf_counter() - startup_begin) * 1000, 2)
        print(
            f"[初始化] Web Agent 就绪 instance={self.instance_id} "
            f"total={self._startup_metrics['total_duration_ms']:.2f}ms"
        )

    def _timed_phase(self, name: str, func):
        phase_start = time.perf_counter()
        result = func()
        elapsed_ms = round((time.perf_counter() - phase_start) * 1000, 2)
        self._startup_metrics["phases_ms"][name] = elapsed_ms
        print(f"[初始化][{self.instance_id}] {name}: {elapsed_ms:.2f}ms")
        return result
    
    def _init_components(self):
        """初始化所有组件"""
        print(f"[初始化] HomeMind Web Agent 组件 instance={self.instance_id} ...")

        def init_storage_and_routing():
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
            self.tap_engine = TAPEngine()
            self.tap_rule_store = TAPRuleStore()
            self.scene_store = SceneStore()
            self.nl_to_tap = NLToTAPConverter()
            self.command_validator = CommandValidator(scene_store=self.scene_store)
            self.tool_registry = ToolRegistry()

        self._timed_phase("session_preference_and_router", init_storage_and_routing)
        self.last_cloud_context = {}
        self.last_route_info = {}
        self.scheduler_enabled = True
        self.scheduler_interval = float(os.environ.get("HOMEMIND_RULE_SCHEDULER_INTERVAL", "5"))
        self.dqn_scheduler_interval = float(os.environ.get("HOMEMIND_DQN_SCHEDULER_INTERVAL", "300"))
        self._last_rule_fire = {}
        self._last_dqn_recommend_at = 0.0
        self._last_dqn_daily_learning_date = ""
        
        # 初始化上下文
        def init_context():
            self.context = HomeContext()
            self.context.current_scene = "sleep"
            self.context.temperature = 25.0
            self.context.humidity = 60.0
            self.context.members_home = 1

        self._timed_phase("context", init_context)
        
        # 初始化设备模拟器
        def init_simulator():
            self.device_simulator = DeviceSimulator()
            self.simulator = self.device_simulator

        self._timed_phase("device_simulator", init_simulator)
        
        # 初始化工具（传入协议网关）
        def init_tools():
            self.device_control = device_ctrl.DeviceController(protocol_gateway=self._gateway)
            self.info_query = info_query.InfoQuery()
            self.scene_switcher = scene_switch.SceneSwitcher(self.device_control, scene_store=self.scene_store)
            self.language_normalizer = language_normalizer
            self.tool_registry.bind_many(
                {
                    "device_control": self.device_control,
                    "scene_switcher": self.scene_switcher,
                    "info_query": self.info_query,
                }
            )
            self.transaction_manager = ExecutionTransactionManager(
                self.tool_registry,
                device_controller=self.device_control,
                session_store=self.session_store,
                context=self.context,
            )

        self._timed_phase("tools", init_tools)
        
        # 尝试初始化 Embedding 和知识库
        self.embedding_model = None
        self.kb = None
        self.kb_writer = None
        try:
            self.embedding_model = self._timed_phase("embedding_model", get_embedding_model)
            embedding_fn = self.embedding_model.encode if self.embedding_model else None
            def init_knowledge_base():
                self.kb = KnowledgeBase(embedding_fn=embedding_fn)
                self.kb.preference_store = self.preference_store
                self.kb_writer = kb_writer.KBWriter(self.kb)

            self._timed_phase("knowledge_base", init_knowledge_base)
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
                self.bsr = self._timed_phase("bsr", lambda: BSRecall(kb=self.kb))
                print("[初始化] BSR 召回模块已加载")
            except Exception as e:
                print(f"[警告] BSR 初始化失败: {e}")
        
        try:
            self.lsr = self._timed_phase("lsr", PrecisionRanking)
            print("[初始化] LSR 精排模块已加载")
        except Exception as e:
            print(f"[警告] LSR 初始化失败: {e}")
        
        try:
            self.llm = self._timed_phase(
                "llm",
                lambda: LLMWrapper(
                    backend=os.environ.get("LLM_BACKEND", "mock"),
                    model_path=os.environ.get("LLM_MODEL_PATH", ""),
                    api_base=os.environ.get("LLM_API_BASE", ""),
                    api_key=os.environ.get("LLM_API_KEY", ""),
                    cloud_model=os.environ.get("LLM_MODEL", ""),
                ),
            )
            print("[初始化] LLM 决策模块已加载")
        except Exception as e:
            print(f"[警告] LLM 初始化失败: {e}")
        
        try:
            self.dqn = self._timed_phase(
                "dqn",
                lambda: DQNPolicy(model_dir=os.environ.get("HOMEMIND_DQN_MODEL_DIR", "models")),
            )
            self.dqn_fb = DQNFeedbackTool(self.dqn)
            print("[初始化] DQN 策略模块已加载")
        except Exception as e:
            print(f"[警告] DQN 初始化失败: {e}")
        
        self._timed_phase("restore_state", self._restore_persisted_state)
        print("[初始化] 完成!")

    def _restore_persisted_state(self):
        current_scene = self.session_store.get_current_scene()
        if current_scene:
            self.context.current_scene = current_scene
            self.context.last_scene = SCENE_INDEX_MAP.get(current_scene, -1)
        if self.kb and os.path.exists(self.kb.backup_path):
            self.kb.restore()

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

    def _cloud_json_draft(self, prompt: str, max_tokens: int = 512) -> dict:
        if not self.llm or not getattr(self.llm, "is_cloud_available", lambda: False)():
            return {}
        return self.llm.complete_json(prompt, max_tokens=max_tokens)

    def _available_device_prompt(self) -> str:
        floor_devices = [
            {
                "id": item.get("id") or item.get("entity_id", ""),
                "name": item.get("name", ""),
                "type": item.get("type", ""),
                "area": item.get("area", ""),
                "areaName": item.get("areaName", ""),
            }
            for item in self._floor_plan_devices()
        ]
        registry = [
            {
                "id": item.get("id", ""),
                "name": item.get("name", ""),
                "type": item.get("type", ""),
                "area": item.get("area", ""),
                "areaName": item.get("areaName", ""),
            }
            for item in self._device_registry()
        ]
        return json.dumps({"floor_plan_devices": floor_devices, "registered_devices": registry}, ensure_ascii=False)

    def _normalize_scene_config(self, config: dict) -> dict:
        normalized = {}
        if not isinstance(config, dict):
            return normalized
        for device, command in config.items():
            device_name = str(device or "").strip()
            if not device_name or not isinstance(command, dict):
                continue
            action = str(command.get("action") or command.get("device_action") or "").strip()
            if action not in {"on", "off", "adjust", "open", "close"}:
                continue
            params = command.get("params", {})
            normalized[device_name] = {"action": action, "params": dict(params or {}) if isinstance(params, dict) else {}}
        return normalized

    def _validate_scene_config(self, config: dict) -> dict:
        errors = []
        warnings = []
        valid_actions = {}
        for device, command in self._normalize_scene_config(config).items():
            device_action = command.get("action", "")
            validation = self._validate_spatial_device_command(
                {
                    "action": "\u8bbe\u5907\u63a7\u5236",
                    "device": device,
                    "device_action": device_action,
                    "params": command.get("params", {}),
                },
                text=device,
            )
            if validation.get("valid"):
                valid_actions[device] = command
            else:
                warnings.append(validation.get("message") or f"{device} 未通过空间设备校验")
        if not valid_actions:
            errors.append("没有可执行的场景动作")
        return {"valid": not errors, "errors": errors, "warnings": warnings, "config": valid_actions}

    def _validate_tap_rule_draft(self, rule: dict) -> dict:
        errors = []
        if not isinstance(rule, dict):
            return {"valid": False, "errors": ["rule must be an object"], "warnings": []}
        trigger = rule.get("trigger", {})
        action = rule.get("action", {})
        if not isinstance(trigger, dict) or not trigger.get("type"):
            errors.append("缺少触发条件")
        if not isinstance(action, dict) or not action.get("type"):
            errors.append("缺少执行动作")
        warnings = []
        action_type = str(action.get("type") or "")
        if action_type not in {"device_control", "scene_switch"}:
            errors.append("unsupported action type")
        if action_type == "device_control":
            if not action.get("device") or not action.get("device_action"):
                errors.append("device_control requires device and device_action")
            command = {
                "action": "\u8bbe\u5907\u63a7\u5236",
                "device": action.get("device", ""),
                "device_action": action.get("device_action", ""),
                "params": action.get("params", {}),
            }
            spatial = self._validate_spatial_device_command(command, text=str(action.get("device", "")))
            if not spatial.get("valid"):
                warnings.append(spatial.get("message") or "设备空间校验未通过")
        elif action_type == "scene_switch":
            scene = str(action.get("scene") or "").strip()
            if not scene:
                errors.append("scene_switch requires scene")
            elif self.scene_store.get_scene(scene) is None:
                warnings.append(f"scene {scene} has not been created")
        return {"valid": not errors, "errors": errors, "warnings": warnings}

    def _cloud_scene_draft(self, text: str) -> dict:
        prompt = (
            "你是智能家居低代码场景配置器。根据用户描述生成 JSON，且只能引用可用设备名称或语义设备名。\n"
            f"可用设备: {self._available_device_prompt()}\n"
            f"用户描述: {text}\n"
            "只输出 JSON：{\"name\":\"场景名\",\"config\":{\"设备名\":{\"action\":\"on/off/adjust/open/close\",\"params\":{}}}}"
        )
        parsed = self._cloud_json_draft(prompt, max_tokens=700)
        if not parsed:
            return {}
        return {
            "name": str(parsed.get("name") or "").strip(),
            "config": self._normalize_scene_config(parsed.get("config", {}) or {}),
            "source": "cloud",
        }

    def _build_scene_draft(self, text: str) -> dict | None:
        draft = self._cloud_scene_draft(text)
        if not draft or not draft.get("config"):
            parsed = self.nl_to_tap.parse_scene_creation(text)
            if parsed:
                draft = {
                    "name": parsed.get("name", ""),
                    "config": self._normalize_scene_config(parsed.get("config", {}) or {}),
                    "source": "local",
                }
        if not draft or not draft.get("config"):
            return None
        validation = self._validate_scene_config(draft["config"])
        draft["config"] = validation["config"] or draft["config"]
        draft["validation"] = validation
        if not draft.get("name"):
            draft["name"] = str(text or "未命名场景")[:20]
        return draft

    def _cloud_tap_rule_draft(self, text: str, previous_rule: dict | None = None) -> dict:
        prompt = (
            "你是智能家居 TAP 自动化规则生成器。根据用户描述生成可校验规则，必要时结合上一轮规则修正。\n"
            f"可用设备: {self._available_device_prompt()}\n"
            f"上一轮规则: {json.dumps(previous_rule or {}, ensure_ascii=False)}\n"
            f"用户描述: {text}\n"
            "只输出 JSON：{\"name\":\"规则名\",\"trigger\":{\"type\":\"time|holiday|day_of_week|temperature|humidity|occupancy\",\"at\":\"HH:MM\",\"name\":\"\",\"month\":1,\"day\":1},\"conditions\":[],\"action\":{\"type\":\"device_control|scene_switch\",\"device\":\"\",\"device_action\":\"on/off/adjust/open/close\",\"params\":{},\"scene\":\"\"},\"priority\":50}"
        )
        parsed = self._cloud_json_draft(prompt, max_tokens=700)
        if parsed:
            parsed["_source"] = "cloud"
        return parsed if isinstance(parsed, dict) else {}

    def _draft_tap_rule(self, raw_text: str, normalized_text: str = "", previous_rule: dict | None = None) -> dict | None:
        text = " ".join(part for part in [raw_text, normalized_text] if part).strip()
        rule = self._cloud_tap_rule_draft(text, previous_rule=previous_rule)
        if not rule:
            if previous_rule and normalized_text:
                rule = self.nl_to_tap.parse(normalized_text)
            if not rule:
                rule = self.nl_to_tap.parse(raw_text) or self.nl_to_tap.parse(normalized_text or raw_text)
            if rule:
                rule["_source"] = "local"
        if not rule and previous_rule:
            trigger = self.nl_to_tap.extract_trigger(text)
            if trigger:
                rule = {
                    "name": previous_rule.get("name", raw_text[:40]),
                    "trigger": trigger,
                    "conditions": [],
                    "action": dict(previous_rule.get("action", {}) or {}),
                    "priority": previous_rule.get("priority", 50),
                    "_source": "local",
                }
        if not isinstance(rule, dict):
            return None
        return {
            "name": rule.get("name", raw_text[:40]),
            "trigger": dict(rule.get("trigger", {}) or {}),
            "conditions": list(rule.get("conditions", []) or []),
            "action": dict(rule.get("action", {}) or {}),
            "priority": int(rule.get("priority", 50) or 50),
            "source": rule.get("_source", "cloud" if self.llm and self.llm.is_cloud_available() else "local"),
        }

    def _build_automation_proposal(self, raw_text: str, normalized_text: str, previous_rule: dict | None = None) -> dict | None:
        rule = self._draft_tap_rule(raw_text, normalized_text, previous_rule=previous_rule)
        if not rule:
            return None
        trigger = dict(rule.get("trigger", {}) or {})
        action = dict(rule.get("action", {}) or {})
        validation = self._validate_tap_rule_draft(rule)
        if not validation.get("valid"):
            return None
        summary = self._summarize_automation_rule(trigger, action)
        proposal_id = self._next_message_id("proposal")
        rule_preview = {
            "name": rule.get("name", raw_text[:40]),
            "trigger": trigger,
            "action": action,
            "priority": rule.get("priority", 50),
            "validation": validation,
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
            "feedback_target": {"message_id": interaction["message_id"], "target_type": "automation_proposal"},
        }
        self.session_store.set_pending_confirmation(
            "automation_proposal",
            proposal,
            feedback_target={"message_id": interaction["message_id"], "target_type": "automation_proposal"},
        )
        return proposal

    def _update_pending_automation_trigger(self, raw_text: str, normalized_text: str) -> dict | None:
        pending = self.session_store.get_pending_confirmation()
        if pending.get("action_type") != "automation_proposal":
            return None
        trigger = self.nl_to_tap.extract_trigger(" ".join(part for part in [raw_text, normalized_text] if part))
        if not trigger:
            return None

        payload = dict(pending.get("payload", {}) or {})
        rule_preview = dict(payload.get("rule_preview", {}) or {})
        action = dict(rule_preview.get("action", {}) or {})
        if not action:
            return None

        updated_rule = {
            "name": rule_preview.get("name", raw_text[:40]),
            "trigger": trigger,
            "action": action,
            "priority": rule_preview.get("priority", 50),
        }
        summary = self._summarize_automation_rule(trigger, action)
        proposal_id = self._next_message_id("proposal")
        message_id = self._next_message_id("automation")
        interaction = self._build_message_metadata(
            "automation_proposal",
            raw_text,
            normalized_text,
            decision_snapshot={"rule": updated_rule},
            extra={"proposal_id": proposal_id, "rule_preview": updated_rule},
            message_id=message_id,
        )
        proposal = {
            "message_id": interaction["message_id"],
            "proposal_id": proposal_id,
            "summary": summary,
            "rule_preview": updated_rule,
            "confirm_actions": ["accept", "change", "reject"],
        }
        self.session_store.set_pending_confirmation(
            "automation_proposal",
            proposal,
            feedback_target={"message_id": interaction["message_id"], "target_type": "automation_proposal"},
        )
        return proposal

    def _summarize_automation_rule(self, trigger: dict, action: dict) -> str:
        trigger_type = trigger.get("type")
        if trigger_type == "time":
            trigger_text = f"每天 {trigger.get('at', '--:--')}"
        elif trigger_type == "day_of_week":
            days = set(int(day) for day in trigger.get("days", []))
            trigger_text = "每周末" if days == {5, 6} else f"每周{','.join(str(day) for day in sorted(days))}"
        elif trigger_type == "holiday":
            trigger_text = f"{trigger.get('name') or trigger.get('pattern') or '节假日'}当天"
        else:
            trigger_text = "满足条件时"
        if action.get("type") == "scene_switch":
            action_text = f"切换到{action.get('scene', '指定场景')}"
        else:
            device = action.get("device", "设备")
            device_action = action.get("device_action", "执行动作")
            action_label = {"on": "打开", "off": "关闭", "adjust": "调节", "open": "打开", "close": "关闭"}.get(device_action, device_action)
            action_text = f"{action_label}{device}"
        return f"我理解成：{trigger_text} {action_text}。要为你创建定时任务吗？"

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

    def _build_corrected_execution_result(
        self,
        original_input: str,
        normalized_input: str,
        correction: str,
        decision_snapshot: dict,
    ) -> dict | None:
        corrected = str(correction or normalized_input or original_input or "").strip()
        if not corrected:
            return None

        cloud_command = self._cloud_json_draft(
            (
                "你是智能家居多轮纠正解析器。结合上一轮命令和用户纠正，输出修正后的结构化命令。\n"
                f"可用设备: {self._available_device_prompt()}\n"
                f"上一轮命令: {json.dumps(decision_snapshot or {}, ensure_ascii=False)}\n"
                f"用户纠正: {corrected}\n"
                "只输出 JSON：{\"action\":\"设备控制|场景切换\",\"device\":\"\",\"scene\":\"\",\"device_action\":\"on/off/adjust/open/close\",\"params\":{},\"confidence\":0.0,\"reasoning\":\"\"}"
            ),
            max_tokens=500,
        )
        cloud_action = str(cloud_command.get("action") or "").strip()
        if cloud_action == "device_control":
            cloud_command["action"] = "\u8bbe\u5907\u63a7\u5236"
        if cloud_command.get("action") in {"\u8bbe\u5907\u63a7\u5236", "设备控制"}:
            device = str(cloud_command.get("device") or "").strip()
            device_action = str(cloud_command.get("device_action") or "").strip()
            if device and device_action:
                command = {
                    "action": "\u8bbe\u5907\u63a7\u5236",
                    "device": device,
                    "scene": "",
                    "device_action": device_action,
                    "params": dict(cloud_command.get("params", {}) or {}),
                    "confidence": float(cloud_command.get("confidence", 0.95) or 0.95),
                    "reasoning": str(cloud_command.get("reasoning") or "cloud corrected interaction feedback"),
                }
                executed = self._execute_device_with_spatial_gate(command, text=corrected, route="feedback_change_cloud")
                if executed.get("status") == "success":
                    interaction = self._build_message_metadata("execution", corrected, corrected, decision_snapshot=command)
                    return {
                        "status": "success",
                        "response_type": "execution_result",
                        "message_id": interaction["message_id"],
                        "action": executed.get("action", f"{device}_{device_action}"),
                        "response": executed.get("response", ""),
                        "confidence": command["confidence"],
                        "route": "feedback_change_cloud",
                        "route_reason": "cloud_corrected_previous_execution",
                        "feedback_target": {"message_id": interaction["message_id"], "target_type": "execution"},
                    }

        if cloud_action in {"\u573a\u666f\u5207\u6362", "scene_switch"}:
            scene = str(cloud_command.get("scene") or "").strip()
            if scene:
                executed = self._execute_scene_with_spatial_gate(scene, route="feedback_change_cloud")
                if executed.get("status") == "success":
                    command = {
                        "action": "\u573a\u666f\u5207\u6362",
                        "device": "",
                        "scene": scene,
                        "device_action": "",
                        "params": {},
                        "confidence": float(cloud_command.get("confidence", 0.95) or 0.95),
                        "reasoning": str(cloud_command.get("reasoning") or "cloud corrected scene feedback"),
                    }
                    interaction = self._build_message_metadata("execution", corrected, corrected, decision_snapshot=command)
                    return {
                        "status": "success",
                        "response_type": "execution_result",
                        "message_id": interaction["message_id"],
                        "action": "scene_switch",
                        "scene": scene,
                        "response": executed.get("response", ""),
                        "confidence": command["confidence"],
                        "route": "feedback_change_cloud",
                        "route_reason": "cloud_corrected_previous_scene",
                        "feedback_target": {"message_id": interaction["message_id"], "target_type": "execution"},
                    }

        previous_action_type = str(decision_snapshot.get("action") or "").strip()
        previous_device = str(decision_snapshot.get("device") or "").strip()
        previous_device_action = str(decision_snapshot.get("device_action") or "").strip()
        previous_params = dict(decision_snapshot.get("params", {}) or {})

        parsed_device = self._device_from_text(corrected) if hasattr(self, "_device_from_text") else ""
        device = parsed_device or previous_device
        parsed_action, parsed_params = self._action_from_text(corrected, device) if hasattr(self, "_action_from_text") else ("", {})
        device_action = parsed_action or previous_device_action
        params = parsed_params if parsed_action else previous_params

        if previous_action_type == "\u8bbe\u5907\u63a7\u5236" and device and device_action:
            command = {
                "action": "\u8bbe\u5907\u63a7\u5236",
                "device": device,
                "scene": "",
                "device_action": device_action,
                "params": params,
                "confidence": 0.95,
                "reasoning": "corrected interaction feedback",
            }
            executed = self._execute_device_with_spatial_gate(command, text=corrected, route="feedback_change")
            if executed.get("status") != "success":
                return executed
            interaction = self._build_message_metadata(
                "execution",
                corrected,
                corrected,
                decision_snapshot=command,
            )
            return {
                "status": "success",
                "response_type": "execution_result",
                "message_id": interaction["message_id"],
                "action": executed.get("action", f"{device}_{device_action}"),
                "response": executed.get("response", ""),
                "confidence": command["confidence"],
                "route": "feedback_change",
                "route_reason": "corrected_previous_execution",
                "feedback_target": {"message_id": interaction["message_id"], "target_type": "execution"},
            }

        if previous_action_type == "\u573a\u666f\u5207\u6362":
            parsed = self._run_llm_first_query(corrected, corrected)
            parsed.pop("_debug", None)
            return parsed

        return None

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
                self._record_dqn_feedback(action, "\u63a5\u53d7", source="interaction")
                return {"status": "success", "message": "DQN feedback recorded"}
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
                self._record_dqn_feedback(action, "\u62d2\u7edd", source="interaction")
                self.session_store.clear_pending_confirmation()
                return {"status": "success", "message": "DQN feedback recorded"}
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
                previous_rule = dict(interaction.get("rule_preview", {}) or decision_snapshot.get("rule", {}) or {})
                proposal = self._build_automation_proposal(original_input or corrected, corrected, previous_rule=previous_rule)
                if proposal:
                    return {
                        "status": "success",
                        "response_type": "automation_proposal",
                        "message": proposal["summary"],
                        "proposal": proposal,
                    }
            if corrected:
                corrected_result = self._build_corrected_execution_result(
                    original_input,
                    normalized_input,
                    corrected,
                    decision_snapshot,
                )
                if corrected_result:
                    return corrected_result
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

    def _spatial_rejection_for_decision(self, decision: dict, text: str, route: str):
        if decision.get("action") != "\u8bbe\u5907\u63a7\u5236":
            return None
        spatial = self._validate_spatial_device_command(decision, text=text)
        if spatial.get("valid"):
            return None
        return self._execute_device_with_spatial_gate(decision, text=text, route=route)

    def _detect_unsupported_request(self, raw_text: str, normalized_text: str = ""):
        unsupported = self.router.detect_unsupported_request(raw_text, normalized_query=normalized_text)
        if unsupported:
            self.last_route_info = unsupported
            self.session_store.update_clarification(unsupported["message"])
        return unsupported

    def _execute_structured_command(self, command: dict, route: str = "tap") -> dict:
        validation = self._validate_decision(command)
        if not validation.valid:
            return {"status": "invalid", "errors": validation.errors, "command": command}
        if validation.requires_confirmation:
            return {"status": "confirmation_required", "command": validation.normalized_command}

        normalized = validation.normalized_command
        action_type = normalized.get("action", "")
        device = normalized.get("device", "")
        device_action = normalized.get("device_action", "")
        scene = normalized.get("scene", "")
        params = normalized.get("params", {})

        if action_type == "设备控制" and device and device_action:
            result = self._execute_device_with_spatial_gate(normalized, text="", route=route)
            result["command"] = normalized
            return result
        if action_type == "场景切换" and scene:
            result = self._execute_scene_with_spatial_gate(scene, route=route)
            result["command"] = normalized
            return result
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

    def _parse_dqn_action(self, recommendation_id: str, fallback: int = 5) -> int:
        parts = str(recommendation_id or "").rsplit("_", 1)
        if len(parts) == 2:
            try:
                return int(parts[1])
            except ValueError:
                return fallback
        return fallback

    def _normalize_dqn_feedback(self, response: str) -> str:
        value = str(response or "").strip()
        lowered = value.lower()
        if lowered in {"accept", "accepted"} or value in {"\u63a5\u53d7", "\u93ba\u30e5\u5f48"}:
            return "\u63a5\u53d7"
        if lowered in {"reject", "rejected"} or value in {"\u62d2\u7edd", "\u93b7\u6394\u7cb7"}:
            return "\u62d2\u7edd"
        if lowered in {"ignore", "ignored"} or value in {"\u5ffd\u7565", "\u8e47\u754c\u6690"}:
            return "\u5ffd\u7565"
        if lowered in {"change", "corrected"} or value in {"\u7ea0\u6b63", "\u7efe\u72b3\ue11c"}:
            return "\u7ea0\u6b63"
        return value

    def _dqn_feedback_acceptance(self, feedback: str) -> bool | None:
        value = str(feedback or "").strip()
        if value in {"\u63a5\u53d7", "accepted", "accept"}:
            return True
        if value in {"\u62d2\u7edd", "\u7ea0\u6b63", "rejected", "reject", "corrected", "change"}:
            return False
        return None

    def _record_dqn_recommendation_memory(self, recommendation: dict, action: int, source: str = "scheduler") -> None:
        scene = str(recommendation.get("scene_name") or recommendation.get("scene") or SCENE_NAMES.get(action, "") or "")
        if not scene:
            return
        confidence = float(recommendation.get("confidence", 0.0) or 0.0)
        reason = str(recommendation.get("reason", "") or "")
        message_id = str(recommendation.get("message_id", "") or "")
        self.preference_store.record_dqn_recommendation(
            scene=scene,
            action=action,
            confidence=confidence,
            reason=reason,
            source=source,
            message_id=message_id,
        )
        if self.kb:
            self.kb.add(
                f"DQN recommendation: scene={scene}, action={action}, confidence={confidence:.4f}, source={source}",
                category="DQN",
                accepted=True,
                event_type="recommendation",
                scene=scene,
                action=action,
                confidence=confidence,
                source=source,
                memory_key=f"dqn:recommendation:{source}:{message_id or action}:{int(time.time())}",
            )
            self.kb.backup()

    def _record_dqn_feedback(self, action: int, response: str, source: str = "api") -> dict:
        if not self.dqn:
            return {"status": "unavailable", "reason": "dqn_not_initialized"}

        feedback = self._normalize_dqn_feedback(response)
        if self.dqn_fb:
            self.dqn_fb.record(self.context, action, feedback)
        else:
            self.dqn.record_feedback(self.context, action, feedback)

        event = dict(getattr(self.dqn, "last_feedback_event", {}) or {})
        scene = SCENE_NAMES.get(action, "")
        reward = float(event.get("reward", 0.0) or 0.0)
        updated = bool(event.get("updated", False))
        buffer_size = int(event.get("buffer_size", 0) or 0)
        model_saved = False
        try:
            model_saved = bool(self.dqn.save())
        except Exception as exc:
            print(f"[DQN Save Error] {exc}")

        self.preference_store.record_recommendation_feedback(scene, feedback)
        self.preference_store.record_dqn_feedback(
            scene=scene,
            action=action,
            feedback=feedback,
            reward=reward,
            updated=updated,
            buffer_size=buffer_size,
            source=source,
        )
        if self.kb:
            accepted = self._dqn_feedback_acceptance(feedback)
            self.kb.add(
                f"DQN feedback: scene={scene}, action={action}, feedback={feedback}, reward={reward:.2f}, source={source}",
                category="DQN",
                accepted=bool(accepted),
                event_type="feedback",
                scene=scene,
                action=action,
                feedback=feedback,
                reward=reward,
                updated=updated,
                source=source,
                memory_key=f"dqn:feedback:{source}:{action}:{int(time.time())}",
            )
            self.kb.backup()

        return {
            "status": "success",
            "action": action,
            "scene": scene,
            "feedback": feedback,
            "reward": reward,
            "updated": updated,
            "buffer_size": buffer_size,
            "model_saved": model_saved,
        }

    def _run_daily_dqn_learning(self, now: datetime = None) -> dict:
        if not self.dqn:
            return {}
        now = now or datetime.now()
        today = now.date().isoformat()
        if self._last_dqn_daily_learning_date == today:
            return {}

        summary = self.dqn.daily_incremental_update()
        if summary.get("status") != "updated":
            return dict(summary)

        self._last_dqn_daily_learning_date = today
        model_saved = False
        try:
            model_saved = bool(self.dqn.save())
        except Exception as exc:
            print(f"[DQN Save Error] {exc}")
        summary = dict(summary)
        summary["model_saved"] = model_saved
        self.preference_store.record_dqn_learning(summary, source="scheduler")
        if self.kb:
            self.kb.add(
                "DQN daily learning: "
                f"status={summary.get('status')}, buffer={summary.get('buffer_size')}, "
                f"epsilon={summary.get('epsilon')}, saved={model_saved}",
                category="DQN",
                accepted=summary.get("status") == "updated",
                event_type="daily_learning",
                source="scheduler",
                status=summary.get("status"),
                buffer_size=summary.get("buffer_size"),
                model_saved=model_saved,
                memory_key=f"dqn:daily_learning:{today}",
            )
            self.kb.backup()
        return summary

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

        dqn_learning = self._run_daily_dqn_learning(now)
        if dqn_learning and dqn_learning.get("status") == "updated":
            executed.append({"type": "dqn_daily_learning", "learning": dqn_learning})

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
        self._record_dqn_recommendation_memory(recommendation, action_idx, source="scheduler")
        self._last_dqn_recommend_at = current
        socketio.emit("message", {
            "type": "dqn_recommendation",
            "data": recommendation,
        })
        return recommendation

    def _start_scheduler_loop(self):
        if os.environ.get("HOMEMIND_DISABLE_BACKGROUND_THREADS") == "1":
            self.scheduler_thread = None
            self._timed_phase("scheduler_loop", lambda: None)
            return

        def scheduler_worker():
            while True:
                try:
                    self.context.hour = datetime.now().hour
                    self._scheduler_tick()
                    time.sleep(max(1.0, self.scheduler_interval))
                except Exception as e:
                    print(f"[Scheduler Error] {e}")
                    time.sleep(2)

        def start_scheduler():
            self.scheduler_thread = threading.Thread(target=scheduler_worker, daemon=True)
            self.scheduler_thread.start()

        self._timed_phase("scheduler_loop", start_scheduler)

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
        kb_status = {}
        if self.kb:
            try:
                recent_memory = self.kb.memory_store[-5:]
            except Exception:
                recent_memory = []
            try:
                kb_status = self.kb.get_status()
            except Exception:
                kb_status = {}
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
            "kb_status": kb_status,
        }

    def get_privacy_status(self) -> dict:
        backend = getattr(self.llm, "backend", "mock")
        cloud_enabled = backend == "openai" and self.llm.is_cloud_available()
        session = self.session_store.get_runtime_context()
        route = session.get("last_route", self.last_route_info.get("route", "local"))
        storage_status = get_encrypted_storage().status()
        return {
            "status": "success",
            "cloud_enabled": cloud_enabled,
            "llm_backend": backend,
            "last_route": route,
            "last_route_reason": self.last_route_info.get("reason", ""),
            "last_cloud_context": self.last_cloud_context,
            "storage_security": storage_status,
            "cloud_log_policy": self.llm.cloud_logging_status() if hasattr(self.llm, "cloud_logging_status") else {"policy": "none", "raw_payload_retained": False},
            "audit_security": self.audit_logger.status() if hasattr(self.audit_logger, "status") else {},
            "minimal_fields": ["hour", "temperature", "humidity", "occupancy", "scene", "top_candidates", "preference_summary"],
        }
    
    def _start_agent_loop(self):
        if os.environ.get("HOMEMIND_DISABLE_BACKGROUND_THREADS") == "1":
            self.agent_thread = None
            self._timed_phase("agent_loop", lambda: None)
            return

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
        
        def start_agent_worker():
            self.agent_thread = threading.Thread(target=agent_worker, daemon=True)
            self.agent_thread.start()

        self._timed_phase("agent_loop", start_agent_worker)
    
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

        if intent_info["route"] == "clarify":
            message = intent_info.get("reply_message") or intent_info.get("message") or "请问你想执行哪个具体操作？"
            self.session_store.update_clarification(message)
            payload = {
                "action": "clarification",
                "result": message,
                "status": "clarification",
                "response_type": "clarification",
                "query_id": query_id,
                "route": intent_info["route"],
                "route_reason": intent_info["reason"],
                "target": intent_info.get("target", ""),
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
                print(
                    "[Privacy] 云端最小上下文字段: "
                    f"keys={sorted(cloud_context.keys())} "
                    f"bytes={len(json.dumps(cloud_context, ensure_ascii=False))}"
                )
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

                spatial_rejection = self._spatial_rejection_for_decision(decision, query_text, route_info["route"])
                if spatial_rejection:
                    socketio.emit("message", {
                        "type": "agent_clarification",
                        "data": {
                            "question": spatial_rejection.get("response", ""),
                            "candidates": route_info["top_candidates"],
                            "query_id": query_id
                        }
                    })
                    return

                validation = self._validate_decision(decision)
                if not validation.valid:
                    message = "我暂时不能执行这个指令：" + ";".join(validation.errors)
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
                if validation.requires_confirmation:
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
                decision = validation.normalized_command
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

        if not self._has_device_identifier(device_id):
            socketio.emit("message", {
                "type": "device_update",
                "data": {
                    "device": device_id,
                    "state": {},
                    "result": "device not found"
                }
            })
            return
        
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
        
        result_payload = self._execute_scene_with_spatial_gate(scene, route="local")
        result = result_payload.get("response", result_payload.get("message", ""))
        
        socketio.emit("message", {
            "type": "scene_update",
            "data": {
                "scene": scene_id,
                "devices": self.get_all_states()["devices"]
            }
        })
        socketio.emit("message", {
            "type": "agent_response",
            "data": {
                "action": "scene_switch",
                "result": result,
                "status": "success",
                "response_type": "execution_result",
                "scene": scene_id,
                "route": "local",
                "route_reason": "scene_switch_event",
            },
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
        self._record_dqn_feedback(action, response, source="websocket")
        return
        
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

    def _device_registry(self) -> list:
        registry = _load_device_registry()
        self._ensure_device_states(registry)
        return registry

    def _active_floor_plan_mapping(self) -> dict | None:
        mappings = _read_json_list(FLOOR_PLAN_DEVICE_STORE_PATH)
        if not mappings:
            return None
        plans = _read_json_list(FLOOR_PLAN_STORE_PATH)
        active_plan = next((plan for plan in plans if plan.get("active")), None)
        if active_plan:
            active_id = active_plan.get("id")
            matched = next((item for item in mappings if item.get("floorPlanId") == active_id), None)
            if matched:
                return matched
        if plans:
            plan_ids = {plan.get("id") for plan in plans}
            matched = next((item for item in mappings if item.get("floorPlanId") in plan_ids), None)
            if matched:
                return matched
        return mappings[0]

    def _floor_plan_devices(self) -> list:
        mapping = self._active_floor_plan_mapping()
        if not mapping:
            return []
        return [device for device in mapping.get("devices", []) if isinstance(device, dict)]

    def _floor_plan_area_aliases(self) -> dict:
        mapping = self._active_floor_plan_mapping() or {}
        aliases = {area: list(values) for area, values in DEFAULT_AREA_ALIASES.items()}
        for area, name in (mapping.get("areaNames") or {}).items():
            area = str(area or "").strip()
            name = str(name or "").strip()
            if area and name:
                aliases.setdefault(area, []).append(name)
        for device in mapping.get("devices", []):
            area = str(device.get("area") or "").strip()
            name = str(device.get("areaName") or "").strip()
            if area and name:
                aliases.setdefault(area, []).append(name)
            if area:
                aliases.setdefault(area, []).append(area)
        return aliases

    def _area_filter_from_text(self, text: str) -> str:
        normalized_text = _normalize_area_name(text)
        if not normalized_text:
            return ""
        candidates = []
        for area, aliases in self._floor_plan_area_aliases().items():
            for alias in aliases:
                alias_key = _normalize_area_name(alias)
                if alias_key:
                    candidates.append((len(alias_key), area, alias_key))
        for _, area, alias_key in sorted(candidates, reverse=True):
            if alias_key in normalized_text:
                return area
        return ""

    def _mapped_device_matches_semantic(self, semantic_device: str, mapped_device: dict) -> bool:
        semantic_key = str(semantic_device or "").strip().lower()
        exact_values = {
            str(mapped_device.get("id") or "").strip().lower(),
            str(mapped_device.get("name") or "").strip().lower(),
            str(mapped_device.get("entity_id") or "").strip().lower(),
        }
        if semantic_key and semantic_key in exact_values:
            return True
        rule = SEMANTIC_DEVICE_MATCHES.get(semantic_device)
        if not rule:
            return False
        device_type = str(mapped_device.get("type") or mapped_device.get("device_type") or "").strip().lower()
        haystack = " ".join(
            str(mapped_device.get(key) or "").lower()
            for key in ("id", "name", "type", "area", "areaName")
        )
        if device_type in rule["types"]:
            return True
        return any(token.lower() in haystack for token in rule["tokens"])

    def _semantic_device_for_mapped_device(self, mapped_device: dict) -> str:
        for semantic_device in SEMANTIC_DEVICE_MATCHES:
            if self._mapped_device_matches_semantic(semantic_device, mapped_device):
                return semantic_device
        return str(mapped_device.get("name") or mapped_device.get("id") or "").strip()

    def _floor_plan_device_by_identifier(self, device_id: str) -> dict | None:
        key = str(device_id or "").strip()
        if not key:
            return None
        for device in self._floor_plan_devices():
            if key in {
                str(device.get("id") or ""),
                str(device.get("name") or ""),
                str(device.get("entity_id") or ""),
            }:
                return device
        return None

    def _spatial_targets_for_device(self, semantic_device: str, text: str = "") -> dict:
        mapping = self._active_floor_plan_mapping()
        if not mapping:
            return {
                "valid": False,
                "reason": "no_floor_plan_mapping",
                "message": "\u8bf7\u5148\u4e0a\u4f20 SVG \u6237\u578b\u56fe\u5e76\u7ed1\u5b9a\u8bbe\u5907\u6620\u5c04\uff0c\u7136\u540e\u518d\u6267\u884c\u8bbe\u5907\u63a7\u5236\u3002",
                "targets": [],
                "area": "",
            }
        devices = self._floor_plan_devices()
        area = self._area_filter_from_text(text)
        matches = [
            device for device in devices
            if self._mapped_device_matches_semantic(semantic_device, device)
            and (not area or device.get("area") == area)
        ]
        if matches:
            return {"valid": True, "reason": "mapped_device_found", "targets": matches, "area": area, "message": ""}
        area_label = area or "\u5f53\u524d\u6237\u578b\u56fe"
        return {
            "valid": False,
            "reason": "device_not_in_floor_plan",
            "message": f"\u5f53\u524d SVG \u6237\u578b\u56fe\u7684\u8bbe\u5907\u8868\u91cc\u6ca1\u6709\u53ef\u63a7\u7684\u201c{area_label}{semantic_device}\u201d\uff0c\u5df2\u62e6\u622a\u6267\u884c\u3002",
            "targets": [],
            "area": area,
        }

    def _validate_spatial_device_command(self, command: dict, text: str = "") -> dict:
        if command.get("action") != "\u8bbe\u5907\u63a7\u5236":
            return {"valid": True, "reason": "not_device_control", "message": "", "targets": []}
        device = str(command.get("device") or "").strip()
        if not device:
            return {"valid": True, "reason": "no_device", "message": "", "targets": []}
        return self._spatial_targets_for_device(device, text=text)

    def _execute_device_with_spatial_gate(self, command: dict, text: str, route: str) -> dict:
        spatial = self._validate_spatial_device_command(command, text=text)
        if not spatial["valid"]:
            self.session_store.update_clarification(spatial["message"])
            return {
                "status": "unsupported",
                "response_type": "clarification",
                "action": "unsupported",
                "target": command.get("device", ""),
                "response": spatial["message"],
                "message": spatial["message"],
                "route": "unsupported",
                "route_reason": spatial["reason"],
                "spatial": spatial,
            }
        device = command.get("device", "")
        device_action = command.get("device_action", "")
        params = command.get("params", {})
        message = self.device_control.execute(device, device_action, params)
        target_names = [target.get("name") or target.get("id") for target in spatial.get("targets", [])]
        if target_names:
            message = f"{message}\u6620\u5c04\u8bbe\u5907\uff1a{', '.join(target_names)}\u3002"
        self.session_store.update_from_decision(command, route=route, result=message)
        self.preference_store.record_action_accept(command, self.context)
        return {
            "status": "success",
            "response_type": "execution_result",
            "action": f"{device}_{device_action}",
            "device": device,
            "device_action": device_action,
            "params": params,
            "response": message,
            "message": message,
            "spatial": spatial,
        }

    def _execute_scene_with_spatial_gate(self, scene: str, route: str = "local") -> dict:
        config = self.scene_store.get_scene(scene)
        if config is None:
            message = f"\u4e0d\u652f\u6301\u7684\u573a\u666f: {scene}"
            self.session_store.update_clarification(message)
            return {"status": "unsupported", "response": message, "message": message, "route_reason": "scene_not_found"}
        executed = []
        skipped = []
        for device, cmd in config.items():
            command = {
                "action": "\u8bbe\u5907\u63a7\u5236",
                "device": device,
                "scene": "",
                "device_action": cmd.get("action", ""),
                "params": cmd.get("params", {}),
                "confidence": 1.0,
                "reasoning": f"scene {scene}",
            }
            spatial = self._validate_spatial_device_command(command)
            if not spatial["valid"]:
                skipped.append(device)
                continue
            result = self.device_control.execute(device, command["device_action"], command["params"])
            executed.append(result)
        if not executed:
            message = f"\u5f53\u524d SVG \u6237\u578b\u56fe\u7684\u8bbe\u5907\u8868\u4e0d\u652f\u6301\u6267\u884c\u201c{scene}\u201d\u4e2d\u7684\u4efb\u4f55\u8bbe\u5907\u52a8\u4f5c\uff0c\u5df2\u62e6\u622a\u573a\u666f\u5207\u6362\u3002"
            self.session_store.update_clarification(message)
            return {
                "status": "unsupported",
                "response": message,
                "message": message,
                "route_reason": "scene_devices_not_in_floor_plan",
                "skipped_devices": skipped,
            }
        self.context.current_scene = scene
        self.context.last_scene = SCENE_INDEX_MAP.get(scene, -1)
        self.session_store.update_scene(scene)
        decision = {
            "action": "\u573a\u666f\u5207\u6362",
            "device": "",
            "scene": scene,
            "device_action": "",
            "params": {},
            "confidence": 1.0,
            "reasoning": "scene execution with spatial gate",
        }
        message = f"\u5df2\u5207\u6362\u5230{scene}\u3002" + " ".join(executed)
        if skipped:
            message += f"\u672a\u5728\u6237\u578b\u8bbe\u5907\u8868\u4e2d\u627e\u5230\u7684\u8bbe\u5907\u5df2\u8df3\u8fc7\uff1a{', '.join(skipped)}\u3002"
        self.session_store.update_from_decision(decision, route=route, result=message)
        self.preference_store.record_action_accept(decision, self.context)
        return {
            "status": "success",
            "response_type": "execution_result",
            "action": "scene_switch",
            "scene": scene,
            "response": message,
            "message": message,
            "skipped_devices": skipped,
        }

    def _ensure_device_states(self, registry: list) -> None:
        for item in registry:
            self.device_control.add_device(item["name"], _default_device_state(item.get("type", "")))

    def _device_registry_map(self) -> dict:
        return {item["id"]: item for item in self._device_registry()}

    def _format_device_state(self, raw: dict) -> dict:
        raw = raw or {}
        is_on = raw.get("status") == "开"
        return {
            "is_on": is_on,
            **{k: v for k, v in raw.items() if k != "status"}
        }

    def _has_device_identifier(self, device_id: str) -> bool:
        if not device_id:
            return False
        if self._floor_plan_device_by_identifier(device_id):
            return True
        registry = self._device_registry()
        if any(item["id"] == device_id or item["name"] == device_id for item in registry):
            return True
        return False

    def list_devices(self) -> list:
        registry = self._device_registry()
        raw_states = self.device_control.get_all_state()
        return [
            {
                **item,
                "state": self._format_device_state(raw_states.get(item["name"], {})),
            }
            for item in registry
        ]

    def create_device(self, payload: dict) -> dict:
        raw_name = str(payload.get("name") or "").strip()
        area_name = str(payload.get("areaName") or payload.get("roomName") or "").strip()
        name = _spatial_device_name(raw_name, area_name)
        if not name:
            raise ValueError("name is required")
        device_id = _safe_device_id(payload.get("id") or payload.get("deviceId"), name)
        registry = self._device_registry()
        if any(item["id"] == device_id for item in registry):
            raise KeyError("device id already exists")
        if any(item["name"] == name for item in registry):
            raise KeyError("device name already exists")
        item = {
            "id": device_id,
            "name": name,
            "type": str(payload.get("type") or "switch").strip() or "switch",
            "protocol": "simulated",
        }
        area = str(payload.get("area") or payload.get("room") or "").strip()
        if area:
            item["area"] = area
        if area_name:
            item["areaName"] = area_name
        registry.append(item)
        _save_device_registry(registry)
        self.device_control.add_device(item["name"], _default_device_state(item["type"]))
        return item

    def update_device(self, device_id: str, payload: dict) -> dict:
        registry = self._device_registry()
        for index, item in enumerate(registry):
            if item["id"] != device_id:
                continue
            if payload.get("id") and payload.get("id") != device_id:
                raise ValueError("device id cannot be changed")
            immutable_fields = sorted(field for field in IMMUTABLE_DEVICE_UPDATE_FIELDS if field in payload)
            if immutable_fields:
                raise ValueError(f"device runtime fields cannot be changed: {', '.join(immutable_fields)}")
            old_name = item["name"]
            next_area_name = str(payload.get("areaName") or payload.get("roomName") or item.get("areaName") or "").strip()
            next_name = _spatial_device_name(
                str(payload.get("name") or item["name"]).strip() or item["name"],
                next_area_name,
            )
            if any(other["id"] != device_id and other.get("name") == next_name for other in registry):
                raise ValueError("device name already exists")
            updated = {
                **item,
                "name": next_name,
                "type": str(payload.get("type") or item.get("type") or "switch").strip() or "switch",
                "protocol": "simulated",
            }
            if "area" in payload or "room" in payload:
                area = str(payload.get("area") or payload.get("room") or "").strip()
                if area:
                    updated["area"] = area
                else:
                    updated.pop("area", None)
            if "areaName" in payload or "roomName" in payload:
                area_name = str(payload.get("areaName") or payload.get("roomName") or "").strip()
                if area_name:
                    updated["areaName"] = area_name
                else:
                    updated.pop("areaName", None)
            registry[index] = updated
            _save_device_registry(registry)
            self.device_control.update_device(old_name, updated["name"], _default_device_state(updated["type"]))
            return updated
        raise LookupError("device not found")

    def delete_device(self, device_id: str) -> dict:
        registry = self._device_registry()
        for index, item in enumerate(registry):
            if item["id"] != device_id:
                continue
            removed = registry.pop(index)
            _save_device_registry(registry)
            self.device_control.delete_device(removed["name"])
            return removed
        raise LookupError("device not found")

    def get_all_states(self) -> dict:
        """获取所有状态，返回前端统一的设备格式"""
        raw_states = self.device_control.get_all_state()
        storage_status = get_encrypted_storage().status()
        devices = {}
        for item in self._device_registry():
            raw = raw_states.get(item["name"], {})
            devices[item["id"]] = self._format_device_state(raw)
        return {
            "context": {
                "scene": self.context.current_scene,
                "temperature": self.context.temperature,
                "humidity": self.context.humidity,
                "occupancy": self.context.members_home,
                "hour": datetime.now().hour
            },
            "devices": devices,
            "storage_security": storage_status,
            "agent_init_metrics": deepcopy(agent_init_metrics),
            "startup_metrics": deepcopy(self._startup_metrics),
            "kb_status": self.kb.get_status() if self.kb else {
                "chromadb_importable": False,
                "chromadb_enabled": False,
                "collection_name": "",
                "persist_dir": "",
                "collection_count": 0,
                "memory_store_count": 0,
                "preset_count": 0,
            },
        }
    
    def _resolve_device(self, device_id: str) -> str:
        """将英文设备ID解析为中文设备名"""
        mapped = self._floor_plan_device_by_identifier(device_id)
        if mapped:
            return self._semantic_device_for_mapped_device(mapped)
        item = self._device_registry_map().get(device_id)
        return item["name"] if item else device_id
    
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

        if intent_info["route"] == "clarify":
            message = intent_info.get("reply_message") or intent_info.get("message") or "请问你想执行哪个具体操作？"
            self.session_store.update_clarification(message)
            return {
                "status": "clarification",
                "response_type": "clarification",
                "question": message,
                "response": message,
                "route": intent_info["route"],
                "route_reason": intent_info["reason"],
                "target": intent_info.get("target", ""),
                "normalized_query": normalized.to_dict(),
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
                print(
                    "[Privacy] 云端最小上下文字段: "
                    f"keys={sorted(cloud_context.keys())} "
                    f"bytes={len(json.dumps(cloud_context, ensure_ascii=False))}"
                )
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
                spatial_rejection = self._spatial_rejection_for_decision(decision, query_for_ai, route_info["route"])
                if spatial_rejection:
                    return {
                        **spatial_rejection,
                        "normalized_query": normalized.to_dict(),
                    }
                validation = self._validate_decision(decision)
                if not validation.valid:
                    message = "我暂时不能执行这个指令：" + ";".join(validation.errors)
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
                if validation.requires_confirmation:
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
                decision = validation.normalized_command
                
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


def _webagent_run_llm_first_query(self: HomeMindWebAgent, raw_text: str, normalized_text: str) -> dict:
    if not self.llm:
        return {"status": "no_action", "message": "AI 模块未加载", "_debug": {}}

    pre_unsupported = self._detect_unsupported_request(raw_text, normalized_text=normalized_text)
    if pre_unsupported:
        return {
            "status": "unsupported",
            "response_type": "clarification",
            "target": pre_unsupported["target"],
            "response": pre_unsupported["message"],
            "route": pre_unsupported["route"],
            "route_reason": pre_unsupported["reason"],
            "_debug": {"intent_plan": {"intent_type": "unsupported_or_ambiguous_command", "route": "unsupported"}},
        }

    intent_plan = self.llm.plan_intent(raw_text, normalized_query=normalized_text, context=self.context)
    if hasattr(self.llm, "_post_process_intent"):
        intent_plan = self.llm._post_process_intent(intent_plan, normalized_text or raw_text)
    self.last_route_info = {
        "intent_type": intent_plan.get("intent_type", ""),
        "route": intent_plan.get("route", ""),
        "reason": intent_plan.get("reasoning", ""),
    }

    if intent_plan["intent_type"] == "chat_reply":
        interaction = self._build_message_metadata(
            "decision",
            raw_text,
            normalized_text,
            decision_snapshot={"intent_type": intent_plan["intent_type"], "route": "reply"},
        )
        return {
            "status": "success",
            "response_type": "chat",
            "message_id": interaction["message_id"],
            "response": intent_plan.get("reply_message") or "你好，我在。",
            "route": "reply",
            "route_reason": intent_plan.get("reasoning", ""),
            "feedback_target": {"message_id": interaction["message_id"], "target_type": "decision"},
            "_debug": {"intent_plan": intent_plan},
        }

    if intent_plan["intent_type"] == "clarification_needed":
        rescued_intent = self._try_cloud_rescue_intent(raw_text, normalized_text)
        if rescued_intent is not None:
            intent_plan = rescued_intent
        else:
            message = intent_plan.get("reply_message") or "请问你是想控制设备、切换场景，还是创建定时任务？"
            self.session_store.update_clarification(message)
            return {
                "status": "clarification",
                "response_type": "clarification",
                "question": message,
                "route": "clarify",
                "route_reason": intent_plan.get("reasoning", ""),
                "_debug": {"intent_plan": intent_plan},
            }

    if intent_plan["intent_type"] == "chat_reply":
        interaction = self._build_message_metadata(
            "decision",
            raw_text,
            normalized_text,
            decision_snapshot={"intent_type": intent_plan["intent_type"], "route": "reply"},
        )
        return {
            "status": "success",
            "response_type": "chat",
            "message_id": interaction["message_id"],
            "response": intent_plan.get("reply_message") or "你好，我在。",
            "route": "reply",
            "route_reason": intent_plan.get("reasoning", ""),
            "feedback_target": {"message_id": interaction["message_id"], "target_type": "decision"},
            "_debug": {"intent_plan": intent_plan},
        }

    if intent_plan["intent_type"] == "automation_request":
        proposal = self._build_automation_proposal(raw_text, normalized_text)
        if proposal:
            return {
                "status": "success",
                "response_type": "automation_proposal",
                "message_id": proposal["message_id"],
                "response": proposal["summary"],
                "proposal": proposal,
                "route": "automation",
                "route_reason": intent_plan.get("reasoning", ""),
                "feedback_target": {"message_id": proposal["message_id"], "target_type": "automation_proposal"},
                "_debug": {"intent_plan": intent_plan},
            }
        message = "我理解到你想创建定时任务，但还缺少明确的时间或动作。你可以试试“晚上7:00打开空调”。"
        self.session_store.update_clarification(message)
        return {
            "status": "clarification",
            "response_type": "clarification",
            "question": message,
            "route": "automation",
            "route_reason": "automation_proposal_failed",
            "_debug": {"intent_plan": intent_plan},
        }

    goal_query = intent_plan.get("normalized_goal") or normalized_text or raw_text
    unsupported = self._detect_unsupported_request(raw_text, normalized_text=goal_query)
    if unsupported:
        return {
            "status": "unsupported",
            "response_type": "clarification",
            "target": unsupported["target"],
            "response": unsupported["message"],
            "route": unsupported["route"],
            "route_reason": unsupported["reason"],
            "_debug": {"intent_plan": intent_plan},
        }

    if not self.bsr or not self.lsr or not self.llm:
        return {"status": "no_action", "message": "AI 模块未加载", "_debug": {"intent_plan": intent_plan}}

    candidates = self.bsr.recall(goal_query, self.context)
    ranked = self.lsr.rank(
        goal_query,
        candidates,
        self.context,
        kb=self.kb,
        session_store=self.session_store,
    ) if candidates else []

    if not ranked:
        rescued = self._try_cloud_rescue_result(
            raw_text,
            normalized_text,
            goal_query,
            intent_plan,
            "cloud_rescue_no_ranked_candidates",
            candidates=candidates,
            ranked=[],
        )
        if rescued is not None:
            return rescued
        question = self.llm.ask_clarification(goal_query, candidates)
        self.session_store.update_clarification(question)
        return {
            "status": "clarification",
            "response_type": "clarification",
            "question": question,
            "candidates": [c.get("action", "") for c in candidates[:3]],
            "route": "clarify",
            "route_reason": "no_ranked_candidates",
            "_debug": {"intent_plan": intent_plan, "candidates": candidates, "ranked": ranked},
        }

    route_info = self.router.decide_route(
        raw_text,
        ranked,
        normalized_query=goal_query,
        cloud_available=self.llm.is_cloud_available(),
    )
    self.last_route_info = {
        **route_info,
        "intent_type": intent_plan.get("intent_type", ""),
        "intent_reason": intent_plan.get("reasoning", ""),
    }
    if route_info["route"] == "clarify":
        rescued = self._try_cloud_rescue_result(
            raw_text,
            normalized_text,
            goal_query,
            intent_plan,
            "cloud_rescue_from_clarify",
            candidates=candidates,
            ranked=ranked,
        )
        if rescued is not None:
            return rescued
        question = self.llm.ask_clarification(goal_query, ranked)
        self.session_store.update_clarification(question)
        return {
            "status": "clarification",
            "response_type": "clarification",
            "question": question,
            "candidates": route_info["top_candidates"],
            "route": route_info["route"],
            "route_reason": route_info["reason"],
            "_debug": {"intent_plan": intent_plan, "candidates": candidates, "ranked": ranked, "route_info": route_info},
        }

    cloud_context = self._build_cloud_context(ranked[:3])
    rag_context = self.kb.get_context_prompt(goal_query, self.context) if self.kb else ""
    print(
        "[Privacy] 云端最小上下文字段: "
        f"keys={sorted(cloud_context.keys())} "
        f"bytes={len(json.dumps(cloud_context, ensure_ascii=False))}"
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

    if decision.get("confidence", 0.0) < self.confidence_threshold:
        rescued = None
        if route_info["route"] != "cloud":
            rescued = self._try_cloud_rescue_result(
                raw_text,
                normalized_text,
                goal_query,
                intent_plan,
                "cloud_rescue_low_confidence",
                candidates=candidates,
                ranked=ranked,
            )
        if rescued is not None:
            return rescued
        question = self.llm.ask_clarification(goal_query, ranked)
        self.session_store.update_clarification(question)
        return {
            "status": "clarification",
            "response_type": "clarification",
            "question": question,
            "candidates": route_info["top_candidates"],
            "route": route_info["route"],
            "route_reason": route_info["reason"],
            "_debug": {
                "intent_plan": intent_plan,
                "candidates": candidates,
                "ranked": ranked,
                "route_info": route_info,
                "decision": decision,
            },
        }

    spatial_rejection = self._spatial_rejection_for_decision(
        decision,
        " ".join(part for part in [raw_text, normalized_text, goal_query] if part),
        route_info["route"],
    )
    if spatial_rejection:
        return {
            **spatial_rejection,
            "_debug": {
                "intent_plan": intent_plan,
                "candidates": candidates,
                "ranked": ranked,
                "route_info": route_info,
                "decision": decision,
            },
        }

    validation = self._validate_decision(decision)
    if not validation.valid:
        message = "我暂时不能执行这个指令：" + ";".join(validation.errors)
        self.session_store.update_clarification(message)
        return {
            "status": "clarification",
            "response_type": "clarification",
            "question": message,
            "candidates": route_info["top_candidates"],
            "route": route_info["route"],
            "route_reason": route_info["reason"],
            "_debug": {
                "intent_plan": intent_plan,
                "candidates": candidates,
                "ranked": ranked,
                "route_info": route_info,
                "decision": decision,
            },
        }
    if validation.requires_confirmation:
        message = "这个操作风险较高，需要二次确认后再执行。"
        self.session_store.update_clarification(message)
        return {
            "status": "clarification",
            "response_type": "clarification",
            "question": message,
            "candidates": route_info["top_candidates"],
            "route": route_info["route"],
            "route_reason": route_info["reason"],
            "_debug": {
                "intent_plan": intent_plan,
                "candidates": candidates,
                "ranked": ranked,
                "route_info": route_info,
                "decision": decision,
            },
        }

    decision = validation.normalized_command
    action_type = decision.get("action", "")
    device = decision.get("device", "")
    device_action = decision.get("device_action", "")
    scene = decision.get("scene", "")
    params = decision.get("params", {})

    debug_payload = {
        "intent_plan": intent_plan,
        "goal_query": goal_query,
        "candidates": candidates,
        "ranked": ranked,
        "route_info": route_info,
        "decision": decision,
    }

    if action_type == "设备控制" and device and device_action:
        execution = self._execute_device_with_spatial_gate(
            decision,
            text=" ".join(part for part in [raw_text, normalized_text, goal_query] if part),
            route=route_info["route"],
        )
        if execution.get("status") != "success":
            return {**execution, "_debug": debug_payload}
        message = execution.get("response", "")
        interaction = self._build_message_metadata(
            "execution",
            raw_text,
            normalized_text,
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
            "feedback_target": {"message_id": interaction["message_id"], "target_type": "execution"},
            "_debug": debug_payload,
        }

    if action_type == "场景切换" and scene:
        execution = self._execute_scene_with_spatial_gate(scene, route=route_info["route"])
        if execution.get("status") != "success":
            return {**execution, "_debug": debug_payload}
        message = execution.get("response", "")
        interaction = self._build_message_metadata(
            "execution",
            raw_text,
            normalized_text,
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
            "feedback_target": {"message_id": interaction["message_id"], "target_type": "execution"},
            "_debug": debug_payload,
        }

    self.session_store.update_clarification("我需要更多信息")
    return {
        "status": "clarification",
        "response_type": "clarification",
        "question": "我需要更多信息",
        "candidates": [r["action"] for r in ranked[:3]],
        "route": route_info["route"],
        "route_reason": route_info["reason"],
        "_debug": debug_payload,
    }


def _webagent_device_from_text(self: HomeMindWebAgent, text: str) -> str:
    aliases = {
        "\u7a7a\u8c03": ("\u7a7a\u8c03", "ac", "air conditioner"),
        "\u706f\u5149": ("\u706f", "\u706f\u5149", "light"),
        "\u7535\u89c6": ("\u7535\u89c6", "tv", "television"),
        "\u97f3\u54cd": ("\u97f3\u54cd", "\u5587\u53ed", "speaker"),
        "\u98ce\u6247": ("\u98ce\u6247", "fan"),
        "\u7a97\u6237": ("\u7a97\u6237", "\u7a97", "window"),
        "\u70ed\u6c34\u5668": ("\u70ed\u6c34\u5668",),
    }
    lowered = str(text or "").strip().lower()
    for device, tokens in aliases.items():
        if any(token in lowered for token in tokens):
            return device
    return ""


def _webagent_action_from_text(self: HomeMindWebAgent, text: str, device: str = "") -> tuple[str, dict]:
    value = str(text or "").strip().lower()
    if any(token in value for token in ("\u5173\u95ed", "\u5173\u6389", "\u5173\u4e86", "off")):
        return ("close", {}) if device == "\u7a97\u6237" else ("off", {})
    if any(token in value for token in ("\u6253\u5f00", "\u5f00\u542f", "\u6253\u5f00\u4e00\u4e0b", "\u5f00", "on")):
        params = {}
        if device == "\u7a7a\u8c03":
            params = {"temperature": 26}
        elif device == "\u706f\u5149":
            params = {"brightness": 100}
        elif device == "\u97f3\u54cd":
            params = {"volume": 30}
        elif device == "\u70ed\u6c34\u5668":
            params = {"temperature": 45}
        return ("open", {}) if device == "\u7a97\u6237" else ("on", params)
    if device == "\u7a7a\u8c03":
        if any(token in value for token in ("\u8c03\u9ad8", "\u6696", "\u70ed\u4e00\u70b9")):
            return "adjust", {"temperature": 28}
        if any(token in value for token in ("\u8c03\u4f4e", "\u51c9", "\u51b7\u4e00\u70b9")):
            return "adjust", {"temperature": 24}
    if device == "\u706f\u5149":
        if any(token in value for token in ("\u8c03\u4eae", "\u4eae\u4e00\u70b9")):
            return "adjust", {"brightness": 100}
        if any(token in value for token in ("\u8c03\u6697", "\u6697\u4e00\u70b9")):
            return "adjust", {"brightness": 30}
    return "", {}


def _webagent_execution_result(self: HomeMindWebAgent, command: dict, raw_text: str, normalized_text: str) -> dict:
    executed = self._execute_structured_command(command, route="local")
    if executed.get("status") == "unsupported":
        return {
            "status": "unsupported",
            "response_type": "clarification",
            "target": executed.get("target", command.get("device", "")),
            "response": executed.get("response", executed.get("message", "")),
            "route": executed.get("route", "unsupported"),
            "route_reason": executed.get("route_reason", "device_not_in_floor_plan"),
        }
    if executed.get("status") != "success":
        question = "\u8bf7\u95ee\u4f60\u60f3\u6253\u5f00\u3001\u5173\u95ed\u8fd8\u662f\u8c03\u8282\u8fd9\u4e2a\u8bbe\u5907\uff1f"
        self.session_store.update_clarification(question)
        return {"status": "clarification", "response_type": "clarification", "question": question, "route": "clarify", "route_reason": "pending_clarification_incomplete"}
    interaction = self._build_message_metadata(
        "execution",
        raw_text,
        normalized_text,
        decision_snapshot=command,
    )
    return {
        "status": "success",
        "response_type": "execution_result",
        "message_id": interaction["message_id"],
        "action": executed.get("action", ""),
        "response": executed.get("response", ""),
        "confidence": command.get("confidence", 0.95),
        "route": "local",
        "route_reason": "pending_clarification_resolved",
        "feedback_target": {"message_id": interaction["message_id"], "target_type": "execution"},
    }


def _webagent_try_cloud_rescue_intent(self: HomeMindWebAgent, raw_text: str, normalized_text: str) -> dict | None:
    if not self.llm or not self.llm.is_cloud_available():
        return None
    cloud_context = self._build_cloud_context([])
    rescued = self.llm.rescue_intent_with_cloud(
        raw_text,
        normalized_query=normalized_text,
        context=self.context,
        context_summary=cloud_context,
    )
    if rescued.get("intent_type") == "clarification_needed":
        return None
    return rescued


def _webagent_try_cloud_rescue_result(
    self: HomeMindWebAgent,
    raw_text: str,
    normalized_text: str,
    goal_query: str,
    intent_plan: dict,
    route_reason: str,
    candidates: list | None = None,
    ranked: list | None = None,
) -> dict | None:
    if not self.llm or not self.llm.is_cloud_available():
        return None
    ranked = list(ranked or [])
    candidates = list(candidates or [])
    cloud_context = self._build_cloud_context(ranked[:3])
    rag_context = self.kb.get_context_prompt(goal_query, self.context) if self.kb else ""
    decision = self.llm.rescue_decision_with_cloud(
        goal_query,
        self.context,
        rag_context=rag_context,
        context_summary=cloud_context,
        candidate_actions=[item.get("action", "") for item in ranked[:3]],
    )
    if decision.get("confidence", 0.0) < self.confidence_threshold:
        return None

    route_info = {
        "route": "cloud",
        "reason": route_reason,
        "top_candidates": [item.get("action", "") for item in ranked[:3] if item.get("action")],
    }
    debug_payload = {
        "intent_plan": intent_plan,
        "goal_query": goal_query,
        "candidates": candidates,
        "ranked": ranked,
        "route_info": route_info,
        "decision": decision,
    }

    spatial_rejection = self._spatial_rejection_for_decision(
        decision,
        " ".join(part for part in [raw_text, normalized_text, goal_query] if part),
        route_info["route"],
    )
    if spatial_rejection:
        return {**spatial_rejection, "_debug": debug_payload}

    validation = self._validate_decision(decision)
    if not validation.valid or validation.requires_confirmation:
        return None

    decision = validation.normalized_command
    debug_payload["decision"] = decision
    action_type = decision.get("action", "")
    device = decision.get("device", "")
    device_action = decision.get("device_action", "")
    scene = decision.get("scene", "")

    if action_type == "设备控制" and device and device_action:
        execution = self._execute_device_with_spatial_gate(
            decision,
            text=" ".join(part for part in [raw_text, normalized_text, goal_query] if part),
            route=route_info["route"],
        )
        if execution.get("status") != "success":
            return {**execution, "_debug": debug_payload}
        message = execution.get("response", "")
        interaction = self._build_message_metadata(
            "execution",
            raw_text,
            normalized_text,
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
            "feedback_target": {"message_id": interaction["message_id"], "target_type": "execution"},
            "_debug": debug_payload,
        }

    if action_type == "场景切换" and scene:
        execution = self._execute_scene_with_spatial_gate(scene, route=route_info["route"])
        if execution.get("status") != "success":
            return {**execution, "_debug": debug_payload}
        message = execution.get("response", "")
        interaction = self._build_message_metadata(
            "execution",
            raw_text,
            normalized_text,
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
            "feedback_target": {"message_id": interaction["message_id"], "target_type": "execution"},
            "_debug": debug_payload,
        }

    return None


def _webagent_resolve_pending_clarification(self: HomeMindWebAgent, raw_text: str, normalized_text: str) -> dict | None:
    pending = self.session_store.get_pending_clarification()
    if not pending:
        return None

    payload = dict(pending.get("payload", {}) or {})
    text = str(normalized_text or raw_text or "").strip()
    device = self._device_from_text(text) or payload.get("device", "")
    action, params = self._action_from_text(text, device)

    if device and action:
        self.session_store.clear_pending_clarification()
        command = {
            "action": "\u8bbe\u5907\u63a7\u5236",
            "device": device,
            "scene": "",
            "device_action": action,
            "params": params,
            "confidence": 0.95,
            "reasoning": "pending clarification resolved by follow-up",
        }
        return self._execution_result(command, raw_text, normalized_text)

    if device:
        question = f"\u4f60\u60f3\u5bf9{device}\u6267\u884c\u6253\u5f00\u3001\u5173\u95ed\u8fd8\u662f\u8c03\u8282\uff1f"
        self.session_store.update_clarification(question)
        self.session_store.set_pending_clarification("device_action", {"device": device})
        return {
            "status": "clarification",
            "response_type": "clarification",
            "question": question,
            "candidates": [f"\u6253\u5f00{device}", f"\u5173\u95ed{device}"],
            "route": "clarify",
            "route_reason": "pending_clarification_device_only",
        }

    return None


def _webagent_process_query(self: HomeMindWebAgent, query: str) -> dict:
    self.context.hour = datetime.now().hour
    normalized = self.language_normalizer.normalize(query)
    query_for_ai = normalized.normalized or query
    self._record_query_context(query, query_for_ai)
    safety = self.router.detect_safety_sensitive_request(query, normalized_query=query_for_ai)
    if safety:
        self.session_store.clear_pending_clarification()
        self.session_store.clear_pending_confirmation()
        message = safety.get("reply_message") or safety.get("message") or "这个请求涉及家庭安全设备，请先确认具体设备和安全状态。"
        self.session_store.update_clarification(message)
        return {
            "status": "clarification",
            "response_type": "clarification",
            "question": message,
            "response": message,
            "route": "clarify",
            "route_reason": safety.get("reason", "safety_sensitive_target"),
            "target": safety.get("target", ""),
            "normalized_query": normalized.to_dict(),
        }
    automation_update = self._update_pending_automation_trigger(query, query_for_ai)
    if automation_update is not None:
        return {
            "status": "success",
            "response_type": "automation_proposal",
            "message_id": automation_update["message_id"],
            "response": automation_update["summary"],
            "proposal": automation_update,
            "route": "automation",
            "route_reason": "pending_automation_trigger_updated",
            "normalized_query": normalized.to_dict(),
            "feedback_target": {"message_id": automation_update["message_id"], "target_type": "automation_proposal"},
        }
    pending_result = self._resolve_pending_clarification(query, query_for_ai)
    if pending_result is not None:
        pending_result["normalized_query"] = normalized.to_dict()
        return pending_result
    result = self._run_llm_first_query(query, query_for_ai)
    debug = result.pop("_debug", None)
    result["normalized_query"] = normalized.to_dict()
    if result.get("status") == "clarification":
        self.session_store.set_pending_clarification(
            "query",
            {
                "query": query,
                "normalized": query_for_ai,
                "candidates": result.get("candidates", []),
            },
        )
    if debug:
        self.last_route_info = self.last_route_info or {}
    return result


def _webagent_process_user_input(self: HomeMindWebAgent, data: dict):
    user_text = data.get("text", "").strip()
    if not user_text:
        return

    normalized = self.language_normalizer.normalize(user_text)
    query_text = normalized.normalized or user_text
    self._record_query_context(user_text, query_text)
    query_id = f"q_{int(time.time() * 1000)}"
    print(f"[Agent] 收到用户输入: {user_text}")
    self.context.hour = datetime.now().hour

    safety = self.router.detect_safety_sensitive_request(user_text, normalized_query=query_text)
    if safety:
        self.session_store.clear_pending_clarification()
        self.session_store.clear_pending_confirmation()
        message = safety.get("reply_message") or safety.get("message") or "这个请求涉及家庭安全设备，请先确认具体设备和安全状态。"
        self.session_store.update_clarification(message)
        socketio.emit("message", {
            "type": "agent_clarification",
            "data": {
                "question": message,
                "candidates": [],
                "query_id": query_id,
                "route": "clarify",
                "route_reason": safety.get("reason", "safety_sensitive_target"),
                "target": safety.get("target", ""),
            },
        })
        return

    pipeline = {
        "query_id": query_id,
        "query": user_text,
        "normalized_query": normalized.to_dict(),
        "steps": {
            "bsr": {"status": "pending", "candidates": []},
            "lsr": {"status": "pending", "ranked": []},
            "llm": {"status": "pending", "decision": None},
            "exec": {"status": "pending", "result": None},
        },
    }
    socketio.emit("pipeline_update", {"type": "pipeline_start", "data": pipeline})

    automation_update = self._update_pending_automation_trigger(user_text, query_text)
    if automation_update is not None:
        result = {
            "status": "success",
            "response_type": "automation_proposal",
            "message_id": automation_update["message_id"],
            "response": automation_update["summary"],
            "proposal": automation_update,
            "route": "automation",
            "route_reason": "pending_automation_trigger_updated",
            "feedback_target": {"message_id": automation_update["message_id"], "target_type": "automation_proposal"},
            "_debug": {"intent_plan": {"intent_type": "automation_request", "route": "automation"}},
        }
    else:
        result = self._resolve_pending_clarification(user_text, query_text)
    if result is None:
        result = self._run_llm_first_query(user_text, query_text)
    debug = result.pop("_debug", {}) or {}
    if result.get("status") == "clarification":
        self.session_store.set_pending_clarification(
            "query",
            {
                "query": user_text,
                "normalized": query_text,
                "candidates": result.get("candidates", []),
            },
        )
    intent_plan = debug.get("intent_plan", {})

    pipeline["steps"]["llm"] = {
        "status": "done",
        "decision": {
            "intent_type": intent_plan.get("intent_type", ""),
            "normalized_goal": intent_plan.get("normalized_goal", ""),
            "confidence": float(intent_plan.get("decision_confidence", 0.0)),
            "reasoning": intent_plan.get("reasoning", ""),
        },
    }
    socketio.emit("pipeline_update", {"type": "pipeline_step", "data": {
        "query_id": query_id, "step": "llm", "data": pipeline["steps"]["llm"]
    }})

    candidates = debug.get("candidates", [])
    if candidates:
        pipeline["steps"]["bsr"] = {
            "status": "done",
            "candidates": [
                {"id": i, "action": c.get("action", ""), "score": float(c.get("score", 0))}
                for i, c in enumerate(candidates)
            ],
        }
        socketio.emit("pipeline_update", {"type": "pipeline_step", "data": {
            "query_id": query_id, "step": "bsr", "data": pipeline["steps"]["bsr"]
        }})

    ranked = debug.get("ranked", [])
    if ranked:
        pipeline["steps"]["lsr"] = {
            "status": "done",
            "ranked": [
                {"id": i, "action": r.get("action", ""), "score": float(r.get("final_score", 0))}
                for i, r in enumerate(ranked)
            ],
        }
        socketio.emit("pipeline_update", {"type": "pipeline_step", "data": {
            "query_id": query_id, "step": "lsr", "data": pipeline["steps"]["lsr"]
        }})

    pipeline["steps"]["exec"] = {"status": "done", "result": result}
    socketio.emit("pipeline_update", {"type": "pipeline_step", "data": {
        "query_id": query_id, "step": "exec", "data": pipeline["steps"]["exec"]
    }})

    result["query_id"] = query_id
    if result["status"] == "clarification":
        socketio.emit("message", {
            "type": "agent_clarification",
            "data": {
                "question": result.get("question", result.get("response", "我需要更多信息")),
                "candidates": result.get("candidates", []),
                "query_id": query_id,
                "route": result.get("route", ""),
                "route_reason": result.get("route_reason", ""),
            },
        })
        return

    socketio.emit("message", {
        "type": "agent_response",
        "data": {
            "action": result.get("action", result.get("response_type", "")),
            "result": result.get("response", result.get("question", result.get("message", ""))),
            "status": result.get("status", "success"),
            "response_type": result.get("response_type", ""),
            "message_id": result.get("message_id", ""),
            "proposal": result.get("proposal"),
            "target": result.get("target", ""),
            "feedback_target": result.get("feedback_target"),
            "query_id": query_id,
            "route": result.get("route", ""),
            "route_reason": result.get("route_reason", ""),
        },
    })

def _webagent_build_identity_context(self: HomeMindWebAgent, route: str = "local"):
    runtime = self.session_store.get_runtime_context()
    return self.runtime_security.identity_manager.issue(
        user_id=runtime.get("user_id", "default"),
        session_id=runtime.get("last_updated_at", "") or self.instance_id,
        metadata={"route": route, "mode": "simulated", "channel": "web"},
    )


def _webagent_audit_event(
    self: HomeMindWebAgent,
    *,
    trace_id: str,
    query: str,
    route: str,
    routing_reason: str,
    decision: dict | None = None,
    validation=None,
    execution_result: str = "",
    error: str = "",
    started_at: float = 0.0,
) -> None:
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
        validation={
            "valid": getattr(validation, "valid", True),
            "errors": list(getattr(validation, "errors", []) or []),
        },
        error=error,
        llm_backend=getattr(self.llm, "backend", ""),
        session_id=self.instance_id,
    )
    self.metrics.record_latency(latency_ms)
    if error:
        self.metrics.record_error()


def _webagent_execute_registered_command(
    self: HomeMindWebAgent,
    command: dict,
    route: str = "local",
    spatial: dict | None = None,
) -> dict:
    action = str(command.get("action", "")).strip()
    if action == "场景切换":
        return self._execute_scene_with_spatial_gate(command.get("scene", ""), route=route)

    execution = self.transaction_manager.execute(command)
    if execution.get("status") == "success" and action == "设备控制":
        target_names = [target.get("name") or target.get("id") for target in (spatial or {}).get("targets", [])]
        if target_names:
            execution["response"] = f"{execution.get('response', '')}映射设备：{', '.join(target_names)}。"
        self.session_store.update_from_decision(command, route=route, result=execution.get("response", ""))
        self.preference_store.record_action_accept(command, self.context)
        if self.kb:
            self.kb.record_timeseries_summary(
                self.context,
                self.device_control.get_all_state(),
                trigger=command.get("action", ""),
                route=route,
            )
    elif execution.get("status") == "success" and action == "信息查询":
        self.session_store.update_from_decision(command, route=route, result=execution.get("response", ""))
    self.runtime_security.record_outcome(command, success=execution.get("status") == "success", confirmed=False)
    return execution


def _webagent_execute_structured_command_v2(self: HomeMindWebAgent, command: dict, route: str = "tap") -> dict:
    validation = self._validate_decision(command)
    if not validation.valid:
        return {"status": "invalid", "errors": validation.errors, "command": command}

    normalized = validation.normalized_command
    identity = self._build_identity_context(route=route)
    guard = self.runtime_security.evaluate(
        normalized,
        validation,
        identity,
        runtime_context={"hour": self.context.hour, "route": route},
    )
    if not guard.get("allowed", False):
        return {"status": "invalid", "errors": [guard.get("reason", "runtime_guard_denied")], "command": normalized}
    if guard.get("effect") == "confirm":
        return {"status": "confirmation_required", "command": normalized}

    if normalized.get("action") == "设备控制":
        spatial = self._validate_spatial_device_command(normalized)
        if not spatial.get("valid"):
            self.session_store.update_clarification(spatial["message"])
            return {
                "status": "unsupported",
                "response_type": "clarification",
                "action": "unsupported",
                "target": normalized.get("device", ""),
                "response": spatial["message"],
                "message": spatial["message"],
                "route": "unsupported",
                "route_reason": spatial["reason"],
                "spatial": spatial,
            }
        execution = self._execute_registered_command(normalized, route=route, spatial=spatial)
    else:
        execution = self._execute_registered_command(normalized, route=route)

    if execution.get("status") == "success":
        self.runtime_security.record_outcome(normalized, success=True, confirmed=False)
    else:
        self.runtime_security.record_outcome(normalized, success=False, confirmed=False)
    execution["command"] = normalized
    return execution


def _webagent_run_llm_first_query_v2(self: HomeMindWebAgent, raw_text: str, normalized_text: str) -> dict:
    trace_id = f"web_{int(time.time() * 1000)}"
    started_at = time.time()

    injection = self.injection_detector.check_and_log(raw_text)
    if injection.detected:
        self.session_store.update_clarification(injection.message)
        self._audit_event(
            trace_id=trace_id,
            query=raw_text,
            route="clarify",
            routing_reason=injection.pattern or "prompt_injection",
            execution_result=injection.message,
            error="prompt_injection_detected",
            started_at=started_at,
        )
        return {
            "status": "clarification",
            "response_type": "clarification",
            "question": injection.message,
            "response": injection.message,
            "route": "clarify",
            "route_reason": injection.pattern or "prompt_injection",
        }

    result = _webagent_run_llm_first_query(self, raw_text, normalized_text)
    route = result.get("route", "")
    reason = result.get("route_reason", "")

    decision = None
    if result.get("_debug"):
        decision = dict((result.get("_debug") or {}).get("decision", {}) or {})

    self._audit_event(
        trace_id=trace_id,
        query=raw_text,
        route=route or "local",
        routing_reason=reason,
        decision=decision,
        execution_result=result.get("response") or result.get("question", ""),
        started_at=started_at,
    )
    return result


def _webagent_process_query_v2(self: HomeMindWebAgent, query: str) -> dict:
    self.context.hour = datetime.now().hour
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
    normalized = self.language_normalizer.normalize(query)
    query_for_ai = normalized.normalized or query
    self._record_query_context(query, query_for_ai)

    safety = self.router.detect_safety_sensitive_request(query, normalized_query=query_for_ai)
    if safety:
        self.session_store.clear_pending_clarification()
        self.session_store.clear_pending_confirmation()
        message = safety.get("reply_message") or safety.get("message") or "这个请求涉及家庭安全设备，请先确认具体设备和安全状态。"
        self.session_store.update_clarification(message)
        return {
            "status": "clarification",
            "response_type": "clarification",
            "question": message,
            "response": message,
            "route": "clarify",
            "route_reason": safety.get("reason", "safety_sensitive_target"),
            "target": safety.get("target", ""),
            "normalized_query": normalized.to_dict(),
        }

    automation_update = self._update_pending_automation_trigger(query, query_for_ai)
    if automation_update is not None:
        return {
            "status": "success",
            "response_type": "automation_proposal",
            "message_id": automation_update["message_id"],
            "response": automation_update["summary"],
            "proposal": automation_update,
            "route": "automation",
            "route_reason": "pending_automation_trigger_updated",
            "normalized_query": normalized.to_dict(),
            "feedback_target": {"message_id": automation_update["message_id"], "target_type": "automation_proposal"},
        }

    pending_result = self._resolve_pending_clarification(query, query_for_ai)
    if pending_result is not None:
        pending_result["normalized_query"] = normalized.to_dict()
        return pending_result

    result = self._run_llm_first_query(query, query_for_ai)
    result["normalized_query"] = normalized.to_dict()
    if result.get("status") == "clarification":
        self.session_store.set_pending_clarification(
            "query",
            {
                "query": query,
                "normalized": query_for_ai,
                "candidates": result.get("candidates", []),
            },
        )
    return result


def _webagent_execute_device_with_spatial_gate_v2(self: HomeMindWebAgent, command: dict, text: str, route: str) -> dict:
    spatial = self._validate_spatial_device_command(command, text=text)
    if not spatial["valid"]:
        self.session_store.update_clarification(spatial["message"])
        return {
            "status": "unsupported",
            "response_type": "clarification",
            "action": "unsupported",
            "target": command.get("device", ""),
            "response": spatial["message"],
            "message": spatial["message"],
            "route": "unsupported",
            "route_reason": spatial["reason"],
            "spatial": spatial,
        }
    validation = self._validate_decision(command)
    identity = self._build_identity_context(route=route)
    guard = self.runtime_security.evaluate(
        validation.normalized_command,
        validation,
        identity,
        runtime_context={"hour": self.context.hour, "route": route},
    )
    if not guard.get("allowed", False):
        message = "我暂时不能执行这个指令：" + guard.get("reason", "runtime_guard_denied")
        self.session_store.update_clarification(message)
        return {
            "status": "clarification",
            "response_type": "clarification",
            "action": "clarification",
            "response": message,
            "message": message,
            "route": route,
            "route_reason": guard.get("reason", ""),
        }
    if guard.get("effect") == "confirm":
        message = "这个操作需要确认后再执行。"
        self.session_store.update_clarification(message)
        return {
            "status": "clarification",
            "response_type": "clarification",
            "action": "clarification",
            "response": message,
            "message": message,
            "route": route,
            "route_reason": guard.get("reason", ""),
        }
    return self._execute_registered_command(command, route=route, spatial=spatial)


def _webagent_execute_scene_with_spatial_gate_v2(self: HomeMindWebAgent, scene: str, route: str = "local") -> dict:
    config = self.scene_store.get_scene(scene)
    if config is None:
        message = f"不支持的场景: {scene}"
        self.session_store.update_clarification(message)
        return {"status": "unsupported", "response": message, "message": message, "route_reason": "scene_not_found"}

    scene_decision = {
        "action": "场景切换",
        "device": "",
        "scene": scene,
        "device_action": "",
        "params": {},
        "confidence": 1.0,
        "reasoning": "scene execution with spatial gate",
    }
    scene_validation = self._validate_decision(scene_decision)
    scene_identity = self._build_identity_context(route=route)
    scene_guard = self.runtime_security.evaluate(
        scene_validation.normalized_command,
        scene_validation,
        scene_identity,
        runtime_context={"hour": self.context.hour, "route": route},
    )
    if not scene_guard.get("allowed", False):
        message = "我暂时不能执行这个指令：" + scene_guard.get("reason", "runtime_guard_denied")
        self.session_store.update_clarification(message)
        return {"status": "clarification", "response": message, "message": message, "route_reason": scene_guard.get("reason", "")}
    if scene_guard.get("effect") == "confirm":
        message = "这个操作需要确认后再执行。"
        self.session_store.update_clarification(message)
        return {"status": "clarification", "response": message, "message": message, "route_reason": scene_guard.get("reason", "")}

    snapshot = self.transaction_manager._snapshot()
    executed = []
    skipped = []
    for device, cmd in config.items():
        command = {
            "action": "设备控制",
            "device": device,
            "scene": "",
            "device_action": cmd.get("action", ""),
            "params": cmd.get("params", {}),
            "confidence": 1.0,
            "reasoning": f"scene {scene}",
        }
        spatial = self._validate_spatial_device_command(command)
        if not spatial["valid"]:
            skipped.append(device)
            continue
        execution = self._execute_registered_command(command, route=route, spatial=spatial)
        if execution.get("status") != "success":
            self.transaction_manager._restore(snapshot)
            return execution
        executed.append(execution.get("response", ""))

    if not executed:
        message = f"当前 SVG 户型图的设备表不支持执行“{scene}”中的任何设备动作，已拦截场景切换。"
        self.session_store.update_clarification(message)
        self.runtime_security.record_outcome(scene_decision, success=False, confirmed=False)
        return {
            "status": "unsupported",
            "response": message,
            "message": message,
            "route_reason": "scene_devices_not_in_floor_plan",
            "skipped_devices": skipped,
        }

    self.context.current_scene = scene
    self.context.last_scene = SCENE_INDEX_MAP.get(scene, -1)
    self.session_store.update_scene(scene)
    message = f"已切换到{scene}。" + " ".join(executed)
    if skipped:
        message += f"未在户型设备表中找到的设备已跳过：{', '.join(skipped)}。"
    self.session_store.update_from_decision(scene_decision, route=route, result=message)
    self.preference_store.record_action_accept(scene_decision, self.context)
    self.runtime_security.record_outcome(scene_decision, success=True, confirmed=False)
    if self.kb:
        self.kb.record_timeseries_summary(
            self.context,
            self.device_control.get_all_state(),
            trigger=scene_decision.get("action", ""),
            route=route,
        )
    return {
        "status": "success",
        "response_type": "execution_result",
        "action": "scene_switch",
        "scene": scene,
        "response": message,
        "message": message,
        "skipped_devices": skipped,
    }


HomeMindWebAgent._build_identity_context = _webagent_build_identity_context
HomeMindWebAgent._audit_event = _webagent_audit_event
HomeMindWebAgent._execute_registered_command = _webagent_execute_registered_command
HomeMindWebAgent._execute_device_with_spatial_gate = _webagent_execute_device_with_spatial_gate_v2
HomeMindWebAgent._execute_scene_with_spatial_gate = _webagent_execute_scene_with_spatial_gate_v2
HomeMindWebAgent._execute_structured_command = _webagent_execute_structured_command_v2
HomeMindWebAgent._run_llm_first_query = _webagent_run_llm_first_query_v2
HomeMindWebAgent._device_from_text = _webagent_device_from_text
HomeMindWebAgent._action_from_text = _webagent_action_from_text
HomeMindWebAgent._execution_result = _webagent_execution_result
HomeMindWebAgent._try_cloud_rescue_intent = _webagent_try_cloud_rescue_intent
HomeMindWebAgent._try_cloud_rescue_result = _webagent_try_cloud_rescue_result
HomeMindWebAgent._resolve_pending_clarification = _webagent_resolve_pending_clarification
HomeMindWebAgent.process_query = _webagent_process_query_v2
HomeMindWebAgent._process_user_input = _webagent_process_user_input


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


@app.route("/api/audit/logs", methods=["GET"])
def audit_logs():
    """Audit retrieval endpoint."""
    if not agent:
        return jsonify({"error": "Agent æœªåˆå§‹åŒ–"}), 500
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        limit = 50
    user_id = str(request.args.get("user_id", "") or "").strip() or None
    since_raw = str(request.args.get("since", "") or "").strip()
    until_raw = str(request.args.get("until", "") or "").strip()
    since = datetime.fromisoformat(since_raw) if since_raw else None
    until = datetime.fromisoformat(until_raw) if until_raw else None
    records = agent.audit_logger.query(since=since, until=until, user_id=user_id, limit=max(1, min(limit, 200)))
    integrity = agent.audit_logger.verify_integrity() if hasattr(agent.audit_logger, "verify_integrity") else {}
    security = agent.audit_logger.status() if hasattr(agent.audit_logger, "status") else {}
    return jsonify({"status": "success", "records": records, "count": len(records), "integrity": integrity, "security": security})


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


@app.route("/api/devices", methods=["GET"])
def list_devices():
    """Device switch registry and current states."""
    if not agent:
        return jsonify({"error": "Agent æœªåˆå§‹åŒ–"}), 500
    return jsonify({"status": "success", "devices": agent.list_devices()})


@app.route("/api/devices", methods=["POST"])
def create_device():
    """Create a switchable device."""
    data = request.get_json(silent=True) or {}
    if not agent:
        return jsonify({"error": "Agent æœªåˆå§‹åŒ–"}), 500
    try:
        device = agent.create_device(data)
        return jsonify({"status": "success", "device": {**device, "state": agent._get_device_state(device["id"])}})
    except KeyError as exc:
        return jsonify({"status": "error", "error": str(exc).strip("'")}), 409
    except ValueError as exc:
        return jsonify({"status": "error", "error": str(exc)}), 400


@app.route("/api/devices/<device>", methods=["PUT"])
def update_device(device):
    """Update switchable device metadata."""
    data = request.get_json(silent=True) or {}
    if not agent:
        return jsonify({"error": "Agent æœªåˆå§‹åŒ–"}), 500
    try:
        updated = agent.update_device(device, data)
        return jsonify({"status": "success", "device": {**updated, "state": agent._get_device_state(updated["id"])}})
    except LookupError as exc:
        return jsonify({"status": "error", "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"status": "error", "error": str(exc)}), 400


@app.route("/api/devices/<device>", methods=["DELETE"])
def delete_device(device):
    """Delete a switchable device."""
    if not agent:
        return jsonify({"error": "Agent æœªåˆå§‹åŒ–"}), 500
    try:
        removed = agent.delete_device(device)
        return jsonify({"status": "success", "device": removed})
    except LookupError as exc:
        return jsonify({"status": "error", "error": str(exc)}), 404


@app.route("/api/devices/<device>/control", methods=["POST"])
def control_device(device):
    """设备控制接口"""
    data = request.get_json()
    action = data.get("action", "on")
    params = data.get("params", {})
    
    if agent:
        if not agent._has_device_identifier(device):
            return jsonify({"status": "error", "error": "device not found"}), 404
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
        names = agent.scene_store.list_scenes()
        items = [
            {
                "id": agent._scene_id_from_name(name),
                "name": name,
                "config": agent.scene_store.get_scene(name) or {},
            }
            for name in names
        ]
        return jsonify({"status": "success", "scenes": names, "items": items})
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/scenes", methods=["POST"])
def create_scene():
    """场景创建接口"""
    data = request.get_json(silent=True) or {}
    if not data.get("name"):
        return jsonify({"status": "error", "error": "name is required"}), 400
    if agent:
        try:
            config = data.get("config", {})
            if not isinstance(config, dict):
                return jsonify({"status": "error", "error": "config must be an object"}), 400
            scene = agent.scene_store.add_scene(data["name"], config)
        except ValueError as exc:
            return jsonify({"status": "error", "error": str(exc)}), 400
        return jsonify({"status": "success", "name": data["name"], "scene": scene})
    return jsonify({"error": "Agent 未初始化"}), 500


@app.route("/api/scenes/from-nl", methods=["POST"])
def create_scene_from_nl():
    """自然语言创建场景接口"""
    data = request.get_json(silent=True) or {}
    if agent:
        text = str(data.get("text", "")).strip()
        if not text:
            return jsonify({"status": "error", "error": "text is required"}), 400
        draft = agent._build_scene_draft(text)
        if not draft:
            return jsonify({"status": "error", "error": "cannot parse scene draft"}), 400
        if data.get("save"):
            if not draft.get("validation", {}).get("valid"):
                return jsonify({"status": "error", "error": "scene draft validation failed", "validation": draft.get("validation", {})}), 400
            scene = agent.scene_store.add_scene(draft["name"], draft["config"])
            return jsonify({
                "status": "success",
                "response_type": "scene_saved",
                "name": draft["name"],
                "scene": scene,
                "config": scene,
                "validation": draft.get("validation", {}),
                "source": draft.get("source", "local"),
            })
        return jsonify({
            "status": "success",
            "response_type": "scene_draft",
            "name": draft["name"],
            "config": draft["config"],
            "validation": draft.get("validation", {}),
            "source": draft.get("source", "local"),
        })
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
        config = data.get("config", {})
        if not isinstance(config, dict):
            return jsonify({"status": "error", "error": "config must be an object"}), 400
        scene = agent.scene_store.update_scene(scene_name, config)
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
        result_payload = agent._execute_scene_with_spatial_gate(scene_name, route="local")
        status_code = 200 if result_payload.get("status") == "success" else 404
        return jsonify({
            "status": result_payload.get("status", "success"),
            "scene": scene,
            "result": result_payload.get("response", result_payload.get("message", "")),
            "skipped_devices": result_payload.get("skipped_devices", []),
            "devices": agent.get_all_states()["devices"]
        }), status_code
    
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
            recommendation = {
                "id": f"dqn_{action_idx}",
                "scene": recommended_scene,
                "scene_name": scene_name,
                "reason": f"DQN recommends {scene_name} from current context",
                "confidence": confidence,
            }
            agent._record_dqn_recommendation_memory(recommendation, action_idx, source="api")
            return jsonify({"status": "success", "recommendation": recommendation})
            
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
    
    if agent and agent.dqn:
        action = agent._parse_dqn_action(rec_id)
        return jsonify(agent._record_dqn_feedback(action, response, source="api"))

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
        text = str(data.get("text", "")).strip()
        if not text:
            return jsonify({"status": "error", "error": "text is required"}), 400
        rule_data = agent._draft_tap_rule(text, text)
        if not rule_data:
            return jsonify({"status": "error", "error": "cannot parse TAP rule draft"}), 400
        validation = agent._validate_tap_rule_draft(rule_data)
        rule_payload = {
            "name": rule_data.get("name", text[:40]),
            "enabled": True,
            "trigger": dict(rule_data.get("trigger", {}) or {}),
            "conditions": list(rule_data.get("conditions", []) or []),
            "action": dict(rule_data.get("action", {}) or {}),
            "priority": int(rule_data.get("priority", 50)),
        }
        if data.get("save"):
            if not validation.get("valid"):
                return jsonify({"status": "error", "error": "rule draft validation failed", "validation": validation}), 400
            rule = agent.tap_rule_store.add_rule(rule_payload)
            return jsonify({
                "status": "success",
                "response_type": "tap_rule_saved",
                "rule": rule,
                "validation": validation,
                "source": rule_data.get("source", "local"),
            })
        return jsonify({
            "status": "success",
            "response_type": "tap_rule_draft",
            "rule": rule_payload,
            "validation": validation,
            "source": rule_data.get("source", "local"),
        })
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


@app.route("/api/floor-plans", methods=["GET"])
def list_floor_plans():
    """List uploaded SVG floor plans with metadata."""
    plans = _read_json_list(FLOOR_PLAN_STORE_PATH)
    return jsonify({"status": "success", "success": True, "floorPlans": plans, "files": plans})


@app.route("/api/floor-plans", methods=["POST"])
def upload_floor_plan():
    """Upload a validated SVG floor plan and persist metadata."""
    uploaded = request.files.get("floorPlan") or request.files.get("svg") or request.files.get("file")
    if not uploaded:
        return jsonify({"status": "error", "error": "svg file is required"}), 400

    result = _save_floor_plan_svg(
        uploaded,
        name=request.form.get("name", ""),
        description=request.form.get("description", ""),
    )
    if result.get("status") != "success":
        return jsonify(result), 400
    return jsonify({**result, "success": True})


@app.route("/api/floor-plans/<plan_id>", methods=["GET"])
def get_floor_plan(plan_id):
    plan = _find_floor_plan(plan_id)
    if not plan:
        return jsonify({"status": "error", "error": "floor plan not found"}), 404
    return jsonify({"status": "success", "success": True, "floorPlan": plan})


@app.route("/api/floor-plans/<plan_id>", methods=["PUT"])
def update_floor_plan(plan_id):
    plans = _read_json_list(FLOOR_PLAN_STORE_PATH)
    for index, plan in enumerate(plans):
        if plan.get("id") != plan_id:
            continue
        data = request.get_json(silent=True) or {}
        updated = dict(plan)
        for key in ("name", "description"):
            if key in data:
                updated[key] = str(data.get(key, "") or "").strip()
        updated["updatedAt"] = datetime.now().astimezone().isoformat()
        plans[index] = updated
        _write_json_list(FLOOR_PLAN_STORE_PATH, plans)
        return jsonify({"status": "success", "success": True, "floorPlan": updated})
    return jsonify({"status": "error", "error": "floor plan not found"}), 404


@app.route("/api/floor-plans/<plan_id>/activate", methods=["POST"])
def activate_floor_plan(plan_id):
    plan = _set_active_floor_plan(plan_id)
    if not plan:
        return jsonify({"status": "error", "error": "floor plan not found"}), 404
    return jsonify({"status": "success", "success": True, "floorPlan": plan})


@app.route("/api/floor-plans/<plan_id>", methods=["DELETE"])
def delete_floor_plan(plan_id):
    plans = _read_json_list(FLOOR_PLAN_STORE_PATH)
    kept = []
    removed = None
    for plan in plans:
        if plan.get("id") == plan_id:
            removed = plan
        else:
            kept.append(plan)
    if not removed:
        return jsonify({"status": "error", "error": "floor plan not found"}), 404

    _write_json_list(FLOOR_PLAN_STORE_PATH, kept)
    file_path = Path(removed.get("filePath", ""))
    try:
        if file_path.exists() and file_path.resolve().parent == FLOOR_PLAN_UPLOAD_DIR.resolve():
            file_path.unlink()
    except Exception:
        pass

    mappings = [item for item in _read_json_list(FLOOR_PLAN_DEVICE_STORE_PATH) if item.get("floorPlanId") != plan_id]
    _write_json_list(FLOOR_PLAN_DEVICE_STORE_PATH, mappings)
    return jsonify({"status": "success", "success": True, "message": "floor plan deleted"})


@app.route("/api/floor-plans/<plan_id>/svg", methods=["GET"])
def get_floor_plan_svg(plan_id):
    plan = _find_floor_plan(plan_id)
    if not plan:
        return jsonify({"status": "error", "error": "floor plan not found"}), 404
    file_path = Path(plan.get("filePath", ""))
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception:
        return jsonify({"status": "error", "error": "svg file not found"}), 404
    return app.response_class(content, mimetype="image/svg+xml")


@app.route("/api/floor-plans/<plan_id>/devices", methods=["GET"])
def get_floor_plan_devices(plan_id):
    mapping = next((item for item in _read_json_list(FLOOR_PLAN_DEVICE_STORE_PATH) if item.get("floorPlanId") == plan_id), None)
    if not mapping:
        return jsonify({"status": "success", "success": True, "deviceMapping": None, "devices": []})
    return jsonify({"status": "success", "success": True, "deviceMapping": mapping, "devices": mapping.get("devices", [])})


@app.route("/api/floor-plans/<plan_id>/devices", methods=["POST"])
def save_floor_plan_devices(plan_id):
    plan = _find_floor_plan(plan_id)
    if not plan:
        return jsonify({"status": "error", "error": "floor plan not found"}), 404
    data = request.get_json(silent=True) or {}
    raw_input = data.get("devices") if "devices" in data else data.get("deviceMapping")
    ok, error, tuples = _normalize_device_mapping_to_tuples(raw_input)
    if not ok:
        return jsonify({"status": "error", "error": error}), 400
    nested_mapping = raw_input if isinstance(raw_input, dict) else {}
    custom_rooms = data.get("customRooms") if isinstance(data.get("customRooms"), dict) else nested_mapping.get("customRooms")
    custom_rooms = custom_rooms if isinstance(custom_rooms, dict) else None
    area_names = _area_names_from_payload(data, tuples)
    devices = _compute_device_positions(tuples, plan, custom_rooms=custom_rooms)
    for device in devices:
        if not device.get("areaName"):
            device["areaName"] = area_names.get(device.get("area", ""), "")
    mappings = _read_json_list(FLOOR_PLAN_DEVICE_STORE_PATH)
    existing = next((item for item in mappings if item.get("floorPlanId") == plan_id), None)
    entry = {
        "floorPlanId": plan_id,
        "devices": devices,
        "rawDevices": tuples,
        "customRooms": custom_rooms,
        "areaNames": area_names,
        "updatedAt": datetime.now().astimezone().isoformat(),
    }
    if existing:
        entry["createdAt"] = existing.get("createdAt", entry["updatedAt"])
        mappings = [entry if item.get("floorPlanId") == plan_id else item for item in mappings]
    else:
        entry["createdAt"] = entry["updatedAt"]
        mappings.append(entry)
    _write_json_list(FLOOR_PLAN_DEVICE_STORE_PATH, mappings)
    return jsonify({"status": "success", "success": True, "deviceMapping": entry, "devices": devices, "deviceCount": len(devices)})


@app.route("/api/floor-plans/<plan_id>/devices", methods=["DELETE"])
def delete_floor_plan_devices(plan_id):
    mappings = _read_json_list(FLOOR_PLAN_DEVICE_STORE_PATH)
    kept = [item for item in mappings if item.get("floorPlanId") != plan_id]
    if len(kept) == len(mappings):
        return jsonify({"status": "error", "error": "device mapping not found"}), 404
    _write_json_list(FLOOR_PLAN_DEVICE_STORE_PATH, kept)
    return jsonify({"status": "success", "success": True, "message": "device mapping deleted"})


@app.route("/uploads/floor-plans/<path:filename>", methods=["GET"])
def serve_floor_plan_svg(filename):
    """Serve uploaded SVG floor plans as static assets."""
    safe_name = _safe_svg_filename(filename)
    if safe_name != filename:
        return jsonify({"status": "error", "error": "invalid filename"}), 400
    return send_from_directory(FLOOR_PLAN_UPLOAD_DIR, safe_name, mimetype="image/svg+xml")



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
        init_agent(init_reason="websocket_connect")
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
        init_agent(init_reason="websocket_message")
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
        init_agent(init_reason="websocket_user_input")
    if agent:
        agent_queue.put({"type": "user_input", "data": data})


# ==================== 主程序 ====================

def init_agent(mode: str = None, protocol_gateway=None, force_reinit: bool = False, init_reason: str = ""):
    """初始化全局 Agent"""
    global agent, device_simulator
    global agent_init_metrics
    globals()["protocol_gateway"] = protocol_gateway

    # 从环境变量读取模式（如果未指定）
    if mode is None:
        mode = os.environ.get("HOMEMIND_MODE", "simulated")

    reason = init_reason or "unspecified"
    with agent_init_lock:
        agent_init_metrics["call_count"] += 1
        agent_init_metrics["last_reason"] = reason
        agent_init_metrics["last_mode"] = mode
        agent_init_metrics["last_started_at"] = datetime.now().isoformat(timespec="seconds")

        if agent is not None and not force_reinit:
            print(
                f"[初始化] 复用已有 Agent instance={getattr(agent, 'instance_id', 'unknown')} "
                f"reason={reason} call={agent_init_metrics['call_count']}"
            )
            return agent

        print(
            f"[初始化] Agent 模式: {mode} reason={reason} "
            f"force_reinit={force_reinit} call={agent_init_metrics['call_count']}"
        )
        init_begin = time.perf_counter()
        agent = HomeMindWebAgent(protocol_gateway=protocol_gateway)
        device_simulator = agent.device_simulator
        agent_init_metrics["completed_count"] += 1
        agent_init_metrics["last_duration_ms"] = round((time.perf_counter() - init_begin) * 1000, 2)
        agent_init_metrics["last_instance_id"] = getattr(agent, "instance_id", "")
        agent_init_metrics["phases_ms"] = dict(getattr(agent, "_startup_metrics", {}).get("phases_ms", {}))
        print(
            f"[初始化] Agent 初始化完成 instance={agent_init_metrics['last_instance_id']} "
            f"total={agent_init_metrics['last_duration_ms']:.2f}ms"
        )
        return agent


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
