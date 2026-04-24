import sys
import os
import shutil
import threading
import uuid
from PySide6.QtWidgets import (
    QMainWindow, QPushButton, QVBoxLayout, QWidget,
    QMessageBox, QFileDialog, QLabel, QDialog,
    QHBoxLayout, QGraphicsPixmapItem, QMenu, QFrame, QComboBox,
    QRadioButton, QButtonGroup
)
from PySide6.QtCore import Qt, QPoint, Signal, QTimer, QSize
from PySide6.QtGui import QPixmap, QIcon, QKeySequence, QAction, QFont
from infinite_canvas import (
    InfiniteCanvas, IMAGE_EXTS, VIDEO_EXTS,
    ImageItem, CompareItem, GeneratingItem, PlaceholderItem, VideoItem
)
from editing_panel import EditingPanel
from settings_dialog import SettingsDialog
from kie_ai_driver import KieAIDriver
from marker_tool import MarkerItem, MarkerToolbar
from workflow_panel import WorkflowEngine, CanvasTaskCard, WorkflowToolBar
from undo_manager import (
    UndoStack, AddItemsCommand, DeleteItemsCommand, MoveItemsCommand,
    AddMarkerCommand, DeleteMarkersCommand, BakeMarkersCommand,
    ReplaceGenCommand, _snapshot_item
)








# DeleteButton 已移除，改为右键菜单（见 InfiniteCanvas.contextMenuEvent）


# ──────────────────────────────────────────────
# 多图模式弹窗（独立小圆角弹窗，仅多图合并输入时显示）
# 极简黑白灰风格，与功能面板完全分离
# ──────────────────────────────────────────────
class ModePopup(QFrame):
    """仅多图选中时弹出，提供「单独生成」「合并生成」选项。"""

    mode_changed = Signal(str)  # "single" or "merge"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode_handler = None
        self.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.88);
                border: 0.5px solid rgba(0, 0, 0, 0.08);
                border-radius: 8px;
            }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self.btn_single = QPushButton("单独生成")
        self.btn_single.setCheckable(True)
        self.btn_single.setChecked(True)
        self.btn_single.setFixedHeight(26)
        self.btn_single.setToolTip("切换单独/合并生成模式")
        self.btn_single.setStyleSheet("""
            QPushButton {
                background: transparent; color: #999; border: none;
                border-radius: 5px; padding: 2px 10px;
                font-size: 11px; font-weight: 600;
            }
            QPushButton:checked {
                background: rgba(0, 0, 0, 0.06); color: #222;
            }
            QPushButton:hover { color: #333; }
        """)
        self.btn_single.clicked.connect(lambda: self._set_mode("single"))
        layout.addWidget(self.btn_single)

        self.btn_merge = QPushButton("合并生成")
        self.btn_merge.setCheckable(True)
        self.btn_merge.setFixedHeight(26)
        self.btn_merge.setToolTip("切换单独/合并生成模式")
        self.btn_merge.setStyleSheet("""
            QPushButton {
                background: transparent; color: #999; border: none;
                border-radius: 5px; padding: 2px 10px;
                font-size: 11px; font-weight: 600;
            }
            QPushButton:checked {
                background: rgba(0, 0, 0, 0.06); color: #222;
            }
            QPushButton:hover { color: #333; }
        """)
        self.btn_merge.clicked.connect(lambda: self._set_mode("merge"))
        layout.addWidget(self.btn_merge)

        self.adjustSize()

    def set_mode_handler(self, handler):
        self._mode_handler = handler

    def _set_mode(self, mode):
        self.btn_single.setChecked(mode == "single")
        self.btn_merge.setChecked(mode == "merge")
        if self._mode_handler:
            self._mode_handler(mode)
        self.mode_changed.emit(mode)

    def set_mode(self, mode):
        """程序化设置模式，不触发 handler"""
        self.btn_single.setChecked(mode == "single")
        self.btn_merge.setChecked(mode == "merge")

    def update_pos(self, items, canvas):
        """定位在多图选中区域正上方居中"""
        if not items:
            self.hide()
            return
        min_x = float('inf')
        max_x = float('-inf')
        min_y = float('inf')
        for item in items:
            r = item.sceneBoundingRect()
            min_x = min(min_x, r.left())
            max_x = max(max_x, r.right())
            min_y = min(min_y, r.top())

        center_x = (min_x + max_x) / 2
        top_y = min_y - 12
        view_pos = canvas.mapFromScene(QPoint(int(center_x), int(top_y)))
        x = view_pos.x() - self.width() // 2
        y = max(4, view_pos.y() - self.height())
        self.move(x, y)
        self.show()
        self.raise_()


# ──────────────────────────────────────────────
# MainWindow — 支持异步多任务并发
# ──────────────────────────────────────────────
class MainWindow(QMainWindow):
    task_progress_signal = Signal(str, str)
    task_finished_signal = Signal(str, str)
    task_error_signal = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("EastAIstudio")
        self.resize(1280, 800)

        # ── 窗口图标（任务栏 + 标题栏系统图标）─────
        _icon_path = self._resolve_icon_path("icon.ico")
        if _icon_path:
            self.setWindowIcon(QIcon(_icon_path))

        self.canvas = InfiniteCanvas(self)
        self.setCentralWidget(self.canvas)

        # ── 全局撤销栈 ──────────────────────
        self.undo_stack = UndoStack()
        import infinite_canvas
        infinite_canvas.set_undo_stack(self.undo_stack)

        self.driver = KieAIDriver()

        # ── 任务管理 ────────────────────────────
        self._active_tasks = {}
        self._task_lock = threading.Lock()

        # ── 标记模式 ────────────────────────────
        self._marker_mode = False      # 是否处于标记模式
        self._marker_target = None     # 当前标记的图片 item

        # ── 左下角设置按钮 ──────────────────────
        self.settings_btn = QPushButton("设置", self.canvas)
        self.settings_btn.setFixedSize(48, 28)
        self.settings_btn.setToolTip("设置")
        self.settings_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.78);
                border: 0.5px solid rgba(0, 0, 0, 0.08);
                border-radius: 6px;
                font-size: 11px; font-weight: 600;
                color: #555;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.92); color: #222; }
            QPushButton:pressed { background: rgba(240, 240, 242, 0.95); }
        """)
        self.settings_btn.clicked.connect(self.open_settings)

        # ── 工作流按钮（左下角，设置按钮右侧）─────
        self.workflow_btn = QPushButton("工作流", self.canvas)
        self.workflow_btn.setFixedSize(56, 28)
        self.workflow_btn.setToolTip("批量工作流（配置条模式）")
        self.workflow_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.78);
                border: 0.5px solid rgba(0, 0, 0, 0.08);
                border-radius: 6px;
                font-size: 11px; font-weight: 600;
                color: #555;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.92); color: #222; }
            QPushButton:pressed { background: rgba(240, 240, 242, 0.95); }
        """)
        self.workflow_btn.clicked.connect(self._toggle_workflow)

        # ── 工作流执行引擎（v9.8: 浮动工具栏）──
        self.workflow_engine = WorkflowEngine(self.canvas)
        self.workflow_engine.set_driver(self.driver)
        # 生成结果信号 → 放入画布
        self.workflow_engine.results_ready.connect(self._on_workflow_results_to_canvas)
        self.workflow_engine.results_compared.connect(self._on_workflow_results_compared)
        self.workflow_engine.workflow_closed.connect(self._on_workflow_closed)

        # ── 工作流工具栏（v9.9: 固定尺寸浮动控件，parent=self 绕开 QGraphicsView 事件拦截）──
        self.workflow_toolbar = WorkflowToolBar(self.workflow_engine.config, self)
        self.workflow_toolbar.hide()
        # 信号连接
        self.workflow_toolbar.add_task_requested.connect(self._add_workflow_task)
        self.workflow_toolbar.execute_all_requested.connect(self._execute_all_workflow)
        self.workflow_toolbar.close_requested.connect(self._close_workflow)

        # ── 导入按钮（小图标，支持图片+视频）────
        self.import_btn = QPushButton("⊕", self.canvas)
        self.import_btn.setFixedSize(36, 36)
        self.import_btn.setToolTip("导入图片或视频")
        self.import_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.78);
                border: 0.5px solid rgba(0, 0, 0, 0.08);
                border-radius: 8px;
                font-size: 16px;
                color: #555;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.92); color: #222; }
            QPushButton:pressed { background: rgba(240, 240, 242, 0.95); }
        """)
        self.import_btn.clicked.connect(self.on_import_files)

        # ── 模式切换浮动弹窗（仅多图时显示）──────
        self._mode_popup = ModePopup(self.canvas)
        self._mode_popup.set_mode_handler(self._on_mode_popup_changed)
        self._mode_popup.hide()

        # ── 底部浮动面板 ───────────────────────
        self.editing_panel = EditingPanel(self.canvas)
        self.editing_panel.hide()
        self.editing_panel.generate_requested.connect(self.start_generation)
        self.editing_panel.clear_image_requested.connect(self.on_clear_ref_image)

        # ── 标记工具栏 ───────────────────────
        self.marker_toolbar = MarkerToolbar(self.canvas)
        self.marker_toolbar.hide()
        self.marker_toolbar.marker_added.connect(self._on_marker_changed)
        self.marker_toolbar.replace_requested.connect(self._on_marker_replace)

        # ── 信号连接 ────────────────────────────
        self.canvas.image_selected.connect(self.show_editing_panel)
        self.canvas.multi_selected.connect(self.on_multi_selected)
        self.canvas.selection_cleared.connect(self.hide_all_panels)
        self.canvas.files_dropped.connect(self._on_files_dropped)
        self.canvas.item_right_clicked.connect(self._on_item_right_clicked)
        self.task_progress_signal.connect(self._on_task_progress)
        self.task_finished_signal.connect(self._on_task_finished)
        self.task_error_signal.connect(self._on_task_error)

        # 双击菜单
        self.canvas.scene.mouseDoubleClickEvent = self._scene_double_click

        # 标记模式：拦截画布点击
        self._orig_canvas_mouse_press = self.canvas.mousePressEvent
        self.canvas.mousePressEvent = self._canvas_mouse_press_hook

        self.current_target_item = None
        self._multi_selected_items = []

        # ── 快捷键 ──────────────────────────────
        from PySide6.QtGui import QShortcut
        QShortcut(QKeySequence(Qt.Key_Delete), self).activated.connect(self.on_delete_selected)
        QShortcut(QKeySequence("Ctrl+A"), self).activated.connect(self.canvas.select_all)
        QShortcut(QKeySequence("Ctrl+C"), self).activated.connect(self.canvas.copy_selected)
        QShortcut(QKeySequence("Ctrl+V"), self).activated.connect(self.on_paste)
        QShortcut(QKeySequence("M"), self).activated.connect(self.toggle_marker_mode)
        QShortcut(QKeySequence("Ctrl+Z"), self).activated.connect(self.on_undo)

        # ── 浮动控件位置更新定时器 ──────────────
        self._toolbar_timer = QTimer()
        self._toolbar_timer.setInterval(50)
        self._toolbar_timer.timeout.connect(self._update_float_positions)
        self._toolbar_timer.start()

    # ── 工具 ────────────────────────────────────
    @staticmethod
    def _resolve_icon_path(filename: str) -> str:
        """兼容 PyInstaller 打包后的路径查找"""
        # 开发模式：相对于 main.py 所在目录
        base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
        full = os.path.join(base, filename)
        if os.path.isfile(full):
            return full
        # 再尝试 EXE 同级目录
        exe_dir = os.path.dirname(sys.executable)
        full2 = os.path.join(exe_dir, filename)
        if os.path.isfile(full2):
            return full2
        return ""

    # ── 布局 ────────────────────────────────────
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.settings_btn.move(20, self.height() - 50)
        self.import_btn.move(20, self.height() // 2 - 20)
        self.workflow_btn.move(78, self.height() - 50)
        # ★ v9.9: 窗口大小改变时重新定位工作流工具栏（保持顶部居中）
        if self.workflow_toolbar.isVisible():
            self._position_workflow_toolbar()

    def _update_float_positions(self):
        if self.current_target_item and not self._multi_selected_items:
            self.update_editing_panel_pos()
        if self._multi_selected_items:
            self.update_editing_panel_pos_multi()
            self._mode_popup.update_pos(self._multi_selected_items, self.canvas)
        if self.marker_toolbar.isVisible():
            self._update_marker_toolbar_pos()
        # ★ v9.9: 持续钉住工具栏位置（防止画布滚动/缩放导致偏移）
        if self.workflow_toolbar.isVisible():
            self._position_workflow_toolbar()

    # ── 设置 ────────────────────────────────────
    def open_settings(self):
        dlg = SettingsDialog(self)
        dlg.exec()

    # ── 导入文件（改为横向排列）────────────────
    def on_import_files(self):
        img_exts = " ".join(f"*{e}" for e in sorted(IMAGE_EXTS))
        vid_exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTS))
        all_exts = f"{img_exts} {vid_exts}"

        file_paths, _ = QFileDialog.getOpenFileNames(
            self, "选择图片/视频导入画布（可多选）", "",
            f"所有媒体文件 ({all_exts});;图片文件 ({img_exts});;视频文件 ({vid_exts})"
        )
        if not file_paths:
            return

        added = self.canvas.add_files_in_row(file_paths)
        if added:
            self.statusBar().showMessage(f"已导入 {len(added)} 个文件", 3000)
            self.canvas.scene.clearSelection()
            for item in added:
                item.setSelected(True)
            # 撤销：撤销导入 = 从画布移除这些 item
            self.undo_stack.push(AddItemsCommand(
                added, self.canvas.scene,
                description=f"导入 {len(added)} 个文件"
            ))

    def _on_files_dropped(self, file_paths):
        self.statusBar().showMessage(f"已拖入 {len(file_paths)} 个文件", 3000)

    # ── 粘贴（包装 canvas.paste，加入撤销支持）────
    def on_paste(self):
        new_items = self.canvas.paste()
        if new_items:
            self.undo_stack.push(AddItemsCommand(
                new_items, self.canvas.scene,
                description=f"粘贴 {len(new_items)} 个图片"
            ))

    # ── 全局撤销（Ctrl+Z）────────────────────────
    def on_undo(self):
        if self.undo_stack.is_empty():
            self.statusBar().showMessage("没有可撤销的操作", 1500)
            return
        desc = self.undo_stack.peek_description()
        self.undo_stack.undo()
        self.statusBar().showMessage(f"已撤销: {desc}", 2000)

    # ── 单图选中 ────────────────────────────────
    def show_editing_panel(self, item, bottom_center_scene_pos):
        self.current_target_item = item
        self._multi_selected_items = []

        if item and hasattr(item, 'path') and item.path:
            self.editing_panel.set_image_path(item.path)
        else:
            self.editing_panel.set_image_path(None)

        self.editing_panel.hide_merge_bar()
        self.editing_panel.show()
        self.update_editing_panel_pos()

    def hide_all_panels(self):
        self.current_target_item = None
        self.editing_panel.hide()
        self._mode_popup.hide()
        self._multi_selected_items = []
        self.canvas.clear_merge_orders()

    def update_editing_panel_pos(self):
        if not self.current_target_item:
            return
        rect = self.current_target_item.sceneBoundingRect()
        bottom_center = QPoint(int(rect.center().x()), int(rect.bottom() + 12))
        view_pos = self.canvas.mapFromScene(bottom_center)
        panel_x = view_pos.x() - self.editing_panel.width() // 2
        panel_y = view_pos.y()
        self.editing_panel.move(max(10, panel_x), min(panel_y, self.height() - self.editing_panel.height() - 10))

    def update_editing_panel_pos_multi(self):
        if not self._multi_selected_items:
            return
        min_x = float('inf')
        max_x = float('-inf')
        min_y = float('inf')
        max_y = float('-inf')
        for item in self._multi_selected_items:
            r = item.sceneBoundingRect()
            min_x = min(min_x, r.left())
            max_x = max(max_x, r.right())
            min_y = min(min_y, r.top())
            max_y = max(max_y, r.bottom())

        center_x = (min_x + max_x) / 2
        bottom_y = max_y + 12

        view_pos = self.canvas.mapFromScene(QPoint(int(center_x), int(bottom_y)))
        panel_x = view_pos.x() - self.editing_panel.width() // 2
        panel_y = view_pos.y()
        self.editing_panel.move(max(10, panel_x), min(panel_y, self.height() - self.editing_panel.height() - 10))

    def on_clear_ref_image(self):
        if self.current_target_item:
            self.current_target_item.path = None

    # ── 多图选中 ────────────────────────────────
    def on_multi_selected(self, ordered_items):
        self._multi_selected_items = ordered_items

        if len(ordered_items) > 1:
            self.canvas.mark_merge_orders(ordered_items)
            self.editing_panel.set_image_path(None)
            # 隐藏 editing_panel 内的合并栏，改用独立弹窗
            self.editing_panel.hide_merge_bar()
            # 多选时自动切换为合并生成模式
            self.editing_panel.radio_merge.setChecked(True)
            self._mode_popup.set_mode("merge")
            self._mode_popup.update_pos(ordered_items, self.canvas)
            self.editing_panel.show()
            self.update_editing_panel_pos_multi()
        else:
            self.canvas.clear_merge_orders()
            self._mode_popup.hide()

    def _on_mode_popup_changed(self, mode):
        """ModePopup 模式切换回调"""
        if mode == "single":
            self.editing_panel.radio_single.setChecked(True)
        else:
            self.editing_panel.radio_merge.setChecked(True)

    # ── 删除 ────────────────────────────────────
    def on_delete_selected(self):
        snapshots = self.canvas.delete_selected()
        if snapshots:
            # 分离 MarkerItem 快照和普通 item 快照
            marker_snaps = []
            item_snaps = []
            for snap in snapshots:
                if snap.get('type') == 'MarkerItem':
                    marker_snaps.append(snap)
                else:
                    item_snaps.append(snap)

            # 普通 item 删除的撤销（item 快照中已包含子 MarkerItem 信息）
            if item_snaps:
                self.undo_stack.push(DeleteItemsCommand(
                    item_snaps, self.canvas.scene,
                    description=f"删除 {len(item_snaps)} 个图片"
                ))

            # 独立标记点删除的撤销（仅当标记被单独选中删除时）
            if marker_snaps:
                for ms in marker_snaps:
                    parent = ms.get('parent_item')
                    if parent and parent.scene():  # 父 item 还在场景中
                        self.undo_stack.push(DeleteMarkersCommand(
                            [ms['marker']], parent, self.marker_toolbar,
                            description=f"删除标记 {ms['marker'].get('label', '?')}"
                        ))

        self.hide_all_panels()

    # ══════════════════════════════════════════════
    #  异步任务管理
    # ══════════════════════════════════════════════
    def _new_task_id(self):
        return uuid.uuid4().hex[:8]

    def _active_task_count(self):
        with self._task_lock:
            return len(self._active_tasks)

    # ── 生成入口 ────────────────────────────────

    @staticmethod
    def _ratio_to_size(ratio_str, base_size=512):
        """根据比例字符串（如 '1:1', '4:5', 'auto'）返回 (w, h) 像素尺寸。
        auto 模式返回 (base_size, base_size)。"""
        if ratio_str and ratio_str != 'auto' and ':' in ratio_str:
            try:
                rw, rh = ratio_str.split(':')
                rw, rh = int(rw), int(rh)
                # 短边 = base_size，长边按比例放大
                if rw >= rh:
                    w = base_size
                    h = max(int(base_size * rh / rw), 100)
                else:
                    h = base_size
                    w = max(int(base_size * rw / rh), 100)
                return w, h
            except (ValueError, ZeroDivisionError):
                pass
        return base_size, base_size

    def start_generation(self, prompt, ratio, res, image_path="", model=None):
        # 检测是否有带标记的图片（退出标记模式时已渲染到图片上）
        has_markers = False
        target_item = self.current_target_item
        if target_item and hasattr(target_item, 'has_markers'):
            has_markers = target_item.has_markers

        print(f"[生成入口] target_item path={getattr(target_item, 'path', None)}, has_markers={has_markers}, model={model}")

        if has_markers:
            # 自动追加标记提示词
            marker_suffix = "请根据图中数字标记位置进行内容替换，最终生成的图片不要出现任何标记点和数字"
            if marker_suffix not in prompt:
                prompt = f"{prompt}。{marker_suffix}"
                # 同步更新提示词输入框
                self.editing_panel.prompt_input.setPlainText(prompt)
                print(f"[标记] 已自动追加提示词: {marker_suffix}")

        if self.editing_panel.radio_merge.isChecked() and len(self._multi_selected_items) > 1:
            # 合并生成：所有图片合为一张，占位框排在最右侧
            self._execute_merge_generate(self._multi_selected_items, prompt, ratio, res, model)
        elif len(self._multi_selected_items) > 1:
            # 多图单独生成：每张图片各自生成一张，全部排在最右侧
            self._execute_multi_single_generate(self._multi_selected_items, prompt, ratio, res, model)
        else:
            # 单图生成
            self._start_single_generation(prompt, ratio, res, image_path, model)

    def _start_single_generation(self, prompt, ratio, res, image_path="", model=None):
        orig_path = None
        if self.current_target_item and hasattr(self.current_target_item, 'path'):
            orig_path = self.current_target_item.path

        # 确定提交给 API 的图片路径
        # 优先级：1) 手动上传的参考图 2) 画布图片的路径（可能已带标记渲染）
        effective_image_path = image_path if image_path else orig_path

        # ── 如果处于标记模式，先渲染带标记的临时图片再提交 ──
        if self._marker_mode and self.current_target_item and not image_path:
            self.marker_toolbar.set_target_item(self.current_target_item)
            if self.marker_toolbar._markers:
                rendered = self.marker_toolbar.render_markers_to_image(orig_path)
                if rendered and rendered != orig_path:
                    effective_image_path = rendered
                    # 自动追加标记模式提示词
                    marker_suffix = "请根据图中数字标记位置进行内容替换，最终生成的图片不要出现任何标记点和数字"
                    if marker_suffix not in prompt:
                        prompt = prompt + "。" + marker_suffix
                    print(f"[单图生成] 标记模式内渲染带标记图片: {rendered}")

        # ── 批量生成：根据数量创建多个占位框和任务 ──
        gen_count = self.editing_panel.gen_count
        GAP = 20

        # 计算首个占位框位置和尺寸
        first_x, first_y, ph_w, ph_h = self._calc_placeholder_pos()

        for i in range(gen_count):
            task_id = self._new_task_id()
            ph_x = first_x + i * (ph_w + GAP)

            placeholder = self.canvas.add_placeholder(ph_x, first_y, ph_w, ph_h, task_id)
            placeholder.set_progress(0, "准备生成…")

            with self._task_lock:
                self._active_tasks[task_id] = {
                    "placeholder": placeholder,
                    "orig_path": orig_path,
                }

            threading.Thread(
                target=self._generate_thread,
                args=(task_id, prompt, ratio, res, effective_image_path, placeholder, None, model),
                daemon=True
            ).start()

        print(f"[单图生成] orig_path={orig_path}, effective_image_path={effective_image_path}, 批量数量={gen_count}, model={model}")

        self._update_gen_btn_state()
        count_msg = f"已提交 {gen_count} 个任务" if gen_count > 1 else f"任务 {task_id} 已提交"
        self.statusBar().showMessage(f"{count_msg}，共 {self._active_task_count()} 个任务进行中")

    def _calc_placeholder_pos(self):
        """
        单图模式：占位框位置在原图右侧。
        占位框的尺寸严格按渲染图（带标记图）的原始像素比例，不随生成比例而变形。
        目的：预览区域始终与渲染图等大，生成结果居中填入，白色填充仅作预览。
        """
        GAP = 20
        DEFAULT_W, DEFAULT_H = 512, 512

        # 确保 current_target_item 有效且仍在场景中
        item = self.current_target_item
        if item and item.scene() is not None:
            rect = item.sceneBoundingRect()
            x = rect.right() + GAP
            y = rect.top()
            # 占位框尺寸 = 渲染图在画布上的实际尺寸（保持原始比例）
            w = int(rect.width())
            h = int(rect.height())
            print(f"[占位框] item={item}, rect={rect.x():.0f},{rect.y():.0f} {rect.width():.0f}x{rect.height():.0f} → 占位框({x:.0f},{y:.0f}) {w}x{h}")
            return x, y, max(w, 100), max(h, 100)

        # 无参考图时：按生成比例计算，放在当前视口中央
        print(f"[占位框] current_target_item={item}（无效），改用视口中心位置")
        ratio_str = self.editing_panel.ratio_combo.currentText()
        w, h = self._ratio_to_size(ratio_str, base_size=DEFAULT_W)
        view_center = self.canvas.viewport().rect().center()
        scene_center = self.canvas.mapToScene(view_center)
        return scene_center.x() + GAP, scene_center.y() - h/2, w, h

    # ── 多图单独生成（统一排在最右侧）──────────
    def _execute_multi_single_generate(self, items, prompt, ratio, res, model=None):
        """
        多图选中 + 单独生成模式：
        每张图片独立生成，所有占位框从当前最右侧开始横向排列，不插入原有图片中间。
        支持批量数量：每张图片生成 gen_count 张结果。
        """
        GAP = 20
        gen_count = self.editing_panel.gen_count

        # 标记模式下自动追加提示词
        if self._marker_mode:
            marker_suffix = "请根据图中数字标记位置进行内容替换，最终生成的图片不要出现任何标记点和数字"
            if marker_suffix not in prompt:
                prompt = prompt + "。" + marker_suffix

        # 收集所有需要生成的 (orig_path, effective_image_path) 对
        tasks_info = []
        for item in items:
            orig_path = item.path if hasattr(item, 'path') else None
            if orig_path is None:
                print(f"[多图单独生成] 跳过无路径的 item")
                continue

            effective_image_path = orig_path
            # ── 如果处于标记模式，先渲染带标记的临时图片 ──
            if self._marker_mode:
                self.marker_toolbar.set_target_item(item)
                if self.marker_toolbar._markers:
                    rendered = self.marker_toolbar.render_markers_to_image(orig_path)
                    if rendered and rendered != orig_path:
                        effective_image_path = rendered
                        print(f"[多图单独生成] 标记模式内渲染: {rendered}")

            tasks_info.append((orig_path, effective_image_path, item))

        # 计算所有占位框的位置（每张图 × gen_count 个占位框）
        cursor_x = self.canvas.get_rightmost_x() + GAP
        total_tasks = 0

        for orig_path, effective_image_path, item in tasks_info:
            rect = item.sceneBoundingRect()
            ph_w = max(int(rect.width()), 256)
            ph_h = max(int(rect.height()), 256)
            ph_y = rect.top()

            for _ in range(gen_count):
                task_id = self._new_task_id()
                ph_x = cursor_x
                cursor_x += ph_w + GAP

                placeholder = self.canvas.add_placeholder(ph_x, ph_y, ph_w, ph_h, task_id)
                placeholder.set_progress(0, "准备生成…")

                with self._task_lock:
                    self._active_tasks[task_id] = {
                        "placeholder": placeholder,
                        "orig_path": orig_path,
                    }

                threading.Thread(
                    target=self._generate_thread,
                    args=(task_id, prompt, ratio, res, effective_image_path, placeholder, None, model),
                    daemon=True
                ).start()
                total_tasks += 1

        self._update_gen_btn_state()
        self.statusBar().showMessage(f"已提交 {total_tasks} 个独立生成任务，共 {self._active_task_count()} 个任务进行中")

    def _generate_thread(self, task_id, prompt, ratio, res, image_path, placeholder, mask_coords=None, model=None):
        def progress_callback(msg, percent):
            self.task_progress_signal.emit(task_id, msg)

        try:
            print(f"[生成线程 {task_id}] 开始，提示词: {prompt}, 比例: {ratio}, 画质: {res}, 参考图: {image_path}, model: {model}")
            local_path = self.driver.generate_image(
                prompt, image_path, ratio, res, progress_callback, mask_coords=mask_coords, model=model
            )
            print(f"[生成线程 {task_id}] 返回路径: {local_path}")
            if not local_path:
                self.task_error_signal.emit(task_id, "生成完成但未返回文件路径，请检查API响应")
                return
            if not os.path.isfile(local_path):
                self.task_error_signal.emit(task_id, f"文件不存在: {local_path}")
                return
            print(f"[生成线程 {task_id}] 文件大小: {os.path.getsize(local_path)} 字节")
            self.task_finished_signal.emit(task_id, local_path)
        except Exception as e:
            import traceback
            err = traceback.format_exc()
            print(f"[生成线程 {task_id}] 异常:\n{err}")
            self.task_error_signal.emit(task_id, str(e))

    # ── 合并生成（统一排在最右侧）──────────────
    def _execute_merge_generate(self, items, prompt, ratio, res, model=None):
        gen_count = self.editing_panel.gen_count

        image_paths = []
        for i, item in enumerate(items, 1):
            path = item.path if hasattr(item, 'path') else None
            if path:
                # ── 如果处于标记模式，先渲染带标记的临时图片 ──
                effective_path = path
                if self._marker_mode:
                    self.marker_toolbar.set_target_item(item)
                    if self.marker_toolbar._markers:
                        rendered = self.marker_toolbar.render_markers_to_image(path)
                        if rendered and rendered != path:
                            effective_path = rendered
                            print(f"[合并生成] 图{i} 标记模式内渲染: {rendered}")
                image_paths.append(effective_path)
            else:
                print(f"[合并生成] 图{i} 无路径，跳过")

        if not image_paths:
            QMessageBox.warning(self, "提示", "选中的图片没有有效的文件路径")
            return

        # 标记模式下自动追加提示词
        if self._marker_mode:
            marker_suffix = "请根据图中数字标记位置进行内容替换，最终生成的图片不要出现任何标记点和数字"
            if marker_suffix not in prompt:
                prompt = prompt + "。" + marker_suffix

        # 占位框排在画布最右侧，尺寸跟随输出比例
        GAP = 20
        last_rect = items[-1].sceneBoundingRect()
        base = int(max(last_rect.width(), last_rect.height()))
        ph_w, ph_h = self._ratio_to_size(ratio, base_size=max(base, 256))
        ph_y = last_rect.top()
        cursor_x = self.canvas.get_rightmost_x() + GAP

        orig_path = items[0].path if hasattr(items[0], 'path') else None

        # 批量生成：创建 gen_count 个占位框
        for i in range(gen_count):
            task_id = self._new_task_id()
            ph_x = cursor_x
            cursor_x += ph_w + GAP

            placeholder = self.canvas.add_placeholder(ph_x, ph_y, ph_w, ph_h, task_id)
            placeholder.set_progress(0, "准备合并生成…")

            with self._task_lock:
                self._active_tasks[task_id] = {
                    "placeholder": placeholder,
                    "orig_path": orig_path,
                }

            threading.Thread(
                target=self._merge_generate_thread,
                args=(task_id, prompt, ratio, res, image_paths, placeholder, model),
                daemon=True
            ).start()

        self._update_gen_btn_state()
        total = gen_count
        count_msg = f"已提交 {total} 个合并任务" if total > 1 else f"合并任务已提交"
        self.statusBar().showMessage(f"{count_msg}，共 {self._active_task_count()} 个任务进行中")

    def _merge_generate_thread(self, task_id, prompt, ratio, res, image_paths, placeholder, model=None):
        def progress_callback(msg, percent):
            self.task_progress_signal.emit(task_id, msg)

        try:
            print(f"[合并生成线程 {task_id}] 开始，图片数: {len(image_paths)}, model: {model}")
            local_path = self.driver.generate_image_multi(
                prompt, image_paths, ratio, res, progress_callback, model=model
            )
            print(f"[合并生成线程 {task_id}] 返回路径: {local_path}")
            if not local_path:
                self.task_error_signal.emit(task_id, "合并生成完成但未返回文件路径")
                return
            if not os.path.isfile(local_path):
                self.task_error_signal.emit(task_id, f"文件不存在: {local_path}")
                return
            print(f"[合并生成线程 {task_id}] 文件大小: {os.path.getsize(local_path)} 字节")
            self.task_finished_signal.emit(task_id, local_path)
        except Exception as e:
            import traceback
            err = traceback.format_exc()
            print(f"[合并生成线程 {task_id}] 异常:\n{err}")
            self.task_error_signal.emit(task_id, str(e))

    # ── 信号槽 ──────────────────────────────────
    def _on_task_progress(self, task_id, msg):
        with self._task_lock:
            task = self._active_tasks.get(task_id)
        if not task:
            return
        placeholder = task.get("placeholder")
        if placeholder and placeholder.scene():
            percent = self._extract_percent(msg)
            placeholder.set_progress(percent, msg)
        count = self._active_task_count()
        self.statusBar().showMessage(f"[{task_id}] {msg}  |  共 {count} 个任务进行中")

    def _extract_percent(self, msg):
        try:
            import re
            m = re.search(r'(\d+)%', msg)
            if m:
                return int(m.group(1))
        except Exception:
            pass
        if "上传" in msg:
            return 10
        if "创建" in msg or "排队" in msg:
            return 20
        if "生成成功" in msg or "下载" in msg:
            return 90
        return 50

    def _on_task_finished(self, task_id, local_path):
        print(f"[主线程] 任务 {task_id} 生成成功，路径: {local_path}")

        with self._task_lock:
            task = self._active_tasks.pop(task_id, None)
        if not task:
            return

        placeholder = task.get("placeholder")
        orig_path = task.get("orig_path")

        result_item, placeholder_snap = self.canvas.replace_with_compare(placeholder, orig_path, local_path)

        if result_item:
            result_item.setSelected(True)
            # 记录生成替换的撤销
            self.undo_stack.push(ReplaceGenCommand(
                result_item, placeholder_snap, local_path, orig_path,
                self.canvas.scene,
                description=f"生成图片 ({task_id})"
            ))
            self.statusBar().showMessage(
                f"任务 {task_id} 完成！拖动分割线对比效果  |  剩余 {self._active_task_count()} 个任务", 6000
            )
        else:
            QMessageBox.warning(self, "提示", f"图片已生成但无法加载到画布:\n{local_path}")

        self._update_gen_btn_state()

    def _on_task_error(self, task_id, error_msg):
        print(f"[主线程] 任务 {task_id} 生成失败: {error_msg}")

        with self._task_lock:
            task = self._active_tasks.pop(task_id, None)

        if task:
            placeholder = task.get("placeholder")
            if placeholder:
                try:
                    placeholder.stop()
                    self.canvas.remove_item(placeholder)
                except Exception:
                    pass

        count = self._active_task_count()
        self.statusBar().showMessage(f"任务 {task_id} 失败: {error_msg[:60]}  |  剩余 {count} 个任务", 5000)
        QMessageBox.critical(self, f"生成失败 [{task_id}]", error_msg)
        self._update_gen_btn_state()

    def _update_gen_btn_state(self):
        count = self._active_task_count()
        if count > 0:
            self.editing_panel.gen_btn.setText(f"生成 ({count})")
        else:
            self.editing_panel.gen_btn.setText("生成")
        self.editing_panel.gen_btn.setEnabled(True)

    # ── 右键菜单（图片/对比图）────────────────────
    def _on_item_right_clicked(self, item, scene_pos):
        """右键单击图片/对比图，弹出极简黑白灰菜单"""
        # 直接用当前鼠标全局位置，避免坐标系转换偏差
        from PySide6.QtGui import QCursor
        global_pos = QCursor.pos()

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: rgba(255, 255, 255, 0.96);
                border: 0.5px solid rgba(0, 0, 0, 0.10);
                border-radius: 8px;
                padding: 4px 0px;
            }
            QMenu::item {
                padding: 8px 20px;
                border-radius: 4px;
                color: #222;
                font-size: 12px;
                font-weight: 500;
                background: transparent;
            }
            QMenu::item:selected {
                background: rgba(0, 0, 0, 0.06);
                color: #000;
            }
            QMenu::item#deleteAction {
                color: rgba(220, 38, 38, 0.9);
            }
            QMenu::item#deleteAction:selected {
                background: rgba(220, 38, 38, 0.06);
                color: rgba(200, 30, 30, 1.0);
            }
        """)

        act_download = menu.addAction("另存为" if isinstance(item, VideoItem) else "下载图片")
        act_play = None
        if isinstance(item, VideoItem):
            act_play = menu.addAction("播放视频" if not item._playing else "停止播放")

        # ★ v9.10: 工作流支持空状态加入（不再需要已有卡片）
        act_workflow = None
        if isinstance(item, (ImageItem, CompareItem)):
            path = getattr(item, 'path', None)
            if path and os.path.isfile(path):
                menu.addSeparator()
                act_workflow = menu.addAction("加入工作流")

        act_delete = menu.addAction("删除")
        act_delete.setObjectName("deleteAction")

        action = menu.exec(global_pos)

        if action == act_play:
            item._toggle_play()
        elif action == act_download:
            self._download_item(item)
        elif action == act_workflow:
            # ★ v9.10: 如果有多选，批量加入；否则只加当前图片
            if self._multi_selected_items:
                self._add_to_workflow()  # 无参数 = 批量模式
            else:
                self._add_to_workflow(item)
        elif action == act_delete:
            snap = _snapshot_item(item)
            from marker_tool import MarkerItem as _MI
            for child in list(item.childItems()):
                if isinstance(child, _MI):
                    child.setParentItem(None)
                    if child.scene():
                        child.scene().removeItem(child)
            self.canvas.scene.removeItem(item)
            self.undo_stack.push(DeleteItemsCommand(
                [snap], self.canvas.scene,
                description="删除图片"
            ))
            self.hide_all_panels()

    # ── 双击弹出操作菜单 ────────────────────────
    def _scene_double_click(self, event):
        items = self.canvas.scene.items(event.scenePos())
        for item in items:
            # 视频：直接触发播放，不弹菜单
            if isinstance(item, VideoItem):
                item._toggle_play()
                event.accept()
                return
            if isinstance(item, (ImageItem, CompareItem)):
                self._show_item_menu(item, self.canvas.mapFromScene(event.scenePos()))
                return
        from PySide6.QtWidgets import QGraphicsScene
        QGraphicsScene.mouseDoubleClickEvent(self.canvas.scene, event)

    def _show_item_menu(self, item, view_pos):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: rgba(255, 255, 255, 0.92);
                border: 0.5px solid rgba(0, 0, 0, 0.08);
                border-radius: 8px; padding: 4px;
            }
            QMenu::item { padding: 7px 16px; border-radius: 5px; color: #333; font-size: 12px; font-weight: 500; }
            QMenu::item:selected { background: rgba(0, 0, 0, 0.05); color: #111; }
            QMenu::separator { height: 0.5px; background: rgba(0, 0, 0, 0.08); margin: 3px 8px; }
        """)

        act_zoom = menu.addAction("放大查看")
        menu.addSeparator()
        act_regen = menu.addAction("重新生成")
        act_edit_prompt = menu.addAction("修改提示词")
        menu.addSeparator()
        act_download = menu.addAction("另存为")
        menu.addSeparator()
        act_marker = menu.addAction("标记模式")
        menu.addSeparator()
        act_delete = menu.addAction("删除")

        action = menu.exec(self.canvas.mapToGlobal(view_pos))

        if action == act_zoom:
            self._zoom_view(item)
        elif action == act_regen:
            self._regen_item(item, None)
        elif action == act_edit_prompt:
            self._regen_item(item, "edit_prompt")
        elif action == act_download:
            self._download_item(item)
        elif action == act_marker:
            self._marker_mode = True
            self._marker_target = item
            self.marker_toolbar.set_target_item(item)
            # 工具栏保持隐藏，标记功能正常使用
            self.canvas.setCursor(Qt.CrossCursor)
            self.statusBar().showMessage("📍 标记模式已开启：点击图片添加标记点", 3000)
        elif action == act_delete:
            # 拍快照再删除，支持撤销
            snap = _snapshot_item(item)
            self.canvas.scene.removeItem(item)
            # 移除子 MarkerItem
            from marker_tool import MarkerItem as _MI
            for child in list(item.childItems()):
                if isinstance(child, _MI):
                    child.setParentItem(None)
                    if child.scene():
                        child.scene().removeItem(child)
            self.undo_stack.push(DeleteItemsCommand(
                [snap], self.canvas.scene,
                description="删除图片"
            ))
            self.hide_all_panels()

    def _zoom_view(self, item):
        path = item.path if hasattr(item, 'path') else None
        if not path or not os.path.isfile(path):
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("查看原图")
        # 恢复弹窗 logo
        _icon_path = self._resolve_icon_path("icon.ico")
        if _icon_path:
            dlg.setWindowIcon(QIcon(_icon_path))
        dlg.resize(900, 700)
        dlg.setStyleSheet("QDialog { background: #fafafa; }")
        layout = QVBoxLayout(dlg)
        lbl = QLabel()
        lbl.setAlignment(Qt.AlignCenter)
        px = QPixmap(path)
        lbl.setPixmap(px.scaled(860, 660, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        layout.addWidget(lbl)
        dlg.exec()

    def _regen_item(self, item, mode):
        if self.editing_panel.isHidden():
            item.setSelected(True)
        if mode == "edit_prompt":
            self.editing_panel.prompt_input.setFocus()
            self.editing_panel.prompt_input.selectAll()
        else:
            self.editing_panel.on_generate()

    def _download_item(self, item):
        path = item.path if hasattr(item, 'path') else None
        if not path or not os.path.isfile(path):
            QMessageBox.warning(self, "提示", "找不到原始文件")
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "另存为", os.path.basename(path),
            "图片文件 (*.png *.jpg *.jpeg *.webp)"
        )
        if dest:
            shutil.copy2(path, dest)
            self.statusBar().showMessage(f"已保存到: {dest}", 5000)

    # ══════════════════════════════════════════════
    #  标记模式
    # ══════════════════════════════════════════════

    def toggle_marker_mode(self):
        """切换标记模式（快捷键 M）"""
        self._marker_mode = not self._marker_mode
        if self._marker_mode:
            # 检查画布上是否有图片
            has_image = any(
                isinstance(item, (ImageItem, CompareItem))
                for item in self.canvas.scene.items()
            )
            if not has_image:
                self._marker_mode = False
                self.statusBar().showMessage("⚠️ 画布上没有图片，请先拖入图片再使用标记功能", 3000)
                return
            self.statusBar().showMessage("📍 标记模式已开启：点击图片添加标记点，拖拽标记可移动，再按 M 关闭", 5000)
            self.canvas.setCursor(Qt.CrossCursor)
            # 禁止图片拖动，避免和标记冲突
            for item in self.canvas.scene.items():
                if isinstance(item, (ImageItem, CompareItem)):
                    item.setFlag(QGraphicsPixmapItem.ItemIsMovable, False)
        else:
            # ── 退出标记模式：渲染标记到图片上 ──
            self._bake_markers_to_images()

            self.statusBar().showMessage("标记模式已关闭，标记已渲染到图片上", 2000)
            self.canvas.setCursor(Qt.ArrowCursor)
            self.marker_toolbar.hide()
            self._marker_target = None
            # 恢复图片拖动
            for item in self.canvas.scene.items():
                if isinstance(item, (ImageItem, CompareItem)):
                    item.setFlag(QGraphicsPixmapItem.ItemIsMovable, True)

    def _bake_markers_to_images(self):
        """退出标记模式时，将所有标记点渲染到对应图片上，
        替换画布上的图片为带标记版本，并移除标记点。"""
        from PySide6.QtGui import QPixmap
        from PySide6.QtCore import QPointF as _QPointF

        # 收集所有有标记的图片 item
        items_with_markers = []
        for item in list(self.canvas.scene.items()):
            if not isinstance(item, (ImageItem, CompareItem)):
                continue
            has_markers = any(
                isinstance(child, MarkerItem)
                for child in item.childItems()
            )
            if has_markers:
                items_with_markers.append(item)

        if not items_with_markers:
            return

        # 收集渲染前的状态，用于撤销
        bake_undo_data = []

        for item in items_with_markers:
            # 设置 target_item 以便 render_markers_to_image 使用
            self.marker_toolbar.set_target_item(item)
            orig_path = item.path if hasattr(item, 'path') else None
            if not orig_path or not os.path.isfile(orig_path):
                continue

            # 保存渲染前状态（用于撤销）
            marker_snaps = []
            for child in item.childItems():
                if isinstance(child, MarkerItem):
                    marker_snaps.append({
                        'index': child.index,
                        'label': child.label,
                        'color_name': child.color_name,
                        'local_pos': _QPointF(child.pos()),
                    })

            orig_pixmap_copy = QPixmap(item.pixmap()) if isinstance(item, ImageItem) else None

            # 渲染带标记的新图片
            rendered_path = self.marker_toolbar.render_markers_to_image(orig_path)
            if not rendered_path or rendered_path == orig_path:
                print(f"[标记渲染] 图片 {orig_path} 渲染失败或无标记，跳过")
                continue

            # 加载新图片替换画布上的显示
            new_pixmap = QPixmap(rendered_path)
            if new_pixmap.isNull():
                print(f"[标记渲染] 无法加载渲染后图片: {rendered_path}")
                continue

            # 记录当前位置和状态
            old_pos = item.pos()
            old_selected = item.isSelected()
            old_merge_order = getattr(item, 'merge_order', -1)

            # 保存到撤销数据
            bake_undo_data.append({
                'item': item,
                'orig_path': orig_path,
                'orig_pixmap': orig_pixmap_copy,
                'markers': marker_snaps,
                'orig_pos': _QPointF(old_pos),
            })

            # 更新图片和路径
            item.setPixmap(new_pixmap)
            item.path = rendered_path
            item.has_markers = True  # 标记已渲染到图片上
            if hasattr(item, 'merge_order'):
                item.merge_order = old_merge_order

            # 同步 editing_panel 的参考图路径
            if item == self.current_target_item:
                self.editing_panel.set_image_path(rendered_path)

            # 恢复位置和选中状态
            item.setPos(old_pos)
            if old_selected:
                item.setSelected(True)

            # 移除所有标记点子 item
            markers_to_remove = [
                child for child in item.childItems()
                if isinstance(child, MarkerItem)
            ]
            for m in markers_to_remove:
                m.setParentItem(None)
                if m.scene():
                    m.scene().removeItem(m)

            print(f"[标记渲染] 图片已更新: {orig_path} → {rendered_path} (标记数: {len(markers_to_remove)})")

        # 记录渲染撤销命令
        if bake_undo_data:
            total_markers = sum(len(d['markers']) for d in bake_undo_data)
            self.undo_stack.push(BakeMarkersCommand(
                bake_undo_data,
                description=f"渲染 {len(bake_undo_data)} 张图片上的 {total_markers} 个标记"
            ))

        # 清空标记工具栏状态
        self.marker_toolbar._markers.clear()
        self.marker_toolbar._undo_stack.clear()
        self.marker_toolbar._next_index = 1
        self.marker_toolbar._update_display()

    def _canvas_mouse_press_hook(self, event):
        """拦截画布鼠标点击，标记模式下在图片上添加标记点"""
        if self._marker_mode and event.button() == Qt.LeftButton:
            # 获取点击位置的 item
            item_under = self.canvas.itemAt(event.position().toPoint())
            # 如果点到了标记点，让拖拽正常工作
            if isinstance(item_under, MarkerItem):
                self._orig_canvas_mouse_press(event)
                return

            # 获取点击位置的场景坐标
            scene_pos = self.canvas.mapToScene(event.position().toPoint())
            # 检查是否点击了图片（跳过 MarkerItem）
            items = self.canvas.scene.items(scene_pos)
            image_item = None
            for item in items:
                if isinstance(item, MarkerItem):
                    continue  # 跳过标记点
                if isinstance(item, (ImageItem, CompareItem)):
                    image_item = item
                    break

            if image_item:
                self._marker_target = image_item
                self.marker_toolbar.set_target_item(image_item)
                # 同步 current_target_item，确保生成入口能检测到正确的图片
                self.current_target_item = image_item
                self.marker_toolbar.add_marker_at(scene_pos.x(), scene_pos.y())
                # 全局撤销：记录添加标记
                if self.marker_toolbar._markers:
                    marker = self.marker_toolbar._markers[-1]
                    self.undo_stack.push(AddMarkerCommand(
                        marker, image_item, self.marker_toolbar,
                        description=f"添加标记 {marker.label}"
                    ))
                # 工具栏保持隐藏，标记功能正常使用
                marker = self.marker_toolbar._markers[-1]
                px, py = marker.get_pixel_coords()
                nx, ny = marker.get_image_coords()
                self.statusBar().showMessage(
                    f"📍 已添加标记 {marker.label} "
                    f"像素: ({px}, {py})  归一化: ({nx}, {ny})  |  再按 M 关闭标记模式"
                )
                return  # 不传递给原始处理
            else:
                # 点击空白区域，忽略
                self.statusBar().showMessage("请在图片上点击添加标记点", 2000)
                return

        # 非标记模式或非图片区域，传递给原始处理
        self._orig_canvas_mouse_press(event)

    def _update_marker_toolbar_pos(self):
        """标记工具栏定位在右侧"""
        if not self.marker_toolbar.isVisible():
            return
        # 固定在窗口右侧
        x = self.canvas.width() - self.marker_toolbar.width() - 12
        y = 12
        self.marker_toolbar.move(x, y)

    # ══════════════════════════════════════════════
    #  工作流（v9.0 独立配置条模式）
    # ══════════════════════════════════════════════
    _WF_BTN_NORMAL = """
        QPushButton {
            background: rgba(255, 255, 255, 0.78);
            border: 0.5px solid rgba(0, 0, 0, 0.08);
            border-radius: 6px;
            font-size: 11px; font-weight: 600;
            color: #555;
        }
        QPushButton:hover { background: rgba(255, 255, 255, 0.92); color: #222; }
        QPushButton:pressed { background: rgba(240, 240, 242, 0.95); }
    """
    _WF_BTN_ACTIVE = """
        QPushButton {
            background: rgba(0, 0, 0, 0.08);
            border: 0.5px solid rgba(0, 0, 0, 0.06);
            border-radius: 6px;
            font-size: 11px; font-weight: 700;
            color: #111;
        }
        QPushButton:hover { background: rgba(0, 0, 0, 0.12); }
    """

    def _toggle_workflow(self):
        """★ v9.9: 点击工作流按钮 → 显示/隐藏浮动工具栏。"""
        if self.workflow_toolbar.isVisible():
            # 已显示 → 关闭工作流
            self._close_workflow()
        else:
            # 显示工具栏（固定在 canvas 顶部居中）
            self._position_workflow_toolbar()
            self.workflow_toolbar.show()
            self.workflow_toolbar.raise_()
            self.workflow_btn.setStyleSheet(self._WF_BTN_ACTIVE)
            self.statusBar().showMessage("工作流已启用 — 先选模板，再点添加任务", 2000)
    
    def _position_workflow_toolbar(self):
        """★ v9.9: 将工具栏固定在 canvas 区域顶部居中（主窗口坐标系）。
        
        工具栏 parent=self（MainWindow），move(x,y) 相对主窗口左上角。
        需要把 canvas 在窗口中的位置算进去。
        """
        from workflow_panel import TOOLBAR_W, TOOLBAR_H
        
        # canvas 在主窗口中的几何位置
        canvas_geo = self.canvas.geometry()
        canvas_x = canvas_geo.x()
        canvas_y = canvas_geo.y()
        canvas_w = canvas_geo.width()
        
        # 居中 + 紧贴 canvas 顶部
        x = canvas_x + max(10, (canvas_w - TOOLBAR_W) // 2)
        y = canvas_y + 10  # 距 canvas 顶 10px
        
        self.workflow_toolbar.move(int(x), int(y))

    def _workflow_anchor_pos(self):
        """★ v9.10: 计算工作流工具栏底部中心在场景中的坐标。
        
        返回 QPointF，用于卡片定位锚点。工具栏隐藏时返回 None。
        """
        tb = self.workflow_toolbar
        if not tb.isVisible():
            return None
        tb_bottom_center = QPoint(
            tb.x() + tb.width() // 2,
            tb.y() + tb.height()
        )
        return self.canvas.mapToScene(tb_bottom_center)

    def _add_workflow_task(self):
        """添加任务卡片到画布（紧贴工作流工具栏正下方）。"""
        import traceback
        try:
            config = self.workflow_engine.config
            template = config.template
            ratio = config.ratio

            anchor = self._workflow_anchor_pos()
            print(f"[工作流] 锚点(场景): {anchor}")

            card = self.workflow_engine.create_task_card(
                template, ratio_str=ratio, scene=self.canvas.scene,
                anchor_pos=anchor
            )
            print(f"[工作流] 卡片已创建: #{card.group_index}, pos={card.pos()}")
            # 视图跟随到新卡片
            self.canvas.centerOn(card)
            self.statusBar().showMessage(f"已添加任务 #{card.group_index}（{template['name']}）", 1500)
        except Exception as e:
            print(f"[工作流] ★★★ 添加任务异常: {e}")
            traceback.print_exc()
            QMessageBox.warning(self, "错误", f"添加任务失败：{e}")

    def _execute_all_workflow(self):
        """执行所有任务。"""
        if self.workflow_engine.is_running:
            return
        if not self.workflow_engine.task_cards:
            QMessageBox.information(self, "提示", "请先添加任务卡片并上传图片")
            return
        valid = [c for c in self.workflow_engine.task_cards if c.get_image_paths()]
        if not valid:
            QMessageBox.information(self, "提示", "没有已上传图片的任务卡片")
            return
        config = self.workflow_engine.config
        self.workflow_engine.execute_all(
            config.ratio, config.resolution, config.model, config.global_prompt
        )

    def _close_workflow(self):
        """★ v9.10: 关闭工作流：仅隐藏工具栏，保留所有任务卡片和图片。
        
        只有用户手动点击 ✕ 删除任务卡片或清空按钮时才真正删除内容。
        """
        self.workflow_toolbar.hide()
        self.workflow_btn.setStyleSheet(self._WF_BTN_NORMAL)
        self.statusBar().showMessage("工作流已收起（内容已保留）", 1500)

    def _on_workflow_closed(self):
        """所有任务清空时重置状态。"""
        pass

    def _add_to_workflow(self, item=None):
        """★ v9.10: 将画布图片（单张或多选）添加到工作流。
        
        Args:
            item: 单个 QGraphicsItem。如果为 None 且有多选图片，则批量处理。
        """
        # 收集要加入的图片路径列表
        paths = []
        
        if item is not None:
            # 单图模式
            path = getattr(item, 'path', None)
            if path and os.path.isfile(path):
                paths.append(path)
        elif self._multi_selected_items:
            # 多选模式：收集所有选中图片的路径
            for sel_item in self._multi_selected_items:
                if isinstance(sel_item, (ImageItem, CompareItem)):
                    p = getattr(sel_item, 'path', None)
                    if p and os.path.isfile(p):
                        paths.append(p)
        
        if not paths:
            QMessageBox.warning(self, "提示", "没有可添加的图片")
            return
        
        # 如果工作流未启用，自动启用
        if not self.workflow_toolbar.isVisible():
            self._toggle_workflow()
        
        template = self.workflow_engine.config.template
        anchor = self._workflow_anchor_pos()
        self.workflow_engine.handle_dropped_images(
            paths, scene=self.canvas.scene, template=template,
            anchor_pos=anchor
        )
        self.statusBar().showMessage(f"已添加 {len(paths)} 张图片到工作流", 2000)

    def _on_workflow_results_to_canvas(self, result_paths):
        """工作流生成完成：将所有结果放入画布（原图尺寸无压缩）"""
        if not result_paths:
            return
        
        added = self.canvas.add_files_in_row(result_paths)
        if added:
            self.canvas.scene.clearSelection()
            for item in added:
                item.setSelected(True)
            self.canvas.centerOn(added[-1])
            self.undo_stack.push(AddItemsCommand(
                added, self.canvas.scene,
                description=f"工作流生成 {len(added)} 张"
            ))
            self.statusBar().showMessage(f"已生成 {len(added)} 张图片放入画布", 3000)

    def _on_workflow_results_compared(self, compared_pairs):
        """★ 对比图：将每个生成结果与原始渲染图配对创建 CompareItem"""
        if not compared_pairs:
            return
        
        print(f"[工作流] 创建 {len(compared_pairs)} 个对比图")
        
        # 获取当前最右边的 X 坐标
        start_x = self.canvas.get_rightmost_x() + self.canvas.GAP
        
        added_items = []
        for result_path, orig_path in compared_pairs:
            if not result_path or not os.path.isfile(result_path):
                print(f"[工作流] 跳过无效结果: {result_path}")
                continue
            
            # 加载生成图
            gen_px = QPixmap(result_path)
            if gen_px.isNull():
                print(f"[工作流] 生成图加载失败: {result_path}")
                continue
            
            # 如果有原始渲染图，创建 CompareItem
            if orig_path and os.path.isfile(orig_path):
                orig_px = QPixmap(orig_path)
                if not orig_px.isNull():
                    # 渲染图缩放到 1024 宽以内
                    if orig_px.width() > 1024:
                        orig_px = orig_px.scaledToWidth(1024, Qt.SmoothTransformation)
                    
                    item = CompareItem(orig_px, gen_px, orig_path, result_path)
                    item.setOpacity(0)
                    item.setPos(start_x, 0)
                    self.canvas.scene.addItem(item)
                    
                    # 淡入动画
                    from PySide6.QtCore import QPropertyAnimation, QEasingCurve
                    anim = QPropertyAnimation(item, b"qt_opacity")
                    anim.setDuration(300)
                    anim.setStartValue(0.0)
                    anim.setEndValue(1.0)
                    anim.setEasingCurve(QEasingCurve.OutCubic)
                    anim.start(QPropertyAnimation.DeleteWhenStopped)
                    item._anim = anim
                    
                    added_items.append(item)
                    start_x += item.boundingRect().width() + self.canvas.GAP
                    print(f"[工作流] 创建对比图: orig={orig_path}, gen={result_path}")
                    continue
            
            # 没有原始图时，创建普通 ImageItem
            img_item = self.canvas.add_image(result_path, start_x, 0)
            if img_item:
                added_items.append(img_item)
                start_x += img_item.boundingRect().width() + self.canvas.GAP
        
        if added_items:
            self.canvas.scene.clearSelection()
            for item in added_items:
                item.setSelected(True)
            self.canvas.centerOn(added_items[-1])
            self.statusBar().showMessage(f"已创建 {len(added_items)} 个对比图", 3000)

    def _on_marker_changed(self):
        """标记点变化时刷新坐标和标签"""
        self.marker_toolbar.update_positions()

    def _get_mask_coords_for_item(self, item):
        """收集指定图片 item 上所有标记点的坐标字符串。
        坐标为图片局部相对坐标，格式：x1,y1,label1;x2,y2,label2"""
        if not item:
            return None
        from marker_tool import MarkerItem
        parts = []
        for child in item.childItems():
            if isinstance(child, MarkerItem):
                x, y = child.get_image_coords()
                parts.append(f"{x},{y},{child.label}")
        if parts:
            coords_str = ";".join(parts)
            print(f"[mask_coords] 图片 {getattr(item, 'path', '?')} 的标记坐标: {coords_str}")
            return coords_str
        return None

    def _on_marker_replace(self, request_text):
        """自然语言局部替换：解析标记坐标→AI 局部修改"""
        import re

        # 解析请求中的标记编号
        match = re.search(r'标记\s*(\d+)', request_text)
        if not match:
            QMessageBox.warning(self, "提示", "请在指令中指定标记编号，如：把标记 1 的位置替换成 XX")
            return

        target_label = match.group(1)

        # 从标记工具栏中找到对应标记点的坐标
        marker = None
        for m in self.marker_toolbar._markers:
            if m.label == target_label:
                marker = m
                break

        if not marker:
            QMessageBox.warning(self, "提示", f"未找到标记 {target_label}")
            return

        x, y = marker.get_image_coords()
        img_path = marker.parent_image_item.path if marker.parent_image_item else None

        if not img_path or not os.path.isfile(img_path):
            QMessageBox.warning(self, "提示", "找不到原始图片文件")
            return

        # 构建增强提示词，包含坐标和替换指令
        coords_info = f"在图片坐标({x},{y})位置进行局部修改"
        enhanced_prompt = f"{coords_info}。{request_text}"

        print(f"[局部替换] 增强提示词: {enhanced_prompt}")
        print(f"[局部替换] 参考图: {img_path}")
        print(f"[局部替换] 坐标: ({x}, {y})")

        # 使用现有的 AI 生成接口，附带参考图
        task_id = self._new_task_id()

        # 渲染带标记的图片替代原图提交（标记模式内直接渲染，不等待退出）
        effective_image_path = img_path
        parent_item = marker.parentItem() or marker.parent_image_item
        if parent_item:
            self.marker_toolbar.set_target_item(parent_item)
            rendered_path = self.marker_toolbar.render_markers_to_image(img_path)
            if rendered_path and rendered_path != img_path:
                effective_image_path = rendered_path
                print(f"[标记替换] 使用带标记图片: {rendered_path}")

        # 自动追加标记提示词
        marker_suffix = "请根据图中数字标记位置进行内容替换，最终生成的图片不要出现任何标记点和数字"
        if marker_suffix not in enhanced_prompt:
            enhanced_prompt = f"{enhanced_prompt}。{marker_suffix}"

        placeholder_x, placeholder_y, ph_w, ph_h = self._calc_placeholder_pos()
        placeholder = self.canvas.add_placeholder(placeholder_x, placeholder_y, ph_w, ph_h, task_id)
        placeholder.set_progress(0, "局部替换中…")

        with self._task_lock:
            self._active_tasks[task_id] = {
                "placeholder": placeholder,
                "orig_path": img_path,
            }

        self._update_gen_btn_state()
        self.statusBar().showMessage(f"局部替换任务 {task_id} 已提交", 3000)

        threading.Thread(
            target=self._generate_thread,
            args=(task_id, enhanced_prompt, "1:1", "1K", effective_image_path, placeholder, None),
            daemon=True
        ).start()
