"""
Web 服务配置
"""
import os

class Config:
    """基础配置"""
    SECRET_KEY = os.environ.get("SECRET_KEY", "homemind-secret-key-2024")
    DEBUG = os.environ.get("DEBUG", "True").lower() == "true"
    
    # Flask 配置
    JSON_AS_ASCII = False
    JSON_SORT_KEYS = False
    
    # CORS 配置
    CORS_ORIGINS = [
        "http://localhost:5000",
        "http://127.0.0.1:5000",
        "http://localhost:3000",
        "file://",  # 本地文件访问
    ]
    
    # WebSocket 配置
    WS_PING_INTERVAL = 30
    WS_PING_TIMEOUT = 10
    
    # 协议配置
    PROTOCOLS = {
        "simulated": {
            "enabled": True,
            "type": "simulated",
        },
        "mqtt": {
            "enabled": False,
            "type": "mqtt",
            "broker_host": os.environ.get("MQTT_BROKER_HOST", "localhost"),
            "broker_port": int(os.environ.get("MQTT_BROKER_PORT", "1883")),
            "username": os.environ.get("MQTT_USERNAME", ""),
            "password": os.environ.get("MQTT_PASSWORD", ""),
            "topics": {
                "command": "home/device/{device_id}/command",
                "state": "home/device/{device_id}/state",
            },
        },
        "home_assistant": {
            "enabled": False,
            "type": "home_assistant",
            "host": os.environ.get("HA_HOST", "http://localhost:8123"),
            "token": os.environ.get("HA_TOKEN", ""),
        },
        "xiaomi": {
            "enabled": False,
            "type": "xiaomi",
            "server_ip": os.environ.get("XIAOMI_SERVER_IP", "192.168.1.1"),
            "server_port": int(os.environ.get("XIAOMI_SERVER_PORT", "54321")),
            "token": os.environ.get("XIAOMI_TOKEN", ""),
        },
        "matter": {
            "enabled": False,
            "type": "matter",
            "controller_ip": os.environ.get("MATTER_CONTROLLER_IP", "192.168.1.100"),
            "controller_port": int(os.environ.get("MATTER_CONTROLLER_PORT", "5580")),
        },
    }
    
    # 设备默认配置
    DEFAULT_DEVICES = [
        {
            "id": "air_conditioner",
            "name": "空调",
            "type": "air_conditioner",
            "room": "living_room",
            "protocol": "simulated",
        },
        {
            "id": "light",
            "name": "灯光",
            "type": "light",
            "room": "living_room",
            "protocol": "simulated",
        },
        {
            "id": "tv",
            "name": "电视",
            "type": "tv",
            "room": "living_room",
            "protocol": "simulated",
        },
        {
            "id": "water_heater",
            "name": "热水器",
            "type": "water_heater",
            "room": "bathroom",
            "protocol": "simulated",
        },
        {
            "id": "fan",
            "name": "风扇",
            "type": "fan",
            "room": "bedroom",
            "protocol": "simulated",
        },
        {
            "id": "speaker",
            "name": "音响",
            "type": "speaker",
            "room": "living_room",
            "protocol": "simulated",
        },
        {
            "id": "window",
            "name": "窗户",
            "type": "window",
            "room": "living_room",
            "protocol": "simulated",
        },
    ]


class DevelopmentConfig(Config):
    """开发环境配置"""
    DEBUG = True


class ProductionConfig(Config):
    """生产环境配置"""
    DEBUG = False
    SECRET_KEY = os.environ.get("SECRET_KEY")  # 生产环境必须设置


config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
