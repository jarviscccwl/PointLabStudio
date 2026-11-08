# PointLab Studio

**v1.3.0** · 点云浏览、处理与分析工具

支持逐帧浏览、多图层叠加、双窗格对比、裁剪、测量、滤波、配准和网格重建，提供深色与浅色主题。基于 PySide6、pygfx/wgpu 和 Open3D。

## 快速启动

发布包位于 `dist/`，无需安装 Python：

| 系统 | 文件 | 使用方式 |
| --- | --- | --- |
| Windows 10/11 x64 | `PointLabStudio-v1.3.0-Windows-x64.exe` | 双击运行 |
| Ubuntu 24.04 x86_64 | `PointLabStudio-v1.3.0-Ubuntu-x86_64.tar.gz` | 解压后运行 `PointLabStudio` |
| Ubuntu 24.04 ARM64 | `PointLabStudio-v1.3.0-Ubuntu-arm64.tar.gz` | 解压后运行 `PointLabStudio` |

Ubuntu 包中的 `_internal` 是运行依赖，请与程序放在一起。Windows 已验证启动；Ubuntu 两种架构已检查启动入口和窗口依赖，尚未验证真实桌面与显卡。macOS Apple Silicon 暂无成品，已提供构建脚本。

需要可用的图形驱动；Windows 显卡需支持 Vulkan 或 DirectX 12。启动时会解压运行库，等待进度条完成即可。

## 开始使用

1. **打开点云**：拖入文件或文件夹，也可按 `Ctrl+O`。新打开的图层位于列表上方。
2. **调整视角**：左键旋转，右键或中键平移，滚轮缩放，双击设置旋转中心。按住 `Alt` 可慢速微调。
3. **逐帧查看**：文件夹按文件名自然排序，用方向键切帧。手动勾选其他点云可叠加显示；「双窗格」用于对比相邻帧。
4. **处理与导出**：在「处理」菜单选择操作，使用 `Ctrl+E` 导出当前点云。支持撤销、重做和任务取消。

### 工作区与导出有什么区别？

**只是查看点云，不需要创建或保存工作区。** 工作区用于下次继续当前的整理和处理。

| 目的 | 操作 | 保存内容 |
| --- | --- | --- |
| 下次继续工作 | `Ctrl+S` 保存工作区 | `.pls` 文件，以及需要时生成的同名 `.assets` 目录 |
| 在其他软件使用点云 | `Ctrl+E` 导出点云 | PLY、PCD、LAS 等点云文件 |

`.pls` 记录源文件路径、分组、视角和显示状态，处理结果保存在 `.assets` 中。分享或移动工作区时，需一起带上源点云、`.pls` 和已有的 `.assets`，保持相对位置。旧版 `.pls` 仍可打开。

普通浏览可直接关闭；有处理结果或已保存工作区的改动时，会提示保存。保存工作区不会覆盖源点云。「移出工作区」仅移除场景项目；重命名、移动原文件或「移到回收站」会改变磁盘文件。

### 常用快捷键

| 按键 | 功能 | 按键 | 功能 |
| --- | --- | --- | --- |
| `1` / `2` / `3` | 高度 / 强度 / RGB | `4` / `5` | 标签 / 标量场 |
| `6` / `7` | 单色 / 自动 | `C` | 循环颜色 |
| `←` / `↑` | 上一帧 | `→` / `↓` | 下一帧 |
| `R` | 重置视角 | `+` / `−` | 调整点大小 |
| `B` | 开关裁剪 | `Space` | 显示或隐藏选中点云 |
| `Ctrl+Z` / `Ctrl+Y` | 撤销 / 重做 | `H` | 查看全部快捷键 |
| `Alt` + 拖动或滚轮 | 以 20% 速度微调 | `Esc` | 退出工具或取消拖动 |

数字颜色键在视口或场景列表获得焦点时生效，不影响输入框。

## 文件格式与使用限制

- **读取**：BIN、PLY、PCD、XYZ/XYZN/XYZRGB、PTS、LAS/LAZ、E57、NPY/NPZ、TXT/CSV。
- **导出**：PLY、PCD、LAS、PTS、XYZ；需要保留更多字段时优先使用 PLY/PCD。
- **雷达包**：「打开 → 导入雷达包」支持 RoboSense EM4 的非压缩 pcap/pcapng（UDP 6699）和包含 `sensor_msgs/PointCloud2` 的 mcap，结果保存为逐帧 `.npy`。双回波数据取首回波。
- **采样预览**：大文件可能只加载部分点；此时处理和导出也只包含已加载点，请留意界面提示。
- **裁剪**：视口裁剪只改变显示。保存框内点请用「工作区 → 导出裁剪内点云」；生成新图层请用「处理 → 盒裁切」。
- **单位与属性**：坐标按米显示，不自动换算。体素降采样保留颜色和强度均值，其他属性不参与聚合；DBSCAN 超过 15 万点时采用抽样与标签传播。
- **LAS**：导出为 LAS 1.2，采用毫米精度，分类值限 0–31。LAZ/E57 仅支持读取。
- **回收站**：系统回收站不可用时会保留未处理文件，不改为永久删除；真实移入与撤销恢复尚需手动验证。

## 从源码运行

使用 Python 3.12。在项目根目录的 PowerShell 中执行：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.\start.bat
```

之后双击 `start.bat` 即可。源码读取 LAZ/E57 需额外安装 `laspy lazrs pye57`，发布包已包含这些依赖。

```powershell
# 直接打开文件或目录
.venv\Scripts\python.exe run.py "D:\点云目录"
# 查看批处理命令行帮助
.venv\Scripts\python.exe -m apps.cli --help
```

## 构建发布包

成品输出到 `dist/`，`build/` 仅存放中间文件。版本由 `core/__init__.py` 定义。

**Windows x64**：在上述源码环境中执行：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File packaging/build.ps1
```

**Ubuntu / macOS Apple Silicon**：在对应系统、架构的 Python 3.12 虚拟环境中执行：

```bash
python -m pip install -r requirements-build.txt
python packaging/build-native.py
```

Ubuntu 系统依赖见 [Dockerfile.linux](packaging/Dockerfile.linux)。macOS 构建需要 Apple Silicon Mac，生成 `.dmg`，脚本未配置签名或公证。

Windows 也可用 Docker Desktop 的 Linux 容器在本地构建 Ubuntu 包。以下命令生成 x86_64 包；将两处 `linux/amd64` 换为 `linux/arm64`、两处标签后缀 `amd64` 换为 `arm64` 即可构建 ARM64 包：

```powershell
docker build --platform linux/amd64 -f packaging/Dockerfile.linux -t pointlab-build:v1.3.0-amd64 .
docker run --rm --platform linux/amd64 --mount "type=bind,source=$($PWD.Path),target=/workspace" pointlab-build:v1.3.0-amd64
```

## 设置与排错

- **日志**：界面底部「日志」显示操作错误；启动日志为配置目录下的 `logs/pointlab.log`。
- **配置目录**：Windows 默认 `%APPDATA%\PointLabStudio\`，其他系统默认 `~/.pointlabstudio/`；可用 `POINTLAB_HOME` 指定。Windows EXE 在默认位置不可写时会尝试其他位置，包括 EXE 旁的 `.PointLabStudio/`。
- **无法启动或渲染**：先查看日志，再检查目录写入权限和显卡驱动。源码环境缺依赖时，重新安装 `requirements.txt`。
- **EXE 图标仍是旧样式**：关闭并重新打开文件夹；构建脚本会主动通知 Windows 刷新图标缓存。

代码目录：`apps/` 入口、`core/` 数据与算法、`ui/` 界面、`resources/` 图标、`packaging/` 构建、`tests/` 测试。

## 更新记录

### v1.3.0 · 2026-11-08

- 新图层置顶，新增 `1–7` 颜色快捷键和 `Alt` 慢速视角操作。
- 「工程」更名为「工作区」，明确保存与导出的区别，普通浏览无需保存确认。
- 新增单条百分比启动页，统一字体和配色，修复 `QRenderWidget` 短暂弹窗。
- 恢复原版蓝色图标，完善多尺寸图标与 Windows 图标缓存刷新。
- 提供 Windows x64、Ubuntu x86_64 / ARM64 包及 macOS Apple Silicon 构建脚本。

| 历史版本 | 主要变化 |
| --- | --- |
| v1.2.0 · 2026-11-07 | 雷达包导入、自由视角修正、重复代码清理（源码验证版） |
| v1.1.1 · 2026-11-07 | 文件夹树、批量文件管理、工作区保存与恢复（源码验证版） |
| v1.1.0 · 2026-10-31 | 逐帧浏览、双窗格、裁剪导出、网格管理和 Windows 单文件 EXE |
