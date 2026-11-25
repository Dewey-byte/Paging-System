# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build specification for the School Paging System.
# Build with:  .venv\Scripts\pyinstaller.exe --noconfirm school_paging_system.spec

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

datas = [("assets", "assets")]
datas += collect_data_files("sounddevice")
datas += collect_data_files("soundfile")
binaries = collect_dynamic_libs("sounddevice") + collect_dynamic_libs("soundfile")

# Qt modules the application never uses (keeps the bundle small).
qt_excludes = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.Qt3DCore",
    "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtDesigner", "PySide6.QtBluetooth", "PySide6.QtLocation", "PySide6.QtPositioning",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets", "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSpatialAudio",
    "PySide6.QtTextToSpeech", "PySide6.QtHttpServer", "PySide6.QtNfc", "PySide6.QtWebSockets",
]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=["win32com", "win32com.client", "pythoncom", "pywintypes", "win32timezone",
                   "reportlab.graphics.barcode.common", "reportlab.graphics.barcode.code128"],
    hookspath=[],
    runtime_hooks=[],
    excludes=qt_excludes + ["tkinter", "pytest", "matplotlib", "scipy", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SchoolPagingSystem",
    icon="assets/icons/app.ico",
    console=False,
    disable_windowed_traceback=False,
    upx=False,
    version=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="SchoolPagingSystem",
)
