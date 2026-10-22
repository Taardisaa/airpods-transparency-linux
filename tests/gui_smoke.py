"""Optional GUI smoke test; run with QT_QPA_PLATFORM=offscreen."""
import struct
import time

from PySide6 import QtWidgets

from airpods_transparency.gui import Window
from airpods_transparency.settings import Settings


def drain(app, window):
    deadline = time.monotonic() + 5
    while window.pending and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert window.pending is None


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
