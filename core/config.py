"""集中化配置管理。所有模块必须从此处引用配置，禁止硬编码。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int = 0) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        return default


def _env_float(key: str, default: float = 0.0) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key, "").lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


# ─────────────────────────────────────────────
# LLM / 推理配置
# ─────────────────────────────────────────────


LLM_BACKEND = _env("LLM_BACKEND", "mock")  # mock | llama_cpp | openai | ollama


EDGE_LLM_PROFILES = {
    "qwen25_1_5b_q4": {
        "display_name": "Qwen2.5-1.5B-Instruct-Q4_K_M",
        "model_path": "models/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf",
        "backend": "llama_cpp",
        "n_ctx": 2048,
        "n_threads": 4,
        "n_gpu_layers": -1,
    },
}


DEFAULT_EDGE_LLM_PROFILE = _env("EDGE_LLM_PROFILE", "qwen25_1_5b_q4")
_DEFAULT_EDGE_LLM = EDGE_LLM_PROFILES.get(
    DEFAULT_EDGE_LLM_PROFILE,
    EDGE_LLM_PROFILES["qwen25_1_5b_q4"],
)


LLAMA_CPP_CONFIG = {
    "profile": DEFAULT_EDGE_LLM_PROFILE,
    "model_path": _env("LLM_MODEL_PATH", _DEFAULT_EDGE_LLM["model_path"]),
    "n_ctx": _env_int("LLAMA_N_CTX", _DEFAULT_EDGE_LLM["n_ctx"]),
    "n_threads": _env_int("LLAMA_N_THREADS", _DEFAULT_EDGE_LLM["n_threads"]),
    "n_gpu_layers": _env_int("LLAMA_N_GPU_LAYERS", _DEFAULT_EDGE_LLM["n_gpu_layers"]),
    "gpu_mode": _env("LLAMA_GPU_MODE", "auto").strip().lower(),
    "use_mlock": True,
    "kv_cache_type": _env("LLAMA_KV_CACHE_TYPE", "Q8_0"),
}


OLLAMA_CONFIG = {
    "base_url": _env("OLLAMA_BASE_URL", "http://localhost:11434"),
    "model": _env("OLLAMA_MODEL", "qwen2.5:1.5b"),
    "fallback_models": ["llama3.2:1b", "tinyllama:1.1b"],
    "timeout": _env_int("OLLAMA_TIMEOUT", 30),
    "num_parallel": _env_int("OLLAMA_NUM_PARALLEL", 1),
}


OPENAI_CONFIG = {
    "api_base": _env("LLM_API_BASE", ""),
    "api_key": _env("LLM_API_KEY", ""),
    "model": _env("LLM_MODEL", "gpt-4o-mini"),
    "enable_fallback": _env_bool("LLM_ENABLE_CLOUD_FALLBACK", True),
    "max_retries": _env_int("OPENAI_MAX_RETRIES", 3),
    "timeout": _env_int("OPENAI_TIMEOUT", 30),
    "log_policy": _env("CLOUD_LOG_POLICY", "none").strip().lower(),  # none | metadata
    "log_retention_days": _env_int("CLOUD_LOG_RETENTION_DAYS", 0),
    "log_path": _env("CLOUD_LOG_PATH", ""),
}


# 推理路由阈值
ROUTING_THRESHOLDS = {
    "local": _env_float("ROUTING_LOCAL_THRESHOLD", 0.70),
    "cloud": _env_float("ROUTING_CLOUD_THRESHOLD", 0.40),
}


# ─────────────────────────────────────────────
# ReAct 推理配置
# ─────────────────────────────────────────────


REACT_CONFIG = {
    "max_iterations": _env_int("REACT_MAX_ITERATIONS", 3),
    "confidence_exit_threshold": _env_float("REACT_CONFIDENCE_EXIT", 0.95),
    "iteration_timeout_s": _env_int("REACT_ITERATION_TIMEOUT_S", 10),
}


# ─────────────────────────────────────────────
# 协议配置
# ─────────────────────────────────────────────


PROTOCOL_CONFIG = {
    "mqtt": {
        "broker": _env("MQTT_BROKER", "localhost"),
        "port": _env_int("MQTT_PORT", 1883),
        "username": _env("MQTT_USERNAME", ""),
        "password": _env("MQTT_PASSWORD", ""),
        "use_tls": _env_bool("MQTT_USE_TLS", False),
        "auto_reconnect": True,
        "reconnect_delay_s": 5,
    },
    "home_assistant": {
        "url": _env("HA_URL", "http://localhost:8123"),
        "token": _env("HA_TOKEN", ""),
    },
    "xiaomi": {
        "gateway_ip": _env("XIAOMI_GATEWAY_IP", ""),
        "did": _env("XIAOMI_DID", ""),
    },
    "matter": {
        "controller_ip": _env("MATTER_CONTROLLER_IP", ""),
        "port": _env_int("MATTER_PORT", 5580),
    },
}


# ─────────────────────────────────────────────
# 存储路径
# ─────────────────────────────────────────────


STORAGE_CONFIG = {
    "data_dir": _env("HOMEMIND_DATA_DIR", "data"),
    "models_dir": _env("HOMEMIND_MODELS_DIR", "models"),
    "logs_dir": _env("HOMEMIND_LOGS_DIR", "logs"),
    "traces_dir": _env("HOMEMIND_TRACES_DIR", "traces"),
}


# ─────────────────────────────────────────────
# RAG 配置
# ─────────────────────────────────────────────


RAG_CONFIG = {
    "top_k": _env_int("RAG_TOP_K", 3),
    "max_context_tokens": _env_int("RAG_MAX_CONTEXT_TOKENS", 500),
    "hot_cache_ttl_hours": _env_int("RAG_HOT_CACHE_TTL_HOURS", 6),
    "semantic_compression_target": _env_int("RAG_COMPRESSION_TARGET", 300),
    "max_records": _env_int("RAG_MAX_RECORDS", 500),
    "use_faiss": _env_bool("RAG_USE_FAISS", True),
}


# ─────────────────────────────────────────────
# 可观测性配置
# ─────────────────────────────────────────────


OBSERVABILITY_CONFIG = {
    "enabled": _env_bool("OBSERVABILITY_ENABLED", True),
    "otel_endpoint": _env("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
    "log_traces": _env_bool("OBSERVABILITY_LOG_TRACES", True),
    "trace_retention_days": _env_int("TRACE_RETENTION_DAYS", 7),
    "metrics_port": _env_int("METRICS_PORT", 9090),
}


ALERT_THRESHOLDS = {
    "memory_percent_warning": _env_int("ALERT_MEMORY_WARNING_PCT", 70),
    "memory_percent_critical": _env_int("ALERT_MEMORY_CRITICAL_PCT", 80),
    "latency_p95_ms_warning": _env_int("ALERT_LATENCY_P95_MS", 8000),
    "cloud_ratio_warning": _env_float("ALERT_CLOUD_RATIO_WARNING", 0.30),
    "protocol_disconnect_minutes": _env_int("ALERT_DISCONNECT_MINUTES", 5),
}


# ─────────────────────────────────────────────
# 安全与治理配置
# ─────────────────────────────────────────────


SECURITY_CONFIG = {
    "autonomy_high_risk_confirms": _env_int("AUTONOMY_HIGH_RISK_CONFIRMS", 5),
    "autonomy_success_threshold": _env_float("AUTONOMY_SUCCESS_THRESHOLD", 0.80),
    "rate_limit_window_s": _env_int("RATE_LIMIT_WINDOW_S", 30),
    "rate_limit_max_ops": _env_int("RATE_LIMIT_MAX_OPS", 5),
    "audit_retention_days": _env_int("AUDIT_RETENTION_DAYS", 90),
    "audit_encrypt_fields": _env_bool("AUDIT_ENCRYPT_FIELDS", True),
    "audit_hash_chain": _env_bool("AUDIT_HASH_CHAIN", True),
}


# ─────────────────────────────────────────────
# OTA / 生命周期配置
# ─────────────────────────────────────────────


OTA_CONFIG = {
    "model_update_check_interval_h": _env_int("MODEL_UPDATE_CHECK_H", 24),
    "rule_backup_count": _env_int("RULE_BACKUP_COUNT", 5),
}


# ─────────────────────────────────────────────
# DQN 学习配置
# ─────────────────────────────────────────────


DQN_CONFIG = {
    "epsilon_start": _env_float("DQN_EPSILON_START", 0.30),
    "epsilon_min": _env_float("DQN_EPSILON_MIN", 0.05),
    "gamma": _env_float("DQN_GAMMA", 0.95),
    "lr": _env_float("DQN_LR", 0.001),
    "replay_capacity": _env_int("DQN_REPLAY_CAPACITY", 1000),
    "update_freq": _env_int("DQN_UPDATE_FREQ", 50),
    "target_sync_freq": _env_int("DQN_TARGET_SYNC_FREQ", 250),
    "batch_size": _env_int("DQN_BATCH_SIZE", 16),
}


# ─────────────────────────────────────────────
# 全局开关
# ─────────────────────────────────────────────


GLOBAL_CONFIG = {
    "confidence_threshold": _env_float("CONFIDENCE_THRESHOLD", 0.75),
    "mode": _env("HOMEMIND_MODE", "simulated"),
    "debug": _env_bool("HOMEMIND_DEBUG", False),
}
