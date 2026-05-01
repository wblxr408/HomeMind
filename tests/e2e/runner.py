"""HomeMind 端到端场景测试运行器。

用法:
    pytest tests/e2e/                    # 运行全部场景
    pytest tests/e2e/ -k "device"       # 只跑设备控制场景
    python tests/e2e/runner.py          # 直接运行，输出 pass@k 报告
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


@dataclass
class Scenario:
    id: str
    category: str
    description: str
    input: str
    expected_action: str
    expected_device: str = ""
    expected_scene: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    passes: int = 0
    fails: int = 0
    runs: int = 0


def load_scenarios() -> List[Scenario]:
    scenarios = []
    for path in sorted(SCENARIOS_DIR.glob("*.yaml")):
        try:
            import yaml

            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not data:
                continue
            for item in data.get("scenarios", []):
                scenarios.append(Scenario(
                    id=str(item.get("id", path.stem)),
                    category=str(item.get("category", "unknown")),
                    description=str(item.get("description", "")),
                    input=str(item.get("input", "")),
                    expected_action=str(item.get("expected_action", "")),
                    expected_device=str(item.get("expected_device", "")),
                    expected_scene=str(item.get("expected_scene", "")),
                    context=item.get("context", {}),
                ))
        except ImportError:
            logger.warning("PyYAML not installed; skipping scenario file %s", path)
        except Exception as exc:
            logger.warning("Failed to load scenario file %s: %s", path, exc)
    logger.info("Loaded %d test scenarios from %s", len(scenarios), SCENARIOS_DIR)
    return scenarios


def run_scenario(scenario: Scenario, agent) -> bool:
    scenario.runs += 1
    try:
        for key, value in scenario.context.items():
            agent.update_context(**{key: value})

        result = agent.process(scenario.input)
        result_lower = result.lower()

        if scenario.expected_action:
            if scenario.expected_action.lower() not in result_lower:
                scenario.fails += 1
                logger.warning("  FAIL [%s] expected '%s' in result", scenario.id, scenario.expected_action)
                return False

        scenario.passes += 1
        logger.info("  PASS [%s]", scenario.id)
        return True
    except Exception as exc:
        scenario.fails += 1
        logger.error("  ERROR [%s] %s", scenario.id, exc)
        return False


def report(scenarios: List[Scenario]) -> Dict[str, Any]:
    total = len(scenarios)
    total_runs = sum(s.runs for s in scenarios)
    total_passes = sum(s.passes for s in scenarios)
    total_fails = sum(s.fails for s in scenarios)

    by_category: Dict[str, Dict] = {}
    for s in scenarios:
        cat = by_category.setdefault(s.category, {"pass": 0, "fail": 0, "total": 0})
        cat["total"] += 1
        if s.passes > 0:
            cat["pass"] += 1
        else:
            cat["fail"] += 1

    pass_rate = (total_passes / total_runs * 100) if total_runs > 0 else 0.0

    print("\n" + "=" * 60)
    print("  HomeMind E2E 测试报告")
    print("=" * 60)
    print(f"  总场景: {total}  总运行: {total_runs}")
    print(f"  通过:   {total_passes}  失败: {total_fails}  通过率: {pass_rate:.1f}%")
    print()
    print("  按类别:")
    for cat, stats in sorted(by_category.items()):
        cat_rate = (stats["pass"] / stats["total"] * 100) if stats["total"] > 0 else 0
        print(f"    {cat:<20} {stats['pass']}/{stats['total']} ({cat_rate:.0f}%)")
    print("=" * 60)

    return {
        "total_scenarios": total,
        "total_runs": total_runs,
        "passes": total_passes,
        "fails": total_fails,
        "pass_rate": pass_rate,
        "by_category": by_category,
    }


def main():
    from main import HomeMindAgent
    from demo.simulator import HomeSimulator

    scenarios = load_scenarios()
    if not scenarios:
        print("No scenarios found in", SCENARIOS_DIR)
        return

    agent = HomeMindAgent()
    sim = HomeSimulator()
    agent.attach_simulator(sim)

    print(f"\nRunning {len(scenarios)} E2E scenarios...\n")
    for scenario in scenarios:
        run_scenario(scenario, agent)

    report_data = report(scenarios)

    if report_data["fails"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
