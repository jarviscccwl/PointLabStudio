"""雷达包导入回归：pcap/pcapng 解析、EM4 解码、mcap PointCloud2、落盘。"""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

from core.io import lidar_import, mcap_reader, pcap_reader, rs_em4


# ------------------------------- 构造工具 -------------------------------
def eth_udp(payload: bytes, sport: int = 6699, dport: int = 6699) -> bytes:
    udp = struct.pack(">HHHH", sport, dport, 8 + len(payload), 0) + payload
    ip = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 0, 0, 64, 17, 0,
                     bytes([192, 168, 1, 200]), bytes([192, 168, 1, 10])) + udp
    return bytes.fromhex("ffffffffffff") + bytes.fromhex("001122334455") + b"\x08\x00" + ip


def write_pcap(path: str, packets, *, big_endian: bool = False, nanosecond: bool = False):
    endian = ">" if big_endian else "<"
    magic = 0xA1B23C4D if nanosecond else 0xA1B2C3D4
    with open(path, "wb") as handle:
        handle.write(struct.pack(f"{endian}IHHiIII", magic, 2, 4, 0, 0, 65535, 1))
        for ts, data in packets:
            seconds = int(ts)
            fraction = int(round((ts - seconds) * (1e9 if nanosecond else 1e6)))
            handle.write(struct.pack(f"{endian}IIII", seconds, fraction, len(data), len(data)))
            handle.write(data)


def _pcapng_block(block_type: int, body: bytes) -> bytes:
    total = 12 + len(body) + (-(len(body)) % 4)
    return struct.pack("<II", block_type, total) + body + b"\x00" * (-len(body) % 4) + \
        struct.pack("<I", total)


def write_pcapng(path: str, packets):
    shb = struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1)
    idb = struct.pack("<HHI", 1, 0, 65535)
    with open(path, "wb") as handle:
        handle.write(_pcapng_block(0x0A0D0D0A, shb))
        handle.write(_pcapng_block(0x00000001, idb))
        for ts, data in packets:
            stamp = int(round(ts * 1_000_000))
            epb = struct.pack("<IIIII", 0, stamp >> 32, stamp & 0xFFFFFFFF, len(data), len(data)) + data
            handle.write(_pcapng_block(0x00000006, epb))


def msop(pkt_cnt: int, yaw_deg: float, *, surface: int = 1, radius: int = 1000,
         cols: int = 1200, return_mode: int = 4, attr: int = 0, sec: int = 1_700_000_000,
         rows: dict | None = None, compressed: bool = False) -> bytes:
    buf = bytearray(1084)
    buf[0:4] = b"\x55\xaa\x5a\xa5"
    struct.pack_into("<H", buf, 4, pkt_cnt)
    buf[6] = 0x02 if compressed else 0x00
    buf[8] = return_mode
    struct.pack_into("<Q", buf, 10, sec)
    buf[20] = 1
    buf[21] = 10
    struct.pack_into("<H", buf, 22, cols - 1)
    struct.pack_into("<h", buf, 24, int(round(yaw_deg * 100)))
    buf[26] = 0x01
    buf[27] = surface
    buf[30] = 0x70
    for i in range(260):
        value = (rows or {}).get(i, radius)
        struct.pack_into("<HBB", buf, 32 + 4 * i, value, 7, attr)
    return bytes(buf)


def difop2(*, yaw_offset_entry1: int = 0, pixel_pitch: list | None = None,
           surface_offset: int = 0) -> bytes:
    buf = bytearray(1162)
    buf[0:4] = b"\xa5\xff\x00\xae"
    buf[67] = 4
    buf[68] = 20
    buf[69] = 26
    for k in range(13):
        struct.pack_into("<h", buf, 70 + 2 * k, yaw_offset_entry1 if k == 1 else 0)
    pitches = pixel_pitch or [int(round((-12.975 + 0.05 * i) * 100)) for i in range(520)]
    for i in range(520):
        struct.pack_into("<h", buf, 96 + 2 * i, int(pitches[i]))
    for k in range(4):
        struct.pack_into("<h", buf, 1136 + 2 * k, surface_offset)
    return bytes(buf)


class PcapReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_pcap_little_endian(self):
        path = self.dir / "a.pcap"
        write_pcap(str(path), [(1_700_000_000.25, eth_udp(b"hello"))])
        datagrams = list(pcap_reader.iter_udp_datagrams(str(path)))
        self.assertEqual(len(datagrams), 1)
        datagram = datagrams[0]
        self.assertEqual(datagram.payload, b"hello")
        self.assertEqual((datagram.source_port, datagram.dest_port), (6699, 6699))
        self.assertEqual(datagram.source_ip, "192.168.1.200")
        self.assertAlmostEqual(datagram.timestamp, 1_700_000_000.25, places=6)

    def test_pcap_big_endian_nanosecond(self):
        path = self.dir / "b.pcap"
        write_pcap(str(path), [(5.5, eth_udp(b"abc"))], big_endian=True, nanosecond=True)
        datagrams = list(pcap_reader.iter_udp_datagrams(str(path)))
        self.assertEqual([d.payload for d in datagrams], [b"abc"])
        self.assertAlmostEqual(datagrams[0].timestamp, 5.5, places=6)

    def test_pcapng(self):
        path = self.dir / "c.pcapng"
        write_pcapng(str(path), [(7.125, eth_udp(b"ng"))])
        datagrams = list(pcap_reader.iter_udp_datagrams(str(path)))
        self.assertEqual([d.payload for d in datagrams], [b"ng"])
        self.assertAlmostEqual(datagrams[0].timestamp, 7.125, places=6)

    def test_truncated_pcap_reports_error(self):
        path = self.dir / "d.pcap"
        write_pcap(str(path), [(1.0, eth_udp(b"x" * 40))])
        data = path.read_bytes()
        path.write_bytes(data[:-20])
        with self.assertRaises(pcap_reader.PcapError):
            list(pcap_reader.iter_udp_datagrams(str(path)))


class Em4DecodeTests(unittest.TestCase):
    def test_manual_angles_and_cartesian(self):
        pitch = [0] * 520
        pitch[22] = -1546
        calibration = rs_em4.Em4Calibration.from_difop2(
            difop2(yaw_offset_entry1=-3, pixel_pitch=pitch, surface_offset=15))
        self.assertAlmostEqual(calibration.pixel_pitch[22], -15.46)
        self.assertEqual(calibration.yaw_offsets_for_rows()[22], -0.03)
        self.assertAlmostEqual(calibration.surface_pitch[0], 0.15)

        assembler = rs_em4.Em4FrameAssembler(calibration)
        self.assertIsNone(assembler.add(msop(1, 54.56, radius=0, rows={22: 2000})))
        self.assertIsNone(assembler.add(msop(2, 54.56, radius=0)))
        frame = assembler.flush()
        self.assertIsNotNone(frame)
        self.assertEqual(frame.point_count, 1)
        yaw = np.deg2rad(54.53)
        pitch_rad = np.deg2rad(-15.31)
        expected = np.array([10.0 * np.cos(pitch_rad) * np.cos(yaw),
                             10.0 * np.cos(pitch_rad) * np.sin(yaw),
                             10.0 * np.sin(pitch_rad)], dtype=np.float64)
        np.testing.assert_allclose(frame.xyz[0], expected, atol=1e-5)

    def test_noise_attribute_dropped(self):
        assembler = rs_em4.Em4FrameAssembler(rs_em4.Em4Calibration.nominal())
        assembler.add(msop(1, 0.0, attr=rs_em4.ATTR_NOISE))
        assembler.add(msop(2, 0.0, attr=rs_em4.ATTR_NOISE))
        frame = assembler.flush()
        self.assertIsNotNone(frame)
        self.assertEqual(frame.point_count, 0)
        self.assertGreater(frame.noise_dropped, 0)

    def test_dual_return_keeps_first_echo(self):
        assembler = rs_em4.Em4FrameAssembler(rs_em4.Em4Calibration.nominal())
        rows = {row: 1000 for row in range(0, 260, 2)}
        for part in range(4):
            assembler.add(msop(part + 1, 10.0, cols=1, return_mode=0, rows=rows))
        frame = assembler.flush()
        self.assertIsNotNone(frame)
        self.assertTrue(frame.dual_return)
        self.assertEqual(frame.columns, 1)
        self.assertEqual(frame.point_count, 520)

    def test_compressed_packet_rejected(self):
        with self.assertRaises(rs_em4.Em4CompressedError):
            rs_em4.parse_msop(msop(1, 0.0, compressed=True))

    def test_frame_boundary_starts_with_part_zero(self):
        assembler = rs_em4.Em4FrameAssembler(rs_em4.Em4Calibration.nominal())
        for column in range(2):
            for part in range(2):
                assembler.add(msop(column * 2 + part + 1, column * 0.1, cols=2,
                                   return_mode=4, radius=1000))
        frame = assembler.flush()
        self.assertIsNotNone(frame)
        self.assertEqual(frame.columns, 2)
        self.assertEqual(frame.point_count, 2 * 2 * 260)


class LidarImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _write_frames(self, frames: int = 2) -> Path:
        path = self.dir / "run.pcap"
        packets = []
        for frame in range(frames):
            for column in range(2):
                for part in range(2):
                    packets.append((1_700_000_000 + frame + column * 1e-4,
                                    eth_udp(msop(column * 2 + part + 1, column * 0.1, cols=2))))
        write_pcap(str(path), packets)
        return path

    def test_import_pcap_frames(self):
        path = self._write_frames()
        self.assertTrue(lidar_import.is_lidar_package(str(path)))
        info = lidar_import.inspect_source(str(path))
        self.assertEqual(info.kind, "pcap")
        output = self.dir / "frames"
        result = lidar_import.import_frames(str(path), str(output), count=2)
        self.assertEqual(result.frames, 2)
        self.assertEqual(result.points, 2 * 2 * 2 * 260)
        self.assertTrue((output / "frames.json").is_file())
        data = np.load(result.files[0])
        self.assertEqual(data.shape, (1040, 4))
        self.assertEqual(data.dtype, np.float32)

    def test_import_range_and_downsample(self):
        path = self._write_frames(frames=3)
        output = self.dir / "subset"
        result = lidar_import.import_frames(str(path), str(output), start=1, count=1,
                                            max_points=300)
        self.assertEqual(result.frames, 1)
        data = np.load(result.files[0])
        self.assertLessEqual(data.shape[0], 300)
        self.assertEqual(result.points, data.shape[0])

    def test_import_rejects_unknown_payload(self):
        path = self.dir / "random.pcap"
        write_pcap(str(path), [(1.0, eth_udp(b"not-a-msop-packet"))])
        with self.assertRaises(lidar_import.LidarImportError) as ctx:
            lidar_import.import_frames(str(path), str(self.dir / "none"), count=1)
        self.assertIn("6699", str(ctx.exception))


class McapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _write_mcap(self, points: np.ndarray, intensity: np.ndarray) -> Path:
        from mcap_ros2.writer import Writer as Ros2Writer

        path = self.dir / "cloud.mcap"
        ros = Ros2Writer(str(path))
        schema = ros.register_msgdef(
            "sensor_msgs/msg/PointCloud2",
            """std_msgs/Header header
uint32 height
uint32 width
sensor_msgs/PointField[] fields
bool is_bigendian
uint32 point_step
uint32 row_step
uint8[] data
bool is_dense
================================================================================
MSG: std_msgs/Header
builtin_interfaces/Time stamp
string frame_id
================================================================================
MSG: builtin_interfaces/Time
int32 sec
uint32 nanosec
================================================================================
MSG: sensor_msgs/PointField
string name
uint32 offset
uint8 datatype
uint32 count
""",
        )
        layout = np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
                           ("i", "<f4"), ("pad", "<f4")])
        packed = np.zeros(len(points), dtype=layout)
        packed["x"], packed["y"], packed["z"] = points.T
        packed["i"] = intensity
        fields = [
            {"name": "x", "offset": 0, "datatype": 7, "count": 1},
            {"name": "y", "offset": 4, "datatype": 7, "count": 1},
            {"name": "z", "offset": 8, "datatype": 7, "count": 1},
            {"name": "intensity", "offset": 12, "datatype": 7, "count": 1},
        ]
        ros.write_message(
            "/points",
            schema,
            {
                "header": {"stamp": {"sec": 1, "nanosec": 500_000_000}, "frame_id": "lidar"},
                "height": 1,
                "width": len(points),
                "fields": fields,
                "is_bigendian": False,
                "point_step": layout.itemsize,
                "row_step": layout.itemsize * len(points),
                "data": list(packed.tobytes()),
                "is_dense": True,
            },
            1_700_000_000_000_000_000,
        )
        ros.finish()
        return path

    def test_pointcloud2_roundtrip(self):
        points = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]], np.float32)
        intensity = np.array([10.0, 20.0, 30.0], np.float32)
        path = self._write_mcap(points, intensity)

        topics = mcap_reader.list_topics(str(path))
        self.assertEqual([t.topic for t in topics], ["/points"])
        frames = list(mcap_reader.iter_frames(str(path), "/points", count=1))
        self.assertEqual(len(frames), 1)
        np.testing.assert_allclose(frames[0].xyz, points)
        np.testing.assert_allclose(frames[0].intensity, intensity)
        self.assertAlmostEqual(frames[0].timestamp, 1.5)

        output = self.dir / "mcap_frames"
        result = lidar_import.import_frames(str(path), str(output), topic="/points", count=1)
        self.assertEqual((result.frames, result.points), (1, 3))
        saved = np.load(result.files[0])
        np.testing.assert_allclose(saved[:, :3], points)
        np.testing.assert_allclose(saved[:, 3], intensity)


if __name__ == "__main__":
    unittest.main()
