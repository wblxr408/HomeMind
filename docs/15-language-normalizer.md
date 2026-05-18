# Language Normalizer 语言规范化 — 面试拷打指南

## 模块定位

LanguageNormalizer 将用户的自然语言（中文方言、英文口语、语音识别结果）规范化为 HomeMind 的标准中文命令短语。

```
用户输入: "turn on the air conditioner"
      ↓
LanguageNormalizer.normalize(text)
      ↓
NormalizedQuery(
    original="turn on the air conditioner",
    normalized="打开空调",
    language="en",
    confidence=0.95,
    matched_rule="en_open_ac",
)
```

---

## 核心接口

### `normalize(text, language="auto") -> NormalizedQuery`

```python
@dataclass
class NormalizedQuery:
    original: str
    normalized: str
    language: str          # en | zh | unknown
    confidence: float     # 0.0-1.0
    matched_rule: str     # 匹配的规则名
    extra_candidates: List[str]  # 额外候选动作
```

---

## 语言检测

```python
def _detect_language(text, language):
    if language in ("zh", "en"):  # 显式指定
        return language
    if re.search(r"[a-zA-Z]", text):  # 包含英文字母
        return "en"
    if re.search(r"[\u4e00-\u9fff]", text):  # 包含中文字符
        return "zh"
    return "unknown"
```

---

## 规则匹配

### 英文规则示例
```python
("en_open_ac", "en", r"(turn|switch|power)?on(the)?(airconditioner|ac|a/c)", "打开空调", 0.95, [])
("en_cooler", "en", r"(makeit)?(cooler|toohot|hot|cooldown)", "太热了", 0.84, ["打开空调", "打开风扇", "打开窗户"])
```

### 中文方言规则示例
```python
("zh_hot_dialect", "zh", r"(热煞|热死|热得很|热得慌|遭不住|太热|好热|凉快点)", "太热了", 0.88, ["打开空调", "打开风扇", "打开窗户"])
("zh_away_scene_colloquial", "zh", r"(出门|离家|不在家|要走了|我走了|走了|准备走|马上走)", "切换离家模式", 0.9, [])
```

---

## 置信度决策

```python
for rule in self._rules:
    if rule["pattern"].search(comparable):
        if rule["confidence"] >= 0.9:
            return immediately  # 高置信直接返回
        break  # 中置信继续检查更优规则

# 检查语音反馈历史
feedback_match = self._lookup_feedback(original)
if feedback_match:
    return confidence=0.98  # 用户纠正过，以纠正为准

# 返回低置信 passthrough
return NormalizedQuery(..., confidence=0.5, matched_rule="passthrough")
```

---

## 面试核心问题

### 1. 为什么要区分语言？
- 中英文用户表达习惯不同
- 规则按语言分类，避免跨语言误匹配
- 中文方言覆盖"热煞"/"遭不住"等口语

### 2. 为什么置信度 >= 0.9 才直接返回？
- 0.9 以上说明规则非常确定
- 避免低置信度规则错误覆盖高置信度规则
- 语音识别结果通常置信度偏低

### 3. 为什么要有 extra_candidates？
```python
("en_cooler", ..., "太热了", 0.84, ["打开空调", "打开风扇", "打开窗户"])
```
- 提供同一意图的多个候选
- 供 LSR 精排时选择最合适的
- 避免单一归一化丢失备选

### 4. passthrough 的意义？
```python
matched_rule="passthrough", confidence=0.5
```
- 无法匹配任何规则时，原文返回
- 进入后续 BSR 召回流程再做处理
- 避免在规范层丢失原始信息
