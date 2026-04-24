"""
图像标注工具模块
基于 ComfyUI comfy_imagecrop 的标注逻辑，在 PySide6 无限画布上实现：
- 点击图片添加数字标记 1、2、3…，支持拖拽移动
- 标记点作为图片子 item，坐标为图片局部相对坐标，移动图片时标记跟随
- 自动记录标记点坐标 (x,y) + 编号，格式：x1,y1,1;x2,y2,2
- 输出独立标签 label_1~label_10
- 自然语言局部替换：解析坐标→AI 修改

极简黑白灰风格
"""

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QLineEdit, QFrame, QToolTip
)
from PySide6.QtCore import Qt, Signal, QPointF, QRectF, QTimer, Property
from PySide6.QtGui import (
    QPainter, QColor, QPen, QFont, QBrush,
    QPolygonF, QLinearGradient, QCursor
)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject
import os


# ──────────────────────────────────────────────
# MarkerItem：地图标记样式的标注点
# 极简黑白灰风格，选中状态红色点缀
# ──────────────────────────────────────────────
class MarkerItem(QGraphicsObject):
    """画布上的标注点，地图标记样式，可拖拽移动。
    
    ★ v9.10 锚点修复：
      - pos() = 尖角底部锚点（pin tip），即用户点击的精确位置
      - 所有绘制内容相对于锚点上偏，确保尖角始终对准点击点
      - 选中放大时锚点不变，仅外圈和主体尺寸变化
    作为所属图片 item 的子 item，position 即为图片局部坐标。
    """

    # 标记点颜色方案 — 黑白灰 + 红色选中
    COLORS = {
        "灰": QColor(80, 80, 85),
    }
    DEFAULT_COLOR = COLORS["灰"]

    def __init__(self, index, label="", color_name="灰", parent_image_item=None):
        super().__init__()
        self.index = index           # 编号（1, 2, 3...）
        self.label = label or str(index)
        self.color_name = color_name
        self.parent_image_item = parent_image_item  # 所属图片 item
        self._selected = False
        self._size = 36              # 标记点直径

        # ★ 锚点偏移量：从圆心到尖角底部的距离
        self._anchor_offset = 0       # 在 _compute_anchor 中计算

        # 作为图片的子 item —— position 即为图片局部坐标
        if parent_image_item:
            self.setParentItem(parent_image_item)

        # 可移动 + 可选中
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(100)  # 在图片之上
        # 使用 DeviceCoordinateCache 消除拖动时的黄色残影
        self.setCacheMode(QGraphicsItem.DeviceCoordinateCache)
        
        # 预计算锚点偏移
        self._compute_anchor()

    def _compute_anchor(self):
        """★ 计算锚点偏移：圆心(0,0) 到 尖角底部的 Y 距离。
        
        绘制时所有内容上移此距离，使尖角底部精确落在 pos() 处。
        """
        s = self._size
        radius = s / 2
        tip_h = s // 3
        self._anchor_offset = radius + tip_h  # 尖角底部在圆心下方这么多像素

    def boundingRect(self):
        s = self._size
        radius = s / 2
        tip_h = s // 3
        pad = 4
        # ★ 以尖角底部(锚点=原点)为基准计算包围盒
        # 尖角在原点(0,0)，圆心在(0, -_anchor_offset)，圆顶在最上方
        top = -self._anchor_offset - radius - pad   # 圆顶
        bottom = pad                                 # 尖角底部稍下
        left = -radius - pad
        right = radius + pad
        return QRectF(left, top, right - left, bottom - top)

    def paint(self, painter, option, widget=None):
        s = self._size
        radius = s / 2
        tip_height = s // 3
        is_sel = self._selected or self.isSelected()
        fill = QColor(220, 38, 38) if is_sel else self.DEFAULT_COLOR
        
        # ★ 所有 Y 坐标加上锚点偏移，使尖角底部落在 (0,0)=pos()
        ay = self._anchor_offset  # 上移量

        painter.setRenderHint(QPainter.Antialiasing)

        # 选中高亮外圈
        if is_sel:
            painter.setPen(QPen(QColor(220, 38, 38, 180), 2.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QPointF(0, -ay), radius + 5, radius + 5)

        # 柔和阴影
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 40))
        painter.drawEllipse(QPointF(1.5, -ay + 2), radius, radius)

        # 底部尖角 —— 尖端精确在 (0, 0)，即锚点/点击位置
        tip_poly = QPolygonF([
            QPointF(0, 0),                          # ★ 锚点！尖角尖端
            QPointF(-radius / 2, -tip_height),      # 尖角左上
            QPointF(radius / 2, -tip_height),       # 尖角右上
        ])
        painter.setBrush(fill)
        painter.drawPolygon(tip_poly)

        # 圆形主体 —— 圆心在 (0, -ay)
        painter.setBrush(fill)
        painter.drawEllipse(QPointF(0, -ay), radius, radius)

        # 白色内圈
        inner_r = radius - 3
        if inner_r > 0:
            painter.setBrush(Qt.NoBrush)
            ring_color = QColor(220, 38, 38, 200) if is_sel else QColor(255, 255, 255, 160)
            painter.setPen(QPen(ring_color, 2))
            painter.drawEllipse(QPointF(0, -ay), inner_r, inner_r)

        # 文字标签（在圆心位置）
        painter.setPen(QColor(255, 255, 255))
        font = QFont("微软雅黑", max(8, int(s * 0.35)), QFont.Bold)
        painter.setFont(font)
        painter.drawText(QRectF(-radius, -ay - radius, s, s), Qt.AlignCenter, self.label)

    def get_image_coords(self):
        """获取标记点在所属图片上的归一化坐标 (0~1 比例)。"""
        pos = self.pos()
        parent = self.parentItem()
        if parent:
            rect = parent.boundingRect()
            w = rect.width()
            h = rect.height()
            if w > 0 and h > 0:
                return round(pos.x() / w, 4), round(pos.y() / h, 4)
        return round(pos.x(), 1), round(pos.y(), 1)

    def get_pixel_coords(self):
        """获取标记点在所属图片上的像素坐标（用于界面显示）。"""
        pos = self.pos()
        return int(pos.x()), int(pos.y())

    def set_marker_selected(self, selected):
        self._selected = selected
        self.update()

    def hoverEnterEvent(self, event):
        px, py = self.get_pixel_coords()
        nx, ny = self.get_image_coords()
        QToolTip.showText(
            event.screenPos(),
            f"标记 {self.label}\n像素: ({px}, {py})  归一化: ({nx}, {ny})"
        )
        self.setCursor(Qt.SizeAllCursor)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        QToolTip.hideText()
        self.setCursor(Qt.ArrowCursor)
        super().hoverLeaveEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            parent = self.parentItem()
            if parent:
                rect = parent.boundingRect()
                pad = 18
                x = max(pad, min(value.x(), rect.width() - pad))
                y = max(pad, min(value.y(), rect.height() - pad))
                if x != value.x() or y != value.y():
                    self.setPos(QPointF(x, y))
                    return QPointF(x, y)
            px, py = self.get_pixel_coords()
            nx, ny = self.get_image_coords()
            QToolTip.showText(
                QCursor().pos(),
                f"标记 {self.label}\n像素: ({px}, {py})  归一化: ({nx}, {ny})"
            )
        elif change == QGraphicsItem.ItemSceneChange:
            pass
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.update()
        if self.scene() and self.scene().views():
            self.scene().views()[0].viewport().update()


# ──────────────────────────────────────────────
# MarkerToolbar：标注工具栏（浮动面板）
# 极简黑白灰风格
# ──────────────────────────────────────────────
class MarkerToolbar(QFrame):
    """标注工具栏：添加/删除/撤销/清空/自定义标签/坐标输出"""

    marker_added = Signal()          # 标记点被添加/移动/删除后通知
    replace_requested = Signal(str)  # 自然语言局部替换请求

    def __init__(self, parent=None):
        super().__init__(parent)
        self._markers = []           # MarkerItem 列表
        self._next_index = 1         # 下一个编号
        self._target_item = None     # 当前标注的图片 item
        self._undo_stack = []        # 撤销栈

        self.setFixedWidth(300)
        self.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.92);
                border: 0.5px solid rgba(0, 0, 0, 0.08);
                border-radius: 10px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        # ── 标题 ──
        title = QLabel("图像标注")
        title.setStyleSheet("color: #222; font-size: 14px; font-weight: 700;")
        layout.addWidget(title)

        # ── 坐标输出框 ──
        self.coord_display = QLineEdit()
        self.coord_display.setReadOnly(True)
        self.coord_display.setPlaceholderText("坐标：x1,y1,1;x2,y2,2;...")
        self.coord_display.setStyleSheet("""
            QLineEdit {
                background: rgba(0, 0, 0, 0.04); color: #555; border: none;
                border-radius: 6px; padding: 6px 10px; font-family: Consolas, monospace;
                font-size: 11px;
            }
        """)
        layout.addWidget(self.coord_display)

        # ── 自定义标签输入 ──
        label_row = QHBoxLayout()
        self.label_input = QLineEdit()
        self.label_input.setPlaceholderText("输入自定义标签")
        self.label_input.setStyleSheet("""
            QLineEdit {
                background: rgba(0, 0, 0, 0.04); color: #333; border: none;
                border-radius: 6px; padding: 6px 10px; font-size: 11px;
            }
            QLineEdit:focus { background: rgba(0, 0, 0, 0.06); }
        """)
        label_row.addWidget(self.label_input)

        add_btn = QPushButton("添加")
        add_btn.setFixedWidth(48)
        add_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0, 0, 0, 0.05); color: #333; border: none;
                border-radius: 6px; padding: 5px; font-weight: 700; font-size: 11px;
            }
            QPushButton:hover { background: rgba(0, 0, 0, 0.09); }
            QPushButton:pressed { background: rgba(0, 0, 0, 0.03); }
        """)
        add_btn.clicked.connect(self._on_add_custom_label)
        label_row.addWidget(add_btn)
        layout.addLayout(label_row)

        # ── 数字按钮行 ──
        num_row = QHBoxLayout()
        num_row.setSpacing(3)
        for i, ch in enumerate("1234567890"):
            btn = QPushButton(ch)
            btn.setFixedSize(24, 24)
            btn.setStyleSheet("""
                QPushButton {
                    background: rgba(0, 0, 0, 0.04); color: #666; border: none;
                    border-radius: 5px; font-weight: 600; font-size: 11px;
                }
                QPushButton:hover { background: rgba(0, 0, 0, 0.08); color: #333; }
            """)
            btn.clicked.connect(lambda checked, c=ch: self._on_preset_label(c))
            num_row.addWidget(btn)
        layout.addLayout(num_row)

        # ── 字母按钮行 ──
        letter_row = QHBoxLayout()
        letter_row.setSpacing(3)
        for ch in "ABCDEFGHIJ":
            btn = QPushButton(ch)
            btn.setFixedSize(24, 24)
            btn.setStyleSheet("""
                QPushButton {
                    background: rgba(0, 0, 0, 0.04); color: #666; border: none;
                    border-radius: 5px; font-weight: 600; font-size: 11px;
                }
                QPushButton:hover { background: rgba(0, 0, 0, 0.08); color: #333; }
            """)
            btn.clicked.connect(lambda checked, c=ch: self._on_preset_label(c))
            letter_row.addWidget(btn)
        layout.addLayout(letter_row)

        # ── 操作按钮行 ──
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(6)

        self._make_ctrl_btn(ctrl_row, "删除选中", self._on_delete_selected)
        self._make_ctrl_btn(ctrl_row, "撤销", self._on_undo)
        self._make_ctrl_btn(ctrl_row, "清空", self._on_clear)
        layout.addLayout(ctrl_row)

        # ── 标记数量 ──
        self.count_label = QLabel("标记: 0")
        self.count_label.setStyleSheet("color: #888; font-size: 11px; font-weight: 500;")

        # ── 分隔线 ──
        layout.addWidget(self.count_label)

        # ── 局部替换 ──
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: rgba(0, 0, 0, 0.08);")
        layout.addWidget(sep)

        replace_title = QLabel("局部替换")
        replace_title.setStyleSheet("color: #222; font-size: 13px; font-weight: 700;")
        layout.addWidget(replace_title)

        self.replace_input = QLineEdit()
        self.replace_input.setPlaceholderText("例：把标记 1 的位置替换成红色领带")
        self.replace_input.setStyleSheet("""
            QLineEdit {
                background: rgba(0, 0, 0, 0.04); color: #333; border: none;
                border-radius: 6px; padding: 6px 10px; font-size: 11px;
            }
            QLineEdit:focus { background: rgba(0, 0, 0, 0.06); }
        """)
        layout.addWidget(self.replace_input)

        replace_btn = QPushButton("执行局部替换")
        replace_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0, 0, 0, 0.05); color: #333; border: none;
                border-radius: 6px; padding: 8px; font-weight: 700; font-size: 12px;
            }
            QPushButton:hover { background: rgba(0, 0, 0, 0.09); }
            QPushButton:pressed { background: rgba(0, 0, 0, 0.03); }
        """)
        replace_btn.clicked.connect(self._on_replace)
        layout.addWidget(replace_btn)

        # ── 标签输出区域 ──
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("color: rgba(0, 0, 0, 0.08);")
        layout.addWidget(sep2)

        output_title = QLabel("标签输出")
        output_title.setStyleSheet("color: #222; font-size: 13px; font-weight: 700;")
        layout.addWidget(output_title)

        self.label_outputs = {}
        for i in range(1, 11):
            row = QHBoxLayout()
            key_label = QLabel(f"label_{i}:")
            key_label.setFixedWidth(50)
            key_label.setStyleSheet("color: #888; font-size: 10px;")
            val_label = QLabel("")
            val_label.setStyleSheet("color: #666; font-size: 10px; font-family: Consolas, monospace;")
            val_label.setWordWrap(True)
            row.addWidget(key_label)
            row.addWidget(val_label)
            layout.addLayout(row)
            self.label_outputs[i] = val_label

    @staticmethod
    def _make_ctrl_btn(layout, text, callback):
        btn = QPushButton(text)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(0, 0, 0, 0.04); color: #666; border: none;
                border-radius: 6px; padding: 5px 10px; font-size: 11px; font-weight: 600;
            }}
            QPushButton:hover {{ background: rgba(0, 0, 0, 0.08); color: #333; }}
        """)
        btn.clicked.connect(callback)
        layout.addWidget(btn)

    # ── 公共接口 ────────────────────────────────

    def set_target_item(self, item):
        """设置当前标注的图片 item"""
        self._target_item = item
        self._markers = []
        self._next_index = 1
        if item:
            for child in item.childItems():
                if isinstance(child, MarkerItem):
                    self._markers.append(child)
                    if child.index >= self._next_index:
                        self._next_index = child.index + 1
        self._update_display()

    def scene(self):
        """获取场景（通过 parent）"""
        if self.parent() and hasattr(self.parent(), 'scene'):
            s = self.parent().scene
            if callable(s):
                return s()
            return s
        return None

    def add_marker_at(self, scene_x, scene_y, label=""):
        """在场景坐标 (scene_x, scene_y) 处添加标记点。"""
        if not self._target_item:
            return None

        idx = self._next_index
        self._next_index += 1
        marker = MarkerItem(idx, label or str(idx), parent_image_item=self._target_item)

        local_pos = self._target_item.mapFromScene(QPointF(scene_x, scene_y))
        pad = 18
        rect = self._target_item.boundingRect()
        clamped_x = max(pad, min(local_pos.x(), rect.width() - pad))
        clamped_y = max(pad, min(local_pos.y(), rect.height() - pad))
        marker.setPos(QPointF(clamped_x, clamped_y))

        self._markers.append(marker)
        self._undo_stack.append(marker)
        self._update_display()
        self.marker_added.emit()
        return marker

    def get_annotations_string(self):
        """输出坐标字符串（归一化比例 0~1）"""
        parts = []
        for m in self._markers:
            x, y = m.get_image_coords()
            parts.append(f"{x},{y},{m.label}")
        return ";".join(parts)

    def get_individual_labels(self):
        """输出 label_1 ~ label_10 的字典"""
        labels = {}
        for i in range(1, 11):
            labels[f"label_{i}"] = ""
        for m in self._markers:
            idx = m.index
            if 1 <= idx <= 10:
                labels[f"label_{idx}"] = m.label
        return labels

    def render_markers_to_image(self, image_path):
        """将当前图片上的所有标记点渲染到一张新图片上。
        标记中心点坐标保持不变，仅外圈放大（6倍）便于 AI 识别。
        返回带标记的新图片路径，如果无标记则返回原路径。"""
        if not self._markers or not image_path:
            return image_path

        from PySide6.QtGui import QImage
        from PySide6.QtCore import Qt as _Qt

        img = QImage(image_path)
        if img.isNull():
            print(f"[render_markers] 无法加载图片: {image_path}")
            return image_path

        img_w = img.width()
        img_h = img.height()

        painter = QPainter(img)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        for m in self._markers:
            nx, ny = m.get_image_coords()
            cx = nx * img_w
            cy = ny * img_h

            # ★ v9.10: 锚点在(cx,cy)是尖角尖端位置（与画布显示一致）
            render_radius = 108
            tip_height = render_radius // 2
            anchor_offset = render_radius + tip_height  # 圆心到尖角底部的距离

            fill = MarkerItem.DEFAULT_COLOR

            # 底部尖角 —— 尖端精确在锚点 (cx, cy)
            tip_poly = QPolygonF([
                QPointF(cx, cy),                                    # ★ 锚点
                QPointF(cx - render_radius / 2, cy - tip_height),
                QPointF(cx + render_radius / 2, cy - tip_height),
            ])
            painter.setPen(_Qt.NoPen)
            painter.setBrush(fill)
            painter.drawPolygon(tip_poly)

            # 外圈白边
            painter.setPen(QPen(QColor(255, 255, 255), 6))
            painter.setBrush(_Qt.NoBrush)
            painter.drawEllipse(QPointF(cx, cy - anchor_offset), render_radius + 4, render_radius + 4)

            # 圆形主体
            painter.setPen(_Qt.NoPen)
            painter.setBrush(fill)
            painter.drawEllipse(QPointF(cx, cy - anchor_offset), render_radius, render_radius)

            # 白色内圈
            inner_r = render_radius - 10
            if inner_r > 0:
                painter.setBrush(_Qt.NoBrush)
                painter.setPen(QPen(QColor(255, 255, 255), 4))
                painter.drawEllipse(QPointF(cx, cy - anchor_offset), inner_r, inner_r)

            # 数字标签 —— 在圆心位置（锚点上方）
            font_size = max(24, int(render_radius * 0.55))
            painter.setPen(QColor(255, 255, 255))
            font = QFont("Arial", font_size, QFont.Bold)
            painter.setFont(font)
            text_rect = QRectF(cx - render_radius, cy - anchor_offset - render_radius,
                               render_radius * 2, render_radius * 2)
            painter.drawText(text_rect, _Qt.AlignCenter, m.label)

        painter.end()

        # 保存到临时文件
        import tempfile
        import time
        base, ext = os.path.splitext(image_path)
        temp_dir = tempfile.gettempdir()
        timestamp = int(time.time() * 1000)
        marked_path = os.path.join(temp_dir, f"marked_{timestamp}_{os.path.basename(image_path)}")
        if not marked_path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
            marked_path += '.png'
        img.save(marked_path)
        print(f"[render_markers] 带标记图片已保存: {marked_path} (标记数: {len(self._markers)})")
        return marked_path

    # ── 内部操作 ────────────────────────────────

    def _on_add_custom_label(self):
        text = self.label_input.text().strip()
        if not text:
            return
        if self._target_item:
            rect = self._target_item.boundingRect()
            cx = rect.center().x()
            cy = rect.center().y()
            marker = self.add_marker_at(
                self._target_item.mapToScene(QPointF(cx, cy)).x(),
                self._target_item.mapToScene(QPointF(cx, cy)).y(),
                text
            )
            self.label_input.clear()
            if marker:
                marker.setSelected(True)
                marker.set_marker_selected(True)

    def _on_preset_label(self, char):
        if self._target_item:
            rect = self._target_item.boundingRect()
            cx = rect.center().x()
            cy = rect.center().y()
            marker = self.add_marker_at(
                self._target_item.mapToScene(QPointF(cx, cy)).x(),
                self._target_item.mapToScene(QPointF(cx, cy)).y(),
                char
            )
            if marker:
                marker.setSelected(True)
                marker.set_marker_selected(True)

    def _on_delete_selected(self):
        to_remove = [m for m in self._markers if m.isSelected() or m._selected]
        for m in to_remove:
            m.setParentItem(None)
            if m.scene():
                m.scene().removeItem(m)
            self._markers.remove(m)
            if m in self._undo_stack:
                self._undo_stack.remove(m)
        self._update_display()
        self.marker_added.emit()

    def _on_undo(self):
        if self._undo_stack:
            m = self._undo_stack.pop()
            m.setParentItem(None)
            if m.scene():
                m.scene().removeItem(m)
            if m in self._markers:
                self._markers.remove(m)
            self._update_display()
            self.marker_added.emit()

    def _on_clear(self):
        for m in self._markers:
            m.setParentItem(None)
            if m.scene():
                m.scene().removeItem(m)
        self._markers.clear()
        self._undo_stack.clear()
        self._next_index = 1
        self._update_display()
        self.marker_added.emit()

    def _on_replace(self):
        text = self.replace_input.text().strip()
        if text:
            coords = self.get_annotations_string()
            full_request = f"{text} | 标记坐标: {coords}"
            self.replace_requested.emit(full_request)

    def _update_display(self):
        """更新坐标显示、标签输出、计数"""
        self.coord_display.setText(self.get_annotations_string())
        self.count_label.setText(f"标记: {len(self._markers)}")
        labels = self.get_individual_labels()
        for i in range(1, 11):
            self.label_outputs[i].setText(labels.get(f"label_{i}", ""))

    def update_positions(self):
        """标记点位置变化后刷新坐标显示"""
        self._update_display()
