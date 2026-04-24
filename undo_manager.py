"""
全局撤销管理器 — Command 模式 + UndoStack

设计原则：
1. 每个用户操作封装为一个 Command 对象（do/undo）
2. 全局唯一 UndoStack，Ctrl+Z 撤销最近一步
3. 新操作压栈时自动清空 redo 栈（不支持 Ctrl+Y 重做，简化逻辑）
4. 所有 Command 的 undo 方法自行恢复场景状态，不依赖外部变量

支持的操作类型：
- AddItemsCommand:    导入/拖入/粘贴添加图片
- DeleteItemsCommand: 删除选中图片
- PasteItemsCommand:  粘贴图片（含剪贴板信息）
- MoveItemsCommand:   拖动图片位置
- AddMarkerCommand:   添加标记点
- BakeMarkersCommand: 退出标记模式时渲染标记到图片
- ReplaceGenCommand:  生成完成替换占位框

"""

import copy
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QGraphicsItem


# ══════════════════════════════════════════════════
#  Command 基类
# ══════════════════════════════════════════════════
class Command:
    """撤销命令基类，子类必须实现 undo()"""
    def __init__(self, description=""):
        self.description = description

    def undo(self):
        raise NotImplementedError


# ══════════════════════════════════════════════════
#  UndoStack — 全局撤销栈
# ══════════════════════════════════════════════════
class UndoStack:
    """全局撤销栈，最多保留 MAX_SIZE 步操作记录"""

    MAX_SIZE = 10

    def __init__(self):
        self._stack = []      # Command 列表，栈顶 = 最近操作
        self._frozen = False  # 冻结标志：undo 执行期间禁止压栈

    def push(self, cmd: Command):
        """压入新命令（undo 执行期间自动忽略）"""
        if self._frozen:
            return
        self._stack.append(cmd)
        if len(self._stack) > self.MAX_SIZE:
            self._stack.pop(0)
        print(f"[UndoStack] +{cmd.description}  (栈深度={len(self._stack)})")

    def undo(self):
        """撤销最近一步操作"""
        if not self._stack:
            print("[UndoStack] 栈为空，无法撤销")
            return False
        cmd = self._stack.pop()
        self._frozen = True
        try:
            cmd.undo()
            print(f"[UndoStack] ↩ 撤销: {cmd.description}  (剩余={len(self._stack)})")
        except Exception as e:
            print(f"[UndoStack] 撤销异常: {e}")
        finally:
            self._frozen = False
        return True

    def clear(self):
        self._stack.clear()

    def is_empty(self):
        return len(self._stack) == 0

    def size(self):
        return len(self._stack)

    def peek_description(self):
        """查看栈顶操作描述（用于状态栏提示）"""
        if self._stack:
            return self._stack[-1].description
        return ""


# ══════════════════════════════════════════════════
#  序列化工具：保存/恢复 item 状态快照
# ══════════════════════════════════════════════════
def _snapshot_item(item):
    """捕获一个画布 item 的关键状态，用于后续恢复。
    返回 dict，包含足够的信息重新创建或还原该 item。"""
    from infinite_canvas import ImageItem, CompareItem, VideoItem, GeneratingItem
    from marker_tool import MarkerItem

    snap = {
        'type': type(item).__name__,
        'pos': QPointF(item.pos()),
        'selected': item.isSelected(),
        'zvalue': item.zValue(),
        'id': id(item),
    }

    if isinstance(item, ImageItem):
        snap['path'] = item.path
        snap['has_markers'] = item.has_markers
        snap['merge_order'] = item.merge_order
        snap['global_index'] = item.global_index
        snap['pixmap_size'] = (item.pixmap().width(), item.pixmap().height()) if not item.pixmap().isNull() else (0, 0)
        # 记录子 MarkerItem 的状态
        snap['markers'] = []
        for child in item.childItems():
            if isinstance(child, MarkerItem):
                snap['markers'].append(_snapshot_marker(child))

    elif isinstance(item, CompareItem):
        snap['path'] = item.path
        snap['path_orig'] = item.path_orig
        snap['merge_order'] = item.merge_order
        snap['divider'] = item._divider
        snap['w'] = item._w
        snap['h'] = item._h
        # 记录子 MarkerItem
        snap['markers'] = []
        for child in item.childItems():
            if isinstance(child, MarkerItem):
                snap['markers'].append(_snapshot_marker(child))

    elif isinstance(item, VideoItem):
        snap['path'] = item.path
        snap['duration_sec'] = item.duration_sec

    elif isinstance(item, GeneratingItem):
        snap['task_id'] = item._task_id
        snap['w'] = item._w
        snap['h'] = item._h

    return snap


def _snapshot_marker(marker):
    """捕获标记点状态"""
    return {
        'index': marker.index,
        'label': marker.label,
        'color_name': marker.color_name,
        'local_pos': QPointF(marker.pos()),
    }


# ══════════════════════════════════════════════════
#  AddItemsCommand — 添加图片（导入/拖入/粘贴）
# ══════════════════════════════════════════════════
class AddItemsCommand(Command):
    """撤销添加：从场景中移除这些 item"""

    def __init__(self, items, scene, description="添加图片"):
        super().__init__(description)
        # 保存每个 item 的引用和快照
        self._items = list(items)
        self._scene = scene

    def undo(self):
        from infinite_canvas import ImageItem, CompareItem, VideoItem
        from marker_tool import MarkerItem

        removed = []
        for item in self._items:
            if item.scene() == self._scene:
                # 先移除子 MarkerItem
                for child in list(item.childItems()):
                    if isinstance(child, MarkerItem):
                        child.setParentItem(None)
                        if child.scene():
                            child.scene().removeItem(child)
                self._scene.removeItem(item)
                removed.append(item)

        if removed:
            # 清空选择
            self._scene.clearSelection()
            print(f"[撤销-添加] 移除了 {len(removed)} 个 item")


# ══════════════════════════════════════════════════
#  DeleteItemsCommand — 删除选中项
# ══════════════════════════════════════════════════
class DeleteItemsCommand(Command):
    """撤销删除：把被删的 item 重新加回场景，恢复到原位置"""

    def __init__(self, snapshots, scene, description="删除图片"):
        super().__init__(description)
        self._snapshots = snapshots  # 已拍好的快照列表
        self._scene = scene
        self._restored_items = []    # 恢复后的 item 引用

    def undo(self):
        from infinite_canvas import ImageItem, CompareItem, VideoItem
        from marker_tool import MarkerItem

        for snap in self._snapshots:
            item = None
            item_type = snap['type']

            if item_type == 'ImageItem':
                path = snap['path']
                if path:
                    pixmap = QPixmap(path)
                    if not pixmap.isNull():
                        if pixmap.width() > 1024:
                            pixmap = pixmap.scaledToWidth(1024, Qt.SmoothTransformation)
                        item = ImageItem(pixmap, path)
                        item.has_markers = snap.get('has_markers', False)
                        item.merge_order = snap.get('merge_order', -1)

            elif item_type == 'CompareItem':
                path_orig = snap.get('path_orig')
                path_gen = snap.get('path')
                import os
                orig_px = QPixmap(path_orig) if path_orig and os.path.isfile(path_orig) else None
                gen_px = QPixmap(path_gen) if path_gen and os.path.isfile(path_gen) else None
                if orig_px and gen_px and not orig_px.isNull() and not gen_px.isNull():
                    if orig_px.width() > 1024:
                        orig_px = orig_px.scaledToWidth(1024, Qt.SmoothTransformation)
                    gen_px = gen_px.scaled(orig_px.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    item = CompareItem(orig_px, gen_px, path_orig, path_gen)
                    item.merge_order = snap.get('merge_order', -1)
                    item._divider = snap.get('divider', item._w / 2)

            elif item_type == 'VideoItem':
                from infinite_canvas import _extract_video_thumbnail
                path = snap['path']
                if path:
                    pixmap, duration = _extract_video_thumbnail(path)
                    if not pixmap.isNull():
                        if pixmap.width() > 512:
                            pixmap = pixmap.scaledToWidth(512, Qt.SmoothTransformation)
                        item = VideoItem(pixmap, path, snap.get('duration_sec', 0))

            if item:
                item.setPos(snap['pos'])
                self._scene.addItem(item)
                if snap.get('selected', False):
                    item.setSelected(True)
                self._restored_items.append(item)

                # 恢复子 MarkerItem
                markers_data = snap.get('markers', [])
                for m_data in markers_data:
                    marker = MarkerItem(
                        m_data['index'],
                        m_data['label'],
                        m_data['color_name'],
                        parent_image_item=item
                    )
                    marker.setPos(m_data['local_pos'])

        print(f"[撤销-删除] 恢复了 {len(self._restored_items)} 个 item")


# ══════════════════════════════════════════════════
#  MoveItemsCommand — 拖动图片位置
# ══════════════════════════════════════════════════
class MoveItemsCommand(Command):
    """撤销移动：将 item 恢复到移动前的位置"""

    def __init__(self, move_data, description="移动图片"):
        """
        move_data: list of (item, old_pos, new_pos)
        """
        super().__init__(description)
        self._move_data = move_data

    def undo(self):
        for item, old_pos, new_pos in self._move_data:
            if item.scene():
                item.setPos(old_pos)
                item.update()
        print(f"[撤销-移动] 恢复了 {len(self._move_data)} 个 item 的位置")


# ══════════════════════════════════════════════════
#  AddMarkerCommand — 添加标记点
# ══════════════════════════════════════════════════
class AddMarkerCommand(Command):
    """撤销添加标记：从场景中移除该标记点"""

    def __init__(self, marker, parent_item, marker_toolbar, description="添加标记"):
        super().__init__(description)
        self._marker = marker
        self._parent_item = parent_item
        self._marker_toolbar = marker_toolbar

    def undo(self):
        from marker_tool import MarkerItem
        if self._marker.scene():
            self._marker.setParentItem(None)
            if self._marker.scene():
                self._marker.scene().removeItem(self._marker)
        # 从工具栏列表中移除
        if self._marker in self._marker_toolbar._markers:
            self._marker_toolbar._markers.remove(self._marker)
        if self._marker in self._marker_toolbar._undo_stack:
            self._marker_toolbar._undo_stack.remove(self._marker)
        self._marker_toolbar._update_display()
        print(f"[撤销-添加标记] 移除了标记 {self._marker.label}")


# ══════════════════════════════════════════════════
#  DeleteMarkersCommand — 删除标记点
# ══════════════════════════════════════════════════
class DeleteMarkersCommand(Command):
    """撤销删除标记：将标记点重新添加回图片上"""

    def __init__(self, marker_snapshots, parent_item, marker_toolbar, description="删除标记"):
        """
        marker_snapshots: list of dict with keys: index, label, color_name, local_pos
        """
        super().__init__(description)
        self._snapshots = marker_snapshots
        self._parent_item = parent_item
        self._marker_toolbar = marker_toolbar

    def undo(self):
        from marker_tool import MarkerItem
        for snap in self._snapshots:
            marker = MarkerItem(
                snap['index'],
                snap['label'],
                snap['color_name'],
                parent_image_item=self._parent_item
            )
            marker.setPos(snap['local_pos'])
            self._marker_toolbar._markers.append(marker)
            # 不压入 _undo_stack，避免和工具栏自己的撤销冲突

        self._marker_toolbar._update_display()
        print(f"[撤销-删除标记] 恢复了 {len(self._snapshots)} 个标记")


# ══════════════════════════════════════════════════
#  BakeMarkersCommand — 退出标记模式时渲染标记到图片
# ══════════════════════════════════════════════════
class BakeMarkersCommand(Command):
    """撤销标记渲染：将图片恢复为渲染前的原始状态，重新添加标记点"""

    def __init__(self, bake_data, description="渲染标记到图片"):
        """
        bake_data: list of dict per item, keys:
            item: ImageItem/CompareItem 引用
            orig_path: 原始图片路径（渲染前）
            orig_pixmap: 原始 QPixmap（渲染前的 pixmap 副本）
            markers: list of _snapshot_marker 结果
            orig_pos: QPointF
        """
        super().__init__(description)
        self._bake_data = bake_data

    def undo(self):
        from marker_tool import MarkerItem

        for data in self._bake_data:
            item = data['item']
            if not item.scene():
                continue

            # 恢复原始 pixmap
            orig_pixmap = data['orig_pixmap']
            if orig_pixmap and not orig_pixmap.isNull():
                item.setPixmap(orig_pixmap)

            # 恢复原始路径
            item.path = data['orig_path']
            item.has_markers = False

            # 恢复位置
            item.setPos(data['orig_pos'])

            # 重新添加标记点子 item
            for m_snap in data['markers']:
                marker = MarkerItem(
                    m_snap['index'],
                    m_snap['label'],
                    m_snap['color_name'],
                    parent_image_item=item
                )
                marker.setPos(m_snap['local_pos'])

        print(f"[撤销-渲染标记] 恢复了 {len(self._bake_data)} 个图片到渲染前状态")


# ══════════════════════════════════════════════════
#  ReplaceGenCommand — 生成完成替换占位框
# ══════════════════════════════════════════════════
class ReplaceGenCommand(Command):
    """撤销生成替换：移除生成结果，恢复占位框或原始图片"""

    def __init__(self, result_item, placeholder_snapshot, gen_path, orig_path,
                 scene, description="生成图片"):
        super().__init__(description)
        self._result_item = result_item
        self._placeholder_snap = placeholder_snapshot
        self._gen_path = gen_path
        self._orig_path = orig_path
        self._scene = scene

    def undo(self):
        from infinite_canvas import GeneratingItem, ImageItem, CompareItem

        # 移除生成结果 item
        result = self._result_item
        if result and result.scene() == self._scene:
            # 如果是 CompareItem 或 ImageItem，先移除子 MarkerItem
            from marker_tool import MarkerItem
            for child in list(result.childItems()):
                if isinstance(child, MarkerItem):
                    child.setParentItem(None)
                    if child.scene():
                        child.scene().removeItem(child)
            self._scene.removeItem(result)

        # 恢复占位框
        snap = self._placeholder_snap
        if snap:
            ph = GeneratingItem(snap['w'], snap['h'], task_id=snap.get('task_id', ''))
            ph.setPos(snap['pos'])
            self._scene.addItem(ph)

        print(f"[撤销-生成] 移除了生成结果，恢复占位框")
