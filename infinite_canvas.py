import os
import json
from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsRectItem, QGraphicsItem, QGraphicsObject, QGraphicsProxyWidget, QPushButton,
    QApplication, QStyle
)
from PySide6.QtCore import (
    Qt, Signal, QPointF, QRectF, QTimer, QPropertyAnimation,
    QEasingCurve, Property, QObject, QMimeData, QUrl, QPoint
)
from PySide6.QtGui import (
    QPainter, QPixmap, QColor, QWheelEvent, QMouseEvent,
    QBrush, QPen, QFont, QLinearGradient, QPolygonF, QKeyEvent,
    QKeySequence, QDragEnterEvent, QDropEvent, QDrag
)


from marker_tool import MarkerItem


# ──────────────────────────────────────────────
# 全局撤销栈（由 main_window 初始化时注入）
# ──────────────────────────────────────────────
_undo_stack = None

def set_undo_stack(stack):
    global _undo_stack
    _undo_stack = stack

def get_undo_stack():
    return _undo_stack


# ──────────────────────────────────────────────
# 支持的文件类型
# ──────────────────────────────────────────────
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".ico"}
VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv", ".m4v"}


# ──────────────────────────────────────────────
# ImageItem：可选中、可移动的图片，带全局序号标记
# ──────────────────────────────────────────────
class ImageItem(QGraphicsPixmapItem):
    _global_counter = 0  # 全局计数器

    def __init__(self, pixmap, path):
        super().__init__(pixmap)
        self.path = path
        self.global_index = ImageItem._global_counter
        ImageItem._global_counter += 1
        self.merge_order = -1  # 合并生成时临时序号（-1=未参与合并）
        self.has_markers = False  # 是否已渲染标记到图片上
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setAcceptHoverEvents(True)
        self._opacity = 1.0
        # 使用 DeviceCoordinateCache 消除拖动时的残影
        self.setCacheMode(QGraphicsItem.DeviceCoordinateCache)

    def get_opacity(self):
        return self._opacity

    def set_opacity(self, val):
        self._opacity = val
        self.setOpacity(val)

    # Qt Property for animation
    qt_opacity = Property(float, get_opacity, set_opacity)

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        # 选中时绘制灰色边框
        if self.isSelected():
            r = self.boundingRect()
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(0, 0, 0, 80), 2))
            painter.drawRoundedRect(r.adjusted(1, 1, -1, -1), 4, 4)
        # 多选时绘制合并序号标记（★ v9.10: 字体5倍放大，不缩放画布也清晰可读）
        if self.merge_order > 0:
            r = self.boundingRect()
            font_size = max(28, int(r.height() * 0.08))  # 根据图片高度自适应，最小28px
            tag_w = int(font_size * 2.2)
            tag_h = int(font_size * 1.4)
            tag_rect = QRectF(r.right() - tag_w - 6, 6, tag_w, tag_h)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 160))
            painter.drawRoundedRect(tag_rect, int(tag_h / 3), int(tag_h / 3))
            painter.setPen(QColor(255, 255, 255))
            painter.setFont(QFont("微软雅黑", font_size, QFont.Bold))
            painter.drawText(tag_rect, Qt.AlignCenter, f"图{self.merge_order}")

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        # 开始追踪移动：记录拖动前位置
        if event.button() == Qt.LeftButton and self.scene():
            from infinite_canvas import get_undo_stack
            stack = get_undo_stack()
            if stack and not stack._frozen:
                canvas = None
                for view in self.scene().views():
                    canvas = view
                    break
                if canvas:
                    canvas._move_before[id(self)] = QPointF(self.pos())
                    canvas._move_tracked_items.add(id(self))

    def mouseReleaseEvent(self, event):
        # 先检查是否有实际移动
        moved = False
        if self.scene():
            from infinite_canvas import get_undo_stack
            stack = get_undo_stack()
            canvas = None
            for view in self.scene().views():
                canvas = view
                break
            if canvas and id(self) in canvas._move_before:
                old_pos = canvas._move_before[id(self)]
                new_pos = self.pos()
                if (old_pos - new_pos).manhattanLength() > 2:
                    moved = True
                # 清理追踪
                del canvas._move_before[id(self)]
                canvas._move_tracked_items.discard(id(self))

        super().mouseReleaseEvent(event)
        # 拖动结束后强制刷新 viewport 消除残影
        self.update()
        if self.scene() and hasattr(self.scene(), 'views'):
            views = self.scene().views()
            if views:
                views[0].viewport().update()

        # 记录移动撤销
        if moved and self.scene():
            from undo_manager import MoveItemsCommand
            stack = get_undo_stack()
            if stack:
                stack.push(MoveItemsCommand(
                    [(self, old_pos, new_pos)],
                    description="移动图片"
                ))


# ──────────────────────────────────────────────
# VideoItem：画布内视频播放（缩略图 + 点击播放 + QVideoSink 帧绘制）
# ──────────────────────────────────────────────
class VideoItem(QGraphicsObject):
    """视频项：缩略图状态下显示封面+播放按钮，点击后用 QVideoSink 拿帧在 paint() 中绘制"""

    def __init__(self, pixmap, path, duration_sec=0):
        super().__init__()
        self.path = path
        self.duration_sec = duration_sec
        self._pixmap = pixmap
        self._hover_play = False
        self._playing = False

        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setAcceptHoverEvents(True)

        # 视频播放组件（懒初始化）
        self._player = None
        self._audio_output = None
        self._video_sink = None      # QVideoSink：提供视频帧数据
        self._current_frame = None   # 最新一帧 QImage
        self._timer = None           # 刷新定时器

    def setPixmap(self, pixmap):
        """更新缩略图"""
        self._pixmap = pixmap
        self.update()

    def pixmap(self):
        return self._pixmap

    def boundingRect(self):
        if self._pixmap and not self._pixmap.isNull():
            return QRectF(0, 0, self._pixmap.width(), self._pixmap.height())
        return QRectF(0, 0, 512, 288)

    def paint(self, painter, option, widget=None):
        option.state &= ~QStyle.StateFlag.State_Selected
        rect = self.boundingRect()

        # 播放状态：绘制视频帧
        if self._playing and self._current_frame is not None:
            painter.drawImage(rect.toRect(), self._current_frame)
            # 叠加底部信息条
            bar_h = 28
            bar_rect = QRectF(0, rect.height() - bar_h, rect.width(), bar_h)
            painter.fillRect(bar_rect, QColor(0, 0, 0, 160))
            painter.setPen(QColor(255, 255, 255))
            painter.setFont(QFont("Arial", 10))
            painter.drawText(bar_rect, Qt.AlignVCenter | Qt.AlignCenter, "▶ 播放中")
            # 选中边框
            if self.isSelected():
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(QColor(0, 0, 0, 80), 2))
                painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 4, 4)
            return

        # 缩略图状态
        if self._pixmap and not self._pixmap.isNull():
            painter.drawPixmap(0, 0, self._pixmap)
        else:
            painter.fillRect(rect, QColor(240, 240, 243))

        # 底部信息条
        bar_h = 28
        bar_rect = QRectF(0, rect.height() - bar_h, rect.width(), bar_h)
        painter.fillRect(bar_rect, QColor(0, 0, 0, 160))

        # 播放三角
        tri_size = 10
        cx, cy = 14, rect.height() - bar_h / 2
        painter.setBrush(QColor(255, 255, 255, 220))
        painter.setPen(Qt.NoPen)
        painter.drawPolygon(QPolygonF([
            QPointF(cx - tri_size/2, cy - tri_size/2),
            QPointF(cx - tri_size/2, cy + tri_size/2),
            QPointF(cx + tri_size/2, cy),
        ]))

        # 时长文字
        if self.duration_sec > 0:
            mins = int(self.duration_sec) // 60
            secs = int(self.duration_sec) % 60
            time_str = f"{mins}:{secs:02d}"
        else:
            time_str = "视频"
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(QFont("Arial", 10))
        painter.drawText(QRectF(28, rect.height() - bar_h, rect.width() - 36, bar_h),
                         Qt.AlignVCenter | Qt.AlignLeft, time_str)

        # 右上角扩展名标签
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 120))
        tag_w, tag_h = 36, 18
        painter.drawRoundedRect(QRectF(rect.width() - tag_w - 4, 4, tag_w, tag_h), 4, 4)
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(QFont("Arial", 8, QFont.Bold))
        ext = os.path.splitext(self.path)[1].upper().lstrip(".") if self.path else "MP4"
        painter.drawText(QRectF(rect.width() - tag_w - 4, 4, tag_w, tag_h),
                         Qt.AlignCenter, ext[:4])

        # 悬停：中央大播放按钮 + 半透明遮罩
        if self._hover_play:
            painter.fillRect(rect, QColor(0, 0, 0, 60))
            play_r = 28
            pcx, pcy = rect.width() / 2, rect.height() / 2
            painter.setBrush(QColor(255, 255, 255, 200))
            painter.setPen(QPen(QColor(255, 255, 255, 100), 2))
            painter.drawEllipse(QPointF(pcx, pcy), play_r, play_r)
            painter.setBrush(QColor(60, 60, 60, 220))
            painter.setPen(Qt.NoPen)
            painter.drawPolygon(QPolygonF([
                QPointF(pcx - 10, pcy - 14),
                QPointF(pcx - 10, pcy + 14),
                QPointF(pcx + 14, pcy),
            ]))

        # 选中边框
        if self.isSelected():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(0, 0, 0, 80), 2))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 4, 4)

    def hoverEnterEvent(self, event):
        if not self._playing:
            self._hover_play = True
            self.setCursor(Qt.PointingHandCursor)
            self.update()

    def hoverLeaveEvent(self, event):
        self._hover_play = False
        self.setCursor(Qt.ArrowCursor)
        self.update()

    def mouseDoubleClickEvent(self, event):
        """双击切换播放/暂停"""
        self._toggle_play()
        event.accept()

    def mousePressEvent(self, event):
        """单击：如果是播放状态且点击在播放区域，切换暂停；否则正常选中/拖拽"""
        if event.button() == Qt.LeftButton and not self._playing:
            # 记录拖拽起始位置
            if self.scene():
                from infinite_canvas import get_undo_stack
                stack = get_undo_stack()
                if stack and not stack._frozen:
                    canvas = None
                    for view in self.scene().views():
                        canvas = view
                        break
                    if canvas:
                        canvas._move_before[id(self)] = QPointF(self.pos())
                        canvas._move_tracked_items.add(id(self))
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        moved = False
        old_pos = None
        if self.scene() and not self._playing:
            from infinite_canvas import get_undo_stack
            stack = get_undo_stack()
            canvas = None
            for view in self.scene().views():
                canvas = view
                break
            if canvas and id(self) in canvas._move_before:
                old_pos = canvas._move_before[id(self)]
                new_pos = self.pos()
                if (old_pos - new_pos).manhattanLength() > 2:
                    moved = True
                del canvas._move_before[id(self)]
                canvas._move_tracked_items.discard(id(self))
        super().mouseReleaseEvent(event)
        if moved and old_pos is not None and self.scene():
            from undo_manager import MoveItemsCommand
            stack = get_undo_stack()
            if stack:
                stack.push(MoveItemsCommand(
                    [(self, old_pos, self.pos())],
                    description="移动视频"
                ))

    # ── 画布内播放（QVideoSink 帧绘制方案）────────
    def _toggle_play(self):
        """切换播放/停止"""
        if self._playing:
            self._stop_playback()
        else:
            self._start_playback()

    def _start_playback(self):
        """使用 QVideoSink 获取视频帧，在 paint() 中手动绘制"""
        print(f"[VideoItem] _start_playback 被调用, path={self.path}")
        try:
            from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink
            print("[VideoItem] QtMultimedia 导入成功（含 QVideoSink）")
        except ImportError as e:
            print(f"[VideoItem] QtMultimedia 不可用: {e}")
            if self.path and os.path.isfile(self.path):
                os.startfile(self.path)
            return

        if not self.path or not os.path.isfile(self.path):
            print(f"[VideoItem] 视频文件不存在: {self.path}")
            return

        # 如果已经在播放，先停掉
        if self._playing:
            self._stop_playback()

        rect = self.boundingRect()
        w, h = int(rect.width()), int(rect.height())
        print(f"[VideoItem] 缩略图尺寸: {w}x{h}")

        # ── 用 QVideoSink 替代 QVideoWidget ──
        # QVideoSink 直接提供 QImage 帧，不走 widget 渲染管线，
        # 完全绕开 QGraphicsProxyWidget + QVideoWidget 的黑屏问题
        self._video_sink = QVideoSink()
        self._video_sink.videoFrameChanged.connect(self._on_video_frame)

        self._audio_output = QAudioOutput()
        self._player = QMediaPlayer()
        self._player.setAudioOutput(self._audio_output)
        self._player.setVideoOutput(self._video_sink)

        abs_path = os.path.abspath(self.path)
        print(f"[VideoItem] 设置视频源: {abs_path}")
        self._player.setSource(QUrl.fromLocalFile(abs_path))

        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.errorOccurred.connect(self._on_player_error)

        self._playing = True
        self.update()

        # 启动刷新定时器：每 33ms 刷新一帧（约 30fps）
        if self._timer is None or not hasattr(self, '_timer') or self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(33)  # ~30fps
        self._timer.timeout.connect(self.update)
        self._timer.start()

        print(f"[VideoItem] 开始播放: {self.path}")
        self._player.play()

    def _on_video_frame(self, frame):
        """QVideoSink 新帧回调：将视频帧转为 QImage 供 paint() 使用"""
        if frame.isValid():
            img = frame.toImage()
            if not img.isNull():
                self._current_frame = img

    def _stop_playback(self):
        """停止播放，回到缩略图"""
        # 停止刷新定时器
        if self._timer and (hasattr(self._timer, 'isActive') and self._timer.isActive()):
            self._timer.stop()

        if self._player:
            self._player.stop()
            self._player.deleteLater()
            self._player = None
        if self._audio_output:
            self._audio_output.deleteLater()
            self._audio_output = None
        if self._video_sink:
            self._video_sink.deleteLater()
            self._video_sink = None

        self._current_frame = None
        self._playing = False
        self.update()
        print(f"[VideoItem] 停止播放")

    def _on_media_status(self, status):
        """播放结束回到缩略图"""
        from PySide6.QtMultimedia import QMediaPlayer
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            print("[VideoItem] 播放结束")
            self._stop_playback()

    def _on_player_error(self, error, error_str):
        print(f"[VideoItem] 播放错误: {error}, {error_str}")
        self._stop_playback()

    def cleanup(self):
        """清理播放器资源"""
        self._stop_playback()


# ──────────────────────────────────────────────
# GeneratingItem：带独立进度条的生成占位框
# ──────────────────────────────────────────────
class GeneratingItem(QGraphicsItem):
    def __init__(self, width, height, task_id="", parent=None):
        super().__init__(parent)
        self._w = width
        self._h = height
        self._task_id = task_id
        self._progress = 0        # 0~100
        self._status_text = "排队中…"
        self._sweep_x = -width
        self.setFlag(QGraphicsItem.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.ItemIsMovable, False)

        self._timer = QTimer()
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._advance_sweep)
        self._timer.start()

    @property
    def task_id(self):
        return self._task_id

    def set_progress(self, value, text=""):
        """更新进度条（0~100）和状态文字"""
        self._progress = max(0, min(100, value))
        if text:
            self._status_text = text
        self.update()

    def _advance_sweep(self):
        self._sweep_x += 8
        if self._sweep_x > self._w + 80:
            self._sweep_x = -80
        self.update()

    def stop(self):
        self._timer.stop()

    def boundingRect(self):
        return QRectF(0, 0, self._w, self._h)

    def paint(self, painter, option, widget=None):
        r = QRectF(0, 0, self._w, self._h)

        # 背景
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(240, 240, 243))
        painter.drawRoundedRect(r, 8, 8)

        # 扫光动画
        sweep_w = 80
        grad = QLinearGradient(self._sweep_x - sweep_w, 0, self._sweep_x + sweep_w, 0)
        grad.setColorAt(0.0, QColor(255, 255, 255, 0))
        grad.setColorAt(0.5, QColor(255, 255, 255, 120))
        grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(grad)
        painter.drawRoundedRect(r, 8, 8)

        # 虚线边框
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(180, 180, 190), 1.5, Qt.DashLine))
        painter.drawRoundedRect(r.adjusted(0.75, 0.75, -0.75, -0.75), 8, 8)

        # 状态文字（上方）
        painter.setPen(QColor(120, 120, 135))
        painter.setFont(QFont("微软雅黑", 10))
        text_rect = QRectF(0, 0, self._w, self._h - 32)
        painter.drawText(text_rect, Qt.AlignCenter, self._status_text)

        # 进度条（底部）
        bar_h = 6
        bar_margin = 16
        bar_y = self._h - bar_h - 14
        bar_rect = QRectF(bar_margin, bar_y, self._w - bar_margin * 2, bar_h)

        # 进度条底色
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(210, 210, 215))
        painter.drawRoundedRect(bar_rect, 3, 3)

        # 进度条填充
        if self._progress > 0:
            fill_w = bar_rect.width() * (self._progress / 100.0)
            fill_rect = QRectF(bar_margin, bar_y, fill_w, bar_h)
            fill_grad = QLinearGradient(bar_margin, bar_y, bar_margin + fill_w, bar_y)
            fill_grad.setColorAt(0.0, QColor(100, 100, 110))
            fill_grad.setColorAt(1.0, QColor(160, 160, 170))
            painter.setBrush(fill_grad)
            painter.drawRoundedRect(fill_rect, 3, 3)

        # 百分比文字
        painter.setPen(QColor(140, 140, 155))
        painter.setFont(QFont("微软雅黑", 8))
        pct_rect = QRectF(0, bar_y + bar_h + 1, self._w, 14)
        painter.drawText(pct_rect, Qt.AlignCenter, f"{self._progress}%")


# 兼容旧引用
PlaceholderItem = GeneratingItem


# ──────────────────────────────────────────────
# CompareItem：左右滑动对比（原图 | 生成图）
# 修复：拖动分割线时强制整区域重绘，消除残影
# ──────────────────────────────────────────────
class CompareItem(QGraphicsObject):
    def __init__(self, orig_pixmap, gen_pixmap, path_orig, path_gen):
        """
        orig_pixmap: 渲染图（带标记）的像素图，决定整体画布尺寸
        gen_pixmap:  生成结果图（目标比例），居中缩放适配画布，白色填充预览
        path_orig:   渲染图路径
        path_gen:    生成结果图路径（导出时直接保存此文件，不含白色填充）
        """
        super().__init__()
        self.orig = orig_pixmap
        self.path = path_gen      # 外部访问 path 时返回生成图（下载/另存用）
        self.path_orig = path_orig
        self.path_gen = path_gen  # 原始生成图路径（不含白色填充）

        # 画布尺寸由渲染图（orig）决定，严格保持其原始比例
        self._w = orig_pixmap.width()
        self._h = orig_pixmap.height()

        # 将生成图缩放到画布内（保持比例，居中，白色填充四周）
        self.gen_scaled = gen_pixmap.scaled(
            self._w, self._h,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )
        # 记录居中偏移（让对比右侧居中显示）
        self._gen_offset_x = (self._w - self.gen_scaled.width()) // 2
        self._gen_offset_y = (self._h - self.gen_scaled.height()) // 2

        self._divider = self._w / 2
        self._dragging = False
        self._opacity_val = 1.0
        self.merge_order = -1

        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setAcceptHoverEvents(True)
        self.setCacheMode(QGraphicsItem.DeviceCoordinateCache)

    def get_opacity(self):
        return self._opacity_val

    def set_opacity_anim(self, val):
        self._opacity_val = val
        self.setOpacity(val)

    qt_opacity = Property(float, get_opacity, set_opacity_anim)

    def boundingRect(self):
        return QRectF(-2, -2, self._w + 4, self._h + 4)

    def paint(self, painter, option, widget=None):
        r = QRectF(0, 0, self._w, self._h)
        d = self._divider

        # 背景填白（预览用白色填充区域）
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255))
        painter.drawRect(r)

        # 左半：渲染图（orig，完整填满）
        painter.save()
        painter.setClipRect(QRectF(0, 0, max(d, 0), self._h))
        painter.drawPixmap(0, 0, self._w, self._h, self.orig)
        painter.restore()

        # 右半：生成图（居中缩放，白色填充边距）
        painter.save()
        painter.setClipRect(QRectF(d, 0, max(self._w - d, 0), self._h))
        # 先填白色背景（空白区域）
        painter.fillRect(QRectF(d, 0, max(self._w - d, 0), self._h), QColor(255, 255, 255))
        # 居中绘制生成图
        painter.drawPixmap(
            self._gen_offset_x,
            self._gen_offset_y,
            self.gen_scaled
        )
        painter.restore()

        # 分割白线
        painter.setClipping(False)
        painter.setPen(QPen(QColor(255, 255, 255, 220), 2))
        painter.drawLine(QPointF(d, 0), QPointF(d, self._h))

        # 中心拖拽圆圈 + 箭头
        cx, cy = d, self._h / 2
        painter.setBrush(QColor(255, 255, 255, 230))
        painter.setPen(QPen(QColor(100, 100, 100), 1))
        painter.drawEllipse(QPointF(cx, cy), 14, 14)
        painter.setPen(QPen(QColor(80, 80, 80), 2))
        for dx, direction in [(-6, -1), (6, 1)]:
            ax = cx + dx
            painter.drawLine(QPointF(ax, cy - 5), QPointF(ax + direction*4, cy))
            painter.drawLine(QPointF(ax, cy + 5), QPointF(ax + direction*4, cy))

        # 选中边框
        if self.isSelected():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(0, 0, 0, 80), 2))
            painter.drawRoundedRect(r.adjusted(1, 1, -1, -1), 4, 4)

    def hoverMoveEvent(self, event):
        if abs(event.pos().x() - self._divider) < 20:
            self.setCursor(Qt.SizeHorCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    def mousePressEvent(self, event):
        if abs(event.pos().x() - self._divider) < 20:
            self._dragging = True
            event.accept()
        else:
            # 开始追踪移动
            if event.button() == Qt.LeftButton and self.scene():
                from infinite_canvas import get_undo_stack
                stack = get_undo_stack()
                if stack and not stack._frozen:
                    canvas = None
                    for view in self.scene().views():
                        canvas = view
                        break
                    if canvas:
                        canvas._move_before[id(self)] = QPointF(self.pos())
                        canvas._move_tracked_items.add(id(self))
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            x = max(0, min(event.pos().x(), self._w))
            self._divider = x
            # 清除缓存强制完全重绘（DeviceCoordinateCache 需要手动失效）
            self.update()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        # 检查是否有实际移动（非分割线拖动）
        moved = False
        old_pos = None
        if self.scene() and not self._dragging:
            from infinite_canvas import get_undo_stack
            stack = get_undo_stack()
            canvas = None
            for view in self.scene().views():
                canvas = view
                break
            if canvas and id(self) in canvas._move_before:
                old_pos = canvas._move_before[id(self)]
                new_pos = self.pos()
                if (old_pos - new_pos).manhattanLength() > 2:
                    moved = True
                del canvas._move_before[id(self)]
                canvas._move_tracked_items.discard(id(self))

        self._dragging = False
        self.update()
        # 刷新 viewport 确保无残影
        if self.scene() and self.scene().views():
            self.scene().views()[0].viewport().update()
        super().mouseReleaseEvent(event)

        # 记录移动撤销
        if moved and old_pos is not None:
            from undo_manager import MoveItemsCommand
            stack = get_undo_stack()
            if stack:
                stack.push(MoveItemsCommand(
                    [(self, old_pos, self.pos())],
                    description="移动对比图"
                ))


# ──────────────────────────────────────────────
# 视频缩略图提取
# ──────────────────────────────────────────────
def _has_ffmpeg():
    """检测系统是否安装了 ffmpeg（含已知安装路径回退）"""
    import shutil
    if shutil.which("ffmpeg") is not None:
        return True
    # 回退：已知安装路径
    fallback = r"C:\ffmpeg\ffmpeg-8.1-essentials_build\bin\ffmpeg.exe"
    if os.path.isfile(fallback):
        # 将 bin 目录加入 PATH 以便 ffprobe 也能找到
        bin_dir = os.path.dirname(fallback)
        os.environ["PATH"] = bin_dir + ";" + os.environ.get("PATH", "")
        return True
    return False

def _extract_video_thumbnail_ffmpeg(video_path, max_width=512):
    """仅用 ffmpeg 提取视频缩略图和时长（在子线程中运行）"""
    import subprocess
    try:
        probe_cmd = ["ffprobe", "-v", "quiet", "-print_format", "json",
                     "--show_format", video_path]
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10)
        duration = 0
        if probe_result.returncode == 0:
            info = json.loads(probe_result.stdout)
            duration = float(info.get("format", {}).get("duration", 0))

        thumb_cmd = ["ffmpeg", "-y", "-i", video_path, "-vframes", "1",
                     "-vf", f"scale={max_width}:-1", "-f", "image2pipe",
                     "-vcodec", "png", "-"]
        result = subprocess.run(thumb_cmd, capture_output=True, timeout=15)
        if result.returncode == 0 and result.stdout:
            pixmap = QPixmap()
            pixmap.loadFromData(result.stdout)
            if not pixmap.isNull():
                return pixmap, duration
    except Exception as e:
        print(f"[视频缩略图] ffmpeg 提取失败: {e}")
    return None, 0


def _extract_video_thumbnail_win32(video_path, max_width=512):
    """用 OpenCV (cv2) 提取视频首帧和时长（不依赖 ffmpeg）"""
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            print(f"[Win32缩略图] cv2 无法打开视频: {video_path}")
            return None

        # 获取时长
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        duration = frame_count / fps if fps > 0 else 0

        # 读取第一帧
        ret, frame = cap.read()
        cap.release()

        if not ret or frame is None:
            print(f"[Win32缩略图] cv2 读取首帧失败")
            return None

        # BGR → RGB
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = frame_rgb.shape
        bytes_per_line = ch * w

        from PySide6.QtGui import QImage
        qimg = QImage(frame_rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
        if qimg.isNull():
            return None

        pixmap = QPixmap.fromImage(qimg.copy())  # .copy() 确保数据独立
        if not pixmap.isNull() and pixmap.width() > max_width:
            pixmap = pixmap.scaledToWidth(max_width, Qt.SmoothTransformation)

        print(f"[Win32缩略图] cv2 提取成功: {pixmap.width()}x{pixmap.height()}, duration={duration:.1f}s")
        return pixmap

    except ImportError:
        print("[Win32缩略图] cv2 未安装")
        return None
    except Exception as e:
        print(f"[Win32缩略图] 异常: {e}")
        return None


def _make_video_icon_pixmap(max_width=384):
    """生成带播放图标的视频占位图（不依赖外部工具）"""
    h = int(max_width * 9 / 16)
    pixmap = QPixmap(max_width, h)
    pixmap.fill(QColor(240, 240, 243))

    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)

    # 中央播放按钮圆圈
    cx, cy = max_width / 2, h / 2
    circle_r = min(max_width, h) * 0.15
    p.setBrush(QColor(0, 0, 0, 50))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPointF(cx, cy), circle_r, circle_r)

    # 白色三角
    tri_r = circle_r * 0.45
    p.setBrush(QColor(255, 255, 255, 200))
    triangle = QPolygonF([
        QPointF(cx - tri_r * 0.6, cy - tri_r),
        QPointF(cx - tri_r * 0.6, cy + tri_r),
        QPointF(cx + tri_r, cy),
    ])
    p.drawPolygon(triangle)

    # 底部文字
    p.setPen(QColor(140, 140, 150))
    p.setFont(QFont("微软雅黑", 10))
    p.drawText(QRectF(0, h - 32, max_width, 28), Qt.AlignCenter, "双击播放视频")
    p.end()
    return pixmap


def _make_video_placeholder_pixmap(max_width=512):
    """生成视频加载中占位图（瞬间完成，用于异步加载前的展示）"""
    return _make_video_icon_pixmap(max_width)


# ──────────────────────────────────────────────
# 跨线程信号桥接（子线程 → 主线程更新 VideoItem）
# ──────────────────────────────────────────────
class _ThumbnailBridge(QObject):
    """子线程提取完缩略图后，通过此信号安全地回到主线程更新 VideoItem"""
    ready = Signal(object, QPixmap, float)   # (video_item, pixmap, duration)

_bridge = _ThumbnailBridge()


# ──────────────────────────────────────────────
# InfiniteCanvas 主画布
# ──────────────────────────────────────────────
class InfiniteCanvas(QGraphicsView):
    image_selected = Signal(object, QPointF)      # 单图选中
    multi_selected = Signal(list)                  # 多图选中（传出选中的item列表）
    selection_cleared = Signal()
    files_dropped = Signal(list)                   # 系统拖拽导入文件路径列表
    item_right_clicked = Signal(object, QPointF)   # 右键点击图片 item 信号
    canvas_scale_changed = Signal(float)           # ★ v9.6: 画布缩放变化信号（传入缩放因子）

    GAP = 20   # 图片间距（横向排列用）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor(255, 255, 255))
        self.scene.setSceneRect(-10000, -10000, 20000, 20000)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.RubberBandDrag)  # 启用框选
        # 使用 FullViewportUpdate 确保拖动时无残影
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)

        # 使用 QOpenGLWidget 作为 viewport，让 QVideoWidget 能在 QGraphicsProxyWidget 内正确渲染视频帧
        # 不设置的话，QVideoWidget 只出声音不出画面（纯 QWidget viewport 不支持硬件加速渲染）
        try:
            from PySide6.QtOpenGLWidgets import QOpenGLWidget
            from PySide6.QtGui import QSurfaceFormat
            fmt = QSurfaceFormat()
            fmt.setVersion(2, 1)  # 兼容性最好的版本
            fmt.setProfile(QSurfaceFormat.CompatibilityProfile)
            fmt.setSwapBehavior(QSurfaceFormat.DoubleBuffer)
            QSurfaceFormat.setDefaultFormat(fmt)
            gl_widget = QOpenGLWidget()
            gl_widget.setFormat(fmt)
            self.setViewport(gl_widget)
            print("[Canvas] QOpenGLWidget viewport 已设置")
        except ImportError as e:
            print(f"[Canvas] QOpenGLWidget 不可用，视频播放可能无画面: {e}")
        except Exception as e:
            print(f"[Canvas] 设置 OpenGL viewport 失败: {e}")

        # ★ v9.6: 当前画布缩放因子
        self._canvas_scale = 1.0

        # 启用系统拖放
        self.setAcceptDrops(True)

        self._is_panning = False
        self._pan_start = QPointF()
        self._selection_order = []   # 记录框选顺序

        # ── 画布内拖拽到工作流面板的状态追踪 ──
        self._drag_start_pos = None     # 拖拽起始视口坐标
        self._drag_source_item = None   # 拖拽的图片 item

        # 复制剪贴板缓存
        self._clipboard_paths = []
        self._clipboard_positions = []   # 对应 item 的场景坐标

        # ── 移动追踪：记录拖动前的位置 ──
        self._move_tracking = False
        self._move_before = {}   # id(item) -> old_pos (QPointF)
        self._move_tracked_items = set()  # 正在拖动的 item id 集合

        self.scene.selectionChanged.connect(self.on_selection_changed)

        # 连接跨线程缩略图信号
        _bridge.ready.connect(self._apply_video_thumbnail)

    # ────────────────────────────────────────
    # 计算当前所有 item 最右边的 X 坐标
    # ────────────────────────────────────────
    def get_rightmost_x(self):
        """返回画布上所有图片/对比图的最右 X 坐标，没有任何 item 则返回 0"""
        max_x = None
        for item in self.scene.items():
            if isinstance(item, (ImageItem, CompareItem, VideoItem)):
                r = item.sceneBoundingRect()
                if max_x is None or r.right() > max_x:
                    max_x = r.right()
        return max_x if max_x is not None else 0

    # ── 添加图片 ──────────────────────────────
    def add_image(self, image_path, x=0, y=0):
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            print(f"[Canvas] QPixmap 加载失败: {image_path}")
            return None
        # 无损显示：不压缩原图像素
        item = ImageItem(pixmap, image_path)
        item.setPos(x, y)
        self.scene.addItem(item)
        self.centerOn(item)
        return item

    def add_image_with_fade(self, image_path, x=0, y=0):
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            print(f"[Canvas] QPixmap 加载失败: {image_path}")
            return None
        if pixmap.width() > 1024:
            pixmap = pixmap.scaledToWidth(1024, Qt.SmoothTransformation)
        item = ImageItem(pixmap, image_path)
        item.setOpacity(0)
        item.setPos(x, y)
        self.scene.addItem(item)

        anim = QPropertyAnimation(item, b"qt_opacity")
        anim.setDuration(300)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start(QPropertyAnimation.DeleteWhenStopped)
        item._fade_anim = anim

        self.centerOn(item)
        return item

    # ── 批量导入并横向排列（不堆叠）──────────
    def add_files_in_row(self, file_paths, start_x=None, row_y=None):
        """按文件路径列表横向依次放置，自动排在现有图片最右侧。
        返回新增的 item 列表（供撤销系统使用）。"""
        """
        按文件路径列表横向依次放置，自动排在现有图片最右侧。
        start_x: 起始 X（None = 自动接在最右边）
        row_y:   Y 坐标（None = 视图中心 Y）
        """
        if not file_paths:
            return []

        if start_x is None:
            start_x = self.get_rightmost_x() + self.GAP

        if row_y is None:
            view_center = self.viewport().rect().center()
            row_y = self.mapToScene(view_center).y() - 256

        added = []
        cursor_x = start_x

        for file_path in file_paths:
            ext = os.path.splitext(file_path)[1].lower()
            # 先加载 pixmap 获取宽度以便定位
            if ext in IMAGE_EXTS:
                pixmap = QPixmap(file_path)
                if pixmap.isNull():
                    print(f"[Canvas] 加载失败: {file_path}")
                    continue
                # 无损显示：不压缩原图像素
                item = ImageItem(pixmap, file_path)
                item.setPos(cursor_x, row_y)
                self.scene.addItem(item)
                cursor_x += pixmap.width() + self.GAP
                added.append(item)

            elif ext in VIDEO_EXTS:
                # 先用占位图，避免 ffmpeg 同步调用卡住 UI
                placeholder_px = _make_video_placeholder_pixmap(512)
                item = VideoItem(placeholder_px, file_path, duration_sec=0)
                item.setPos(cursor_x, row_y)
                self.scene.addItem(item)
                item_w = placeholder_px.width()
                cursor_x += item_w + self.GAP
                added.append(item)
                # 异步提取视频缩略图
                self._load_video_thumbnail_async(item, file_path)

        if added:
            self.centerOn(added[-1])
        return added

    # ── 添加视频 ──────────────────────────────
    def add_video(self, video_path, x=0, y=0):
        placeholder_px = _make_video_placeholder_pixmap(512)
        item = VideoItem(placeholder_px, video_path, duration_sec=0)
        item.setPos(x, y)
        self.scene.addItem(item)
        self.centerOn(item)
        self._load_video_thumbnail_async(item, video_path)
        return item

    def add_file(self, file_path, x=0, y=0):
        ext = os.path.splitext(file_path)[1].lower()
        if ext in IMAGE_EXTS:
            return self.add_image(file_path, x, y)
        elif ext in VIDEO_EXTS:
            return self.add_video(file_path, x, y)
        return None

    # ── 异步加载视频缩略图 ──────────────────────
    def _load_video_thumbnail_async(self, video_item, video_path):
        """异步提取视频缩略图。优先 ffmpeg，回退 OpenCV (cv2)。"""
        import threading
        def _worker():
            if _has_ffmpeg():
                pixmap, duration = _extract_video_thumbnail_ffmpeg(video_path)
                if pixmap is not None and not pixmap.isNull():
                    print(f"[异步缩略图] ffmpeg 完成: {pixmap.width()}x{pixmap.height()}, dur={duration}s")
                    _bridge.ready.emit(video_item, pixmap, duration)
                    return
                print(f"[异步缩略图] ffmpeg 失败，尝试 cv2")

            # cv2 回退
            result = _extract_video_thumbnail_win32(video_path)
            if result and not result.isNull():
                _bridge.ready.emit(video_item, result, 0)
            else:
                print(f"[异步缩略图] 所有方式都提取失败")
        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def _apply_video_thumbnail(self, video_item, pixmap, duration):
        """主线程中更新 VideoItem 的缩略图（由 _bridge.ready 信号触发）"""
        if video_item.scene() is not self.scene:
            print(f"[异步缩略图] item 已不在场景中，跳过更新")
            return
        old_w = video_item.boundingRect().width()
        video_item.setPixmap(pixmap)
        video_item.duration_sec = duration
        new_w = pixmap.width()
        video_item.update()
        self.scene().update()
        print(f"[异步缩略图] 已更新 VideoItem: {old_w:.0f} → {new_w}x{pixmap.height()}, duration={duration}s")

    # ── 占位框 ──────────────────────────────
    def add_placeholder(self, x, y, width, height, task_id=""):
        item = GeneratingItem(width, height, task_id=task_id)
        item.setPos(x, y)
        self.scene.addItem(item)
        return item

    def remove_item(self, item):
        if item and item.scene() == self.scene:
            # 清理视频播放器资源
            if isinstance(item, VideoItem):
                item.cleanup()
            item.scene().removeItem(item)

    # ── 对比视图 ──────────────────────────────
    def replace_with_compare(self, placeholder_item, orig_path, gen_path):
        """
        替换占位框为对比图。
        orig_path: 带标记的渲染图路径（决定画布基准尺寸和对比左侧）
        gen_path:  AI 生成结果图路径（目标比例，居中缩放填入右侧，导出时直接保存原始）
        返回 (result_item, placeholder_snapshot) 元组
        """
        pos = placeholder_item.pos()
        w = placeholder_item._w
        h = placeholder_item._h

        placeholder_snapshot = {
            'pos': QPointF(pos),
            'w': w,
            'h': h,
            'task_id': placeholder_item._task_id,
        }

        placeholder_item.stop()
        self.scene.removeItem(placeholder_item)

        print(f"[Canvas] 替换占位框，渲染图: {orig_path}, 生成图: {gen_path}")

        orig_px = QPixmap(orig_path) if orig_path and os.path.isfile(orig_path) else None
        gen_px = QPixmap(gen_path) if gen_path and os.path.isfile(gen_path) else None

        if orig_px:
            print(f"[Canvas] 渲染图加载: {orig_px.width()}x{orig_px.height()}, isNull={orig_px.isNull()}")
        else:
            print(f"[Canvas] 渲染图未加载: orig_path={orig_path}")

        if gen_px:
            print(f"[Canvas] 生成图加载: {gen_px.width()}x{gen_px.height()}, isNull={gen_px.isNull()}")
        else:
            print(f"[Canvas] 生成图未加载: gen_path={gen_path}")

        if orig_px and not orig_px.isNull() and gen_px and not gen_px.isNull():
            # 渲染图缩放到 1024 宽以内（保持显示清晰）
            if orig_px.width() > 1024:
                orig_px = orig_px.scaledToWidth(1024, Qt.SmoothTransformation)
            # 生成图保持原始，CompareItem 内部按画布比例居中缩放

            item = CompareItem(orig_px, gen_px, orig_path, gen_path)
            item.setOpacity(0)
            item.setPos(pos)
            self.scene.addItem(item)

            anim = QPropertyAnimation(item, b"qt_opacity")
            anim.setDuration(300)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.start(QPropertyAnimation.DeleteWhenStopped)
            item._anim = anim

            self.centerOn(item)
            return item, placeholder_snapshot

        if gen_px and not gen_px.isNull():
            if gen_px.width() > 1024:
                gen_px = gen_px.scaledToWidth(1024, Qt.SmoothTransformation)
            img_item = ImageItem(gen_px, gen_path)
            img_item.setOpacity(0)
            img_item.setPos(pos)
            self.scene.addItem(img_item)

            anim = QPropertyAnimation(img_item, b"qt_opacity")
            anim.setDuration(300)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.start(QPropertyAnimation.DeleteWhenStopped)
            img_item._anim = anim

            self.centerOn(img_item)
            return img_item, placeholder_snapshot

        return None, placeholder_snapshot

    # ── 选中逻辑 ──────────────────────────────
    def on_selection_changed(self):
        selected = self.scene.selectedItems()
        image_items = [i for i in selected if isinstance(i, (ImageItem, CompareItem, VideoItem))]

        if len(image_items) == 0:
            self.selection_cleared.emit()
        elif len(image_items) == 1:
            item = image_items[0]
            rect = item.sceneBoundingRect()
            bottom_center = QPointF(rect.center().x(), rect.bottom())
            self.image_selected.emit(item, bottom_center)
            self.multi_selected.emit([])
        else:
            self._update_selection_order(image_items)
            self.multi_selected.emit(self._selection_order)

    def _update_selection_order(self, current_items):
        existing_set = set(id(i) for i in self._selection_order)
        new_items = [i for i in current_items if id(i) not in existing_set]
        current_set = set(id(i) for i in current_items)
        self._selection_order = [i for i in self._selection_order if id(i) in current_set]
        self._selection_order.extend(new_items)

    def clear_merge_orders(self):
        for item in self.scene.items():
            if isinstance(item, (ImageItem, CompareItem)):
                item.merge_order = -1
                item.update()

    def mark_merge_orders(self, ordered_items):
        self.clear_merge_orders()
        for i, item in enumerate(ordered_items, 1):
            item.merge_order = i
            item.update()

    # ── 删除选中项 ──────────────────────────────
    def delete_selected(self):
        """删除选中项，并返回快照列表供撤销系统使用"""
        from undo_manager import _snapshot_item
        from marker_tool import MarkerItem

        # 收集要删除的 item（排除 MarkerItem，它们随父 item 一起删除）
        items_to_delete = []
        for item in self.scene.selectedItems():
            if isinstance(item, MarkerItem):
                # 独立被选中的 MarkerItem（不在父 item 的选中范围内）
                items_to_delete.append(item)
            elif isinstance(item, (ImageItem, CompareItem, VideoItem)):
                items_to_delete.append(item)

        # 拍快照
        snapshots = []
        for item in items_to_delete:
            if isinstance(item, MarkerItem):
                # 单独删除的标记点，拍简单快照
                parent = item.parentItem()
                snapshots.append({
                    'type': 'MarkerItem',
                    'marker': _snapshot_marker(item),
                    'parent_item': parent,
                })
            else:
                snapshots.append(_snapshot_item(item))

        # 执行删除
        for item in items_to_delete:
            if isinstance(item, MarkerItem):
                item.setParentItem(None)
                if item.scene():
                    item.scene().removeItem(item)
            else:
                # 先移除子 MarkerItem
                for child in list(item.childItems()):
                    if isinstance(child, MarkerItem):
                        child.setParentItem(None)
                        if child.scene():
                            child.scene().removeItem(child)
                self.scene.removeItem(item)

        self._selection_order.clear()
        return snapshots

    # ── 全选 ────────────────────────────────────
    def select_all(self):
        for item in self.scene.items():
            if isinstance(item, (ImageItem, CompareItem, VideoItem)):
                item.setSelected(True)

    # ── 复制（记录选中图片的路径 + 场景坐标）───────────────
    def copy_selected(self):
        self._clipboard_paths = []
        self._clipboard_positions = []   # 每个 item 的场景坐标
        for item in self.scene.selectedItems():
            if isinstance(item, (ImageItem, CompareItem, VideoItem)):
                p = getattr(item, 'path', None)
                if p and os.path.isfile(p):
                    self._clipboard_paths.append(p)
                    self._clipboard_positions.append(item.scenePos())
        print(f"[复制] 已复制 {len(self._clipboard_paths)} 个文件路径到内部剪贴板")

    # ── 粘贴（紧靠原图左侧）───────────────────────────────
    def paste(self):
        """粘贴图片，返回新创建的 item 列表供撤销系统使用"""
        if not self._clipboard_paths:
            return []
        self.scene.clearSelection()

        # 先把所有要粘贴的图宽度加载出来，用于计算起始 X
        pixmap_list = []
        for p in self._clipboard_paths:
            px = QPixmap(p)
            if not px.isNull() and px.width() > 1024:
                px = px.scaledToWidth(1024, Qt.SmoothTransformation)
            pixmap_list.append(px)

        total_w = sum(px.width() for px in pixmap_list if not px.isNull()) + \
                  self.GAP * max(0, len(pixmap_list) - 1)

        # 以第一个被复制 item 的位置为参考：粘贴在其左侧
        if self._clipboard_positions:
            ref_pos = self._clipboard_positions[0]
            # 新图组整体右边对齐原图左边（留 GAP）
            start_x = ref_pos.x() - total_w - self.GAP
            row_y = ref_pos.y()
        else:
            # 无位置信息时退化到最右侧
            start_x = self.get_rightmost_x() + self.GAP
            view_center = self.viewport().rect().center()
            row_y = self.mapToScene(view_center).y() - 256

        new_items = self.add_files_in_row(self._clipboard_paths, start_x, row_y)
        for item in new_items:
            item.setSelected(True)
        return new_items

    # ── 系统拖放事件 ──────────────────────────
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            # 检查是否有图片/视频文件
            for url in urls:
                if url.isLocalFile():
                    ext = os.path.splitext(url.toLocalFile())[1].lower()
                    if ext in IMAGE_EXTS or ext in VIDEO_EXTS:
                        event.acceptProposedAction()
                        return
        event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        # 静默处理，防止 "drag leave received before drag enter" 警告
        event.accept()

    def dropEvent(self, event: QDropEvent):
        if not event.mimeData().hasUrls():
            event.ignore()
            return

        file_paths = []
        for url in event.mimeData().urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                ext = os.path.splitext(path)[1].lower()
                if ext in IMAGE_EXTS or ext in VIDEO_EXTS:
                    file_paths.append(path)

        if not file_paths:
            event.ignore()
            return

        event.acceptProposedAction()
        # 拖入位置作为起始 Y
        drop_pos = self.mapToScene(event.position().toPoint())
        row_y = drop_pos.y() - 256
        # 从最右侧排列
        start_x = self.get_rightmost_x() + self.GAP
        added = self.add_files_in_row(file_paths, start_x, row_y)

        if added:
            self.scene.clearSelection()
            for item in added:
                item.setSelected(True)
            # 撤销：撤销拖入 = 从画布移除这些 item
            from undo_manager import AddItemsCommand
            stack = get_undo_stack()
            if stack:
                stack.push(AddItemsCommand(
                    added, self.scene,
                    description=f"拖入 {len(added)} 个文件"
                ))

        self.files_dropped.emit(file_paths)

    # ── 鼠标/键盘事件 ──────────────────────────
    def wheelEvent(self, event: QWheelEvent):
        if event.modifiers() & Qt.ControlModifier:
            # ★ v9.2: Ctrl+滚轮 → 缩放选中的 CanvasTaskCard 组
            self._scale_selected_items(event)
        else:
            # 默认：缩放整个画布视图
            factor = 1.1 if event.angleDelta().y() > 0 else 0.9
            self.scale(factor, factor)
            # ★ v9.6: 全局缩放时更新工具栏字体
            self._notify_canvas_scale_change()
    
    def _scale_selected_items(self, event):
        """★ v9.2: Ctrl+滚轮缩放选中的 CanvasTaskCard。"""
        from workflow_panel import CanvasTaskCard
        selected = [item for item in self.scene.selectedItems() if isinstance(item, CanvasTaskCard)]
        if not selected:
            # 没有选中的任务卡片，退化为全局缩放
            factor = 1.1 if event.angleDelta().y() > 0 else 0.9
            self.scale(factor, factor)
            return
        
        factor = 1.1 if event.angleDelta().y() > 0 else 0.9
        
        for card in selected:
            # 限制缩放范围 0.1 ~ 3.0
            new_scale = card._scale_factor * factor
            new_scale = max(0.1, min(new_scale, 3.0))
            
            # 以鼠标位置为中心缩放
            mouse_scene_pos = self.mapToScene(event.position().toPoint())
            card_pos = card.pos()
            
            # 计算缩放后的偏移（保持鼠标指向位置不变）
            dx = (mouse_scene_pos.x() - card_pos.x()) * (1 - factor)
            dy = (mouse_scene_pos.y() - card_pos.y()) * (1 - factor)
            
            card._scale_factor = new_scale
            card._compute_geometry()
            card.prepareGeometryChange()
            card.setPos(card_pos.x() + dx, card_pos.y() + dy)
            card.update()
        
        # ★ v9.6: 选中卡片缩放后更新工具栏字体
        self._notify_canvas_scale_change()
    
    def _notify_canvas_scale_change(self):
        """★ v9.6: 获取当前画布缩放比例并发送信号。"""
        # 从 transform 获取当前缩放比例（取平均）
        scale_x = self.transform().m11()
        scale_y = self.transform().m22()
        avg_scale = (scale_x + scale_y) / 2.0
        
        if abs(self._canvas_scale - avg_scale) > 0.01:
            self._canvas_scale = avg_scale
            self.canvas_scale_changed.emit(avg_scale)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MiddleButton:
            self._is_panning = True
            self._pan_start = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            self.setDragMode(QGraphicsView.NoDrag)
            event.accept()
            return

        # 右键单击：检查是否点击到图片/对比图/视频（含子项），发出信号
        if event.button() == Qt.RightButton:
            item_under = self.itemAt(event.position().toPoint())
            # 向上查找，处理 MarkerItem 等子项叠在图片上的情况
            target = item_under
            while target is not None and not isinstance(target, (ImageItem, CompareItem, VideoItem)):
                target = target.parentItem()
            if target is not None:
                scene_pos = self.mapToScene(event.position().toPoint())
                self.item_right_clicked.emit(target, scene_pos)
                event.accept()
                return

        if event.button() == Qt.LeftButton:
            # 命中可移动 item → 切 NoDrag 让 ItemIsMovable 生效（任何缩放比例下均可拖动）
            # 命中空白处 → RubberBandDrag 框选
            item_under = self.itemAt(event.position().toPoint())
            # 支持所有设置了 ItemIsMovable 的 item（包括 ImageItem、CanvasTaskCard 等）
            is_movable = (item_under and item_under.isEnabled() and 
                         (item_under.flags() & QGraphicsItem.ItemIsMovable))
            if is_movable:
                self.setDragMode(QGraphicsView.NoDrag)
                # ★ 记录拖拽起始位置和源 item（用于画布内拖拽到工作流面板）
                target = item_under
                while target is not None and not isinstance(target, (ImageItem, CompareItem)):
                    target = target.parentItem()
                if target and isinstance(target, (ImageItem, CompareItem)) and hasattr(target, 'path') and target.path:
                    self._drag_start_pos = event.position().toPoint()
                    self._drag_source_item = target
                else:
                    # 非 ImageItem/CompareItem 的可移动 item（如 CanvasTaskCard），不启动面板拖拽
                    self._drag_start_pos = None
                    self._drag_source_item = None
            else:
                self.setDragMode(QGraphicsView.RubberBandDrag)
                self._drag_start_pos = None
                self._drag_source_item = None

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._is_panning:
            delta = event.position() - self._pan_start
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - delta.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - delta.y()))
            self._pan_start = event.position()
            event.accept()
            return

        # ★ 画布内拖拽到工作流面板：仅在鼠标移出视口边缘时启动 QDrag
        #    画布内的正常拖动完全由 ItemIsMovable 处理，不受干扰
        if (self._drag_start_pos is not None and self._drag_source_item is not None
                and event.buttons() & Qt.LeftButton):
            vp = self.viewport().rect()
            pos = event.position().toPoint()
            # 只在鼠标离开视口边界时才启动面板拖拽（画布内部自由拖动不受影响）
            if not vp.contains(pos):
                item = self._drag_source_item
                path = getattr(item, 'path', None)
                if path:
                    # 创建 QDrag，携带图片路径
                    mime_data = QMimeData()
                    mime_data.setData("application/x-canvas-image", path.encode('utf-8'))
                    drag = QDrag(self)
                    drag.setMimeData(mime_data)
                    # 拖拽预览缩略图
                    pixmap = item.pixmap() if hasattr(item, 'pixmap') else QPixmap()
                    if not pixmap.isNull():
                        thumb = pixmap.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        drag.setPixmap(thumb)
                        drag.setHotSpot(QPoint(0, 0))
                    # 清除拖拽状态
                    self._drag_start_pos = None
                    self._drag_source_item = None
                    drag.exec(Qt.CopyAction)
                    event.accept()
                    return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._is_panning:
            self._is_panning = False
            self.setCursor(Qt.ArrowCursor)
            self.setDragMode(QGraphicsView.RubberBandDrag)
            event.accept()
            return
        # 清除拖拽状态
        self._drag_start_pos = None
        self._drag_source_item = None
        super().mouseReleaseEvent(event)
        # 释放后恢复框选模式，确保下次点击空白处可框选
        if event.button() == Qt.LeftButton:
            self.setDragMode(QGraphicsView.RubberBandDrag)

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        mods = event.modifiers()

        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            # 删除由 main_window 的快捷键处理，这里不再重复处理
            pass

        if mods & Qt.ControlModifier:
            if key == Qt.Key_A:
                self.select_all()
                return
            if key == Qt.Key_C:
                self.copy_selected()
                return
            # Ctrl+V 和 Ctrl+Z 由 main_window 快捷键处理

        super().keyPressEvent(event)
