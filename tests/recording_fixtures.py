"""Small real ROS2 MCAPs for recording interaction tests."""
import numpy as np
from mcap.writer import Writer
from rosbags.typesys import Stores, get_typestore


def write_sensor_mcap(path, *, clouds=(), images=()):
    store = get_typestore(Stores.ROS2_HUMBLE)
    types = store.types
    with path.open("wb") as handle:
        writer = Writer(handle)
        writer.start(profile="ros2")
        for kind, frames in (("PointCloud2", clouds), ("Image", images)):
            if not frames:
                continue
            name = "sensor_msgs/msg/" + kind
            definition, _ = store.generate_msgdef(name, ros_version=2)
            schema = writer.register_schema(name, "ros2msg", definition.encode())
            channel = writer.register_channel("/" + kind, "cdr", schema)
            for stamp, value in frames:
                time = types["builtin_interfaces/msg/Time"](stamp // 10**9, stamp % 10**9)
                header = types["std_msgs/msg/Header"](time, "sensor")
                if kind == "PointCloud2":
                    xyz = np.asarray(value, dtype="<f4").reshape(-1, 3)
                    fields = [types["sensor_msgs/msg/PointField"](axis, i * 4, 7, 1)
                              for i, axis in enumerate("xyz")]
                    message = types[name](header, 1, len(xyz), fields, False, 12,
                                          len(xyz) * 12, xyz.view(np.uint8).reshape(-1), True)
                else:
                    pixels = np.asarray(value, dtype=np.uint8)
                    message = types[name](header, pixels.shape[0], pixels.shape[1],
                                          "rgb8", 0, pixels.shape[1] * 3, pixels.reshape(-1))
                writer.add_message(channel, stamp, bytes(store.serialize_cdr(message, name)), stamp)
        writer.finish()
