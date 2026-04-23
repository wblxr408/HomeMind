#!/usr/bin/env python3
"""
HomeMind Web 启动器
支持模拟模式和真实设备模式
"""
import sys
import os
import argparse
import json

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from web.server import app, socketio, init_agent


def load_protocol_config():
    """加载协议配置"""
    config_path = os.path.join(os.path.dirname(__file__), "web", "protocol_config.json")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def init_protocol_gateway(mode: str):
    """Initialize protocol gateway based on mode"""
    if mode == "simulated":
        from demo.device_simulator import DeviceSimulator
        return DeviceSimulator()
    elif mode == "real":
        # Try to initialize real protocol gateway
        from core.protocols.smart_home_gateway import SmartHomeGateway
        try:
            gateway = SmartHomeGateway()
            gateway.discover_devices()
            return gateway
        except Exception as e:
            print(f"[警告] 真实设备网关初始化失败: {e}")
            print("[回退] 使用模拟设备模式")
            from demo.device_simulator import DeviceSimulator
            return DeviceSimulator()
    else:
        from demo.device_simulator import DeviceSimulator
        return DeviceSimulator()


def main():
    parser = argparse.ArgumentParser(description="HomeMind 中央指令器")
    parser.add_argument("--host", default="0.0.0.0", help="服务地址")
    parser.add_argument("--port", type=int, default=5000, help="服务端口")
    parser.add_argument("--debug", action="store_true", help="调试模式")
    parser.add_argument("--mode", choices=["simulated", "real"], default="simulated",
                       help="运行模式: simulated=模拟设备, real=真实设备")
    args = parser.parse_args()

    # 设置模式环境变量，供 server.py 使用
    os.environ["HOMEMIND_MODE"] = args.mode

    print("=" * 50)
    print("  HomeMind 中央指令器")
    print("=" * 50)
    print()
    print(f"  模式: {'模拟设备' if args.mode == 'simulated' else '真实设备'}")
    print(f"  地址: http://{args.host}:{args.port}")
    print()

    # 初始化协议网关
    protocol_gateway = init_protocol_gateway(args.mode)

    # 初始化 Agent（传入协议网关）
    init_agent(mode=args.mode, protocol_gateway=protocol_gateway)

    print()
    print("  控制面板: http://localhost:5000")
    print("  API 状态:  http://localhost:5000/api/status")
    print()
    print("  按 Ctrl+C 停止服务")
    print()

    # 启动服务
    socketio.run(
        app,
        host=args.host,
        port=args.port,
        debug=args.debug,
        allow_unsafe_werkzeug=True
    )


if __name__ == "__main__":
    main()
