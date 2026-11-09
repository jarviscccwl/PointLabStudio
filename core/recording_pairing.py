"""Select an initial cloud/image pair within one capture, without changing clocks."""
from dataclasses import dataclass
import os
from pathlib import Path

from core.recording_sync import TimestampMatcher


@dataclass(frozen=True)
class CapturePair:
    primary: tuple[str, str]
    secondary: tuple[str, str]
    position: int
    matches: int


def capture_directory(entry):
    path = Path(entry.source.path)
    format_name = getattr(entry.source.info, "format", "")
    bag_names = ("bag", "rosbag", "rosbag2")
    if (format_name == "ROS2 bag" and path.suffix.casefold() not in (".db3", ".mcap")
            and path.name.casefold() not in bag_names):
        return os.path.normcase(os.path.normpath(str(path)))
    parent = path.parent
    # ROS2 directory sources and their explicitly opened split files should
    # group with a sibling PCAP, not with other captures under the dataset root.
    if parent.name.casefold() in bag_names:
        parent = parent.parent
    return os.path.normcase(os.path.normpath(str(parent)))


def suggest_pair(entries, *, basis="record", tolerance_ns=50_000_000, offset_ns=0):
    """Return a same-capture pair and first matched 1-based browser position.

    Missing timestamps remain missing. A pair with no match starts at position
    one so the normal browser can explain why it could not associate a frame.
    Input frames are never filtered or reordered.
    """
    clouds = [entry for entry in entries if entry.stream.kind == "pointcloud" and entry.frames]
    cameras = [entry for entry in entries if entry.stream.kind == "image" and entry.frames]
    choices = []
    matchers = {}
    for priority, cloud in enumerate(clouds):
        for camera in cameras:
            if capture_directory(cloud) != capture_directory(camera):
                continue
            if camera.key not in matchers:
                matchers[camera.key] = TimestampMatcher(camera.frames, basis, offset_ns)
            matcher = matchers[camera.key]
            first, count = 1, 0
            for position, ref in enumerate(cloud.frames, 1):
                stamp = ref.record_time_ns if basis == "record" else ref.timestamp_ns
                if stamp is not None and matcher.closest(stamp, tolerance_ns) is not None:
                    if count == 0:
                        first = position
                    count += 1
            pair = CapturePair(cloud.key, camera.key, first, count)
            choices.append(((not count, priority, -count, camera.key), pair))
    return min(choices, key=lambda choice: choice[0])[1] if choices else None
