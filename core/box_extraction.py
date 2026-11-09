"""Non-destructive full-resolution extraction in the source coordinate system."""
from dataclasses import replace
import json
from pathlib import Path
import re
import tempfile
from uuid import uuid4

import numpy as np

from core.cancellation import check_cancel
from core.object_tracking import points_in_box


def extract_box(source, box, *, name=None, info="", cancel=None, progress=None):
    """Return an independent cloud, or None for an empty box; never display LOD."""
    chunks = []
    count = source.point_count
    step = 131_072
    for start in range(0, count, step):
        check_cancel(cancel)
        indices = np.flatnonzero(points_in_box(source.xyz[start:start + step], box))
        if len(indices):
            chunks.append(indices + start)
        if progress:
            progress(.8 * min(count, start + step) / count)
    check_cancel(cancel)
    if not chunks:
        return None
    indices = np.concatenate(chunks)
    result = replace(source, name=name or f"{source.name} · 框内点", path=None,
                     xyz=source.xyz[indices].copy(), fields=source.subset_fields(indices),
                     transform=np.array(source.transform, copy=True), info=info,
                     visible=True, visibility_pinned=False, load_state="ready")
    check_cancel(cancel)
    if progress:
        progress(1.)
    return result


def extract_track(source, stream_id, observations, directory, name, *, token=None,
                  cancel=None, progress=None):
    """Write one PCD per observation, publishing a complete folder atomically."""
    from core.io.exporters import export_entity
    from core.io.recording_types import FrameData

    observations = sorted(observations, key=lambda observation: observation.frame_index)
    if not observations:
        raise ValueError("当前目标没有可提取的框。")
    parent = Path(directory).resolve(strict=True)
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(". ")[:40] or "target"
    output = parent / f"target-{safe_name}-{uuid4().hex[:8]}"
    records = []

    def checkpoint():
        check_cancel(cancel)
        if source.cache_token() != token:
            raise ValueError("源文件已更改，未保存本次批量提取结果。")

    with tempfile.TemporaryDirectory(prefix=".pointlab-extract-", dir=parent) as temporary:
        staging = Path(temporary)
        for position, observation in enumerate(observations):
            checkpoint()
            index = observation.frame_index
            frame = source.read_frame(stream_id, index, cancel=cancel)
            if not isinstance(frame, FrameData) or frame.kind != "pointcloud":
                raise ValueError(f"原始帧 {index + 1} 没有可提取的点云。")
            cloud = extract_box(frame.value, observation.box, cancel=cancel)
            record = {"frame_index": index, "frame_number": index + 1,
                      "timestamp_ns": frame.timestamp_ns, "record_time_ns": frame.record_time_ns,
                      "manual": observation.manual, "box": observation.box.to_dict(),
                      "point_count": 0 if cloud is None else cloud.point_count, "file": None}
            if cloud is not None:
                stamp = frame.timestamp_ns if frame.timestamp_ns is not None else frame.record_time_ns
                record["file"] = f"frame_{index + 1:06d}_{stamp if stamp is not None else 'unknown'}.pcd"
                export_entity(cloud, str(staging / record["file"]))
            records.append(record)
            if progress:
                progress((position + 1) / len(observations) * .99)
        checkpoint()
        manifest = {"target": name, "source": str(source.path), "stream_id": stream_id,
                    "coordinate_system": "source", "frames": records}
        (staging / "index.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        checkpoint()
        staging.replace(output)
    if progress:
        progress(1.)
    return output
