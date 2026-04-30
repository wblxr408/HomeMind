import os
import unittest
from pathlib import Path

from core.lsr.precision_ranking import LSRecify
from core.memory import SessionStore
from core.security import reset_encrypted_storage


class FollowUpContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["HOMEMIND_STORAGE_KEY"] = "test-storage-key"

    def setUp(self):
        self.session_path = Path("data/test_followup_session.json")
        if self.session_path.exists():
            self.session_path.unlink()
        reset_encrypted_storage()
        self.store = SessionStore(path=str(self.session_path))
        self.ranker = LSRecify()

    def tearDown(self):
        if self.session_path.exists():
            self.session_path.unlink()
        reset_encrypted_storage()

    def test_follow_up_lower_command_prefers_last_air_conditioner(self):
        self.store.update_from_decision(
            {
                "action": "设备控制",
                "device": "空调",
                "device_action": "on",
                "params": {"temperature": 26},
            },
            result="已开启空调，温度26°C。",
        )

        explicit = self.ranker._explicit_device_target("调低一些", session_store=self.store)
        self.assertEqual(explicit, "调低空调温度")

    def test_repeated_hot_complaint_prefers_lower_air_conditioner_temperature(self):
        self.store.update_from_decision(
            {
                "action": "设备控制",
                "device": "空调",
                "device_action": "on",
                "params": {"temperature": 26},
            },
            result="已开启空调，温度26°C。",
        )

        explicit = self.ranker._explicit_device_target("还是有点热", session_store=self.store)
        self.assertEqual(explicit, "调低空调温度")

    def test_repeated_cold_complaint_prefers_higher_air_conditioner_temperature(self):
        self.store.update_from_decision(
            {
                "action": "设备控制",
                "device": "空调",
                "device_action": "adjust",
                "params": {"temperature": 24},
            },
            result="已将空调调到24°C。",
        )

        explicit = self.ranker._explicit_device_target("还是有点冷", session_store=self.store)
        self.assertEqual(explicit, "调高空调温度")

    def test_hot_complaint_after_air_conditioner_off_reopens_air_conditioner(self):
        self.store.update_from_decision(
            {
                "action": "设备控制",
                "device": "空调",
                "device_action": "off",
                "params": {},
            },
            result="已关闭空调。",
        )

        explicit = self.ranker._explicit_device_target("有点热了", session_store=self.store)
        self.assertEqual(explicit, "打开空调")

    def test_follow_up_dim_command_prefers_last_light(self):
        self.store.update_from_decision(
            {
                "action": "设备控制",
                "device": "灯光",
                "device_action": "on",
                "params": {"brightness": 100},
            },
            result="已打开灯光。",
        )

        explicit = self.ranker._explicit_device_target("暗一些", session_store=self.store)
        self.assertEqual(explicit, "调暗灯光")

    def test_dark_complaint_after_light_off_reopens_light(self):
        self.store.update_from_decision(
            {
                "action": "设备控制",
                "device": "灯光",
                "device_action": "off",
                "params": {},
            },
            result="已关闭灯光。",
        )

        explicit = self.ranker._explicit_device_target("还是太暗了", session_store=self.store)
        self.assertEqual(explicit, "打开灯光")

    def test_noisy_follow_up_after_tv_prefers_turning_tv_off(self):
        self.store.update_from_decision(
            {
                "action": "设备控制",
                "device": "电视",
                "device_action": "on",
                "params": {},
            },
            result="已打开电视。",
        )

        explicit = self.ranker._explicit_device_target("还是有点吵", session_store=self.store)
        self.assertEqual(explicit, "关闭电视")

    def test_stuffy_follow_up_after_window_close_reopens_window(self):
        self.store.update_from_decision(
            {
                "action": "设备控制",
                "device": "窗户",
                "device_action": "close",
                "params": {},
            },
            result="已关闭窗户。",
        )

        explicit = self.ranker._explicit_device_target("有点闷", session_store=self.store)
        self.assertEqual(explicit, "打开窗户")


if __name__ == "__main__":
    unittest.main(verbosity=2)
