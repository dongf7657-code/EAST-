# EastAI Studio — 项目迁移说明（完整版）

> **生成时间**：2026-04-21 09:28  
> **目的**：让新账号的 AI 快速对齐项目状态和开发环境，无缝衔接后续开发  
> **开发者**：方东升（深圳，ComfyUI 节点开发者）

---

## 一、项目概况

| 项目 | 说明 |
|------|------|
| **项目名** | EastAI Studio（EastAIstudio） |
| **类型** | PySide6 桌面 AI 绘图与无限画布标记应用 |
| **核心功能** | 通过 kie.ai API 进行 AI 换装/人像合成，支持无限画布浏览和批量工作流 |
| **技术栈** | Python 3.10+/3.14, PySide6, httpx |
| **架构** | 事件总线三层架构：UI层 → 适配层 → 驱动层 |
| **构建工具** | PyInstaller（--onefile），spec 文件 `EastAIstudio.spec` |
| **调试模式** | console=True（spec 中配置，保持控制台窗口打开查看日志） |

---

## 二、代码目录结构

### 源码目录

```
E:\腾讯小龙虾输出代码文件夹\gmin\
```

### 文件清单

| 文件 | 行数(约) | 职责 |
|------|---------|------|
| `main.py` | ~50 | 入口文件。QApplication 初始化，环境变量设置（QSG_RHI_BACKEND=d3d11），创建主窗口 |
| `config_manager.py` | ~100 | 配置管理。JSON 文件持久化存储，支持热重载，读写 API Key 等配置 |
| `infinite_canvas.py` | ~800 | **无限画布核心**。包含 ImageItem、VideoItem、CompareItem、GeneratingItem、QDrag 拖拽导入、Ctrl+滚轮缩放选中项（0.1~3.0）、QOpenGLWidget 视口 |
| `main_window.py` | ~1200 | **主窗口 UI**。左下角设置按钮、导入按钮、工作流按钮、右键菜单（含"加入工作流"）、工作流工具栏管理、结果输出到画布 |
| `kie_ai_driver.py` | ~150 | kie.ai API 驱动。创建任务（POST）、轮询状态（GET）、获取结果、httpx 异步请求 |
| `workflow_panel.py` | ~1600 | **批量工作流面板（当前重点开发组件，v9.2）**。详见第三章 |
| `editing_panel.py` | ~300 | 编辑面板。浮动卡片 UI，用于图片编辑操作 |
| `marker_tool.py` | ~200 | 画布标记工具。支持在图片上绘制标记区域 |
| `settings_dialog.py` | ~100 | 设置对话框。模态弹窗，左下角入口，配置 API Key 等 |
| `undo_manager.py` | ~80 | 撤销管理器。支持操作撤销/重做 |
| `requirements.txt` | ~15 | Python 依赖清单 |
| `EastAIstudio.spec` | ~80 | PyInstaller 打包配置（console=True） |
| `build.bat` | ~5 | 一键打包脚本 |

### EXE 输出

```
E:\腾讯小龙虾输出代码文件夹\gmin\dist\EastAIstudio\EastAIstudio.exe
```

### 打包命令

```bash
cd E:\腾讯小龙虾输出代码文件夹\gmin
python -m PyInstaller EastAIstudio.spec --noconfirm
```

---

## 三、批量工作流组件（workflow_panel.py）— 开发背景与完整架构

### 3.1 开发背景

EastAI Studio 核心功能是通过 kie.ai API 进行 AI 换装/人像合成。用户需要批量上传模特照片和服装图片，一次性生成多张换装效果。

批量工作流面板允许用户：
1. 选择模板（如"模特+服装"、"模特+上衣+裤子"等）
2. 批量上传参考图片到卡槽
3. 一键执行所有任务（多线程并发调用 API）
4. 结果自动输出到画布

### 3.2 演进历史

2026-04-20~21 两天内经历了 v2 → v9.2 共 **12 次重大迭代**：

| 版本 | 时间 | 关键变更 |
|------|------|----------|
| v2 | 04-20 | 从零构建，QWidget 面板 + QDialog 弹窗 + 拖拽导入 |
| v3 | 04-20 | OverflowError 修复（id(card) 超范围改自增 card_id）、多图拖拽修复、dropEvent 修复 |
| v4 | 04-20 | 画布内 QDrag 拖拽、占位框按比例、面板自由拖动 |
| v5 | 04-21 04:04 | 改为 QDialog 非模态弹窗，results_ready 信号传回 main_window |
| v6 | 04-21 04:29 | **架构重构**：配置条(QWidget) + 画布内 QGraphicsObject 卡片 + WorkflowEngine |
| v6.1~6.7 | 04-21 | UI 优化、2K/4K 尺寸、原图无损、等比缩放、联动自适应、磁吸对齐 |
| v7.0 | 04-21 07:30 | 弹窗绑定 + 多选上传 + 按列分配 |
| v8.0 | 04-21 07:50 | 融合一体，工具栏绘制在卡片内，_PopupCombo 下拉框 |
| v9.0 | 04-21 08:10 | **独立配置条** WorkflowToolBar(QWidget) + 纯 CanvasTaskCard，原生 QComboBox |
| v9.1 | 04-21 08:30 | 面板固定 1920px 居中 + 拖拽手柄 |
| **v9.2** | 04-21 08:50 | **场景内架构**：WorkflowToolBar → QGraphicsProxyWidget 放入场景 |
| v9.2 补丁 | 04-21 09:01 | 工具栏宽度适配 + drawPixmap 兼容修复 + 信号循环防护 |

### 3.3 当前架构（v9.2 完整详解）

```
QGraphicsScene
│
├── WorkflowToolBar (QWidget, 通过 QGraphicsProxyWidget 包装, ZValue=1000 置顶)
│   ├── ⠿ 拖拽手柄 → drag_moved(dx, dy) 信号 → engine._on_toolbar_drag()
│   ├── 模板 QComboBox（4个内置模板）
│   ├── 比例 QComboBox（auto/1:1/2:3/3:2/3:4/4:3/4:5/5:4/9:16/16:9/21:9）
│   ├── 画质 QComboBox（2K/4K）
│   ├── 模型 QComboBox（nano-banana-pro 等 4 个内置，可编辑输入）
│   ├── [+ 添加任务] QPushButton
│   ├── [执行全部] QPushButton（运行时禁用）
│   ├── 提示词 QLineEdit
│   └── [✕ 关闭] QPushButton
│
└── CanvasTaskCard × N (QGraphicsObject, ItemIsSelectable | ItemIsMovable)
    ├── 名称行：显示 "任务 #N · 状态"
    ├── ✕ 删除按钮（红色，点击删除卡片）
    ├── 卡槽行：N 个图片卡槽（宽度 SLOT_WIDTH=1920px）
    │   ├── 空卡槽：虚线边框 + 标签（"模特"/"服装"等）
    │   └── 已填卡槽：1:1 原图渲染，大于 1920px 等比压缩居中
    ├── 生成占位框：扫描渐变动画（执行中显示）
    ├── 结果图：生成完成后显示在占位框位置
    └── 提示词区域：点击弹出编辑弹窗

WorkflowEngine (QObject, 信号中继)
├── set_toolbar(toolbar) → 创建 QGraphicsProxyWidget 包装
├── show_toolbar_in_scene(scene) → 居中偏上定位
├── remove_toolbar_from_scene() → 从场景移除
├── create_task_card(scene) → 卡片创建 + 工具栏宽度同步 + X 对齐
├── _on_toolbar_drag(dx, dy) → 移动 proxy
├── _on_card_geometry_changed(card) → 自动重排（防重入）
├── _reposition_all_cards() → 垂直排列 + 工具栏 X 对齐
├── execute_all() → threading.Thread 并发执行
├── handle_dropped_images(paths) → 按列向下分配
├── clear_all() → 彻底清理 proxy + 任务 + 状态
├── results_ready = Signal(list) → main_window 放入画布
└── workflow_closed = Signal() → 所有任务清空时通知
```

### 3.4 全部功能清单（当前已实现）

| # | 功能 | 实现状态 | 技术细节 |
|---|------|---------|---------|
| 1 | 模板选择 | ✅ | 4 个内置：模特+服装、模特+上衣+裤子、模特+服装+动作、面部+背景+服装 |
| 2 | 自定义比例 | ✅ | auto/1:1~21:9 共 11 种，ratio_to_height() 函数计算卡槽高度 |
| 3 | 画质选择 | ✅ | 2K/4K |
| 4 | 模型选择 | ✅ | 4 个内置（nano-banana-pro/2, seedream-4.5/5.0-lite），QComboBox 可编辑 |
| 5 | 卡槽点击上传 | ✅ | QFileDialog.getOpenFileNames 多选 |
| 6 | 按列向下分配多图 | ✅ | extra_images_ready(card, slot_index, extra_paths) 信号 |
| 7 | 卡片内拖拽导入图片 | ✅ | QDrag + MIME data |
| 8 | 画布右键"加入工作流" | ✅ | main_window 右键菜单 |
| 9 | 工具栏拖拽移动 | ✅ | ⠿ 手柄 → drag_moved 信号 → _on_toolbar_drag |
| 10 | 工具栏 + 卡片整体缩放 | ✅ | 画布滚轮缩放选中项 |
| 11 | 框选多卡片 | ✅ | ItemIsSelectable，拖拽框选 |
| 12 | Ctrl+滚轮缩放选中组 | ✅ | InfiniteCanvas.wheelEvent 拦截 Ctrl，0.1~3.0 |
| 13 | 多任务并发执行 | ✅ | threading.Thread + _SignalRelay 跨线程通信 |
| 14 | 生成扫描动画 | ✅ | QTimer + QLinearGradient 滑动渐变 |
| 15 | 结果自动输出画布 | ✅ | results_ready 信号 → add_files_in_row |
| 16 | 工具栏宽度自动适配 | ✅ | sync_width(card_width) 方法 |
| 17 | 工具栏 X 坐标对齐 | ✅ | _reposition_all_cards 末尾同步 |
| 18 | 信号循环防护 | ✅ | _repositioning 标志 + try/finally |
| 19 | drawPixmap 兼容保护 | ✅ | int 参数重载 + isinstance 检查 |
| 20 | 图片无损显示 | ✅ | QPixmap 原图加载，大于 2K 等比压缩 |
| 21 | 提示词编辑 | ✅ | 点击提示词区域弹出编辑弹窗 |
| 22 | 卡片自动重排 | ✅ | _reposition_all_cards 垂直排列防重叠 |
| 23 | 空卡槽联动同步 | ✅ | slot_sizes_changed 信号广播 |

### 3.5 关键常量

```python
SLOT_WIDTH = 1920          # 所有卡槽宽度锁定 2K
DEFAULT_SLOT_H = 1080      # 默认卡槽高度
BASE_SLOT_H = 200          # UI 缩放基准高度
BASE_NAME_H = 28           # 名称行基准高度
BASE_PROMPT_H = 34         # 提示词区域基准高度
BASE_PADDING = 10          # 卡片内边距
BASE_GAP = 12              # 卡槽间距
BASE_CARD_GAP = 16         # 卡片间距
PANEL_WIDTH = 1920         # 工具栏初始宽度（会根据卡片宽度动态调整）
```

### 3.6 内置模板定义

```python
TEMPLATES = [
    {"id": "model_clothing",      "name": "模特+服装",        "slots": 2, "slot_labels": ["模特", "服装"]},
    {"id": "model_top_bottom",    "name": "模特+上衣+裤子",   "slots": 3, "slot_labels": ["模特", "上衣", "裤子"]},
    {"id": "model_clothing_pose", "name": "模特+服装+动作",   "slots": 3, "slot_labels": ["模特", "服装", "动作参考"]},
    {"id": "face_bg_clothing",    "name": "面部+背景+服装",   "slots": 3, "slot_labels": ["面部", "背景", "服装"]},
]
```

### 3.7 UI 规范

- **风格**：极简黑白灰，Apple 设计语言
- **配色**：背景 rgba(248,248,250,240)，文字 #333/#888，仅删除按钮用红色
- **功能标注**：加粗文字，无彩色图标
- **图片显示**：QPixmap 原图加载，大于 2K 等比压缩居中，白色填充空白
- **圆角**：工具栏 8px，按钮 5px，下拉框 5px/6px
- **字体**：11px，加粗 600
- **语言**：全中文界面

---

## 四、kie.ai API 接口文档

### 4.1 创建任务

```
POST /api/v1/jobs/createTask
Content-Type: application/json

请求体：
{
    "model": "nano-banana-pro",
    "prompt": "这是一项服装换装任务...",
    "image_urls": ["https://...", "https://..."],
    // 其他参数根据模型而定
}
```

### 4.2 查询任务状态

```
GET /api/v1/jobs/recordInfo?taskId=xxx

响应：
{
    "data": {
        "state": "waiting" | "queuing" | "generating" | "success" | "fail",
        "resultJson": "{\"resultUrls\":[\"https://...\"]}"  // 仅 success 时有
    }
}
```

### 4.3 状态流转

```
waiting → queuing → generating → success
                              → fail
```

### 4.4 结果解析

```python
import json
result = json.loads(data["resultJson"])
urls = result["resultUrls"]  # 生成图片 URL 列表
```

---

## 五、视频播放架构（infinite_canvas.py）

| 项目 | 说明 |
|------|------|
| 方案 | QVideoSink（不用 QVideoWidget+QGraphicsProxyWidget，后者在 QGraphicsView 中黑屏） |
| 帧回调 | `QVideoSink.videoFrameChanged` → `_on_video_frame()` → QImage |
| 渲染 | `paint()` 中 `painter.drawImage()` 手动绘制 |
| 刷新率 | QTimer 33ms（约 30fps） |
| 环境变量 | `QSG_RHI_BACKEND=d3d11`（main.py 中 QApplication 创建前设置） |
| 视口 | InfiniteCanvas 用 QOpenGLWidget |
| 交互 | 双击播放/暂停，右键菜单播放选项 |
| 缩略图 | 优先 ffmpeg，回退 cv2，_ThumbnailBridge 信号桥接跨线程 |
| ffmpeg 路径 | `C:\ffmpeg\ffmpeg-8.1-essentials_build\bin\` |
| 打包 | PyInstaller 需收集 PySide6 multimedia 插件（spec 中 collect_data_files） |

---

## 六、待完成任务

### 🔴 高优先级

1. **drawPixmap 兼容性充分测试** — v9.2 补丁已改用 `drawPixmap(int, int, int, int, QPixmap)` 避免 QRectF 重载问题，需在多分辨率图片下充分测试
2. **工作流面板整体绑定稳定性** — 工具栏 QGraphicsProxyWidget + 卡片的 X 对齐在缩放/拖拽后是否稳定
3. **多选图片按列分配可靠性** — `_reposition_suspended` 暂停期间是否所有边界情况都覆盖

### 🟡 中优先级

4. **视频播放稳定性** — QVideoSink 方案在某些 GPU/驱动下可能黑屏，需更多环境测试
5. **PyInstaller 打包体积优化** — 当前 EXE 约 7MB，可考虑排除未用模块
6. **错误处理增强** — API 调用失败时的用户友好提示（目前只有控制台日志）
7. **工具栏与卡片缩放同步** — 当前缩放只影响卡片，工具栏 proxy 是否跟随缩放

### 🟢 低优先级

8. **单元测试覆盖** — 核心逻辑（图片分配、并发执行、重排算法）缺少自动化测试
9. **多语言支持** — 当前硬编码中文，如需国际化需重构字符串
10. **撤销/重做** — 工作流操作目前不支持撤销

---

## 七、开发者偏好

| 偏好 | 说明 |
|------|------|
| 界面语言 | 中文（代码注释可用英文，UI 文案必须中文） |
| 调试模式 | 控制台保持打开（spec console=True），方便看 print 日志和报错 |
| 开发节奏 | **先修 Bug 后加功能**，不跳过已知问题 |
| 版本管理 | 频繁版本回退到稳定状态 |
| 代码风格 | 直接务实，厌恶冗余，不要解释过多 |
| 沟通方式 | 结构化编号需求列表，严格逐条实现，不允许遗漏任何需求点 |
| 交付要求 | 每次修改完打包 EXE 验证 |
| 文件位置 | 代码文件放 `E:\腾讯小龙虾输出代码文件夹\` 目录 |

---

## 八、已安装技能插件清单

### 8.1 用户级技能（~/.workbuddy/skills/）

| # | 技能名 | 安装路径 | 功能说明 |
|---|--------|---------|---------|
| 1 | **claude-mem** | `~/.workbuddy/skills/claude-mem/` | 跨会话记忆系统，包含 timeline、ragtime、observations 子技能。用于记住项目决策、开发历史、踩坑经验 |
| 2 | **Code** | `~/.workbuddy/skills/Code/` | 编码工作流，规划→实现→验证→测试全流程覆盖 |
| 3 | **gstack** | `~/.workbuddy/skills/gstack/` | 无头浏览器 QA 测试，支持导航、交互、截图、Bug 证据采集 |
| 4 | **superpowers** | `~/.workbuddy/skills/superpowers/` | Spec-first TDD 子代理驱动开发工作流 |

### 8.2 插件市场

| # | 市场名 | 路径 | 说明 |
|---|--------|------|------|
| 1 | **codebuddy-plugins-official** | `~/.workbuddy/plugins/marketplaces/codebuddy-plugins-official/` | 官方插件市场，58 个插件 |
| 2 | **cb_teams_marketplace** | `~/.workbuddy/plugins/marketplaces/cb_teams_marketplace/` | 腾讯团队市场，34 个插件 |

### 8.3 常用插件技能

| 分类 | 插件名 | 说明 |
|------|--------|------|
| **Office 文档** | docx, pdf, pptx, xlsx | Word/PDF/PPT/Excel 读写 |
| **浏览器** | playwright-cli, gstack | 浏览器自动化测试 |
| **云开发** | cloudbase | CloudBase 全栈开发（云函数/数据库/存储/AI） |
| **金融数据** | finance-data, neodata-financial-search | A股/港股/美股/基金/宏观数据检索 |
| **开发流程** | make-plan, do, agent-team-agile-workflow | 计划/执行/敏捷团队协作 |
| **技能管理** | find-skills, skill-creator, skills-security-check | 技能查找/创建/安全审计 |

### 8.4 安装方式

在新账号中，通过 `find-skills` 技能搜索并安装：

```
请使用 find-skills 查找并安装以下用户级技能：
1. claude-mem — 跨会话记忆系统
2. Code — 编码工作流
3. gstack — 无头浏览器 QA 测试
4. superpowers — TDD 子代理驱动开发
```

---

## 九、踩坑经验（重要！必读）

以下是开发过程中实际遇到并解决的技术问题，新 AI 接手时务必注意：

### 9.1 PySide6 / Python 3.14 相关

| # | 问题 | 解决方案 |
|---|------|---------|
| 1 | `drawPixmap(QRectF, QPixmap)` 在 PySide6 中报类型错误 | 统一用 `drawPixmap(int_x, int_y, int_w, int_h, pixmap)` 的 int 参数重载 |
| 2 | `QPixmap.scaled()` 在某些 PySide6 版本可能返回 `QImage` 而非 `QPixmap` | 加 `isinstance(scaled, QPixmap)` 检查，非 QPixmap 时转 `QPixmap.fromImage()` |
| 3 | `QGraphicsObject.parent()` 返回 `QGraphicsItem` 不是 `QWidget` | 获取顶层窗口用 `QApplication.activeWindow()`，不要用 `parent()` |
| 4 | `QPointF` 在 `QtCore` 不在 `QtGui`（某些 PySide6 版本） | 导入时确认来源：`from PySide6.QtCore import QPointF` |

### 9.2 信号与事件

| # | 问题 | 解决方案 |
|---|------|---------|
| 5 | `geometry_changed` → `_reposition_all_cards` → `setPos` → `geometry_changed` 信号死循环 | 加 `_repositioning` 标志 + try/finally 确保重置 |
| 6 | `OverflowError: argument too large for C long` — `id(card)` 返回 64 位指针超出 Qt Signal int 范围 | 改用自增 `card_id` 代替 `id(card)` |
| 7 | `WorkflowConfig` 必须继承 `QObject` | `Signal()` 只能用 在 QObject 子类上，否则 `changed.connect()` 静默失败 |
| 8 | QGraphicsItem 无 `dragMoveEvent` 则 `dropEvent` 不触发 | Qt 规则：必须重写 `dragMoveEvent` 和 `dragLeaveEvent` |

### 9.3 画布与场景

| # | 问题 | 解决方案 |
|---|------|---------|
| 9 | `InfiniteCanvas.self.scene` 覆盖了 `QGraphicsView.scene()` 方法 | `self.scene` 是实例属性不是方法，用 `self.canvas.scene`（无括号） |
| 10 | QVideoWidget+QGraphicsProxyWidget 在 QGraphicsView 中黑屏 | 改用 QVideoSink 方案，手动 `painter.drawImage()` 绘制每帧 |
| 11 | QPixmap 在非主线程创建可能无效 | 确保 QPixmap 操作在主线程，或用信号桥接 |

### 9.4 UI 与布局

| # | 问题 | 解决方案 |
|---|------|---------|
| 12 | 面板拖拽拦截子控件点击（按钮/下拉框收不到事件） | 用 `eventFilter` 只拦截标题区域拖拽，不全局拦截 `mousePressEvent` |
| 13 | 工具栏固定宽度与卡片宽度不一致导致视觉错乱 | 工具栏宽度动态跟随卡片宽度（`sync_width` 方法） |
| 14 | 提示词点击事件穿透到 ItemIsMovable 拖拽 | `mousePressEvent` 中提示词区域优先 `return + accept()` |
| 15 | `QPointF.toPoint()` 兼容性问题 | 改用 `QPoint(int(x), int(y))` 构造 |

---

## 十、新账号 AI 自动衔接提示词

> **将以下提示词完整粘贴到新账号 AI 的首次对话中，即可快速对齐项目状态：**

---

```
═══════════════════════════════════════════════════
  EastAI Studio — 新会话项目衔接提示词
  直接粘贴此内容到新 AI 对话即可
═══════════════════════════════════════════════════

## 你的角色

你是 EastAI Studio 的开发助手。这是一个 PySide6 桌面 AI 绘图应用，核心功能是通过 kie.ai API 进行 AI 换装/人像合成，核心组件是无限画布 + 批量工作流面板。

开发者是方东升（深圳，ComfyUI 节点开发者），偏好直接务实的沟通风格，不要废话。

## 代码位置

- 所有源码在：E:\腾讯小龙虾输出代码文件夹\gmin\
- 核心文件：
  - main.py（入口）
  - main_window.py（主窗口 UI，~1200 行）
  - infinite_canvas.py（无限画布，~800 行）
  - workflow_panel.py（批量工作流面板，~1600 行，当前重点开发组件）
  - kie_ai_driver.py（kie.ai API 驱动）
  - config_manager.py（配置管理）
  - editing_panel.py（编辑面板）
  - marker_tool.py（标记工具）
  - settings_dialog.py（设置对话框）
  - undo_manager.py（撤销管理器）
- EXE 输出：E:\腾讯小龙虾输出代码文件夹\gmin\dist\EastAIstudio\EastAIstudio.exe
- 打包命令：cd E:\腾讯小龙虾输出代码文件夹\gmin && python -m PyInstaller EastAIstudio.spec --noconfirm

## 当前架构

事件总线三层架构：UI层 → 适配层 → 驱动层

批量工作流面板（workflow_panel.py）v9.2 架构：
- WorkflowToolBar(QWidget) 通过 QGraphicsProxyWidget 放入 QGraphicsScene，与 CanvasTaskCard 同坐标系
- WorkflowEngine(QObject) 管理 proxy + 卡片创建/删除/并发执行
- 4 个内置模板（模特+服装、模特+上衣+裤子、模特+服装+动作、面部+背景+服装）
- SLOT_WIDTH=1920px，所有卡槽宽度锁定 2K
- 工具栏宽度通过 sync_width(card_width) 自动适配卡片宽度
- 工具栏 X 坐标与卡片对齐（_reposition_all_cards 中同步）
- 支持框选多卡片、Ctrl+滚轮缩放选中组（0.1~3.0）
- _repositioning/_reposition_suspended 双标志防信号循环
- drawPixmap 使用 int 参数重载避免 PySide6 兼容问题
- threading.Thread 并发执行 + _SignalRelay 跨线程通信
- extra_images_ready(card, slot_index, extra_paths) 信号实现按列向下分配

## kie.ai API

- 创建任务：POST /api/v1/jobs/createTask
- 查询状态：GET /api/v1/jobs/recordInfo?taskId=xxx
- 状态：data.state (waiting → queuing → generating → success/fail)
- 结果：data.resultJson 中 JSON 字符串解析 resultUrls 数组
- 驱动文件：kie_ai_driver.py

## 工作规范（必须严格遵守）

1. 中文界面，中文交流，代码注释可用英文但 UI 文案必须中文
2. 极简黑白灰 UI 风格，仅删除按钮用红色，无彩色图标
3. 先修 Bug 后加功能，不跳过已知问题
4. 每次修改完打包 EXE 验证：python -m PyInstaller EastAIstudio.spec --noconfirm
5. 控制台保持打开（spec console=True）方便看日志
6. 用户给结构化编号需求时，严格逐条实现不遗漏
7. 代码文件放 E:\腾讯小龙虾输出代码文件夹\ 目录
8. 直接务实，不要解释过多

## 需要安装的技能

请依次使用 find-skills 查找并安装以下用户级技能：
1. claude-mem — 跨会话记忆系统（用于记住项目决策和开发历史）
2. Code — 编码工作流（规划→实现→验证→测试）
3. gstack — 无头浏览器 QA 测试
4. superpowers — TDD 子代理驱动开发

安装完成后，请阅读以下文件获取完整项目上下文：
- E:\腾讯小龙虾输出代码文件夹\gmin\workflow_panel.py（当前重点组件，~1600 行）
- E:\腾讯小龙虾输出代码文件夹\gmin\main_window.py（主窗口，~1200 行）
- 如果工作记忆文件存在：.workbuddy/memory/MEMORY.md 和最近的日志文件

## 踩坑经验（非常重要！）

1. InfiniteCanvas.self.scene 是实例属性（QGraphicsScene），不是方法——用 self.canvas.scene（无括号）
2. Python 3.14 + PySide6 中 drawPixmap 的 QRectF 重载可能报类型错误，统一用 drawPixmap(int_x, int_y, int_w, int_h, pixmap) 的 int 参数版本
3. QPixmap.scaled() 在某些 PySide6 版本可能返回 QImage，需要 isinstance 检查后再调用 drawPixmap
4. 信号连接可能导致循环（geometry_changed → _reposition_all_cards → setPos → geometry_changed），必须加 _repositioning 标志 + try/finally
5. QGraphicsObject 的 parent() 返回 QGraphicsItem 不是 QWidget，获取顶层窗口用 QApplication.activeWindow()
6. id(card) 返回 64 位指针可能超出 Qt Signal int 范围，用自增 card_id 代替
7. WorkflowConfig 必须继承 QObject（Signal 只能在 QObject 子类上用）
8. QGraphicsItem 无 dragMoveEvent 则 dropEvent 不触发，必须同时重写 dragMoveEvent 和 dragLeaveEvent
9. QPointF 在某些 PySide6 版本在 QtCore 不在 QtGui
10. 面板 mousePressEvent 全局 accept() 会拦截子控件点击，用 eventFilter 只拦截特定区域
11. QVideoWidget+QGraphicsProxyWidget 在 QGraphicsView 中黑屏，改用 QVideoSink + 手动 drawImage
12. view_pos.toPoint() 可能不兼容，用 QPoint(int(x), int(y)) 构造
13. 工具栏固定宽度与卡片宽度不一致会导致视觉错乱，用 sync_width 动态适配

## 当前待办（按优先级）

🔴 高优先级：
1. drawPixmap 兼容性充分测试（多分辨率图片）
2. 工作流面板整体绑定稳定性（缩放/拖拽后工具栏与卡片对齐）
3. 多选图片按列分配可靠性（_reposition_suspended 边界情况）

🟡 中优先级：
4. 视频播放稳定性（QVideoSink 在不同 GPU/驱动下测试）
5. PyInstaller 打包体积优化
6. API 调用失败的用户友好提示
7. 工具栏与卡片缩放同步

🟢 低优先级：
8. 单元测试覆盖
9. 多语言支持
10. 工作流操作撤销/重做

═══════════════════════════════════════════════════
```

---

## 十一、快速参考卡片

```
┌─────────────────────────────────────────────────────┐
│  EastAI Studio 快速参考                              │
├─────────────────────────────────────────────────────┤
│  代码: E:\腾讯小龙虾输出代码文件夹\gmin\             │
│  EXE:  E:\...\gmin\dist\EastAIstudio\EastAIstudio.exe│
│  打包: python -m PyInstaller EastAIstudio.spec --noconfirm │
│  调试: console=True（保持控制台打开）                  │
│  版本: workflow_panel.py v9.2                       │
│  API:  kie.ai POST createTask / GET recordInfo      │
│  架构: UI层 → 适配层 → 驱动层                        │
│  技能: claude-mem, Code, gstack, superpowers        │
└─────────────────────────────────────────────────────┘
```

---

*文档结束。如需更新，请修改此文件并同步更新 .workbuddy/memory/MEMORY.md。*
