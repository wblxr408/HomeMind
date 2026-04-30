"""Seed richer demo history and device inventory for HomeMind.

The script is deterministic and safe to rerun. It writes:
- data/device-registry.json
- data/devices.json
- data/scenes.json
- data/tap_rules.json
- data/demo_history.json
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
FLOOR_PLAN_ID = "floorPlan-sample.svg"
TZ = timezone(timedelta(hours=8))


AREAS = {
    "living_room": {"name": "客厅", "box": (12, 18, 31, 42)},
    "dining_room": {"name": "餐厅", "box": (18, 56, 36, 76)},
    "kitchen": {"name": "厨房", "box": (39, 56, 53, 79)},
    "entrance": {"name": "玄关", "box": (8, 45, 22, 59)},
    "bedroom": {"name": "主卧", "box": (45, 20, 66, 45)},
    "bedroom2": {"name": "次卧", "box": (70, 18, 92, 43)},
    "study": {"name": "书房", "box": (69, 62, 91, 86)},
    "bathroom1": {"name": "主卫", "box": (52, 65, 66, 84)},
    "bathroom2": {"name": "次卫", "box": (76, 49, 91, 62)},
    "balcony": {"name": "阳台", "box": (7, 78, 33, 92)},
}


ROOM_DEVICE_NAMES = {
    "living_room": [
        ("light", "主灯", "zigbee"),
        ("light", "筒灯", "zigbee"),
        ("light", "灯带", "zigbee"),
        ("light", "落地灯", "matter"),
        ("air_conditioner", "空调", "mqtt"),
        ("tv", "电视", "infrared"),
        ("speaker", "音响", "wifi"),
        ("fan", "循环扇", "mqtt"),
        ("curtain", "窗帘", "zigbee"),
        ("motion_sensor", "人体传感器", "zigbee"),
        ("temperature_sensor", "温湿度传感器", "zigbee"),
        ("switch", "电视墙插座", "matter"),
    ],
    "dining_room": [
        ("light", "吊灯", "zigbee"),
        ("light", "餐边柜灯", "zigbee"),
        ("switch", "咖啡机插座", "matter"),
        ("speaker", "餐厅音响", "wifi"),
        ("motion_sensor", "人体传感器", "zigbee"),
        ("temperature_sensor", "温湿度传感器", "zigbee"),
        ("air_quality_sensor", "空气质量传感器", "mqtt"),
        ("switch", "净水器插座", "matter"),
    ],
    "kitchen": [
        ("light", "主灯", "zigbee"),
        ("light", "操作台灯", "zigbee"),
        ("switch", "油烟机插座", "matter"),
        ("switch", "蒸烤箱插座", "matter"),
        ("gas_sensor", "燃气传感器", "zigbee"),
        ("smoke_sensor", "烟雾传感器", "zigbee"),
        ("water_leak_sensor", "漏水传感器", "zigbee"),
        ("temperature_sensor", "温湿度传感器", "zigbee"),
        ("switch", "洗碗机插座", "matter"),
        ("switch", "冰箱监测插座", "matter"),
    ],
    "entrance": [
        ("light", "玄关灯", "zigbee"),
        ("door_sensor", "入户门磁", "zigbee"),
        ("camera", "门口摄像头", "onvif"),
        ("lock", "智能门锁", "ble"),
        ("motion_sensor", "人体传感器", "zigbee"),
        ("switch", "鞋柜除味插座", "matter"),
        ("light", "鞋柜灯", "zigbee"),
        ("alarm", "安防报警器", "zigbee"),
    ],
    "bedroom": [
        ("light", "主灯", "zigbee"),
        ("light", "床头灯左", "matter"),
        ("light", "床头灯右", "matter"),
        ("light", "衣柜灯", "zigbee"),
        ("air_conditioner", "空调", "mqtt"),
        ("fan", "风扇", "mqtt"),
        ("curtain", "窗帘", "zigbee"),
        ("speaker", "白噪声音响", "wifi"),
        ("temperature_sensor", "温湿度传感器", "zigbee"),
        ("motion_sensor", "人体传感器", "zigbee"),
        ("switch", "电热毯插座", "matter"),
        ("window", "窗户", "zigbee"),
    ],
    "bedroom2": [
        ("light", "主灯", "zigbee"),
        ("light", "床头灯", "matter"),
        ("light", "书桌灯", "matter"),
        ("air_conditioner", "空调", "mqtt"),
        ("fan", "风扇", "mqtt"),
        ("curtain", "窗帘", "zigbee"),
        ("temperature_sensor", "温湿度传感器", "zigbee"),
        ("motion_sensor", "人体传感器", "zigbee"),
        ("switch", "加湿器插座", "matter"),
        ("window", "窗户", "zigbee"),
    ],
    "study": [
        ("light", "主灯", "zigbee"),
        ("light", "台灯", "matter"),
        ("light", "书柜灯", "zigbee"),
        ("air_conditioner", "空调", "mqtt"),
        ("speaker", "书房音响", "wifi"),
        ("curtain", "窗帘", "zigbee"),
        ("temperature_sensor", "温湿度传感器", "zigbee"),
        ("motion_sensor", "人体传感器", "zigbee"),
        ("switch", "电脑插座", "matter"),
        ("switch", "打印机插座", "matter"),
        ("camera", "桌面摄像头", "onvif"),
        ("air_quality_sensor", "空气质量传感器", "mqtt"),
    ],
    "bathroom1": [
        ("light", "主灯", "zigbee"),
        ("light", "镜前灯", "zigbee"),
        ("water_heater", "热水器", "modbus_tcp"),
        ("fan", "排风扇", "mqtt"),
        ("water_leak_sensor", "漏水传感器", "zigbee"),
        ("humidity_sensor", "湿度传感器", "zigbee"),
        ("motion_sensor", "人体传感器", "zigbee"),
        ("switch", "智能马桶插座", "matter"),
    ],
    "bathroom2": [
        ("light", "主灯", "zigbee"),
        ("light", "镜前灯", "zigbee"),
        ("fan", "排风扇", "mqtt"),
        ("water_leak_sensor", "漏水传感器", "zigbee"),
        ("humidity_sensor", "湿度传感器", "zigbee"),
        ("motion_sensor", "人体传感器", "zigbee"),
        ("switch", "洗衣机插座", "matter"),
        ("switch", "烘干机插座", "matter"),
    ],
    "balcony": [
        ("light", "阳台灯", "zigbee"),
        ("window", "窗户", "zigbee"),
        ("curtain", "纱帘", "zigbee"),
        ("switch", "洗衣机插座", "matter"),
        ("water_leak_sensor", "漏水传感器", "zigbee"),
        ("temperature_sensor", "温湿度传感器", "zigbee"),
        ("light", "晾衣架灯", "zigbee"),
        ("switch", "电动晾衣架", "mqtt"),
        ("weather_sensor", "户外天气传感器", "mqtt"),
        ("camera", "阳台摄像头", "onvif"),
        ("plant_sensor", "绿植传感器", "ble"),
        ("switch", "扫地机器人充电座", "matter"),
    ],
}


TYPE_PREFIX = {
    "air_conditioner": "climate",
    "tv": "media",
    "speaker": "speaker",
    "fan": "fan",
    "curtain": "cover",
    "window": "cover",
    "water_heater": "water_heater",
    "lock": "lock",
    "camera": "camera",
    "alarm": "alarm",
    "switch": "switch",
    "light": "light",
}


def slug(value: str) -> str:
    mapping = {
        "living_room": "living_room",
        "dining_room": "dining_room",
        "kitchen": "kitchen",
        "entrance": "entrance",
        "bedroom": "bedroom",
        "bedroom2": "bedroom2",
        "study": "study",
        "bathroom1": "bathroom1",
        "bathroom2": "bathroom2",
        "balcony": "balcony",
    }
    return mapping.get(value, value.replace(" ", "_"))


def device_id_for(area: str, device_type: str, index: int) -> str:
    prefix = TYPE_PREFIX.get(device_type, device_type)
    return f"{prefix}.{slug(area)}_{index:02d}"


def position_for(area: str, index: int, total: int) -> tuple[float, float]:
    left, top, right, bottom = AREAS[area]["box"]
    cols = max(2, min(4, int(total ** 0.5 + 0.999)))
    rows = max(1, (total + cols - 1) // cols)
    row = index // cols
    col = index % cols
    x = left + (right - left) * ((col + 1) / (cols + 1))
    y = top + (bottom - top) * ((row + 1) / (rows + 1))
    return round(x, 2), round(y, 2)


def build_devices() -> list[dict]:
    devices: list[dict] = []
    for area, specs in ROOM_DEVICE_NAMES.items():
        area_name = AREAS[area]["name"]
        for index, (device_type, label, protocol) in enumerate(specs, start=1):
            x, y = position_for(area, index - 1, len(specs))
            name = f"{area_name}{label}"
            devices.append(
                {
                    "id": device_id_for(area, device_type, index),
                    "name": name,
                    "type": device_type,
                    "protocol": protocol,
                    "area": area,
                    "areaName": area_name,
                    "x": x,
                    "y": y,
                    "icon": device_type,
                }
            )
    return devices


def read_json(path: Path, default):
    if not path.exists():
        return deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return deepcopy(default)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_scenes() -> dict:
    existing = read_json(DATA_DIR / "scenes.json", {})
    if not isinstance(existing, dict):
        existing = {}
    extra = {
        "深夜阅读模式": {
            "客厅落地灯": {"action": "adjust", "params": {"brightness": 35}},
            "书房台灯": {"action": "adjust", "params": {"brightness": 55}},
            "客厅电视": {"action": "off", "params": {}},
        },
        "厨房备餐模式": {
            "厨房操作台灯": {"action": "adjust", "params": {"brightness": 100}},
            "厨房油烟机插座": {"action": "on", "params": {}},
            "餐厅吊灯": {"action": "adjust", "params": {"brightness": 70}},
        },
        "全屋安静模式": {
            "客厅音响": {"action": "off", "params": {}},
            "餐厅音响": {"action": "off", "params": {}},
            "书房音响": {"action": "off", "params": {}},
            "主卧白噪声音响": {"action": "adjust", "params": {"volume": 18}},
        },
        "空气舒适模式": {
            "客厅空调": {"action": "adjust", "params": {"temperature": 26}},
            "主卧空调": {"action": "adjust", "params": {"temperature": 25}},
            "书房空调": {"action": "adjust", "params": {"temperature": 25}},
        },
        "阳台晾晒模式": {
            "阳台电动晾衣架": {"action": "on", "params": {}},
            "阳台灯": {"action": "on", "params": {}},
            "阳台窗户": {"action": "open", "params": {}},
        },
    }
    existing.update(extra)
    return existing


def build_rules(now: datetime) -> list[dict]:
    return [
        {
            "id": f"demo_rule_{index:02d}",
            "name": name,
            "enabled": True,
            "priority": priority,
            "trigger": trigger,
            "conditions": conditions,
            "action": action,
            "created_at": (now - timedelta(days=20 - index)).isoformat(),
            "updated_at": (now - timedelta(days=5 - index % 5)).isoformat(),
        }
        for index, (name, priority, trigger, conditions, action) in enumerate(
            [
                ("工作日早晨打开客厅空调", 80, {"type": "time", "at": "07:30"}, [], {"type": "device_control", "device": "空调", "device_action": "on", "params": {"temperature": 26}}),
                ("晚上十点进入睡眠模式", 90, {"type": "time", "at": "22:30"}, [], {"type": "scene_switch", "scene": "睡眠模式"}),
                ("五一关闭客厅空调", 70, {"type": "holiday", "name": "五一", "month": 5, "day": 1}, [], {"type": "device_control", "device": "空调", "device_action": "off", "params": {}}),
                ("高温自动降温", 75, {"type": "temperature", "op": ">", "value": 29}, [{"type": "occupancy", "op": ">", "value": 0}], {"type": "device_control", "device": "空调", "device_action": "on", "params": {"temperature": 26}}),
                ("低湿度提醒开加湿器插座", 50, {"type": "humidity", "op": "<", "value": 35}, [], {"type": "device_control", "device": "风扇", "device_action": "off", "params": {}}),
                ("回家后打开玄关灯", 65, {"type": "occupancy", "op": ">", "value": 0}, [], {"type": "device_control", "device": "灯光", "device_action": "on", "params": {"brightness": 70}}),
                ("周末上午切换待客模式", 55, {"type": "day_of_week", "days": [5, 6]}, [], {"type": "scene_switch", "scene": "待客模式"}),
                ("夜间关闭电视", 60, {"type": "time", "at": "23:00"}, [], {"type": "device_control", "device": "电视", "device_action": "off", "params": {}}),
                ("厨房备餐时打开操作灯", 50, {"type": "time", "at": "18:00"}, [], {"type": "scene_switch", "scene": "厨房备餐模式"}),
                ("书房晚间阅读", 50, {"type": "time", "at": "20:30"}, [], {"type": "scene_switch", "scene": "深夜阅读模式"}),
                ("睡前关闭音响", 50, {"type": "time", "at": "22:15"}, [], {"type": "device_control", "device": "音响", "device_action": "off", "params": {}}),
                ("湿度过高打开排风扇", 45, {"type": "humidity", "op": ">", "value": 75}, [], {"type": "device_control", "device": "风扇", "device_action": "on", "params": {"speed": 3}}),
            ],
            start=1,
        )
    ]


def build_demo_history(devices: list[dict], now: datetime) -> list[dict]:
    events = []
    action_templates = [
        ("device_control", "客厅空调", "adjust", {"temperature": 26}, "早晨自动调温"),
        ("scene_switch", "", "", {"scene": "工作模式"}, "工作日进入办公状态"),
        ("device_control", "书房台灯", "adjust", {"brightness": 70}, "夜间阅读"),
        ("device_control", "客厅电视", "off", {}, "睡前关闭影音设备"),
        ("scene_switch", "", "", {"scene": "睡眠模式"}, "睡前例行动作"),
        ("device_control", "厨房操作台灯", "on", {}, "晚餐备餐"),
        ("device_control", "主卧空调", "adjust", {"temperature": 25}, "卧室睡眠温度"),
        ("scene_switch", "", "", {"scene": "离家模式"}, "离家节能"),
    ]
    for index in range(80):
        action_type, device, device_action, params, note = action_templates[index % len(action_templates)]
        events.append(
            {
                "id": f"hist_{index + 1:03d}",
                "timestamp": (now - timedelta(hours=index * 3)).isoformat(),
                "type": action_type,
                "device": device,
                "device_action": device_action,
                "params": params,
                "accepted": index % 7 != 0,
                "source": "seed_demo_history",
                "note": note,
            }
        )
    events.append(
        {
            "id": "inventory_snapshot",
            "timestamp": now.isoformat(),
            "type": "inventory",
            "device_count": len(devices),
            "area_count": len(AREAS),
            "source": "seed_demo_history",
        }
    )
    return events


def seed() -> dict:
    now = datetime.now(TZ).replace(microsecond=0)
    devices = build_devices()
    area_names = {area: config["name"] for area, config in AREAS.items()}

    registry = [
        {
            "id": device["id"],
            "name": device["name"],
            "type": device["type"],
            "protocol": device["protocol"],
            "area": device["area"],
            "areaName": device["areaName"],
        }
        for device in devices
    ]
    write_json(DATA_DIR / "device-registry.json", registry)

    mapping = {
        "floorPlanId": FLOOR_PLAN_ID,
        "areaNames": area_names,
        "devices": devices,
        "rawDevices": [
            [device["id"], device["area"], device["type"], device["name"], device["areaName"]]
            for device in devices
        ],
        "customRooms": {
            area: {
                "name": config["name"],
                "coordinates": [{"x": config["box"][0] * 6.4, "y": config["box"][1] * 6.6}, {"x": config["box"][2] * 6.4, "y": config["box"][3] * 6.6}],
            }
            for area, config in AREAS.items()
        },
        "updatedAt": now.isoformat(),
        "createdAt": "2026-03-29T03:10:45+08:00",
    }
    write_json(DATA_DIR / "devices.json", [mapping])

    write_json(DATA_DIR / "scenes.json", build_scenes())
    write_json(DATA_DIR / "tap_rules.json", build_rules(now))
    write_json(DATA_DIR / "demo_history.json", build_demo_history(devices, now))

    return {
        "device_count": len(devices),
        "area_count": len(AREAS),
        "rule_count": len(build_rules(now)),
        "history_count": len(build_demo_history(devices, now)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed HomeMind demo history data")
    parser.add_argument("--check", action="store_true", help="Only print the deterministic counts")
    args = parser.parse_args()
    if args.check:
        devices = build_devices()
        print(json.dumps({"device_count": len(devices), "area_count": len(AREAS)}, ensure_ascii=False))
        return
    summary = seed()
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
