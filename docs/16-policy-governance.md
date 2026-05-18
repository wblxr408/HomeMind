# Policy Engine & Governance 治理与策略引擎 — 面试拷打指南

## 模块定位

PolicyEngine 和 Governance 模块提供组织级的**策略评估能力**，决定每个命令是否被允许执行。配合 RuntimeSecurityChain 实现多层安全防御。

---

## PolicyEngine

### `evaluate(policy_context) -> Dict`

接收命令上下文，返回授权效果：
```python
policy_context = {
    "device": "...",
    "scene": "...",
    "action": "...",
    "risk_level": "low/medium/high",
    "rate_limited": bool,
    "hour": int,
    "user_id": str,
    "route": "local/cloud/...",
}

policy_result = {
    "effect": "allow" | "confirm" | "deny",
    "reason": str,
    "policy": str,
}
```

---

## 治理策略设计原则

### 1. 基于时间的策略
- 深夜（22:00-07:00）限制高风险设备
- 工作时间允许更多操作

### 2. 基于路由的策略
- 本地路由：低延迟，信任度高
- 云端路由：复杂决策，可能需要确认

### 3. 基于用户身份的策略
- 家庭成员：全功能
- 访客：限制高风险设备
- 儿童：限制部分设备

---

## 面试核心问题

### 1. PolicyEngine 和 RuntimeSecurityChain 的区别？
- **PolicyEngine**：组织级策略，集中式规则
- **RuntimeSecurityChain**：运行时链，叠加多层检查
- PolicyEngine 在 RuntimeSecurityChain 内部被调用

### 2. 为什么需要治理层？
- 超越单一命令的安全检查
- 支持组织级访问控制
- 审计和合规需求

### 3. confirm 和 deny 的区别？
- **deny**：直接拒绝，不执行
- **confirm**：需要用户确认后执行
- deny 用于明确违规，confirm 用于需要二次确认的场景
