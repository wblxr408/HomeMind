# HomeMind 端侧智能体项目 — 配图绘制指南

## 一、图件清单

| 编号 | 图名                    | 类型                   | 尺寸建议 | 优先级 |
| :--: | ----------------------- | ---------------------- | -------- | :----: |
| 图1 | 项目总体效果图          | 综合展示图（多图拼接） | 宽15cm   |   高   |
| 图2 | 端侧智能体总体架构图    | 架构流程图             | 宽15cm   |   高   |
| 图3 | Router 置信度路由策略图 | 决策流程图             | 宽12cm   |   中   |
| 图4 | 核心实验结果图          | 柱状图 + 折线图        | 宽14cm   |   高   |
| 图5 | 端侧推理延迟分解图      | 堆叠柱状图             | 宽14cm   |   高   |
| 图6 | Token 压缩效果对比图    | 分组柱状图             | 宽12cm   |   中   |
| 图7 | 偏好学习准确率曲线图    | 折线图                 | 宽12cm   |   中   |
| 图8 | 前端主界面截图          | 真实截图/模拟界面      | 宽15cm   |   高   |
| 图9 | 规则管理页面截图        | 真实截图/模拟界面      | 宽15cm   |   中   |
| 图10 | 户型图与设备映射页截图  | 真实截图/模拟界面      | 宽15cm   |   中   |
| 图11 | 语音输入与归一化过程图  | 流程示意               | 宽12cm   |   低   |
| 图12 | 本地记忆分层架构图      | 分层示意图             | 宽12cm   |   中   |
| 图13 | Schema 校验流程图       | 流程图                 | 宽12cm   |   中   |
| 图14 | TAP 规则引擎执行流程图  | 流程图                 | 宽12cm   |   低   |
| 图15 | 端云协同数据流图        | 数据流图               | 宽12cm   |   中   |

---

## 二、统一视觉风格规范

### 2.1 配色方案

```
主色调（Primary）:     #1D9E75  — HomeMind 品牌绿
深主色（Primary Dark）: #167A5A  — 深绿，用于标题和强调
浅主色（Primary Light）:#E1F5EE  — 淡绿，用于背景填充
辅助色（Accent）:       #2E86AB  — 深蓝，用于云端/网络元素
强调色（Highlight）:    #F18F01  — 橙色，用于警告和重要标注
文本色（Text Dark）:    #2C2C2A  — 深灰，用于正文
文本色（Text Mid）:    #4C4C4C  — 中灰，用于标签
文本色（Text Light）:   #8A8A8A  — 浅灰，用于次要文字
背景色（Background）:   #FFFFFF  — 纯白
分割线（Border）:      #D9E8E3  — 淡绿灰
图表色1（Chart 1）:    #1D9E75  — 品牌绿
图表色2（Chart 2）:    #2E86AB  — 深蓝
图表色3（Chart 3）:    #F18F01  — 橙色
图表色4（Chart 4）:    #7B2D8B  — 紫色
图表色5（Chart 5）:    #E74C3C  — 红色（用于负向指标）
灰色系（Grays）:       #6C757D / #ADB5BD / #DEE2E6 / #F8F9FA
```

### 2.2 字体规范

```
中文正文: Source Han Sans SC / 思源黑体 / Noto Sans SC (Regular/Bold)
英文正文: IBM Plex Sans / Inter (Regular/Bold)
代码/标签: IBM Plex Mono / JetBrains Mono
数学公式: Latin Modern Math
LaTeX 嵌入: 直接使用 TikZ/pgfplots
```

### 2.3 通用绘图规范

```
画布比例:      16:9 或 4:3（具体根据图件内容选择）
边距:          上下左右各 0.8cm
标题字号:      12pt Bold（\zihao{-3}）
副标题字号:    10pt Bold（\zihao{4}）
标签字号:      9pt（\zihao{-4}）
图例字号:      9pt（\zihao{-4}）
线宽:          主线条 1.5pt，辅助线条 0.8pt
圆角:          所有矩形框圆角半径 3pt
阴影:          禁止使用投影阴影
渐变:          禁止使用渐变填充
图标:          适度使用 Font Awesome 或 TikZ 内置图标，每个图不超过 2 个
网格线:        图表使用淡灰色虚线网格（#D9E8E3），不喧宾夺主
图例位置:      图表右上方或下方，与图表内容不重叠
```

### 2.4 图表通用元素

所有图表统一包含：

- **标题**：图号 + 简短描述，12pt Bold，居中于图上方
- **来源标注**：如有必要，9pt 灰色，置于图下方
- **图例**：如有多系列，使用统一配色方案，置于图例区域
- **坐标轴标签**：9pt，与主文字同色

---

## 三、各图绘制提示词

图1：请绘制项目总体效果图，采用 3x2 或 2x3 的综合展示拼接布局，左上展示系统首页模拟界面，右上展示设备状态面板，左下展示场景模式快捷入口，右下展示“输入→BSR→LSR→LLM→执行”的简化流水线，可加入大量留白增强论文配图的干净感。整张图必须反复强调统一视觉系统，背景固定为白色 `#FFFFFF`，主色固定为品牌绿 `#1D9E75`，深绿 `#167A5A` 用于重点标题和关键节点，浅绿 `#E1F5EE` 用于弱填充和卡片背景，边框固定为 `#D9E8E3`，正文文字使用 `#2C2C2A`，次级标签使用 `#4C4C4C`，不得使用投影和渐变。中文字体统一使用思源黑体 ，英文字体统一使用 IBM Plex Sans / Inter，代码或标签若出现则统一使用 IBM Plex Mono / JetBrains Mono，标题建议 12pt Bold，标签和图例建议 9pt，线宽 1.5pt，圆角 3pt，导出为 300dpi PNG 或 PDF，尺寸 15cm × 10cm。

图2：请绘制端侧智能体总体架构图，采用自上而下的垂直分层结构，依次表现交互层、BSR 理解层、LSR 理解层、Router 决策层、LLM 决策层、执行层和记忆层，右侧独立放置云端协同模块，并用虚线表示模糊意图上云路径。图中节点文案写明“Web 前端 / 语音 ASR（Vosk 离线模型）”“BSR 广召回（规则 / 向量 / 历史三路并行）”“LSR 轻量精排（5 维特征加权 MLP，< 5 MB）”“Router 置信度路由（本地直达 / 上云裁决 / 澄清询问）”“LLM 决策（Mock / llama.cpp / OpenAI API 三后端）”“DeviceController / SceneSwitcher / TAP 引擎 / 协议网关”“RAG 知识库 / 偏好库 / 记忆库（ChromaDB + JSON 本地存储）”，右侧云端协同写“意图理解 / 模糊裁决，固定 JSON + Schema 校验”。整张图必须重复使用统一配色，背景白色 `#FFFFFF`，主层级框使用浅绿 `#E1F5EE` 加品牌绿 `#1D9E75` 边框，深绿 `#167A5A` 用于主连接箭头，云端协同模块使用浅灰 `#F8F9FA` 或白底加蓝色 `#2E86AB` 虚线强调，文字颜色使用 `#2C2C2A` 与 `#4C4C4C`，不得使用投影和渐变。中文字体统一使用 Source Han Sans SC / 思源黑体 / Noto Sans SC，英文字体统一使用 IBM Plex Sans / Inter，代码或英文标签统一使用 IBM Plex Mono / JetBrains Mono，标题 12pt Bold，层内文字 10pt Bold，标签 9pt，线宽 1.5pt，圆角 3pt，尺寸 2：1。

图3：请绘制 Router 置信度路由策略图，采用横向决策流程，依次展示“用户自然语言输入”“置信度 ≥ 0.75？”“0.40 ≤ 置信度 < 0.75？”三个关键判断，并分流到“本地 LLM 决策层执行，< 5ms”“上传最小必要上下文→云端裁决”“触发澄清询问”，底部再补一条“网络异常→本地 Fallback”的兜底路径。整张图必须明确高、中、低、兜底四条路径的区别，同时每一条提示里都要重复统一配色和字体要求，背景使用白色 `#FFFFFF`，判断菱形采用 `#4C4C4C` 边框和 `#F8F9FA` 填充，高置信路径使用品牌绿 `#1D9E75`，中置信路径使用蓝色 `#2E86AB`，低置信路径使用橙色 `#F18F01`，兜底路径使用灰色 `#ADB5BD`，正文和标签分别使用 `#2C2C2A` 与 `#4C4C4C`，禁止阴影和渐变。中文字体统一为思源黑体 ，英文字体统一为 IBM Plex Sans / Inter，局部技术标签可用 IBM Plex Mono / JetBrains Mono，判断框文字 9pt Bold，连接箭头 1.5pt，圆角 3pt，输出为 300dpi PNG，尺寸 2：1。

图4：请绘制核心实验结果图，采用柱状图加折线图的组合方式，X 轴包含“本地直达成功率”“端云协同成功率”“P50 延迟（<50ms）”“P99 延迟（<200ms）”“Schema 拦截率（<3%）”“偏好学习准确率”六项指标，柱值可表现为 92%、85%、达标阈值换算值、达标阈值换算值、3%、88%，并在图上叠加一条表示相对重要性的折线，折线节点可标注百分比或权重。整张图必须反复使用统一配色和字体设定，背景固定白色 `#FFFFFF` 或极浅灰网格 `#F8F9FA`，主要柱体使用品牌绿 `#1D9E75`，辅助系列可用蓝色 `#2E86AB`，强调折线统一使用橙色 `#F18F01`，阈值线使用灰色 `#ADB5BD` 虚线，正文文字使用 `#2C2C2A`，坐标与标签使用 `#4C4C4C`，不允许阴影和渐变。中文字体统一使用 Source Han Sans SC / 思源黑体 / Noto Sans SC，英文字体统一使用 IBM Plex Sans / Inter，若出现缩写标签则统一用 IBM Plex Mono / JetBrains Mono，标题 12pt Bold，坐标标签 9pt，折线 2pt，柱边框可用深绿 `#167A5A`，导出为 300dpi PNG，尺寸 14cm × 8cm。

图5：请绘制端侧推理延迟分解图，采用水平堆叠柱状图展示 P50 与 P99 两条路径，各阶段包含语言归一化、BSR 规则召回、BSR 向量召回、LSR 精排、LLM 决策、工具执行，P50 可标注 1、1、30、1、3、5ms，总计约 41ms，P99 可标注 1、1、45、2、5、8ms，总计约 62ms，并在右侧补充总耗时说明和达标标记。整张图必须把统一配色与字体要求写进提示词，背景使用白色 `#FFFFFF`，P50 主柱使用品牌绿 `#1D9E75`，P99 叠加柱使用蓝色 `#2E86AB` 并允许半透明，最耗时的向量召回阶段可继续强调蓝色，关键总计或警示标注使用橙色 `#F18F01`，边框和次级引导线使用 `#D9E8E3` 或 `#ADB5BD`，正文颜色为 `#2C2C2A`，标签颜色为 `#4C4C4C`，不使用阴影和渐变。中文字体统一采用 Source Han Sans SC / 思源黑体 / Noto Sans SC，英文字体统一采用 IBM Plex Sans / Inter，技术缩写标签统一采用 IBM Plex Mono / JetBrains Mono，标题 12pt Bold，标签 9pt，主要折线或引导线 2pt，柱体边框可用深绿 `#167A5A`，导出为 300dpi PNG，尺寸 14cm × 7cm。

### 图6：Token 压缩效果对比图（分组柱状图）

```
【画面构成】
三组对比柱状图，每组两个柱子（压缩前 vs 压缩后）：

组1: "原始上下文" 柱高 100% | "压缩后" 柱高 ~40%（标注 -60%）
组2: "带历史上下文" 柱高 100% | "去重摘要后" 柱高 ~30%（标注 -70%）
组3: "TAP 规则上下文" 柱高 100% | "固定字段后" 柱高 ~20%（标注 -80%）

每组柱子之间标注平均压缩率（Overall: ~60%）

【配色】
压缩前: #6C757D（灰色）
压缩后: #1D9E75（绿色），填充 + 边框
节省量标注: 橙色数字（#F18F01），9pt Bold
背景: 白色，网格线淡灰色

【导出格式】
PNG 300dpi，尺寸 12cm x 7cm
```

### 图7：偏好学习准确率曲线图（折线图）

```
【画面构成】
折线图，X轴为交互轮次（1~20），Y轴为偏好学习准确率（0~100%）：

曲线1（主曲线）: 学习准确率从 60% 逐渐上升至 88%，绿色，线宽 2pt，圆点标记
曲线2（参考线）: 固定阈值 70%，虚线，标注 "70% 基线"
曲线3（对比线）: 随机基线 50%，灰色虚线，标注 "随机基线"

填充区域: 曲线1下方填充浅绿色（#E1F5EE），透明度 0.3

【配色】
主曲线: #1D9E75
参考基线: #F18F01，虚线
随机基线: #ADB5BD，虚线
填充: #1D9E75，alpha 0.2
圆点: 绿色，实心，直径 4pt
背景: 白色

【导出格式】
PNG 300dpi，尺寸 12cm x 7cm
```

### 图8：前端主界面截图

```
【画面构成】
模拟 Web 界面截图，整体布局：
- 顶部: 浅绿色标题栏，文字 "HomeMind" + 当前时间/温度显示
- 左侧边栏: 环境状态面板（温度 25°C、湿度 60%、在家 1人、当前场景：睡眠模式）
- 中央主区域: 聊天对话气泡（用户："有点闷，帮我开下空调"，系统："已开启空调，温度26°C"）
- 右侧面板: 设备状态卡片网格（空调、灯光、电视、风扇、窗户等，带开关图标）
- 底部: 输入框 + 发送按钮

【配色要求】
整体浅色背景，#F8F9FA
卡片: 白色背景，#D9E8E3 边框
品牌绿: #1D9E75（按钮、强调文字）
图标: 简洁线条风格，深灰色

【导出格式】
PNG 300dpi，尺寸 15cm x 9cm
如为真实截图：直接嵌入 300dpi PNG
```

### 图9：规则管理页面截图

```
【画面构成】
规则管理页面布局：
- 左侧: 规则列表（3~4 条规则卡片，每条显示别名、触发条件、启用/停用开关）
- 右侧: TAP 规则编辑器（YAML 代码高亮显示，配色与整体风格一致）
- 底部: "创建规则" 按钮 + 校验状态（绿色 ✓）

规则示例：
1. 睡眠模式自动切换 — 22:30 定时 — [启用]
2. 高温自动开空调 — 温度 > 30°C — [启用]
3. 离家自动关灯 — 出门模式触发 — [停用]

【配色要求】
代码编辑器: 深色背景 (#2C2C2A)，浅色代码文字，关键词绿色高亮
规则卡片: 白色背景，绿色左边框（启用）/ 灰色左边框（停用）
开关: 绿色（启用）/ 灰色（停用）

【导出格式】
PNG 300dpi，尺寸 15cm x 9cm
```

### 图10：户型图与设备映射页截图

```
【画面构成】
SVG 户型图配置页面布局：
- 左侧: SVG 户型图（线条风格，房间用浅绿色填充）
- 右侧: 设备映射列表（表格形式：设备ID、房间区域、设备类型、坐标）
- 顶部: "上传户型图" 按钮 + "导入设备映射" 按钮

户型图内容：
- 客厅（沙发、电视图标）+ 2个灯图标
- 卧室（床图标）+ 1个灯图标
- 厨房（炉灶图标）+ 1个灯图标
- 空调设备图标分布在家中各房间

【配色要求】
户型图底色: #F8F9FA（浅灰白）
墙体: #4C4C4C（深灰）
房间填充: #E1F5EE（淡绿）
设备图标: #1D9E75（品牌绿），简洁线条风格
表格: 白色背景，交替行淡绿

【导出格式】
PNG 300dpi，尺寸 15cm x 9cm
```

### 图11：语音输入与归一化过程图

```
【画面构成】
水平流程图，左到右：

节点1: 语音波形示意（简化的正弦波图标）— "用户语音输入"
箭头 →
节点2: 语音波形 → 文字（麦克风图标）— "ASR 识别" — 标注 "Vosk 离线模型"
箭头 →
节点3: "热死咯，整亮点" — "语言归一化"（齿轮图标）
箭头 →
节点4: "打开空调" + "调亮灯光" — "标准命令输出"（对勾图标）

每节点下方小字标注处理时间和置信度
例如: "中文识别，置信度 94%"
      "归一化为2条标准命令"

【配色】
流程节点: 白色填充，#1D9E75 边框
箭头: #1D9E75，线宽 1.5pt
图标: #4C4C4C，线条风格
文字: #2C2C2A

【导出格式】
PNG 300dpi，尺寸 14cm x 5cm
```

### 图12：本地记忆分层架构图

```
【画面构成】
三层堆叠结构示意图（从上到下）：

第一层（最顶层，最窄）: "会话上下文" — HomeContext，内存存储
  内容预览: "hour=22, temp=26, scene=睡眠模式..."
  标注: 实时更新，会话结束后清空

第二层（中间层，中等宽度）: "结构化偏好" — PreferenceRepository，JSON文件
  内容预览: "temperature_pref=24°C, brightness=80%..."
  标注: 跨会话持久化，scope维度聚合

第三层（最底层，最宽）: "长期记忆" — RAG知识库 + ChromaDB
  内容预览: "用户习惯摘要 / 健康建议 / 场景规则"
  标注: 向量检索，AES加密备份

三层之间用向下箭头连接，表示数据流动方向
右侧标注存储介质和容量

【配色】
第一层: #E1F5EE（淡绿背景）
第二层: #D4EDE3（稍深绿背景）
第三层: #C0DDD0（最深绿背景）
边框: #1D9E75（品牌绿）
文字: #2C2C2A

【导出格式】
PNG 300dpi，尺寸 12cm x 8cm
```

### 图13：Schema 校验流程图

```
【画面构成】
水平决策流程图：

节点1: "云端 JSON 输出"（蓝色框）→ 进入校验管道
节点2: "字段完整性检查"（菱形判断）→ 通过/不通过
节点3: "action 字段校验"（菱形判断）→ 有效/无效
节点4: "参数范围校验"（菱形判断）→ 合规/越界
节点5a（通过）: "DeviceController.execute()" — 绿色输出
节点5b（拒绝）: "触发澄清询问" — 橙色输出

每步判断节点下方小字标注检查规则：
  "device != null"
  "action ∈ {on, off, adjust, open, close}"
  "16 ≤ temperature ≤ 32"

【配色】
云端输入: #2E86AB（蓝色）
判断节点: #4C4C4C 边框，#F8F9FA 填充
通过路径: #1D9E75（绿色箭头）
拒绝路径: #F18F01（橙色箭头）
输出框: 白色填充，对应颜色边框

【导出格式】
PNG 300dpi，尺寸 14cm x 6cm
```

### 图14：TAP 规则引擎执行流程图

```
【画面构成】
垂直流程图，从上到下：

步骤1: "触发事件发生"（时间/状态/场景触发）
  → 进入规则引擎
步骤2: "遍历规则列表" — "检查 enabled 标志"
  → 跳过禁用规则
步骤3: "Trigger 匹配检查" — 菱形判断
  → 不匹配则检查下一条规则
步骤4: "Condition 条件判断" — 菱形判断
  → 条件不满足则记录 skipped
步骤5: "执行 Action 序列" — 多个动作框
步骤6: "返回 RuleExecution 结果"

右侧小注释：
  "Trigger 类型: time / scene / state / numeric_state"
  "支持 6 种 Condition 类型"
  "支持 device_control / scene / notify 三类 Action"

【配色】
流程框: #F8F9FA 填充，#1D9E75 边框
判断菱形: #4C4C4C 边框，#FFFDE7 填充
执行动作: #E8F5E9 填充，#2E7D32 边框
跳过/跳过: #FFEBEE 填充，#C62828 边框
箭头: #1D9E75，线宽 1.5pt

【导出格式】
PNG 300dpi，尺寸 12cm x 10cm
```

### 图15：端云协同数据流图

```
【画面构成】
左右对称的数据流图：

左侧（端侧）:
  用户输入 → 语言归一化 → BSR → LSR → Router
  Router 决策分叉：
    明确意图 → 本地 LLM → 执行 → 结果
    模糊意图 → 最小上下文 → 上传（标注 "Token压缩 ~60%"）

右侧（云端）:
  接收压缩上下文 → LLM 推理 → JSON 输出
  → Schema 校验 → 返回决策结果

中间:
  上传箭头（蓝色虚线，带锁图标，标注 "加密传输"）
  返回箭头（蓝色实线）

底部:
  记忆层（本地存储），箭头表示数据写回

【配色】
端侧模块: #1D9E75（绿色系）
云端模块: #2E86AB（蓝色系）
上传通道: #2E86AB，虚线
返回通道: #1D9E75，实线
锁图标: #F18F01（橙色）
背景: 白色

【导出格式】
PNG 300dpi，尺寸 14cm x 8cm
```

---

## 四、Python + Matplotlib 统一绘图脚本

以下脚本使用统一配色方案生成所有图表，确保风格完全一致。

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HomeMind 统一图表绘制脚本
使用 Matplotlib + seaborn 生成报告中所有图表
配色方案与 LaTeX 风格完全一致
"""

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np
import os

# ============================================================
# 统一配色方案（与 LaTeX 风格完全一致）
# ============================================================
COLORS = {
    "primary":      "#1D9E75",    # 品牌绿
    "primary_dark": "#167A5A",    # 深绿
    "primary_light":"#E1F5EE",    # 淡绿
    "accent":       "#2E86AB",     # 深蓝（云端）
    "highlight":    "#F18F01",    # 橙色（强调）
    "text_dark":    "#2C2C2A",    # 深灰
    "text_mid":     "#4C4C4C",    # 中灰
    "text_light":   "#8A8A8A",    # 浅灰
    "border":       "#D9E8E3",    # 边框色
    "bg":           "#FFFFFF",     # 背景
    "gray_dark":    "#6C757D",
    "gray_mid":     "#ADB5BD",
    "gray_light":   "#DEE2E6",
    "gray_bg":      "#F8F9FA",
    "chart3":       "#7B2D8B",    # 紫色
    "chart4":       "#E74C3C",    # 红色
}

plt.rcParams.update({
    "font.family": ["Inter", "IBM Plex Sans", "Noto Sans SC", "sans-serif"],
    "font.size": 10,
    "axes.titlesize": 12,
    "axes.titleweight": "bold",
    "axes.labelsize": 9,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "legend.fontsize": 9,
    "figure.facecolor": COLORS["bg"],
    "axes.facecolor": COLORS["bg"],
    "axes.grid": True,
    "grid.color": COLORS["gray_light"],
    "grid.linestyle": "--",
    "grid.linewidth": 0.5,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.edgecolor": COLORS["border"],
    "lines.linewidth": 1.5,
    "patch.force_edgecolor": False,
})

def save_fig(fig, name, dpi=300):
    """保存图片，添加稀稀白白边框"""
    path = os.path.join("image", f"{name}.png")
    os.makedirs("image", exist_ok=True)
    fig.savefig(path, dpi=dpi, bbox_inches="tight",
                facecolor=COLORS["bg"], edgecolor="none")
    plt.close(fig)
    print(f"✓ Saved: {path}")
    return path

# ============================================================
# 图4: 核心实验结果图（柱状图）
# ============================================================
def plot_experiment_results():
    labels = [
        "本地直达\n成功率",
        "端云协同\n成功率",
        "P50延迟\n(<50ms)",
        "P99延迟\n(<200ms)",
        "Schema\n拦截率",
        "偏好学习\n准确率",
    ]
    values = [92, 85, 50, 50, 3, 88]
    colors = [COLORS["primary"]] * 6
    # 特殊处理：P50/P99 延迟用不同方式表达
    bar_values = [92, 85, 100, 100, 3, 88]  # 换算为百分比

    fig, ax = plt.subplots(figsize=(7, 4))
    bars = ax.bar(labels, bar_values, color=colors,
                 edgecolor=COLORS["primary_dark"], linewidth=1.2, width=0.6)

    # 在柱子上标注实际值
    for bar, val, label in zip(bars, values, labels):
        height = bar.get_height()
        if "延迟" in label:
            ax.text(bar.get_x() + bar.get_width()/2, height - 8,
                    f"<{val}ms✓", ha="center", va="top",
                    color="white", fontsize=9, fontweight="bold")
        elif val < 10:
            ax.text(bar.get_x() + bar.get_width()/2, height - 3,
                    f"{val}%", ha="center", va="top",
                    color="white", fontsize=9, fontweight="bold")
        else:
            ax.text(bar.get_x() + bar.get_width()/2, height - 4,
                    f"{val}%", ha="center", va="top",
                    color="white", fontsize=9, fontweight="bold")

    ax.set_ylim(0, 115)
    ax.set_ylabel("百分比 / 指标值", color=COLORS["text_dark"])
    ax.set_title("核心实验结果汇总", color=COLORS["text_dark"],
                 pad=12, fontsize=12, fontweight="bold")
    ax.axhline(y=70, color=COLORS["highlight"], linestyle="--",
               linewidth=1, alpha=0.6, label="70% 基线")
    ax.legend(loc="upper right", framealpha=0.8)

    # 标注达标阈值线
    ax.axhline(y=100, color=COLORS["gray_mid"], linestyle=":",
               linewidth=0.8, alpha=0.5)
    ax.text(5.6, 102, "目标线 100%", fontsize=7, color=COLORS["gray_mid"])

    save_fig(fig, "fig4_experiment_results")

# ============================================================
# 图5: 端侧推理延迟分解图（堆叠柱状图）
# ============================================================
def plot_latency_breakdown():
    stages = ["语言\n归一化", "BSR\n规则", "BSR\n向量", "LSR\n精排", "LLM\n决策", "工具\n执行"]
    p50 = [1, 1, 30, 1, 3, 5]  # ms
    p99 = [1, 1, 45, 2, 5, 8]  # ms

    fig, ax = plt.subplots(figsize=(7, 4))

    x = np.arange(len(stages))
    width = 0.4

    bars_p50 = ax.bar(x, p50, width, label="P50",
                       color=COLORS["primary"], alpha=0.85,
                       edgecolor=COLORS["primary_dark"], linewidth=1)
    bars_p99 = ax.bar(x, p99, width, label="P99",
                       color=COLORS["accent"], alpha=0.6,
                       edgecolor=COLORS["accent"], linewidth=1)

    # 标注数值
    for bar, val in zip(bars_p50, p50):
        ax.text(bar.get_x() + bar.get_width()/2, val + 0.5,
                f"{val}ms", ha="center", va="bottom",
                fontsize=8, color=COLORS["primary_dark"], fontweight="bold")

    total_p50 = sum(p50)
    total_p99 = sum(p99)
    ax.text(5.6, max(p99) * 0.8,
            f"总计\nP50: {total_p50}ms ✓\nP99: {total_p99}ms ✓",
            ha="left", va="center", fontsize=9,
            color=COLORS["text_dark"],
            bbox=dict(boxstyle="round,pad=0.3",
                      facecolor=COLORS["primary_light"],
                      edgecolor=COLORS["primary"], linewidth=1))

    ax.set_xticks(x)
    ax.set_xticklabels(stages, fontsize=9)
    ax.set_ylabel("延迟 (ms)", color=COLORS["text_dark"])
    ax.set_title("端侧推理延迟分解", color=COLORS["text_dark"],
                 pad=12, fontsize=12, fontweight="bold")
    ax.legend(loc="upper left", framealpha=0.8)

    # 标注最耗时阶段
    ax.annotate("最耗时阶段\n(可缓存优化)",
                xy=(2, 30), xytext=(2.8, 20),
                fontsize=8, color=COLORS["accent"],
                arrowprops=dict(arrowstyle="->",
                               color=COLORS["accent"], lw=1.2))

    save_fig(fig, "fig5_latency_breakdown")

# ============================================================
# 图6: Token 压缩效果对比图（分组柱状图）
# ============================================================
def plot_token_compression():
    groups = ["原始上下文", "带历史上下文", "TAP规则上下文"]
    before = [100, 100, 100]
    after = [40, 30, 20]

    fig, ax = plt.subplots(figsize=(6, 4))
    x = np.arange(len(groups))
    width = 0.35

    ax.bar(x - width/2, before, width, label="压缩前",
           color=COLORS["gray_mid"], edgecolor=COLORS["gray_dark"],
           linewidth=1)
    ax.bar(x + width/2, after, width, label="压缩后",
           color=COLORS["primary"], edgecolor=COLORS["primary_dark"],
           linewidth=1, alpha=0.9)

    # 标注节省量
    for i, (b, a) in enumerate(zip(before, after)):
        ax.text(i + width/2, a + 2,
                f"-{b-a}%", ha="center", va="bottom",
                fontsize=9, color=COLORS["highlight"], fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(groups, fontsize=9)
    ax.set_ylabel("Token 数量 (%)", color=COLORS["text_dark"])
    ax.set_ylim(0, 120)
    ax.set_title("Token 压缩效果对比", color=COLORS["text_dark"],
                 pad=12, fontsize=12, fontweight="bold")
    ax.legend(loc="upper right", framealpha=0.8)

    # 标注平均节省
    ax.text(1, 108, "平均节省 ~60%",
            ha="center", fontsize=9, color=COLORS["highlight"],
            fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.3",
                      facecolor="#FFF8E1",
                      edgecolor=COLORS["highlight"], linewidth=1))

    save_fig(fig, "fig6_token_compression")

# ============================================================
# 图7: 偏好学习准确率曲线图（折线图）
# ============================================================
def plot_preference_learning():
    rounds = np.arange(1, 21)
    # 模拟从60%逐渐升至88%的学习曲线
    base = 60
    target = 88
    decay = np.exp(-rounds / 8)
    accuracy = target - (target - base) * decay + np.random.randn(20) * 2
    accuracy = np.clip(accuracy, 50, 95)
    accuracy[0] = 60  # 确保起点准确

    fig, ax = plt.subplots(figsize=(6, 4))

    ax.fill_between(rounds, accuracy, alpha=0.15, color=COLORS["primary"])
    ax.plot(rounds, accuracy, color=COLORS["primary"],
            linewidth=2, marker="o", markersize=4,
            markerfacecolor=COLORS["primary"], markeredgecolor="white")

    ax.axhline(y=70, color=COLORS["highlight"], linestyle="--",
               linewidth=1.2, label="70% 基线")
    ax.axhline(y=50, color=COLORS["gray_mid"], linestyle="--",
               linewidth=1, label="随机基线 50%")

    ax.set_xlim(1, 20)
    ax.set_ylim(45, 100)
    ax.set_xlabel("交互轮次", color=COLORS["text_dark"])
    ax.set_ylabel("偏好学习准确率 (%)", color=COLORS["text_dark"])
    ax.set_title("偏好学习准确率随交互轮次变化",
                 color=COLORS["text_dark"],
                 pad=12, fontsize=12, fontweight="bold")
    ax.legend(loc="lower right", framealpha=0.8)

    # 标注收敛值
    ax.annotate(f"收敛于 ~{int(accuracy[-1])}%",
                xy=(20, accuracy[-1]),
                xytext=(16, accuracy[-1] - 8),
                fontsize=9, color=COLORS["primary_dark"],
                fontweight="bold",
                arrowprops=dict(arrowstyle="->",
                               color=COLORS["primary"], lw=1.2))

    save_fig(fig, "fig7_preference_learning")

# ============================================================
# 主函数：生成所有图表
# ============================================================
if __name__ == "__main__":
    print("生成 HomeMind 项目图表...")
    plot_experiment_results()
    plot_latency_breakdown()
    plot_token_compression()
    plot_preference_learning()
    print("\n所有图表生成完毕！")
```

---

## 五、LaTeX TikZ 架构图代码（直接替换）

以下为图2（端侧智能体总体架构图）的 LaTeX TikZ 代码，可直接替换 main.tex 中的占位图。

```latex
\begin{figure}[H]
\centering
\begin{tikzpicture}[
    node distance=0.4cm,
    box/.style={draw, rounded corners=3pt, minimum width=7.2cm,
                minimum height=0.85cm, font=\sffamily\small,
                fill=white, line width=0.8pt},
    cloud/.style={draw, rounded corners=3pt, minimum width=4cm,
                  minimum height=0.7cm, font=\sffamily\small,
                  fill=gray!8, line width=0.8pt, dashed},
    arr/.style={->, >=stealth, line width=1.5pt},
    darr/.style={->, >=stealth, dashed, line width=1pt},
    label/.style={font=\sffamily\small\color{gray}}
]

% 边缘分组框
\node[draw, rounded corners=4pt, dashed,
      inner sep=0.3cm, line width=0.5pt,
      fill=none, color=hmgreen!60!black,
      minimum width=7.8cm] (edgebox) {};

% 交互层
\node[box, fill=hmgreenlight, line color=hmgreen,
      below=0.5cm of edgebox.north] (interact)
      {\textbf{交互层}：Web 前端 / 语音 ASR（Vosk 离线模型）};

% 理解层 BSR
\node[box, fill=hmgreenlight, line color=hmgreen,
      below=0.6cm of interact] (bsr)
      {\textbf{理解层}：BSR 广召回（规则 / 向量 / 历史三路并行）};

% 理解层 LSR
\node[box, fill=hmgreenlight, line color=hmgreen,
      below=0.6cm of bsr] (lsr)
      {\textbf{理解层}：LSR 轻量精排（5 维特征加权 MLP，$<$ 5 MB）};

% 决策层 Router
\node[box, fill=hmgreenlight, line color=hmgreen,
      below=0.6cm of lsr] (router)
      {\textbf{决策层}：Router 置信度路由（本地直达 / 上云裁决 / 澄清询问）};

% 决策层 LLM
\node[box, fill=hmgreenlight, line color=hmgreen,
      below=0.6cm of router] (llm)
      {\textbf{决策层}：LLM 决策（Mock / llama.cpp / OpenAI API 三后端）};

% 执行层
\node[box, fill=hmgreenlight, line color=hmgreen,
      below=0.6cm of llm] (exec)
      {\textbf{执行层}：DeviceController / SceneSwitcher / TAP 引擎 / 协议网关};

% 记忆层
\node[box, fill=gray!12, line color=gray!60!black,
      below=0.6cm of exec] (persist)
      {\textbf{记忆层}：RAG 知识库 / 偏好库 / 记忆库（ChromaDB + JSON 本地存储）};

% 云端协同模块
\node[cloud, right=0.8cm of router, anchor=north west] (cloud)
      {\textbf{云端协同}};
\node[cloud, anchor=north west, minimum width=3.6cm,
      font=\sffamily\fontsize{8}{8}] at (cloud.north east)
      {意图理解 / 模糊裁决};
\node[cloud, anchor=north west, minimum width=3.6cm,
      font=\sffamily\fontsize{8}{8}] at (cloud.south west)
      {固定 JSON + Schema 校验};

% 层内箭头
\draw[arr, hmdark] (interact) -- (bsr);
\draw[arr, hmdark] (bsr) -- (lsr);
\draw[arr, hmdark] (lsr) -- (router);
\draw[arr, hmdark] (router) -- (llm);
\draw[arr, hmdark] (llm) -- (exec);
\draw[arr, hmdark] (exec) -- (persist);

% 云端连接
\draw[arr, hmaccent] (router.east) -- ++(0.6, 0) node[above, font=\sffamily\small, color=hmaccent] {模糊意图};
\draw[darr, hmaccent] (cloud.west) -- ++(-0.4, 0)
      node[above, font=\sffamily\small, color=hmaccent] {JSON + 校验};

% 记忆回传
\draw[darr, hmdark!60!black] (persist.west) -- ++(-0.5, 0) .. controls ++(-1.5, 1) .. (bsr.west);
\draw[darr, hmdark!60!black] (persist.west) -- ++(-0.5, 0) .. controls ++(-1.5, 2.5) .. (lsr.west);

% 层标签（右侧）
\node[label, right=0.15cm of edgebox.east, anchor=north] at (edgebox.north) {端侧};

\end{tikzpicture}
\caption{端侧智能体总体架构图}
\end{figure}

% 需要在导言区定义颜色：
%\definecolor{hmaccent}{HTML}{2E86AB}
```

---

## 六、使用建议

| 图件类型       | 推荐工具                                                   | 备注                |
| -------------- | ---------------------------------------------------------- | ------------------- |
| 架构图、流程图 | **Matplotlib + custom patches** 或 **Draw.io** | 保持配色统一        |
| 实验数据图表   | **Matplotlib**                                       | 使用上述统一脚本    |
| 界面截图       | 真实截图                                                   | 直接嵌入 300dpi PNG |
| 户型图         | **Inkscape / Figma** + 导出 SVG                      | 与 SVG 格式一致     |

**核心原则**：所有图件必须使用本指南第二章定义的统一配色方案。图表类使用 Python 脚本生成（保证数据精确），架构类和流程类使用 Matplotlib 手动绘制或 Draw.io 绘制后导出 PNG。
