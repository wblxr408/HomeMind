#!/usr/bin/env python3
"""Measure HomeMind memory profiles in isolated subprocesses.

Each profile runs in a fresh Python process and prints RSS checkpoints. This is
more reliable than measuring several heavy libraries in one process because
imports such as torch, chromadb, sentence-transformers, and llama.cpp share
runtime state and allocator caches.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(os.environ.get("HM_REPO_ROOT", Path(__file__).resolve().parents[1])).resolve()


CHILD_RUNNER = r"""
import gc
import json
import os
import sys
import time
from pathlib import Path

import psutil

REPO_ROOT = Path(os.environ.get("HM_REPO_ROOT", Path(__file__).resolve().parents[1])).resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

proc = psutil.Process()
checkpoints = []

def rss_mb():
    gc.collect()
    return round(proc.memory_info().rss / 1024 / 1024, 2)

def mark(name):
    checkpoints.append({"name": name, "rss_mb": rss_mb()})

def finish(profile, status="ok", error=""):
    print(json.dumps({
        "profile": profile,
        "status": status,
        "error": error,
        "checkpoints": checkpoints,
        "peak_rss_mb": max([item["rss_mb"] for item in checkpoints] or [rss_mb()]),
    }, ensure_ascii=False))

profile = os.environ.get("HM_MEM_PROFILE", "")
mark("python_start")

try:
    if profile == "web_agent":
        os.environ.setdefault("HOMEMIND_STORAGE_KEY", "mem-profile-key")
        os.environ.setdefault("HOMEMIND_DQN_MODEL_DIR", str(REPO_ROOT / "data" / "memory_profile_dqn"))
        from web import server
        mark("after_import_web_server")
        server.init_agent(mode="simulated", init_reason="memory_profile", force_reinit=True)
        mark("after_init_agent")
        client = server.app.test_client()
        for query in ["打开空调", "有点热", "打开灯光", "我要睡觉了", "turn on the light"] * int(os.environ.get("HM_MEM_QUERY_LOOPS", "10")):
            client.post("/api/query", json={"query": query})
        mark("after_queries")
        finish(profile)

    elif profile == "core_agent":
        os.environ.setdefault("HOMEMIND_STORAGE_KEY", "mem-profile-key")
        os.environ.setdefault("HOMEMIND_DQN_MODEL_DIR", str(REPO_ROOT / "data" / "memory_profile_dqn"))
        from main import HomeMindAgent
        mark("after_import_main")
        agent = HomeMindAgent()
        mark("after_init_agent")
        for query in ["打开空调", "有点热", "打开灯光", "我要睡觉了", "你好"] * int(os.environ.get("HM_MEM_QUERY_LOOPS", "10")):
            agent.process(query)
        mark("after_queries")
        finish(profile)

    elif profile == "embedding":
        model_name = os.environ.get("HM_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
        from sentence_transformers import SentenceTransformer
        mark("after_import_sentence_transformers")
        model = SentenceTransformer(model_name)
        mark("after_model_load")
        model.encode(["打开空调", "关闭灯光", "切换睡眠模式"] * 8)
        mark("after_encode")
        finish(profile)

    elif profile == "chromadb":
        persist_dir = os.environ.get("HM_CHROMA_DIR", str(REPO_ROOT / "data" / "chroma_db"))
        import chromadb
        mark("after_import_chromadb")
        client = chromadb.PersistentClient(path=persist_dir)
        mark("after_persistent_client")
        collection = client.get_or_create_collection("memory_profile")
        collection.upsert(
            ids=[f"id_{i}" for i in range(100)],
            documents=[f"用户习惯记录 {i}: 晚上打开空调" for i in range(100)],
            embeddings=[[float((i + j) % 7) / 7 for j in range(16)] for i in range(100)],
        )
        mark("after_upsert_100")
        collection.query(query_embeddings=[[0.1] * 16], n_results=5)
        mark("after_query")
        finish(profile)

    elif profile == "torch":
        import torch
        mark("after_import_torch")
        tensor = torch.randn(2048, 2048)
        mark("after_tensor_alloc")
        _ = tensor @ tensor
        mark("after_matmul")
        from core.dqn.policy import DQNPolicy
        policy = DQNPolicy(model_dir=os.environ.get("HOMEMIND_DQN_MODEL_DIR", str(REPO_ROOT / "data" / "memory_profile_dqn")))
        mark("after_dqn_policy")
        finish(profile)

    elif profile == "llama_cpp":
        model_path = os.environ.get("HM_LLAMA_MODEL", "").strip()
        if not model_path:
            raise RuntimeError("HM_LLAMA_MODEL is required for llama_cpp profile")
        from llama_cpp import Llama
        mark("after_import_llama_cpp")
        llm = Llama(
            model_path=model_path,
            n_ctx=int(os.environ.get("HM_LLAMA_CTX", "2048")),
            n_threads=int(os.environ.get("HM_LLAMA_THREADS", "4")),
            n_gpu_layers=int(os.environ.get("HM_LLAMA_GPU_LAYERS", "0")),
            verbose=False,
        )
        mark("after_model_load")
        llm("请把用户指令转换为智能家居动作：打开空调", max_tokens=64)
        mark("after_first_inference")
        finish(profile)

    elif profile == "full_local":
        os.environ.setdefault("HOMEMIND_STORAGE_KEY", "mem-profile-key")
        os.environ.setdefault("HOMEMIND_DQN_MODEL_DIR", str(REPO_ROOT / "data" / "memory_profile_dqn"))
        embedding_model = os.environ.get("HM_EMBEDDING_MODEL", "").strip()
        llama_model = os.environ.get("HM_LLAMA_MODEL", "").strip()

        if embedding_model:
            from sentence_transformers import SentenceTransformer
            mark("after_import_sentence_transformers")
            emb = SentenceTransformer(embedding_model)
            mark("after_embedding_load")
            emb.encode(["打开空调", "关闭灯光"])
            mark("after_embedding_encode")

        try:
            import chromadb
            mark("after_import_chromadb")
            chromadb.PersistentClient(path=os.environ.get("HM_CHROMA_DIR", str(REPO_ROOT / "data" / "chroma_db")))
            mark("after_chromadb_client")
        except Exception as exc:
            checkpoints.append({"name": "chromadb_unavailable", "rss_mb": rss_mb(), "error": str(exc)})

        try:
            import torch
            mark("after_import_torch")
        except Exception as exc:
            checkpoints.append({"name": "torch_unavailable", "rss_mb": rss_mb(), "error": str(exc)})

        if llama_model:
            from llama_cpp import Llama
            mark("after_import_llama_cpp")
            Llama(
                model_path=llama_model,
                n_ctx=int(os.environ.get("HM_LLAMA_CTX", "2048")),
                n_threads=int(os.environ.get("HM_LLAMA_THREADS", "4")),
                n_gpu_layers=int(os.environ.get("HM_LLAMA_GPU_LAYERS", "0")),
                verbose=False,
            )
            mark("after_llama_load")

        from web import server
        mark("after_import_web_server")
        server.init_agent(mode="simulated", init_reason="full_local_memory_profile", force_reinit=True)
        mark("after_init_agent")
        finish(profile)

    else:
        raise RuntimeError(f"unknown profile: {profile}")

except Exception as exc:
    mark("error")
    finish(profile, status="error", error=f"{type(exc).__name__}: {exc}")
"""


def run_profile(profile: str, args: argparse.Namespace) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("PYTHONHASHSEED", "0")
    env["HM_REPO_ROOT"] = str(REPO_ROOT)
    env["HM_MEM_PROFILE"] = profile
    env["HM_MEM_QUERY_LOOPS"] = str(args.query_loops)
    if args.embedding_model:
        env["HM_EMBEDDING_MODEL"] = args.embedding_model
    if args.chroma_dir:
        env["HM_CHROMA_DIR"] = args.chroma_dir
    if args.llama_model:
        env["HM_LLAMA_MODEL"] = args.llama_model
    env["HM_LLAMA_CTX"] = str(args.llama_ctx)
    env["HM_LLAMA_THREADS"] = str(args.llama_threads)
    env["HM_LLAMA_GPU_LAYERS"] = str(args.llama_gpu_layers)

    with tempfile.NamedTemporaryFile("w", suffix=".py", encoding="utf-8", delete=False) as handle:
        handle.write(CHILD_RUNNER)
        child_path = handle.name
    try:
        completed = subprocess.run(
            [sys.executable, child_path],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=args.timeout,
        )
    except subprocess.TimeoutExpired as exc:
        Path(child_path).unlink(missing_ok=True)
        return {
            "profile": profile,
            "status": "timeout",
            "error": f"profile exceeded timeout {args.timeout}s",
            "checkpoints": [],
            "peak_rss_mb": 0,
            "returncode": None,
            "stdout_tail": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
        }
    finally:
        Path(child_path).unlink(missing_ok=True)

    parsed = None
    for line in reversed((completed.stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
            break
        except json.JSONDecodeError:
            continue

    if parsed is None:
        parsed = {
            "profile": profile,
            "status": "error",
            "error": "profile did not emit JSON",
            "checkpoints": [],
            "peak_rss_mb": 0,
        }

    parsed["returncode"] = completed.returncode
    if args.include_stderr:
        parsed["stderr_tail"] = (completed.stderr or "")[-4000:]
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure HomeMind RSS memory profiles.")
    parser.add_argument(
        "--profiles",
        nargs="+",
        default=["web_agent", "core_agent", "embedding", "chromadb", "torch", "llama_cpp", "full_local"],
        help="Profiles to run.",
    )
    parser.add_argument("--embedding-model", default="", help="SentenceTransformer model name or local path.")
    parser.add_argument("--chroma-dir", default=str(REPO_ROOT / "data" / "chroma_db"), help="ChromaDB persist dir.")
    parser.add_argument("--llama-model", default="", help="Path to .gguf model for llama.cpp profiles.")
    parser.add_argument("--llama-ctx", type=int, default=2048)
    parser.add_argument("--llama-threads", type=int, default=4)
    parser.add_argument("--llama-gpu-layers", type=int, default=0)
    parser.add_argument("--query-loops", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--include-stderr", action="store_true")
    parser.add_argument("--output", default="", help="Optional JSON output path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    results = []
    for profile in args.profiles:
        print(f"[memory-profile] starting {profile}", flush=True)
        result = run_profile(profile, args)
        print(
            f"[memory-profile] finished {profile}: {result.get('status')} "
            f"peak={result.get('peak_rss_mb')}MB",
            flush=True,
        )
        results.append(result)
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "repo": str(REPO_ROOT),
        "profiles": results,
    }

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
