# PointLab Studio

**基于 v1.3.0 的源码改进版** · 点云、图像与传感器采集包浏览、处理与分析工具

支持逐帧浏览、多图层叠加、双窗格对比、裁剪、测量、滤波、配准和网格重建，提供深色与浅色主题。基于 PySide6、pygfx/wgpu 和 Open3D。

本次新增功能从 **`start.bat`** 启动。`dist/PointLabStudio-v1.3.0-Windows-x64.exe` 保留为原版发行文件，尚未重新打包。

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

1. **打开文件**：拖到左侧场景或中间视图，也可按 `Ctrl+O` 或点击「打开文件或文件夹」。采集包会先显示内容概览；普通点云、图片和文本可直接浏览。
2. **调整视角**：左键旋转，右键或中键平移，滚轮缩放，双击设置旋转中心。按住 `Alt` 可慢速微调。
3. **逐帧查看**：文件夹按文件名自然排序，用方向键切帧。手动勾选其他点云可叠加显示；「双窗格」用于对比相邻帧。
4. **处理与导出**：在「处理」菜单选择操作，使用 `Ctrl+E` 导出当前点云。支持撤销、重做和任务取消。

### 采集包：先看内容，再选择浏览

打开 PCAP/PCAPNG、MCAP、ROS1 BAG 或 ROS2 采集目录后，概览会列出数据源、话题名、消息格式、类型、数量、记录时间和读取提示。ROS2 分卷作为一个数据源，同目录图片按文件名自然排序。

默认勾选全部数据流、浏览全部帧，**不需要提前填写帧数**。也可按帧范围或相对记录时间选择。PCAP 首次只做快速识别，数量显示「待统计」；点击「统计准确帧数」可先扫描并查看准确数量，也可直接「加入浏览」完成索引。首次扫描大包需要时间，支持取消，后续复用本机索引。索引不会写进原始采集目录，也不会先解包成海量文件。

左侧按数据源列出点云、图像、相机参数、IMU 和其他消息；点击后在中央查看。使用帧号、滑条、上一帧/下一帧或固定帧率播放。图像支持缩放、拖动、适应窗口和 1:1；「帧信息」展开编码、时间来源、坐标系和解析提示。暂不支持的消息保留有界原始内容与错误说明。

「同步查看」以左侧为主，在右侧寻找最接近的时间。默认使用记录/抓包时间；也可切换设备/消息头时间，设置匹配容差和右侧时间偏移。偏移加在右侧时间上，超过容差或缺少所选时钟会明确显示未匹配。两类时间以整数纳秒保存，不隐式修正时钟差；普通图片、文本没有时间戳时不会虚构时间。

「导出当前帧」导出左侧当前内容；图片保留去畸变效果，投影标记仅供查看，文本/表格导出当前页。若需滤波、测量、裁剪等现有点云工具，先点「当前点云加入场景」，得到独立处理副本。

### 相机参数与投影

可选择包内 CameraInfo，或导入 JSON/YAML 内外参。单条 CameraInfo 作为静态参数保留，多条参数按当前图像时间匹配。支持 `plumb_bob`、`rational_polynomial` 和 `equidistant`，处理原始 CameraInfo 的 ROI 与 binning，并可显示去畸变图像。

点云投影还需要明确的雷达到相机外参；不会把缺失外参当作单位矩阵。预览最多采样 50,000 点。导入文件可以包含完整内外参，也可在已选 CameraInfo 的基础上只提供外参。导入后使用这一组固定参数；切换数据流组合会重新选择相机参数，避免沿用另一相机的标定。

以下只是文件结构示例，数字必须换成实际标定结果：

```yaml
width: 1920
height: 1080
K: [[1000, 0, 960], [0, 1000, 540], [0, 0, 1]]
D: [0, 0, 0, 0, 0]
distortion_model: plumb_bob
translation_units: m
T_camera_lidar:
  - [1, 0, 0, 0]
  - [0, 1, 0, 0]
  - [0, 0, 1, 0]
  - [0, 0, 0, 1]
```

`T_camera_lidar` 将雷达点变换到相机光学坐标系（X 向右、Y 向下、Z 向前）；也支持反向的 `T_lidar_camera`。平移单位可选 `m`、`cm`、`mm`，点云按米解释。ROS `camera_matrix/data`、`distortion_coefficients/data` 写法同样可读。包内 K/D 用于原始图像；已经校正的图像应导入与其像素空间一致的内参、零畸变和对应光学坐标系外参，不会自动用 P/R 替换 K/D。

### 文本、表格与工作区

TXT/LOG/CSV/TSV/JSON/YAML 可以直接查看；大文本和表格按页读取，提供「上一页/下一页」、本页搜索和表格排序。默认每页最多约 256 KiB、表格 1,000 行，CSV 引号内换行保持完整；超长单条记录会提示。文本按 UTF-8 读取，其他编码会提示可能的替换字符。

数值 TXT/CSV/TSV 可点击「作为点云导入」，预览并明确选择 X/Y/Z 列，其余数值列保留为标量场。无表头文件需要指定列，导入结果可继续使用点云处理功能。

保存 `.pls` 会保留数据源、所选话题、范围、当前帧、同步设置及手工标定；再次打开按路径重新读取，原文件保持不变。移动工作区时也要保留采集包的相对位置。

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
| `6` | 单色 | `C` | 循环颜色 |
| `←` / `↑` | 上一帧 | `→` / `↓` | 下一帧 |
| `R` | 重置视角 | `+` / `−` | 调整点大小 |
| `B` | 开关裁剪 | `Space` | 显示或隐藏选中点云 |
| `Ctrl+Z` / `Ctrl+Y` | 撤销 / 重做 | `H` | 查看全部快捷键 |
| `Alt` + 拖动或滚轮 | 以 20% 速度微调 | `Esc` | 退出工具或取消拖动 |

数字颜色键在视口或场景列表获得焦点时生效，不影响输入框。

## 文件格式与使用限制

- **读取**：BIN、PLY、PCD、XYZ/XYZN/XYZRGB、PTS、LAS/LAZ、E57、NPY/NPZ、TXT/CSV。
- **导出**：PLY、PCD、LAS、PTS、XYZ；需要保留更多字段时优先使用 PLY/PCD。
- **采集容器**：MCAP、ROS1 BAG、ROS2 MCAP/SQLite 目录、独立 DB3；按消息定义识别内容，不依赖话题名称是否含有 `raw` 或 `compressed`。
- **点云消息**：PointCloud2 支持大小端、行/点填充、RGB 和额外字段；PCAP/PCAPNG 当前支持 RoboSense EM4 非压缩 MSOP/DIFOP、多传感器与不完整帧。EM4 双回波取首回波，缺少校准时明确提示使用近似角度。
- **图像消息**：JPEG/PNG 压缩图像、PNG compressedDepth；raw RGB/BGR/RGBA/BGRA、mono8/16、8UC1/16UC1/32FC1、Bayer8/16、UYVY/YUYV。高位深和浮点深度显示时缩放为可视范围，元数据保留原始编码与缩放说明。
- **普通图片**：PNG/JPEG/BMP/TIFF/WebP/GIF；动画或多页图片当前读取第一帧。
- **尚未支持**：其他厂商 PCAP 协议、EM4 压缩/分片 MSOP、RVL 图像压缩、仅有 IDL 的部分 MCAP 动态消息定义。未知消息保留预览与提示。EM4 非零水平角校正存在厂商文档映射歧义，会显示警告；本次提供的四组 PCAP 校准均为零水平偏移。
- **资源限制**：ROS2 文件级 zstd 压缩由读取库先解压临时存储，该阶段取消可能需要等待。帧读取使用有界缓存；大型文件的首次索引仍需顺序扫描。
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

### 源码改进 · 未发布

- 统一采集包概览、选择与索引浏览，新增图像、相机参数、IMU、消息和文本表格查看。
- 新增跨文件时间匹配、内外参导入、图像去畸变和点云投影预览，工作区可恢复浏览设置。
- 修正 EM4 包字段、字节序、帧边界与时间来源；保留原始纳秒时钟和部分帧提示。
- 颜色菜单与快捷键统一为 `1–6`，不再提供「自动」项；旧工作区的自动颜色恢复为高度模式。
- 已用 `D:\2026_10_14` 的五组 MCAP、四组 PCAP 验证读取；其中一组通过正常界面入口验证双流浏览、去畸变及工作区往返恢复。其他格式由合成文件回归测试覆盖。

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
