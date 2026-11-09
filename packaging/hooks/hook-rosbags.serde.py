"""Generated ROS serializers import primitives at runtime, outside static analysis."""

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("rosbags.serde")
