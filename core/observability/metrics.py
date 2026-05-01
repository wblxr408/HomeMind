"""Prometheus 指标收集器。

四大监控维度：
1. LLM 经济性（推理次数 / token 消耗 / 云端比率）
2. 推理延迟（p50 / p95 / p99）
3. 消息管道（队列深度 / 协议连接状态）
4. 运营健康（内存使用率 / 设备在线率 / 规则触发成功率）
"""

from __future__ import annotations

import logging
import os
import psutil
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from threading import Lock
from typing import Any, Dict, Optional

from core.config import ALERT_THRESHOLDS

logger = logging.getLogger(__name__)

# 全局指标实例
_metrics: Optional["AgentMetrics"] = None


class AgentMetrics:
    """轻量级内存内指标收集器，输出 Prometheus 文本格式。"""

    def __init__(self):
        self._lock = Lock()
        self._start_time = time.time()

        # LLM 经济性
        self.llm_requests_local = 0
        self.llm_requests_cloud = 0
        self.llm_tokens_total = 0

        # 延迟（滚动窗口，最多保留 1000 个样本）
        self._latencies: deque = deque(maxlen=1000)

        # 消息管道
        self.protocol_connected = True
        self.mqtt_connected = False
        self.message_queue_depth = 0

        # 运营健康
        self.memory_usage_percent = 0.0
        self.device_online_rate = 1.0
        self.rule_trigger_count = 0
        self.rule_trigger_success = 0

        # 错误计数
        self.error_count = 0

        # 告警状态
        self._alerts: Dict[str, str] = {}

    # ── 记录接口 ──────────────────────────────────────

    def record_llm_request(self, route: str, token_count: int = 0):
        with self._lock:
            if route in ("local", "llama_cpp", "ollama"):
                self.llm_requests_local += 1
            else:
                self.llm_requests_cloud += 1
            self.llm_tokens_total += token_count

    def record_latency(self, latency_ms: float):
        with self._lock:
            self._latencies.append(latency_ms)

    def record_protocol_status(self, connected: bool, protocol: str = "general"):
        with self._lock:
            if protocol == "mqtt":
                self.mqtt_connected = connected
            else:
                self.protocol_connected = connected

    def record_queue_depth(self, depth: int):
        with self._lock:
            self.message_queue_depth = depth

    def record_memory(self, percent: float = None):
        if percent is None:
            try:
                percent = psutil.virtual_memory().percent
            except Exception:
                return
        with self._lock:
            self.memory_usage_percent = percent

    def record_device_online(self, rate: float):
        with self._lock:
            self.device_online_rate = max(0.0, min(1.0, rate))

    def record_rule_trigger(self, success: bool):
        with self._lock:
            self.rule_trigger_count += 1
            if success:
                self.rule_trigger_success += 1

    def record_error(self):
        with self._lock:
            self.error_count += 1

    # ── 百分位计算 ──────────────────────────────────────

    def _percentile(self, data: list, p: float) -> float:
        if not data:
            return 0.0
        sorted_data = sorted(data)
        idx = int(len(sorted_data) * p / 100.0)
        idx = min(idx, len(sorted_data) - 1)
        return round(sorted_data[idx], 1)

    # ── Prometheus 文本格式输出 ─────────────────────────

    def render_prometheus(self) -> str:
        with self._lock:
            latencies = list(self._latencies)
            total_requests = self.llm_requests_local + self.llm_requests_cloud
            cloud_ratio = (
                self.llm_requests_cloud / total_requests
                if total_requests > 0
                else 0.0
            )
            rule_success_rate = (
                self.rule_trigger_success / self.rule_trigger_count
                if self.rule_trigger_count > 0
                else 0.0
            )

            lines = [
                "# HELP Homemind_agent_uptime_seconds 运行时间（秒）",
                "# TYPE Homemind_agent_uptime_seconds gauge",
                f"Homemind_agent_uptime_seconds {round(time.time() - self._start_time, 1)}",
                "",
                "# HELP Homemind_llm_requests_total LLM 请求总数",
                "# TYPE Homemind_llm_requests_total counter",
                f'Homemind_llm_requests_total{{route="local"}} {self.llm_requests_local}',
                f'Homemind_llm_requests_total{{route="cloud"}} {self.llm_requests_cloud}',
                "",
                "# HELP Homemind_llm_cloud_ratio 云端请求占比",
                "# TYPE Homemind_llm_cloud_ratio gauge",
                f"Homemind_llm_cloud_ratio {cloud_ratio:.3f}",
                "",
                "# HELP Homemind_llm_tokens_total 消耗 token 总数",
                "# TYPE Homemind_llm_tokens_total counter",
                f"Homemind_llm_tokens_total {self.llm_tokens_total}",
                "",
                "# HELP Homemind_inference_latency_ms 推理延迟（毫秒）",
                "# TYPE Homemind_inference_latency_ms gauge",
                f"Homemind_inference_latency_ms{{quantile=\"p50\"}} {self._percentile(latencies, 50)}",
                f"Homemind_inference_latency_ms{{quantile=\"p95\"}} {self._percentile(latencies, 95)}",
                f"Homemind_inference_latency_ms{{quantile=\"p99\"}} {self._percentile(latencies, 99)}",
                "",
                "# HELP Homemind_memory_percent 内存使用率",
                "# TYPE Homemind_memory_percent gauge",
                f"Homemind_memory_percent {self.memory_usage_percent:.1f}",
                "",
                "# HELP Homemind_protocol_connected 协议连接状态",
                "# TYPE Homemind_protocol_connected gauge",
                f'Homemind_protocol_connected{{protocol="general"}} {int(self.protocol_connected)}',
                f'Homemind_protocol_connected{{protocol="mqtt"}} {int(self.mqtt_connected)}',
                "",
                "# HELP Homemind_message_queue_depth 消息队列深度",
                "# TYPE Homemind_message_queue_depth gauge",
                f"Homemind_message_queue_depth {self.message_queue_depth}",
                "",
                "# HELP Homemind_device_online_rate 设备在线率",
                "# TYPE Homemind_device_online_rate gauge",
                f"Homemind_device_online_rate {self.device_online_rate:.3f}",
                "",
                "# HELP Homemind_rule_trigger_success_rate 规则触发成功率",
                "# TYPE Homemind_rule_trigger_success_rate gauge",
                f"Homemind_rule_trigger_success_rate {rule_success_rate:.3f}",
                "",
                "# HELP Homemind_errors_total 错误总数",
                "# TYPE Homemind_errors_total counter",
                f"Homemind_errors_total {self.error_count}",
                "",
            ]
            return "\n".join(lines)

    def get_alerts(self) -> Dict[str, str]:
        """检查各指标是否触发告警。"""
        alerts: Dict[str, str] = {}
        th = ALERT_THRESHOLDS
        with self._lock:
            latencies = list(self._latencies)

            if self.memory_usage_percent >= th["memory_percent_critical"]:
                alerts["memory_critical"] = f"内存使用率 {self.memory_usage_percent:.1f}% 超过临界值 {th['memory_percent_critical']}%"
            elif self.memory_usage_percent >= th["memory_percent_warning"]:
                alerts["memory_warning"] = f"内存使用率 {self.memory_usage_percent:.1f}% 超过警告值 {th['memory_percent_warning']}%"

            p95 = self._percentile(latencies, 95)
            if p95 >= th["latency_p95_ms_warning"]:
                alerts["latency_warning"] = f"推理延迟 p95={p95}ms 超过阈值 {th['latency_p95_ms_warning']}ms"

            total = self.llm_requests_local + self.llm_requests_cloud
            cloud_ratio = self.llm_requests_cloud / total if total > 0 else 0.0
            if cloud_ratio >= th["cloud_ratio_warning"]:
                alerts["cloud_ratio_warning"] = f"云端调用占比 {cloud_ratio:.1%} 超过阈值 {th['cloud_ratio_warning']:.1%}"

            if not self.protocol_connected:
                alerts["protocol_disconnected"] = "协议网关断连"

        return alerts

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            latencies = list(self._latencies)
            total = self.llm_requests_local + self.llm_requests_cloud
            return {
                "uptime_s": round(time.time() - self._start_time, 1),
                "llm_requests": {"local": self.llm_requests_local, "cloud": self.llm_requests_cloud, "total": total},
                "latency_ms": {
                    "p50": self._percentile(latencies, 50),
                    "p95": self._percentile(latencies, 95),
                    "p99": self._percentile(latencies, 99),
                },
                "memory_percent": self.memory_usage_percent,
                "protocol_connected": self.protocol_connected,
                "mqtt_connected": self.mqtt_connected,
                "queue_depth": self.message_queue_depth,
                "device_online_rate": self.device_online_rate,
                "rule_trigger_success_rate": (
                    self.rule_trigger_success / self.rule_trigger_count
                    if self.rule_trigger_count > 0
                    else 0.0
                ),
                "error_count": self.error_count,
            }


def get_metrics() -> AgentMetrics:
    global _metrics
    if _metrics is None:
        _metrics = AgentMetrics()
    return _metrics
