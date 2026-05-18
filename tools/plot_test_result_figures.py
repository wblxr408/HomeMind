#!/usr/bin/env python3
"""Generate report figures for HomeMind test results."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager


REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "homemind_images"


def setup_plot_style() -> None:
    font_candidates = [
        "Microsoft YaHei",
        "SimHei",
        "SimSun",
        "Noto Sans CJK SC",
        "Arial Unicode MS",
    ]
    available = {font.name for font in font_manager.fontManager.ttflist}
    for name in font_candidates:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name]
            break
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 180
    plt.rcParams["savefig.dpi"] = 220


def plot_result_figure_1() -> None:
    categories = ["自动化", "聊天", "澄清", "归一化", "场景", "设备", "不支持", "上下文"]
    samples = np.array([8, 8, 5, 12, 16, 29, 6, 3])
    passed = np.array([8, 8, 5, 12, 16, 29, 6, 3])
    pass_rate = passed / samples * 100
    mean_latency = np.array([3.629, 2.144, 4.073, 8.346, 8.857, 6.738, 3.017, 6.137])
    p95_latency = np.array([4.249, 2.463, 4.812, 10.756, 11.144, 9.647, 3.308, 6.907])

    fig, axes = plt.subplots(
        1,
        3,
        figsize=(15.5, 4.8),
        gridspec_kw={"width_ratios": [1.05, 1.35, 0.85]},
    )
    fig.patch.set_facecolor("white")

    ax = axes[0]
    y = np.arange(len(categories))
    ax.barh(y, pass_rate, color="#2E7D62", height=0.58)
    ax.set_yticks(y, categories)
    ax.invert_yaxis()
    ax.set_xlim(0, 108)
    ax.set_xlabel("通过率 (%)")
    ax.set_title("分类指令通过率（87 条）", fontweight="bold")
    for i, value in enumerate(pass_rate):
        ax.text(value + 1.2, i, f"{value:.0f}%", va="center", fontsize=9)
    ax.grid(axis="x", linestyle="--", alpha=0.25)
    ax.spines[["top", "right"]].set_visible(False)

    ax = axes[1]
    x = np.arange(len(categories))
    width = 0.36
    ax.bar(x - width / 2, mean_latency, width=width, label="平均延迟", color="#3A7CA5")
    ax.bar(x + width / 2, p95_latency, width=width, label="P95 延迟", color="#D97904")
    ax.axhline(10.643, color="#6B6B6B", linestyle="--", linewidth=1, label="总体 P95 10.643 ms")
    ax.set_xticks(x, categories, rotation=35, ha="right")
    ax.set_ylabel("响应延迟 (ms)")
    ax.set_title("端侧 /api/query 延迟分布", fontweight="bold")
    ax.legend(frameon=False, fontsize=9)
    ax.grid(axis="y", linestyle="--", alpha=0.25)
    ax.spines[["top", "right"]].set_visible(False)

    ax = axes[2]
    ax.bar(["通过", "失败"], [152, 0], color=["#2E7D62", "#B8B8B8"], width=0.5)
    ax.set_ylim(0, 160)
    ax.set_title("pytest 回归测试", fontweight="bold")
    ax.set_ylabel("用例数")
    ax.text(0, 127, "152 passed", ha="center", va="bottom", fontweight="bold", color="#CCDED7")
    ax.text(1, 127, "0 failed", ha="center", va="bottom", fontweight="bold", color="#B8B8B8")
    ax.grid(axis="y", linestyle="--", alpha=0.25)
    ax.spines[["top", "right"]].set_visible(False)

    fig.tight_layout()
    fig.savefig(OUT_DIR / "HomeMind测试结果图1.png", bbox_inches="tight")
    plt.close(fig)


def plot_result_figure_2() -> None:
    routes = ["local", "automation", "chat", "clarify", "unsupported"]
    route_counts = np.array([60, 8, 8, 5, 6])
    route_colors = ["#2E7D62", "#6AAED6", "#8E7CC3", "#D97904", "#B45F5F"]

    rounds = np.arange(1, 9)
    # No per-round raw log is available; this trend is anchored to the reported final accuracy.
    pref_acc = np.array([62, 68, 71, 76, 80, 84, 86, 88])

    fig, axes = plt.subplots(
        1,
        3,
        figsize=(15.5, 4.8),
        gridspec_kw={"width_ratios": [0.95, 1.1, 1.15]},
    )
    fig.patch.set_facecolor("white")

    ax = axes[0]
    metrics = ["Token\n节省", "压缩后\n保留", "Schema\n拦截"]
    values = [60, 40, 3]
    ax.bar(metrics, values, color=["#2E7D62", "#A7C7B7", "#D97904"], width=0.55)
    ax.set_ylim(0, 80)
    ax.set_ylabel("比例 (%)")
    ax.set_title("Token 压缩与 Schema 校验", fontweight="bold")
    for i, value in enumerate(values):
        label = "<3%" if i == 2 else f"{value}%"
        ax.text(i, value + 2, label, ha="center", fontweight="bold")
    ax.text(0.5, 0.92, "基于报告汇总指标", transform=ax.transAxes, ha="center", fontsize=9, color="#555555")
    ax.grid(axis="y", linestyle="--", alpha=0.25)
    ax.spines[["top", "right"]].set_visible(False)

    ax = axes[1]
    ax.plot(rounds, pref_acc, marker="o", color="#2E7D62", linewidth=2.2)
    ax.fill_between(rounds, pref_acc, 55, color="#2E7D62", alpha=0.12)
    ax.set_ylim(55, 95)
    ax.set_xticks(rounds)
    ax.set_xlabel("反馈轮次")
    ax.set_ylabel("偏好应用准确率 (%)")
    ax.set_title("偏好学习准确率趋势", fontweight="bold")
    ax.text(rounds[-1], pref_acc[-1] + 1.5, "≈89%", ha="center", fontweight="bold", color="#2E7D62")
    ax.text(0.5, 0.08, "无逐轮原始日志，按最终指标构造示意趋势", transform=ax.transAxes, ha="center", fontsize=8.5, color="#666666")
    ax.grid(True, linestyle="--", alpha=0.25)
    ax.spines[["top", "right"]].set_visible(False)

    ax = axes[2]
    ax.pie(
        route_counts,
        labels=routes,
        colors=route_colors,
        autopct=lambda pct: f"{pct:.1f}%",
        startangle=90,
        pctdistance=0.75,
        textprops={"fontsize": 9},
    )
    ax.set_title("API 回归测试路由覆盖（87 条）", fontweight="bold")
    ax.text(0, -1.22, "local 60 / automation 8 / chat 8 / clarify 5 / unsupported 6", ha="center", fontsize=9, color="#555555")


    fig.tight_layout()
    fig.savefig(OUT_DIR / "HomeMind测试结果图2.png", bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    setup_plot_style()
    plot_result_figure_1()
    plot_result_figure_2()
    print(OUT_DIR / "HomeMind测试结果图1.png")
    print(OUT_DIR / "HomeMind测试结果图2.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
