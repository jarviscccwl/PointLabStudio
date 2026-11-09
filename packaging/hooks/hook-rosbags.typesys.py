"""ROS type stores are selected with import_module(), so analysis cannot see them."""
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("rosbags.typesys.stores")
