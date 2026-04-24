import sys
import os
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QMessageBox, QComboBox, QHBoxLayout
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtCore import Qt
from config_manager import ConfigManager


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setFixedSize(380, 200)
        self.config = ConfigManager()

        # ── 弹窗 logo ──
        _icon_path = self._resolve_icon_path("icon.ico")
        if _icon_path:
            self.setWindowIcon(QIcon(_icon_path))

        # ── 极简浅色风格 ──
        self.setStyleSheet("""
            QDialog {
                background: #fafafa;
            }
            QLabel {
                color: #555;
                font-size: 12px;
                font-weight: 500;
            }
            QLineEdit {
                padding: 7px 10px;
                border: none;
                border-radius: 6px;
                background: rgba(0, 0, 0, 0.04);
                color: #333;
                font-size: 12px;
                selection-background-color: rgba(0, 0, 0, 0.08);
            }
            QLineEdit:focus {
                background: rgba(0, 0, 0, 0.06);
            }
            QComboBox {
                padding: 6px 10px;
                border: none;
                border-radius: 6px;
                background: rgba(0, 0, 0, 0.04);
                color: #333;
                font-size: 12px;
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
            }
            QPushButton {
                background: rgba(0, 0, 0, 0.05);
                color: #333;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 12px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: rgba(0, 0, 0, 0.09);
            }
            QPushButton:pressed {
                background: rgba(0, 0, 0, 0.03);
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(QLabel("API 线路"))
        self.provider_combo = QComboBox()
        self.provider_combo.addItem("Kie.ai (api.kie.ai)", "kie")
        self.provider_combo.addItem("Grsai (grsai.dakka.com.cn)", "grsai")

        current_provider = self.config.get("api_provider", "kie")
        index = self.provider_combo.findData(current_provider)
        if index >= 0:
            self.provider_combo.setCurrentIndex(index)
        layout.addWidget(self.provider_combo)

        layout.addWidget(QLabel("API Key"))
        self.api_key_input = QLineEdit()
        self.api_key_input.setText(self.config.get("api_key", ""))
        self.api_key_input.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.api_key_input)

        self.save_btn = QPushButton("保存")
        self.save_btn.clicked.connect(self.save_settings)
        layout.addWidget(self.save_btn)

    def save_settings(self):
        api_key = self.api_key_input.text().strip()
        provider = self.provider_combo.currentData()
        self.config.set("api_key", api_key)
        self.config.set("api_provider", provider)
        QMessageBox.information(self, "成功", "设置已保存")
        self.accept()

    @staticmethod
    def _resolve_icon_path(filename: str) -> str:
        base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
        full = os.path.join(base, filename)
        if os.path.isfile(full):
            return full
        exe_dir = os.path.dirname(sys.executable)
        full2 = os.path.join(exe_dir, filename)
        if os.path.isfile(full2):
            return full2
        return ""
