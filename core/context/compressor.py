"""
上下文压缩引擎 — Query-Conditioned 自适应压缩

基于 SeleCom 论文设计：
- 不做固定比率截断，而是基于 query 对每个 context block 语义评分
- 优先保留与 query 高度相关的内容
- 超出 token 预算的部分用 LLM 摘要

压缩层级：
  Level 0 (PASS): context < 500 chars → 直接透传
  Level 1 (SELECT): 500-2000 chars → Query-Conditioned 软选择
  Level 2 (COMPRESS): 2000-4000 chars → 选择 + Token Budget 分配
  Level 3 (SUMMARIZE): > 4000 chars → 选择 + 摘要替换
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from core.config import COMPRESSOR_CONFIG

logger = logging.getLogger(__name__)

# ── 工具函数 ────────────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """
    估算 token 数量（中文约 1 token ≈ 1.5 字符，英文约 4 字符/token）。
    使用 tiktoken（如果可用），否则用经验公式估算。
    """
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        # 经验估算：中文按 1.5，英文按 4
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        other_chars = len(text) - chinese_chars
        return int(chinese_chars / 1.5 + other_chars / 4.0)


def cosine_similarity(a: List[float], b: List[float]) -> float:
    import numpy as np
    a = np.array(a, dtype=float)
    b = np.array(b, dtype=float)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


# ── Context Block ────────────────────────────────────────────────────────────

@dataclass
class ContextBlock:
    """单个上下文块。"""
    content: str
    source: str = ""        # "session" / "rag" / "preference" / "history"
    block_id: str = ""      # 唯一标识符
    value_score: float = 0.5  # 0.0-1.0，知识价值分
    token_count: int = 0
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.token_count == 0:
            self.token_count = estimate_tokens(self.content)
        if not self.block_id:
            import uuid
            self.block_id = self.block_id or str(uuid.uuid4())[:8]

    def to_text(self) -> str:
        if self.source:
            return f"[{self.source}] {self.content}"
        return self.content


# ── Token Budget ─────────────────────────────────────────────────────────────

@dataclass
class BudgetResult:
    """Token Budget 分配结果。"""
    kept: List[ContextBlock] = field(default_factory=list)
    discarded: List[ContextBlock] = field(default_factory=list)
    summary: Optional[str] = None
    total_tokens: int = 0
    kept_tokens: int = 0

    def exceeds_limit(self) -> bool:
        return self.total_tokens > 0 and self.kept_tokens > self.total_tokens


class TokenBudget:
    """固定 token 预算分配器。"""

    def __init__(self, max_tokens: int = 4096):
        self.max_tokens = max_tokens

    def fit(self, blocks: List[ContextBlock]) -> BudgetResult:
        """
        将 blocks 按 token 预算分配，返回保留和丢弃的块列表。
        """
        result = BudgetResult(total_tokens=self.max_tokens)
        used_tokens = 0

        for block in blocks:
            if used_tokens + block.token_count <= self.max_tokens:
                result.kept.append(block)
                used_tokens += block.token_count
            else:
                result.discarded.append(block)

        result.kept_tokens = used_tokens
        return result


# ── Soft Selector ────────────────────────────────────────────────────────────

class SoftSelector:
    """
    查询条件软选择器（基于 SeleCom 论文）。

    对每个 context block 评分：
      score = cosine_sim(query_emb, block_emb) * 0.7 + value_score * 0.3

    返回按分数降序排列的块列表。
    """

    def __init__(self, embedding_fn: Optional[Callable[[str], List[float]]] = None):
        self._embedding_fn = embedding_fn

    def _encode(self, text: str) -> List[float]:
        """获取文本 embedding。"""
        if self._embedding_fn:
            return self._embedding_fn(text)
        # 回退：使用 sentence-transformers
        try:
            from core.utils.embedding import encode
            emb = encode(text)
            if hasattr(emb, 'tolist'):
                return emb.tolist()
            return list(emb) if isinstance(emb, (list, tuple)) else [0.0] * 384
        except Exception:
            return [0.0] * 384

    def score(self, query: str, blocks: List[ContextBlock]) -> List[tuple[float, ContextBlock]]:
        """
        对所有 block 评分并排序。
        返回 [(score, block), ...]，按 score 降序。
        """
        query_emb = self._encode(query)
        scored = []

        for block in blocks:
            block_emb = self._encode(block.content)
            sim = cosine_similarity(query_emb, block_emb)
            # SeleCom 权重：语义相似度 70% + 知识价值 30%
            score = sim * 0.7 + block.value_score * 0.3
            scored.append((score, block))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored

    def select(self, query: str, blocks: List[ContextBlock]) -> List[ContextBlock]:
        """返回排序后的 block 列表（不丢弃任何块，只重排）。"""
        return [block for _, block in self.score(query, blocks)]


# ── 摘要生成器 ─────────────────────────────────────────────────────────────

class Summarizer:
    """
    LLM-based 摘要生成器。
    将多个丢弃的 block 压缩为一条摘要。
    """

    def __init__(self, llm_fn: Optional[Callable] = None):
        self._llm_fn = llm_fn  # 可注入 LLMDecider

    async def summarize(self, blocks: List[ContextBlock], query: str = "") -> str:
        """
        将多个 context block 合并为一条摘要。
        如果没有 LLM，回退为抽取式摘要（取第一条）。
        """
        if not blocks:
            return ""

        if self._llm_fn is None:
            return self._extract_summary(blocks, query)

        try:
            prompt = self._build_summary_prompt(blocks, query)
            result = await self._llm_fn(prompt)
            return str(result) if result else self._extract_summary(blocks, query)
        except Exception as exc:
            logger.warning("LLM summarization failed: %s, using extractive fallback", exc)
            return self._extract_summary(blocks, query)

    def _build_summary_prompt(self, blocks: List[ContextBlock], query: str) -> str:
        contents = "\n".join(f"- {b.content}" for b in blocks)
        return (
            f"用户当前问题：{query}\n\n"
            f"以下是相关上下文，请压缩为一段简洁的摘要（不超过100字）：\n{contents}\n\n"
            f"摘要："
        )

    def _extract_summary(self, blocks: List[ContextBlock], query: str) -> str:
        """抽取式摘要：取最高分 block 的前 100 字。"""
        if not blocks:
            return ""
        top = blocks[0]
        content = top.content
        return content[:100] + "..." if len(content) > 100 else content


# ── 主压缩引擎 ───────────────────────────────────────────────────────────────

class ContextCompressor:
    """
    Query-Conditioned 自适应上下文压缩引擎。

    压缩层级由 context 长度和 query 决定：
      PASS     (< 500 chars)     → 直接透传
      SELECT   (500-2000 chars)  → 语义重排
      COMPRESS (2000-4000 chars) → 重排 + 预算分配
      SUMMARIZE (> 4000 chars)  → 重排 + 摘要

    用法：
        compressor = ContextCompressor()
        result = await compressor.compress(
            query="打开空调",
            context_blocks=[block1, block2],
            max_tokens=2048,
        )
        # result 是压缩后的文本字符串
    """

    def __init__(
        self,
        embedding_fn: Optional[Callable[[str], List[float]]] = None,
        llm_fn: Optional[Callable] = None,
        max_tokens: int = 2048,
    ):
        self._embedding_fn = embedding_fn
        self._llm_fn = llm_fn
        self._max_tokens = max_tokens
        self._selector = SoftSelector(embedding_fn=embedding_fn)
        self._budget = TokenBudget(max_tokens=max_tokens)
        self._summarizer = Summarizer(llm_fn=llm_fn)
        cfg = COMPRESSOR_CONFIG
        self.COMPRESS_THRESHOLD = cfg.get("compress_threshold_chars", 500)
        self.SUMMARIZE_THRESHOLD = cfg.get("summarize_threshold_chars", 2000)

    async def compress(
        self,
        query: str,
        context_blocks: List[ContextBlock],
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        主压缩入口。

        Args:
            query: 当前用户查询
            context_blocks: 上下文块列表
            max_tokens: 最大 token 预算（覆盖默认值）

        Returns:
            压缩后的文本字符串
        """
        if not context_blocks:
            return ""

        if max_tokens is not None:
            self._budget = TokenBudget(max_tokens=max_tokens)

        # 预处理：计算 token 数
        for block in context_blocks:
            if block.token_count == 0:
                block.token_count = estimate_tokens(block.content)

        # 总 context 长度
        total_chars = sum(len(b.content) for b in context_blocks)

        # Level 0: 直接透传
        if total_chars < self.COMPRESS_THRESHOLD:
            return self._render_blocks(context_blocks)

        # Level 1-3: Query-Conditioned 选择
        ranked = self._selector.select(query, context_blocks)

        # Level 1: 只重排，不截断
        if total_chars < self.SUMMARIZE_THRESHOLD:
            return self._render_blocks(ranked)

        # Level 2: 预算分配
        budget_result = self._budget.fit(ranked)

        # Level 3: 超预算部分摘要
        if budget_result.exceeds_limit() and budget_result.discarded:
            summary = await self._summarizer.summarize(
                budget_result.discarded, query
            )
            if summary:
                budget_result.summary = summary
                budget_result.kept.append(
                    ContextBlock(content=summary, source="summary", value_score=0.3)
                )

        return self._render_budget_result(budget_result)

    def _render_blocks(self, blocks: List[ContextBlock]) -> str:
        """将 block 列表渲染为文本。"""
        return "\n".join(b.to_text() for b in blocks)

    def _render_budget_result(self, result: BudgetResult) -> str:
        """将 BudgetResult 渲染为文本。"""
        parts = [b.to_text() for b in result.kept]
        if result.summary:
            parts.append(f"[摘要] {result.summary}")
        return "\n".join(parts)


# ── 便捷工厂函数 ────────────────────────────────────────────────────────────

def make_block(content: str, source: str = "", value_score: float = 0.5, **metadata) -> ContextBlock:
    """从文本创建 ContextBlock。"""
    return ContextBlock(content=content, source=source, value_score=value_score, metadata=metadata)


def compress_text(
    query: str,
    texts: List[str],
    sources: Optional[List[str]] = None,
    max_tokens: int = 2048,
) -> str:
    """
    便捷函数：从文本列表直接压缩。
    同步版本，用于非 async 场景。
    """
    blocks = [
        make_block(t, source=(sources[i] if sources and i < len(sources) else ""))
        for i, t in enumerate(texts)
    ]
    compressor = ContextCompressor(max_tokens=max_tokens)

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, compressor.compress(query, blocks))
                return future.result()
        else:
            return asyncio.run(compressor.compress(query, blocks))
    except Exception as exc:
        logger.warning("Sync compress_text failed: %s", exc)
        return "\n".join(texts)
