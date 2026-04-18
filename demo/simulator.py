"""
HomeMind 交互式演示与仿真环境
模拟完整的家庭场景交互流程
"""

import logging
import time
import threading
from typing import Dict, Any, Optional, TYPE_CHECKING

from demo.device_simulator import DeviceSimulator
from demo.context import HomeContext
from core.constants import SCENE_INDEX_MAP

if TYPE_CHECKING:
    from main import HomeMindAgent

logger = logging.getLogger(__name__)


class HomeSimulator:
    """
    家庭仿真器
    管理环境上下文、设备状态，支持时间推进和场景模拟
    """

    def __init__(self):
        self.device_sim = DeviceSimulator()
        self._hour = 22
        self._minute = 0
        self._temperature = 28.0
        self._humidity = 75.0
        self._members_home = 2
        self._last_scene = 3
        self._day_of_week = 4

    def get_context(self) -> HomeContext:
        """获取当前环境上下文"""
        return HomeContext(
            hour=self._hour,
            temperature=self._temperature,
            humidity=self._humidity,
            members_home=self._members_home,
            day_of_week=self._day_of_week,
            last_scene=self._last_scene,
            devices=self.device_sim.get_state(),
        )

    def set_time(self, hour: int, minute: int = 0):
        self._hour = hour % 24
        self._minute = minute % 60
        logger.info(f"时间设置为: {self._hour:02d}:{self._minute:02d}")

    def set_environment(self, temperature: float = None, humidity: float = None):
        if temperature is not None:
            self._temperature = temperature
        if humidity is not None:
            self._humidity = humidity

    def set_members(self, count: int):
        self._members_home = max(0, min(10, count))

    def set_day(self, day: int):
        self._day_of_week = day % 7

    def apply_scene(self, scene_name: str):
        """应用场景到设备模拟器，同步设备状态"""
        scene_map = {
            "睡眠模式": {
                "灯光": {"status": "开", "brightness": 10},
                "空调": {"status": "开", "temperature": 26},
                "电视": {"status": "关"},
            },
            "待客模式": {
                "灯光": {"status": "开", "brightness": 100},
                "空调": {"status": "开", "temperature": 25},
                "音响": {"status": "开"},
            },
            "离家模式": {
                "灯光": {"status": "关"},
                "空调": {"status": "关"},
                "电视": {"status": "关"},
                "音响": {"status": "关"},
            },
            "观影模式": {
                "灯光": {"status": "开", "brightness": 30},
                "空调": {"status": "开", "temperature": 25},
                "电视": {"status": "开"},
            },
            "起床模式": {
                "灯光": {"status": "开", "brightness": 80},
                "音响": {"status": "开"},
            },
            "回家模式": {
                "灯光": {"status": "开", "brightness": 70},
                "空调": {"status": "开", "temperature": 26},
                "电视": {"status": "关"},
                "音响": {"status": "开"},
            },
        }

        if scene_name in scene_map:
            for device, state in scene_map[scene_name].items():
                status = state.pop("status", "开")
                self.device_sim.update(device, status, **state)
            logger.info(f"场景应用: {scene_name}")

        scene_index_map = {"睡眠模式": 0, "待客模式": 1, "离家模式": 2,
                          "观影模式": 3, "起床模式": 4, "回家模式": 1}
        self._last_scene = scene_index_map.get(scene_name, -1)

    def advance_time(self, delta_hours: float = 1.0):
        """推进仿真时间"""
        total_minutes = self._hour * 60 + self._minute + int(delta_hours * 60)
        self._hour = (total_minutes // 60) % 24
        self._minute = total_minutes % 60


class InteractionDemo:
    """
    交互演示脚本
    按照 design.md 第六章的典型场景演示
    """

    SCENES = {
        0: "睡眠模式", 1: "待客模式", 2: "离家模式",
        3: "观影模式", 4: "起床模式", 5: "无推荐",
    }

    def __init__(self):
        self.sim = HomeSimulator()

    def scenario_fuzzy_intent(self) -> str:
        """场景一：模糊环境感知（BSR+LSR+LLM 流程）"""
        self.sim.set_time(20)
        self.sim.set_environment(temperature=28.0, humidity=75.0)
        self.sim.set_members(2)
        self.sim.apply_scene("观影模式")

        return self._build_demo_output(
            "有点闷",
            "BSR 候选召回 → LSR 轻量精排 → LLM 决策 → 置信度评估 → 设备控制 → 知识库写入",
            [
                "[BSR 候选召回] 规则: 闷→空调/风扇/开窗, 向量 Top-3, RAG历史: 28°C以上偏好开空调",
                "[LSR 精排] 打开空调(0.92) > 打开风扇(0.65) > 打开窗户(0.58)",
                "[LLM 决策] 动作=打开空调, params={temperature:26}, confidence=0.91",
                "[执行] device_control(空调, on, temp=26)",
                "[知识库写入] 用户接受温度28°C开空调的决策",
            ]
        )

    def scenario_history_reference(self) -> str:
        """场景二：历史指代消解（RAG 检索）"""
        self.sim.set_time(21)
        self.sim.set_environment(temperature=25.0, humidity=55.0)
        self.sim.set_members(2)
        self.sim._last_scene = 0

        return self._build_demo_output(
            "像昨天晚上那样",
            "RAG 历史检索 → BSR 召回 → LLM 决策 → 批量执行 → DQN 记录",
            [
                "[RAG 检索] 昨晚22:30: 灯光亮度10%, 空调26°C, 睡眠模式",
                "[BSR 召回] 候选: 睡眠模式相关动作",
                "[LLM 决策] 动作=恢复昨晚设置, confidence=0.94",
                "[执行] 批量执行: 灯光调暗, 空调26°C, 睡眠模式",
                "[DQN记录] 22:30+2人在家→睡眠模式被接受, reward=+1.0",
            ]
        )

    def scenario_dqn_recommend(self) -> str:
        """场景三：DQN 主动推荐（独立流程）"""
        self.sim.set_time(22, 15)
        self.sim.set_environment(temperature=25.0, humidity=55.0)
        self.sim.set_members(2)
        self.sim._last_scene = 3

        return self._build_demo_output(
            "[主动感知]",
            "环境状态 → DQN 策略推理 → 主动推荐 → 用户反馈 → 经验回放",
            [
                "[状态] hour=22, members=2, temperature=25, last_scene=观影模式",
                "[DQN 推理] Q值: 睡眠模式=0.89 > 待客=0.12 > 离家=0.05",
                "[推荐] 现在22点了，要切换到睡眠模式吗？",
                "[用户响应] 好的",
                "[执行] scene_switch(睡眠), dqn_feedback(reward=+1.0), buffer累计第47条",
            ]
        )

    def scenario_clarification(self) -> str:
        """场景四：主动澄清（置信度触发）"""
        self.sim.set_time(14)
        self.sim.set_environment(temperature=30.0, humidity=65.0)
        self.sim.set_members(1)

        return self._build_demo_output(
            "调一下",
            "意图理解 → 置信度评估 → 主动澄清 → 用户补充 → 执行 + 知识库写入",
            [
                "[意图理解] 置信度: 0.42 < 阈值0.75",
                "[澄清询问] 请问您想调节哪个设备？空调、灯光还是音量？",
                "[用户响应] 灯，亮一点",
                "[执行] device_control(灯, adjust, brightness+20%)",
                "[知识库写入] '调一下'在该时间段多指灯光",
            ]
        )

    def scenario_multi_turn(self) -> str:
        """场景五：多轮任务延续"""
        self.sim.set_time(9)
        self.sim.set_environment(temperature=26.0, humidity=50.0)
        self.sim.set_members(2)

        return self._build_demo_output(
            "我要出门了",
            "离家模式执行 → 日程询问 → 定时预热",
            [
                "[执行] 离家模式，关闭全部设备",
                "[回复] 已关闭空调、灯光和电视。预计几点回来？",
                "[用户响应] 晚上七点",
                "[日程写入] 19:00 提前10分钟开启空调",
                "[回复] 好的，六点五十我会提前开好空调等您。",
                "[DQN记录] 离家前询问回来时间被用户响应, reward=+1.0",
            ]
        )

    def _build_demo_output(self, query: str, flow: str, steps: list) -> str:
        sep = "-" * 52
        return "\n".join([
            f"用户：  \"{query}\"",
            sep,
            f"[流程] {flow}",
            sep,
            *steps,
            sep,
        ])

    def run_all_scenarios(self):
        """运行所有演示场景"""
        scenarios = [
            ("场景一：模糊意图感知", self.scenario_fuzzy_intent),
            ("场景二：历史指代消解", self.scenario_history_reference),
            ("场景三：DQN主动推荐", self.scenario_dqn_recommend),
            ("场景四：主动澄清", self.scenario_clarification),
            ("场景五：多轮任务", self.scenario_multi_turn),
        ]
        banner = "=" * 56
        print(f"\n{banner}")
        print("HomeMind 典型交互演示")
        print(banner)
        for title, fn in scenarios:
            print(f"\n{banner}")
            print(f"{title}")
            print(banner)
            print(fn())
            time.sleep(0.3)


class AutoSimulation:
    """自动仿真：定时推进时间，模拟真实家庭一天"""

    def __init__(self, agent):
        self.agent = agent
        self.running = False
        self._thread: Optional[threading.Thread] = None

    def start(self, interval_seconds: int = 10):
        self.running = True
        self._thread = threading.Thread(target=self._run, args=(interval_seconds,), daemon=True)
        self._thread.start()
        logger.info("自动仿真已启动")

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("自动仿真已停止")

    def _run(self, interval: int):
        hour = 6
        while self.running and hour <= 23:
            self.agent.update_context(hour=hour)
            recommend = self.agent.proactive_recommend()
            if recommend:
                print(f"\n[HomeMind 主动] {recommend}")
            time.sleep(interval)
            hour += 1
        self.running = False
