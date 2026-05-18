"""
上下文压缩 Pipeline — 与 HomeMind 决策层集成

将 ContextCompressor 嵌入 decide_local / decide_cloud 流程，
在构建 prompt 前自动压缩 RAG context。
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from core.context.compressor import (
    ContextBlock,
    ContextCompressor,
    make_block,
    estimate_tokens,
)

logger = logging.getLogger(__name__)


class ContextPipeline:
    """
    上下文压缩 Pipeline，串联 HomeMind 现有组件。

    典型调用链：
        query + session + rag_context + preference_context
            → parse blocks
            → compress(query, blocks, max_tokens)
            → 注入 LLM prompt

    用法（嵌入 LLMDecider）：
        pipeline = ContextPipeline(llm_decider)
        compressed = await pipeline.process(query, session_store, kb, preference_store)
        # 然后将 compressed 传给 decide_local / decide_cloud
    """

    def __init__(self, llm_decider: Optional[Any] = None, max_tokens: int = 2048):
        self._llm = llm_decider
        self._max_tokens = max_tokens
        self._compressor: Optional[ContextCompressor] = None

    def _get_compressor(self) -> ContextCompressor:
        if self._compressor is None:
            self._compressor = ContextCompressor(
                llm_fn=self._llm.decide if self._llm else None,
                max_tokens=self._max_tokens,
            )
        return self._compressor

    def _collect_blocks(
        self,
        session_store: Optional[Any],
        kb: Optional[Any],
        preference_store: Optional[Any],
    ) -> List[ContextBlock]:
        """从各个来源收集 context blocks。"""
        blocks: List[ContextBlock] = []

        # SessionStore — 最近对话历史
        if session_store:
            try:
                runtime = session_store.get_runtime_context()
                recent_turns = runtime.get("recent_turns", [])
                for turn in recent_turns:
                    blocks.append(make_block(
                        content=f"{turn.get('role', 'user')}: {turn.get('text', '')}",
                        source="session",
                        value_score=0.6,
                    ))
            except Exception as exc:
                logger.warning("Failed to collect session blocks: %s", exc)

        # PreferenceStore — 用户偏好
        if preference_store:
            try:
                summary = preference_store.get_cloud_preference_summary()
                if summary:
                    blocks.append(make_block(
                        content=str(summary),
                        source="preference",
                        value_score=0.8,
                    ))
            except Exception as exc:
                logger.warning("Failed to collect preference blocks: %s", exc)

        # RAG KB — 已有上下文（在调用方传入）
        # 此处只收集预置块

        return blocks

    async def process(
        self,
        query: str,
        session_store: Optional[Any] = None,
        kb_context: str = "",
        rag_blocks: Optional[List[ContextBlock]] = None,
        extra_context: Optional[List[ContextBlock]] = None,
    ) -> str:
        """
        处理上下文压缩。

        Args:
            query: 当前用户查询
            session_store: SessionStore 实例（用于提取最近对话）
            kb_context: 来自 KnowledgeBase 的原始上下文字符串
            rag_blocks: RAG 返回的结构化块（优先使用）
            extra_context: 额外的 context blocks

        Returns:
            压缩后的文本字符串，可直接注入 LLM prompt
        """
        blocks: List[ContextBlock] = []

        # 收集预置 blocks
        blocks.extend(self._collect_blocks(session_store, None, None))

        # 转换 KB context 为 blocks
        if kb_context:
            for line in kb_context.split("\n"):
                line = line.strip()
                if line:
                    blocks.append(make_block(content=line, source="rag", value_score=0.5))

        # RAG 结构化块
        if rag_blocks:
            blocks.extend(rag_blocks)

        # 额外块
        if extra_context:
            blocks.extend(extra_context)

        if not blocks:
            return ""

        # 压缩
        compressor = self._get_compressor()
        try:
            return await compressor.compress(query, blocks, max_tokens=self._max_tokens)
        except Exception as exc:
            logger.warning("Context compression failed: %s, returning raw context", exc)
            return kb_context

    def should_compress(self, context: str, threshold: int = 500) -> bool:
        """判断是否需要压缩。"""
        return len(context) >= threshold


# ── 与 LLMDecider 的集成钩子 ────────────────────────────────────────────────

async def inject_compression(
    query: str,
    rag_context: str,
    llm_decider: Any,
    max_tokens: int = 2048,
) -> str:
    """
    在 LLMDecider.decide_local / decide_cloud 内部调用的压缩函数。

    在 build prompt 前将 rag_context 压缩后再传入。
    """
    if not rag_context or len(rag_context) < 500:
        return rag_context

    blocks = []
    for line in rag_context.split("\n"):
        line = line.strip()
        if line:
            blocks.append(make_block(content=line, source="rag", value_score=0.5))

    compressor = ContextCompressor(
        llm_fn=getattr(llm_decider, "decide", None),
        max_tokens=max_tokens,
    )
    try:
        return await compressor.compress(query, blocks, max_tokens=max_tokens)
    except Exception:
        return rag_context
