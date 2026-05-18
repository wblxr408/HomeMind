# InferenceRouter 推理路由 — 面试拷打指南

## 模块定位

InferenceRouter 是 HomeMind 的**决策分发层**，负责根据意图类型、置信度和云端可用性，将请求路由到不同处理路径：本地执行 / 云端执行 / 澄清询问 / 自动化创建。

```
用户输入
  ↓
Router.classify_intent() — 判断意图类型
  ↓
Router.decide_route() — 选择执行路径
  ↓
本地执行  /  云端执行  /  澄清询问  /  自动化创建
```

---

## 核心接口

### `InferenceRouter.__init__()`
加载路由阈值配置：
```python
self.local_threshold = 0.70   # ≥0.70 → 本地执行
self.cloud_threshold = 0.40   # ≥0.40 → 云端执行
# 低于 0.40 → 澄清询问
```

### `classify_intent(query, normalized_query) -> Dict`
判断意图类型（**在进入精排前调用**）：
- `chat_reply` — 寒暄
- `action_command` — 可执行命令
- `automation_request` — 定时自动化
- `unsupported` — 不支持的设备

### `decide_route(query, ranked_candidates, ...) -> Dict`
选择执行路径（在精排后调用）。

---

## classify_intent 流程

```
输入文本
  ↓
1. 安全敏感检测 → clarification_needed
  ↓
2. 不支持设备检测 → unsupported
  ↓
3. 寒暄检测 → chat_reply
  ↓
4. 自动化请求检测 → automation_request
  ↓
5. 显式命令检测 → action_command (route=candidate)
  ↓
兜底 → action_command (route=candidate)
```

### 安全敏感检测
```python
def detect_safety_sensitive_request(query):
    # 门锁/安防/摄像头/燃气阀...
    SECURITY_SENSITIVE_TARGETS = [
        "门锁", "智能锁", "门禁", "安防", "摄像头", "监控", "燃气阀", ...
    ]
    if any(target in haystack for target in SECURITY_SENSITIVE_TARGETS):
        return clarification_needed
```
**所有门锁/安防相关 → 必须澄清，绝不自动执行。**

### 不支持设备检测
```python
UNSUPPORTED_TARGETS = {
    "冰箱": "",
    "投影仪": "",
    "闹钟": "如果你是想早上提醒，我可以先帮你切换到起床模式",
    ...
}
```
**闹钟有建议回复，引导到替代方案。**

### 显式命令检测
```python
EXPLICIT_PATTERNS = [
    r"^(打开|关闭|调高|调低|调亮|调暗|切换|查看|查询|设置)",
    r"(睡眠模式|待客模式|离家模式|...)",
    r"(空调|灯光|电视|风扇|窗户|音响|热水器)",
]
```
**匹配显式动词 + 设备/场景关键词 → route="candidate"（进入精排流程）**

---

## decide_route 流程

```
base_intent = classify_intent()
  ↓
base_intent.route in {reply, automation, unsupported, clarify}
  → 直接返回（不进入精排）

无候选列表 → clarification_needed

有候选：
  ↓
1. 高风险设备 → clarification_needed
  ↓
2. 显式命令 → local（直接本地执行）
  ↓
3. 综合分数 ≥ 0.70 → local
  ↓
4. 综合分数 ≥ 0.40 → cloud / fallback
  ↓
5. 综合分数 < 0.40 → clarification_needed
```

### 综合分数计算
```python
combined_score = bsr_score * 0.4 + lsr_score * 0.6
```

---

## 阈值配置

```python
ROUTING_THRESHOLDS = {
    "local": 0.70,
    "cloud": 0.40,
}
```
- **本地阈值 0.70**：高于此值信任本地规则决策
- **云端阈值 0.40**：高于此值可发送给云端 LLM
- **低于 0.40**：不信任任何决策，要求用户澄清

---

## 场景快捷映射

```python
SCENE_SHORTCUTS = {
    "早安": "早安模式",
    "晚安": "睡眠模式",
    "回家": "回家模式",
}
```
**"晚安" → 自动替换为"睡眠模式"**，避免遗漏。

---

## 面试核心问题清单

### 1. 为什么路由要先于精排判断？
- 寒暄/自动化/不支持设备不需要精排
- 减少不必要的计算开销
- 安全敏感设备必须澄清

### 2. local_threshold=0.70 是怎么确定的？
- 经验值，在精确率和召回率之间平衡
- 0.70 意味着精排分数较高时才信任本地决策
- 可通过 A/B 测试调优

### 3. 为什么 BSR 和 LSR 的分数权重是 0.4 和 0.6？
- LSR（精排）包含更多上下文特征（温度/时间/偏好）
- 因此权重略高于 BSR（粗召回）
- 0.4:0.6 保持两者都参与决策

### 4. 显式命令为什么直接路由到 local？
- 关键词明确，无需云端辅助
- 减少延迟，提升响应速度
- 显式命令的错误成本低

### 5. 为什么高风险设备要强制澄清？
```python
HIGH_RISK_DEVICES = {"热水器", "窗户"}
```
- 热水器：高温有烫伤风险
- 窗户：安全风险
- 即使置信度很高，也需要用户确认

### 6. unsupported 和 clarify 的区别？
- `unsupported`：明确知道设备不支持，返回引导建议
- `clarify`：无法判断意图，需要用户澄清

### 7. 为什么不支持设备要进入 classify 而非 decide_route？
- 在精排之前拦截，避免浪费计算
- 返回替代建议（如闹钟→起床模式）
- 用户体验更好

### 8. 路由决策的容错机制？
- 所有分支都有兜底：clarification_needed
- 即使分类错误，最终都是澄清而非误执行
