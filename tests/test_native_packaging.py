"""Check archive contents and host guards without building foreign binaries."""
import importlib.util
from pathlib import Path
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
                    build.main()
                archive = next((root / "dist").glob("*.tar.gz"))
                self.assertIn(architecture, archive.name)
                with tarfile.open(archive) as packaged:
                    self.assertEqual(packaged.extractfile("PointLabStudio/PointLabStudio").read(),
                                     b"test-executable")
                    self.assertEqual(packaged.extractfile("PointLabStudio/_internal/library.so").read(),
                                     b"test-library")


if __name__ == "__main__":
    unittest.main()
