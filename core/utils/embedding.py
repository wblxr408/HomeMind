# -*- coding: utf-8 -*-
"""统一 Embedding 服务。"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import List, Union

logger = logging.getLogger(__name__)

MODEL_NAME = "all-MiniLM-L6-v2"
EMBED_DIM = 384
_model = None
_init_attempted = False


def _embedding_mode() -> str:
    value = str(os.environ.get("HOMEMIND_EMBEDDING_MODE", "local")).strip().lower()
    if value in {"disabled", "off", "none", "false", "0"}:
        return "disabled"
    if value in {"download", "online", "remote"}:
        return "download"
    return "local"


def get_model():
    global _model, _init_attempted
    if _model is not None or _init_attempted:
        return _model

    _init_attempted = True
    mode = _embedding_mode()
    if mode == "disabled":
        logger.warning("EmbeddingService: embeddings disabled by HOMEMIND_EMBEDDING_MODE")
        return None

    try:
        from sentence_transformers import SentenceTransformer

        kwargs = {}
        if mode == "local":
            kwargs["local_files_only"] = True
        _model = SentenceTransformer(MODEL_NAME, **kwargs)
        logger.info("EmbeddingService: %s loaded in %s mode", MODEL_NAME, mode)
    except Exception as exc:
        logger.warning(
            "EmbeddingService: model init failed in %s mode, fallback to deterministic vectors: %s",
            mode,
            exc,
        )
        _model = None
    return _model


def _fallback_vector(text: str):
    import numpy as np

    digest = hashlib.sha256(str(text).encode("utf-8")).digest()
    seed = int.from_bytes(digest[:8], "big", signed=False)
    rng = np.random.default_rng(seed)
    return rng.random(EMBED_DIM).astype(np.float32)


def encode(texts: Union[str, List[str]]):
    model = get_model()
    if model is None:
        import numpy as np

        if isinstance(texts, str):
            return _fallback_vector(texts)
        return np.vstack([_fallback_vector(text) for text in texts]).astype(np.float32)
    return model.encode(texts)
