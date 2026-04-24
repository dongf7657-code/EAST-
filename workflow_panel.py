"""
工作流面板组件 v9.8 — 浮动工具栏 + 任务卡片
═════════════════════════════════════════════════════════
核心设计（v9.8）：
1. WorkflowToolBar(QWidget)：固定尺寸浮动在 canvas 顶部居中
   - 不使用 QGraphicsProxyWidget，完全作为普通控件
   - 尺寸固定 860x56，不可拖动
2. CanvasTaskCard(QGraphicsObject)：纯任务卡片，在画布场景中
3. WorkflowEngine：管理任务卡片，不再关联工具栏位置
"""
import os
import sys
import threading
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QTextEdit, QFileDialog,
    QMessageBox, QSizePolicy, QMenu, QLineEdit,
    QApplication, QDialog, QDialogButtonBox,
    QListWidget, QListWidgetItem,
)
from PySide6.QtCore import (
    Qt, Signal, QObject, QTimer, QPoint, QPointF, QRect, QRectF
)
from PySide6.QtGui import (
    QPixmap, QPainter, QColor, QLinearGradient, QPen, QBrush, QFont,
    QCursor,
)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject

# ════════════════════════════════════════════════════
#  模板定义
# ════════════════════════════════════════════════════
TEMPLATES = [
    {
        "id": "model_clothing",
        "name": "模特+服装",
        "slots": 2,
        "slot_labels": ["模特", "服装"],
        "default_prompt": "这是一项服装换装任务。第一张图是模特，第二张图是服装。请将第二张图中的服装穿在第一张图的模特身上，保持模特的面部特征、姿势和背景不变，生成一张逼真的换装照片",
    },
    {
        "id": "model_top_bottom",
        "name": "模特+上衣+裤子",
        "slots": 3,
        "slot_labels": ["模特", "上衣", "裤子"],
        "default_prompt": "这是一项服装换装任务。第一张图是模特，第二张图是上衣，第三张图是裤子。请将第二张图的上衣和第三张图的裤子穿在第一张图的模特身上，保持模特的面部特征、姿势和背景不变，生成一张逼真的换装照片",
    },
    {
        "id": "model_clothing_pose",
        "name": "模特+服装+动作",
        "slots": 3,
        "slot_labels": ["模特", "服装", "动作参考"],
        "default_prompt": "这是一项服装换装任务。第一张图是模特，第二张图是服装，第三张图是动作参考。请将第二张图中的服装穿在第一张图的模特身上，参考第三张图的动作姿态，保持模特面部特征和背景不变，生成一张逼真的换装照片",
    },
    {
        "id": "face_bg_clothing",
        "name": "面部+背景+服装",
        "slots": 3,
        "slot_labels": ["面部", "背景", "服装"],
        "default_prompt": "这是一项人像合成换装任务。第一张图是面部，第二张图是背景，第三张图是服装。请将面部与身体自然融合，穿上第三张图的服装，使用第二张图的背景，保持五官特征不变，生成一张逼真的人像照片",
    },
    # ════════════════════════════════════════════════════
    #  模特多动作模板（v10.0 新增）
    # ════════════════════════════════════════════════════
    {
        "id": "model_multi_pose",
        "name": "模特多动作",
        "type": "multi_pose",   # 特殊类型，使用独立布局
        "slots": 1,             # 仅1个参考图槽位
        "slot_labels": ["参考图"],
        "default_prompt": "",
    },
]

# ════════════════════════════════════════════════════
#  统一模型注册表（图片编辑 + 工作流共用）
# ════════════════════════════════════════════════════
# 平台绑定规则（按用户提供）：
#   KIE 平台：Seedream 4.5/5.0 系列、Nano Banana 系列、GPT Image 系列
#   Grsai 平台：Nano Banana 系列、GPT Image 系列
PLATFORM_KIE = "kie"
PLATFORM_GRS = "grsai"

ALL_MODELS = [
    # Nano Banana 系列 — 两个平台都支持
    {"id": "nano-banana-pro", "name": "NanoBanana Pro",   "platforms": [PLATFORM_KIE, PLATFORM_GRS]},
    {"id": "nano-banana-2",   "name": "NanoBanana 2",    "platforms": [PLATFORM_KIE, PLATFORM_GRS]},
    # Seedream 系列 — 仅 KIE 平台
    {"id": "seedream-4.5",     "name": "Seedream 4.5",    "platforms": [PLATFORM_KIE]},
    {"id": "seedream-5.0-lite","name": "Seedream 5.0",    "platforms": [PLATFORM_KIE]},
    # GPT Image 系列 — 两个平台都支持
    {"id": "gpt-image-2", "name": "GPT Image 2", "platforms": [PLATFORM_KIE, PLATFORM_GRS]},
]

# 模型 ID → 显示名（用于 ComboBox）
MODEL_ID_TO_NAME = {m["id"]: m["name"] for m in ALL_MODELS}
MODEL_NAME_TO_ID = {m["name"]: m["id"] for m in ALL_MODELS}
# 兼容旧版：id 字符串列表
MODEL_IDS = [m["id"] for m in ALL_MODELS]
# 工具栏 ComboBox 使用的模型 ID 列表（含 gpt-image-2，与主界面同步）
BUILTIN_MODELS = MODEL_IDS

def get_models_for_platform(platform):
    """返回指定平台可用的模型列表（按注册顺序）"""
    return [m for m in ALL_MODELS if platform in m["platforms"]]

def get_model_platforms(model_id):
    """返回指定模型支持的平台列表"""
    for m in ALL_MODELS:
        if m["id"] == model_id:
            return m["platforms"]
    return []

IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff', '.tif'}

# ════════════════════════════════════════════════════
#  全局尺寸常量（修复 v9.x BUG: 工具栏铺满屏幕）
#
#  原来 SLOT_WIDTH=1920 直接导致卡片总宽度 ~5800px
#  原来 PANEL_WIDTH=1920 导致工具栏铺满屏幕
#
#  新方案：
#  - SLOT_WIDTH=400：卡片槽的"逻辑基准宽度"（内部 2K 渲染）
#  - 工具栏宽度由 canvas 视口宽度动态决定
#  - 画布上显示时按比例缩放
# ════════════════════════════════════════════════════
SLOT_WIDTH = 400          # 卡片槽显示宽度（2K 逻辑：1920px）
DEFAULT_SLOT_H = 225      # 基准高度（400:225 ≈ 16:9）
BASE_SLOT_H = 100         # 最小槽高
BASE_NAME_H = 32
BASE_TOOLBAR_H = 52
BASE_PROMPT_H = 34
BASE_PADDING = 10
BASE_GAP = 12
BASE_CARD_GAP = 16

# 工具栏默认宽度（场景坐标系中的初始宽度，会动态更新）
DEFAULT_TOOLBAR_W = 960

# 比例 → 高度表（基于 SLOT_WIDTH 计算）
RATIO_TO_HEIGHT = {
    '1:1':  400,
    '2:3':  600,
    '3:2':  267,
    '3:4':  533,
    '4:3':  300,
    '4:5':  500,
    '5:4':  320,
    '9:16': 711,
    '16:9': 225,
    '21:9': 172,
}

RATIOS = ['auto', '1:1', '2:3', '3:2', '3:4', '4:3', '4:5', '5:4', '9:16', '16:9', '21:9']
RESOLUTIONS = ['2K', '4K']




def parse_ratio(ratio_str):
    if not ratio_str or ratio_str == 'auto' or ':' not in ratio_str:
        return None
    try:
        parts = ratio_str.split(':')
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None


def ratio_to_height(ratio_str):
    if ratio_str in RATIO_TO_HEIGHT:
        return RATIO_TO_HEIGHT[ratio_str]
    r = parse_ratio(ratio_str)
    if r:
        rw, rh = r
        return int(SLOT_WIDTH * rh / rw)
    return DEFAULT_SLOT_H


# ════════════════════════════════════════════════════
#  公共样式
# ════════════════════════════════════════════════════
STYLE_COMBO = """
    QComboBox {
        padding: 6px 10px;
        border: none;
        border-radius: 6px;
        background: rgba(0, 0, 0, 0.04);
        font-size: 14px;
        color: #333;
        font-weight: 700;
    }
    QComboBox:hover { background: rgba(0, 0, 0, 0.07); }
    QComboBox::drop-down { border: none; width: 20px; }
    QComboBox::down-arrow {
        image: none;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 4px solid #888;
    }
    QComboBox QAbstractItemView {
        background: rgba(255, 255, 255, 0.96);
        border: 0.5px solid rgba(0, 0, 0, 0.08);
        border-radius: 6px;
        padding: 4px;
        selection-background-color: rgba(0, 0, 0, 0.06);
        selection-color: #111;
        color: #333;
        outline: none;
        font-size: 14px;
    }
"""

STYLE_BTN_SMALL = """
    QPushButton {
        background: rgba(0, 0, 0, 0.05);
        color: #333;
        font-weight: 700;
        padding: 6px 16px;
        font-size: 14px;
        border-radius: 6px;
        border: none;
    }
    QPushButton:hover { background: rgba(0, 0, 0, 0.09); }
    QPushButton:pressed { background: rgba(0, 0, 0, 0.03); }
    QPushButton:disabled { color: #bbb; background: rgba(0,0,0,0.02); }
"""

STYLE_LINEEDIT = """
    QLineEdit {
        padding: 6px 10px;
        border: none;
        border-radius: 6px;
        background: rgba(0, 0, 0, 0.03);
        font-size: 14px;
        color: #333;
    }
    QLineEdit:focus { background: rgba(0, 0, 0, 0.05); }
    QLineEdit::placeholder { color: #bbb; }
"""


def _get_top_widget():
    app = QApplication.instance()
    if app:
        w = app.activeWindow()
        if w:
            return w
    return None


class _SignalRelay(QObject):
    card_status_signal = Signal(int, str, str)
    card_results_signal = Signal(int, str)
    card_error_signal = Signal(int, str)
    batch_done_signal = Signal(int, int, int, list)


# ════════════════════════════════════════════════════
#  WorkflowConfig — 纯数据容器（不再继承 QWidget）
# ════════════════════════════════════════════════════
class WorkflowConfig(QObject):
    """工作流配置数据容器（v8.0: 继承 QObject 以支持 Signal）。
    
    存储模板、比例、画质、模型、全局提示词等配置。
    配置 UI 直接画在 CanvasTaskCard（任务一）顶部。
    """
    
    changed = Signal()  # 配置变化时通知
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._template = TEMPLATES[0]
        self._ratio = 'auto'
        self._resolution = '2K'
        self._model = 'nano-banana-pro'
        self._global_prompt = ""
        self._driver = None
    
    def set_driver(self, driver):
        self._driver = driver
    
    @property
    def template(self):
        return self._template
    
    @property
    def ratio(self):
        return self._ratio
    
    @property
    def resolution(self):
        return self._resolution
    
    @property
    def model(self):
        return self._model
    
    @property
    def global_prompt(self):
        return self._global_prompt
    
    def set_template_by_index(self, index):
        if 0 <= index < len(TEMPLATES):
            self._template = TEMPLATES[index]
            self.changed.emit()
    
    def set_ratio(self, ratio):
        self._ratio = ratio
        self.changed.emit()
    
    def set_resolution(self, res):
        self._resolution = res
        self.changed.emit()
    
    def set_model(self, model):
        self._model = model.strip() or "nano-banana-pro"
        self.changed.emit()
    
    def set_global_prompt(self, prompt):
        self._global_prompt = prompt.strip()


# ════════════════════════════════════════════════════
#  WorkflowToolBar — 固定尺寸浮动工具栏
# ════════════════════════════════════════════════════
# ★ v9.8: 固定尺寸常量
TOOLBAR_W = 860
TOOLBAR_H = 56

class WorkflowToolBar(QWidget):
    """★ v9.8: 固定尺寸浮动工具栏，顶部居中，不影响画布。
    
    不使用 QGraphicsProxyWidget，作为普通 QWidget 浮动在 canvas 上。
    尺寸固定 860x56，位置由 main_window 控制在顶部居中。
    
    布局（左→右）：
    [模板▼] [比例▼] [画质▼] [模型▼] | [+添加任务] [执行全部] | 提示词输入框 | [✕关闭]
    """
    
    add_task_requested = Signal()
    execute_all_requested = Signal()
    close_requested = Signal()
    
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._config = config
        self._running = False
        
        # ★ v9.8: 固定尺寸，不允许缩放
        self.setFixedSize(TOOLBAR_W, TOOLBAR_H)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setStyleSheet("""
            WorkflowToolBar {
                background: rgba(248, 248, 250, 240);
                border: 0.5px solid rgba(0, 0, 0, 0.08);
                border-radius: 10px;
            }
        """)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 12, 8)
        layout.setSpacing(10)
        
        # 模板
        self.combo_template = QComboBox()
        self.combo_template.addItems([t['name'] for t in TEMPLATES])
        self.combo_template.setFixedWidth(130)
        self.combo_template.setStyleSheet(STYLE_COMBO)
        self.combo_template.currentIndexChanged.connect(self._on_template_changed)
        layout.addWidget(self.combo_template)
        
        # 比例
        self.combo_ratio = QComboBox()
        self.combo_ratio.addItems(RATIOS)
        self.combo_ratio.setCurrentText(config.ratio)
        self.combo_ratio.setFixedWidth(75)
        self.combo_ratio.setStyleSheet(STYLE_COMBO)
        self.combo_ratio.currentTextChanged.connect(config.set_ratio)
        layout.addWidget(self.combo_ratio)
        
        # 画质
        self.combo_resolution = QComboBox()
        self.combo_resolution.addItems(RESOLUTIONS)
        self.combo_resolution.setCurrentText(config.resolution)
        self.combo_resolution.setFixedWidth(60)
        self.combo_resolution.setStyleSheet(STYLE_COMBO)
        self.combo_resolution.currentTextChanged.connect(config.set_resolution)
        layout.addWidget(self.combo_resolution)
        
        # 模型
        self.combo_model = QComboBox()
        self.combo_model.addItems(BUILTIN_MODELS)
        self.combo_model.setCurrentText(config.model)
        self.combo_model.setEditable(True)
        self.combo_model.setFixedWidth(150)
        self.combo_model.setStyleSheet(STYLE_COMBO)
        self.combo_model.currentTextChanged.connect(config.set_model)
        layout.addWidget(self.combo_model)
        
        # 分隔
        sep = QLabel("|")
        sep.setStyleSheet("color: rgba(0,0,0,0.1); font-weight: 200; font-size: 18px;")
        sep.setFixedWidth(12)
        layout.addWidget(sep)
        
        # + 添加任务
        self.btn_add = QPushButton("+ 添加任务")
        self.btn_add.setStyleSheet(STYLE_BTN_SMALL)
        self.btn_add.clicked.connect(self._on_add_clicked)
        layout.addWidget(self.btn_add)
        
        # 执行全部
        self.btn_execute = QPushButton("执行全部")
        self.btn_execute.setStyleSheet("""
            QPushButton {
                background: rgba(0, 0, 0, 0.08);
                color: #111; font-weight: 700;
                padding: 6px 18px; font-size: 14px;
                border-radius: 6px; border: none;
            }
            QPushButton:hover { background: rgba(0, 0, 0, 0.12); }
            QPushButton:pressed { background: rgba(0, 0, 0, 0.04); }
            QPushButton:disabled { color: #bbb; background: rgba(0,0,0,0.03); }
        """)
        self.btn_execute.clicked.connect(self.execute_all_requested.emit)
        layout.addWidget(self.btn_execute)
        
        # 分隔
        sep2 = QLabel("|")
        sep2.setStyleSheet("color: rgba(0,0,0,0.1); font-weight: 200; font-size: 18px;")
        sep2.setFixedWidth(12)
        layout.addWidget(sep2)
        
        # 提示词
        self.line_prompt = QLineEdit()
        self.line_prompt.setPlaceholderText("全局提示词（可选）")
        self.line_prompt.setText(config.global_prompt)
        self.line_prompt.setStyleSheet(STYLE_LINEEDIT)
        self.line_prompt.editingFinished.connect(self._on_prompt_changed)
        layout.addWidget(self.line_prompt, 1)  # stretch
        
        # ✕ 关闭
        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(28, 28)
        self.btn_close.setStyleSheet("""
            QPushButton {
                background: transparent; border: none;
                color: #999; font-size: 14px; font-weight: 700;
                border-radius: 5px;
            }
            QPushButton:hover { background: rgba(220, 38, 38, 0.08); color: #c00; }
        """)
        self.btn_close.clicked.connect(self.close_requested.emit)
        layout.addWidget(self.btn_close)
    
    # ── 配置同步 ────────────────────────────────
    def _on_template_changed(self, index):
        if 0 <= index < len(TEMPLATES):
            self._config.set_template_by_index(index)
    
    def _on_add_clicked(self):
        print(f"[工作流工具栏] + 添加任务 被点击，即将发射信号")
        self.add_task_requested.emit()
        print(f"[工作流工具栏] 信号已发射")

    def _on_prompt_changed(self):
        self._config.set_global_prompt(self.line_prompt.text())
    def sync_from_config(self):
        """从 config 同步 UI 状态。"""
        self.combo_ratio.blockSignals(True)
        self.combo_ratio.setCurrentText(self._config.ratio)
        self.combo_ratio.blockSignals(False)
        
        self.combo_resolution.blockSignals(True)
        self.combo_resolution.setCurrentText(self._config.resolution)
        self.combo_resolution.blockSignals(False)
        
        self.combo_model.blockSignals(True)
        self.combo_model.setCurrentText(self._config.model)
        self.combo_model.blockSignals(False)
        
        self.line_prompt.blockSignals(True)
        self.line_prompt.setText(self._config.global_prompt)
        self.line_prompt.blockSignals(False)


# ════════════════════════════════════════════════════
#  CanvasTaskCard — 画布上的任务卡片
# ════════════════════════════════════════════════════
class CanvasTaskCard(QGraphicsObject):
    """在画布上渲染的任务卡片（纯任务，不含配置工具栏）。
    
    ┌─[任务 #1 · 状态]───────────────────────────────────────────────┐
    │  [卡槽1(1920)] [卡槽2(1920)] [结果框(1920)]                    │
    │  [点击编辑提示词...]                                            │
    └──────────────────────────────────────────────┘
    """
    
    delete_requested = Signal(object)
    card_clicked = Signal(object)
    slot_sizes_changed = Signal(object)
    geometry_changed = Signal(object)
    # ★ 按列分配信号 — (card, slot_index, extra_paths)
    extra_images_ready = Signal(object, int, list)
    # ★ v9.10: 槽位图片操作信号
    slot_move_up_requested = Signal(int)      # 上移: slot_index
    slot_move_down_requested = Signal(int)    # 下移: slot_index
    slot_image_delete_requested = Signal(int) # 删除单张图片: slot_index
    # ★ v10.0: 模特多动作模板信号
    multi_pose_generate_prompts = Signal(object)  # 请求生成提示词
    multi_pose_execute_requested = Signal(object) # 请求执行出图
    multi_pose_prompts_generated = Signal(object, list)  # 提示词已生成

    def __init__(self, group_index, template, card_id, ratio_str="auto",
                 initial_slot_h=None, config=None, parent=None):
        super().__init__(parent)
        self.group_index = group_index
        self.template = template
        self.card_id = card_id
        self._ratio_str = ratio_str
        self._config = config
        self._is_multi_pose = template.get("type") == "multi_pose"
        
        self.slots = []
        self.result_paths = []
        self.status = "idle"
        self._error_msg = ""
        
        self._scan_pos = 0.0
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(30)
        self._anim_timer.timeout.connect(self._advance_scan)
        
        self._result_pixmap = None
        self._custom_prompt = ""
        
        self._slot_h = initial_slot_h if initial_slot_h else ratio_to_height(ratio_str)
        
        # ★ v12: 模特多动作模板专属状态
        if self._is_multi_pose:
            self._pose_count = 1          # 默认1组（可调1-5）
            self._generated_poses = []    # ["动作1提示词", ...]
            self._pose_expert_prompt = "" # 动作提示词专家预设
            self._pose_extra_info = ""    # 产品卖点/补充需求
            self._selected_model = config.model if config else "nano-banana-pro"
            self._hover_state = ""        # 当前悬停区域
            self._result_pixmaps = []     # [QPixmap, ...] 已生成的成品图
            self._show_result_count = 0   # ★ v12: 当前显示的成品框数量（执行前为0）
            self._generating_prompts = False
            self._editing_model = False   # ★ v12: 是否在编辑模型名称
            self._editing_model_text = "" # ★ v12: 模型输入框内容
        else:
            self._pose_count = 0
            self._generated_poses = []
            self._pose_custom_prompt = ""
            self._pose_extra_info = ""
            self._selected_model = ""
            self._hover_state = ""
            self._result_pixmaps = []
            self._show_result_count = 0
            self._generating_prompts = False
            self._editing_model = False
            self._editing_model_text = ""
        
        for label in template["slot_labels"]:
            self.slots.append((None, None))
        
        self._scale_factor = 1.0
        self._last_prompt_click_scene_pos = None
        
        self._compute_geometry()
        
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setAcceptHoverEvents(True)
        self.setCacheMode(QGraphicsItem.NoCache)

    @property
    def scale_factor(self):
        return self._scale_factor

    def _compute_scale_factor(self):
        self._scale_factor = max(self._slot_h / BASE_SLOT_H, 1.0)

    def _s(self, base_value):
        return max(int(base_value * self._scale_factor), base_value)

    def _s_font_size(self, base_size):
        return max(int(base_size * self._scale_factor), base_size)

    # ════════════════════════════════════════════════════
    #  模特多动作模板几何计算（v12 重构：三列独立分割+滚轮控制）
    # ════════════════════════════════════════════════════
    # v12 新布局（严格三列独立分离）：
    # ┌─────────────────────────────────────────────────────────────────────┐
    # │  标题栏                                                                   │
    # ├──────────────┬──────────────────────────────────┬───────────────────┤
    # │              │                                  │                   │
    # │  第一列：      │  第二列：                         │  第三列：           │
    # │  参考图上传框  │  动作提示词专家预设                 │  成品展示区          │
    # │  (200×200)    │  产品卖点描述                       │  (执行后动态增加)    │
    # │              │  模型选择(可手动输入+滚轮)           │                   │
    # │              │  数量(滚轮调节)                     │                   │
    # │              │  [生成动作提示词]                    │                   │
    # │              │  ──────────────────               │                   │
    # │              │  动作提示词1/2/3...               │                   │
    # │              │  [▶ 执行生成]                      │                   │
    # └──────────────┴──────────────────────────────────┴───────────────────┘
    REF_W = 200           # 参考图宽度
    REF_H = 200           # 参考图高度
    CONTROL_H = 32        # 控制行高度
    INPUT_H = 38          # 输入框高度
    INPUT_GAP = 8         # 输入框间距
    PROMPT_LABEL_H = 14   # 提示词标签高度
    
    def _compute_multi_pose_geometry(self):
        """计算 multi_pose 模板的几何尺寸（v13 极简三列+统一14px字体）

        关键变更（v13）：
        - 三列严格独立分割，比例优化
        - 全文字体统一14px，标签11px
        - 数量支持滚轮+手动输入，无加减按钮
        - 模型支持手动输入+滚轮，无下拉
        - 成品框默认1个，执行后动态增加
        """
        self._compute_scale_factor()
        pad = self._s(10)
        gap = self._s(8)

        self._name_h = self._s(BASE_NAME_H)
        self._name_y = 0

        # ════════════════════════════════════════════════════
        # 第一列：参考图上传框（200×200，干净无控件）
        # ════════════════════════════════════════════════════
        self._col1_w = self._s(200)
        self._col1_h = self._s(200)
        self._col1_x = pad
        self._col1_y = self._name_h + pad

        # ════════════════════════════════════════════════════
        # 第二列：控制面板（竖向紧凑排列）
        # ════════════════════════════════════════════════════
        self._col2_x = self._col1_x + self._col1_w + gap
        self._col2_w = self._s(180)  # 紧凑宽度

        ctrl_x = self._col2_x

        # 控件尺寸（统一14px字体）
        CTRL_H = self._s(36)  # 统一高度
        INPUT_H = self._s(36)
        GAP = self._s(8)

        # 1. 动作提示词专家预设
        self._expert_x = ctrl_x
        self._expert_y = self._col1_y
        self._expert_w = self._col2_w
        self._expert_h = INPUT_H

        # 2. 产品卖点描述
        self._卖点_y = self._expert_y + self._expert_h + GAP
        self._卖点_x = ctrl_x
        self._卖点_w = self._col2_w
        self._卖点_h = INPUT_H

        # 3. 模型选择（手动输入+滚轮）
        self._model_y = self._卖点_y + self._卖点_h + GAP
        self._model_h = CTRL_H
        self._model_w = self._col2_w - self._s(70)  # 留空间给数量

        # 4. 数量（滚轮调节）
        self._count_x = ctrl_x + self._model_w + GAP
        self._count_y = self._model_y
        self._count_w = self._s(60)
        self._count_h = CTRL_H

        # 5. 生成动作提示词按钮
        self._gen_btn_y = self._model_y + self._model_h + GAP
        self._gen_btn_h = self._s(34)
        self._gen_btn_w = self._col2_w

        # 6. 动作提示词列表
        self._prompt_list_y = self._gen_btn_y + self._gen_btn_h + GAP
        self._pbox_h = self._s(32)
        self._pbox_w = self._col2_w
        n_poses = max(self._pose_count, 1)
        prompt_list_h = n_poses * self._pbox_h + (n_poses - 1) * GAP

        # 7. 执行生成按钮
        self._btn_area_y = self._prompt_list_y + prompt_list_h + GAP
        self._btn_area_h = self._s(38)
        self._btn_area_w = self._col2_w

        # 第二列总高度
        col2_total_h = self._btn_area_y + self._btn_area_h - self._col1_y

        # ════════════════════════════════════════════════════
        # 第三列：成品展示区（默认1个，执行后动态增加）
        # ════════════════════════════════════════════════════
        RESULT_SIZE = self._s(160)  # 成品框尺寸
        RESULT_GAP = self._s(8)

        self._col3_x = self._col2_x + self._col2_w + gap
        self._col3_y = self._col1_y

        # ★ v13: 默认1个成品框（执行后根据_show_result_count动态增加）
        n_results = max(self._show_result_count, 1) if self._show_result_count > 0 else 1
        self._result_box_w = RESULT_SIZE
        self._result_box_h = RESULT_SIZE
        self._result_gap = RESULT_GAP

        self._col3_w = n_results * RESULT_SIZE + (n_results - 1) * RESULT_GAP if n_results > 1 else RESULT_SIZE
        self._col3_h = self._col1_h

        # ════════════════════════════════════════════════════
        # 总尺寸计算
        # ════════════════════════════════════════════════════
        self._total_w = self._col3_x + self._col3_w + pad
        self._total_h = self._name_h + pad + max(self._col1_h, col2_total_h, self._col3_h) + pad

        # ════════════════════════════════════════════════════
        # 可点击区域定义
        # ════════════════════════════════════════════════════
        self._slot_rect = QRectF(self._col1_x, self._col1_y, self._col1_w, self._col1_h)
        self._expert_rect = QRectF(self._expert_x, self._expert_y, self._expert_w, self._expert_h)
        self._卖点_rect = QRectF(self._卖点_x, self._卖点_y, self._卖点_w, self._卖点_h)
        self._model_rect = QRectF(ctrl_x, self._model_y, self._model_w, self._model_h)
        self._count_rect = QRectF(self._count_x, self._count_y, self._count_w, self._count_h)
        self._gen_btn_rect = QRectF(ctrl_x, self._gen_btn_y, self._gen_btn_w, self._gen_btn_h)
        self._btn_rect = QRectF(ctrl_x, self._btn_area_y, self._btn_area_w, self._btn_area_h)

        del_btn_w = self._s(30)
        self._del_rect = QRectF(self._total_w - pad - del_btn_w, self._name_y, del_btn_w, self._name_h)

        # 动作提示词格子
        self._prompt_boxes = []
        for i in range(n_poses):
            px = self._col2_x
            py = self._prompt_list_y + i * (self._pbox_h + GAP)
            self._prompt_boxes.append(QRectF(px, py, self._pbox_w, self._pbox_h))

        # 成品图格子
        self._result_boxes = []
        actual_result_count = self._show_result_count if self._show_result_count > 0 else 1
        for i in range(actual_result_count):
            rx = self._col3_x + i * (RESULT_SIZE + RESULT_GAP)
            ry = self._col3_y
            self._result_boxes.append(QRectF(rx, ry, RESULT_SIZE, RESULT_SIZE))

    def _draw_pixmap_in_slot(self, painter, px, slot_rect):
        if not px or px.isNull():
            return
        # ★ v9.2: 类型保护，确保是 QPixmap
        if not isinstance(px, QPixmap):
            try:
                px = QPixmap.fromImage(px)
            except Exception:
                return
        img_w = px.width()
        img_h = px.height()
        frame_w = slot_rect.width()
        frame_h = slot_rect.height()
        
        if img_w <= 0 or img_h <= 0 or frame_w <= 0 or frame_h <= 0:
            return
        
        draw_w, draw_h = float(img_w), float(img_h)
        
        if img_w > frame_w or img_h > frame_h:
            ratio_w = frame_w / img_w
            ratio_h = frame_h / img_h
            ratio = min(ratio_w, ratio_h)
            draw_w = img_w * ratio
            draw_h = img_h * ratio
        
        if draw_w <= 0 or draw_h <= 0:
            return
        
        img_x = slot_rect.x() + (frame_w - draw_w) / 2
        img_y = slot_rect.y() + (frame_h - draw_h) / 2
        
        need_scale = (abs(draw_w - img_w) > 0.5 or abs(draw_h - img_h) > 0.5)
        if need_scale:
            sw = max(1, int(draw_w))
            sh = max(1, int(draw_h))
            scaled = px.scaled(sw, sh,
                               Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            # ★ v9.2: 用 int 坐标 + int 尺寸调用 drawPixmap(x, y, w, h, pixmap)
            #      避免 QRectF 重载在某些 PySide6 版本中的类型问题
            if not scaled.isNull():
                painter.drawPixmap(int(img_x), int(img_y), sw, sh, scaled)
        else:
            painter.drawPixmap(int(img_x), int(img_y), px)

    def _draw_slot_buttons(self, painter, slot_index, slot_rect):
        """★ v9.12: 极简线性图标按钮，垂直排列在槽位右侧外部空白处。

        16×16px 固定尺寸，不缩放，不遮挡图片主体。
        ∧ 上移 | ∨ 下移 | ✕ 删除（红色）
        """
        BTN = 16       # 固定16px
        GAP_BTN = 2    # 按钮间距
        MARGIN = 4     # 槽位右侧间距

        # 按钮组位置：槽位右侧边缘外，垂直居中
        bx = slot_rect.right() + MARGIN
        total_btn_h = BTN * 3 + GAP_BTN * 2
        by = slot_rect.top() + (slot_rect.height() - total_btn_h) / 2

        btn_up_r   = QRectF(bx, by, BTN, BTN)
        btn_down_r = QRectF(bx, by + BTN + GAP_BTN, BTN, BTN)
        btn_del_r  = QRectF(bx, by + (BTN + GAP_BTN) * 2, BTN, BTN)

        # ── 上移 ∧ ──
        painter.setPen(QPen(QColor(100, 100, 100, 200), 1.5))
        painter.setBrush(Qt.NoBrush)
        c = btn_up_r.center()
        s = 4
        painter.drawLine(QPointF(c.x() - s, c.y() + s), QPointF(c.x(), c.y() - s))
        painter.drawLine(QPointF(c.x(), c.y() - s), QPointF(c.x() + s, c.y() + s))

        # ── 下移 ∨ ──
        c = btn_down_r.center()
        painter.drawLine(QPointF(c.x() - s, c.y() - s), QPointF(c.x(), c.y() + s))
        painter.drawLine(QPointF(c.x(), c.y() + s), QPointF(c.x() + s, c.y() - s))

        # ── 删除 ✕（红色）──
        painter.setPen(QPen(QColor(200, 60, 60, 200), 1.5))
        c = btn_del_r.center()
        painter.drawLine(QPointF(c.x() - s, c.y() - s), QPointF(c.x() + s, c.y() + s))
        painter.drawLine(QPointF(c.x() - s, c.y() + s), QPointF(c.x() + s, c.y() - s))

    def _calc_slot_height_for_image(self, px):
        if not px or px.isNull():
            return self._slot_h
        img_w, img_h = px.width(), px.height()
        if img_w <= 0:
            return self._slot_h
        h = int(SLOT_WIDTH * img_h / img_w)
        return h

    def _compute_geometry(self):
        # ★ v10.0: 模特多动作模板走专用几何计算
        if self._is_multi_pose:
            self._compute_multi_pose_geometry()
            return
        
        n_slots = len(self.template["slot_labels"])
        self._compute_scale_factor()
        
        pad = self._s(BASE_PADDING)
        gap = self._s(BASE_GAP)
        self._pad = pad
        self._gap = gap
        
        # ★ v9.0: 无工具栏，直接从名称行开始
        self._toolbar_h = 0
        
        self._name_h = self._s(BASE_NAME_H)
        self._prompt_h = self._s(BASE_PROMPT_H)
        
        # 各区域 Y 坐标
        self._name_y = 0
        self._slots_y = self._name_h + pad
        self._slots_start_x = pad
        
        ratio_h = ratio_to_height(self._ratio_str)
        max_h = self._slot_h
        for i in range(n_slots):
            if i < len(self.slots) and self.slots[i][1] and not self.slots[i][1].isNull():
                img_h = self._calc_slot_height_for_image(self.slots[i][1])
                max_h = max(max_h, img_h)
        max_h = max(max_h, ratio_h)
        
        if self._result_pixmap and not self._result_pixmap.isNull():
            result_h = self._calc_slot_height_for_image(self._result_pixmap)
            max_h = max(max_h, result_h)
        
        self._slot_h = max_h
        
        self._gen_w = SLOT_WIDTH
        # ★ v9.12: 每个槽位右侧外部有操作按钮条（MARGIN 4 + BTN 16 + pad 4 = 24px）
        self._btn_strip_w = 24
        total_w = pad + n_slots * (SLOT_WIDTH + self._btn_strip_w + gap) + SLOT_WIDTH + gap + pad
        self._total_w = total_w
        self._total_h = self._name_h + self._slot_h + self._prompt_h + pad * 3
        
        self._gen_x = pad + n_slots * (SLOT_WIDTH + self._btn_strip_w + gap) + gap
        self._prompt_y = self._slots_y + self._slot_h + self._s(4)

    def _get_slot_x(self, index):
        return self._slots_start_x + index * (SLOT_WIDTH + self._btn_strip_w + self._gap)

    def boundingRect(self):
        return QRectF(0, 0, self._total_w, self._total_h)

    def _paint_multi_pose(self, painter):
        """绘制模特多动作模板卡片（v13 极简三列+统一14px字体）"""
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self._total_w, self._total_h
        pad = self._s(10)
        gap = self._s(8)

        # ── 卡片背景 ──
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(250, 250, 252, 230))
        corner_r = self._s(10)
        painter.drawRoundedRect(QRectF(0, 0, w, h), corner_r, corner_r)

        if self.isSelected():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(0, 0, 0, 60), 1.5))
            painter.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), corner_r, corner_r)

        # ── 标题栏（统一14px字体）──
        painter.setPen(QColor(80, 80, 80))
        font = QFont("Microsoft YaHei UI", self._s_font_size(14))
        painter.setFont(font)
        name_text = f"任务 #{self.group_index} · {self.template['name']}"
        status_labels = {"idle": "", "running": "生成中...", "success": "完成", "error": "失败"}
        if self.status != "idle":
            name_text += f"  {status_labels.get(self.status, '')}"
        painter.drawText(QRectF(pad, self._name_y, w - pad * 2 - self._s(30), self._name_h),
                        Qt.AlignVCenter | Qt.AlignLeft, name_text)

        # ✕ 删除按钮
        painter.setPen(QColor(200, 60, 60))
        painter.setFont(QFont("Microsoft YaHei UI", self._s_font_size(14)))
        if self._hover_state == "del":
            painter.setBrush(QColor(255, 230, 230))
        else:
            painter.setBrush(Qt.NoBrush)
        del_r = QRectF(self._total_w - pad - self._s(30), self._name_y, self._s(30), self._name_h)
        painter.drawRoundedRect(del_r, self._s(4), self._s(4))
        painter.drawText(del_r, Qt.AlignCenter, "✕")

        # ════════════════════════════════════════════════════
        # 第一列：参考图上传框（200×200，干净无控件）
        # ════════════════════════════════════════════════════
        ref_rect = self._slot_rect
        ref_corner = self._s(6)

        if self._hover_state == "ref":
            painter.setBrush(QColor(245, 245, 246))
            painter.setPen(QPen(QColor(0, 0, 0, 80), 1.5))
        else:
            painter.setBrush(QColor(255, 255, 255))
            painter.setPen(QPen(QColor(0, 0, 0, 30), 1))
        painter.drawRoundedRect(ref_rect, ref_corner, ref_corner)

        if self.slots and self.slots[0][1] and not self.slots[0][1].isNull():
            self._draw_pixmap_in_slot(painter, self.slots[0][1], ref_rect)
        else:
            # 虚线边框
            pen = QPen(QColor(180, 180, 180), 1, Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            inner = ref_rect.adjusted(self._s(4), self._s(4), -self._s(4), -self._s(4))
            painter.drawRoundedRect(inner, ref_corner, ref_corner)

            # 统一14px字体
            painter.setPen(QColor(180, 180, 180))
            ef = QFont("Microsoft YaHei UI", self._s_font_size(14))
            painter.setFont(ef)
            painter.drawText(ref_rect, Qt.AlignCenter, "+")

        # ════════════════════════════════════════════════════
        # 第二列：控制面板（竖向紧凑排列，统一14px字体）
        # ════════════════════════════════════════════════════
        # 统一字体：正文14px，标签11px
        body_f = QFont("Microsoft YaHei UI", self._s_font_size(14))
        label_f = QFont("Microsoft YaHei UI", self._s_font_size(11))

        # ── 1. 动作提示词专家预设 ──
        expert_rect = self._expert_rect
        is_expert_hover = self._hover_state == "expert"
        painter.setBrush(QColor(250, 250, 252) if is_expert_hover else QColor(244, 244, 246))
        painter.setPen(QPen(QColor(0, 0, 0, 30), 1))
        painter.drawRoundedRect(expert_rect, self._s(5), self._s(5))

        # 标签（11px）
        painter.setPen(QColor(120, 120, 120))
        painter.setFont(label_f)
        painter.drawText(QRectF(expert_rect.x(), expert_rect.y() - self._s(12), expert_rect.width(), self._s(10)),
                        Qt.AlignLeft | Qt.AlignBottom, "动作提示词专家")

        # 内容（14px）
        painter.setPen(QColor(60, 60, 60) if self._pose_expert_prompt else QColor(180, 180, 180))
        painter.setFont(body_f)
        expert_text = self._pose_expert_prompt if self._pose_expert_prompt else "点击输入..."
        metrics = painter.fontMetrics()
        max_w = expert_rect.width() - self._s(12)
        display_text = metrics.elidedText(expert_text, Qt.TextElideMode.ElideRight, max_w)
        painter.drawText(expert_rect.adjusted(self._s(8), self._s(4), -self._s(8), 0),
                        Qt.AlignVCenter | Qt.AlignLeft, display_text)

        # ── 2. 产品卖点描述 ──
        dian_rect = self._卖点_rect
        is_dian_hover = self._hover_state == "卖点"
        painter.setBrush(QColor(250, 250, 252) if is_dian_hover else QColor(244, 244, 246))
        painter.setPen(QPen(QColor(0, 0, 0, 30), 1))
        painter.drawRoundedRect(dian_rect, self._s(5), self._s(5))

        painter.setPen(QColor(120, 120, 120))
        painter.setFont(label_f)
        painter.drawText(QRectF(dian_rect.x(), dian_rect.y() - self._s(12), dian_rect.width(), self._s(10)),
                        Qt.AlignLeft | Qt.AlignBottom, "产品卖点")

        painter.setPen(QColor(60, 60, 60) if self._pose_extra_info else QColor(180, 180, 180))
        painter.setFont(body_f)
        dian_text = self._pose_extra_info if self._pose_extra_info else "点击输入..."
        display_text = metrics.elidedText(dian_text, Qt.TextElideMode.ElideRight, max_w)
        painter.drawText(dian_rect.adjusted(self._s(8), self._s(4), -self._s(8), 0),
                        Qt.AlignVCenter | Qt.AlignLeft, display_text)

        # ── 3. 模型选择（手动输入+滚轮）──
        mrect = self._model_rect
        m_corner = self._s(5)
        if self._hover_state == "model" or self._editing_model:
            painter.setBrush(QColor(245, 245, 246))
            painter.setPen(QPen(QColor(0, 0, 0, 80), 1.5))
        else:
            painter.setBrush(QColor(244, 244, 246))
            painter.setPen(QPen(QColor(0, 0, 0, 40), 1))
        painter.drawRoundedRect(mrect, m_corner, m_corner)

        # 标签（11px）
        painter.setPen(QColor(120, 120, 120))
        painter.setFont(label_f)
        painter.drawText(QRectF(mrect.x(), mrect.y() - self._s(12), mrect.width(), self._s(10)),
                        Qt.AlignLeft | Qt.AlignBottom, "模型")

        # 内容（14px）
        model_name = self._editing_model_text if self._editing_model else MODEL_ID_TO_NAME.get(self._selected_model, self._selected_model)
        if len(model_name) > 18:
            model_name = model_name[:18] + "..."
        painter.setPen(QColor(60, 60, 60))
        painter.setFont(body_f)
        painter.drawText(mrect.adjusted(self._s(8), self._s(4), -self._s(8), 0),
                        Qt.AlignVCenter | Qt.AlignLeft, model_name if model_name else "点击输入...")

        # ── 4. 数量（滚轮调节）──
        cnt_rect = self._count_rect
        is_cnt_hover = self._hover_state == "count"
        if is_cnt_hover:
            painter.setBrush(QColor(245, 245, 246))
            painter.setPen(QPen(QColor(0, 0, 0, 80), 1.5))
        else:
            painter.setBrush(QColor(244, 244, 246))
            painter.setPen(QPen(QColor(0, 0, 0, 40), 1))
        painter.drawRoundedRect(cnt_rect, self._s(5), self._s(5))

        # 标签（11px）
        painter.setPen(QColor(120, 120, 120))
        painter.setFont(label_f)
        painter.drawText(QRectF(cnt_rect.x(), cnt_rect.y() - self._s(12), cnt_rect.width(), self._s(10)),
                        Qt.AlignLeft | Qt.AlignBottom, "数量")

        # 数值（14px粗体）
        painter.setPen(QColor(40, 40, 40))
        cf = QFont("Microsoft YaHei UI", self._s_font_size(14))
        painter.setFont(cf)
        painter.drawText(cnt_rect, Qt.AlignCenter, str(self._pose_count))

        # ── 5. 生成动作提示词按钮 ──
        gp_rect = self._gen_btn_rect
        is_gp_hover = self._hover_state == "gen_prompts"
        if self._generating_prompts:
            painter.setBrush(QColor(230, 230, 234))
            painter.setPen(Qt.NoPen)
        elif is_gp_hover:
            painter.setBrush(QColor(22, 22, 22))
            painter.setPen(Qt.NoPen)
        else:
            painter.setBrush(QColor(60, 60, 60))
            painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(gp_rect, self._s(5), self._s(5))

        painter.setPen(QColor(255, 255, 255))
        gf = QFont("Microsoft YaHei UI", self._s_font_size(14))
        painter.setFont(gf)
        gp_text = "生成动作提示词" if not self._generating_prompts else "生成中..."
        painter.drawText(gp_rect, Qt.AlignCenter, gp_text)

        # ── 6. 动作提示词列表（序号纵向排列）──
        prompt_title_y = self._prompt_list_y - self._s(12)
        painter.setPen(QColor(120, 120, 120))
        pt_f = QFont("Microsoft YaHei UI", self._s_font_size(11))
        painter.setFont(pt_f)
        painter.drawText(QRectF(self._col2_x, prompt_title_y, self._col2_w, self._s(10)),
                        Qt.AlignLeft | Qt.AlignVCenter, f"提示词 ({self._pose_count})")

        for i, box_rect in enumerate(self._prompt_boxes):
            if i >= len(self._generated_poses) or not self._generated_poses[i]:
                # 空格子
                if self._hover_state == f"prompt_{i}":
                    painter.setBrush(QColor(245, 245, 246))
                    painter.setPen(QPen(QColor(0, 0, 0, 60), 1))
                else:
                    painter.setBrush(QColor(255, 255, 255))
                    painter.setPen(QPen(QColor(0, 0, 0, 25), 1))
                painter.drawRoundedRect(box_rect, self._s(5), self._s(5))

                painter.setPen(QColor(200, 200, 200))
                ef3 = QFont("Microsoft YaHei UI", self._s_font_size(14))
                painter.setFont(ef3)
                painter.drawText(box_rect, Qt.AlignCenter, f"+ 动作{i+1}")
            else:
                # 有内容格子
                if self._hover_state == f"prompt_{i}":
                    painter.setBrush(QColor(248, 248, 250))
                    painter.setPen(QPen(QColor(0, 0, 0, 50), 1))
                else:
                    painter.setBrush(QColor(252, 252, 254))
                    painter.setPen(QPen(QColor(0, 0, 0, 20), 1))
                painter.drawRoundedRect(box_rect, self._s(5), self._s(5))

                painter.setPen(QColor(60, 60, 60))
                ef4 = QFont("Microsoft YaHei UI", self._s_font_size(14))
                painter.setFont(ef4)
                text = self._generated_poses[i]
                max_w_box = box_rect.width() - self._s(20)
                display_text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, max_w_box)
                painter.drawText(box_rect.adjusted(self._s(18), 0, -self._s(4), 0),
                                Qt.AlignVCenter | Qt.AlignLeft, display_text)

                # 序号角标
                num_bg_r = QRectF(box_rect.x() + self._s(2), box_rect.y() + self._s(2), self._s(14), self._s(14))
                painter.setBrush(QColor(0, 0, 0, 40))
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(num_bg_r, self._s(3), self._s(3))
                painter.setPen(QColor(255, 255, 255))
                nf = QFont("Microsoft YaHei UI", self._s_font_size(10))
                painter.setFont(nf)
                painter.drawText(num_bg_r, Qt.AlignCenter, str(i + 1))

        # ── 7. 执行生成按钮 ──
        btn_rect = self._btn_rect
        is_btn_hover = self._hover_state == "execute"
        if self.status == "running":
            painter.setBrush(QColor(200, 200, 200))
            painter.setPen(Qt.NoPen)
        elif is_btn_hover:
            painter.setBrush(QColor(20, 20, 20))
            painter.setPen(Qt.NoPen)
        else:
            painter.setBrush(QColor(50, 50, 50))
            painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(btn_rect, self._s(6), self._s(6))

        painter.setPen(QColor(255, 255, 255))
        bf = QFont("Microsoft YaHei UI", self._s_font_size(14))
        painter.setFont(bf)

        if self.status == "running":
            btn_text = "生成中..."
        elif self._show_result_count > 0:
            btn_text = f"执行 ({self._show_result_count}张)"
        else:
            btn_text = "执行生成"
        painter.drawText(btn_rect, Qt.AlignCenter, btn_text)

        # ════════════════════════════════════════════════════
        # 第三列：成品展示区（默认1个，执行后动态增加）
        # ════════════════════════════════════════════════════
        result_title_y = self._col3_y - self._s(12)
        painter.setPen(QColor(120, 120, 120))
        painter.setFont(pt_f)
        result_label = f"结果 ({self._show_result_count}张)" if self._show_result_count > 0 else "结果"
        painter.drawText(QRectF(self._col3_x, result_title_y, self._col3_w, self._s(10)),
                        Qt.AlignLeft | Qt.AlignVCenter, result_label)

        # 成品图格子
        for i, box_rect in enumerate(self._result_boxes):
            is_executed = i < self._show_result_count

            if is_executed and i < len(self._result_pixmaps) and self._result_pixmaps[i] and not self._result_pixmaps[i].isNull():
                # 有结果的格子
                painter.setBrush(QColor(255, 255, 255))
                painter.setPen(QPen(QColor(22, 163, 74, 80), 1.5))
                painter.drawRoundedRect(box_rect, self._s(5), self._s(5))
                self._draw_pixmap_in_slot(painter, self._result_pixmaps[i], box_rect)

                # 序号角标
                num_bg_r = QRectF(box_rect.x() + self._s(2), box_rect.y() + self._s(2), self._s(14), self._s(14))
                painter.setBrush(QColor(22, 163, 74, 180))
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(num_bg_r, self._s(3), self._s(3))
                painter.setPen(QColor(255, 255, 255))
                nf = QFont("Microsoft YaHei UI", self._s_font_size(10))
                painter.setFont(nf)
                painter.drawText(num_bg_r, Qt.AlignCenter, str(i + 1))
            elif self.status == "running" and self._anim_timer.isActive():
                # 生成中动画
                painter.setBrush(QColor(255, 255, 255))
                painter.setPen(QPen(QColor(0, 0, 0, 25), 1))
                painter.drawRoundedRect(box_rect, self._s(5), self._s(5))

                bar_x = int(self._scan_pos * (box_rect.width() + 40)) - 20
                gradient = QLinearGradient(box_rect.x() + bar_x - 20, 0, box_rect.x() + bar_x + 20, 0)
                gradient.setColorAt(0.0, QColor(255, 255, 255, 0))
                gradient.setColorAt(0.5, QColor(245, 245, 245, 180))
                gradient.setColorAt(1.0, QColor(255, 255, 255, 0))
                painter.setPen(Qt.NoPen)
                painter.setBrush(gradient)
                painter.drawRoundedRect(box_rect, self._s(5), self._s(5))

                painter.setPen(QColor(160, 160, 160))
                rf = QFont("Microsoft YaHei UI", self._s_font_size(14))
                painter.setFont(rf)
                painter.drawText(box_rect, Qt.AlignCenter, f"{i+1}/{self._pose_count}")
            elif self._show_result_count == 0:
                # 未执行时显示1个占位框
                painter.setBrush(QColor(255, 255, 255))
                painter.setPen(QPen(QColor(0, 0, 0, 20), 1, Qt.DotLine))
                painter.drawRoundedRect(box_rect, self._s(5), self._s(5))
                painter.setPen(QColor(200, 200, 200))
                rf2 = QFont("Microsoft YaHei UI", self._s_font_size(14))
                painter.setFont(rf2)
                painter.drawText(box_rect, Qt.AlignCenter, "+")
            else:
                # 空结果格子
                painter.setBrush(QColor(255, 255, 255))
                painter.setPen(QPen(QColor(0, 0, 0, 20), 1, Qt.DotLine))
                painter.drawRoundedRect(box_rect, self._s(5), self._s(5))
                painter.setPen(QColor(200, 200, 200))
                rf2 = QFont("Microsoft YaHei UI", self._s_font_size(14))
                painter.setFont(rf2)
                painter.drawText(box_rect, Qt.AlignCenter, f"{i+1}")

    def paint(self, painter, option, widget=None):
        # ★ v10.0: 模特多动作模板走专用渲染
        if self._is_multi_pose:
            self._paint_multi_pose(painter)
            return
        
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self._total_w, self._total_h
        pad = self._pad
        sf = self._scale_factor
        
        # ── 卡片背景 ──
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(250, 250, 252, 230))
        corner_r = self._s(10)
        painter.drawRoundedRect(QRectF(0, 0, w, h), corner_r, corner_r)
        
        if self.isSelected():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(0, 0, 0, 60), 1.5))
            painter.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), corner_r, corner_r)
        
# ── 任务名称行 ──
        painter.setPen(QColor(80, 80, 80))
        name_font_size = self._s_font_size(14)
        font = QFont("Microsoft YaHei UI", name_font_size, QFont.Bold)
        painter.setFont(font)
        name_text = f"任务 #{self.group_index} · {self.template['name']}"
        
        status_labels = {"idle": "", "running": "生成中...", "success": "完成", "error": "失败"}
        status_text = status_labels.get(self.status, "")
        if status_text:
            name_text += f"  {status_text}"
        
        del_btn_w = self._s(30)
        painter.drawText(QRectF(pad, self._name_y, w - pad * 2 - del_btn_w, self._name_h),
                       Qt.AlignVCenter | Qt.AlignLeft, name_text)
        
        # ✕ 删除按钮
        painter.setPen(QColor(200, 60, 60))
        close_font_size = self._s_font_size(14)
        close_font = QFont("Microsoft YaHei UI", close_font_size, QFont.Bold)
        painter.setFont(close_font)
        close_rect = QRectF(w - pad - del_btn_w, self._name_y, del_btn_w, self._name_h)
        painter.drawText(close_rect, Qt.AlignVCenter | Qt.AlignRight, "✕")
        
        # ── 卡槽 ──
        slot_label_font_size = self._s_font_size(7)
        slot_label_font = QFont("Microsoft YaHei UI", slot_label_font_size)
        slot_empty_font_size = self._s_font_size(9)
        slot_empty_font = QFont("Microsoft YaHei UI", slot_empty_font_size, QFont.DemiBold)
        
        for i, (path, px) in enumerate(self.slots):
            sx = self._get_slot_x(i)
            sy = self._slots_y
            slot_rect = QRectF(sx, sy, SLOT_WIDTH, self._slot_h)
            slot_corner = self._s(6)
            
            painter.setBrush(QColor(255, 255, 255))
            painter.setPen(QPen(QColor(0, 0, 0, 30), 1))
            painter.drawRoundedRect(slot_rect, slot_corner, slot_corner)
            
            if px and not px.isNull():
                self._draw_pixmap_in_slot(painter, px, slot_rect)
                
                # ★ v9.10: 绘制槽位操作按钮（上移/下移/删除图片）
                self._draw_slot_buttons(painter, i, slot_rect)
            else:
                painter.setPen(QColor(170, 170, 170))
                painter.setFont(slot_empty_font)
                label = self.template["slot_labels"][i] if i < len(self.template["slot_labels"]) else f"槽{i+1}"
                painter.drawText(slot_rect, Qt.AlignCenter, f"+ {label}")
            
            painter.setPen(QColor(160, 160, 160))
            painter.setFont(slot_label_font)
            label = self.template["slot_labels"][i] if i < len(self.template["slot_labels"]) else ""
            if label:
                label_h = self._s(14)
                painter.drawText(QRectF(sx, sy + self._slot_h - label_h, SLOT_WIDTH, label_h),
                               Qt.AlignCenter, label)
        
        # ── 生成占位框 ──
        gen_rect = QRectF(self._gen_x, self._slots_y, SLOT_WIDTH, self._slot_h)
        gen_corner = self._s(6)
        
        if self.status == "running" and self._anim_timer.isActive():
            painter.setBrush(QColor(255, 255, 255))
            painter.setPen(QPen(QColor(0, 0, 0, 25), 1))
            painter.drawRoundedRect(gen_rect, gen_corner, gen_corner)
            
            bar_x = int(self._scan_pos * (SLOT_WIDTH + 60)) - 30
            gradient = QLinearGradient(gen_rect.x() + bar_x - 30, 0, gen_rect.x() + bar_x + 30, 0)
            gradient.setColorAt(0.0, QColor(255, 255, 255, 0))
            gradient.setColorAt(0.5, QColor(245, 245, 245, 180))
            gradient.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(gradient)
            painter.drawRoundedRect(gen_rect, gen_corner, gen_corner)
            
            painter.setPen(QColor(160, 160, 160))
            font = QFont("Microsoft YaHei UI", self._s_font_size(8), QFont.DemiBold)
            painter.setFont(font)
            painter.drawText(gen_rect, Qt.AlignCenter, "生成中...")
            
        elif self._result_pixmap and not self._result_pixmap.isNull():
            painter.setBrush(QColor(255, 255, 255))
            painter.setPen(QPen(QColor(22, 163, 74, 80), 1.5))
            painter.drawRoundedRect(gen_rect, gen_corner, gen_corner)
            self._draw_pixmap_in_slot(painter, self._result_pixmap, gen_rect)
            
            painter.setPen(QColor(22, 163, 74))
            font = QFont("Microsoft YaHei UI", self._s_font_size(7))
            painter.setFont(font)
            label_h = self._s(14)
            painter.drawText(QRectF(gen_rect.x(), gen_rect.y() + self._slot_h - label_h, SLOT_WIDTH, label_h),
                           Qt.AlignCenter, "结果")
                           
        elif self.status == "error":
            painter.setBrush(QColor(255, 245, 245))
            painter.setPen(QPen(QColor(220, 38, 38, 60), 1))
            painter.drawRoundedRect(gen_rect, gen_corner, gen_corner)
            painter.setPen(QColor(220, 38, 38))
            font = QFont("Microsoft YaHei UI", self._s_font_size(8))
            painter.setFont(font)
            err_text = self._error_msg[:10] if self._error_msg else "失败"
            painter.drawText(gen_rect, Qt.AlignCenter, err_text)
        else:
            painter.setBrush(QColor(255, 255, 255))
            painter.setPen(QPen(QColor(0, 0, 0, 20), 1, Qt.DotLine))
            painter.drawRoundedRect(gen_rect, gen_corner, gen_corner)
            painter.setPen(QColor(200, 200, 200))
            font = QFont("Microsoft YaHei UI", self._s_font_size(8))
            painter.setFont(font)
            painter.drawText(gen_rect, Qt.AlignCenter, "结果")
        
        # ── 提示词区域 ──
        prompt_rect = QRectF(pad, self._prompt_y, w - pad * 2, self._prompt_h)
        prompt_corner = self._s(4)
        painter.setBrush(QColor(245, 245, 246, 180))
        painter.setPen(QPen(QColor(0, 0, 0, 20), 0.5))
        painter.drawRoundedRect(prompt_rect, prompt_corner, prompt_corner)
        
        prompt_text = self._custom_prompt if self._custom_prompt else "点击编辑提示词..."
        c = 80 if self._custom_prompt else 180
        painter.setPen(QColor(c, c, c))
        font = QFont("Microsoft YaHei UI", self._s_font_size(8))
        painter.setFont(font)
        inner_pad = self._s(6)
        painter.drawText(prompt_rect.adjusted(inner_pad, 0, -inner_pad, 0),
                        Qt.AlignVCenter | Qt.AlignLeft, prompt_text)

    def get_image_paths(self):
        return [s[0] for s in self.slots if s[0]]

    def get_prompt(self, global_suffix=""):
        if self._custom_prompt:
            prompt = self._custom_prompt
        else:
            prompt = self.template["default_prompt"]
        if global_suffix:
            prompt = f"{prompt}。{global_suffix}"
        return prompt

    def set_slot_image(self, slot_index, path):
        if slot_index < 0 or slot_index >= len(self.slots):
            return False
        if not path or (not path.startswith("http") and not os.path.isfile(path)):
            return False
        px = QPixmap(path)
        if px.isNull():
            return False
        self.slots[slot_index] = (path, px)
        
        old_h = self._slot_h
        self._compute_geometry()
        self.prepareGeometryChange()
        self.update()
        if self._slot_h != old_h:
            self.slot_sizes_changed.emit(self)
        self.geometry_changed.emit(self)
        return True

    def fill_next_slot(self, path):
        px = QPixmap(path)
        if px.isNull():
            return False
        for i, (sp, _) in enumerate(self.slots):
            if sp is None:
                self.slots[i] = (path, px)
                old_h = self._slot_h
                self._compute_geometry()
                self.prepareGeometryChange()
                self.update()
                if self._slot_h != old_h:
                    self.slot_sizes_changed.emit(self)
                self.geometry_changed.emit(self)
                return True
        return False

    def set_status(self, status, msg=""):
        self.status = status
        self._error_msg = msg
        if status == "running":
            self._scan_pos = 0.0
            self._result_pixmap = None
            self._anim_timer.start()
        elif status in ("success", "error"):
            self._anim_timer.stop()
        else:
            self._anim_timer.stop()
            self._result_pixmap = None
        self.update()

    def show_result(self, result_path):
        self._anim_timer.stop()
        self.result_paths = [result_path] if result_path else []
        if result_path and os.path.isfile(result_path):
            px = QPixmap(result_path)
            if not px.isNull():
                old_h = self._slot_h
                self._result_pixmap = px
                self._compute_geometry()
                self.prepareGeometryChange()
                if self._slot_h != old_h:
                    self.slot_sizes_changed.emit(self)
                self.geometry_changed.emit(self)
                self.status = "success"
                self.update()
                return
        self.status = "success"
        self.update()

    def clear_all(self):
        self.slots = [(None, None)] * len(self.slots)
        self._custom_prompt = ""
        self.result_paths = []
        self._result_pixmap = None
        self._error_msg = ""
        self._anim_timer.stop()
        self.status = "idle"
        self._slot_h = ratio_to_height(self._ratio_str)
        self._compute_geometry()
        self.prepareGeometryChange()
        self.update()
        self.geometry_changed.emit(self)

    def update_index(self, new_index):
        self.group_index = new_index
        self.update()

    def _advance_scan(self):
        self._scan_pos += 0.015
        if self._scan_pos > 1.0:
            self._scan_pos = 0.0
        self.update()

    def _get_hit_rects(self):
        """★ v9.12: 返回所有可点击区域（按钮在槽位右侧外部）。"""
        w = self._total_w
        pad = self._pad
        del_btn_w = self._s(30)
        del_rect = QRectF(w - pad - del_btn_w, self._name_y, del_btn_w, self._name_h)

        slot_rects = []
        btn_up_rects = []     # 上移按钮
        btn_down_rects = []   # 下移按钮
        btn_del_img_rects = []# 删除图片按钮

        BTN = 16; GAP_BTN = 2; MARGIN = 4

        for i in range(len(self.slots)):
            sx = self._get_slot_x(i)
            sr = QRectF(sx, self._slots_y, SLOT_WIDTH, self._slot_h)
            slot_rects.append(sr)

            if self.slots[i][0] is not None:  # 有图片才有按钮
                bx = sr.right() + MARGIN
                total_btn_h = BTN * 3 + GAP_BTN * 2
                by = sr.top() + (sr.height() - total_btn_h) / 2

                btn_up_rects.append(QRectF(bx, by, BTN, BTN))
                btn_down_rects.append(QRectF(bx, by + BTN + GAP_BTN, BTN, BTN))
                btn_del_img_rects.append(QRectF(bx, by + (BTN + GAP_BTN) * 2, BTN, BTN))
            else:
                btn_up_rects.append(QRectF())
                btn_down_rects.append(QRectF())
                btn_del_img_rects.append(QRectF())

        prompt_rect = QRectF(pad, self._prompt_y, w - pad * 2, self._prompt_h)

        return {
            "del": del_rect,
            "slots": slot_rects,
            "btn_up": btn_up_rects,
            "btn_down": btn_down_rects,
            "btn_del_img": btn_del_img_rects,
            "prompt": prompt_rect,
        }

    # ════════════════════════════════════════════════════
    #  模特多动作鼠标事件（v10.0 新增）
    # ════════════════════════════════════════════════════
    def hoverMoveEvent(self, event):
        """★ v10.0: 悬停状态跟踪"""
        if not self._is_multi_pose:
            super().hoverMoveEvent(event)
            return
        pos = event.pos()
        old = self._hover_state
        self._hover_state = self._get_multi_pose_hover(pos)
        if old != self._hover_state:
            self.update()
    
    def mouseMoveEvent(self, event):
        """★ v10.0: 鼠标移动更新悬停状态"""
        if not self._is_multi_pose:
            super().mouseMoveEvent(event)
            return
        pos = event.pos()
        old = self._hover_state
        self._hover_state = self._get_multi_pose_hover(pos)
        if old != self._hover_state:
            self.update()
        super().mouseMoveEvent(event)

    def _get_multi_pose_hover(self, pos):
        """★ v12: 返回 pos 对应的悬停区域名称"""
        if self._del_rect.contains(pos):
            return "del"
        if self._slot_rect.contains(pos):
            return "ref"
        # 专家预设输入框
        if self._expert_rect.contains(pos):
            return "expert"
        # 产品卖点输入框
        if self._卖点_rect.contains(pos):
            return "卖点"
        # 模型选择（滚轮+点击编辑）
        if self._model_rect.contains(pos):
            return "model"
        # 数量显示（滚轮调节）
        if self._count_rect.contains(pos):
            return "count"
        # 生成提示词按钮
        if self._gen_btn_rect.contains(pos):
            return "gen_prompts"
        # 执行按钮
        if self._btn_rect.contains(pos):
            return "execute"
        # 检查提示词格子
        for i, box_rect in enumerate(self._prompt_boxes):
            if box_rect.contains(pos):
                return f"prompt_{i}"
        return ""
    
    def _multi_pose_mouse_press(self, event):
        """★ v12: 模特多动作模板的鼠标点击处理（滚轮控制数量）"""
        if event.button() == Qt.LeftButton:
            pos = event.pos()

            # ✕ 删除按钮
            if self._del_rect.contains(pos):
                self.delete_requested.emit(self)
                event.accept()
                return

            # 参考图上传
            if self._slot_rect.contains(pos):
                self._open_slot_file_dialog(0)
                event.accept()
                return

            # 专家预设编辑
            if self._expert_rect.contains(pos):
                self._edit_expert_prompt()
                event.accept()
                return

            # 产品卖点编辑
            if self._卖点_rect.contains(pos):
                self._edit_extra_info()
                event.accept()
                return

            # 模型选择（点击进入编辑模式）
            if self._model_rect.contains(pos):
                self._edit_model_input()
                event.accept()
                return

            # 生成提示词按钮
            if self._gen_btn_rect.contains(pos):
                if not self._generating_prompts:
                    self._generate_multi_pose_prompts()
                event.accept()
                return

            # 执行按钮（执行后动态增加成品框）
            if self._btn_rect.contains(pos):
                if self.status != "running":
                    # ★ v12: 先设置要显示的成品框数量，再执行
                    self._show_result_count = self._pose_count
                    self._compute_multi_pose_geometry()
                    self.prepareGeometryChange()
                    self.update()
                    self.multi_pose_execute_requested.emit(self)
                event.accept()
                return

            # 动作提示词格子点击 → 编辑单条
            for i, box_rect in enumerate(self._prompt_boxes):
                if box_rect.contains(pos):
                    self._edit_single_pose_prompt(i)
                    event.accept()
                    return

            # 退出模型编辑模式
            if self._editing_model:
                self._selected_model = self._editing_model_text
                self._editing_model = False
                self.update()

            # 其他区域：卡片选中/拖拽
            super().mousePressEvent(event)
            return

        super().mousePressEvent(event)

    def wheelEvent(self, event):
        """★ v13: 滚轮事件处理数量和模型选择"""
        if not self._is_multi_pose:
            super().wheelEvent(event)
            return

        pos = event.pos()
        delta = event.angleDelta().y()

        # 数量滚轮调节
        if self._count_rect.contains(pos):
            if delta > 0 and self._pose_count < 5:
                self._pose_count += 1
            elif delta < 0 and self._pose_count > 1:
                self._pose_count -= 1
            else:
                super().wheelEvent(event)
                return

            # 同步调整提示词数组
            if self._pose_count > len(self._generated_poses):
                while len(self._generated_poses) < self._pose_count:
                    self._generated_poses.append("")
            else:
                self._generated_poses = self._generated_poses[:self._pose_count]

            self._compute_multi_pose_geometry()
            self.prepareGeometryChange()
            self.update()
            self.geometry_changed.emit(self)
            event.accept()
            return

        # 模型滚轮选择（如果不在编辑模式）
        if self._model_rect.contains(pos) and not self._editing_model:
            # 获取模型列表循环选择
            models = list(MODEL_ID_TO_NAME.keys())

            if models:
                current_idx = models.index(self._selected_model) if self._selected_model in models else -1
                if delta > 0:
                    new_idx = (current_idx + 1) % len(models)
                else:
                    new_idx = (current_idx - 1) % len(models)
                self._selected_model = models[new_idx]
                self.update()

            event.accept()
            return

        super().wheelEvent(event)
    
    def _show_model_selector(self):
        """★ v10.0: 弹出模型选择对话框"""
        parent = _get_top_widget()
        dlg = QDialog(parent)
        dlg.setWindowTitle("选择模型")
        dlg.setFixedSize(300, 320)
        dlg.setStyleSheet("""
            QDialog { background: rgba(250,250,252,240); border-radius: 10px; }
            QListWidget { border: none; background: transparent; }
            QListWidget::item { padding: 8px 12px; border-radius: 6px; margin: 2px 4px; }
            QListWidget::item:selected { background: rgba(0,0,0,0.08); }
            QListWidget::item:hover { background: rgba(0,0,0,0.04); }
        """)
        
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(4)
        
        # 获取当前平台可用模型
        try:
            from .config_manager import ConfigManager
            cfg = ConfigManager()
            platform = cfg.get("api_provider", "kie")
        except Exception:
            platform = "kie"
        
        from_list = get_models_for_platform(platform)
        
        lw = QListWidget()
        for m in from_list:
            item = QListWidgetItem(f"  {m['name']}  ({m['id']})")
            item.setData(Qt.UserRole, m['id'])
            if m['id'] == self._selected_model:
                item.setSelected(True)
            lw.addItem(item)
        
        lw.itemClicked.connect(dlg.accept)
        layout.addWidget(lw)
        
        def on_ok():
            sel = lw.currentItem()
            if sel:
                self._selected_model = sel.data(Qt.UserRole)
                self.update()
        
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(on_ok)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        layout.addWidget(btn_box)
        
        dlg.exec()
    
    def _edit_multi_pose_custom_prompt(self):
        """★ v10.0: 编辑自定义动作指令"""
        parent = _get_top_widget()
        dlg = QDialog(parent)
        dlg.setWindowTitle("动作指令")
        dlg.setFixedSize(500, 160)
        dlg.setStyleSheet("""
            QDialog { background: rgba(250,250,252,240); border-radius: 10px; }
            QTextEdit { border: 1px solid rgba(0,0,0,0.15); border-radius: 8px;
                        background: white; padding: 8px; font-size: 13px; }
        """)
        
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(16, 16, 16, 12)
        
        info = QLabel("输入动作/风格指令（如：走秀姿势、街拍风格、优雅转身）：")
        info.setStyleSheet("color: #555; font-size: 12px; font-weight: 700;")
        layout.addWidget(info)
        
        te = QTextEdit()
        te.setText(self._pose_custom_prompt)
        layout.addWidget(te, 1)
        
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        layout.addWidget(btn_box)
        
        if dlg.exec():
            self._pose_custom_prompt = te.toPlainText().strip()
            self.update()
    
    def _edit_expert_prompt(self):
        """★ v13: 编辑动作提示词专家预设"""
        parent = _get_top_widget()
        dlg = QDialog(parent)
        dlg.setWindowTitle("动作提示词专家")
        dlg.setFixedSize(520, 260)
        dlg.setStyleSheet("""
            QDialog { background: rgba(250,250,252,240); border-radius: 10px; }
            QTextEdit { border: 1px solid rgba(0,0,0,0.15); border-radius: 8px;
                        background: white; padding: 12px; font-size: 14px; }
            QLabel { color: #555; font-size: 14px; }
        """)
        
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 20, 20, 12)
        
        info = QLabel("设置人设/规则/风格限制模板：")
        layout.addWidget(info)
        
        te = QTextEdit()
        te.setText(self._pose_expert_prompt)
        layout.addWidget(te, 1)
        
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        layout.addWidget(btn_box)
        
        if dlg.exec():
            self._pose_expert_prompt = te.toPlainText().strip()
            self.update()
    
    def _edit_extra_info(self):
        """★ v13: 编辑产品卖点/补充需求"""
        parent = _get_top_widget()
        dlg = QDialog(parent)
        dlg.setWindowTitle("产品卖点")
        dlg.setFixedSize(520, 180)
        dlg.setStyleSheet("""
            QDialog { background: rgba(250,250,252,240); border-radius: 10px; }
            QTextEdit { border: 1px solid rgba(0,0,0,0.15); border-radius: 8px;
                        background: white; padding: 12px; font-size: 14px; }
            QLabel { color: #555; font-size: 14px; }
        """)
        
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 20, 20, 12)
        
        info = QLabel("输入产品核心卖点或补充需求：")
        layout.addWidget(info)
        
        te = QTextEdit()
        te.setText(self._pose_extra_info)
        layout.addWidget(te, 1)
        
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        layout.addWidget(btn_box)
        
        if dlg.exec():
            self._pose_extra_info = te.toPlainText().strip()
            self.update()

    def _edit_model_input(self):
        """★ v13: 编辑模型名称（纯输入框，无下拉）"""
        parent = _get_top_widget()
        dlg = QDialog(parent)
        dlg.setWindowTitle("输入模型")
        dlg.setFixedSize(400, 120)
        dlg.setStyleSheet("""
            QDialog { background: rgba(250,250,252,240); border-radius: 10px; }
            QLineEdit { border: 1px solid rgba(0,0,0,0.2); border-radius: 6px;
                       background: white; padding: 10px 14px; font-size: 14px; }
            QLabel { color: #555; font-size: 14px; }
        """)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 20, 20, 12)

        le = QLineEdit()
        le.setText(self._selected_model)
        le.setPlaceholderText("输入模型名称...")
        layout.addWidget(le)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        layout.addWidget(btn_box)

        if dlg.exec():
            self._selected_model = le.text().strip()
            self.update()

    def _edit_single_pose_prompt(self, index):
        """★ v13: 编辑单个动作提示词"""
        parent = _get_top_widget()
        dlg = QDialog(parent)
        dlg.setWindowTitle(f"编辑动作 {index + 1}")
        dlg.setFixedSize(520, 180)
        dlg.setStyleSheet("""
            QDialog { background: rgba(250,250,252,240); border-radius: 10px; }
            QTextEdit { border: 1px solid rgba(0,0,0,0.15); border-radius: 8px;
                        background: white; padding: 12px; font-size: 14px; }
            QLabel { color: #555; font-size: 14px; }
        """)
        
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 20, 20, 12)
        
        info = QLabel(f"动作 {index + 1} 的提示词：")
        layout.addWidget(info)
        
        te = QTextEdit()
        current = self._generated_poses[index] if index < len(self._generated_poses) else ""
        te.setText(current)
        layout.addWidget(te, 1)
        
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        layout.addWidget(btn_box)
        
        if dlg.exec():
            if index < len(self._generated_poses):
                self._generated_poses[index] = te.toPlainText().strip()
            else:
                while len(self._generated_poses) <= index:
                    self._generated_poses.append("")
                self._generated_poses[index] = te.toPlainText().strip()
            self.update()
    
    def _generate_multi_pose_prompts(self):
        """★ v10.0: 调用 AI 生成多组动作提示词"""
        if not self.slots or not self.slots[0][0]:
            parent = _get_top_widget()
            QMessageBox.warning(parent, "提示", "请先上传参考图")
            return
        
        self._generating_prompts = True
        self.update()
        
        # 发射信号，通知引擎调用 AI 生成
        self.multi_pose_generate_prompts.emit(self)
    
    def set_generated_poses(self, poses):
        """★ v10.0: 设置生成的提示词列表（由引擎调用）"""
        self._generated_poses = poses
        self._generating_prompts = False
        self._compute_multi_pose_geometry()
        self.prepareGeometryChange()
        self.update()
        self.geometry_changed.emit(self)
    
    def set_multi_pose_generating(self, is_generating):
        """★ v10.0: 设置生成状态"""
        self._generating_prompts = is_generating
        self.update()

    def mousePressEvent(self, event):
        """★ v9.10: 鼠标事件处理（含槽位图片操作按钮）。★ v10.0: 多动作模板独立处理。"""
        # ★ v10.0: 模特多动作模板走专用事件处理
        if self._is_multi_pose:
            self._multi_pose_mouse_press(event)
            return
        
        if event.button() == Qt.LeftButton:
            pos = event.pos()
            
            # 1. ✕ 删除按钮（整张卡片）
            rects = self._get_hit_rects()
            if rects["del"].contains(pos):
                self.delete_requested.emit(self)
                event.accept()
                return
            
            # ★ 1.5 槽位图片操作按钮（上移/下移/删除单张）—— 优先级高于打开文件对话框
            for i in range(len(rects["btn_up"])):
                if rects["btn_up"][i].contains(pos):
                    print(f"[卡片] 槽位{i} 上移")
                    self.slot_move_up_requested.emit(i)
                    event.accept()
                    return
                if rects["btn_down"][i].contains(pos):
                    print(f"[卡片] 槽位{i} 下移")
                    self.slot_move_down_requested.emit(i)
                    event.accept()
                    return
                if rects["btn_del_img"][i].contains(pos):
                    print(f"[卡片] 槽位{i} 删除图片")
                    self.slot_image_delete_requested.emit(i)
                    event.accept()
                    return
            
            # 2. 卡槽区域 → 打开文件选择器（多选）
            for i, slot_rect in enumerate(rects["slots"]):
                if slot_rect.contains(pos):
                    self._open_slot_file_dialog(i)
                    event.accept()
                    return
            
            # 3. 提示词区域 → 编辑弹窗
            if rects["prompt"].contains(pos):
                self._last_prompt_click_scene_pos = event.scenePos()
                self._edit_prompt()
                event.accept()
                return
            
            # 4. Ctrl+点击 → 清空
            if event.modifiers() & Qt.ControlModifier:
                for i, slot_rect in enumerate(rects["slots"]):
                    if slot_rect.contains(pos):
                        old_h = self._slot_h
                        self.slots[i] = (None, None)
                        self._compute_geometry()
                        self.prepareGeometryChange()
                        self.update()
                        if self._slot_h != old_h:
                            self.slot_sizes_changed.emit(self)
                        self.geometry_changed.emit(self)
                        event.accept()
                        return
            
            # 5. 其他区域：交给 Qt 处理选中/拖拽（ItemIsSelectable + ItemIsMovable）
            super().mousePressEvent(event)
            return
        
        # 非 LeftButton
        super().mousePressEvent(event)
        
        # 非 LeftButton
        super().mousePressEvent(event)

    def _open_slot_file_dialog(self, slot_index):
        """★ v8.0: 打开文件选择对话框，支持多选。
        
        多选逻辑（按列分配）：
        - 第一张图片填入当前 slot_index
        - 剩余图片通过 extra_images_ready 信号交给引擎
        - 引擎只在 slot_index 对应的列向下新建任务并填入
        """
        filter_str = " ".join(f"*{e}" for e in sorted(IMAGE_EXTS))
        parent = _get_top_widget()
        paths, _ = QFileDialog.getOpenFileNames(
            parent, "选择图片（可多选）", "",
            f"图片文件 ({filter_str});;所有文件 (*)"
        )
        if not paths:
            return
        
        print(f"[工作流] 卡槽 {slot_index} 多选上传: {len(paths)} 张图片")
        
        # 填入当前卡槽（只填一张）
        if paths:
            if self.set_slot_image(slot_index, paths[0]):
                print(f"  → 填入卡槽 {slot_index}: {paths[0]}")
        
        # 剩余图片交给引擎处理（按列向下分配）
        extra_paths = paths[1:]
        if extra_paths:
            print(f"  → {len(extra_paths)} 张图片按列 {slot_index} 向下分配")
            self.extra_images_ready.emit(self, slot_index, extra_paths)

    def _edit_prompt(self):
        """弹出提示词编辑对话框，居中显示，限制最大尺寸。"""
        parent = _get_top_widget()
        
        dlg_w = min(int(420 * max(self._scale_factor, 1.0)), 600)
        dlg_h = min(int(220 * max(self._scale_factor, 1.0)), 400)
        
        dlg = QDialog(parent)
        dlg.setWindowTitle("编辑提示词")
        dlg.setFixedSize(dlg_w, dlg_h)
        
        font_size = max(min(self._s_font_size(12), 16), 12)
        dlg.setStyleSheet(f"""
            QDialog {{ background: rgba(250,250,252,0.98); border-radius: 10px; }}
            QTextEdit {{
                padding: 8px; border: none; border-radius: 6px;
                background: rgba(0,0,0,0.03); font-size: {font_size}px; color: #333;
            }}
            QPushButton {{
                padding: 6px 20px; font-size: {max(min(self._s_font_size(11), 14), 11)}px; font-weight: 600;
                border-radius: 5px; border: none;
            }}
        """)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(14, 14, 14, 14)
        
        te = QTextEdit()
        te.setPlainText(self._custom_prompt)
        te.setPlaceholderText(self.template["default_prompt"])
        layout.addWidget(te)
        
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)
        
        if parent:
            parent_geo = parent.geometry()
            dlg.move(
                parent_geo.x() + (parent_geo.width() - dlg_w) // 2,
                parent_geo.y() + (parent_geo.height() - dlg_h) // 2,
            )
        
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._custom_prompt = te.toPlainText().strip()
            self.update()

    def contextMenuEvent(self, event):
        pos = event.pos()
        rects = self._get_hit_rects()
        
        menu = QMenu()
        menu.setStyleSheet("""
            QMenu { background: rgba(255,255,255,0.96); border: 0.5px solid rgba(0,0,0,0.08);
                    border-radius: 8px; padding: 4px; }
            QMenu::item { padding: 6px 16px; border-radius: 4px; color: #333; font-size: 11px; font-weight: 500; }
            QMenu::item:selected { background: rgba(0,0,0,0.05); }
        """)
        
        clicked_slot = -1
        for i, slot_rect in enumerate(rects["slots"]):
            if slot_rect.contains(pos):
                clicked_slot = i
                break
        
        if clicked_slot >= 0:
            act_upload = menu.addAction(f"上传图片 → {self.template['slot_labels'][clicked_slot]}")
            act_clear = menu.addAction("清除此卡槽")
            action = menu.exec(event.screenPos())
            if action == act_upload:
                self._open_slot_file_dialog(clicked_slot)
            elif action == act_clear:
                old_h = self._slot_h
                self.slots[clicked_slot] = (None, None)
                self._compute_geometry()
                self.prepareGeometryChange()
                self.update()
                if self._slot_h != old_h:
                    self.slot_sizes_changed.emit(self)
                self.geometry_changed.emit(self)
        else:
            act_prompt = menu.addAction("编辑提示词")
            act_clear = menu.addAction("清空所有")
            act_del = menu.addAction("删除任务")
            act_del.setStyleSheet("color: rgba(220, 38, 38, 0.9);")
            action = menu.exec(event.screenPos())
            if action == act_prompt:
                self._last_prompt_click_scene_pos = event.scenePos()
                self._edit_prompt()
            elif action == act_clear:
                self.clear_all()
            elif action == act_del:
                self.delete_requested.emit(self)


# ════════════════════════════════════════════════════
#  WorkflowEngine — 执行引擎
# ════════════════════════════════════════════════════
class WorkflowEngine(QObject):
    """★ v9.8: 简化的执行引擎，不再管理工具栏位置。
    
    工具栏作为独立 QWidget 浮动在 canvas 上，位置由 main_window 控制。
    引擎只负责管理任务卡片的创建、删除、执行。
    """
    
    results_ready = Signal(list)
    results_compared = Signal(list)
    workflow_closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._driver = None
        self._task_cards = []
        self._is_running = False
        self._next_card_id = 1
        self._config = WorkflowConfig()
        self._reposition_suspended = False
        
        self._relay = _SignalRelay()
        self._relay.card_status_signal.connect(self._on_card_status)
        self._relay.card_results_signal.connect(self._on_card_results)
        self._relay.card_error_signal.connect(self._on_card_error)
        self._relay.batch_done_signal.connect(self._on_batch_done)

    @property
    def config(self):
        return self._config

    def set_driver(self, driver):
        self._driver = driver
        self._config.set_driver(driver)

    @property
    def task_cards(self):
        return self._task_cards

    @property
    def is_running(self):
        return self._is_running

    def create_task_card(self, template, ratio_str="auto", scene=None, anchor_pos=None):
        """创建新的任务卡片并添加到场景。
        
        ★ v9.10: 支持锚点定位——卡片从 anchor_pos（场景坐标）开始排列，
               紧贴工具栏正下方。
        
        Args:
            anchor_pos: QPointF, 工具栏底部中心映射到场景的坐标点。
                       卡片 X 中心对齐锚点，Y 从锚点开始向下堆叠。
        """
        card_id = self._next_card_id
        self._next_card_id += 1
        
        inherited_h = None
        if self._task_cards:
            last_card = self._task_cards[-1]
            inherited_h = last_card._slot_h
        
        card = CanvasTaskCard(
            len(self._task_cards) + 1, template, card_id,
            ratio_str=ratio_str,
            initial_slot_h=inherited_h,
            config=self._config
        )
        card.delete_requested.connect(self._delete_task_card)
        card.slot_sizes_changed.connect(self._on_card_slot_sizes_changed)
        card.geometry_changed.connect(self._on_card_geometry_changed)
        card.extra_images_ready.connect(self._on_card_extra_images)
        # ★ v9.10: 槽位图片操作信号
        card.slot_move_up_requested.connect(lambda slot_idx, c=card: self._on_slot_move_up(c, slot_idx))
        card.slot_move_down_requested.connect(lambda slot_idx, c=card: self._on_slot_move_down(c, slot_idx))
        card.slot_image_delete_requested.connect(lambda slot_idx, c=card: self._on_slot_image_delete(c, slot_idx))
        # ★ v10.0: 模特多动作模板信号
        card.multi_pose_generate_prompts.connect(lambda c=card: self._on_multi_pose_generate_prompts(c))
        card.multi_pose_execute_requested.connect(lambda c=card: self._on_multi_pose_execute(c))
        self._config.changed.connect(card.update)
        
        if scene:
            card_br = card.boundingRect()
            
            if anchor_pos is not None:
                # ★ 锚点定位：X 居中对齐，Y 紧贴锚点或上一张卡片下方
                if not self._task_cards:
                    # 第一张卡片：X 居中于锚点，Y 从锚点开始
                    start_x = anchor_pos.x() - card_br.width() / 2
                    start_y = anchor_pos.y()
                else:
                    # 后续卡片：X 与第一张对齐，Y 堆叠在最后一张下方
                    first_x = self._task_cards[0].pos().x()
                    last_card = self._task_cards[-1]
                    start_x = first_x
                    start_y = last_card.pos().y() + last_card.boundingRect().height() + BASE_CARD_GAP
                card.setPos(start_x, start_y)
            else:
                # 兼容旧逻辑（无锚点时用场景中心）
                if not self._task_cards:
                    scene_rect = scene.sceneRect()
                    center_x = scene_rect.width() / 2
                    start_x = center_x - card_br.width() / 2
                    start_y = scene_rect.height() / 2 - 100
                else:
                    last_card = self._task_cards[-1]
                    start_x = last_card.pos().x()
                    start_y = last_card.pos().y() + last_card.boundingRect().height() + BASE_CARD_GAP
                card.setPos(start_x, start_y)
            
            scene.addItem(card)
        
        self._task_cards.append(card)
        self._update_indices()
        
        return card

    def add_blank_task(self, scene=None):
        """添加空白任务卡片。"""
        if not scene:
            if self._task_cards:
                scene = self._task_cards[0].scene()
            else:
                return None
        template = self._config.template
        ratio = self._config.ratio
        card = self.create_task_card(template, ratio_str=ratio, scene=scene)
        return card

    def _on_card_extra_images(self, card, slot_index, extra_paths):
        """★ v9.3: 按列分配 — 先填已有任务中该列为空的卡槽，填满后再新建行。
        
        规则：
        1. 只操作 slot_index 列，不影响其他列
        2. 优先填充已有任务中该列为空的卡槽（按行序从触发卡片的下一行开始）
        3. 只有当 slot_index 列图片数超过当前行数时，才在末尾新建行
        4. 不产生空缺行（新建的行只填当前列，其他列留空，视觉上连续）
        """
        if not extra_paths:
            return
        tmpl = card.template
        scene = card.scene()
        n_slots = len(tmpl["slot_labels"])
        
        if slot_index >= n_slots:
            slot_index = 0
        
        print(f"[工作流] 按列分配 v9.3: 列={slot_index}({tmpl['slot_labels'][slot_index]}), 图片数={len(extra_paths)}")
        
        self._reposition_suspended = True
        
        path_idx = 0
        
        # ── 第一步：找触发卡片之后的已有行，优先填该列为空的卡槽 ──
        trigger_found = False
        for existing_card in self._task_cards:
            if existing_card is card:
                trigger_found = True
                continue  # 跳过触发卡片本身（它已经填了第一张）
            if not trigger_found:
                continue  # 触发卡片之前的行跳过
            if path_idx >= len(extra_paths):
                break
            # 该行 slot_index 列为空时才填
            if slot_index < len(existing_card.slots) and existing_card.slots[slot_index][0] is None:
                if existing_card.set_slot_image(slot_index, extra_paths[path_idx]):
                    print(f"  → 填入已有任务 #{existing_card.group_index} 卡槽 {slot_index}: {extra_paths[path_idx]}")
                    path_idx += 1
        
        # ── 第二步：剩余图片不够行，则新建行 ──
        while path_idx < len(extra_paths):
            new_card = self.create_task_card(tmpl, ratio_str=card._ratio_str, scene=scene)
            if new_card.set_slot_image(slot_index, extra_paths[path_idx]):
                print(f"  → 新建任务 #{new_card.group_index} 卡槽 {slot_index}: {extra_paths[path_idx]}")
            path_idx += 1
        
        print(f"[工作流] 按列分配完成，处理 {len(extra_paths)} 张图片")
        
        # 恢复重排
        self._reposition_suspended = False
        self._reposition_all_cards()

    def _on_card_slot_sizes_changed(self, changed_card):
        """★ v9.2: 某卡片高度变化 → 同步所有卡片高度 + 重排位置。"""
        new_h = changed_card._slot_h
        for card in self._task_cards:
            if card is changed_card:
                continue
            if card._slot_h != new_h:
                card._slot_h = new_h
                card._compute_geometry()
                card.prepareGeometryChange()
                card.update()
        # 重排时不干扰用户正在拖拽的卡片
        self._reposition_all_cards()

    def _on_card_geometry_changed(self, card):
        # ★ v9.2: 如果重排已暂停（如按列分配中），跳过
        if getattr(self, '_reposition_suspended', False):
            return
        # ★ v9.2: 防止重排信号循环
        if getattr(self, '_repositioning', False):
            return
        self._reposition_all_cards()

    def _reposition_all_cards(self):
        """★ v9.9: 重排所有卡片——保持垂直排列。
        
        只调整位置偏差超过阈值的卡片，避免频繁微移。
        """
        if not self._task_cards:
            return
        
        if getattr(self, '_repositioning', False):
            return
        self._repositioning = True
        try:
            x = self._task_cards[0].pos().x()
            y = self._task_cards[0].pos().y()
            
            for idx, card in enumerate(self._task_cards):
                if idx == 0:
                    y += card.boundingRect().height() + BASE_CARD_GAP
                    continue
                
                target_x = x
                target_y = y
                current_x = card.pos().x()
                current_y = card.pos().y()
                
                if abs(current_x - target_x) > 1 or abs(current_y - target_y) > 1:
                    card.setPos(target_x, target_y)
                
                y += card.boundingRect().height() + BASE_CARD_GAP
        finally:
            self._repositioning = False

    def _on_slot_move_up(self, card, slot_index):
        """★ v9.11: 槽位图片上移 —— 与上一张卡片同列交换（仅交换图片，框架保留）。
        
        修复要点：
        - prepareGeometryChange 必须在 _compute_geometry 之前调用
        - 交换后同步所有卡片高度并重排位置
        """
        if not self._task_cards or len(self._task_cards) < 2:
            return
        card_idx = self._task_cards.index(card) if card in self._task_cards else -1
        if card_idx <= 0:
            return  # 已经是第一张，无法上移
        
        prev_card = self._task_cards[card_idx - 1]
        
        # ★ 先 prepareGeometryChange，再改几何数据——顺序不能反！
        card.prepareGeometryChange()
        prev_card.prepareGeometryChange()
        
        # 交换两张卡片同一列（slot_index）的图片数据
        my_data = card.slots[slot_index] if slot_index < len(card.slots) else (None, None)
        prev_data = prev_card.slots[slot_index] if slot_index < len(prev_card.slots) else (None, None)
        
        if slot_index < len(card.slots):
            card.slots[slot_index] = prev_data
            if slot_index < len(prev_card.slots):
                prev_card.slots[slot_index] = my_data
        
        # 刷新两张卡片的几何
        for c in [card, prev_card]:
            c._compute_geometry()
            c.update()
        
        # ★ 同步所有卡片到统一高度 + 重排位置
        new_h = max(card._slot_h, prev_card._slot_h)
        for c in self._task_cards:
            if c._slot_h != new_h:
                c._slot_h = new_h
                c._compute_geometry()
                c.prepareGeometryChange()
                c.update()
        
        self._reposition_all_cards()
        print(f"[工作流] 上移: 卡片#{card.group_index} 与 #{prev_card.group_index} 的{slot_index}列图片交换")

    def _on_slot_move_down(self, card, slot_index):
        """★ v9.11: 槽位图片下移 —— 与下一张卡片同列交换（仅交换图片，框架保留）。
        
        修复要点：
        - prepareGeometryChange 必须在 _compute_geometry 之前调用
        - 交换后同步所有卡片高度并重排位置
        """
        if not self._task_cards or len(self._task_cards) < 2:
            return
        card_idx = self._task_cards.index(card) if card in self._task_cards else -1
        if card_idx >= len(self._task_cards) - 1:
            return  # 已经是最后一张，无法下移
        
        next_card = self._task_cards[card_idx + 1]
        
        # ★ 先 prepareGeometryChange，再改几何数据——顺序不能反！
        card.prepareGeometryChange()
        next_card.prepareGeometryChange()
        
        my_data = card.slots[slot_index] if slot_index < len(card.slots) else (None, None)
        next_data = next_card.slots[slot_index] if slot_index < len(next_card.slots) else (None, None)
        
        if slot_index < len(card.slots):
            card.slots[slot_index] = next_data
            if slot_index < len(next_card.slots):
                next_card.slots[slot_index] = my_data
        
        # 刷新两张卡片的几何
        for c in [card, next_card]:
            c._compute_geometry()
            c.update()
        
        # ★ 同步所有卡片到统一高度 + 重排位置
        new_h = max(card._slot_h, next_card._slot_h)
        for c in self._task_cards:
            if c._slot_h != new_h:
                c._slot_h = new_h
                c._compute_geometry()
                c.prepareGeometryChange()
                c.update()
        
        self._reposition_all_cards()
        print(f"[工作流] 下移: 卡片#{card.group_index} 与 #{next_card.group_index} 的{slot_index}列图片交换")

    def _on_slot_image_delete(self, card, slot_index):
        """★ v9.10: 删除槽位中的单张图片，不影响其他内容。"""
        if slot_index < len(card.slots) and card.slots[slot_index][0] is not None:
            old_h = card._slot_h
            card.slots[slot_index] = (None, None)
            card._compute_geometry()
            card.prepareGeometryChange()
            card.update()
            if card._slot_h != old_h:
                self._reposition_all_cards()  # 高度变了可能需要重排
            print(f"[工作流] 删除卡片#{card.group_index} 槽位{slot_index}的图片")

    def _delete_task_card(self, card):
        if card in self._task_cards:
            card._anim_timer.stop()
            self._task_cards.remove(card)
            s = card.scene() if hasattr(card, 'scene') and callable(getattr(card, 'scene', None)) else None
            if s is None and hasattr(card, 'scene'):
                s = getattr(card, 'scene', None)
            if s:
                s.removeItem(card)
            card.deleteLater()
            
            self._update_indices()
            
            # ★ 如果所有任务都删完了，通知关闭
            if not self._task_cards:
                self.workflow_closed.emit()
                return
            
            self._reposition_all_cards()

    def clear_all(self):
        """★ v9.9: 彻底清空所有任务卡片和状态。
        
        注意：工具栏的显示/隐藏由 main_window 控制，引擎只管任务卡片。
        """
        for card in list(self._task_cards):
            card._anim_timer.stop()
            s = card.scene() if hasattr(card, 'scene') and callable(getattr(card, 'scene', None)) else None
            if s is None and hasattr(card, 'scene'):
                s = getattr(card, 'scene', None)
            if s:
                s.removeItem(card)
            card.deleteLater()
        self._task_cards.clear()
        self._is_running = False
        self.workflow_closed.emit()

    def _update_indices(self):
        for i, card in enumerate(self._task_cards, 1):
            card.update_index(i)

    def handle_dropped_images(self, file_paths, scene=None, template=None, anchor_pos=None):
        """★ v9.10: 将拖入的图片按分类独立计数填充到任务卡片。
        
        分类规则（以"模特+上衣+裤子"模板为例）：
          - 每个分类（slot_index）独立计数：模特=slot0, 上衣=slot1, 裤子=slot2
          - 图片按分类列依次填充：第1张→模特槽, 第2张→上衣槽, 第3张→裤子槽,
            第4张→新卡片的模特槽, 第5张→新卡片的上衣槽...
          - 某分类超当前行数时才新增行（卡片），保持连续无空缺
        
        Args:
            file_paths: 图片路径列表
            scene: 场景对象
            template: 模板配置（决定分类/槽数）
            anchor_pos: QPointF, 卡片定位锚点（工具栏底部场景坐标）
        """
        if not file_paths:
            return
        from PySide6.QtCore import QPointF

        tmpl = template or self._config.template or TEMPLATES[0]
        slot_count = tmpl["slots"]  # 分类数量（如3=模特/上衣/裤子）
        
        if slot_count <= 0:
            return

        # ── Step 1: 统计每个分类已有的图片数 ──
        # filled_counts[slot_idx] = 该分类已填的图片总数（跨所有卡片）
        filled_counts = [0] * slot_count
        for card in self._task_cards:
            for s in range(slot_count):
                if s < len(card.slots) and card.slots[s][0] is not None:
                    filled_counts[s] += 1

        # 当前各分类的行数（每行一张卡片，一行包含所有分类）
        row_count = max(len(self._task_cards), 1)

        # ── Step 2: 计算每个分类需要多少张新图才能填满 ──
        total_files = len(file_paths)
        
        # 按 round-robin 分配：文件轮流分配给各分类
        # 例如3分类时：f0→分类0, f1→分类1, f2→分类2, f3→分类0, f4→分类1...
        alloc = [[] for _ in range(slot_count)]  # alloc[slot] = [paths...]
        for i, fp in enumerate(file_paths):
            slot = i % slot_count
            alloc[slot].append(fp)

        # ── Step 3: 确定需要创建的新行数 ──
        new_rows_needed = 0
        for s in range(slot_count):
            existing = filled_counts[s]
            needed_for_this_slot = len(alloc[s])
            current_capacity = row_count  # 每行每分类1个槽
            remaining_after_existing = max(0, needed_for_this_slot - (current_capacity - existing))
            if remaining_after_existing > 0:
                extra_rows = (remaining_after_existing + 0) // 1  # 每多1个就多1行
                new_rows_needed = max(new_rows_needed, extra_rows)

        # ── Step 4: 先填现有卡片的空槽 ──
        # 构建一个"待填列表"：按 round-robin 顺序
        fill_queue = list(file_paths)
        fi = 0

        for card in self._task_cards:
            for s in range(slot_count):
                if fi >= len(fill_queue):
                    break
                if s < len(card.slots) and card.slots[s][0] is None:
                    card.set_slot_image(s, fill_queue[fi])
                    fi += 1
            if fi >= len(fill_queue):
                break

        # 剩余未分配的文件
        remaining = fill_queue[fi:]

        if not remaining:
            # 视口跟随到最后操作位置
            if self._task_cards and scene:
                last_card = self._task_cards[-1]
                view = None
                if hasattr(scene, 'views') and scene.views():
                    view = scene.views()[0]
                if view:
                    view.centerOn(last_card)
            return

        # ── Step 5: 创建新卡片并填充剩余图片 ──
        # 剩余图片继续按 round-robin 分配到新卡片
        ri = 0
        while ri < len(remaining):
            new_card = self.create_task_card(tmpl, scene=scene, anchor_pos=anchor_pos)
            for s in range(slot_count):
                if ri >= len(remaining):
                    break
                new_card.set_slot_image(s, remaining[ri])
                ri += 1

        # 视口跟随到最后新建的卡片
        if self._task_cards and scene:
            last_card = self._task_cards[-1]
            view = None
            if hasattr(scene, 'views') and scene.views():
                view = scene.views()[0]
            if view:
                view.centerOn(last_card)

    def execute_all(self, ratio, resolution, model, global_suffix):
        if self._is_running:
            return
        valid_groups = [c for c in self._task_cards if c.get_image_paths()]
        if not valid_groups:
            return False
        
        self._is_running = True
        
        for card in valid_groups:
            card_id = card.card_id
            threading.Thread(
                target=self._execute_single,
                args=(card_id, card, ratio, resolution, model, global_suffix),
                daemon=True
            ).start()
        return True

    def _execute_single(self, card_id, card, ratio, resolution, model, global_suffix):
        image_paths = card.get_image_paths()
        prompt = card.get_prompt(global_suffix)
        
        print(f"[工作流] 任务组 #{card.group_index} (card_id={card_id}): {len(image_paths)}张图, 模型: {model}")
        self._relay.card_status_signal.emit(card_id, "running", "生成中...")
        
        try:
            if not self._driver:
                raise Exception("AI 驱动未初始化")
            
            def progress_cb(msg, percent):
                try:
                    self._relay.card_status_signal.emit(card_id, "running", f"{msg} ({percent}%)")
                except Exception:
                    pass
            
            local_path = self._driver.generate_image_with_model(
                prompt, image_paths, ratio, resolution, model, callback=progress_cb
            )
            
            if local_path and os.path.isfile(local_path):
                print(f"[工作流] 任务组 #{card.group_index} 完成: {local_path}")
                self._relay.card_results_signal.emit(card_id, local_path)
            else:
                raise Exception("生成未返回有效路径")
        
        except Exception as e:
            import traceback
            print(f"[工作流] 任务组 #{card.group_index} 失败: {traceback.format_exc()}")
            try:
                self._relay.card_error_signal.emit(card_id, str(e)[:80])
            except Exception:
                pass

    # ════════════════════════════════════════════════════
    #  模特多动作执行逻辑（v10.0 新增）
    # ════════════════════════════════════════════════════
    def _on_multi_pose_generate_prompts(self, card):
        """★ v10.1: 生成多组动作提示词
        
        逻辑：
        1. 结合专家预设 + 产品卖点 + 参考图生成差异化动作提示词
        2. 提示词绑定同一参考图作为视觉参考
        """
        if not card._is_multi_pose:
            return
        
        ref_path = card.slots[0][0] if card.slots and card.slots[0][0] else None
        if not ref_path:
            return
        
        n_poses = card._pose_count
        expert_rules = card._pose_expert_prompt  # 专家预设
        extra_info = card._pose_extra_info        # 产品卖点
        
        def do_generate():
            try:
                # 构造提示词让 AI 生成多组动作
                base_prompt = (
                    f"你是一个专业的模特动作描述助手。请根据给定的服装参考图，"
                    f"生成 {n_poses} 个差异化的模特动作描述。\n"
                    f"要求：\n"
                    f"1. 每个动作要独特且多样化（不能重复相同姿势）\n"
                    f"2. 动作应自然、时尚、适合服装展示\n"
                    f"3. 每个描述控制在30-60字\n"
                    f"4. 格式：每行一个动作描述，不要编号\n"
                    f"5. 所有动作必须严格基于参考图的人物、服装、版型、风格\n"
                )
                # 添加专家预设规则
                if expert_rules:
                    base_prompt += f"\n【专家预设规则】{expert_rules}\n"
                # 添加产品卖点
                if extra_info:
                    base_prompt += f"\n【产品卖点/补充需求】{extra_info}\n"
                
                base_prompt += f"\n请直接输出 {n_poses} 个动作描述，每行一个："

                
                # 调用文本生成 API（使用 Grsai 或 Kie.ai 的文本模型）
                poses = self._generate_text_prompts(base_prompt, n_poses)
                
                if poses:
                    # 回填到卡片
                    QApplication.instance().processEvents()
                    card.set_generated_poses(poses)
                    print(f"[多动作] 生成 {len(poses)} 个动作提示词成功")
                else:
                    # 生成失败，使用默认提示词
                    fallback = [
                        f"参考模特图，以全身照展示服装，动作：标准站姿，双手自然下垂"
                        for _ in range(n_poses)
                    ]
                    card.set_generated_poses(fallback)
                    
            except Exception as e:
                import traceback
                print(f"[多动作] 生成提示词失败: {traceback.format_exc()}")
                card.set_multi_pose_generating(False)
        
        threading.Thread(target=do_generate, daemon=True).start()
    
    def _generate_text_prompts(self, prompt, count):
        """★ v10.0: 调用文本模型生成动作提示词"""
        try:
            # 优先使用 Grsai 平台（通常有更好的文本模型）
            if hasattr(self._driver, '_grsai_api_key') and self._driver._grsai_api_key:
                return self._generate_text_via_grsai(prompt, count)
            elif hasattr(self._driver, '_kie_api_key') and self._driver._kie_api_key:
                return self._generate_text_via_kie(prompt, count)
            else:
                print("[多动作] 未配置 API Key，无法生成提示词")
                return None
        except Exception as e:
            print(f"[多动作] 文本生成失败: {e}")
            return None
    
    def _generate_text_via_grsai(self, prompt, count):
        """★ v10.0: 通过 Grsai 平台生成文本"""
        import requests
        from .config_manager import ConfigManager
        config = ConfigManager()
        api_key = config.get("api_key", "")
        if not api_key:
            print("[多动作] Grsai API Key 未配置")
            return None
        
        base_url = "https://grsai.dakka.com.cn"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 800,
            "temperature": 0.9
        }
        resp = requests.post(f"{base_url}/v1/chat/completions", headers=headers, json=payload, timeout=60)
        data = resp.json()
        
        if resp.status_code != 200 or "error" in data:
            print(f"[多动作] Grsai 文本生成失败: {data}")
            return None
        
        text = data["choices"][0]["message"]["content"]
        # 解析每行动作
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        return lines[:count] if lines else None
    
    def _generate_text_via_kie(self, prompt, count):
        """★ v10.0: 通过 Kie.ai 平台生成文本（备用）"""
        import requests
        from .config_manager import ConfigManager
        config = ConfigManager()
        api_key = config.get("api_key", "")
        if not api_key:
            print("[多动作] Kie API Key 未配置")
            return None
        
        base_url = "https://api.kie.ai"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 800,
            "temperature": 0.9
        }
        resp = requests.post(f"{base_url}/v1/chat/completions", headers=headers, json=payload, timeout=60)
        data = resp.json()
        
        if resp.status_code != 200 or "error" in data:
            print(f"[多动作] Kie 文本生成失败: {data}")
            return None
        
        text = data["choices"][0]["message"]["content"]
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        return lines[:count] if lines else None
    
    def _on_multi_pose_execute(self, card):
        """★ v10.0: 执行多动作批量出图"""
        if not card._is_multi_pose:
            return
        
        # 检查必要条件
        ref_path = card.slots[0][0] if card.slots and card.slots[0][0] else None
        if not ref_path:
            parent = _get_top_widget()
            QMessageBox.warning(parent, "提示", "请先上传参考图")
            return
        
        valid_prompts = [p for p in card._generated_poses if p.strip()]
        if not valid_prompts:
            parent = _get_top_widget()
            QMessageBox.warning(parent, "提示", "请先生成或手动输入动作提示词")
            return
        
        if not self._driver:
            parent = _get_top_widget()
            QMessageBox.warning(parent, "错误", "AI 驱动未初始化")
            return
        
        # 获取模型
        model = card._selected_model
        ratio = self._config.ratio if self._config else 'auto'
        resolution = self._config.resolution if self._config else '2K'
        
        print(f"[多动作] 开始批量出图: {len(valid_prompts)} 张, 模型: {model}")
        
        # 启动多线程执行
        self._is_running = True
        card.set_status("running", f"生成中 0/{len(valid_prompts)}")
        
        threading.Thread(
            target=self._execute_multi_pose_thread,
            args=(card, ref_path, valid_prompts, model, ratio, resolution),
            daemon=True
        ).start()
    
    def _execute_multi_pose_thread(self, card, ref_path, prompts, model, ratio, resolution):
        """★ v10.0: 多动作批量出图线程"""
        total = len(prompts)
        results = [None] * total
        
        for i, prompt in enumerate(prompts):
            try:
                # 更新进度
                def progress_cb(msg, percent):
                    try:
                        card.set_status("running", f"生成中 {i+1}/{total} ({percent}%)")
                    except Exception:
                        pass
                
                # 调用图片生成
                local_path = self._driver.generate_image_with_model(
                    prompt, [ref_path], ratio, resolution, model, callback=progress_cb
                )
                
                if local_path and os.path.isfile(local_path):
                    results[i] = local_path
                    print(f"[多动作] 第 {i+1}/{total} 张完成: {local_path}")
                else:
                    print(f"[多动作] 第 {i+1}/{total} 生成失败")
                    
            except Exception as e:
                import traceback
                print(f"[多动作] 第 {i+1}/{total} 异常: {traceback.format_exc()}")
        
        # 回填结果到卡片
        def fill_results():
            pixmaps = []
            for path in results:
                if path and os.path.isfile(path):
                    px = QPixmap(path)
                    pixmaps.append(px if not px.isNull() else None)
                else:
                    pixmaps.append(None)
            
            card._result_pixmaps = pixmaps
            card._compute_multi_pose_geometry()
            card.prepareGeometryChange()
            
            # 更新状态
            success_count = sum(1 for p in results if p and os.path.isfile(p))
            if success_count == total:
                card.status = "success"
            elif success_count > 0:
                card.status = "success"  # 部分成功也标记为成功
            else:
                card.status = "error"
                card._error_msg = "全部生成失败"
            
            card.update()
            card.geometry_changed.emit(card)
            self._is_running = False
            
            print(f"[多动作] 批量出图完成: {success_count}/{total} 成功")
        
        # 在主线程中更新UI
        QApplication.instance().processEvents()
        fill_results()

    def _check_all_done(self):
        if not self._task_cards:
            return
        running = any(c.status == "running" for c in self._task_cards)
        if not running:
            success = sum(1 for c in self._task_cards if c.status == "success")
            error = sum(1 for c in self._task_cards if c.status == "error")
            total = success + error
            all_paths = []
            for c in self._task_cards:
                all_paths.extend(c.result_paths)
            self._relay.batch_done_signal.emit(success, error, total, all_paths)

    def _on_card_status(self, card_id, status, msg):
        for card in self._task_cards:
            if card.card_id == card_id:
                card.set_status(status, msg)
                break

    def _on_card_results(self, card_id, result_path):
        for card in self._task_cards:
            if card.card_id == card_id:
                card.show_result(result_path)
                break
        self._check_all_done()

    def _on_card_error(self, card_id, error_msg):
        for card in self._task_cards:
            if card.card_id == card_id:
                card.set_status("error", error_msg)
                break
        self._check_all_done()

    def _on_batch_done(self, success, error, total, result_paths):
        self._is_running = False
        msg = f"完成：成功 {success}，失败 {error}，共 {total} 组"
        print(f"[工作流] {msg}")
        
        if result_paths:
            # ★ 收集每张生成图对应的原始渲染图路径（用于创建对比图）
            compared_pairs = []
            for card in self._task_cards:
                if card.status == "success" and card.result_paths:
                    # 使用第一张上传的图作为原始渲染图
                    orig_path = None
                    for sp, _ in card.slots:
                        if sp:
                            orig_path = sp
                            break
                    for result_path in card.result_paths:
                        compared_pairs.append((result_path, orig_path))
            
            # 同时发送普通结果和对比图配对
            self.results_ready.emit(result_paths)
            if compared_pairs:
                self.results_compared.emit(compared_pairs)
