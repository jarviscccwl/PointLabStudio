"""Non-destructive full-resolution extraction in the source coordinate system."""
from dataclasses import replace
from pathlib import Path
import shutil
import tempfile

import numpy as np

from core.cancellation import OperationCancelled, check_cancel
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


def track_output_paths(target, observations):
    """A user-selected filename, with original frame numbers for a sequence."""
    target = Path(target).absolute()
    if target.suffix.lower() not in (".pcd", ".ply"):
        raise ValueError("请选择 PCD 或 PLY 文件。")
    indices = sorted(observation.frame_index for observation in observations)
    if not indices or len(set(indices)) != len(indices):
        raise ValueError("请选择有效且不重复的有框帧。")
    return tuple(target if len(indices) == 1 else target.with_name(
        f"{target.stem}_{index + 1:06d}{target.suffix.lower()}") for index in indices)


def extract_track(source, stream_id, observations, target, name, *, token=None,
                  cancel=None, progress=None, overwrite=False):
    """Publish only named cloud files; empty boxes do not produce files."""
    from core.io.exporters import export_entity
    from core.io.recording_types import FrameData
    from core.sequence_export import ExportRollbackError, publish_export_files

    observations = sorted(observations, key=lambda observation: observation.frame_index)
    if not observations:
        raise ValueError("当前目标没有可提取的框。")
    paths = track_output_paths(target, observations)
    parent = paths[0].parent.resolve(strict=True)
    for path in paths:
        if path.exists() and (not overwrite or not path.is_file()):
            raise FileExistsError(f"文件已存在：{path}")
    output = []

    def checkpoint():
        check_cancel(cancel)
        if source.cache_token() != token:
            raise ValueError("源文件已更改，未保存本次批量提取结果。")

    staging = Path(tempfile.mkdtemp(prefix=".pointlab-extract-", dir=parent))
    keep_recovery = False
    try:
        for position, (observation, path) in enumerate(zip(observations, paths)):
            checkpoint()
            index = observation.frame_index
            frame = source.read_frame(stream_id, index, cancel=cancel)
            if not isinstance(frame, FrameData) or frame.kind != "pointcloud":
                raise ValueError(f"原始帧 {index + 1} 没有可提取的点云。")
            cloud = extract_box(frame.value, observation.box, name=name, cancel=cancel)
            if cloud is not None:
                export_entity(cloud, str(staging / path.name))
                output.append(path)
            elif path.exists():
                raise ValueError(f"原始帧 {index + 1} 是空框，但已有同名文件 {path.name}。"
                                 "本次未保存，请使用新名称或调整此帧的框。")
            if progress:
                progress((position + 1) / len(observations) * .99)
        checkpoint()
        if output and not publish_export_files(staging, output, overwrite=overwrite, cancelled=cancel):
            raise OperationCancelled()
    except ExportRollbackError:
        keep_recovery = True
        raise
    finally:
        if not keep_recovery:
            shutil.rmtree(staging)
    if progress:
        progress(1.)
    return tuple(output)
