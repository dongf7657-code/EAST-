import sys
import os
import traceback
from PySide6.QtWidgets import QApplication
from main_window import MainWindow

# ── 渲染后端设置（必须在 QApplication 创建之前）──
# Windows 上 QVideoWidget 需要 D3D11 或 OpenGL 后端才能正确渲染视频帧
# 设置为 d3d11 兼容性最好；如果仍有问题可改为 "opengl" 或删除此行让 Qt 自动选择
if sys.platform == "win32":
    os.environ.setdefault("QSG_RHI_BACKEND", "d3d11")


class _TeeWriter:
    """同时写文件和原始终端的 Writer，防止 windowed 模式下 print() 崩溃"""
    def __init__(self, *writers):
        self._writers = [w for w in writers if w is not None]

    def write(self, msg):
        for w in self._writers:
            try:
                w.write(msg)
            except Exception:
                pass

    def flush(self):
        for w in self._writers:
            try:
                w.flush()
            except Exception:
                pass


if __name__ == "__main__":
    # 切换工作目录到脚本/EXE 所在目录
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)

    # ── 日志重定向 ──────────────────────────────
    # windowed 打包下 stdout/stderr 可能为 None 或无效，print() 会崩溃
    # 策略：先保存原始流，再打开日志文件，用 TeeWriter 同时写两边
    _orig_stdout = sys.stdout
    _orig_stderr = sys.stderr

    _log_path = os.path.join(base_dir, "app.log")
    try:
        _log_file = open(_log_path, "a", encoding="utf-8")
        sys.stdout = _TeeWriter(_orig_stdout, _log_file)
        sys.stderr = _TeeWriter(_orig_stderr, _log_file)
    except Exception:
        # 日志文件无法创建（只读目录等），用安全空写器兜底
        class _NullWriter:
            def write(self, msg): pass
            def flush(self): pass
        if sys.stdout is None:
            sys.stdout = _NullWriter()
        if sys.stderr is None:
            sys.stderr = _NullWriter()

    # 全局异常钩子：捕获未处理的异常写入日志，防止静默崩溃
    def _global_excepthook(exc_type, exc_val, exc_tb):
        try:
            sys.stderr.write("".join(traceback.format_exception(exc_type, exc_val, exc_tb)))
            sys.stderr.write("\n")
            sys.stderr.flush()
        except Exception:
            pass

    sys.excepthook = _global_excepthook

    print(f"[启动] EastAIstudio 工作目录: {base_dir}")
    print(f"[启动] Python: {sys.version}")
    print(f"[启动] frozen: {getattr(sys, 'frozen', False)}")

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # ── 全局应用名称 + 图标（所有窗口自动继承，含任务栏、预览弹窗）──
    app.setApplicationName("EastAIstudio")
    app.setApplicationDisplayName("EastAIstudio")
    app.setOrganizationName("EastAI")

    _icon_path = ""
    if getattr(sys, 'frozen', False):
        _base = os.path.dirname(sys.executable)
        _icon_path = os.path.join(_base, "icon.ico")
        if not os.path.isfile(_icon_path):
            _icon_path = os.path.join(_base, "_internal", "icon.ico")
    if not _icon_path or not os.path.isfile(_icon_path):
        _icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
    if os.path.isfile(_icon_path):
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(_icon_path))

    # ── 全局浅色极简调色板 ──
    from PySide6.QtGui import QPalette, QColor
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(245, 245, 248))
    palette.setColor(QPalette.WindowText, QColor(51, 51, 51))
    palette.setColor(QPalette.Base, QColor(255, 255, 255))
    palette.setColor(QPalette.AlternateBase, QColor(245, 245, 248))
    palette.setColor(QPalette.ToolTipBase, QColor(255, 255, 255))
    palette.setColor(QPalette.ToolTipText, QColor(51, 51, 51))
    palette.setColor(QPalette.Text, QColor(51, 51, 51))
    palette.setColor(QPalette.Button, QColor(245, 245, 248))
    palette.setColor(QPalette.ButtonText, QColor(51, 51, 51))
    palette.setColor(QPalette.BrightText, QColor(0, 0, 0))
    palette.setColor(QPalette.Highlight, QColor(0, 0, 0, 25))
    palette.setColor(QPalette.HighlightedText, QColor(0, 0, 0))
    app.setPalette(palette)

    # ── 全局 QToolTip 极简样式 ──
    app.setStyleSheet("""
        QToolTip {
            background: rgba(255, 255, 255, 0.92);
            color: #333;
            border: 0.5px solid rgba(0, 0, 0, 0.10);
            border-radius: 4px;
            padding: 5px 10px;
            font-size: 12px;
            font-weight: 500;
        }
    """)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())
