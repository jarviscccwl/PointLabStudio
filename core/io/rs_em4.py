"""RoboSense EM4-F MSOP / DIFOP2 解码（非压缩模式）。

协议依据官方手册 `docs/en/EM4.md`（RoboSense-Robotics/product-manual）：

- MSOP 固定 1084 B，点数据 32..1071，260 点 × 4 B（radius u16 / intensity u8 / attr u8）；
- NROI 每帧 1200 列，单回波每列 2 包（第 1 包行 1-260，第 2 包行 261-520）；
- radius 分辨率 5 mm（300 m 量程 / 5 mm = 60000，u16 恰好放得下）；
- 角度：``yaw = yaw_angle + HalfVcselYawOffset[(i // 20) % 13]``，
  ``pitch = PixelPitch[i] + SurfacePitchOffset[surface]``，单位 0.01°；
- 未收到 DIFOP2 时退回标称标定（垂直 -12.5°~+12.5°，520 线等分）。

压缩模式（roi_compression_mode 0x02/0x03）的位流布局手册未公开，遇到时给出明确提示。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

MSOP_MAGIC = b"\x55\xaa\x5a\xa5"
MSOP_FRAGMENT_MAGIC = b"\x55\xaa\x5a\x02"
DIFOP2_MAGIC = b"\xa5\xff\x00\xae"

MSOP_SIZE = 1084
DIFOP2_SIZE = 1162
POINT_OFFSET = 32
POINTS_PER_PACKET = 260
POINT_BYTES = 4
ROWS_PER_COLUMN = 520
MAX_COLUMNS = 1600
RADIUS_UNIT_M = 0.005
ATTR_NOISE = 0x08


class Em4FormatError(ValueError):
    """MSOP / DIFOP2 数据不完整或不是 EM4 格式。"""


class Em4CompressedError(Em4FormatError):
    """捕获包为压缩模式，当前解码器不支持。"""


def is_msop(payload: bytes) -> bool:
    return len(payload) >= 8 and payload[:4] == MSOP_MAGIC


def is_difop2(payload: bytes) -> bool:
    return len(payload) >= DIFOP2_SIZE and payload[:4] == DIFOP2_MAGIC


@dataclass(frozen=True)
class Em4Calibration:
    """DIFOP2 角度标定；单位统一为度。"""

    pixel_pitch: np.ndarray  # (520,) 每线 pitch
    yaw_offsets: np.ndarray  # (13,) 半 VCSEL 偏航修正
    surface_pitch: np.ndarray  # (4,) 四个转镜面的 pitch 偏移
    pixels_per_half: int = 20
    surface_count: int = 4

    @classmethod
    def nominal(cls, surface_count: int = 4) -> "Em4Calibration":
        pitch = np.linspace(-12.5, 12.5, ROWS_PER_COLUMN, dtype=np.float64)
        return cls(pitch, np.zeros(13), np.zeros(max(surface_count, 1)), 20, surface_count)

    @classmethod
    def from_difop2(cls, payload: bytes) -> "Em4Calibration":
        if not is_difop2(payload):
            raise Em4FormatError("不是 EM4 DIFOP2 数据。")
        surface_count = payload[67] or 4
        pixels_per_half = payload[68] or 20
        yaw = np.frombuffer(payload, dtype="<i2", count=13, offset=70).astype(np.float64) * 0.01
        pitch = np.frombuffer(payload, dtype="<i2", count=ROWS_PER_COLUMN,
                              offset=96).astype(np.float64) * 0.01
        surfaces = np.frombuffer(payload, dtype="<i2", count=4, offset=1136).astype(np.float64) * 0.01
        if not np.all(np.isfinite(pitch)) or float(np.ptp(pitch)) < 1.0:
            raise Em4FormatError("DIFOP2 的 PixelPitch 标定数据异常。")
        return cls(pitch, yaw, surfaces, int(pixels_per_half), int(surface_count))

    def pitch_for(self, surface: int) -> np.ndarray:
        index = min(max(surface - 1, 0), len(self.surface_pitch) - 1)
        return self.pixel_pitch + float(self.surface_pitch[index])

    def yaw_offsets_for_rows(self) -> np.ndarray:
        """520 行对应的偏航修正（同一 VCSEL 的奇偶两个分区共用一条修正）。"""
        if not len(self.yaw_offsets):
            return np.zeros(ROWS_PER_COLUMN)
        step = max(int(self.pixels_per_half), 1)
        index = (np.arange(ROWS_PER_COLUMN) // step) % len(self.yaw_offsets)
        return self.yaw_offsets[index]


@dataclass(frozen=True)
class Em4MsopPacket:
    timestamp: float
    packet_index: int
    column: int
    part: int
    columns: int
    surface: int
    yaw_deg: float
    radius: np.ndarray  # (260,) uint16
    intensity: np.ndarray  # (260,) uint8
    attribute: np.ndarray  # (260,) uint8
    return_mode: int
    compressed: bool = False


def parse_msop(payload: bytes) -> Em4MsopPacket:
    if not is_msop(payload):
        raise Em4FormatError("不是 EM4 MSOP 数据包。")
    if len(payload) < MSOP_SIZE:
        raise Em4FormatError(f"MSOP 长度不足：{len(payload)} < {MSOP_SIZE}。")
    packet_index = struct.unpack_from("<H", payload, 4)[0]
    compression = payload[6]
    if compression not in (0x00, 0x01):
        raise Em4CompressedError(
            "该 pcap 为点云压缩模式（roi_compression_mode=0x%02X）。"
            "请在雷达配置中关闭“点云压缩 / Compress”后重新录制，或提供一份非压缩包。" % compression
        )
    return_mode = payload[8]
    timestamp = float(struct.unpack_from("<Q", payload, 10)[0] & ((1 << 48) - 1))
    timestamp += float(struct.unpack_from("<I", payload, 16)[0]) * 1e-6
    columns = struct.unpack_from("<H", payload, 22)[0] + 1
    yaw_deg = struct.unpack_from("<h", payload, 24)[0] * 0.01
    surface = payload[27]
    dual = return_mode == 0
    per_column = 4 if dual else 2
    column = (packet_index - 1) // per_column
    part = (packet_index - 1) % per_column
    points = np.frombuffer(payload, dtype=np.uint8, count=POINTS_PER_PACKET * POINT_BYTES,
                           offset=POINT_OFFSET)
    groups = points.reshape(POINTS_PER_PACKET, POINT_BYTES)
    radius = np.ascontiguousarray(groups[:, 0:2]).view("<u2").reshape(-1)
    return Em4MsopPacket(
        timestamp=timestamp,
        packet_index=int(packet_index),
        column=int(column),
        part=int(part),
        columns=int(max(columns, column + 1)),
        surface=int(surface),
        yaw_deg=float(yaw_deg),
        radius=radius.copy(),
        intensity=groups[:, 2].copy(),
        attribute=groups[:, 3].copy(),
        return_mode=int(return_mode),
        compressed=False,
    )


@dataclass
class Em4Frame:
    xyz: np.ndarray  # (N, 3) float32，单位米
    intensity: np.ndarray  # (N,) float32
    timestamp: float
    columns: int
    dual_return: bool = False
    noise_dropped: int = 0

    @property
    def point_count(self) -> int:
        return int(self.xyz.shape[0])


@dataclass
class _FrameBuffers:
    radius: np.ndarray = field(default_factory=lambda: np.zeros((MAX_COLUMNS, ROWS_PER_COLUMN),
                                                               dtype=np.uint16))
    intensity: np.ndarray = field(default_factory=lambda: np.zeros((MAX_COLUMNS, ROWS_PER_COLUMN),
                                                                  dtype=np.uint8))
    attribute: np.ndarray = field(default_factory=lambda: np.zeros((MAX_COLUMNS, ROWS_PER_COLUMN),
                                                                  dtype=np.uint8))
    yaw: np.ndarray = field(default_factory=lambda: np.zeros(MAX_COLUMNS, dtype=np.float64))
    surface: np.ndarray = field(default_factory=lambda: np.ones(MAX_COLUMNS, dtype=np.int32))
    parts: np.ndarray = field(default_factory=lambda: np.zeros(MAX_COLUMNS, dtype=np.uint8))
    columns: int = 0
    timestamp: float = 0.0

    def reset(self) -> None:
        self.radius.fill(0)
        self.intensity.fill(0)
        self.attribute.fill(0)
        self.yaw.fill(0.0)
        self.surface.fill(1)
        self.parts.fill(0)
        self.columns = 0
        self.timestamp = 0.0


class Em4FrameAssembler:
    """把 MSOP 包按 frame 拼成点云；收到下一帧首包时输出上一帧。"""

    def __init__(self, calibration: Em4Calibration | None = None,
                 drop_noise: bool = True, min_range_m: float = 0.0):
        self.calibration = calibration or Em4Calibration.nominal()
        self.drop_noise = drop_noise
        self.min_range_m = max(float(min_range_m), 0.0)
        self._buffers = _FrameBuffers()
        self._dual = False
        self._seen = False
        self.warnings: list[str] = []

    def calibrate(self, difop2: bytes) -> None:
        try:
            self.calibration = Em4Calibration.from_difop2(difop2)
        except Em4FormatError:
            pass

    def add(self, payload: bytes) -> Em4Frame | None:
        packet = parse_msop(payload)
        finished = None
        # 新一帧的首包（第 0 列 part 0）到来时收尾上一帧
        if self._seen and packet.column == 0 and packet.part == 0:
            finished = self._finalize()
        if not self._seen:
            self._seen = True
            self._dual = packet.return_mode == 0
            self._buffers.timestamp = packet.timestamp
        buffers = self._buffers
        column = packet.column
        if column >= MAX_COLUMNS:
            return finished
        buffers.columns = max(buffers.columns, min(packet.columns, MAX_COLUMNS))
        row_start = packet.part * POINTS_PER_PACKET
        if self._dual:
            row_start = packet.part * 130
        # 行号越界保护：异常包直接忽略，避免写坏整帧
        if row_start + (POINTS_PER_PACKET if not self._dual else 130) > ROWS_PER_COLUMN:
            return finished
        buffers.yaw[column] = packet.yaw_deg
        buffers.surface[column] = packet.surface
        if self._dual:
            rows = packet.radius[0::2][:130]  # 只取第一次回波
            buffers.radius[column, row_start:row_start + 130] = rows
            buffers.intensity[column, row_start:row_start + 130] = packet.intensity[0::2][:130]
            buffers.attribute[column, row_start:row_start + 130] = packet.attribute[0::2][:130]
        else:
            buffers.radius[column, row_start:row_start + POINTS_PER_PACKET] = packet.radius
            buffers.intensity[column, row_start:row_start + POINTS_PER_PACKET] = packet.intensity
            buffers.attribute[column, row_start:row_start + POINTS_PER_PACKET] = packet.attribute
        buffers.parts[column] |= np.uint8(1 << min(packet.part, 7))
        return finished

    def flush(self) -> Em4Frame | None:
        return self._finalize() if self._seen else None

    def _finalize(self) -> Em4Frame | None:
        buffers = self._buffers
        needed = 0x0F if self._dual else 0x03
        ready = [c for c in range(buffers.columns) if (int(buffers.parts[c]) & needed) == needed]
        frame = None
        if ready:
            frame = self._build(buffers, ready)
        buffers.reset()
        self._seen = False
        self._dual = False
        return frame

    def _build(self, buffers: _FrameBuffers, columns: list[int]) -> Em4Frame:
        index = np.asarray(columns, dtype=np.int64)
        radius = buffers.radius[index].astype(np.float64) * RADIUS_UNIT_M
        intensity = buffers.intensity[index].astype(np.float32)
        attribute = buffers.attribute[index]
        yaw = buffers.yaw[index][:, None] + self.calibration.yaw_offsets_for_rows()[None, :]
        surfaces = buffers.surface[index]
        pitch_rows = self.calibration.pixel_pitch[None, :]
        surface_offsets = self.calibration.surface_pitch[np.clip(surfaces - 1, 0,
                                                                 len(self.calibration.surface_pitch) - 1)]
        pitch = pitch_rows + surface_offsets[:, None]
        pitch_rad = np.deg2rad(pitch)
        yaw_rad = np.deg2rad(yaw)
        cos_pitch = np.cos(pitch_rad)
        xyz = np.empty(radius.shape + (3,), dtype=np.float32)
        xyz[..., 0] = (radius * cos_pitch * np.cos(yaw_rad)).astype(np.float32)
        xyz[..., 1] = (radius * cos_pitch * np.sin(yaw_rad)).astype(np.float32)
        xyz[..., 2] = (radius * np.sin(pitch_rad)).astype(np.float32)
        valid = radius > 0.0
        noise = 0
        if self.drop_noise:
            noise_mask = attribute == ATTR_NOISE
            noise = int(np.count_nonzero(valid & noise_mask))
            valid &= ~noise_mask
        if self.min_range_m > 0.0:
            valid &= radius >= self.min_range_m
        return Em4Frame(
            xyz=np.ascontiguousarray(xyz[valid]),
            intensity=np.ascontiguousarray(intensity[valid]),
            timestamp=buffers.timestamp,
            columns=len(columns),
            dual_return=self._dual,
            noise_dropped=noise,
        )
