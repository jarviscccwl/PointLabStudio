<p align="center">
  <img src="resources/app.svg" width="64" height="64" alt="PointLab Studio">
</p>

<h1 align="center">PointLab Studio</h1>

<p align="center">
  <strong>点云与多传感器数据的桌面工作台</strong><br>
  浏览 · 同步 · 标定 · 三维框选 · 导出
</p>

<p align="center">
  <a href="https://github.com/jarviscccwl/PointLabStudio/tree/preview"><img src="https://img.shields.io/badge/preview-v2.1.0-2563eb?style=flat-square" alt="preview v2.1.0"></a>
  <img src="https://img.shields.io/badge/desktop-Windows_x64-475569?style=flat-square" alt="Windows x64">
  <img src="https://img.shields.io/badge/python-3.12-475569?style=flat-square" alt="Python 3.12">
</p>

<p align="center">
  <a href="#5-分钟上手"><strong>5 分钟上手</strong></a>
  &nbsp; · &nbsp; <a href="#下载与运行">下载与运行</a>
  &nbsp; · &nbsp; <a href="docs/usage.md">使用手册</a>
  &nbsp; · &nbsp; <a href="#从源码运行">源码运行</a>
  &nbsp; · &nbsp; <a href="https://github.com/jarviscccwl/PointLabStudio/issues">问题反馈</a>
</p>

---

PointLab Studio 将点云、图像和采集包放在同一个窗口中：从选择数据流、同步查看，到修正内外参、编辑目标框，再保存融合动画或提取点云。也可直接打开普通点云，进行测量、滤波、裁剪、配准和网格重建。基于 **PySide6、pygfx / wgpu 和 Open3D**，提供深色与浅色主题。

> **当前版本：v2.1.0。** 完整内外参编辑、融合与区间动画导出、自定义文件名、目标类别、逐帧显示和工作台体验更新已纳入本版。源码位于 [`preview`](https://github.com/jarviscccwl/PointLabStudio/tree/preview) 分支；Windows EXE 仅交付到本地 `dist/PointLabStudio-v2.1.0-Windows-x64.exe`，不上传 GitHub。也可用 `start.bat` 从源码运行。公开的 v2.0.0 Release 是历史版本。

## 5 分钟上手

以下流程适用于 **v2.1.0**。先准备一组采集数据；首次索引大型文件所需时间取决于数据量和磁盘速度。

1. **打开采集目录** — 将文件夹拖入窗口，或点击「打开文件或文件夹」。可直接读取 PCAP/PCAPNG、MCAP、ROS BAG 和同组图像，无需先拆成逐帧文件。
2. **在概览中选择内容** — 查看数据源与话题，按名称搜索、按类型筛选，再点「加入浏览」。默认浏览全部帧，也可限定帧范围或时间范围，不必预先知道总帧数。
3. **同步查看点云与图像** — 同组数据自动配对为双窗格，用播放条、方向键或帧号逐帧检查。在「时间匹配」中调整时钟来源、容差与偏移，缺少匹配时会明确提示。
4. **导入或编辑内外参** — 点击图像上方「融合」，导入标定文件或 ZIP，分别选择内参和外参。用「编辑内参」「编辑外参」修正参数，再开启点云投影、图像赋色或图像中的 3D 框；修改后刷新预览，可另存标定。
5. **建立目标与跨帧关联** — 点击「3D 框选」，新建框后在俯视、前视、侧视中调整位置、尺寸与姿态。选择或输入「类别」，按 Enter 保存，再关联相邻帧并逐帧检查、人工修正。类别随同一目标保留。
6. **命名并保存结果** — 点击「选择区间」，拖动时间轴的「起」「止」手柄；再点「导出」，选择当前帧、时间轴选区或全部帧及格式，最后「命名并保存…」。**GIF/MP4 只生成一个动画文件**；单帧 PNG/PLY/PCD 也直接保存为一个文件。多帧输出使用输入名称加帧号，不另建结果目录或附加清单。

只想提取目标点云时，用「提取框内点」加入场景继续处理，或从其下拉菜单选择「导出有框帧点云…」，自定义名称并批量保存为 **PCD / PLY**。保存 `.pls` 工作区可下次继续同步设置、标定、选区和目标标注；普通浏览无需先创建工作区。

[完整操作步骤、快捷键与示例 →](docs/usage.md)

## v2.1.0 能力

| 任务 | 可以完成的操作 |
| :--- | :--- |
| **浏览与同步** | 采集目录概览、话题筛选、范围选择、双窗格、时间定位与同步；复用索引、预加载邻帧，切帧保留视角 |
| **内外参与融合** | 多格式标定导入；编辑图像尺寸、焦距、主点、畸变、平移与旋转或 4×4 外参矩阵；去畸变、点云投影、图像赋色与三维框投影 |
| **目标标注** | 三视图调整完整三轴姿态，目标名称与类别，前后帧几何关联、人工修正、局部撤销及标注 JSON 导出 |
| **提取与处理** | 当前框内点加入场景，有框帧批量 PCD/PLY；测量、滤波、裁剪、配准、网格重建及常用点云格式导出 |
| **继续工作** | `.pls` 保留数据源、浏览设置、手工标定和目标标注；处理结果按需存入同名 `.assets` 目录 |

**两种 3D 框用途不同：** 采集包中的框是带目标 ID、类别和逐帧记录的标注；普通点云场景中的框是临时范围选择工具，用于提取或删除框内点，切换点云后清除。跨帧目标标注在采集包浏览中管理。

小窗口下，三视图采用紧凑布局，为点云与图像保留预览空间，手动调整分隔条后保留尺寸。空间不足时自动收起区间输入说明，已选范围和时间轴手柄保留，也可在导出窗口精确输入；放大窗口后恢复。标定窗口可滚动填写，应用按钮始终可见；图像边缘标签向内排布，减少文字裁切。

切帧和完成建框时会清除旧屏幕选点高亮，当前帧的框内高亮从当前点云重新计算。后台预加载期间可继续调整当前框；快速返回缓存帧也能继续编辑，减少等待和重复刷新。首次索引、解码与持续播放速度仍取决于数据量和设备。

## 导出什么，得到什么

| 需要的结果 | 操作与格式 |
| :--- | :--- |
| **分享融合动画** | 播放条「导出」→ MP4 / GIF；可选同步双窗格、投影图像或点云画面，自定义名称、尺寸和速度 |
| **保存画面或帧点云** | 播放条「导出」→ PNG、PLY、PCD 或 PNG＋PLY；画面包含已启用的投影点和 3D 框，点云保留源坐标、字段及 RGB |
| **提取有框目标** | 「提取框内点」下拉菜单 →「导出有框帧点云…」；PCD / PLY，多帧追加原始帧号 |
| **交付标注或继续编辑** | 单独导出标注 JSON；`Ctrl+S` 保存 `.pls` 工作区 |

区间导出包含起止两端，帧号对应当前导入的浏览范围，「全部帧」也指该范围。多帧画面/点云追加浏览帧号；有框目标批量提取追加原始帧号。动画采用设定帧率，缺少匹配图像时显示空白占位，不沿用上一帧。

有匹配图像且启用赋色时，点云导出为有效采样点写入 RGB，并以 `image_color_valid` 标明；其余点保留源 RGB，没有源 RGB 时使用中性灰。高度、强度等显示色带不会写成点云 RGB。覆盖已有文件前确认，导出可取消，结束后恢复浏览位置与视角。

## 下载与运行

| 版本 / 平台 | 获取方式 | 说明 |
| :--- | :--- | :--- |
| **v2.1.0 · Windows 10 / 11 x64** | 本地 `dist/PointLabStudio-v2.1.0-Windows-x64.exe` | 单文件，无需安装 Python；本次不提供 GitHub EXE 下载 |
| **v2.1.0 源码** | [`preview` 分支](https://github.com/jarviscccwl/PointLabStudio/tree/preview) · [源码 ZIP](https://github.com/jarviscccwl/PointLabStudio/archive/refs/heads/preview.zip) | 包含本页更新，按下文配置后运行 `start.bat` |
| **历史 v2.0.0 · Windows x64** | [下载旧版 EXE](https://github.com/jarviscccwl/PointLabStudio/releases/download/v2.0.0/PointLabStudio-v2.0.0-Windows-x64.exe) · [固定源码](https://github.com/jarviscccwl/PointLabStudio/archive/refs/tags/v2.0.0.zip) | 不含 v2.1.0 更新 |
| **Linux / macOS** | [自行构建](docs/usage.md#构建发布包) | 已有构建脚本，本次未提供这些平台的成品 |

Windows EXE 双击启动，首次启动需等待运行库解压。显卡需支持 Vulkan 或 DirectX 12。本地 v2.1.0 的校验信息与验收记录见 [v2.1.0 交付说明](docs/release-v2.1.0-2026-11-10.md)。

## 从源码运行

推荐 **Python 3.12**。已有本地项目时，直接在项目根目录使用下列命令配置环境；已配置过则双击 `start.bat` 即可：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.\start.bat
```

如需另外获取 **v2.1.0** 源码，可下载 `preview` 源码 ZIP，或在新目录执行以下命令，再按上述步骤配置。`main` 和历史版本标签与 `preview` 分开维护。

```powershell
git clone --branch preview https://github.com/jarviscccwl/PointLabStudio.git
cd PointLabStudio
```

源码读取 LAZ / E57 需额外安装 `laspy lazrs pye57`；Windows 发布 EXE 已包含这些依赖。

```powershell
# 直接打开文件或目录
.venv\Scripts\python.exe run.py "D:\点云目录"

# 查看批处理命令行帮助
.venv\Scripts\python.exe -m apps.cli --help
```

[构建 Windows / Linux / macOS 发布包 →](docs/usage.md#构建发布包)

## 格式与使用边界

| 数据 | 支持格式 |
| :--- | :--- |
| 点云读取 | BIN、PLY、PCD、XYZ、PTS、LAS / LAZ、E57、NPY / NPZ、数值 TXT / CSV / TSV |
| 采集包 | PCAP / PCAPNG、MCAP、ROS1 BAG、ROS2 MCAP / SQLite 目录、DB3 |
| 图像与文本 | PNG、JPEG、BMP、TIFF、WebP、GIF；TXT、LOG、CSV、TSV、JSON、YAML |
| 标定导入 | JSON、ROS / OpenCV YAML、TXT / CSV 矩阵、TOML、NPY / NPZ，以及包含这些文件的 ZIP |
| 普通点云导出 | PLY、PCD、LAS、PTS、XYZ；采集包区间输出与标注见上表 |

- **PCAP 协议**：目前支持 RoboSense EM4 非压缩数据；其他厂商协议、EM4 压缩/分片数据尚未支持。
- **标定与同步**：融合需要实际相机内参、明确的雷达→相机外参和正确时间关系。支持导入与手工编辑，不会自动求解标定；缺失外参不会当作单位矩阵。当前没有 IMU 运动补偿。
- **目标关联**：依据局部几何产生建议，不自动识别类别。遮挡、相似目标、稀疏点及较大运动可能导致错误，需逐帧检查；不支持跨长时间遮挡重识别。
- **大数据与预览**：首次索引需要扫描。部分大点云可能采用采样加载，此时普通场景处理与导出只包含已加载点，请留意提示。融合图像预览最多投影 50,000 点，这个上限不限制点云数据导出。
- **动画与工作区**：MP4 使用 `mp4v`，GIF 有 256 色及 10 ms 时间精度限制，动画按设定帧率播放。移动或分享工作区时，需一起保留源数据、`.pls` 和已有的 `.assets`。

更多消息编码、点字段和格式限制见[使用手册](docs/usage.md#文件格式与使用限制)。

## 文档、更新与反馈

- [使用手册](docs/usage.md)：当前源码的完整流程、快捷键与排错入口。
- [v2.1.0 交付说明](docs/release-v2.1.0-2026-11-10.md)：本地 EXE、源码分支、校验与发布验证。
- [整体使用与界面复核](docs/workspace-polish-2026-11-10.md)：小窗口布局、标定选择、导出画面与真实数据验收。
- [文件命名、类别标签与逐帧显示](docs/export-labels-2026-11-10.md)：2026-11-10 源码更新及对应验证记录。
- [标定、区间导出与连续浏览](docs/fusion-sequence-2026-11-10.md)：此前实现与性能测量；其中目录和清单导出方式已由后续命名导出更新，当前操作以使用手册为准。
- [v2.0.0 发行说明](https://github.com/jarviscccwl/PointLabStudio/releases/tag/v2.0.0)：2026-11-09 发布版、成品与校验信息。
- [文档目录](docs/README.md)：功能说明、历史版本与各轮验证记录。
- [提交问题](https://github.com/jarviscccwl/PointLabStudio/issues)：请附使用的是 EXE 还是源码、版本、系统、复现步骤及日志中的错误信息。启动日志位于配置目录的 `logs/pointlab.log`，Windows 默认配置目录为 `%APPDATA%\PointLabStudio\`。
