"""告警管理器。

检查 AgentMetrics 的各指标，
将告警输出到日志文件和 WebSocket 推送。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from threading import Thread
from typing import Callable, List, Optional

from core.config import ALERT_THRESHOLDS, STORAGE_CONFIG

logger = logging.getLogger(__name__)


class Alert:
    """单条告警。"""

    def __init__(
        self,
        alert_id: str,
        level: str,  # info | warning | critical
        message: str,
        metric: str = "",
        value: float = 0.0,
        threshold: float = 0.0,
    ):
        self.alert_id = alert_id
        self.level = level
        self.message = message
        self.metric = metric
        self.value = value
        self.threshold = threshold
        self.timestamp = datetime.now().astimezone().isoformat()

    def to_dict(self) -> dict:
        return {
            "alert_id": self.alert_id,
            "level": self.level,
            "message": self.message,
            "metric": self.metric,
            "value": self.value,
            "threshold": self.threshold,
            "timestamp": self.timestamp,
        }


class Alerter:
    """告警管理器：检测指标异常 → 写日志 → 推 WebSocket。"""

    def __init__(self, metrics):
        self._metrics = metrics
        self._active_alerts: dict = {}
        self._log_dir = Path(STORAGE_CONFIG["logs_dir"])
        self._alert_log_path = self._log_dir / "alerts.log"
        self._ws_handlers: List[Callable[[Alert], None]] = []
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def register_ws_handler(self, handler: Callable[[Alert], None]):
        """注册 WebSocket 推送回调。"""
        self._ws_handlers.append(handler)

    def check(self) -> List[Alert]:
        """检查指标并返回新增告警列表。"""
        metric_alerts = self._metrics.get_alerts()
        new_alerts: List[Alert] = []

        for key, message in metric_alerts.items():
            if key not in self._active_alerts:
                level = "critical" if "critical" in key else "warning"
                alert = Alert(
                    alert_id=key,
                    level=level,
                    message=message,
                    metric=key,
                )
                self._active_alerts[key] = alert
                new_alerts.append(alert)
                self._fire(alert)

        # 清除已恢复的告警
        for key in list(self._active_alerts.keys()):
            if key not in metric_alerts:
                del self._active_alerts[key]

        return new_alerts

    def _fire(self, alert: Alert):
        """触发告警：写日志 + 推 WebSocket。"""
        log_msg = f"[{alert.level.upper()}] {alert.message}"
        if alert.level == "critical":
            logger.critical(log_msg)
        else:
            logger.warning(log_msg)

        self._write_log(alert)
        for handler in self._ws_handlers:
            try:
                handler(alert)
            except Exception as exc:
                logger.warning("WebSocket alert handler failed: %s", exc)

    def _write_log(self, alert: Alert):
        try:
            with open(self._alert_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(alert.to_dict(), ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning("Alert log write failed: %s", exc)

    def get_active_alerts(self) -> List[dict]:
        return [a.to_dict() for a in self._active_alerts.values()]

    def clear_all(self):
        self._active_alerts.clear()
