# Injection Detector 提示注入检测 — 面试拷打指南

## 模块定位

InjectionDetector 在用户输入进入 Agent 之前，检测常见的**提示注入攻击**模式。轻量级规则匹配，无需额外模型。

```
用户输入
      ↓
InjectionDetector.check(text)
      ↓
无注入 → 进入 BSR 召回
有注入 → 记录日志 + 返回检测结果
```

---

## 核心接口

### `InjectionDetector.check(text) -> InjectionCheckResult`

```python
@dataclass
class InjectionCheckResult:
    detected: bool
    pattern: Optional[str] = None
    severity: str = "low"    # low | medium | high
    message: str = ""
```

---

## 检测类别

### 高危：系统指令注入
```python
SYSTEM_INJECTION_PATTERNS = [
    r"^\s*#\s*(system|instruction|rule)",  # # system: ...
    r"^system\s*:\s*",                       # system: ...
    r"<\s*system\s*>",                        # <system>
    r"<!\[CDATA\[.*?(system|instruction).*?\]\]>",  # CDATA
]
```
**立即触发澄清路径。**

### 高危：忽略/覆盖指令
```python
IGNORE_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|above|prior|instructions)",
    r"disregard\s+(all\s+)?(previous|above|prior)",
    r"forget\s+(all\s+)?(previous|above|prior)",
    r"忘掉(你)?(的)?(角色|身份|设定)",  # 中文劫持
]
```

### 中危：角色劫持
```python
ROLE_HIJACK_PATTERNS = [
    r"现在\s*(你是?|扮演|做)\s*\w",     # 现在你是...
    r"你\s*是?\s*(一个|款|名)\s*\w",    # 你是一个...
    r"(act|pretend|roleplay)\s+as\s+\w", # act as
]
```

### 中危：编码绕过
```python
ENCODING_BYPASS_PATTERNS = [
    r"%[0-9a-fA-F]{2}%[0-9a-fA-F]{2}",  # URL 编码
    r"[A-Za-z0-9+/]{40,}={0,2}",           # 长 Base64 串
]
```

### 低危：超长重复字符
```python
LONG_REPEAT_PATTERN = r"(.)\1{20,}"  # 同一字符重复 20+ 次
```

---

## 面试核心问题

### 1. 为什么不用模型检测？
- 提示注入是近年新问题，模型检测有滞后性
- 规则匹配：零延迟、可解释、稳定
- 模型检测需要额外推理开销

### 2. 不同 severity 的处理策略？
```python
if severity == "high":
    return clarification_needed  # 强制澄清
if severity == "medium":
    return clarification_needed  # 强制澄清
if severity == "low":
    return warning              # 记录但放行
```
高危和中危都强制澄清，低危记录但不阻止。

### 3. 为什么中文也需要检测？
```python
r"忘掉(你)?(的)?(角色|身份|设定)"
```
- 攻击者知道系统是中文环境
- 中英文混合注入越来越常见
