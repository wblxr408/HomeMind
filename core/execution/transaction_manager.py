"""Execution transaction wrapper with rollback for simulated devices."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional


class ExecutionTransactionManager:
    """Rollback simulated device and scene state on execution failure."""

    def __init__(self, tool_registry, device_controller=None, session_store=None, context=None):
        self.tool_registry = tool_registry
        self.device_controller = device_controller
        self.session_store = session_store
        self.context = context

    def execute(self, command: Dict[str, Any]) -> Dict[str, Any]:
        snapshot = self._snapshot()
        result = self.tool_registry.execute(command)
        if result.get("status") == "success":
            return result
        self._restore(snapshot)
        result["rolled_back"] = True
        return result

    def _snapshot(self) -> Dict[str, Any]:
        state = {}
        if self.device_controller is not None:
            state["devices"] = deepcopy(getattr(self.device_controller, "state", {}))
        if self.session_store is not None:
            state["scene"] = self.session_store.get_current_scene()
        if self.context is not None:
            state["context_scene"] = getattr(self.context, "current_scene", "")
            state["context_last_scene"] = getattr(self.context, "last_scene", -1)
            state["context_devices"] = deepcopy(getattr(self.context, "devices", {}))
        return state

    def _restore(self, snapshot: Optional[Dict[str, Any]]) -> None:
        if not snapshot:
            return
        if self.device_controller is not None and "devices" in snapshot:
            self.device_controller.state = deepcopy(snapshot["devices"])
        if self.session_store is not None and "scene" in snapshot:
            self.session_store.update_scene(snapshot["scene"])
        if self.context is not None:
            if "context_scene" in snapshot:
                self.context.current_scene = snapshot["context_scene"]
            if "context_last_scene" in snapshot:
                self.context.last_scene = snapshot["context_last_scene"]
            if "context_devices" in snapshot:
                self.context.devices = deepcopy(snapshot["context_devices"])
