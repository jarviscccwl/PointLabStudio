# v2.1.0 跨平台发行记录

日期：2026-11-10。本次从 GitHub 最新 `main`（`9ce844b`）同步源码，保留 Ubuntu 22.04 兼容性改动，重新构建 Ubuntu 两种架构的安装包。Windows 和 macOS 使用用户指定的已发布成品。未合并 preview 分支。

## 下载与安装

[v2.1.0 Release](https://github.com/jarviscccwl/PointLabStudio/releases/tag/v2.1.0) 统一提供三个系统、四个安装包；本地副本均位于项目 `dist/`。

| 平台 | 文件 | 安装方式 |
| --- | --- | --- |
| Windows 10 / 11 x64 | `PointLabStudio-v2.1.0-Windows-x64.exe` | 双击启动，无需安装 Python |
| macOS Apple Silicon | `PointLabStudio-v2.1.0-macOS-arm64.dmg` | 打开 DMG，将应用拖入「应用程序」 |
| Ubuntu amd64 / x86_64 | `PointLabStudio-v2.1.0-Ubuntu-amd64.deb` | `sudo apt install ./PointLabStudio-v2.1.0-Ubuntu-amd64.deb` |
| Ubuntu arm64 / aarch64 | `PointLabStudio-v2.1.0-Ubuntu-arm64.deb` | `sudo apt install ./PointLabStudio-v2.1.0-Ubuntu-arm64.deb` |

统一校验文件为 [SHA256SUMS.txt](https://github.com/jarviscccwl/PointLabStudio/releases/download/v2.1.0/SHA256SUMS.txt)。在 Ubuntu 下载目录中执行 `sha256sum --ignore-missing -c SHA256SUMS.txt`，macOS 可使用 `shasum -a 256 文件名.dmg`，Windows 使用 `Get-FileHash 文件名.exe -Algorithm SHA256` 对照清单。

Windows 需要 Vulkan 或 DirectX 12 驱动；Ubuntu 需要 Vulkan 驱动及可用的软件源。Ubuntu 兼容基线为 22.04 / glibc 2.35，验证范围为 22.04、24.04，不支持 20.04、32 位 x86 或 ARM32。其他 Ubuntu 版本和衍生发行版未验证。完整安装、升级、卸载与配置路径见 [使用手册](usage.md#安装与运行)。

## 各平台的构建来源

| 安装包 | 来源与边界 |
| --- | --- |
| Windows x64 | 下载原 v2.1.0 Release 的最新 EXE，构建基线 `9ce844b`。文件大小 229,882,368 字节，SHA-256 为 `8f833affcd70e272aa9fd5eb6588a7b42b37a7e192eca1e645f603f7381272fd`。本轮不重复构建 Windows 程序。 |
| macOS ARM64 | 下载原独立 macOS 预发布的 DMG。原发布说明列出的上游基线为 `e7a32f9`，大小 161,514,229 字节，SHA-256 为 `7db8b9e1e2db3e1839ab091e422e283e01e0e29b329a5726b17d4592213ed652`。沿用原构建及其 macOS 适配，不声称由本轮最新 main 重新构建，也不包含 main 后续所有界面改动。 |
| Ubuntu amd64 / arm64 | 使用本轮同步后的应用源码和 Linux 打包配置，分别在对应架构的 Ubuntu 22.04 环境构建；ARM64 用户空间由 QEMU 执行，未将 x86 二进制更换架构标签。 |

本轮应用源码与 `9ce844b` 一致，新增提交包含 Linux 打包、测试、忽略规则和发行文档。最终源码提交发布到 main，并作为统一 v2.1.0 标签的源码快照；Windows 的原始构建记录见 [Windows 交付说明](release-v2.1.0-2026-11-10.md)。

macOS 原发布说明记录了 Apple Silicon、macOS 27.2 的独立启动、点云加载和 Retina 2× Metal 渲染，以及 81 项相关测试通过、2 项跳过。本轮在 Windows 上完成下载和哈希校验，没有进行新的 Mac 实机测试。原构建使用临时签名、未经过 Apple 公证，首次启动可能需要在系统「隐私与安全性」中允许。

## 本轮验证

- Windows 源码回归按文件分进程运行：48 个测试模块全部通过，**733 项测试、235 项子测试通过，18 项跳过**。
- Linux amd64 源码回归：48 个测试模块全部通过，**745 项测试、237 项子测试通过，6 项跳过**。测试环境安装软件 Vulkan 驱动后复查图形相关用例通过。
- Ubuntu amd64 成品已在干净的 22.04 和 24.04 环境安装，普通用户打开中文及空格路径中的 9,600 点点云，确认实际绘制、旋转、正常关闭，再卸载并检查个人设置和输入点云保留。
- ARM64 重点回归覆盖打包、点云读写、PCAP、采集包、标定和区间导出：8 个模块全部通过，**154 项测试、111 项子测试通过**。两个架构的成品均已在 Ubuntu 22.04 和 24.04 完成实际点云绘制、旋转、正常退出及卸载验证；ARM64 使用 QEMU，尚未进行物理 ARM 电脑测试。

Linux 包内包含 Python 3.12.12、Qt 6.7.3 和主要处理依赖。构建逐个检查 ELF 架构及 glibc 要求，拒绝架构混用和高于 glibc 2.35 的依赖。包内排除旧 C++ 运行库，通过 APT 使用系统 `libstdc++6` / `libgcc-s1`，兼容较新系统的显卡运行库。容器中的图形验证使用 X11/Xvfb 和 Mesa 软件 Vulkan，不能据此评价物理设备的性能或设备专用驱动。

## 源码与发布整理

源码直接发布到 main，未合并或删除 preview。保留 v1.x、v2.0.0 的历史 Release 与标签。统一 v2.1.0 中的 macOS 安装包已完成远端大小及哈希核对，随后删除了重复的 `v2.1.0-macos` 预发布及同名标签。Release 保留四个安装包和统一 `SHA256SUMS.txt`，本地当前版的重复单文件校验清单也已清理。二进制只作为 Release 附件，不进入 Git 历史。

README、使用手册和文档目录已统一指向四个安装包。原始采集包、用户配置及工作区不作为冗余清理；待手动删除的旧过程文件见 [维护与清理指南](cleanup-guide.md)。本轮构建、测试、下载和远端校验记录位于项目外的 `release-v2.1.0-main-sync` 验证目录，不参与程序运行。

四个安装包及统一 `SHA256SUMS.txt` 已上传并核对远端大小和 SHA-256，与本地 `dist` 一致。本轮 WSL 临时构建目录（约 2.66 GiB）、10 个构建及验证镜像和无用中间层已清理，测试容器与临时下载服务已停止。Windows 下载缓存的自动删除被拒绝，准确路径和大小已列入清理指南。
