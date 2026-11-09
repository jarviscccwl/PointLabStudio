"""Build on the target macOS or Ubuntu architecture."""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

root = Path(SPECPATH).parent
sys.path.insert(0, str(root))
from core import __version__

if sys.platform not in ("darwin", "linux"):
    raise RuntimeError("Build this spec on macOS or Linux, not Windows.")

datas = [(str(root / "resources"), "resources")]
for package in ("pygfx.data_files", "pygfx.renderers.wgpu.wgsl", "wgpu", "dash", "plotly"):
    datas += collect_data_files(package)
for package in ("open3d", "numpy", "scipy", "pygfx", "wgpu", "rendercanvas", "laspy", "pye57", "dash", "plotly"):
    datas += copy_metadata(package)
binaries = []
for package in ("open3d", "wgpu", "pye57"):
    binaries += collect_dynamic_libs(package)
binaries = [(src, dst) for src, dst in binaries if "torch_ops" not in src]

a = Analysis(
    [str(root / "run.py")], pathex=[str(root)], binaries=binaries, datas=datas,
    hookspath=[str(root / "packaging" / "hooks")],
    hiddenimports=[
        "wgpu.backends.auto", "wgpu.backends.wgpu_native", "rendercanvas.qt",
        "laspy", "lazrs", "laspy._compression.lazrsbackend", "pye57", "pye57.libe57",
        "mcap", "mcap.reader", "mcap.stream_reader", "mcap.summary", "mcap.records",
        "mcap_ros2", "mcap_ros2.reader", "mcap_ros2.writer", "mcap_ros2.decoder",
        "mcap_ros2._dynamic", "mcap_ros2._cdr", "lz4", "lz4.frame", "zstandard",
    ],
    excludes=["tkinter", "IPython", "jupyter", "notebook", "pytest", "_pytest", "pip",
              "ipykernel", "debugpy", "jupyter_client", "jupyter_core", "torch", "tensorflow", "mitsuba"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PointLabStudio",
          debug=False, strip=False, upx=False, console=False,
          target_arch="arm64" if sys.platform == "darwin" else None)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="PointLabStudio")
if sys.platform == "darwin":
    app = BUNDLE(collection, name="PointLabStudio.app", version=__version__,
                 bundle_identifier="com.pointlabstudio.desktop",
                 info_plist={"NSHighResolutionCapable": True})
