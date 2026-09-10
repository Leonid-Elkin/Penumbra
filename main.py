#!/usr/bin/env python3
"""
Penumbra – entry point.

All gameplay lives in the `game` package:
  - vehicles/ and vehicles/bosses/ hold one JSON config per unit (edit those to
    change cannons, attacks, where shots come from, stats, sprite, etc.)
  - levels/ holds one JSON per level (which boss defends + environment theme)
  - game/ holds the engine, entities and UI (menu / hud / battle)
"""

import sys
from PyQt6.QtCore    import Qt
from PyQt6.QtGui     import QSurfaceFormat
from PyQt6.QtWidgets import QApplication

# The install path may contain non-Latin characters; keep console output from
# crashing on a legacy code page.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Scale the whole UI with the display: honour the OS's (possibly fractional)
# display-scaling factor so the interface is sized correctly at any resolution.
try:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
except AttributeError:
    pass

# The battlefield and the animated menus render through QOpenGLWidget, so hand
# the whole app one GPU surface format up front: vsync'd double buffering (no
# tearing) with a little multisampling for clean sprite/vector edges.
_fmt = QSurfaceFormat()
_fmt.setSwapInterval(1)            # vsync – cap present rate to the display
_fmt.setSwapBehavior(QSurfaceFormat.SwapBehavior.DoubleBuffer)
_fmt.setSamples(4)                 # 4× MSAA for smooth diagonals
QSurfaceFormat.setDefaultFormat(_fmt)

from game.ui import AppWindow
from game.icon import app_icon


def main():
    # Tell Windows this process is its own app (not "Python"), so the taskbar
    # groups our windows under our icon instead of the default python.exe one.
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Penumbra.Penumbra")
        except Exception:                       # noqa: BLE001 – cosmetic only
            pass

    app = QApplication(sys.argv)
    app.setApplicationName("Penumbra")
    app.setWindowIcon(app_icon())               # title-bar / taskbar / Alt-Tab
    app.setStyle("Fusion")
    win = AppWindow(); win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()


    