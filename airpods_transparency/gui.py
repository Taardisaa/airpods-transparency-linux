"""Qt desktop controls. Bluetooth operations run off the GUI thread."""
import struct
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from PySide6 import QtCore, QtGui, QtWidgets

from .cli import devices
from .service import RemoteController as Controller, rpc
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
        self.setWindowTitle("AirPods 控制中心" + (" · 演示" if demo else ""))
        self.resize(940, 680)
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.pending = None
        self.pending_error_label = None
        self.loaded = None
        self.controller = DemoController() if demo else None
        self.demo = demo
        self.transport = transport
        self.controls = {}
        self.sliders = {}
        self._populating = False
        self._syncing = False
        self.device_state = {}
        self.status_future = None
        self.profile_future = None
        self.demo_profiles = {}
        self._quitting = False
        self.tray = None
        self.setStyleSheet("""
            QWidget { font-size: 14px; }
            QGroupBox { font-weight: 600; margin-top: 12px; padding-top: 16px; }
            QPushButton { padding: 7px 16px; }
            QDoubleSpinBox { min-width: 90px; padding: 4px; }
        """)
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("AirPods 控制中心")
        title.setStyleSheet("font-size: 23px; font-weight: 600; margin: 8px 0;")
        layout.addWidget(title)
        subtitle = QtWidgets.QLabel("电量与聆听模式 · 通透声音调节 · 配置预设")
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
        self.reconnect_button = QtWidgets.QPushButton("重新连接")
        self.reconnect_button.setToolTip("重建音频和控制连接，声音会短暂中断。保留配对和已保存的声音参数。")
        self.reconnect_button.clicked.connect(self.reconnect)
        self.reconnect_button.setEnabled(not demo)
        row.addWidget(self.reconnect_button)
        layout.addLayout(row)
        self.tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.tabs)
        self.build_overview()
        sound = QtWidgets.QWidget()
        sound_layout = QtWidgets.QVBoxLayout(sound)
        self.tabs.addTab(sound, "声音调节")
        self.enabled = QtWidgets.QCheckBox("启用自定义通透模式")
        sound_layout.addWidget(self.enabled)
        self.link_ears = QtWidgets.QCheckBox("同步左右耳 · 调整任一侧时同时调整另一侧")
        self.link_ears.setChecked(True)
        self.link_ears.setToolTip("同步增音、音调、环境降噪和对话增强。读取时保留耳机原值，编辑时才同步。")
        sound_layout.addWidget(self.link_ears)
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
        for name, widget in self.controls.items():
            if name.startswith(("left_", "right_")):
                signal = widget.toggled if isinstance(widget, QtWidgets.QCheckBox) else widget.valueChanged
                signal.connect(lambda value, field=name: self.sync_ears(field, value))
        sound_layout.addWidget(self.group)
        note = QtWidgets.QLabel("数值为协议归一化值，并非分贝。EQ 和未编辑参数保持原值。\n"
                               "自定义通透与当前聆听模式分别设置；使用时需将耳机切换到通透模式。")
        note.setWordWrap(True)
        sound_layout.addWidget(note)
        buttons = QtWidgets.QHBoxLayout()
        self.apply_button = QtWidgets.QPushButton("备份并应用" if not demo else "应用到演示设备")
        self.apply_button.clicked.connect(self.apply)
        self.restore_button = QtWidgets.QPushButton("从备份恢复…")
        self.restore_button.clicked.connect(self.restore)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.restore_button)
        sound_layout.addLayout(buttons)
        sound_layout.addStretch()
        self.build_presets()
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
        self.overview_timer = QtCore.QTimer(self)
        self.overview_timer.timeout.connect(self.refresh_overview)
        self.overview_timer.start(3000)
        self.build_tray()
        self.refresh_profiles()
        if demo:
            self.address.setText("00:00:00:00:00:00")
            self.address.setEnabled(False)
            self.find_button.setEnabled(False)
            self.load()
        elif mac:
            QtCore.QTimer.singleShot(0, self.load)

    def build_overview(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        self.connection_label = QtWidgets.QLabel("等待连接耳机…")
        self.connection_label.setStyleSheet("font-size: 18px; font-weight: 600; padding: 8px 0;")
        layout.addWidget(self.connection_label)
        self.device_label = QtWidgets.QLabel("型号与固件将在连接后显示。")
        self.device_label.setWordWrap(True)
        layout.addWidget(self.device_label)
        self.battery_bars = {}
        for part, title in (("left", "左耳"), ("right", "右耳"), ("case", "充电盒")):
            box = QtWidgets.QGroupBox(title)
            row = QtWidgets.QVBoxLayout(box)
            bar = QtWidgets.QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setFormat("未报告")
            row.addWidget(bar)
            layout.addWidget(box)
            self.battery_bars[part] = bar
        modes = QtWidgets.QHBoxLayout()
        modes.addWidget(QtWidgets.QLabel("聆听模式"))
        self.mode_combo = QtWidgets.QComboBox()
        for title, key in (("降噪", "anc"), ("通透", "transparency"), ("自适应", "adaptive"), ("关闭", "off")):
            self.mode_combo.addItem(title, key)
        modes.addWidget(self.mode_combo, 1)
        self.mode_button = QtWidgets.QPushButton("切换模式")
        self.mode_button.clicked.connect(lambda: self.change_mode(self.mode_combo.currentData()))
        modes.addWidget(self.mode_button)
        layout.addLayout(modes)
        self.mode_label = QtWidgets.QLabel("当前模式：未知")
        layout.addWidget(self.mode_label)
        self.overview_error = QtWidgets.QLabel("")
        self.overview_error.setWordWrap(True)
        self.overview_error.setTextFormat(QtCore.Qt.PlainText)
        layout.addWidget(self.overview_error)
        layout.addStretch()
        self.tabs.addTab(page, "总览")

    def build_presets(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        explanation = QtWidgets.QLabel("保存耳机当前已应用的声音参数，之后一键恢复。保存前请先应用正在编辑的参数。")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.profile_combo = QtWidgets.QComboBox()
        layout.addWidget(self.profile_combo)
        self.include_mode = QtWidgets.QCheckBox("保存时包含当前聆听模式")
        self.include_mode.setChecked(True)
        layout.addWidget(self.include_mode)
        self.save_profile_button = QtWidgets.QPushButton("保存当前配置为预设…")
        self.save_profile_button.clicked.connect(self.save_profile)
        layout.addWidget(self.save_profile_button)
        self.apply_profile_button = QtWidgets.QPushButton("应用所选预设")
        self.apply_profile_button.clicked.connect(lambda: self.apply_profile(self.profile_combo.currentText()))
        layout.addWidget(self.apply_profile_button)
        self.delete_profile_button = QtWidgets.QPushButton("删除所选预设")
        self.delete_profile_button.clicked.connect(self.delete_profile)
        layout.addWidget(self.delete_profile_button)
        layout.addStretch()
        self.tabs.addTab(page, "配置预设")

    def build_tray(self):
        if self.demo or not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QtWidgets.QSystemTrayIcon(QtGui.QIcon(str(Path(__file__).parent / "assets/airpods-control.svg")), self)
        menu = QtWidgets.QMenu(self)
        menu.addAction("打开控制中心", self.show_window)
        self.tray_status = menu.addAction("等待耳机状态…")
        self.tray_status.setEnabled(False)
        self.tray_mode_menu = menu.addMenu("聆听模式")
        self.tray_modes = {}
        group = QtGui.QActionGroup(self.tray_mode_menu)
        group.setExclusive(True)
        for index in range(self.mode_combo.count()):
            mode = self.mode_combo.itemData(index)
            action = self.tray_mode_menu.addAction(self.mode_combo.itemText(index))
            action.setCheckable(True)
            group.addAction(action)
            action.triggered.connect(lambda checked=False, value=mode: self.change_mode(value))
            self.tray_modes[mode] = action
        self.tray_profiles = menu.addMenu("配置预设")
        menu.addSeparator()
        menu.addAction("退出控制中心", self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_window()
            if reason == QtWidgets.QSystemTrayIcon.Trigger else None)
        self.tray.setToolTip("AirPods 控制中心")
        self.tray.show()

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        self._quitting = True
        if self.tray:
            self.tray.hide()
        self.close()
        QtWidgets.QApplication.instance().quit()

    def demo_status(self):
        return {"connected": True, "name": "AirPods Pro · 演示", "model": "AirPods Pro 3",
                "firmware": "模拟数据", "mode": self.device_state.get("mode", "transparency"),
                "battery": {p: {"percent": v, "charging": False, "available": True}
                            for p, v in (("left", 86), ("right", 88), ("case", 42))}}

    def refresh_overview(self):
        if self.status_future or self.pending:
            return
        try:
            mac = normalize_mac(self.address.text().strip())
        except ValueError:
            return
        self.status_request_mac = mac
        self.status_future = self.pool.submit(self.demo_status if self.demo else lambda: rpc("status", mac))

    def poll_overview(self):
        if self.status_future and self.status_future.done():
            future, self.status_future = self.status_future, None
            if self.address.text().strip().upper() != self.status_request_mac:
                return
            try:
                self.update_overview(future.result())
            except Exception as exc:
                self.update_overview({"connected": False, "error": str(exc), "battery": {}})
        if self.profile_future and self.profile_future.done():
            future, self.profile_future = self.profile_future, None
            try:
                profiles = future.result()["presets"]
                selected = self.profile_combo.currentText()
                self.profile_combo.clear()
                self.profile_combo.addItems(sorted(profiles))
                index = self.profile_combo.findText(selected)
                if index >= 0:
                    self.profile_combo.setCurrentIndex(index)
                if self.tray:
                    self.tray_profiles.clear()
                    for name in sorted(profiles):
                        self.tray_profiles.addAction(name, lambda checked=False, n=name: self.apply_profile(n))
                    if not profiles:
                        self.tray_profiles.addAction("尚无预设").setEnabled(False)
            except Exception as exc:
                self.status.setText(f"读取预设失败：{exc}")
            self.update_actions()

    def update_overview(self, state):
        self.device_state = state
        connected = state.get("connected", False)
        if connected:
            connection = " · 已连接"
        elif state.get("bluetooth_connected") is True:
            connection = " · 蓝牙已连接，控制通道不可用"
        elif state.get("bluetooth_connected") is False:
            connection = " · 未连接"
        else:
            connection = " · 连接状态未知"
        self.connection_label.setText((state.get("name") or "AirPods") + connection)
        self.device_label.setText(f"型号：{state.get('model') or '未知'}    固件：{state.get('firmware') or '未知'}")
        parts = []
        for part, bar in self.battery_bars.items():
            battery = state.get("battery", {}).get(part, {})
            level = battery.get("percent") if connected else None
            bar.setValue(level if level is not None else 0)
            bar.setFormat((f"{level}%" + (" · 充电中" if battery.get("charging") else "")) if level is not None else "未报告")
            parts.append(bar.format())
        mode = state.get("mode") if connected else None
        index = self.mode_combo.findData(mode)
        title = self.mode_combo.itemText(index) if index >= 0 else "未知"
        self.mode_label.setText(f"当前模式：{title}")
        # Do not reset a user's pending mode selection during periodic refresh.
        if not self.mode_combo.hasFocus() and index >= 0 and not getattr(self, "mode_initialized", False):
            self.mode_combo.setCurrentIndex(index)
            self.mode_initialized = True
        self.overview_error.setText(state.get("error") or "")
        if self.tray:
            summary = "左 " + parts[0] + " · 右 " + parts[1] + " · 盒 " + parts[2]
            self.tray_status.setText(summary)
            self.tray.setToolTip(f"AirPods · {title}\n{summary}" if connected else "AirPods · 未连接")
            for key, action in self.tray_modes.items():
                action.setChecked(key == mode)
        self.update_actions()

    def update_actions(self):
        available = bool(self.device_state.get("connected")) and not self.pending
        self.mode_button.setEnabled(available)
        self.save_profile_button.setEnabled(self.loaded is not None and not self.pending)
        has_profiles = self.profile_combo.count() > 0
        self.apply_profile_button.setEnabled(self.loaded is not None and not self.pending and has_profiles)
        self.delete_profile_button.setEnabled(not self.pending and has_profiles)
        if self.tray:
            self.tray_mode_menu.setEnabled(available)
            self.tray_profiles.setEnabled(not self.pending and has_profiles and self.loaded is not None)

    def change_mode(self, mode):
        if self.pending:
            return
        try:
            mac = normalize_mac(self.address.text().strip())
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        def work():
            if self.demo:
                state = self.demo_status()
                state["mode"] = mode
                return state
            return rpc("mode", mac, mode=mode)
        def changed(state):
            self.update_overview(state)
            self.status.setText("聆听模式已确认。")
        self.run(work, changed, "切换聆听模式…")

    def refresh_profiles(self):
        if not self.profile_future:
            self.profile_future = self.pool.submit(lambda: {"presets": self.demo_profiles.copy()}
                                                   if self.demo else rpc("preset_list"))

    def save_profile(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "保存配置预设", "名称（保存耳机当前已应用的设置）")
        if not ok or not name.strip():
            return
        mac = self.controller.mac if not self.demo else None
        include_mode = self.include_mode.isChecked()
        def work():
            if self.demo:
                self.demo_profiles[name] = {"values": self.controller.read().values(),
                                           "mode": self.demo_status()["mode"] if include_mode else None}
                return {}
            return rpc("preset_save", mac, name=name, include_mode=include_mode, transport=self.transport)
        def saved(result):
            self.status.setText(f"已保存当前配置：{name}")
            self.refresh_profiles()
        self.run(work, saved, "保存耳机当前配置…")

    def apply_profile(self, name):
        if not name or self.pending:
            return
        def work():
            if self.demo:
                profile = self.demo_profiles[name]
                if profile.get("mode"):
                    self.device_state["mode"] = profile["mode"]
                return self.controller.apply(profile["values"])
            result = rpc("preset_apply", self.controller.mac, name=name, transport=self.transport)
            return Settings(bytes.fromhex(result["raw_hex"])), result["backup"]
        def applied(result):
            self.applied(result)
            self.refresh_overview()
        self.run(work, applied, f"备份当前值并应用预设：{name}…")

    def delete_profile(self):
        name = self.profile_combo.currentText()
        if not name:
            return
        def work():
            if self.demo:
                self.demo_profiles.pop(name, None)
            else:
                rpc("preset_delete", name=name)
        def deleted(result):
            self.refresh_profiles()
            self.status.setText(f"已删除预设：{name}")
        self.run(work, deleted, "删除预设…")

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
        self.link_ears.setEnabled(state)
        self.apply_button.setEnabled(state)
        self.restore_button.setEnabled(state and not self.demo)
        self.update_actions()

    def sync_ears(self, name, value):
        if self._populating or self._syncing or not self.link_ears.isChecked():
            return
        ear, suffix = name.split("_", 1)
        other = self.controls[("right_" if ear == "left" else "left_") + suffix]
        self._syncing = True
        try:
            if isinstance(other, QtWidgets.QCheckBox):
                other.setChecked(value)
            else:
                other.setValue(value)
        finally:
            self._syncing = False

    def run(self, work, callback, message, error_label=None):
        if self.pending:
            return
        self.set_loaded(False)
        self.find_button.setEnabled(False)
        self.load_button.setEnabled(False)
        self.reconnect_button.setEnabled(False)
        self.address.setEnabled(False)
        self.status.setText(message)
        self.pending = (self.pool.submit(work), callback)
        self.pending_error_label = error_label
        self.update_actions()

    def poll(self):
        self.poll_overview()
        if not self.pending or not self.pending[0].done():
            return
        future, callback = self.pending
        self.pending = None
        self.find_button.setEnabled(not self.demo)
        self.load_button.setEnabled(True)
        self.reconnect_button.setEnabled(not self.demo)
        self.address.setEnabled(not self.demo)
        try:
            callback(future.result())
        except Exception as exc:
            self.loaded = None
            self.status.setText(f"{self.pending_error_label or '操作失败'}：{exc}")
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
        self.refresh_overview()
        self.run(self.controller.read, self.populate, "读取通透参数…", "声音参数暂不可用（不代表蓝牙未连接）")

    def reconnect(self):
        try:
            mac = normalize_mac(self.address.text().strip())
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.loaded = None
        self.controller = Controller(mac, transport=self.transport)
        def work():
            rpc("reconnect", mac)
            return self.controller.read()
        self.run(work, self.populate, "重新建立音频和控制连接，声音会短暂中断…",
                 "重连或读取失败，请检查系统蓝牙连接后重试")

    def populate(self, settings):
        self.loaded = settings
        values = settings.values()
        self.enabled.setChecked(values["enabled"])
        self._populating = True
        try:
            for name, widget in self.controls.items():
                if name not in values:
                    widget.setEnabled(False)
                elif isinstance(widget, QtWidgets.QCheckBox):
                    widget.setChecked(values[name])
                else:
                    widget.setValue(values[name])
        finally:
            self._populating = False
        self.status.setText("演示模式 · 已读取模拟参数。" if self.demo else
                            f"已通过 {self.controller.backend} 读取。调整后点击“备份并应用”。")
        self.refresh_overview()

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
        if self.tray and not self._quitting:
            self.hide()
            event.ignore()
            return
        self.timer.stop()
        self.overview_timer.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)
        event.accept()


def launch(mac=None, demo=False, transport="auto", tray=False):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName("io.github.Taardisaa.AirPodsControl")
    app.setDesktopFileName("io.github.Taardisaa.AirPodsControl")
    app.setWindowIcon(QtGui.QIcon(str(Path(__file__).parent / "assets/airpods-control.svg")))
    window = Window(mac, demo, transport)
    if not (tray and window.tray):
        window.show()
    return app.exec()
