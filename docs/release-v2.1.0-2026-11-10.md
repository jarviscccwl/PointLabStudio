# v2.1.0 交付说明

本文保留 Windows 成品的原始构建与验证记录，构建基线为 `9ce844b`。当前 Windows、macOS 和 Ubuntu 两种架构的统一下载、构建来源与验证范围见 [跨平台发行记录](release-v2.1.0-platforms-2026-11-10.md)。

日期：2026-11-10。v2.1.0 包含内外参完整编辑、融合与区间导出、目标类别、逐帧显示、工作台体验整理，以及本轮的小屏幕自适应修正。日常操作见[使用手册](usage.md)。

## 下载

- Release：<https://github.com/jarviscccwl/PointLabStudio/releases/tag/v2.1.0>
- 程序：<https://github.com/jarviscccwl/PointLabStudio/releases/download/v2.1.0/PointLabStudio-v2.1.0-Windows-x64.exe>
- 源码：标签 [`v2.1.0`](https://github.com/jarviscccwl/PointLabStudio/tree/v2.1.0) · [源码 ZIP](https://github.com/jarviscccwl/PointLabStudio/archive/refs/tags/v2.1.0.zip)

`PointLabStudio-v2.1.0-Windows-x64.exe` 是 Windows 10 / 11 x64 单文件程序，无需安装 Python。首次启动需要解出运行库；退出后启动器清理本次运行目录，用户设置保存在 `%APPDATA%/PointLabStudio`。应用版本与 Windows 文件、产品版本均为 `2.1.0`。

- 文件大小：229,882,368 字节，约 219.23 MiB。
- SHA-256：`8f833affcd70e272aa9fd5eb6588a7b42b37a7e192eca1e645f603f7381272fd`。
- 校验文件：统一 Release 中的 `SHA256SUMS.txt`；原独立 EXE 校验文件已整合到该清单。

## 本版内容

- **标定与融合**：分别选择、导入和手动修改内参及外参；支持 JSON、YAML、TXT/CSV、TOML、NPY/NPZ 和 ZIP。可查看点云投影、图像赋色、去畸变及图像中的 3D 框。
- **区间与导出**：滑动选择起止帧，导出当前帧、选区或全部已导入帧；支持同步双窗格 MP4/GIF、PNG、PLY/PCD，自定义文件名，只保存所选格式。多帧输出直接保存到指定位置，不另建结果文件夹或附加清单。
- **目标标注**：为 3D 框设置目标名称与类别，前后帧关联后保留同一目标的身份，允许逐帧修正；支持当前框内点提取及有框帧批量导出。
- **连续浏览**：提前准备邻近帧，切帧保留视角；清除旧屏幕选点高亮，当前框内高亮从当前帧重新计算。后台预加载期间可以继续调整当前框，批量提取命名时暂停新的预加载。
- **界面整理**：小窗口下合并数据流选择行，自动收起次要说明，给预览和三视图保留空间；标定窗口可滚动，操作按钮保持可见；图像边缘标签向内排布，导出仅列出可用画面。

原始采集包不改写。首次索引和持续播放速度仍取决于文件、磁盘与显卡；跨帧关联使用几何匹配，应逐帧核对结果，不替代人工验收。功能边界与格式限制见 [README](../README.md#格式与使用边界)。

## 小屏幕自适应核查

本轮逐项核对了适配代码，并修正了检查中发现的回归：

- **顶部工具区**（`ui/widgets/responsive_header.py`）：窗口宽度不足以并排显示时，工具组自动换到第二行，宽度恢复后变回单行。
- **跟踪对话框**（`ui/widgets/box_panel.py` 的 `ObjectTrackingDialog._fit_host_layout()`）：按可用高度自动收起次要状态栏、把参数区切到紧凑模式，高度继续降低时把「参数」面板浮动成独立窗口，始终为预览保留至少 48 px。核查发现状态栏隐藏后内联预算少算一个状态栏高度，导致 1024×670 时参数区被误判为可内联、无法浮动；该判断现已把状态栏高度保留在内联预算内，并按「隐藏状态栏不能把参数区在内联与浮动之间来回翻转」加了说明。
- **主窗口初始尺寸**（`apps/desktop.py`）：新增 `fit_window_to_screen()`，启动时按屏幕可用区域收敛窗口尺寸与位置，并同步下调最小尺寸。此前 `setMinimumSize(980, 620)` 与 `resize(1280, 820)`（`ui/main_window.py:274-275`）在 1366×768 屏幕上会超出可用高度。
- 高 DPI 由 Qt 6 默认策略处理；启动画面（`ui/startup.py:68`）按 `devicePixelRatioF()` 渲染，缩放屏上不会模糊。

## 发布验证

完整测试按文件拆为独立进程串行运行，避免多个 Qt/GPU 窗口互相影响：**48 个测试文件、730 项测试、234 项子测试通过**，最终确认运行耗时 386.2 秒（前一轮 357.8 秒）；16 项因当前 Windows 账号缺少符号链接权限或未启用系统回收站集成检查而跳过。

| 检查 | 结果 |
| --- | --- |
| 全量构建 | PyInstaller 与 Windows 启动器成功，退出码 0；约 2 分钟 |
| 版本信息 | EXE 的 FileVersion / ProductVersion 均为 `2.1.0`，与 `core/__init__.py`、`pyproject.toml` 一致 |
| 静态检查 | `ruff check --select F` 全部通过（未使用导入、未使用局部变量已清理） |
| 启动冒烟 | 以 `QT_QPA_PLATFORM=offscreen` 启动成品，进程持续运行 40 秒未退出、未出现崩溃对话框 |
| SHA-256 | 构建输出与上传资产一致，见上节 |

## 文件整理

- 删除临时目录与备份：`.tmp/recording-work`、`.tmp/v210-check`、`.tmp/box_panel_work.py`，以及项目根目录的 `pytest-of-*` 运行目录。
- 清理 20 处冗余代码：14 处未使用导入（`core/io/lidar_import.py`、`ui/main_window.py`、`ui/widgets/recording_browser.py`、`ui/widgets/recording_chrome.py` 及多个测试文件）和 6 处未使用局部变量（`core/io/pcap_reader.py`、`ui/viewport/viewport.py` 及测试文件）。
- 删除整条已不可达的「雷达包转换」界面旧链：`ui/widgets/lidar_import_dialog.py`（134 行）从未被实例化，`ui/main_window.py` 中对应的导入线程、5 个回调/辅助方法与 `_import_thread` 字段一并移除；菜单「打开采集包」仍由 `_open_recording_sources` 提供现有采集包浏览能力，`core/io/lidar_import.py` 的转换 API 及其测试保留。
- 渲染对象创建失败时的 `print` 改为 `logging.exception`，与其他绘制日志一致。
- 修正一个与产品逻辑无关的偶发用例：`tests/test_recording_sources.py` 中图像序列缓存令牌的断言此前依赖 Windows 延迟刷新的目录时间戳，现改为直接驱动令牌所读取的目录 stat，重复运行稳定通过。
- 工作区此前出现的整树换行差异（约 2.3 万行）已确认为 CRLF 噪音，统一规范为 LF 后，实际改动仅剩本轮修复涉及的文件。
- 维护、清理与发版步骤见[维护与清理指南](cleanup-guide.md)。

## 源码交付

源码提交与 `v2.1.0` 标签指向同一提交，`main` 与 `preview` 分支保持同步。交付内容包含程序、资源、测试、构建配置与文档；不包含 EXE、`dist`、虚拟环境、原始采集数据及本机验收导出。
