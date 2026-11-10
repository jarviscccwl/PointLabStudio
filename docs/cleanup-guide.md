# 维护与清理指南

最近整理：2026-11-10（v2.1.0 发布）。本文说明版本库里保留什么、哪些目录可以安全删除，以及如何重建环境、打包和发版。

## 版本库跟踪范围

提交进 Git 的只有源代码、测试、打包脚本、运行资源与文档：

```text
apps/ core/ ui/ tests/ resources/ packaging/
docs/ requirements*.txt pyproject.toml run.py start.bat README.md .gitignore .dockerignore
```

新增文件前先确认它属于源码还是过程产物；采集数据、验收截图、导出动画一律不进版本库。

## 可以安全删除的内容

下列目录与文件均被 `.gitignore` 忽略，删除后不影响源码，重新运行或重新构建时会自动生成：

| 路径 | 说明 |
| --- | --- |
| `.venv/` | 本地 Python 3.12 环境，按下方命令重建 |
| `build/` | PyInstaller 中间产物（工作目录、payload、缓存） |
| `dist/` | 发布成品；只保留当前版本的 EXE 与 `.sha256` |
| `.tmp/` | 测试缓存与临时工作区，含 `pygfx/` 字体缓存 |
| `__pycache__/`、`*.pyc` | 字节码缓存 |
| `.pytest_cache/`、`pytest-of-*/` | pytest 运行产物与临时目录 |
| `*.pls`、`*.assets/` | 工作区文件与动画资源，属于用户数据 |

删除命令示例（在项目根目录执行）：

```powershell
Remove-Item -Recurse -Force build, .tmp, .pytest_cache, pytest-of-* -ErrorAction SilentlyContinue
Get-ChildItem -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
```

`dist/` 里的历史版本（如 v2.0.0）可在确认不再需要时手动删除，只保留当前版本。

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
foreach ($f in Get-ChildItem tests\test_*.py) {
  .venv\Scripts\python.exe -m pytest $f -q -p no:cacheprovider
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
4. 在 GitHub 新建同名 Release，上传 `dist` 中的 EXE 与 `.sha256`。
5. 下载 Release 资产复核文件大小、SHA-256 与 `FileVersion`。
