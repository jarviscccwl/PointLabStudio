<p align="center">
  <img src="resources/app.svg" width="64" height="64" alt="PointLab Studio">
</p>

<h1 align="center">PointLab Studio</h1>

<p align="center">
  <strong>点云与多传感器数据的桌面工作台</strong><br>
  浏览 · 同步 · 融合 · 三维框选 · 点云提取
</p>

<p align="center">
  <a href="https://github.com/jarviscccwl/PointLabStudio/releases/tag/v2.0.0"><img src="https://img.shields.io/badge/release-v2.0.0-2563eb?style=flat-square" alt="Release v2.0.0"></a>
  <img src="https://img.shields.io/badge/desktop-Windows_x64-475569?style=flat-square" alt="Windows x64">
  <img src="https://img.shields.io/badge/python-3.12-475569?style=flat-square" alt="Python 3.12">
</p>

<p align="center">
  <a href="https://github.com/jarviscccwl/PointLabStudio/releases/download/v2.0.0/PointLabStudio-v2.0.0-Windows-x64.exe"><strong>下载 Windows 版</strong></a>
  &nbsp; · &nbsp; <a href="docs/usage.md">使用手册</a>
  &nbsp; · &nbsp; <a href="#从源码运行">源码运行</a>
  &nbsp; · &nbsp; <a href="#更新记录">更新记录</a>
  &nbsp; · &nbsp; <a href="https://github.com/jarviscccwl/PointLabStudio/issues">问题反馈</a>
</p>

---

PointLab Studio 将点云、图像和采集包放在同一个桌面工作流中。从打开文件、逐帧检查，到调整三维框、关联相邻帧和提取目标点云，无需在多个工具之间切换。基于 **PySide6、pygfx / wgpu 和 Open3D**，提供深色与浅色主题。

## 下载与运行

| 平台 | 获取方式 | 说明 |
| :--- | :--- | :--- |
| **Windows 10 / 11 · x64** | [**下载 v2.0.0 EXE**](https://github.com/jarviscccwl/PointLabStudio/releases/download/v2.0.0/PointLabStudio-v2.0.0-Windows-x64.exe) | 单文件，约 219 MiB，无需安装 Python |
| **源码** | [v2.0.0 源码 ZIP](https://github.com/jarviscccwl/PointLabStudio/archive/refs/tags/v2.0.0.zip) · [main](https://github.com/jarviscccwl/PointLabStudio/tree/main) | 固定版本或当前源码 |
| **Linux / macOS** | [自行构建](docs/usage.md#构建发布包) | 本次未提供这些平台的 v2.0.0 成品 |

下载 EXE 后双击启动，将点云文件或采集目录拖入窗口即可浏览。显卡需支持 Vulkan 或 DirectX 12；首次启动需等待运行库解压。

[发行说明与文件校验](https://github.com/jarviscccwl/PointLabStudio/releases/tag/v2.0.0)

## 核心能力

**多源浏览与同步**  
打开 PCAP、MCAP、ROS BAG 或普通文件，先查看数据概览，再选择数据流。支持逐帧播放、双窗格对照、时间容差与偏移，同组点云和图像可自动配对。

**相机标定与融合**  
导入 JSON / YAML 内外参或选用 CameraInfo，将点云投影到图像、为点云赋予图像 RGB，并联动显示三维框。

**三维框编辑与关联**  
俯视、前视、侧视联动编辑，支持完整三轴姿态、键盘精调与局部撤销。以人工框为锚点关联前后帧，再逐帧检查和修正。

**提取、处理与导出**  
当前框内点云可直接加入场景；关联多帧可批量导出 PCD 和时间戳清单。支持滤波、测量、裁剪、配准、网格重建及工作区保存。

## 一个典型工作流

1. **打开数据** — 拖入采集目录，选择点云和图像流。
2. **同步检查** — 在双窗格中逐帧浏览，按需要设置时间匹配和标定。
3. **建立目标框** — 点击「3D 框选」，用三视图调整范围与姿态。
4. **关联与修正** — 关联相邻帧，检查自动建议，保留人工修正。
5. **提取结果** — 当前帧加入场景，或批量输出有框帧的 PCD；保存 `.pls` 以继续工作。

[阅读完整操作指南 →](docs/usage.md)

## 格式支持

| 数据 | 格式 |
| :--- | :--- |
| 点云 | BIN、PLY、PCD、XYZ、PTS、LAS / LAZ、E57、NPY / NPZ、数值 TXT / CSV |
| 采集包 | PCAP / PCAPNG、MCAP、ROS1 BAG、ROS2 MCAP / SQLite |
| 图像与文本 | PNG、JPEG、BMP、TIFF、WebP、GIF；TXT、LOG、CSV、TSV、JSON、YAML |
| 输出 | PLY、PCD、LAS、PTS、XYZ；标注 JSON；工作区 `.pls` |

PCAP 当前支持 RoboSense EM4 非压缩数据。自动关联基于局部几何，结果需要人工检查；融合需要实际内外参与时间匹配。详细边界见[格式与使用限制](docs/usage.md#文件格式与使用限制)。

## 从源码运行

推荐使用 **Python 3.12**。Windows PowerShell：

```powershell
git clone --branch v2.0.0 https://github.com/jarviscccwl/PointLabStudio.git
cd PointLabStudio

py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.\start.bat
```

之后双击 `start.bat` 启动。源码读取 LAZ / E57 需额外安装 `laspy lazrs pye57`，Windows EXE 已包含这些依赖。

```powershell
# 直接打开文件或目录
.venv\Scripts\python.exe run.py "D:\点云目录"

# 批处理命令行
.venv\Scripts\python.exe -m apps.cli --help
```

[构建 Windows / Linux / macOS 发布包 →](docs/usage.md#构建发布包)

## 文档与反馈

- [使用手册](docs/usage.md)：导入、同步、标定、框编辑、快捷键与工作区。
- [框内提取与性能](docs/box-extraction-performance-2026-11-09.md)：提取范围、字段保留、批量导出与性能实测。
- [文档目录](docs/README.md)：功能说明和历史验证记录。
- [提交问题](https://github.com/jarviscccwl/PointLabStudio/issues)：请附版本、系统、复现步骤及日志中的错误信息。

本次 Windows 源码回归发现 394 项测试：378 项通过、16 项因回收站或符号链接权限跳过。发布 EXE 已验证独立启动、1,000 点 PLY 加载与渲染、正常退出。大型采集包的历史验收见[交付记录](docs/release-v2.0.0-preview-2026-11-09.md)。

## 更新记录

### v2.0.0 · 2026-11-09

- 发布 v2.0.0 源码至 `main`，以同名标签固定版本；更新首页导航、功能概览及 Windows 成品校验信息。
- 新增当前帧完整点云提取、关联多帧批量 PCD 导出与来源清单；保留数值字段和纳秒时间戳，支持取消和失败清理。
- 新增图像为点云赋色、三维框到图像的投影联动，以及双窗格颜色设置的保存恢复。
- 优化三视图批量绘制、点云范围计算、MCAP reader 复用和算法按需加载；启动动画使用独立轻量进程。

- 修复较大树冠的前后帧关联误判，启动关联、查看进度和跳转有框帧更直接；新增无框帧返回入口，保留人工修正。
- Windows 包补齐动态 ROS 类型库及消息序列化依赖，避免图像和相机参数因缺少模块而退回原始消息；包含 ROS1/ROS2 BAG、MCAP 原始与压缩图像等已有读取能力。
- 框选与跟踪合并为「3D 框选」，新增嵌入式三视图、完整三轴姿态、鼠标/键盘调整及局部撤销；普通场景复用相同编辑器。
- 修复瞬态绘制/呈现异常后的显示恢复，以及成功重绘后仍误报暂停的问题；增加单话题重载，保留帧位置、视角、同步、标定和标注。
- 主视图与对照视图按行排列，播放操作和时间状态分区；底栏长文字自动省略，减少重复信息。数据包概览的范围选择与底部操作分开，3D 框参数按需展开，小窗口支持滚动。
- 使用生成的点云车辆视觉与实时扫描动效作为启动页，加载进度平滑追随真实数值；源码启动期间动画独立运行，不人为延长启动。
- 减少点云显示时重复的边界扫描和颜色计算临时数组；播放间隔计入读取耗时，新增首末帧按钮及预览区 `Space` / `Home` / `End` 快捷键。
- 直接打开采集文件夹，自动配对同组 PCAP 点云与 MCAP/BAG 图像，定位首对时间匹配帧并开启双窗格。
- 新增目标框选、相邻帧几何关联、人工修正和标注 JSON 导出；目标 ID、原始帧号及人工锚点随工作区保存。
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
