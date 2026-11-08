"""Package macOS ARM64 or Ubuntu ARM64/x86_64 on the matching host."""
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import __version__


def main():
    machine = platform.machine().lower()
    architecture = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "x86_64"}.get(machine)
    if sys.platform not in ("darwin", "linux") or architecture is None:
        raise SystemExit("Run on macOS ARM64 or Ubuntu ARM64/x86_64.")
    if sys.platform == "darwin" and architecture != "arm64":
        raise SystemExit("Use an Apple Silicon Python interpreter.")
    work = ROOT / "build" / f"{sys.platform}-{architecture}"
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("PYGFX_DATA_DIR", str(work / "pygfx"))
    Path(os.environ["PYGFX_DATA_DIR"]).mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--workpath", str(work / "work"), "--distpath", str(work / "payload"),
                    str(ROOT / "packaging" / "PointLabStudio-posix.spec")], check=True, cwd=ROOT)
    if sys.platform == "darwin":
        artifact = dist / f"PointLabStudio-v{__version__}-macOS-arm64.dmg"
        with tempfile.TemporaryDirectory(dir=work) as image:
            shutil.move(str(work / "payload" / "PointLabStudio.app"), image)
            subprocess.run(["hdiutil", "create", "-ov", "-format", "UDZO", "-volname", "PointLabStudio",
                            "-srcfolder", image, str(artifact)], check=True)
    else:
        artifact = dist / f"PointLabStudio-v{__version__}-Ubuntu-{architecture}.tar.gz"
        temporary = artifact.with_suffix(".tmp")
        with tarfile.open(temporary, "w:gz", compresslevel=6) as archive:
            archive.add(work / "payload" / "PointLabStudio", arcname="PointLabStudio")
        temporary.replace(artifact)
    print(f"Built: {artifact}")


if __name__ == "__main__":
    main()
