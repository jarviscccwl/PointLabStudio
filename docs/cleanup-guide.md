# 维护与清理指南

最近整理：2026-11-10（v2.1.0 跨平台发布）。源码以 main 为准，Windows、macOS 和 Ubuntu 两种架构的四个安装包集中在同一个 v2.1.0 Release。本文说明保留范围、可清理内容和维护方式。

## 版本库跟踪范围

提交进 Git 的只有源代码、测试、打包脚本、运行资源与文档：

```text
apps/ core/ ui/ tests/ resources/ packaging/
docs/ requirements*.txt pyproject.toml run.py start.bat README.md .gitignore .dockerignore
```

新增文件前先确认它属于源码还是过程产物；采集数据、验收截图、导出动画一律不进版本库。

## 过程文件与需要保留的内容

以下过程目录可以在对应程序或构建任务退出后清理。被 Git 忽略并不代表文件没有价值，不要批量删除所有忽略文件：

| 路径 | 说明 |
| --- | --- |
| `build/` | PyInstaller 中间产物（工作目录、payload、缓存） |
| `.tmp/` | 测试缓存与临时工作区，含 `pygfx/` 字体缓存 |
| `__pycache__/`、`*.pyc` | 字节码缓存 |
| `.pytest_cache/`、`pytest-of-*/` | pytest 运行产物与临时目录 |

需要保留：

- `dist/`：当前四个安装包及校验清单，是交付成品。
- `.venv/`：源码运行依赖；只有确定要重建环境时才删除。
- `.git/`、源码、资源、打包脚本、测试与文档。
- 自己保存的 `*.pls` 和同名 `*.assets/`：工作区和处理结果，属于用户数据，不能作为缓存删除。
- 原始采集包、点云、图像、内外参和导出结果。

仅删除确认不再需要的旧安装包。GitHub 保留历史 Release、历史标签和 preview 分支；本次不合并 preview，只将独立 macOS v2.1.0 预发布整理进统一 Release。

### 本机待手动清理

此前自动审批拒绝了以下删除操作，仅返回 `blocked by policy`，未提供具体原因。本轮没有换方法重试；这些文件不参与当前成品运行，可以手动删除：

```text
C:\Users\lenovo\Desktop\pointlabstudio\build\ubuntu-release
C:\Users\lenovo\Desktop\pointlabstudio\dist\PointLabStudio-v2.0.0-Windows-x64.exe
C:\Users\lenovo\Desktop\pointlabstudio\dist\PointLabStudio-v2.0.0-Windows-x64.exe.sha256
C:\Users\lenovo\Desktop\pointlabstudio\label-qa.gif
```

`build/ubuntu-release` 是上一轮约 1.22 GiB 的依赖下载及过程目录，本轮复用其中已校验的依赖缓存；不影响 `dist` 中的程序。历史验收目录的清理边界保留在各轮交付说明中，不要删除其父级聊天目录或原始数据。

本轮下载副本与分段缓存也已尝试清理，但自动审批在命令执行前拒绝，仅返回 `blocked by policy`。未重试或改用其他方式删除。该目录共 117 个文件、1,134,691,467 字节（约 1.06 GiB），成品已另存到 `dist` 并核对哈希，可以手动删除整个目录：

```text
C:\Users\lenovo\.codex\visualizations\2026\11\08\01a1b9ba-c0ef-74a2-aab5-1854d236c5cc\release-v2.1.0-main-sync\downloads
```

其父目录中的小型验证记录、原始 Release 元数据和同步前源码恢复副本可保留复查；它们不参与软件运行。

### 本轮已完成的清理

本轮 WSL 临时构建目录 `/var/tmp/pointlabstudio-2.1.0-main-sync` 已删除，释放约 2.66 GiB；10 个本轮构建及验证镜像和无用中间层已移除，测试容器及临时依赖下载服务已停止。先归档了测试记录和截图，再清理构建现场。保留 WSL、Podman、QEMU 等工具，便于以后重新构建。

本地 v2.1.0 的四个单文件 `.sha256` 已合并为 `dist/SHA256SUMS.txt`，Release 同样只保留四个安装包和一个校验清单。重复的 `v2.1.0-macos` 预发布及同名标签已删除，DMG 的大小和 SHA-256 与原文件一致；历史版本和 preview 保留。

## 重建开发环境

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.\start.bat
```

打包机还需要构建依赖：

```powershell
uv pip install --python .venv\Scripts\python.exe -r requirements-build.txt
```

## 运行测试

`tests/` 中的 Qt/GPU 用例必须**按文件分进程**运行，整体跑一个 `pytest tests` 进程会因窗口资源互相干扰而挂起。所有用例都需要把 pygfx 缓存指向项目内目录，否则字体会写入用户配置目录并可能报 `PermissionError`：

```powershell
$env:PYGFX_DATA_DIR = "$PWD\.tmp\pygfx"
New-Item -ItemType Directory -Path $env:PYGFX_DATA_DIR -Force | Out-Null
$env:PYTHONDONTWRITEBYTECODE = "1"
foreach ($f in Get-ChildItem tests\test_*.py) {
  .venv\Scripts\python.exe -B -m pytest $f -q -p no:cacheprovider
}
```

## 打包 Windows 成品

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build.ps1
```

脚本先用 `packaging\PointLabStudio.spec` 生成 PyInstaller payload，再用 .NET 4 的 `csc.exe` 编译 `packaging\Launcher.cs`，输出 `dist\PointLabStudio-v<版本>-Windows-x64.exe` 及版本信息。版本号唯一来源是 `core\__init__.py` 的 `__version__`。首次全量构建约 6 分钟。

构建完成后记录校验值：

```powershell
Get-FileHash dist\PointLabStudio-v2.1.0-Windows-x64.exe -Algorithm SHA256
```

## 发布新版本

1. 修改 `core\__init__.py` 的 `__version__`，同步 `pyproject.toml`。
2. 更新 `README.md` 的版本徽标、下载表格与 `docs/README.md` 的版本说明。
3. 提交并推送 `main`，为同一提交打标签并推送：`git tag v<版本>`、`git push origin v<版本>`。
4. 在 GitHub 同名 Release 上传 EXE、DMG 和两个 DEB，附统一 `SHA256SUMS.txt`；不要把二进制提交进 Git 历史。
5. 逐个核对远端文件大小和 SHA-256。更新既有版本时先核对源标签，避免覆盖其他人刚提交的内容；相同哈希的已有成品无需重复上传。
6. 先验证统一 Release 中的新下载，再清理重复预发布。历史版本及 preview 除非另有明确要求应保留。

Ubuntu 的构建、安装和卸载见[使用手册](usage.md#ubuntu-安装与运行)。本次四个安装包的来源、验证和整理结果见[统一发行记录](release-v2.1.0-platforms-2026-11-10.md)。
