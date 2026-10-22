"""Qt desktop controls. Bluetooth operations run off the GUI thread."""
import struct
import sys
from concurrent.futures import ThreadPoolExecutor

from PySide6 import QtCore, QtWidgets

from .cli import devices
from .controller import Controller
from .settings import FIELDS, Settings
from .transport import normalize_mac


class DemoController:
    def __init__(self):
        self.settings = Settings(struct.pack("<26f", *([1] + [0] * 25)))

    def read(self):
        return self.settings

    def apply(self, changes=None, expected=None):
        self.settings = self.settings.edited(changes or {})
        return self.settings, None


class Window(QtWidgets.QWidget):
    def __init__(self, mac=None, demo=False, transport="auto"):
        super().__init__()
        self.setWindowTitle("AirPods · 通透调节" + (" · 演示" if demo else ""))
        self.resize(940, 680)
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.pending = None
        self.loaded = None
        self.controller = DemoController() if demo else None
        self.demo = demo
        self.transport = transport
        self.controls = {}
        self.sliders = {}
        self.setStyleSheet("""
            QWidget { font-size: 14px; }
            QGroupBox { font-weight: 600; margin-top: 12px; padding-top: 16px; }
            QPushButton { padding: 7px 16px; }
            QDoubleSpinBox { min-width: 90px; padding: 4px; }
        """)
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("调低环境声，保留你需要的声音")
        title.setStyleSheet("font-size: 23px; font-weight: 600; margin: 8px 0;")
        layout.addWidget(title)
        subtitle = QtWidgets.QLabel("读取耳机当前设置后调整。点击应用时备份原值，并读回确认。")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        row = QtWidgets.QHBoxLayout()
        self.address = QtWidgets.QLineEdit(mac or "")
        self.address.setPlaceholderText("耳机蓝牙地址 AA:BB:CC:DD:EE:FF")
        row.addWidget(self.address)
        self.find_button = QtWidgets.QPushButton("查找耳机")
        self.find_button.clicked.connect(self.find)
        row.addWidget(self.find_button)
        self.load_button = QtWidgets.QPushButton("读取设置")
        self.load_button.clicked.connect(self.load)
        row.addWidget(self.load_button)
        layout.addLayout(row)
        self.enabled = QtWidgets.QCheckBox("启用自定义通透模式")
        layout.addWidget(self.enabled)
        self.group = QtWidgets.QGroupBox("环境声音")
        grid = QtWidgets.QGridLayout(self.group)
        for column, label in enumerate(("调节项", "左耳", "右耳")):
            grid.addWidget(QtWidgets.QLabel(label), 0, column)
        labels = [("amplification", "增音 · −1 至 1"), ("tone", "音调 · −1 至 1"),
                  ("noise_reduction", "降低环境噪声 · 0 至 1"),
                  ("conversation_boost", "对话增强")]
        for row_index, (suffix, label) in enumerate(labels, 1):
            grid.addWidget(QtWidgets.QLabel(label), row_index, 0)
            for column, ear in enumerate(("left", "right"), 1):
                name = f"{ear}_{suffix}"
                if suffix == "conversation_boost":
                    widget = QtWidgets.QCheckBox("开启")
                else:
                    widget = QtWidgets.QDoubleSpinBox()
                    _, lo, hi, _ = FIELDS[name]
                    widget.setRange(lo, hi)
                    widget.setSingleStep(0.05)
                    widget.setDecimals(3)
                panel = widget if suffix == "conversation_boost" else self.with_slider(name, widget)
                grid.addWidget(panel, row_index, column)
                self.controls[name] = widget
        own = QtWidgets.QDoubleSpinBox()
        own.setRange(0, 1)
        own.setSingleStep(0.05)
        own.setDecimals(3)
        self.controls["own_voice"] = own
        grid.addWidget(QtWidgets.QLabel("自己声音增音 · 0 至 1"), 5, 0)
        grid.addWidget(self.with_slider("own_voice", own), 5, 1)
        layout.addWidget(self.group)
        note = QtWidgets.QLabel("数值为协议归一化值，并非分贝。EQ 和未编辑参数保持原值。\n"
                               "自定义通透与当前聆听模式分别设置；使用时需将耳机切换到通透模式。")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QtWidgets.QHBoxLayout()
        self.apply_button = QtWidgets.QPushButton("备份并应用" if not demo else "应用到演示设备")
        self.apply_button.clicked.connect(self.apply)
        self.restore_button = QtWidgets.QPushButton("从备份恢复…")
        self.restore_button.clicked.connect(self.restore)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.restore_button)
        layout.addLayout(buttons)
        self.status = QtWidgets.QLabel("演示模式：不会访问或修改真实耳机。" if demo else
                                        "尚未读取耳机。设备需已配对并连接。")
        self.status.setWordWrap(True)
        self.status.setTextFormat(QtCore.Qt.PlainText)
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.status)
        layout.addStretch()
        self.set_loaded(False)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(50)
        if demo:
            self.address.setText("00:00:00:00:00:00")
            self.address.setEnabled(False)
            self.find_button.setEnabled(False)
            self.load()
        elif mac:
            QtCore.QTimer.singleShot(0, self.load)

    def with_slider(self, name, spin):
        panel = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(panel)
        row.setContentsMargins(0, 0, 0, 0)
        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setRange(round(spin.minimum() * 1000), round(spin.maximum() * 1000))
        slider.setSingleStep(10)
        slider.setPageStep(100)
        slider.setMinimumWidth(100)
        slider.setAccessibleName(name.replace("_", " "))
        slider.setToolTip("拖动调节；点击“备份并应用”后写入耳机。")
        slider.setValue(round(spin.value() * 1000))
        slider.valueChanged.connect(lambda value: spin.setValue(value / 1000))
        spin.valueChanged.connect(lambda value: slider.setValue(round(value * 1000)))
        spin.setMaximumWidth(100)
        row.addWidget(slider, 1)
        row.addWidget(spin)
        self.sliders[name] = slider
        return panel

    def set_loaded(self, state):
        self.group.setEnabled(state)
        self.enabled.setEnabled(state)
        self.apply_button.setEnabled(state)
        self.restore_button.setEnabled(state and not self.demo)

    def run(self, work, callback, message):
        if self.pending:
            return
        self.set_loaded(False)
        self.find_button.setEnabled(False)
        self.load_button.setEnabled(False)
        self.address.setEnabled(False)
        self.status.setText(message)
        self.pending = (self.pool.submit(work), callback)

    def poll(self):
        if not self.pending or not self.pending[0].done():
            return
        future, callback = self.pending
        self.pending = None
        self.find_button.setEnabled(not self.demo)
        self.load_button.setEnabled(True)
        self.address.setEnabled(not self.demo)
        try:
            callback(future.result())
        except Exception as exc:
            self.loaded = None
            self.status.setText(f"操作失败：{exc}\n请检查连接与 README 中的 DeviceID 配置，然后重新读取。")
        self.set_loaded(self.loaded is not None)
        if self.loaded:
            available = "own_voice" in self.loaded.values()
            self.controls["own_voice"].setEnabled(available)
            self.sliders["own_voice"].setEnabled(available)

    def find(self):
        def found(result):
            if not result:
                self.status.setText("BlueZ 中没有已知 AirPods。请先在系统蓝牙设置中扫描并配对。")
                return
            options = [f"{name}  ·  {mac}" for mac, name in result]
            choice, ok = QtWidgets.QInputDialog.getItem(self, "选择耳机", "设备", options, 0, False)
            if ok:
                self.address.setText(result[options.index(choice)][0])
                self.loaded = None
                self.status.setText("已选择耳机，请读取设置。")
        self.run(devices, found, "读取 BlueZ 设备列表…")

    def load(self):
        try:
            mac = normalize_mac(self.address.text().strip())
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        if not self.demo:
            self.controller = Controller(mac, transport=self.transport)
        self.loaded = None
        self.run(self.controller.read, self.populate, "读取通透参数…")

    def populate(self, settings):
        self.loaded = settings
        values = settings.values()
        self.enabled.setChecked(values["enabled"])
        for name, widget in self.controls.items():
            if name not in values:
                widget.setEnabled(False)
            elif isinstance(widget, QtWidgets.QCheckBox):
                widget.setChecked(values[name])
            else:
                widget.setValue(values[name])
        self.status.setText("演示模式 · 已读取模拟参数。" if self.demo else
                            f"已通过 {self.controller.backend} 读取。调整后点击“备份并应用”。")

    def edited_values(self):
        # Only genuinely edited controls become patches: rounded display values
        # must not overwrite untouched binary float values.
        changes = {}
        values = self.loaded.values()
        if self.enabled.isChecked() != values["enabled"]:
            changes["enabled"] = self.enabled.isChecked()
        for name, widget in self.controls.items():
            if name not in values:
                continue
            value = widget.isChecked() if isinstance(widget, QtWidgets.QCheckBox) else widget.value()
            shown_original = values[name] if isinstance(value, bool) else round(values[name], 3)
            if value != shown_original:
                changes[name] = value
        return changes

    def check_device(self):
        if not self.demo and normalize_mac(self.address.text().strip()) != self.controller.mac:
            raise ValueError("蓝牙地址已更改，请先读取新设备。")

    def apply(self):
        try:
            self.check_device()
            changes = self.edited_values()
            expected = self.loaded
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        if not changes:
            self.status.setText("参数未改变，无需写入。")
            return
        self.run(lambda: self.controller.apply(changes, expected=expected), self.applied, "备份原值并应用…")

    def applied(self, result):
        settings, backup = result
        self.populate(settings)
        self.status.setText("已应用到演示设备。" if self.demo else
                            f"已写入并读回验证。\n原值备份：{backup}" if backup else "参数未改变，无需写入。")

    def restore(self):
        try:
            self.check_device()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择原值备份", "", "JSON (*.json)")
        if filename:
            expected = self.loaded
            self.run(lambda: self.controller.apply(restore=filename, expected=expected),
                     self.applied, "备份当前值并恢复所选备份…")

    def closeEvent(self, event):
        self.timer.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)
        event.accept()


def launch(mac=None, demo=False, transport="auto"):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    window = Window(mac, demo, transport)
    window.show()
    return app.exec()
