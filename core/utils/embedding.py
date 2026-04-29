# -*- coding: utf-8 -*-
"""统一 Embedding 服务。

默认优先使用本地缓存，避免在冷启动时因联网探测 HuggingFace 而长时间阻塞。
只有显式设置 ``HOMEMIND_EMBEDDING_ALLOW_NETWORK=1`` 时，才允许回退到联网加载。
"""

from contextlib import contextmanager
import logging
import os
from typing import List, Union

logger = logging.getLogger(__name__)

DEFAULT_MODEL_NAME = "all-MiniLM-L6-v2"

_model = None
_init_attempted = False


@contextmanager
def _temporary_env(updates):
    original = {key: os.environ.get(key) for key in updates}
    try:
        for key, value in updates.items():
            os.environ[key] = value
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def reset_model_state():
    global _model, _init_attempted
    _model = None
    _init_attempted = False


def _load_sentence_transformer(model_name: str, local_only: bool):
    from sentence_transformers import SentenceTransformer

    cache_dir = os.environ.get("HOMEMIND_EMBEDDING_CACHE_DIR", "").strip()
    kwargs = {}
    if cache_dir:
        kwargs["cache_folder"] = cache_dir

    if local_only:
        with _temporary_env({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}):
            try:
                return SentenceTransformer(model_name, local_files_only=True, **kwargs)
            except TypeError:
                # 某些旧版本不接受 local_files_only，但离线环境变量仍会阻止联网探测。
                return SentenceTransformer(model_name, **kwargs)

    return SentenceTransformer(model_name, **kwargs)


def get_model():
    global _model, _init_attempted
    if _model is None and not _init_attempted:
        _init_attempted = True
        model_name = os.environ.get("HOMEMIND_EMBEDDING_MODEL", DEFAULT_MODEL_NAME).strip() or DEFAULT_MODEL_NAME
        allow_network = os.environ.get("HOMEMIND_EMBEDDING_ALLOW_NETWORK", "0").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        try:
            _model = _load_sentence_transformer(model_name, local_only=True)
            logger.info("EmbeddingService: %s 本地缓存加载完成", model_name)
        except Exception as local_error:
            logger.warning("EmbeddingService: %s 本地缓存加载失败: %s", model_name, local_error)
            if allow_network:
                try:
                    _model = _load_sentence_transformer(model_name, local_only=False)
                    logger.info("EmbeddingService: %s 联网加载完成", model_name)
                except Exception as online_error:
                    logger.warning("EmbeddingService: %s 联网加载失败: %s", model_name, online_error)
            else:
                logger.info("EmbeddingService: 已禁用联网回退，继续使用无模型降级路径")
    return _model


def encode(texts: Union[str, List[str]]):
    model = get_model()
    if model is None:
        import numpy as np

        if isinstance(texts, str):
            rng = np.random.default_rng(hash(texts) % 2**32)
            return rng.random(384).astype(np.float32)
        rng = np.random.default_rng(0)
        return rng.random((len(texts), 384)).astype(np.float32)
    return model.encode(texts)
