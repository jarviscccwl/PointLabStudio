"""Freeze on the target architecture; build Ubuntu DEBs or a macOS DMG."""
import argparse
import hashlib
import os
import platform
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import __version__


DEB_ARCHITECTURES = {"x86_64": ("amd64", 62), "arm64": ("arm64", 183)}
LINUX_DEPENDS = (
    "libc6 (>= 2.35), libstdc++6 (>= 12), libgcc-s1, libglib2.0-0, "
    "libgl1, libegl1, libvulkan1, libgomp1, libgfortran5, libusb-1.0-0, "
    "libdbus-1-3, libfontconfig1, libfreetype6, libx11-6, libx11-xcb1, "
    "libxext6, libxrender1, libxi6, libsm6, libice6, "
    "libxkbcommon0, libxkbcommon-x11-0, libxcb1, libxcb-cursor0, "
    "libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0, "
    "libxcb-render-util0, libxcb-shape0, libxcb-shm0, libxcb-sync1, "
    "libxcb-xfixes0, libxcb-xinerama0, libxcb-xkb1"
)


def launcher_script(executable=Path("/opt/pointlabstudio/PointLabStudio")):
    """Exec preserves signals, working directory, and file names with spaces."""
    return f'#!/bin/sh\nexec {shlex.quote(str(executable))} "$@"\n'


def write_checksum(artifact):
    digest = hashlib.sha256()
    with artifact.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    artifact.with_suffix(artifact.suffix + ".sha256").write_text(
        f"{digest.hexdigest()}  {artifact.name}\n", encoding="ascii")


def check_glibc_versions(version_info, path):
    versions = [tuple(map(int, match)) for match in
                re.findall(r"\bGLIBC_(\d+)\.(\d+)", version_info)]
    if any(version > (2, 35) for version in versions) or "GLIBC_ABI_DT_RELR" in version_info:
        raise ValueError(f"Runtime requires a newer system than Ubuntu 22.04: {path}. "
                         "Build with packaging/Dockerfile.linux on the target architecture.")


def validate_linux_payload(payload, architecture):
    """Do not publish a package with Windows or mismatched native binaries."""
    expected = DEB_ARCHITECTURES[architecture][1]
    executable = payload / "PointLabStudio"
    with executable.open("rb") as stream:
        header = stream.read(20)
    if len(header) < 20 or header[:6] != b"\x7fELF\x02\x01":
        raise ValueError(f"Expected a 64-bit little-endian Linux ELF executable: {executable}")
    elf_files = []
    for path in payload.rglob("*"):
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            header = stream.read(20)
        if header[:4] == b"\x7fELF":
            if (header[:6] != b"\x7fELF\x02\x01" or
                    int.from_bytes(header[18:20], "little") != expected):
                raise ValueError(f"ELF architecture does not match {architecture}: {path}")
            if not path.is_symlink():
                elf_files.append(path)
    for path in elf_files:
        version_info = subprocess.check_output(["readelf", "--version-info", "--wide", str(path)],
                                               text=True)
        check_glibc_versions(version_info, path)


def build_deb(payload, architecture, work, dist):
    validate_linux_payload(payload, architecture)
    deb_arch = DEB_ARCHITECTURES[architecture][0]
    work.mkdir(parents=True, exist_ok=True)
    dist.mkdir(parents=True, exist_ok=True)
    artifact = dist / f"PointLabStudio-v{__version__}-Ubuntu-{deb_arch}.deb"
    with tempfile.TemporaryDirectory(prefix="deb-", dir=work) as staging:
        stage = Path(staging)
        stage.chmod(0o755)
        installed = stage / "opt" / "pointlabstudio"
        # Newer Mesa/NVIDIA drivers need the host C++ ABI. An older bundled
        # libstdc++ shadows it through PyInstaller's LD_LIBRARY_PATH.
        shutil.copytree(payload, installed, symlinks=True,
                        ignore=shutil.ignore_patterns("libstdc++.so*", "libgcc_s.so*"))
        files = {
            "usr/bin/pointlabstudio": launcher_script(),
            "usr/share/applications/pointlabstudio.desktop": (
                "[Desktop Entry]\nType=Application\nName=PointLabStudio\n"
                "Comment=Point cloud and multisensor data workspace\n"
                "Comment[zh_CN]=点云与多传感器数据工作台\n"
                "Exec=pointlabstudio %f\nTryExec=pointlabstudio\n"
                "Icon=pointlabstudio\nTerminal=false\n"
                "Categories=Graphics;Science;\nStartupWMClass=PointLabStudio\n"
            ),
            "usr/share/icons/hicolor/scalable/apps/pointlabstudio.svg":
                (ROOT / "resources" / "app.svg").read_text(encoding="utf-8"),
        }
        for relative, content in files.items():
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        # Bind-mounted Windows sources do not carry reliable POSIX modes.
        for path in stage.rglob("*"):
            if path.is_symlink():
                continue
            mode = 0o755 if path.is_dir() else 0o644
            if path.is_file():
                with path.open("rb") as stream:
                    header = stream.read(4)
                if header == b"\x7fELF" or header.startswith(b"#!"):
                    mode = 0o755
            path.chmod(mode)
        (installed / "PointLabStudio").chmod(0o755)
        (stage / "usr/bin/pointlabstudio").chmod(0o755)
        size = sum(p.lstat().st_size for p in stage.rglob("*") if p.is_file())
        control = stage / "DEBIAN"
        control.mkdir(mode=0o755)
        (control / "control").write_text(
            f"Package: pointlabstudio\nVersion: {__version__}\n"
            f"Architecture: {deb_arch}\nSection: graphics\nPriority: optional\n"
            "Maintainer: PointLabStudio <jarviscccwl@users.noreply.github.com>\n"
            "Homepage: https://github.com/jarviscccwl/PointLabStudio\n"
            f"Installed-Size: {(size + 1023) // 1024}\nDepends: {LINUX_DEPENDS}\n"
            "Recommends: fonts-noto-cjk, mesa-vulkan-drivers\n"
            "Description: Point cloud and multisensor data workspace\n"
            " Browse recordings, synchronize images and point clouds, calibrate,\n"
            " annotate 3D objects and export data or animations.\n",
            encoding="utf-8", newline="\n")
        (control / "control").chmod(0o644)
        # Keep the output outside the package tree to prevent self-inclusion.
        with tempfile.TemporaryDirectory(prefix="deb-output-", dir=work) as output:
            temporary = Path(output) / artifact.name
            subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "-z6",
                            "--build", str(stage), str(temporary)], check=True)
            shutil.copyfile(temporary, artifact.with_suffix(".deb.tmp"))
            artifact.with_suffix(".deb.tmp").replace(artifact)
    write_checksum(artifact)
    return artifact


def main(argv=None):
    machine = platform.machine().lower()
    architecture = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "x86_64"}.get(machine)
    if sys.platform not in ("darwin", "linux") or architecture is None:
        raise SystemExit("Run on macOS ARM64 or Ubuntu ARM64/x86_64.")
    if sys.platform == "darwin" and architecture != "arm64":
        raise SystemExit("Use an Apple Silicon Python interpreter.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("deb", "tar.gz"), default="deb",
                        help="Linux output format (default: deb)")
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--payload", type=Path, help="Package an already frozen Linux onedir")
    args = parser.parse_args(argv)
    work = (args.work_dir or ROOT / "build" / f"{sys.platform}-{architecture}").resolve()
    dist = args.dist_dir.resolve()
    dist.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("PYGFX_DATA_DIR", str(work / "pygfx"))
    Path(os.environ["PYGFX_DATA_DIR"]).mkdir(parents=True, exist_ok=True)
    if not args.payload:
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                        "--workpath", str(work / "work"), "--distpath", str(work / "payload"),
                        str(ROOT / "packaging" / "PointLabStudio-posix.spec")], check=True, cwd=ROOT)
    payload = args.payload or work / "payload" / "PointLabStudio"
    if sys.platform == "darwin":
        artifact = dist / f"PointLabStudio-v{__version__}-macOS-arm64.dmg"
        with tempfile.TemporaryDirectory(dir=work) as image:
            shutil.move(str(work / "payload" / "PointLabStudio.app"), image)
            subprocess.run(["hdiutil", "create", "-ov", "-format", "UDZO", "-volname", "PointLabStudio",
                            "-srcfolder", image, str(artifact)], check=True)
    elif args.format == "deb":
        artifact = build_deb(payload, architecture, work, dist)
    else:
        artifact = dist / f"PointLabStudio-v{__version__}-Ubuntu-{architecture}.tar.gz"
        temporary = artifact.with_suffix(".tmp")
        with tarfile.open(temporary, "w:gz", compresslevel=6) as archive:
            archive.add(payload, arcname="PointLabStudio")
        temporary.replace(artifact)
    if artifact.suffix != ".deb":
        write_checksum(artifact)
    print(f"Built: {artifact}")


if __name__ == "__main__":
    main()
