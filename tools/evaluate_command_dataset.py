#!/usr/bin/env python3
"""Run data-oriented HomeMind command evaluation.

This script injects a fixed user-command dataset through the Flask API test
client, then writes CSV/JSON/Markdown reports with expected-vs-actual labels,
latency metrics, and routing pipeline observations.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@dataclass(frozen=True)
class CommandSample:
    sample_id: str
    category: str
    query: str
    expected_status: str = ""
    expected_action: str = ""
    expected_route: str = ""
    expected_response_type: str = ""
    expected_target: str = ""
    setup_queries: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""


def build_samples() -> list[CommandSample]:
    samples: list[CommandSample] = []

    def add(
        category: str,
        query: str,
        expected_status: str = "success",
        expected_action: str = "",
        expected_route: str = "",
        expected_response_type: str = "execution_result",
        expected_target: str = "",
        setup_queries: tuple[str, ...] = (),
        notes: str = "",
    ) -> None:
        samples.append(
            CommandSample(
                sample_id=f"S{len(samples) + 1:03d}",
                category=category,
                query=query,
                expected_status=expected_status,
                expected_action=expected_action,
                expected_route=expected_route,
                expected_response_type=expected_response_type,
                expected_target=expected_target,
                setup_queries=setup_queries,
                notes=notes,
            )
        )

    device_cases = [
        ("打开空调", "空调_on"),
        ("把空调打开", "空调_on"),
        ("空调开起来", "空调_on"),
        ("有点热", "空调_on"),
        ("太热了", "空调_on"),
        ("屋里闷得很", "空调_on"),
        ("关闭空调", "空调_off"),
        ("把空调关掉", "空调_off"),
        ("有点冷", "空调_on"),
        ("太冷了", "空调_on"),
        ("打开灯光", "灯光_on"),
        ("开一下灯", "灯光_on"),
        ("把灯打开", "灯光_on"),
        ("关闭灯光", "灯光_off"),
        ("关灯", "灯光_off"),
        ("灯光调亮一点", "灯光_adjust"),
        ("灯光暗一点", "灯光_adjust"),
        ("太暗了", "灯光_adjust"),
        ("太亮了", "灯光_adjust"),
        ("打开电视", "电视_on"),
        ("关闭电视", "电视_off"),
        ("打开风扇", "风扇_on"),
        ("关闭风扇", "风扇_off"),
        ("打开窗户", "窗户_open"),
        ("关闭窗户", "窗户_close"),
        ("打开音响", "音响_on"),
        ("关闭音响", "音响_off"),
        ("打开热水器", "热水器_on"),
        ("关闭热水器", "热水器_off"),
    ]
    for query, action in device_cases:
        add("device_control", query, expected_action=action, expected_route="local")

    scene_cases = [
        ("切换到睡眠模式", "scene_switch"),
        ("我要睡觉了", "scene_switch"),
        ("我困了", "scene_switch"),
        ("切换到离家模式", "scene_switch"),
        ("我要走了", "scene_switch"),
        ("准备出门了", "scene_switch"),
        ("切换到回家模式", "scene_switch"),
        ("我回家了", "scene_switch"),
        ("切换到观影模式", "scene_switch"),
        ("我要看电影", "scene_switch"),
        ("切换到待客模式", "scene_switch"),
        ("客人来了", "scene_switch"),
        ("早安", "scene_switch"),
        ("起床了", "scene_switch"),
        ("切换到工作模式", "scene_switch"),
        ("切换到晚归模式", "scene_switch"),
    ]
    for query, action in scene_cases:
        add("scene_switch", query, expected_action=action, expected_route="local")

    english_cases = [
        ("turn on the ac", "空调_on"),
        ("turn off the ac", "空调_off"),
        ("turn on the light", "灯光_on"),
        ("turn off the lights", "灯光_off"),
        ("brighten the light", "灯光_adjust"),
        ("dim the lights", "灯光_adjust"),
        ("turn on the tv", "电视_on"),
        ("turn off the television", "电视_off"),
        ("sleep mode", "scene_switch"),
        ("movie mode", "scene_switch"),
        ("away mode", "scene_switch"),
        ("I'm leaving", "scene_switch"),
    ]
    for query, action in english_cases:
        add("language_normalization", query, expected_action=action, expected_route="local")

    automation_cases = [
        "晚上7:00打开空调",
        "明天早上7点打开灯光",
        "晚上10点切换到睡眠模式",
        "每天8:30打开窗户",
        "早上6点切换起床模式",
        "下午3点打开风扇",
        "晚上9点关闭电视",
        "中午12点关闭空调",
    ]
    for query in automation_cases:
        add(
            "automation_request",
            query,
            expected_status="success",
            expected_route="automation",
            expected_response_type="automation_proposal",
        )

    chat_cases = ["你好", "您好", "hello", "hi", "谢谢", "thanks", "再见", "bye"]
    for query in chat_cases:
        add(
            "chat_reply",
            query,
            expected_status="success",
            expected_route="reply",
            expected_response_type="chat",
        )

    clarify_cases = ["像昨天那样", "你看着办", "随便", "帮我弄一下", "舒服一点"]
    for query in clarify_cases:
        add(
            "clarification",
            query,
            expected_status="clarification",
            expected_route="clarify",
            expected_response_type="clarification",
        )

    unsupported_cases = [
        ("帮我打开扫地机器人", "扫地机器人"),
        ("打开冰箱", "冰箱"),
        ("关闭洗衣机", "洗衣机"),
        ("帮我打开闹钟", "闹钟"),
        ("启动咖啡机", "咖啡机"),
        ("打开投影仪", "投影仪"),
    ]
    for query, target in unsupported_cases:
        add(
            "unsupported_target",
            query,
            expected_status="unsupported",
            expected_route="unsupported",
            expected_response_type="clarification",
            expected_target=target,
        )

    follow_up_cases = [
        ("再调亮", "灯光_adjust", ("打开灯光",)),
        ("再暗一点", "灯光_adjust", ("打开灯光",)),
        ("关掉它", "", ("打开电视",)),
    ]
    for query, action, setup in follow_up_cases:
        add(
            "context_follow_up",
            query,
            expected_status="success",
            expected_action=action,
            expected_route="local",
            setup_queries=setup,
            notes="Uses setup query to seed short-term context.",
        )

    return samples


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * pct
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def reset_agent_state(agent: Any) -> None:
    agent.session_store.data = agent.session_store._default_data()
    agent.preference_store.data = agent.preference_store._default_data()
    agent.tap_rule_store.rules = []
    agent.last_route_info = {}
    agent.last_cloud_context = {}
    agent.context.current_scene = "sleep"
    agent.context.temperature = 25.0
    agent.context.humidity = 60.0
    agent.context.members_home = 1


def seed_spatial_floor_plan(work_root: Path) -> None:
    """Create the active SVG floor plan and device table required by strict spatial execution."""
    plan_id = "eval-floor-plan.svg"
    floor_plan_dir = work_root / "uploads" / "floor-plans"
    data_dir = work_root / "data"
    floor_plan_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    svg_path = floor_plan_dir / plan_id
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 660">'
        '<rect width="640" height="660" fill="#f8f9fa"/></svg>',
        encoding="utf-8",
    )

    floor_plan = {
        "id": plan_id,
        "name": "Evaluation Floor Plan",
        "description": "Seeded for strict SVG/device-table command evaluation",
        "filePath": str(svg_path),
        "url": f"/uploads/floor-plans/{plan_id}",
        "width": 640,
        "height": 660,
        "active": True,
    }
    devices = [
        {"id": "light.living_room_main", "name": "\u5ba2\u5385\u706f", "type": "light", "area": "living_room", "areaName": "\u5ba2\u5385", "x": 12, "y": 20},
        {"id": "climate.living_room_ac", "name": "\u5ba2\u5385\u7a7a\u8c03", "type": "air_conditioner", "area": "living_room", "areaName": "\u5ba2\u5385", "x": 18, "y": 20},
        {"id": "media.living_room_tv", "name": "\u5ba2\u5385\u7535\u89c6", "type": "tv", "area": "living_room", "areaName": "\u5ba2\u5385", "x": 24, "y": 20},
        {"id": "speaker.living_room", "name": "\u5ba2\u5385\u97f3\u54cd", "type": "speaker", "area": "living_room", "areaName": "\u5ba2\u5385", "x": 30, "y": 20},
        {"id": "fan.bedroom", "name": "\u4e3b\u5367\u98ce\u6247", "type": "fan", "area": "bedroom", "areaName": "\u4e3b\u5367", "x": 50, "y": 30},
        {"id": "cover.bedroom_window", "name": "\u4e3b\u5367\u7a97\u6237", "type": "window", "area": "bedroom", "areaName": "\u4e3b\u5367", "x": 55, "y": 30},
        {"id": "water_heater.bathroom", "name": "\u4e3b\u536b\u70ed\u6c34\u5668", "type": "water_heater", "area": "bathroom1", "areaName": "\u4e3b\u536b", "x": 58, "y": 72},
    ]
    mapping = {
        "floorPlanId": plan_id,
        "devices": devices,
        "rawDevices": devices,
        "areaNames": {"living_room": "\u5ba2\u5385", "bedroom": "\u4e3b\u5367", "bathroom1": "\u4e3b\u536b"},
    }
    (data_dir / "floor-plans.json").write_text(
        json.dumps([floor_plan], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (data_dir / "devices.json").write_text(
        json.dumps([mapping], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def inspect_pipeline(server: Any, query: str) -> dict[str, Any]:
    agent = server.agent
    normalized = server.language_normalizer.normalize(query)
    normalized_text = normalized.normalized or query
    intent = agent.llm.plan_intent(query, normalized_query=normalized_text, context=agent.context)
    result: dict[str, Any] = {
        "normalized": normalized_text,
        "normalizer_rule": getattr(normalized, "matched_rule", ""),
        "normalizer_confidence": getattr(normalized, "confidence", 0.0),
        "intent_type": intent.get("intent_type", ""),
        "intent_confidence": intent.get("decision_confidence", 0.0),
        "top_candidate": "",
        "top_candidate_score": "",
        "predicted_route": intent.get("route", ""),
        "predicted_route_reason": "",
    }

    if intent.get("requires_candidates") and agent.bsr and agent.lsr:
        candidates = agent.bsr.recall(normalized_text, agent.context)
        ranked = agent.lsr.rank(
            normalized_text,
            candidates,
            agent.context,
            kb=agent.kb,
            session_store=agent.session_store,
        )
        if ranked:
            result["top_candidate"] = ranked[0].get("action", "")
            result["top_candidate_score"] = ranked[0].get("final_score", ranked[0].get("score", ""))
        route = agent.router.decide_route(
            query,
            ranked,
            normalized_query=normalized_text,
            cloud_available=agent.llm.is_cloud_available(),
        )
        result["predicted_route"] = route.get("route", "")
        result["predicted_route_reason"] = route.get("reason", "")

    return result


def post_query(client: Any, query: str) -> tuple[int, dict[str, Any], float]:
    start = time.perf_counter()
    response = client.post("/api/query", json={"query": query})
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return response.status_code, response.get_json() or {}, elapsed_ms


def run_evaluation(samples: list[CommandSample]) -> tuple[list[dict[str, Any]], dict[str, Any], Path]:
    work_root = Path(tempfile.mkdtemp(prefix="homemind_eval_"))
    previous_cwd = Path.cwd()
    os.chdir(work_root)
    seed_spatial_floor_plan(work_root)
    os.environ.setdefault("HOMEMIND_STORAGE_KEY", "homemind-eval-storage-key")
    os.environ["HOMEMIND_DQN_MODEL_DIR"] = str(work_root / "data" / "dqn_models")

    server = None
    from web import server

    server.init_agent(mode="simulated", init_reason="command_dataset_eval", force_reinit=True)
    client = server.app.test_client()
    rows: list[dict[str, Any]] = []

    try:
        for sample in samples:
            reset_agent_state(server.agent)
            for setup_query in sample.setup_queries:
                post_query(client, setup_query)

            pipeline = inspect_pipeline(server, sample.query)
            http_status, payload, latency_ms = post_query(client, sample.query)

            actual_status = normalize_text(payload.get("status"))
            actual_action = normalize_text(payload.get("action"))
            actual_route = normalize_text(payload.get("route"))
            actual_response_type = normalize_text(payload.get("response_type"))
            actual_target = normalize_text(payload.get("target"))

            status_match = not sample.expected_status or actual_status == sample.expected_status
            action_match = not sample.expected_action or actual_action == sample.expected_action
            route_match = not sample.expected_route or actual_route == sample.expected_route
            response_type_match = (
                not sample.expected_response_type
                or actual_response_type == sample.expected_response_type
            )
            target_match = not sample.expected_target or actual_target == sample.expected_target
            passed = all([http_status == 200, status_match, action_match, route_match, response_type_match, target_match])

            rows.append(
                {
                    "sample_id": sample.sample_id,
                    "category": sample.category,
                    "query": sample.query,
                    "setup_queries": " | ".join(sample.setup_queries),
                    "expected_status": sample.expected_status,
                    "actual_status": actual_status,
                    "status_match": status_match,
                    "expected_action": sample.expected_action,
                    "actual_action": actual_action,
                    "action_match": action_match,
                    "expected_route": sample.expected_route,
                    "actual_route": actual_route,
                    "route_match": route_match,
                    "expected_response_type": sample.expected_response_type,
                    "actual_response_type": actual_response_type,
                    "response_type_match": response_type_match,
                    "expected_target": sample.expected_target,
                    "actual_target": actual_target,
                    "target_match": target_match,
                    "http_status": http_status,
                    "latency_ms": round(latency_ms, 3),
                    "normalized": pipeline.get("normalized", ""),
                    "normalizer_rule": pipeline.get("normalizer_rule", ""),
                    "normalizer_confidence": pipeline.get("normalizer_confidence", ""),
                    "intent_type": pipeline.get("intent_type", ""),
                    "intent_confidence": pipeline.get("intent_confidence", ""),
                    "top_candidate": pipeline.get("top_candidate", ""),
                    "top_candidate_score": pipeline.get("top_candidate_score", ""),
                    "predicted_route": pipeline.get("predicted_route", ""),
                    "predicted_route_reason": pipeline.get("predicted_route_reason", ""),
                    "route_reason": payload.get("route_reason", ""),
                    "response_excerpt": normalize_text(payload.get("response"))[:120],
                    "passed": passed,
                    "notes": sample.notes,
                }
            )
            server.agent.session_store.clear_pending_confirmation()

        startup_metrics = getattr(server.agent, "startup_metrics", None)
        if startup_metrics is None:
            startup_metrics = getattr(server.agent, "_startup_metrics", {})
        summary = build_summary(rows, startup_metrics, server.agent.get_privacy_status())
        return rows, summary, work_root
    finally:
        if server is not None:
            server.agent = None
        os.chdir(previous_cwd)


def build_summary(rows: list[dict[str, Any]], startup_metrics: dict[str, Any], privacy_status: dict[str, Any]) -> dict[str, Any]:
    total = len(rows)
    passed = sum(1 for row in rows if row["passed"])
    latencies = [float(row["latency_ms"]) for row in rows]
    by_category: dict[str, dict[str, Any]] = {}

    for category in sorted({row["category"] for row in rows}):
        category_rows = [row for row in rows if row["category"] == category]
        category_latencies = [float(row["latency_ms"]) for row in category_rows]
        by_category[category] = {
            "samples": len(category_rows),
            "passed": sum(1 for row in category_rows if row["passed"]),
            "pass_rate": round(sum(1 for row in category_rows if row["passed"]) / len(category_rows), 4),
            "mean_latency_ms": round(statistics.mean(category_latencies), 3),
            "p95_latency_ms": round(percentile(category_latencies, 0.95), 3),
        }

    route_distribution = Counter(row["actual_route"] or "(empty)" for row in rows)
    status_distribution = Counter(row["actual_status"] or "(empty)" for row in rows)
    response_type_distribution = Counter(row["actual_response_type"] or "(empty)" for row in rows)
    mismatches = [
        {
            "sample_id": row["sample_id"],
            "category": row["category"],
            "query": row["query"],
            "expected": {
                "status": row["expected_status"],
                "action": row["expected_action"],
                "route": row["expected_route"],
                "response_type": row["expected_response_type"],
                "target": row["expected_target"],
            },
            "actual": {
                "status": row["actual_status"],
                "action": row["actual_action"],
                "route": row["actual_route"],
                "response_type": row["actual_response_type"],
                "target": row["actual_target"],
            },
        }
        for row in rows
        if not row["passed"]
    ]

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sample_count": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total if total else 0.0, 4),
        "latency": {
            "mean_ms": round(statistics.mean(latencies), 3) if latencies else 0.0,
            "median_ms": round(statistics.median(latencies), 3) if latencies else 0.0,
            "p95_ms": round(percentile(latencies, 0.95), 3),
            "max_ms": round(max(latencies), 3) if latencies else 0.0,
        },
        "by_category": by_category,
        "route_distribution": dict(route_distribution),
        "status_distribution": dict(status_distribution),
        "response_type_distribution": dict(response_type_distribution),
        "startup_metrics": startup_metrics,
        "privacy": {
            "status": privacy_status.get("status", ""),
            "last_route": privacy_status.get("last_route", ""),
            "minimal_fields": privacy_status.get("minimal_fields", []),
            "storage_security": privacy_status.get("storage_security", {}),
        },
        "mismatches": mismatches,
    }


def write_reports(rows: list[dict[str, Any]], summary: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"command_eval_{stamp}.csv"
    json_path = output_dir / f"command_eval_{stamp}.json"
    md_path = output_dir / f"command_eval_{stamp}.md"

    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    with json_path.open("w", encoding="utf-8") as handle:
        json.dump({"summary": summary, "rows": rows}, handle, ensure_ascii=False, indent=2)

    with md_path.open("w", encoding="utf-8") as handle:
        handle.write("# HomeMind Command Dataset Evaluation\n\n")
        handle.write(f"- Generated at: {summary['generated_at']}\n")
        handle.write(f"- Samples: {summary['sample_count']}\n")
        handle.write(f"- Passed: {summary['passed']}\n")
        handle.write(f"- Failed: {summary['failed']}\n")
        handle.write(f"- Pass rate: {summary['pass_rate']:.2%}\n")
        handle.write(
            "- Latency: "
            f"mean {summary['latency']['mean_ms']} ms, "
            f"median {summary['latency']['median_ms']} ms, "
            f"p95 {summary['latency']['p95_ms']} ms, "
            f"max {summary['latency']['max_ms']} ms\n\n"
        )

        handle.write("## Category Summary\n\n")
        handle.write("| Category | Samples | Passed | Pass Rate | Mean Latency ms | P95 Latency ms |\n")
        handle.write("|---|---:|---:|---:|---:|---:|\n")
        for category, metrics in summary["by_category"].items():
            handle.write(
                f"| {category} | {metrics['samples']} | {metrics['passed']} | "
                f"{metrics['pass_rate']:.2%} | {metrics['mean_latency_ms']} | {metrics['p95_latency_ms']} |\n"
            )

        handle.write("\n## Route Distribution\n\n")
        handle.write("| Route | Count |\n|---|---:|\n")
        for route, count in summary["route_distribution"].items():
            handle.write(f"| {route} | {count} |\n")

        handle.write("\n## Mismatches\n\n")
        if not summary["mismatches"]:
            handle.write("No mismatches.\n")
        else:
            handle.write("| ID | Category | Query | Expected | Actual |\n")
            handle.write("|---|---|---|---|---|\n")
            for item in summary["mismatches"]:
                handle.write(
                    f"| {item['sample_id']} | {item['category']} | {item['query']} | "
                    f"{json.dumps(item['expected'], ensure_ascii=False)} | "
                    f"{json.dumps(item['actual'], ensure_ascii=False)} |\n"
                )

        handle.write("\n## Sample-Level Results\n\n")
        handle.write(
            "| ID | Category | Query | Expected Action | Actual Action | "
            "Expected Route | Actual Route | Latency ms | Passed |\n"
        )
        handle.write("|---|---|---|---|---|---|---|---:|---|\n")
        for row in rows:
            handle.write(
                f"| {row['sample_id']} | {row['category']} | {row['query']} | "
                f"{row['expected_action']} | {row['actual_action']} | "
                f"{row['expected_route']} | {row['actual_route']} | "
                f"{row['latency_ms']} | {row['passed']} |\n"
            )

    return {"csv": csv_path, "json": json_path, "markdown": md_path}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate HomeMind against a command dataset.")
    parser.add_argument(
        "--output-dir",
        default=str(REPO_ROOT / "reports"),
        help="Directory for CSV/JSON/Markdown reports.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional number of samples to run; 0 means all samples.",
    )
    parser.add_argument(
        "--keep-workdir",
        action="store_true",
        help="Keep the temporary isolated data directory for debugging.",
    )
    parser.add_argument(
        "--fail-on-mismatch",
        action="store_true",
        help="Return a non-zero exit code when expected-vs-actual mismatches are found.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = REPO_ROOT / output_dir
    samples = build_samples()
    if args.limit:
        samples = samples[: args.limit]

    rows, summary, work_root = run_evaluation(samples)
    paths = write_reports(rows, summary, output_dir)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\nReports:")
    for kind, path in paths.items():
        print(f"- {kind}: {path}")
    print(f"- isolated_workdir: {work_root}")

    if not args.keep_workdir:
        shutil.rmtree(work_root, ignore_errors=True)

    if args.fail_on_mismatch and summary["failed"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
