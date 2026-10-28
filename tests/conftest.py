"""Shared test set-up: run Qt off-screen (no windows pop up during the tests).

On Windows, Qt's off-screen platform finds no fonts of its own ("Qt no longer ships fonts") and lays the window
out with wrong metrics (far too wide). Pointing it at the Windows font folder gives it the real window's fonts.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
