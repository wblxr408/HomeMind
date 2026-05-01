"""语义压缩器。

将 RAG 检索结果压缩到目标 token 数以内，保留核心实体和关系。
使用关键词提取 + 规则压缩，不引入额外的大模型依赖。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from core.config import RAG_CONFIG

logger = logging.getLogger(__name__)


@dataclass
class CompressedChunk:
    """压缩后的知识块。"""

    content: str
    original_length: int
    compressed_length: int
    compression_ratio: float
    source: str
    confidence: str  # high | medium | low
    entities: List[str]


class SemanticCompressor:
    """轻量级语义压缩器。

    策略：
    1. 实体提取：识别设备名、时间、操作词
    2. 关键词保留：保留高信息量词汇
    3. 冗余删除：删除重复描述、填充词
    4. 摘要生成：对超长文本做结构化摘要
    """

    # 填充词模式
    FILLER_PATTERNS = [
        (re.compile(r"一般来说[，,]?"), ""),
        (re.compile(r"通常情况下[，,]?"), ""),
        (re.compile(r"根据经验[，,]?"), ""),
        (re.compile(r"建议你[，,]?"), ""),
        (re.compile(r"建议您[，,]?"), ""),
        (re.compile(r"请注意[，,]?"), ""),
        (re.compile(r"请确保[，,]?"), ""),
        (re.compile(r"[，,]\s*然后[，,]?"), "，"),
        (re.compile(r"[，,]\s*接下来[，,]?"), "，"),
        (re.compile(r"[，,]\s*同时[，,]?"), "，"),
        (re.compile(r"\s+"), " "),
    ]

    # 核心实体关键词（优先保留）
    CORE_KEYWORDS = {
        "设备": ["空调", "灯光", "电视", "热水器", "风扇", "音响", "窗户", "窗帘", "门锁"],
        "场景": ["睡眠", "离家", "回家", "观影", "工作", "早安", "晚归", "待客"],
        "操作": ["打开", "关闭", "调高", "调低", "调亮", "调暗", "切换", "设置", "查询"],
        "参数": ["温度", "湿度", "亮度", "音量", "风速", "模式", "定时"],
        "时间": ["早上", "上午", "中午", "下午", "晚上", "白天", "夜间", "睡眠时间"],
        "条件": ["如果", "当", "每当", "当...时", "在...情况下"],
    }

    def __init__(self, target_tokens: int = None, source_priority: dict = None):
        self._target_tokens = (target_tokens or RAG_CONFIG["semantic_compression_target"]) * 4  # ~4 chars/token
        self._source_priority = source_priority or {
            "设备说明书": "high",
            "历史案例": "medium",
            "用户习惯": "medium",
            "场景规则": "low",
            "用户反馈": "low",
            "健康建议": "low",
        }

    def compress(self, chunks: List[dict], max_total_chars: int = None) -> List[CompressedChunk]:
        """将多个知识块压缩到目标长度。"""
        if not chunks:
            return []

        limit = max_total_chars or self._target_tokens * len(chunks)
        result: List[CompressedChunk] = []
        current_length = 0

        # 按来源优先级排序
        sorted_chunks = sorted(
            chunks,
            key=lambda c: self._source_priority.get(c.get("category", ""), "low"),
            reverse=True,
        )

        for chunk in sorted_chunks:
            content = str(chunk.get("content", ""))
            if not content:
                continue

            compressed = self._compress_single(content, chunk)
            compressed_len = len(compressed.content)

            if current_length + compressed_len > limit:
                if not result:
                    result.append(self._force_truncate(compressed, limit))
                    current_length = len(result[-1].content)
                break

            result.append(compressed)
            current_length += compressed_len

        logger.debug("SemanticCompressor: %d chunks -> %d, saved %.1f%% chars",
                    len(chunks), current_length,
                    (1 - current_length / max(1, sum(len(c.get("content", "")) for c in chunks)) * 100))
        return result

    def _compress_single(self, content: str, chunk: dict) -> CompressedChunk:
        """压缩单个知识块。"""
        original_length = len(content)

        # 步骤1: 删除填充词
        for pattern, replacement in self.FILLER_PATTERNS:
            content = pattern.sub(replacement, content)

        # 步骤2: 实体提取
        entities = self._extract_entities(content)

        # 步骤3: 截断超长内容
        if len(content) > self._target_tokens:
            content = content[: self._target_tokens - 20].rstrip("，,.") + "..."

        compressed_length = len(content)
        ratio = 1 - compressed_length / max(1, original_length)
        category = str(chunk.get("category", ""))
        confidence = self._source_priority.get(category, "low")

        return CompressedChunk(
            content=content.strip(),
            original_length=original_length,
            compressed_length=compressed_length,
            compression_ratio=round(ratio, 2),
            source=category,
            confidence=confidence,
            entities=entities,
        )

    def _force_truncate(self, chunk: CompressedChunk, limit: int) -> CompressedChunk:
        """强制截断到限长。"""
        content = chunk.content[:limit].rstrip("，,.")
        return CompressedChunk(
            content=content,
            original_length=chunk.original_length,
            compressed_length=len(content),
            compression_ratio=1 - len(content) / max(1, chunk.original_length),
            source=chunk.source,
            confidence=chunk.confidence,
            entities=chunk.entities,
        )

    def _extract_entities(self, text: str) -> List[str]:
        """提取核心实体（设备/场景/操作）。"""
        entities = []
        text_lower = text
        for category, keywords in self.CORE_KEYWORDS.items():
            for kw in keywords:
                if kw in text_lower and kw not in entities:
                    entities.append(kw)
        return entities

    def to_context_string(self, chunks: List[CompressedChunk]) -> str:
        """将压缩后的块转换为 LLM 上下文字符串。"""
        if not chunks:
            return ""

        parts = []
        for i, chunk in enumerate(chunks):
            prefix = f"[{chunk.source}|{chunk.confidence}]"
            parts.append(f"{prefix} {chunk.content}")

        return "\n".join(parts)
