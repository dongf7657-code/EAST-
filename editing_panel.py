from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QComboBox, QTextEdit,
    QPushButton, QLabel, QSizePolicy, QRadioButton, QButtonGroup,
    QMessageBox
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QKeyEvent

# 导入统一模型注册表（与工作流面板共用）
import sys, os
_sys_path = os.path.dirname(os.path.abspath(__file__))
if _sys_path not in sys.path:
    sys.path.insert(0, _sys_path)
try:
    from workflow_panel import (
        ALL_MODELS, MODEL_ID_TO_NAME, MODEL_NAME_TO_ID, MODEL_IDS,
        get_models_for_platform, PLATFORM_KIE, PLATFORM_GRS,
        BUILTIN_MODELS  # 保留兼容
    )
except ImportError:
    # fallback：独立定义
    PLATFORM_KIE = "kie"
    PLATFORM_GRS = "grsai"
    ALL_MODELS = [
        {"id": "nano-banana-pro",  "name": "NanoBanana Pro",  "platforms": [PLATFORM_KIE, PLATFORM_GRS]},
        {"id": "nano-banana-2",    "name": "NanoBanana 2",   "platforms": [PLATFORM_KIE, PLATFORM_GRS]},
        {"id": "seedream-4.5",    "name": "Seedream 4.5",  "platforms": [PLATFORM_KIE]},
        {"id": "seedream-5.0-lite","name": "Seedream 5.0",  "platforms": [PLATFORM_KIE]},
        {"id": "gpt-image-2", "name": "GPT Image 2", "platforms": [PLATFORM_KIE, PLATFORM_GRS]},
    ]
    MODEL_ID_TO_NAME = {m["id"]: m["name"] for m in ALL_MODELS}
    MODEL_NAME_TO_ID = {m["name"]: m["id"] for m in ALL_MODELS}
    MODEL_IDS = [m["id"] for m in ALL_MODELS]
    BUILTIN_MODELS = MODEL_IDS

    def get_models_for_platform(platform):
        return [m for m in ALL_MODELS if platform in m["platforms"]]


class EditingPanel(QWidget):
    # ★ v9.13: signal 增加 model 参数
    generate_requested = Signal(str, str, str, str, str)  # prompt, aspect_ratio, resolution, image_path, model
    clear_image_requested = Signal()
    gen_count_changed = Signal(int)  # 生成数量变化信号

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._current_platform = PLATFORM_KIE  # 默认平台
        self._suppress_model_update = False    # 防止切换平台时死循环

        # ═══════════════════════════════════════════
        #  极简黑白灰面板
        # ═══════════════════════════════════════════
        self.setStyleSheet("""
            EditingPanel {
                background: rgba(255, 255, 255, 0.88);
                border: 0.5px solid rgba(0, 0, 0, 0.08);
                border-radius: 10px;
                padding: 8px 10px;
            }
            QComboBox {
                padding: 4px 8px;
                border: none;
                border-radius: 6px;
                background: rgba(0, 0, 0, 0.04);
                font-size: 11px;
                color: #333;
                min-width: 50px;
                font-weight: 500;
            }
            QComboBox:hover {
                background: rgba(0, 0, 0, 0.07);
            }
            QComboBox::drop-down {
                border: none;
                width: 16px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 3px solid transparent;
                border-right: 3px solid transparent;
                border-top: 4px solid #888;
            }
            QComboBox QAbstractItemView {
                background: rgba(255, 255, 255, 0.95);
                border: 0.5px solid rgba(0, 0, 0, 0.08);
                border-radius: 6px;
                padding: 4px;
                selection-background-color: rgba(0, 0, 0, 0.06);
                selection-color: #111;
                color: #333;
                outline: none;
                font-size: 11px;
            }
            QTextEdit {
                padding: 6px 10px;
                border: none;
                border-radius: 8px;
                background: rgba(0, 0, 0, 0.04);
                font-size: 12px;
                color: #333;
                selection-background-color: rgba(0, 0, 0, 0.08);
            }
            QTextEdit:focus {
                background: rgba(0, 0, 0, 0.06);
            }
            QPushButton#generateBtn {
                background: rgba(0, 0, 0, 0.06);
                color: #222;
                font-weight: 700;
                padding: 8px 16px;
                font-size: 12px;
                min-height: 48px;
                border-radius: 8px;
                border: none;
                letter-spacing: 1px;
            }
            QPushButton#generateBtn:hover {
                background: rgba(0, 0, 0, 0.10);
            }
            QPushButton#generateBtn:pressed {
                background: rgba(0, 0, 0, 0.04);
            }
            QLabel#imageLabel {
                color: #888;
                font-size: 10px;
                max-width: 90px;
            }
            QRadioButton {
                font-size: 11px;
                spacing: 4px;
                color: #666;
            }
            QRadioButton::indicator {
                width: 12px;
                height: 12px;
                border-radius: 6px;
                border: 1.5px solid #bbb;
                background: transparent;
            }
            QRadioButton::indicator:checked {
                border-color: #333;
                background: #333;
            }
        """)

        # ══ 外层竖向布局：上行(控件行) + 下行(提示词+生成) ══
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(6)

        # ── 上行：模型 | 比例 | 画质 | 生成数量 ──
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        # 合并模式标签（隐藏，功能由独立弹窗接管）
        self.merge_count_label = QLabel("")
        self.merge_count_label.setStyleSheet(
            "font-size: 10px; color: #888; font-weight: 600; padding: 2px 4px;"
            "background: rgba(0,0,0,0.04); border-radius: 4px;"
        )
        self.merge_count_label.hide()
        top_row.addWidget(self.merge_count_label)

        self.radio_single = QRadioButton("单独")
        self.radio_single.setStyleSheet("font-size: 10px; color: #666;")
        self.radio_merge = QRadioButton("合并")
        self.radio_merge.setStyleSheet("font-size: 10px; color: #666; font-weight: 600;")
        self.radio_single.setChecked(True)
        self.radio_single.hide()
        self.radio_merge.hide()

        self._radio_group = QButtonGroup(self)
        self._radio_group.addButton(self.radio_single)
        self._radio_group.addButton(self.radio_merge)

        top_row.addWidget(self.radio_single)
        top_row.addWidget(self.radio_merge)

        self._merge_sep = QLabel("·")
        self._merge_sep.setStyleSheet("color: #ccc; font-size: 12px; font-weight: bold;")
        self._merge_sep.hide()
        top_row.addWidget(self._merge_sep)

        # ★ v9.13: 平台选择器
        self.platform_combo = QComboBox()
        self.platform_combo.addItem("Kie.ai 平台", PLATFORM_KIE)
        self.platform_combo.addItem("Grsai 平台", PLATFORM_GRS)
        self.platform_combo.setFixedWidth(110)
        self.platform_combo.currentIndexChanged.connect(self._on_platform_changed)
        top_row.addWidget(self.platform_combo)

        # ★ v9.13: 模型选择器（根据平台动态灰禁）
        self.model_combo = QComboBox()
        self.model_combo.setFixedWidth(130)
        self._populate_models(PLATFORM_KIE)
        self.model_combo.currentIndexChanged.connect(self._on_model_selected)
        top_row.addWidget(self.model_combo)

        # 比例
        self.ratio_combo = QComboBox()
        self.ratio_combo.addItems(['auto', '1:1', '2:3', '3:2', '3:4', '4:3', '4:5', '5:4', '9:16', '16:9', '21:9'])
        top_row.addWidget(self.ratio_combo)

        # 画质
        self.res_combo = QComboBox()
        self.res_combo.addItems(['1K', '2K', '4K'])
        top_row.addWidget(self.res_combo)

        # ── 生成数量选择器 ──
        self.gen_count_label = QLabel("数量")
        self.gen_count_label.setStyleSheet(
            "font-size: 10px; color: #888; padding: 0px 2px; font-weight: 600;"
        )
        self.gen_count_label.setToolTip("生成数量")
        top_row.addWidget(self.gen_count_label)

        self.gen_count_combo = QComboBox()
        self.gen_count_combo.addItems(['1', '2', '3', '4', '5'])
        self.gen_count_combo.setFixedWidth(36)
        self.gen_count_combo.setStyleSheet("""
            QComboBox {
                font-size: 11px; padding: 3px 4px;
                border-radius: 5px;
            }
        """)
        self.gen_count_combo.currentIndexChanged.connect(self._on_gen_count_changed)
        top_row.addWidget(self.gen_count_combo)

        # 参考图标签（隐藏，保留功能但不再显示在面板上）
        self.img_label = QLabel("")
        self.img_label.setObjectName("imageLabel")
        self.img_label.hide()

        # 清除参考图按钮 — 已完全移除，不再创建

        top_row.addStretch()
        outer.addLayout(top_row)

        # ── 下行：多行提示词（拉满宽度）+ 生成按钮 ──
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(8)

        # 多行提示词输入框，Ctrl+Enter 触发生成
        self.prompt_input = _PromptTextEdit(self)
        self.prompt_input.setPlaceholderText("输入提示词…")
        self.prompt_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.prompt_input.setMinimumWidth(220)
        self.prompt_input.ctrl_enter_pressed.connect(self.on_generate)
        bottom_row.addWidget(self.prompt_input, 1)

        # 生成按钮 — 极简文字
        self.gen_btn = QPushButton("生成")
        self.gen_btn.setObjectName("generateBtn")
        self.gen_btn.setFixedWidth(72)
        self.gen_btn.clicked.connect(self.on_generate)
        bottom_row.addWidget(self.gen_btn)

        outer.addLayout(bottom_row)

        # 存储上传的图片路径
        self._uploaded_image_path = None

        # 自适应高度
        self.adjustSize()

    @property
    def merge_bar(self):
        return self

    def show(self):
        super().show()

    def hide(self):
        super().hide()

    def show_merge_bar(self):
        # 合并栏功能由独立 ModePopup 接管，这里保持隐藏
        self.merge_count_label.show()
        self.radio_single.hide()
        self.radio_merge.hide()
        self._merge_sep.hide()

    def hide_merge_bar(self):
        self.merge_count_label.hide()
        self.radio_single.hide()
        self.radio_merge.hide()
        self._merge_sep.hide()

    def set_image_path(self, path):
        self._uploaded_image_path = path
        # 保留内部状态，不再显示标签

    @property
    def gen_count(self):
        """获取当前选择的生成数量"""
        return int(self.gen_count_combo.currentText())

    def _on_gen_count_changed(self, index):
        """生成数量变化时发出信号"""
        self.gen_count_changed.emit(self.gen_count)

    def on_clear_image(self):
        self._uploaded_image_path = None
        self.clear_image_requested.emit()

    def on_generate(self):
        prompt = self.prompt_input.toPlainText().strip()
        if not prompt:
            return
        ratio = self.ratio_combo.currentText()
        res = self.res_combo.currentText()
        model = self._get_selected_model_id()
        self.generate_requested.emit(prompt, ratio, res, self._uploaded_image_path or "", model)

    def _get_selected_model_id(self):
        """★ v9.13: 获取当前选中的模型 ID"""
        return self.model_combo.currentData(Qt.UserRole) or "nano-banana-pro"

    def _on_platform_changed(self, index):
        """★ v9.13: 切换平台 → 重建模型列表 + 灰禁不符平台的模型"""
        platform = self.platform_combo.currentData(Qt.UserRole)
        if not platform:
            return
        self._suppress_model_update = True
        self._current_platform = platform
        self._populate_models(platform)
        self._suppress_model_update = False

    def _on_model_selected(self, index):
        """★ v9.13: 选中灰禁模型 → 弹窗提示"""
        if self._suppress_model_update:
            return
        data = self.model_combo.currentData(Qt.UserRole)
        if data is None:  # 灰禁项
            QMessageBox.warning(
                self,
                "模型不可用",
                "该模型未配置当前平台接口，请切换到对应中转平台后使用。\n\n"
                "• Seedream 系列 → Kie.ai 平台\n"
                "• NanoBanana / GPT Image → 两个平台均可"
            )
            # 回退到当前平台第一个可用模型
            available = get_models_for_platform(self._current_platform)
            if available:
                idx = self.model_combo.findData(available[0]["id"], Qt.UserRole)
                if idx >= 0:
                    self.model_combo.setCurrentIndex(idx)

    def _populate_models(self, platform):
        """★ v9.13: 根据平台填充模型列表，不支持的模型灰禁显示"""
        self.model_combo.blockSignals(True)
        self.model_combo.clear()

        available_models = get_models_for_platform(platform)

        for m in ALL_MODELS:
            name = m["name"]
            mid = m["id"]
            is_available = platform in m["platforms"]

            # 用 Qt.UserRole 存模型 ID
            self.model_combo.addItem(name, mid)

            if not is_available:
                # 设置为不可选择（灰色）
                model_index = self.model_combo.count() - 1
                self.model_combo.model().item(model_index).setEnabled(False)
                model = self.model_combo.model().item(model_index)
                model.setForeground(Qt.gray)

        # 默认选中当前平台第一个可用模型
        if available_models:
            idx = self.model_combo.findData(available_models[0]["id"], Qt.UserRole)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)

        self.model_combo.blockSignals(False)


# ── 支持 Ctrl+Enter 触发生成的多行输入框 ────────
class _PromptTextEdit(QTextEdit):
    ctrl_enter_pressed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(56)
        self.setAcceptRichText(False)
        self.setLineWrapMode(QTextEdit.WidgetWidth)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and (event.modifiers() & Qt.ControlModifier):
            self.ctrl_enter_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)
