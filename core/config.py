"""集中化配置管理。所有模块必须从此处引用配置，禁止硬编码。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
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


_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_DIR = _REPO_ROOT / "config"


def _load_json_config(filename: str, default):
    path = _CONFIG_DIR / filename
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def _load_text_config(relative_path: str, default: str = "") -> str:
    path = _CONFIG_DIR / relative_path
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
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

# InferenceRouter 专用阈值（从 config 读取，替代硬编码）
ROUTER_THRESHOLDS = {
    "local": _env_float("ROUTER_LOCAL_THRESHOLD", 0.85),
    "cloud": _env_float("ROUTER_CLOUD_THRESHOLD", 0.55),
}

# InferenceRouter 显式指令匹配模式（可由环境变量覆盖，JSON 数组格式）
_ROUTER_PATTERNS_ENV = os.environ.get("ROUTER_EXPLICIT_PATTERNS", "").strip()
if _ROUTER_PATTERNS_ENV:
    try:
        import json as _json
        ROUTER_EXPLICIT_PATTERNS = _json.loads(_ROUTER_PATTERNS_ENV)
    except Exception:
        ROUTER_EXPLICIT_PATTERNS = None
else:
    ROUTER_EXPLICIT_PATTERNS = None


# ─────────────────────────────────────────────
# ReAct 推理配置
# ─────────────────────────────────────────────


REACT_CONFIG = {
    "max_iterations": _env_int("REACT_MAX_ITERATIONS", 3),
    "confidence_exit_threshold": _env_float("REACT_CONFIDENCE_EXIT", 0.95),
    "iteration_timeout_s": _env_int("REACT_ITERATION_TIMEOUT_S", 10),
}


LLM_DECISION_RULES = _load_json_config(
    "llm_decision_rules.json",
    {
        "device_action_map": {},
        "scene_action_map": {},
        "chat_keywords": {},
        "ambiguous_patterns": [],
        "automation_time_patterns": [],
        "action_hints": [],
        "soft_command_normalizations": [],
        "scene_keywords": [],
        "switch_keywords": [],
        "comfort_keywords": [],
        "explicit_devices": [],
        "explicit_verbs": [],
        "supported_devices": [],
        "supported_scenes": [],
        "clarification_reply": "请问你是想控制设备、切换场景，还是创建定时任务？",
        "comfort_default_prompt": "",
    },
)


PROMPT_TEMPLATES = {
    "intent": _load_text_config("prompts/intent_prompt.txt"),
    "cloud_rescue_intent": _load_text_config("prompts/cloud_rescue_intent_prompt.txt"),
    "cloud_rescue_decision": _load_text_config("prompts/cloud_rescue_decision_prompt.txt"),
    "decision": _load_text_config("prompts/decision_prompt.txt"),
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


# RAG_CONFIG 已移至下方统一配置节
# OBSERVABILITY_CONFIG 在下方定义


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
# RAG / Knowledge Base 配置
# ─────────────────────────────────────────────

RAG_CONFIG = {
    "top_k": _env_int("RAG_TOP_K", 3),
    "max_context_tokens": _env_int("RAG_MAX_CONTEXT_TOKENS", 500),
    "hot_cache_ttl_hours": _env_int("RAG_HOT_CACHE_TTL_HOURS", 6),
    "semantic_compression_target": _env_int("RAG_COMPRESSION_TARGET", 300),
    "max_records": _env_int("RAG_MAX_RECORDS", 500),
    "use_faiss": _env_bool("RAG_USE_FAISS", True),
    "vector_similarity_threshold": _env_float("RAG_SIM_THRESHOLD", 0.1),
    "chroma_collection_name": _env("RAG_CHROMA_COLLECTION", "homemind_kb"),
    "data_dir": _env("RAG_DATA_DIR", "data"),
}


# ─────────────────────────────────────────────
# Embedding 配置
# ─────────────────────────────────────────────

EMBEDDING_CONFIG = {
    "model_name": _env("HOMEMIND_EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
    "allow_network": _env_bool("HOMEMIND_EMBEDDING_ALLOW_NETWORK", False),
    "cache_dir": _env("HOMEMIND_EMBEDDING_CACHE_DIR", ""),
}


# ─────────────────────────────────────────────
# Voice / ASR 配置
# ─────────────────────────────────────────────

VOICE_CONFIG = {
    "vosk_zh_model": _env("HOMEMIND_VOSK_ZH_MODEL", "models/asr/vosk-model-small-cn-0.22"),
    "vosk_en_model": _env("HOMEMIND_VOSK_EN_MODEL", "models/asr/vosk-model-small-en-us-0.15"),
    "sample_rate": _env_int("HOMEMIND_VOSK_SAMPLE_RATE", 16000),
}


# ─────────────────────────────────────────────
# Hierarchical KV Store 配置
# ─────────────────────────────────────────────

KV_CONFIG = {
    "l1_max_bytes": _env_int("KV_L1_MAX_BYTES", 200 * 1024),
    "l2_max_bytes": _env_int("KV_L2_MAX_BYTES", 10 * 1024 * 1024),
    "l1_ttl_seconds": _env_float("KV_L1_TTL_SECONDS", 300.0),
    "l2_ttl_seconds": _env_float("KV_L2_TTL_SECONDS", 3600.0),
    "l2_path": _env("KV_L2_PATH", "data/kv_warm.db"),
    "cleanup_interval_seconds": _env_float("KV_CLEANUP_INTERVAL", 60.0),
}


# ─────────────────────────────────────────────
# Context Compressor 配置
# ─────────────────────────────────────────────

COMPRESSOR_CONFIG = {
    "compress_threshold_chars": _env_int("COMPRESS_THRESHOLD_CHARS", 500),
    "summarize_threshold_chars": _env_int("SUMMARIZE_THRESHOLD_CHARS", 2000),
}


# ─────────────────────────────────────────────
# LSR (Ranking) 配置
# ─────────────────────────────────────────────

LSR_CONFIG = {
    "weights": [
        _env_float("LSR_WEIGHT_F_SCORE", 0.30),
        _env_float("LSR_WEIGHT_F_TEMP", 0.10),
        _env_float("LSR_WEIGHT_F_HUMIDITY", 0.05),
        _env_float("LSR_WEIGHT_F_HOUR", 0.20),
        _env_float("LSR_WEIGHT_F_PREF", 0.35),
    ],
    "bias": _env_float("LSR_BIAS", 0.1),
    "explicit_scene_bonus": _env_float("LSR_EXPLICIT_SCENE_BONUS", 0.35),
    "explicit_device_bonus": _env_float("LSR_EXPLICIT_DEVICE_BONUS", 0.45),
    "explicit_device_penalty": _env_float("LSR_EXPLICIT_DEVICE_PENALTY", 0.35),
}


# ─────────────────────────────────────────────
# 决策置信度配置（mock 模式硬编码值 → 统一管理）
# ─────────────────────────────────────────────

DECISION_CONFIDENCE = {
    "soft_command": _env_float("CONFIDENCE_SOFT_COMMAND", 0.92),
    "chat_reply": _env_float("CONFIDENCE_CHAT_REPLY", 0.98),
    "ambiguous_clarify": _env_float("CONFIDENCE_AMBIGUOUS", 0.88),
    "automation_request": _env_float("CONFIDENCE_AUTOMATION", 0.96),
    "action_command": _env_float("CONFIDENCE_ACTION", 0.92),
    "action_command_weak": _env_float("CONFIDENCE_ACTION_WEAK", 0.82),
    "needs_clarification": _env_float("CONFIDENCE_NEEDS_CLARIFY", 0.55),
    "ac_temp_hot": _env_int("AC_TEMP_HOT", 26),
    "ac_temp_cold": _env_int("AC_TEMP_COLD", 28),
}


# ─────────────────────────────────────────────
# 安全 / 命令验证配置
# ─────────────────────────────────────────────

VALIDATOR_CONFIG = {
    "water_heater_risk_temp": _env_int("VALIDATOR_WATER_HEATER_RISK_TEMP", 60),
    "water_heater_default_temp": _env_int("VALIDATOR_WATER_HEATER_DEFAULT_TEMP", 45),
}


# ─────────────────────────────────────────────
# DQN 奖励配置
# ─────────────────────────────────────────────

DQN_REWARDS = {
    "accept": _env_float("DQN_REWARD_ACCEPT", 1.0),
    "ignore": _env_float("DQN_REWARD_IGNORE", 0.0),
    "reject": _env_float("DQN_REWARD_REJECT", -0.5),
    "correct": _env_float("DQN_REWARD_CORRECT", -1.0),
}


# ─────────────────────────────────────────────
# NL→TAP 规则配置
# ─────────────────────────────────────────────

NL_TAP_CONFIG = {
    "default_priority": _env_int("NL_TAP_DEFAULT_PRIORITY", 50),
    "default_time": _env("NL_TAP_DEFAULT_TIME", "08:00"),
}


# ─────────────────────────────────────────────
# Policy Engine 配置
# ─────────────────────────────────────────────

POLICY_CONFIG = {
    "policy_dir": _env("POLICY_DIR", "config/policies"),
}


# ─────────────────────────────────────────────
# Audit / 日志配置
# ─────────────────────────────────────────────

AUDIT_CONFIG = {
    "audit_db_path": _env("AUDIT_DB_PATH", ""),  # 空字符串 → 使用 STORAGE_CONFIG["data_dir"]
    "trace_retention_days": _env_int("TRACE_RETENTION_DAYS", 7),
}


# ─────────────────────────────────────────────
# 协议网关配置（统一管理，避免硬编码 IP）
# ─────────────────────────────────────────────

GATEWAY_CONFIG = {
    "matter": {
        "controller_ip": _env("MATTER_CONTROLLER_IP", "192.168.1.100"),
        "port": _env_int("MATTER_PORT", 5580),
    },
    "mqtt": {
        "broker": _env("MQTT_BROKER", "localhost"),
        "port": _env_int("MQTT_PORT", 1883),
        "default_port": _env_int("MQTT_DEFAULT_PORT", 1883),
        "tls_port": _env_int("MQTT_TLS_PORT", 8883),
    },
    "xiaomi": {
        "gateway_ip": _env("XIAOMI_GATEWAY_IP", "192.168.1.50"),
    },
    "home_assistant": {
        "url": _env("HA_URL", "http://localhost:8123"),
        "default_ip": "192.168.1.200",
        "default_port": 8123,
    },
}


# ─────────────────────────────────────────────
# Mesh 网络传输配置
# ─────────────────────────────────────────────

MESH_CONFIG = {
    "ws_server_port": _env_int("MESH_WS_PORT", 8765),
    "relay_db_path": _env("MESH_RELAY_DB_PATH", "data/mesh_relay.db"),
}


# ─────────────────────────────────────────────
# Distributed Discovery 配置
# ─────────────────────────────────────────────

DISCOVERY_CONFIG = {
    "service_port": _env_int("DISCOVERY_SERVICE_PORT", 8765),
    "service_type": _env("DISCOVERY_SERVICE_TYPE", "_homemind._tcp.local."),
}


# ─────────────────────────────────────────────
# 全局开关
# ─────────────────────────────────────────────


GLOBAL_CONFIG = {
    "confidence_threshold": _env_float("CONFIDENCE_THRESHOLD", 0.75),
    "mode": _env("HOMEMIND_MODE", "simulated"),
    "debug": _env_bool("HOMEMIND_DEBUG", False),
}
