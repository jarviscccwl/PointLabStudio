# 文件整理与手动清理指南

盘点日期：2026-11-09。项目目录为 `C:\Users\lenovo\Desktop\pointlabstudio`。源码、测试、资源、说明已归入对应目录；`dist` 仅保留最新融合版 v2.0.0 EXE 及同名 `.sha256`。旧版 v1.3.0 EXE 已删除，旧 v2.0.0 已被验证后的新成品替换。截图、解码报告和诊断副本位于项目外的聊天可视化目录。

此前删除过程目录、缓存、诊断副本及验收专用配置的操作被自动审批拒绝，返回 `blocked by policy`，没有提供更具体原因。未重试这些删除，也未换用其他方式绕过。本轮复查时，项目根目录下 `build` 和旧 `.tmp` 均不存在；新成品的构建和验收在项目外进行。

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
| `dist\PointLabStudio-v2.0.0-Windows-x64.exe` 及同名 `.sha256` | 最新融合版交付成品及文件校验值 |
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
