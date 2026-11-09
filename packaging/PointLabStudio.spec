from pathlib import Path
import ast
import os
import struct
import sys

from PyInstaller.config import CONF
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct,
    VSVersionInfo,
)

root = Path(SPECPATH).parent
# Restrict DLL discovery to Python and Windows, excluding unrelated developer tools on PATH.
os.environ["PATH"] = os.pathsep.join(str(path) for path in (
    Path(sys.base_prefix), Path(sys.base_prefix) / "DLLs", Path(sys.prefix) / "Scripts",
    Path(os.environ["SystemRoot"]) / "System32", Path(os.environ["SystemRoot"]),
))
version_tree = ast.parse((root / "core" / "__init__.py").read_text(encoding="utf-8"))
version = next(ast.literal_eval(node.value) for node in version_tree.body
               if isinstance(node, ast.Assign) and any(
                   isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets))
version_tuple = tuple(map(int, version.split("."))) + (0,)
name = f"PointLabStudio-v{version}-Windows-x64"

# Windows ICO can embed a PNG; render the shared SVG without an image editor dependency.
from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)
entries, images = [], []
offset = 6 + 16 * len(sizes)
renderer = QSvgRenderer(str(root / "resources" / "app.svg"))
for size in sizes:
    icon_image = QImage(size, size, QImage.Format_ARGB32)
    icon_image.fill(Qt.transparent)
    painter = QPainter(icon_image)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter)
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    icon_image.save(buffer, "PNG")
    png = bytes(buffer.data())
    entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset))
    images.append(png)
    offset += len(png)
icon = Path(CONF["workpath"]) / "app.ico"
icon.write_bytes(struct.pack("<HHH", 0, 1, len(sizes)) + b"".join(entries) + b"".join(images))
datas = [(str(root / "resources"), "resources")]
for package in ("pygfx.data_files", "pygfx.renderers.wgpu.wgsl", "wgpu", "dash", "plotly"):
    datas += collect_data_files(package)
for package in ("open3d", "numpy", "scipy", "pygfx", "wgpu", "rendercanvas", "laspy", "pye57", "dash", "plotly"):
    datas += copy_metadata(package)
binaries = []
for package in ("open3d", "wgpu", "pye57"):
    binaries += collect_dynamic_libs(package)
binaries = [(src, dst) for src, dst in binaries if "torch_ops" not in src]
# Load the Qt wheel's newer VC runtime before Python loads an older system copy.
import PySide6
qt_dir = Path(PySide6.__file__).parent
for pattern in ("VCRUNTIME140*.dll", "MSVCP140*.dll"):
    binaries += [(str(path), ".") for path in qt_dir.glob(pattern)]

a = Analysis(
    [str(root / "run.py")],
    pathex=[str(root)],
    hookspath=[str(root / "packaging" / "hooks")],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "wgpu.backends.auto", "wgpu.backends.wgpu_native", "rendercanvas.qt",
        "laspy", "lazrs", "laspy._compression.lazrsbackend", "pye57", "pye57.libe57",
        "mcap", "mcap.reader", "mcap.stream_reader", "mcap.summary", "mcap.records",
        "mcap_ros2", "mcap_ros2.reader", "mcap_ros2.writer", "mcap_ros2.decoder",
        "mcap_ros2._dynamic", "mcap_ros2._cdr", "lz4", "lz4.frame", "zstandard",
    ],
    excludes=["tkinter", "IPython", "jupyter", "notebook", "pytest", "_pytest", "py", "pip",
              "ipykernel", "debugpy", "jupyter_client", "jupyter_core", "torch", "tensorflow", "mitsuba"],
    noarchive=False,
)
# Qt uses Windows ICU. An unrelated ICU on PATH has incompatible entry points.
system_icu = {"icu.dll", "icuuc.dll", "icuin.dll"}
a.binaries = [entry for entry in a.binaries if Path(entry[0]).name.lower() not in system_icu]
pyz = PYZ(a.pure)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version_tuple, prodvers=version_tuple,
                      mask=0x3F, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable("040904B0", [
        StringStruct("CompanyName", "PointLab Studio"),
        StringStruct("FileDescription", "PointLab Studio - Point Cloud Workstation"),
        StringStruct("FileVersion", version),
        StringStruct("ProductName", "PointLab Studio"),
        StringStruct("ProductVersion", version),
        StringStruct("OriginalFilename", f"{name}.exe"),
    ])]), VarFileInfo([VarStruct("Translation", [1033, 1200])])],
)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name=name, debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, version=version_info, icon=str(icon),
)
