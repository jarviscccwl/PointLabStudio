"""Check archive contents and host guards without building foreign binaries."""
import importlib.util
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "build_native", Path(__file__).resolve().parents[1] / "packaging" / "build-native.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class NativePackagingTests(unittest.TestCase):
    def test_windows_is_rejected_before_starting_a_build(self):
        with patch.object(build.sys, "platform", "win32"), \
                patch.object(build.platform, "machine", return_value="AMD64"), \
                patch.object(build.subprocess, "run") as run:
            with self.assertRaises(SystemExit):
                build.main()
            run.assert_not_called()

    def test_linux_archive_keeps_executable_and_runtime_together(self):
        for machine, architecture in (("aarch64", "arm64"), ("x86_64", "x86_64")):
            with self.subTest(machine=machine), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                payload = root / "build" / f"linux-{architecture}" / "payload" / "PointLabStudio"
                (payload / "_internal").mkdir(parents=True)
                (payload / "PointLabStudio").write_bytes(b"test-executable")
                (payload / "_internal" / "library.so").write_bytes(b"test-library")
                with patch.object(build, "ROOT", root), \
                        patch.object(build.sys, "platform", "linux"), \
                        patch.object(build.platform, "machine", return_value=machine), \
                        patch.object(build.subprocess, "run"), patch.dict(build.os.environ):
                    build.main(["--format", "tar.gz"])
                archive = next((root / "dist").glob("*.tar.gz"))
                self.assertIn(architecture, archive.name)
                with tarfile.open(archive) as packaged:
                    self.assertEqual(packaged.extractfile("PointLabStudio/PointLabStudio").read(),
                                     b"test-executable")
                    self.assertEqual(packaged.extractfile("PointLabStudio/_internal/library.so").read(),
                                     b"test-library")

    def test_deb_rejects_wrong_or_non_linux_executable(self):
        for executable in (b"MZwindows", self.elf(183)):
            with self.subTest(executable=executable[:8]), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                payload = root / "payload"
                payload.mkdir()
                (payload / "PointLabStudio").write_bytes(executable)
                with self.assertRaisesRegex(ValueError, "ELF|architecture"):
                    build.build_deb(payload, "x86_64", root / "work", root / "dist")
                self.assertFalse(list(root.rglob("*.deb")))

    def test_deb_rejects_library_from_another_architecture(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            payload = root / "payload"
            (payload / "_internal").mkdir(parents=True)
            (payload / "PointLabStudio").write_bytes(self.elf(62))
            (payload / "_internal/library.so").write_bytes(self.elf(183))
            with self.assertRaisesRegex(ValueError, "architecture"):
                build.build_deb(payload, "x86_64", root / "work", root / "dist")

    def test_glibc_requirement_rejects_newer_ubuntu_runtime(self):
        build.check_glibc_versions("Name: GLIBC_2.35 Flags: none", Path("supported.so"))
        for version in ("GLIBC_2.36", "GLIBC_2.39", "GLIBC_ABI_DT_RELR"):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "22.04"):
                build.check_glibc_versions(f"Name: {version} Flags: none", Path("newer.so"))

    @staticmethod
    def elf(machine):
        header = bytearray(64)
        header[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<H", header, 18, machine)
        return bytes(header)

    @unittest.skipUnless(sys.platform == "linux" and shutil.which("dpkg-deb") and shutil.which("readelf"),
                         "Requires Linux dpkg-deb and readelf")
    def test_real_deb_contains_runtime_desktop_entry_and_correct_architecture(self):
        for architecture, deb_arch, machine in (("x86_64", "amd64", 62), ("arm64", "arm64", 183)):
            with self.subTest(architecture=architecture), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                payload = root / "payload"
                (payload / "_internal").mkdir(parents=True)
                (payload / "PointLabStudio").write_bytes(self.elf(machine))
                (payload / "PointLabStudio").chmod(0o755)
                (payload / "_internal" / "library.so").write_bytes(self.elf(machine))
                (payload / "_internal" / "helper").write_bytes(self.elf(machine))
                (payload / "_internal" / "libstdc++.so.6").write_bytes(self.elf(machine))
                (payload / "_internal" / "libgcc_s.so.1").write_bytes(self.elf(machine))
                artifact = build.build_deb(payload, architecture, root / "work", root / "dist")
                metadata = subprocess.check_output(["dpkg-deb", "-f", str(artifact),
                                                    "Package", "Version", "Architecture"], text=True)
                self.assertIn("Package: pointlabstudio", metadata)
                self.assertIn(f"Version: {build.__version__}", metadata)
                self.assertIn(f"Architecture: {deb_arch}", metadata)
                extracted = root / "extracted"
                subprocess.run(["dpkg-deb", "-R", str(artifact), str(extracted)], check=True)
                self.assertEqual((extracted / "opt/pointlabstudio/_internal/library.so").read_bytes(),
                                 self.elf(machine))
                self.assertEqual((extracted / "opt/pointlabstudio/_internal/helper").stat().st_mode & 0o777,
                                 0o755)
                self.assertFalse((extracted / "opt/pointlabstudio/_internal/libstdc++.so.6").exists())
                self.assertFalse((extracted / "opt/pointlabstudio/_internal/libgcc_s.so.1").exists())
                desktop = (extracted / "usr/share/applications/pointlabstudio.desktop").read_text()
                self.assertIn("Exec=pointlabstudio %f", desktop)
                self.assertTrue((extracted / "usr/share/icons/hicolor/scalable/apps/pointlabstudio.svg").is_file())
                self.assertFalse((extracted / "DEBIAN/postrm").exists())
                self.assertFalse((extracted / "home").exists())
                self.assertEqual((extracted.stat().st_mode & 0o777), 0o755)
                listing = subprocess.check_output(["dpkg-deb", "-c", str(artifact)], text=True)
                self.assertNotIn(" root/", listing.replace("root/root", "OWNER"))
                self.assertIn("root/root", listing)
                self.assertTrue(artifact.with_suffix(".deb.sha256").is_file())

    @unittest.skipUnless(sys.platform == "linux", "Requires a POSIX shell")
    def test_launcher_preserves_file_arguments(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root / "install with spaces/PointLabStudio"
            executable.parent.mkdir()
            executable.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n")
            executable.chmod(0o755)
            launcher = root / "pointlabstudio"
            launcher.write_text(build.launcher_script(executable))
            launcher.chmod(0o755)
            arguments = ["a path/数据.pcd", "--background", "light"]
            result = subprocess.check_output([str(launcher), *arguments], text=True)
            self.assertEqual(result.splitlines(), arguments)


if __name__ == "__main__":
    unittest.main()
