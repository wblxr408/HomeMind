"""
Agent Card 管理 — 集中管理所有 Agent 元数据

提供：
- 本地 Agent Card 注册表
- RESTful well-known 端点（/.well-known/agent.json）
- 兼容 LAD-A2A 规范的元数据格式
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.agent.protocols.a2a import AgentCard

logger = logging.getLogger(__name__)


class AgentCardRegistry:
    """
    本地 Agent Card 注册表。

    负责：
    - 注册/注销 Agent Card
    - 导出 well-known JSON
    - 持久化到磁盘
    """

    def __init__(self, storage_path: str = "data/agent_cards.json"):
        self._storage_path = storage_path
        self._cards: Dict[str, AgentCard] = {}
        self._load()

    def register(self, card: AgentCard) -> None:
        self._cards[card.name] = card
        self._save()
        logger.info("AgentCard registered: %s", card.name)

    def unregister(self, name: str) -> bool:
        if name in self._cards:
            del self._cards[name]
            self._save()
            return True
        return False

    def get(self, name: str) -> Optional[AgentCard]:
        return self._cards.get(name)

    def list_all(self) -> List[AgentCard]:
        return list(self._cards.values())

    def to_well_known_json(self) -> str:
        """导出为 LAD-A2A /.well-known/agent.json 兼容格式。"""
        return json.dumps(
            {
                "agents": [card.to_dict() for card in self._cards.values()],
            },
            ensure_ascii=False,
            indent=2,
        )

    def _save(self) -> None:
        Path(self._storage_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._storage_path, "w", encoding="utf-8") as f:
                json.dump(
                    {name: card.to_dict() for name, card in self._cards.items()},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception as exc:
            logger.warning("AgentCardRegistry save failed: %s", exc)

    def _load(self) -> None:
        path = Path(self._storage_path)
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for name, card_data in data.items():
                self._cards[name] = AgentCard(**card_data)
            logger.info("AgentCardRegistry loaded %d cards", len(self._cards))
        except Exception as exc:
            logger.warning("AgentCardRegistry load failed: %s", exc)


# ── 全局单例 ────────────────────────────────────────────────────────────────

_registry: Optional[AgentCardRegistry] = None


def get_card_registry() -> AgentCardRegistry:
    global _registry
    if _registry is None:
        _registry = AgentCardRegistry()
    return _registry
