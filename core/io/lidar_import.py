"""雷达包（pcap / mcap）导入：解析成逐帧点云并落盘为 .npy 帧目录。

帧文件格式：Nx4 float32（x, y, z, intensity），与既有 `read_npy_npz` 的约定一致
（第 4 列自动识别为 intensity）。落盘后可直接用「打开文件夹」查看，帧浏览器、工程、
导出等现有功能全部复用。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field

import numpy as np

from core.cancellation import check_cancel
from core.io import mcap_reader, pcap_reader, rs_em4

LIDAR_EXTS = {".pcap", ".pcapng", ".mcap"}
MSOP_PORTS = {6699}
DIFOP2_PORTS = {7766}


class LidarImportError(ValueError):
    """无法从雷达包中解析出点云。"""


@dataclass
class SourceInfo:
    kind: str  # "pcap" / "mcap"
    topics: list[mcap_reader.McapTopic] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ImportResult:
    files: list[str]
    frames: int
    points: int
    warnings: list[str] = field(default_factory=list)

    @property
    def output_dir(self) -> str:
        return os.path.dirname(self.files[0]) if self.files else ""


def is_lidar_package(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in LIDAR_EXTS


def inspect_source(path: str) -> SourceInfo:
    """判断包类型；mcap 顺带列出 PointCloud2 话题。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".mcap":
        topics = mcap_reader.list_topics(path)
        return SourceInfo("mcap", topics)
    if ext in (".pcap", ".pcapng"):
        return SourceInfo("pcap")
    raise LidarImportError(f"不支持的雷达包类型：{ext}。")


def _safe_prefix(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", stem).strip("_")
    return stem or "frames"


def _sample(xyz: np.ndarray, intensity: np.ndarray, limit: int):
    if limit <= 0 or xyz.shape[0] <= limit:
        return xyz, intensity
    index = np.linspace(0, xyz.shape[0] - 1, limit).astype(np.int64)
    return xyz[index], intensity[index]


def _save_frame(path: str, xyz: np.ndarray, intensity: np.ndarray | None, limit: int) -> int:
    xyz = np.asarray(xyz, dtype=np.float32).reshape(-1, 3)
    if intensity is None:
        intensity = np.zeros(len(xyz), dtype=np.float32)
    intensity = np.asarray(intensity, dtype=np.float32).reshape(-1)
    xyz, intensity = _sample(xyz, intensity, limit)
    np.save(path, np.column_stack((xyz, intensity)).astype(np.float32))
    return int(xyz.shape[0])


def _iter_pcap_frames(path: str, cancel=None, drop_noise: bool = True,
                      min_range: float = 0.0, warnings: list | None = None):
    assembler = rs_em4.Em4FrameAssembler(drop_noise=drop_noise, min_range_m=min_range)
    msop_seen = 0
    difop_seen = False
    for datagram in pcap_reader.iter_udp_datagrams(path, cancel=cancel):
        payload = datagram.payload
        if payload[:4] == rs_em4.DIFOP2_MAGIC:
            assembler.calibrate(payload)
            difop_seen = True
            continue
        if payload[:4] in (rs_em4.MSOP_MAGIC, rs_em4.MSOP_FRAGMENT_MAGIC):
            if payload[:4] == rs_em4.MSOP_FRAGMENT_MAGIC:
                raise LidarImportError(
                    "该 pcap 为分片/压缩 MSOP（55aa5a02），当前只支持非压缩单包模式。"
                )
            msop_seen += 1
            frame = assembler.add(payload)
            if frame is not None:
                yield frame
    frame = assembler.flush()
    if frame is not None:
        yield frame
    if not msop_seen:
        raise LidarImportError(
            "pcap 中没有找到 EM4 MSOP 数据包（端口 6699）。"
            "请确认录制时雷达工作在非压缩模式，且抓包包含 UDP 6699 端口。"
        )
    if warnings is not None and not difop_seen:
        warnings.append("未发现 DIFOP2 标定包，已使用标称角度；如角度有偏差请重新录制含 7766 端口的抓包。")


def import_frames(path: str, output_dir: str, topic: str | None = None, start: int = 0,
                  count: int = 1, max_points: int = 0, drop_noise: bool = True,
                  min_range: float = 0.0, progress=None, cancel=None) -> ImportResult:
    """解析雷达包的前 ``count`` 帧（从 ``start`` 开始），写入 output_dir。"""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise LidarImportError(f"文件不存在：{path}")
    start = max(int(start), 0)
    count = max(int(count), 1)
    output_dir = os.path.abspath(os.path.expanduser(output_dir))
    os.makedirs(output_dir, exist_ok=True)
    prefix = _safe_prefix(path)
    kind = "mcap" if path.lower().endswith(".mcap") else "pcap"

    files: list[str] = []
    written = 0
    total_points = 0
    warnings: list[str] = []

    def frames():
        if kind == "mcap":
            yield from mcap_reader.iter_frames(path, topic=topic, start=start,
                                               count=count, cancel=cancel)
        else:
            index = 0
            for frame in _iter_pcap_frames(path, cancel=cancel, drop_noise=drop_noise,
                                           min_range=min_range, warnings=warnings):
                if index >= start:
                    yield frame
                index += 1
                if index >= start + count:
                    return

    for number, frame in enumerate(frames()):
        if cancel is not None:
            check_cancel(cancel)
        if getattr(frame, "dual_return", False):
            warnings.append("双回波数据仅导入第一次回波。")
        target = os.path.join(output_dir, f"{prefix}_{number:06d}.npy")
        total_points += _save_frame(target, frame.xyz, frame.intensity, max_points)
        files.append(target)
        written += 1
        if progress is not None:
            progress(min(written / count, 1.0))

    if not files:
        raise LidarImportError("没有解析到任何点云帧。")

    manifest = {
        "source": os.path.basename(path),
        "kind": kind,
        "topic": topic or "",
        "start": start,
        "count": written,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "files": [os.path.basename(f) for f in files],
        "warnings": sorted(set(warnings)),
    }
    with open(os.path.join(output_dir, "frames.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    return ImportResult(files, written, total_points, sorted(set(warnings)))
