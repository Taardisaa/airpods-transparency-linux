"""Optional GUI smoke test; run with QT_QPA_PLATFORM=offscreen."""
import struct
import time

from PySide6 import QtWidgets

from airpods_transparency.gui import Window
from airpods_transparency.settings import Settings


def drain(app, window):
    deadline = time.monotonic() + 5
    while (window.pending or window.status_future or window.profile_future) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert window.pending is None
    assert window.status_future is None
    assert window.profile_future is None


def test_demo_load_apply_and_preserve_rounding():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(demo=True)
    window.show()
    drain(app, window)
    assert window.loaded is not None
    assert window.link_ears.isChecked()
    assert window.edited_values() == {}
    buf = bytearray(window.loaded.raw)
    struct.pack_into("<f", buf, 40, 0.123456)
    window.controller.settings = Settings(bytes(buf))
    window.populate(window.controller.read())
    assert window.edited_values() == {}
    assert window.sliders["left_tone"].value() == 123
    assert window.sliders["right_tone"].value() == 0  # Loading must preserve asymmetry.
    window.sliders["left_amplification"].setValue(-300)
    assert window.controls["left_amplification"].value() == -0.3
    assert window.controls["right_amplification"].value() == -0.3
    assert window.sliders["right_amplification"].value() == -300
    window.controls["right_noise_reduction"].setValue(0.25)
    assert window.sliders["right_noise_reduction"].value() == 250
    assert window.sliders["left_noise_reduction"].value() == 250
    window.controls["left_conversation_boost"].setChecked(True)
    assert window.controls["right_conversation_boost"].isChecked()
    window.link_ears.setChecked(False)
    window.controls["right_tone"].setValue(0.4)
    assert window.controls["left_tone"].value() == 0.123
    window.link_ears.setChecked(True)
    assert window.controls["left_tone"].value() == 0.123
    assert window.controls["right_tone"].value() == 0.4
    window.apply()
    drain(app, window)
    assert abs(window.loaded.values()["left_amplification"] + 0.3) < 1e-6
    assert window.loaded.raw[40:44] == bytes(buf[40:44])
    assert "演示" in window.status.text()
    window.close()


def test_overview_modes_and_presets_share_demo_state(monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(demo=True)
    window.show()
    drain(app, window)
    assert window.battery_bars["left"].value() == 86
    assert window.device_state["mode"] == "transparency"
    window.change_mode("anc")
    drain(app, window)
    assert window.device_state["mode"] == "anc"
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", lambda *args: ("Office", True))
    window.save_profile()
    drain(app, window)
    assert window.profile_combo.currentText() == "Office"
    window.controls["left_amplification"].setValue(-0.5)
    window.apply()
    drain(app, window)
    assert window.loaded.values()["left_amplification"] == -0.5
    window.apply_profile("Office")
    drain(app, window)
    assert window.loaded.values()["left_amplification"] == 0
    assert window.device_state["mode"] == "anc"
    window.delete_profile()
    drain(app, window)
    assert window.profile_combo.count() == 0
    window.update_overview({'connected': True, 'bluetooth_connected': True, 'mode': 'transparency',
        'battery': {'right': {'percent': 100, 'charging': False}, 'left': {'percent': None}}})
    assert '已连接' in window.connection_label.text()
    assert window.battery_bars['right'].value() == 100
    assert window.battery_bars['left'].format() == '未报告'
    window.update_overview({'connected': False, 'bluetooth_connected': True, 'battery': {}})
    assert '蓝牙已连接，控制通道不可用' in window.connection_label.text()
    window.close()


def test_reconnect_audio_only_keeps_settings_disabled(monkeypatch):
    from airpods_transparency import gui
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    def rpc(action, *args, **kwargs):
        if action == "preset_list":
            return {"presets": {}}
        if action == "status":
            return {"connected": True, "bluetooth_connected": True, "battery": {}}
        assert action == "reconnect" and not kwargs["rebuild_settings"]
        return {"settings_ready": False, "settings_error": "ATT error 0x0e"}
    monkeypatch.setattr(gui, "rpc", rpc)
    window = Window()
    window.address.setText("AA:BB:CC:DD:EE:FF")
    window.reconnect_button.click()
    drain(app, window)
    assert "立体声音频已连接" in window.status.text()
    assert "0x0e" in window.status.toolTip()
    assert not window.apply_button.isEnabled()
    window.close()


def test_default_identity_read_error_explains_setup(monkeypatch):
    from airpods_transparency import gui
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    window.address.setText("AA:BB:CC:DD:EE:FF")
    def fail_read(self):
        raise RuntimeError("ATT error: 0x0e")
    monkeypatch.setattr(gui.Controller, "read", fail_read)
    monkeypatch.setattr(gui, "rpc", lambda *a, **kw: {"battery": {}})
    for identity, suggests_setup in (("usb:v1D6Bp0246d0540", True), ("bluetooth:v004Cp0000d0000", False)):
        monkeypatch.setattr(gui, "host_modalias", lambda mac: identity)
        window.load()
        drain(app, window)
        assert ("启用高级调节" in window.status.text()) == suggests_setup
        assert "0x0e" in window.status.text()
        assert not window.apply_button.isEnabled()
    window.close()


def test_setup_populates_sliders_from_reconnected_device(monkeypatch):
    from airpods_transparency import gui
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    calls = []
    raw = struct.pack("<26f", 1, *([0] * 25))
    def rpc(action, *args, **kwargs):
        if action == "reconnect":
            calls.append(action)
            return {"settings_ready": True, "backend": "att", "raw_hex": raw.hex()}
        if action == "preset_list":
            return {"presets": {}}
        return {"battery": {}}
    monkeypatch.setattr(gui, "rpc", rpc)
    monkeypatch.setattr(gui, "enable_advanced", lambda mac: calls.append("setup"))
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a: QtWidgets.QMessageBox.No)
    window = Window()
    window.address.setText("AA:BB:CC:DD:EE:FF")
    window.setup_button.click()
    assert not calls
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a: QtWidgets.QMessageBox.Yes)
    window.setup_button.click()
    drain(app, window)
    assert calls == ["setup", "reconnect"]
    assert window.loaded.raw == raw and window.sliders["left_amplification"].isEnabled()
    window.close()
