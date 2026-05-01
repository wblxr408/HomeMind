"""提示注入检测器。

在输入进入 Agent 之前检测常见的提示注入模式。
使用规则匹配而非额外模型，轻量且零依赖。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class InjectionCheckResult:
    """检测结果。"""

    detected: bool
    pattern: Optional[str] = None
    severity: str = "low"  # low | medium | high
    message: str = ""


class InjectionDetector:
    """检测用户输入中的提示注入攻击模式。

    检测范围：
    - 角色扮演指令（"你是一个AI助手，现在扮演..."）
    - 忽略指令（"ignore previous", "disregard above"）
    - 注入前缀（超长特殊字符重复）
    - 编码绕过（URL编码、Base64等）
    - 系统指令注入（"# System:", "SYSTEM:" 等）
    """

    # 忽略指令模式
    IGNORE_PATTERNS = [
        r"ignore\s+(all\s+)?(previous|above|prior|instructions)",
        r"disregard\s+(all\s+)?(previous|above|prior)",
        r"forget\s+(all\s+)?(previous|above|prior)",
        r"discard\s+(all\s+)?(previous|above|prior|instructions)",
        r"新身份|s扮演|s现在是|你是一个|忘掉(你)?(的)?(角色|身份|设定)",
        r"override\s+(the\s+)?(above|previous|prior)\s+(instruction|system|rule)",
    ]

    # 角色扮演 / 身份劫持
    ROLE_HIJACK_PATTERNS = [
        r"现在\s*(你是?|扮演|做)\s*\w",
        r"你\s*是?\s*(一个|款|名)\s*\w",
        r"(act|pretend|roleplay|role-play)\s+as\s+\w",
        r"you\s+are\s+(now\s+)?(a|an|just)\s+\w",
        r"(forget|ignore)\s+your\s+(system|original|previous|actual)\s+\w+",
        r"(new|alternate|malicious)\s+(system|developer|instruction)",
    ]

    # 系统指令注入
    SYSTEM_INJECTION_PATTERNS = [
        r"^\s*#\s*(system|instruction|rule)",
        r"^system\s*:\s*",
        r"<\s*system\s*>",
        r"<!\[CDATA\[.*?(system|instruction).*?\]\]>",
    ]

    # 超长重复字符（可能是混淆注入）
    LONG_REPEAT_PATTERN = r"(.)\1{20,}"

    # Base64 / URL 编码绕过尝试
    ENCODING_BYPASS_PATTERNS = [
        r"%[0-9a-fA-F]{2}%[0-9a-fA-F]{2}",
        r"[A-Za-z0-9+/]{40,}={0,2}",  # 疑似 Base64 长串
    ]

    # 中间插入指令（在正常文本中间隐藏指令）
    MIDDLE_INJECTION_PATTERNS = [
        r"\.{3,}.{0,5}(system|instruction|rule|command|do\s+this|execute)",
        r"\n{3,}.{0,5}(ignore|disregard|forget|override)",
    ]

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._compile_patterns()

    def _compile_patterns(self):
        self._ignore_re = [
            re.compile(p, re.IGNORECASE | re.MULTILINE)
            for p in self.IGNORE_PATTERNS
        ]
        self._role_re = [
            re.compile(p, re.IGNORECASE | re.MULTILINE)
            for p in self.ROLE_HIJACK_PATTERNS
        ]
        self._system_re = [
            re.compile(p, re.IGNORECASE | re.MULTILINE)
            for p in self.SYSTEM_INJECTION_PATTERNS
        ]
        self._repeat_re = re.compile(self.LONG_REPEAT_PATTERN)
        self._encoding_re = [
            re.compile(p, re.IGNORECASE)
            for p in self.ENCODING_BYPASS_PATTERNS
        ]
        self._middle_re = [
            re.compile(p, re.IGNORECASE | re.MULTILINE)
            for p in self.MIDDLE_INJECTION_PATTERNS
        ]

    def check(self, text: str) -> InjectionCheckResult:
        """检测输入是否包含提示注入。"""
        if not self.enabled:
            return InjectionCheckResult(detected=False)

        text = str(text or "").strip()
        if not text:
            return InjectionCheckResult(detected=False)

        # 高危：系统指令注入
        for pattern, regex in [(p, r) for p, r in zip(self.SYSTEM_INJECTION_PATTERNS, self._system_re)]:
            if regex.search(text):
                logger.warning("InjectionDetector: 检测到系统指令注入 pattern=%s", pattern)
                return InjectionCheckResult(
                    detected=True,
                    pattern=pattern,
                    severity="high",
                    message="检测到可疑的系统指令注入，已强制澄清路径",
                )

        # 高危：忽略/覆盖指令
        for pattern, regex in [(p, r) for p, r in zip(self.IGNORE_PATTERNS, self._ignore_re)]:
            if regex.search(text):
                logger.warning("InjectionDetector: 检测到忽略指令 pattern=%s", pattern)
                return InjectionCheckResult(
                    detected=True,
                    pattern=pattern,
                    severity="high",
                    message="检测到指令覆盖尝试，已强制澄清路径",
                )

        # 中危：角色劫持
        for pattern, regex in [(p, r) for p, r in zip(self.ROLE_HIJACK_PATTERNS, self._role_re)]:
            if regex.search(text):
                logger.warning("InjectionDetector: 检测到角色劫持 pattern=%s", pattern)
                return InjectionCheckResult(
                    detected=True,
                    pattern=pattern,
                    severity="medium",
                    message="检测到身份变更尝试，请正常描述需求",
                )

        # 中危：编码绕过
        for pattern, regex in zip(self.ENCODING_BYPASS_PATTERNS, self._encoding_re):
            if regex.search(text):
                logger.warning("InjectionDetector: 检测到编码绕过 pattern=%s", pattern)
                return InjectionCheckResult(
                    detected=True,
                    pattern=pattern,
                    severity="medium",
                    message="检测到可疑编码内容，已强制澄清",
                )

        # 低危：超长重复字符
        if self._repeat_re.search(text):
            logger.warning("InjectionDetector: 检测到超长重复字符")
            return InjectionCheckResult(
                detected=True,
                pattern=self.LONG_REPEAT_PATTERN,
                severity="low",
                message="输入包含异常字符，请重新描述需求",
            )

        return InjectionCheckResult(detected=False)

    def check_and_log(self, text: str) -> InjectionCheckResult:
        """检测并记录日志。"""
        result = self.check(text)
        if result.detected:
            logger.info("InjectionDetector: 检测结果 severity=%s pattern=%s",
                       result.severity, result.pattern)
        return result
