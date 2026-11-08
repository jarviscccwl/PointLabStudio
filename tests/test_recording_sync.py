import unittest

from core.io.recording_types import FrameRef
from core.recording_sync import TimestampMatcher


class TimestampMatcherTests(unittest.TestCase):
    def test_unsorted_frames_preserve_original_frame_indices(self):
        refs = [FrameRef(8, 300, 30), FrameRef(4, 100, 10), FrameRef(7, 200, 20)]
        self.assertEqual(TimestampMatcher(refs).closest(21, 2), (7, -1))

    def test_duplicate_and_equidistant_ties_are_deterministic(self):
        refs = [FrameRef(9, 20, 20), FrameRef(6, 10, 10), FrameRef(2, 10, 10)]
        matcher = TimestampMatcher(refs)
        self.assertEqual(matcher.closest(10, 0), (2, 0))
        self.assertEqual(matcher.closest(15, 5), (2, -5))

    def test_device_time_does_not_fall_back_to_record_time(self):
        refs = [FrameRef(0, None, 100), FrameRef(1, 200, 110)]
        matcher = TimestampMatcher(refs, basis="device")
        self.assertIsNone(matcher.closest(100, 0))
        self.assertEqual(matcher.closest(200, 0), (1, 0))

    def test_offset_tolerance_and_integer_nanoseconds(self):
        stamp = 1_800_000_000_000_000_001
        matcher = TimestampMatcher([FrameRef(3, stamp, stamp)], offset_ns=-7)
        self.assertEqual(matcher.closest(stamp - 9, 2), (3, 2))
        self.assertIsNone(matcher.closest(stamp - 9, 1))

    def test_empty_inputs_and_invalid_options(self):
        self.assertIsNone(TimestampMatcher([]).closest(0, 0))
        self.assertIsNone(TimestampMatcher([FrameRef(0, None, 1)], "device").closest(1, 1))
        with self.assertRaises(ValueError):
            TimestampMatcher([], "other")
        with self.assertRaises(ValueError):
            TimestampMatcher([]).closest(0, -1)
        with self.assertRaises(TypeError):
            TimestampMatcher([], offset_ns=0.5)


if __name__ == "__main__":
    unittest.main()
