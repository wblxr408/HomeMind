# SceneStore 场景存储 — 面试拷打指南

## 模块定位

SceneStore 管理智能家居场景配置的持久化存储。每个场景定义了多个设备的默认状态（开关、调温等）。

```
用户: "切换到观影模式"
      ↓
SceneStore.get_scene("观影模式")
      ↓
{
    "灯光": {"action": "adjust", "params": {"brightness": 30}},
    "空调": {"action": "adjust", "params": {"temperature": 25}},
    "电视": {"action": "on", "params": {}},
    "音响": {"action": "on", "params": {"volume": 40}},
}
      ↓
DeviceController 执行每个设备的动作
```

---

## 核心接口

### `get_scene(name) -> Optional[Dict]`
获取场景配置，返回深拷贝（避免意外修改）。

### `add_scene(name, config) / update_scene(name, config)`
增改场景，自动保存。

### `delete_scene(name)`
删除场景，不允许删除内置场景（需额外判断）。

### `list_scenes() -> List[str]`
列出所有可用场景名称。

---

## 默认场景配置

```python
DEFAULT_SCENE_CONFIGS = {
    "睡眠模式": {
        "灯光": {"action": "adjust", "params": {"brightness": 10}},
        "空调": {"action": "adjust", "params": {"temperature": 26}},
        "电视": {"action": "off"},
        "音响": {"action": "off"},
    },
    "观影模式": {
        "灯光": {"action": "adjust", "params": {"brightness": 30}},
        "空调": {"action": "adjust", "params": {"temperature": 25}},
        "电视": {"action": "on"},
        "音响": {"action": "on", "params": {"volume": 40}},
    },
    # ... 共9个场景
}
```

---

## 自动初始化

```python
def load(self):
    if not os.path.exists(self.path):
        self.scenes = deepcopy(DEFAULT_SCENE_CONFIGS)
        self.save()  # 首次运行自动创建
        return
```

---

## 面试核心问题

### 1. 为什么要深拷贝？
```python
return deepcopy(scene) if scene is not None else None
```
- 防止调用方修改内部 `self.scenes` 字典
- 每次 get 都返回新副本

### 2. 为什么场景配置用 JSON 持久化？
- JSON 人类可读，便于调试
- 无需数据库依赖
- 文件系统足够应对家庭级数据量

### 3. 如何扩展自定义场景？
- `add_scene(name, config)` 直接添加
- 新场景保存到 `data/scenes.json`
- 重启后自动加载
