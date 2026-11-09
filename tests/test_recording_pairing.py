"""Automatic capture-folder pairing uses the selected clock and original frames."""
from types import SimpleNamespace
import unittest

from core.io.recording_types import FrameRef
from core.recording_pairing import suggest_pair


def stream(path, kind, stamps, *, name="0", device=None, format=""):
    return SimpleNamespace(
        key=(path, name),
        source=SimpleNamespace(path=path, info=SimpleNamespace(format=format)),
        stream=SimpleNamespace(kind=kind),
        frames=[FrameRef(i + 20, device[i] if device else None, t)
                for i, t in enumerate(stamps)],
    )


class RecordingPairingTests(unittest.TestCase):
    def test_folder_pair_starts_at_first_matching_cloud_without_trimming(self):
        image = stream("C:/captures/one/bag", "image", [200, 300], format="ROS2 bag")
        cloud = stream("C:/captures/one/lidar.pcap", "pointcloud", [0, 100, 200, 300])
        pair = suggest_pair([image, cloud], tolerance_ns=0)
        self.assertEqual(pair.primary, cloud.key)
        self.assertEqual(pair.secondary, image.key)
        self.assertEqual(pair.position, 3)
        self.assertEqual(pair.matches, 2)
        self.assertEqual(len(cloud.frames), 4)

    def test_separate_capture_directories_are_not_auto_paired(self):
        image = stream("C:/captures/two/bag/bag_0.mcap", "image", [100])
        cloud = stream("C:/captures/one/lidar.pcap", "pointcloud", [100])
        self.assertIsNone(suggest_pair([cloud, image]))

    def test_standalone_ros2_directories_are_distinct_captures(self):
        cloud = stream("C:/captures/capture_A", "pointcloud", [100], format="ROS2 bag")
        image = stream("C:/captures/capture_B", "image", [100], format="ROS2 bag")
        self.assertIsNone(suggest_pair([cloud, image]))

    def test_standalone_db3_file_can_pair_with_sibling_camera(self):
        cloud = stream("C:/capture/cloud.db3", "pointcloud", [100], format="ROS2 bag")
        image = stream("C:/capture/camera.mcap", "image", [100])
        self.assertIsNotNone(suggest_pair([cloud, image]))

    def test_multiple_cameras_choose_matching_coverage_deterministically(self):
        cloud = stream("C:/one/cloud.pcap", "pointcloud", [100, 200, 300])
        wrong = stream("C:/one/bag/part.mcap", "image", [10_000], name="other")
        camera = stream("C:/one/bag/part.mcap", "image", [100, 200, 300], name="camera")
        pair = suggest_pair([wrong, camera, cloud], tolerance_ns=5)
        self.assertEqual(pair.secondary, camera.key)
        self.assertEqual(pair.matches, 3)

    def test_device_clock_and_offset_do_not_fall_back_to_record_clock(self):
        cloud = stream("C:/one/cloud.pcap", "pointcloud", [100], device=[1000])
        image = stream("C:/one/camera.mcap", "image", [100], device=[900])
        self.assertEqual(suggest_pair([cloud, image], basis="device", tolerance_ns=0).matches, 0)
        pair = suggest_pair([cloud, image], basis="device", tolerance_ns=0, offset_ns=100)
        self.assertEqual(pair.matches, 1)

    def test_nonoverlapping_pair_remains_viewable_with_explicit_no_match(self):
        cloud = stream("C:/one/cloud.pcap", "pointcloud", [10, 20])
        image = stream("C:/one/camera.mcap", "image", [100])
        pair = suggest_pair([cloud, image], tolerance_ns=1)
        self.assertEqual((pair.position, pair.matches), (1, 0))


if __name__ == "__main__":
    unittest.main()
