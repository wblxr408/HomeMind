# HomeMind Context Persistence and Privacy Plan

Updated: 2026-04-24

## 1. Goal

This document refines the `UPGRADE_PLAN.md` items for:

1. Context persistence
2. Privacy and security

The target is to upgrade HomeMind from fragmented persistence into a structured memory system, while extending security from encrypted storage to privacy redaction and minimal cloud upload.

## 2. Current State

Already available in the repo:

- `core/rag/knowledge_base.py`
  - local knowledge storage and restore
  - RAG-style context retrieval
- `core/dqn/policy.py`
  - DQN policy save/load
- `core/voice/feedback_store.py`
  - voice feedback JSONL persistence
- `core/security.py`
  - encrypted pickle storage

Current gaps:

- no dedicated `session_store.py`
- no dedicated `preference_store.py`
- no structured session persistence
- no structured long-term preference persistence
- no dedicated privacy redactor
- no minimal cloud payload builder

## 3. Architecture

Keep the existing modules, but split memory and privacy into clearer layers.

```text
Short-term state
-> SessionStore

Structured long-term preference
-> PreferenceStore

Retrievable text memory / evidence
-> KnowledgeBase

Specialized correction history
-> VoiceFeedbackStore

Encrypted local storage support
-> core/security.py

Privacy redaction and cloud-safe context building
-> PrivacyRedactor
```

## 4. Memory Layer Design

### 4.1 SessionStore

File:

- `core/memory/session_store.py`

Purpose:

- persist short-term runtime state
- restore latest context after restart
- support recent interaction continuity

Recommended persisted file:

- `data/session_state.json`

Recommended fields:

```json
{
  "user_id": "default",
  "current_scene": "睡眠模式",
  "last_user_input": "有点闷",
  "last_normalized_input": "有点闷",
  "last_action": {
    "type": "设备控制",
    "device": "空调",
    "device_action": "on",
    "params": {
      "temperature": 26
    }
  },
  "last_clarification": {
    "question": "请问您想调节哪个设备？",
    "answer": "空调"
  },
  "last_route": "cloud",
  "last_updated_at": "2026-04-24T10:20:00+08:00",
  "recent_turns": []
}
```

Core responsibilities:

- save current scene
- save latest input and normalized input
- save latest executed action
- save latest clarification exchange
- save whether the request used local or cloud path
- keep a bounded recent turn list

Suggested API:

```python
class SessionStore:
    def load(self) -> dict: ...
    def save(self) -> bool: ...
    def update_from_query(self, raw_text: str, normalized_text: str = "") -> None: ...
    def update_from_decision(self, decision: dict, route: str = "local") -> None: ...
    def update_clarification(self, question: str, answer: str = "") -> None: ...
    def update_scene(self, scene: str) -> None: ...
    def get_runtime_context(self) -> dict: ...
```

### 4.2 PreferenceStore

File:

- `core/memory/preference_store.py`

Purpose:

- persist stable user habits in structured form
- support ranking boost and cloud preference summary
- avoid mixing long-term preference with raw logs

Recommended persisted file:

- `data/preferences.json`

Recommended fields:

```json
{
  "user_id": "default",
  "devices": {
    "空调": {
      "preferred_temperature": 26,
      "cooling_mode_preferred": true
    },
    "灯光": {
      "sleep_brightness": 30
    }
  },
  "scenes": {
    "睡眠模式": {
      "preferred_hour": 22,
      "accept_count": 8
    }
  },
  "recommendation": {
    "sleep_mode_accept_rate": 0.82
  },
  "language": {
    "dialect_terms": {
      "灯搞亮点": "调亮灯光"
    }
  },
  "updated_at": "2026-04-24T10:20:00+08:00"
}
```

Core responsibilities:

- store stable AC temperature preference
- store stable light brightness preference
- store scene preference and time tendency
- store acceptance tendencies for recommendations
- store language normalization preference when useful

Suggested API:

```python
class PreferenceStore:
    def load(self) -> dict: ...
    def save(self) -> bool: ...
    def record_action_accept(self, decision: dict, context=None) -> None: ...
    def record_feedback(self, raw_text: str, normalized_text: str, feedback: str) -> None: ...
    def get_preference_boost(self, candidate_action: str, context=None) -> float: ...
    def get_cloud_preference_summary(self) -> dict: ...
```

### 4.3 Knowledge Boundary

The three memory layers must not be mixed:

- `SessionStore`
  - recent, short-term, current runtime state
- `PreferenceStore`
  - stable, structured, long-term habit
- `KnowledgeBase`
  - retrievable text evidence and semantic memory

Recommended rule:

- recent state -> `SessionStore`
- stable habit -> `PreferenceStore`
- textual explanation / evidence -> `KnowledgeBase`

## 5. Privacy Layer Design

### 5.1 PrivacyRedactor

File:

- `core/privacy/redactor.py`

Purpose:

- build cloud-safe minimal context
- prevent raw sensitive data from leaving the device
- enforce whitelist-based upload fields

Design rule:

- use whitelist upload, not blacklist deletion

### 5.2 Minimal Cloud Payload

Only upload what cloud-side disambiguation truly needs.

Recommended payload:

```json
{
  "hour": 22,
  "temperature": 28,
  "humidity": 72,
  "occupancy": 2,
  "scene": "观影模式",
  "top_candidates": [
    "打开空调",
    "打开风扇",
    "打开窗户"
  ],
  "preference_summary": {
    "preferred_ac_temp": 26
  }
}
```

Do not upload:

- full device logs
- family member identities
- raw long-term memory text
- full voice history
- full correction history
- full schedule or behavior timeline

### 5.3 Suggested API

```python
class PrivacyRedactor:
    def redact_text(self, text: str) -> str: ...
    def summarize_preferences(self, preference_store) -> dict: ...
    def build_cloud_context(self, context, candidates, session_store=None, preference_store=None) -> dict: ...
```

Responsibilities:

- keep only `hour`, `temperature`, `humidity`, `occupancy`, `scene`
- keep only top candidate actions
- include only compact preference summary
- exclude raw history by default

## 6. Integration Plan

### 6.1 Main Flow

Integrate into:

- `main.py`
- `web/server.py`

Suggested runtime flow:

```text
User input
-> SessionStore.update_from_query(...)
-> LanguageNormalizer
-> BSR
-> LSR
-> if cloud path is needed:
   -> PrivacyRedactor.build_cloud_context(...)
   -> LLMDecider receives redacted context only
-> local validation / execution
-> SessionStore.update_from_decision(...)
-> PreferenceStore.record_action_accept(...) when stable positive feedback is observed
-> KnowledgeBase keeps textual evidence
```

### 6.2 LLM Integration Rule

Even before adding a separate inference router, cloud-side prompt construction should stop reading full raw context directly.

Instead:

- local full context stays on-device
- cloud prompt only uses redacted context summary

### 6.3 Write-back Rule

Per interaction:

1. raw query arrives
   - write to `SessionStore`
2. normalized query produced
   - update `SessionStore`
3. decision executed successfully
   - update `SessionStore.last_action`
   - update current scene if needed
4. user acceptance / correction accumulates
   - write stable preference to `PreferenceStore`
   - write evidence text to `KnowledgeBase`
5. voice correction
   - continue writing to `VoiceFeedbackStore`

## 7. Recommended File Layout

```text
core/
  memory/
    __init__.py
    session_store.py
    preference_store.py
  privacy/
    __init__.py
    redactor.py
```

## 8. MVP Scope

Implement the first version in a narrow, testable scope.

### MVP-1 SessionStore

- load/save JSON
- persist current scene
- persist latest input
- persist latest action

### MVP-2 PreferenceStore

- load/save JSON
- persist AC temperature preference
- persist light brightness preference
- persist scene preference count

### MVP-3 PrivacyRedactor

- build cloud context with:
  - `hour`
  - `temperature`
  - `humidity`
  - `occupancy`
  - `scene`
  - `top_candidates`
  - compact preference summary

### MVP-4 Integration

- update `main.py`
- update `web/server.py`
- do not refactor unrelated modules in this phase

## 9. Validation Criteria

The design is considered complete when:

- restarting HomeMind can restore latest scene and latest action
- repeated accepted behavior forms stable preference entries
- LSR or later ranking can read preference boost from structured storage
- cloud-side input no longer contains full raw memory/history
- minimal cloud payload is visible in logs or debug output
- memory responsibilities are clearly separated across session, preference, and KB

## 10. Delivery Order

Recommended implementation order:

1. `core/memory/session_store.py`
2. `core/memory/preference_store.py`
3. `core/privacy/redactor.py`
4. integrate into `main.py`
5. integrate into `web/server.py`
6. add focused tests

## 11. Alignment with UPGRADE_PLAN

This document maps directly to the following `UPGRADE_PLAN.md` targets:

- 5.2 privacy boundary and minimal upload
- 5.3 context persistence
- 6.1 P0 foundational upgrade
- 6.2 P1 local memory and preference write-back

In other words:

- `SessionStore` solves short-term persistence
- `PreferenceStore` solves structured long-term preference
- `PrivacyRedactor` solves privacy redaction and minimal cloud upload

Together they close two of the largest remaining gaps in the current codebase.
