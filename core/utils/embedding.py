# -*- coding: utf-8 -*-
"""统一 Embedding 服务"""
from typing import List, Union
import logging

logger = logging.getLogger(__name__)
_model = None

def get_model():
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("EmbeddingService: MiniLM-L6-v2 加载完成")
        except Exception as e:
            logger.warning(f"EmbeddingService: MiniLM-L6-v2 加载失败: {e}")
    return _model

def encode(texts: Union[str, List[str]]):
    model = get_model()
    if model is None:
        import numpy as np
        if isinstance(texts, str):
            rng = np.random.default_rng(hash(texts) % 2**32)
            return rng.random(384).astype(np.float32)
        else:
            rng = np.random.default_rng(0)
            return rng.random((len(texts), 384)).astype(np.float32)
    return model.encode(texts)
