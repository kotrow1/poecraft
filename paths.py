"""Папка программы: рядом с app.py, а в собранном .exe — рядом с exe (не во временной папке PyInstaller).
Там лежат data/, config.json, backup/."""
import os
import sys

FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
