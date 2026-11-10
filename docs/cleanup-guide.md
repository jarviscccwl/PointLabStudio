# 文件整理与手动清理指南

最近盘点日期：2026-11-10。项目目录为 `C:\Users\lenovo\Desktop\pointlabstudio`。源码、测试、资源、说明已归入对应目录；当前成品为 `dist/PointLabStudio-v2.1.0-Windows-x64.exe` 及同名 `.sha256`，已包含标定、命名导出、标签和界面更新。旧 v2.0.0 EXE 的自动删除被拒绝，仍保留；v1.3.0 EXE 已在更早阶段删除。截图、解码报告和诊断副本位于项目外的聊天可视化目录。

此前删除过程目录、缓存、诊断副本及验收专用配置的操作被自动审批拒绝，返回 `blocked by policy`，没有提供更具体原因。未重试这些删除，也未换用其他方式绕过。本轮复查时，项目根目录下 `build` 和旧 `.tmp` 均不存在；此前成品构建与本轮验收均在项目外进行。

## v2.1.0 打包与源码交付（2026-11-10）

新成品已放入项目 `dist` 并核对校验值。源码上传目标为 GitHub `preview`，不上传 EXE、缓存、数据包或本机验收文件。详细构建、测试与校验信息见 [v2.1.0 交付说明](release-v2.1.0-2026-11-10.md)。

自动审批拒绝了两项清理操作：删除旧成品，以及删除本轮过程目录。均在命令执行前仅返回 `blocked by policy`，没有更具体原因；未重试或换方式执行。以下旧成品可以手动删除，保留同目录 **v2.1.0** 文件：

```text
C:\Users\lenovo\Desktop\pointlabstudio\dist\PointLabStudio-v2.0.0-Windows-x64.exe
C:\Users\lenovo\Desktop\pointlabstudio\dist\PointLabStudio-v2.0.0-Windows-x64.exe.sha256
```

本轮所有验收进程已退出，启动器创建的运行目录已自行清空。以下目录均位于本轮专用根目录内，不参与 `dist` 成品运行，共约 **692.35 MiB**，可以手动删除：

```text
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\release-v2.1.0\build
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\release-v2.1.0\staged
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\release-v2.1.0\exe-settings
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\release-v2.1.0\exe-settings-visible
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\release-v2.1.0\exe-temp
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\release-v2.1.0\test-settings
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\release-v2.1.0\exports
```

`build` 约 471.07 MiB，`staged` 约 220.31 MiB，其余为独立设置与两份测试动画。其父目录 `release-v2.1.0` 中的日志、验收工作区、源码传输副本、截图及脚本均只用于复查；不需要验证证据时，也可手动删除整个 **release-v2.1.0** 子目录。不要删除其父级目录、桌面标定 ZIP 或 D 盘原始数据。项目根目录的旧 `label-qa.gif` 仍可按本文末尾说明删除。

## 本次整体使用与界面复核（2026-11-10，打包前）

本轮新增代码、测试和文档已放入对应目录，没有新增根目录过程脚本、EXE 或构建目录。原生窗口验收使用独立临时配置，退出后已自动清理。测试禁用字节码和测试缓存写入；此前被拒绝的旧缓存与测试 GIF 未重试，仍可按下文手动删除。

本轮截图、验证脚本、运行日志和检查报告集中在以下目录，不参与软件运行；确认不需要复查证据后，可以手动删除整个这个子目录：

```text
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\workspace-polish
```

本轮读取 D 盘采集包与桌面标定 ZIP，未修改原文件；验收使用的 `export-labels\label-workspace.pls` 也未保存改动。项目根目录仍保留上一轮的 `label-qa.gif`，它是可删除的测试动画；此前归档它的操作与配置清理一起被自动审批拒绝，具体路径见本文末尾。

## 历史 v2.0.0 preview 版打包：可手动清理

最新 EXE 已交付到项目 `dist` 并核对 SHA-256；验收主程序与启动子进程均已退出。运行日志已另存为 `final-exe-ui.log`。以下四个过程目录不参与交付 EXE 运行，共约 **689.72 MiB**，可以手动删除：

| 绝对路径 | 内容 | 大小 |
| --- | --- | ---: |
| `C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\release-v2.0.0-preview\build` | 构建中间文件及原生载荷 | 468.61 MiB |
| `C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\release-v2.0.0-preview\staged` | 已交付成品的验收副本 | 219.18 MiB |
| `C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\release-v2.0.0-preview\exe-settings` | 验收专用设置和日志 | 0.04 MiB |
| `C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\release-v2.0.0-preview\exports` | 已核对的当前帧及三帧批量提取副本 | 1.90 MiB |

已核对这些目录的绝对路径和全部子项，均位于本轮验收目录内且没有重解析点。自动审批在命令启动前拒绝了删除，仅返回 `blocked by policy`，未给出更具体原因；因此尚未删除，未重试或换用其他方法绕过。没有重试此前被拒绝的旧目录。

上一级 `release-v2.0.0-preview` 的其他文件是构建、启动、数据核对和 Git 交付记录，可保留复查；不再需要证据时，也可删除整个该目录。其测试工作区只引用 D 盘采集包，删除测试工作区不删除原始数据。正式交付说明见 [v2.0.0 preview 记录](release-v2.0.0-preview-2026-11-09.md)。

## 框内提取与性能优化的源码验证

源码、测试和使用说明已归入项目对应目录。此阶段未生成 EXE；后续打包记录见上节。源码验证的过程文件全部位于：

`C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\box-extraction-performance`

测试窗口已正常退出。该目录共有 **52 个文件、2,361,566 字节（约 2.25 MiB）**，包含临时配置、测试提取的 PCD、诊断脚本以及截图和验证记录，不参与软件运行。**不再需要复查证据时，可手动删除整个 `box-extraction-performance` 目录**；它不包含项目源码、正式 EXE 或 D 盘原始数据。

本轮已先核对删除目标均在此目录内且没有重解析点，再尝试清理临时设置、提取副本和诊断脚本；自动审批在命令启动前拒绝了删除，返回 `blocked by policy`，没有提供更具体原因。因此本轮这些过程文件均尚未删除，也未重试或换方式绕过。

如果希望保留证据，只需留下 `baseline.interactions.json`、`final.interactions.json`、`startup.comparison.json`、`readers.comparison.json`、`native-state.json`、`native-output-verification.json`、`native-extraction.png` 和 `final-regression.log`；其余本轮内容均可手动删除。正式结论已经保存在 [框内提取与性能记录](box-extraction-performance-2026-11-09.md)。以下历史清单继续保留，未重新尝试删除此前被拒绝的项目。

## 本轮融合版打包：可优先手动清理

最新 EXE 已复制到项目 `dist` 并核对 SHA-256，验收程序及启动子进程均已退出，运行日志已另存为下列目录的上一级 `final-exe-ui.log`。以下三个过程目录不参与交付 EXE 运行，共约 **687.79 MiB**，可以手动删除：

| 绝对路径 | 内容 | 文件数 | 大小 |
| --- | --- | ---: | ---: |
| `C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\release-v2.0.0-fusion\build` | 构建中间文件及原生载荷副本 | 18 | 468.58 MiB |
| `C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\release-v2.0.0-fusion\staged` | 已交付 EXE 的验收副本 | 1 | 219.17 MiB |
| `C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\release-v2.0.0-fusion\exe-settings` | 验收专用设置与日志 | 3 | 0.04 MiB |

已核对这些目录均在本轮验收目录内且不含重解析点。自动删除仍被审批拒绝，仅返回 `blocked by policy`，因此它们尚未删除；没有重试。此处配置不是实际软件的默认用户设置。

上一级 `release-v2.0.0-fusion` 中的其余文件为本轮截图、构建与回归日志、校验清单及验收记录，可保留用于复查；不再需要证据时也可删除整个该子目录。不要删除整个 `.codex` 或其他聊天目录。正式发布记录在项目的 [融合版交付说明](release-v2.0.0-fusion-2026-11-09.md)。

## 本轮融合功能的验证材料

本轮截图、已知标定样例、演示工作区、回归报告和性能记录集中保存在项目外：

`C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\fusion-workflow`

本轮已经删除一次性的测试收集清单 `collected-tests.txt`。其余验收证据不参与软件运行；不再需要复查时，可以删除整个 `fusion-workflow` 子目录。里面的 `real-capture.pls` 只引用 D 盘原始数据，删除演示工作区不会删除原始包。`known-calibration-sample` 是人工合成的投影验证样例，不能当作真实采集包的标定文件。

最终程序已正常退出。以下本轮临时配置的自动删除也被审批拒绝，仅返回 `blocked by policy`；未重试或改用其他方法删除。目录仍存在，共 3 个文件、39,962 字节（约 0.04 MiB），可手动删除：

`C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\fusion-workflow\ui-settings`

它是验收专用配置，不是实际软件的默认用户设置。最终运行日志已另存到上一级 `final-ui.log`。

## 上轮 v2.0.0 打包过程可优先清理

先关闭 PointLab Studio。以下仍存在的目录是最大的过程副本，约 **605 MiB**，不包含交付 EXE，也不参与其运行：

| 绝对路径 | 内容 | 文件数 | 大小 |
| --- | --- | ---: | ---: |
| `C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\release-v2.0.0\diagnostic-runtime` | 为检查依赖而解出的完整运行库副本 | 739 | 605.19 MiB |

本次以下 6 个目录也是验收专用配置，可删除，共 17 个文件、约 0.19 MiB；实际软件默认用户配置不在其中。最终验收日志已另存为上一级的 `final-ui.log`。

```text
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\release-v2.0.0\bundle-diagnostic-settings
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\release-v2.0.0\source-comparison-settings
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\release-v2.0.0\source-frozen-settings
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\release-v2.0.0\exe-settings
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\release-v2.0.0\exe-settings-fixed
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\release-v2.0.0\exe-settings-final
```

如果不再需要复查截图、日志、样例和演示标注，可以直接删除整个以下目录，一并清掉诊断脚本、旧输出和验收配置：

`C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\release-v2.0.0`

该目录的 `.pls` 是验收工作区，只引用 D 盘原始数据；删除验证目录不会删除原始包。正式交付说明已保存在项目的 `docs/release-v2.0.0-2026-11-09.md`。不要删除整个 `.codex` 或其他聊天的目录。

## 可以手动删除

先关闭 PointLab Studio。以下均为已核对的过程文件或可重新生成的缓存，不包含运行依赖、Git 历史或用户原始采集数据。容量约数按 1 MiB = 1,048,576 字节统计。

| 绝对路径 | 内容 | 文件数 | 大小 |
| --- | --- | ---: | ---: |
| `C:\Users\lenovo\Desktop\pointlabstudio\.pytest_cache` | 自动测试缓存 | 5 | 0.01 MiB |

旧清单中的 `.tmp\recording-work`、`.tmp\ui-refinement`、`.tmp\github-sync` 均已不存在。本次仅记录现状，不把它们计入剩余清理容量。

以下 14 个目录也可删除，当前合计 132 个 Python 字节码文件、约 2.72 MiB。再次运行程序时可能重新生成，这是正常现象。

```text
C:\Users\lenovo\Desktop\pointlabstudio\apps\__pycache__
C:\Users\lenovo\Desktop\pointlabstudio\core\__pycache__
C:\Users\lenovo\Desktop\pointlabstudio\core\algorithms\__pycache__
C:\Users\lenovo\Desktop\pointlabstudio\core\color\__pycache__
C:\Users\lenovo\Desktop\pointlabstudio\core\commands\__pycache__
C:\Users\lenovo\Desktop\pointlabstudio\core\geometry\__pycache__
C:\Users\lenovo\Desktop\pointlabstudio\core\io\__pycache__
C:\Users\lenovo\Desktop\pointlabstudio\core\model\__pycache__
C:\Users\lenovo\Desktop\pointlabstudio\core\pipeline\__pycache__
C:\Users\lenovo\Desktop\pointlabstudio\ui\__pycache__
C:\Users\lenovo\Desktop\pointlabstudio\ui\theme\__pycache__
C:\Users\lenovo\Desktop\pointlabstudio\ui\viewport\__pycache__
C:\Users\lenovo\Desktop\pointlabstudio\ui\widgets\__pycache__
C:\Users\lenovo\Desktop\pointlabstudio\tests\__pycache__
```

上述旧缓存合计约 2.72 MiB，清理仅用于目录整理，不会明显减少软件运行内存或提升速度。

## 保留

| 路径或内容 | 用途 |
| --- | --- |
| `.venv` | 当前源码运行所需 Python 和依赖 |
| `.git`、`.github` | 项目历史、恢复点和项目配置 |
| `apps`、`core`、`ui`、`resources` | 软件源码及资源，仅其中上列缓存目录可删 |
| `tests`、`docs`、`packaging` | 回归测试、使用说明、验证记录及构建脚本 |
| `run.py`、`start.bat`、依赖及项目配置文件 | 启动和维护软件 |
| `dist\PointLabStudio-v2.1.0-Windows-x64.exe` 及同名 `.sha256` | 当前交付成品及校验值；旧版 v2.0.0 可按上文删除 |
| 自己保存的 `.pls` 和同名 `.assets` | 工作区、标注和处理结果 |
| `D:\2026_10_14` | 用户原始采集数据，保持原样 |

不要为了清理缓存而删除整个 `core`、`ui`、`tests` 或 `.venv`，也不要将 `.assets` 当作缓存。

## 项目外的验证材料

本轮树冠关联修复的截图、验证报告、复现脚本及演示工作区位于：

`C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\tracking-repair`

该目录不参与软件运行。确认不再需要验证证据后，可以删除整个 `tracking-repair` 子目录；演示工作区只引用 D 盘数据，删除它不会删除原始采集包。正式使用说明和回归测试已放入项目的 `docs` 与 `tests`。

本轮删除一次性诊断脚本 `compare_sampling.py` 也被自动审批拒绝，只返回 `blocked by policy`，没有更具体原因；没有重试或换方式删除。以下两项可手动删除，其他截图、报告和复现脚本可按需保留：

```text
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\tracking-repair\compare_sampling.py
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\tracking-repair\verification-settings
```

`verification-settings` 是验收专用配置，不是实际软件的用户设置；最后一次验证后保留供手动清理。

上轮三维框、播放恢复和启动动效的最终截图、验证记录、验证脚本及演示工作区位于：

`C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\box-workflow`

其中以下三个临时配置目录可以直接删除，共 5 个文件、约 0.04 MiB；本轮自动清理被拒绝，尚未删除：

```text
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\box-workflow\scene-settings
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\box-workflow\startup-settings
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\box-workflow\verification-settings
```

其余材料为验收证据，不参与软件运行。确认不再需要截图、回归输出、演示标注和复现脚本后，可删除整个 `box-workflow` 子目录。正式启动素材已在项目 `resources` 中，删除外部验证目录不会影响程序。

上轮性能对照、验证脚本及演示工作区位于：

`C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\usability`

它也不参与程序运行。确认不再需要截图、性能证据和演示标注后，可删除这个 `usability` 子目录；不要据此删除整个 `.codex` 或其他聊天目录。演示 `.pls` 只引用原始采集数据，删除演示文件不会删除 D 盘原始数据。

## 手动内参修正（2026-11-09）

本轮源码、回归测试和使用说明已留在项目对应目录，没有新增打包输出。以下验收目录位于项目外，不参与软件运行：

```text
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\manual-calibration
```

本轮尝试删除以下项目时，自动审批返回 `blocked by policy`，整个清理命令未执行，也未换方法重试。软件验收进程已正常退出，可以手动删除该目录内的：

- `settings`：测试专用设置、日志和绘制缓存。
- `manual-edit-check.pls`：使用测试焦距的验收工作区，只引用 D 盘原始数据。
- `native-stdout.txt`、`native-stderr.txt`：首次测试启动输出。
- `regression.txt`：被最终 162 项检查覆盖的第一轮测试日志。

`regression-final.txt`、`verification.json`、`original-files.json` 及 4 张 PNG 是本轮验收证据；确认不需要留证后，也可删除整个 `manual-calibration` 目录。不要删除其父级目录、桌面的 `内外参标定.zip` 或 D 盘原始采集包。

## 区间导出与连续浏览（2026-11-10）

本轮源码、测试和说明已归入 `core`、`ui`、`tests`、`docs`，未产生新 EXE 或构建目录。项目根目录没有新增一次性脚本。原先被拒绝的缓存清理未重试。

验收进程已经正常退出。以下五项均是本轮生成的测试设置／导出文件，可以手动删除。自动清理在执行前被审批拒绝，仅返回 `blocked by policy`，没有更具体原因；整个命令没有执行，也未换方法重试：

```text
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\fusion-sequence\native-settings
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\fusion-sequence\real-gif
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\fusion-sequence\real-mp4
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\fusion-sequence\real-png_ply
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\fusion-sequence\PointLab_20261110_003056_991_671-673
```

同目录的 `verified-workspace.pls` 使用测试外参 X 平移 0.02 m，只供验收，亦可删除。`verify_real.py` 为复现脚本，其余 JSON／日志／截图为验证证据，均不参与软件运行；不需要留证时可删除整个 `fusion-sequence` 子目录，不要删除它的父级目录。上述工作区只引用 D 盘数据，删除不会删除原始采集包。

## 标签、文件命名与切帧修复（2026-11-10）

本轮没有新 EXE、构建目录或根目录临时脚本。源码、测试与说明已经归入对应目录，所有验收进程已退出。尝试归档测试 GIF 并删除本轮测试配置时，自动审批拒绝了整个命令，仅返回 `blocked by policy`，没有更具体原因；命令未执行，也未换方法重试。以下三项可以直接手动删除：

```text
C:\Users\lenovo\Desktop\pointlabstudio\label-qa.gif
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\export-labels\settings
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\export-labels\native-settings
```

`label-qa.gif` 是原生保存对话框验收生成的 3 帧动画，不是软件资源。`settings` 和 `native-settings` 是独立测试配置、日志与绘制缓存，不是用户日常使用的配置。

其余 `export-labels` 内容为本轮验收证据，不参与软件运行。其中 `outputs\我的同步预览.gif` 是另一项单文件导出检查，`label-workspace.pls` 和 `label-check.json` 是测试标注与工作区，均可删除。确认不需要保留验证日志和截图时，可以删除整个 `export-labels` 子目录；不要删除它的父目录、用户标定 ZIP 或 D 盘原始数据。此前被拒绝的清理项目未重试，仍按前文清单处理。
