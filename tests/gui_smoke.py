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
    assert window.edited_values() == {}
    buf = bytearray(window.loaded.raw)
    struct.pack_into("<f", buf, 40, 0.123456)
    window.controller.settings = Settings(bytes(buf))
    window.populate(window.controller.read())
    assert window.edited_values() == {}
    window.controls["left_amplification"].setValue(-0.3)
    window.apply()
    drain(app, window)
    assert abs(window.loaded.values()["left_amplification"] + 0.3) < 1e-6
    assert window.loaded.raw[40:44] == bytes(buf[40:44])
    assert "演示" in window.status.text()
    window.close()
