"""点云滤波算法（numpy / scipy 实现，纯 CPU，无 UI 依赖）。

统一约定：返回「保留掩码」或「降采样后的数组」，由命令层组装成新实体。
进度回调 progress(fraction) 用于长任务的界面反馈。
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

from core.cancellation import CancelCb, check_cancel
from core.progress import ProgressCb, report_progress as _report


def voxel_downsample(xyz: np.ndarray, colors: Optional[np.ndarray] = None,
                     intensity: Optional[np.ndarray] = None,
                     voxel: float = 0.2,
                     progress: ProgressCb = None,
                     cancel: CancelCb = None) -> Tuple[np.ndarray, Optional[np.ndarray], Optional[np.ndarray]]:
    """体素降采样：每格取质心，颜色/强度取均值。返回 (xyz, colors, intensity)。"""
    xyz = np.asarray(xyz, dtype=np.float32)
    voxel = float(max(voxel, 1e-6))
    n = len(xyz)
    if n == 0:
        return xyz, colors, intensity

    check_cancel(cancel)
    mn = xyz.min(axis=0)
    q = np.floor((xyz - mn) / voxel).astype(np.int64) + 1
    span = q.max(axis=0) + 2
    key = (q[:, 0] * span[1] + q[:, 1]) * span[2] + q[:, 2]
    _report(progress, 0.1)

    uniq, inverse = np.unique(key, return_inverse=True)
    n_out = int(len(uniq))
    counts = np.bincount(inverse, minlength=n_out).astype(np.float64)
    _report(progress, 0.4)

    centers = np.empty((n_out, 3), dtype=np.float32)
    for axis in range(3):
        centers[:, axis] = np.bincount(inverse, weights=xyz[:, axis], minlength=n_out) / counts
    _report(progress, 0.6)

    out_colors = None
    if colors is not None:
        colors = np.asarray(colors, dtype=np.float32)
        out_colors = np.empty((n_out, 3), dtype=np.float32)
        for axis in range(3):
            out_colors[:, axis] = np.bincount(inverse, weights=colors[:, axis], minlength=n_out) / counts

    out_intensity = None
    if intensity is not None:
        intensity = np.asarray(intensity, dtype=np.float32).reshape(-1)
        out_intensity = (np.bincount(inverse, weights=intensity, minlength=n_out) / counts).astype(np.float32)

    _report(progress, 1.0)
    return centers, out_colors, out_intensity


def statistical_outlier_removal(xyz: np.ndarray, k: int = 20, std_ratio: float = 2.0,
                                progress: ProgressCb = None,
                                chunk: int = 500_000,
                                cancel: CancelCb = None) -> np.ndarray:
    """统计离群点去除（SOR）：返回保留掩码。"""
    from scipy.spatial import cKDTree

    xyz = np.asarray(xyz, dtype=np.float32)
    n = len(xyz)
    if n <= k:
        return np.ones(n, dtype=bool)

    tree = cKDTree(xyz)
    mean_dist = np.empty(n, dtype=np.float32)
    for start in range(0, n, chunk):
        check_cancel(cancel)
        stop = min(start + chunk, n)
        d, _ = tree.query(xyz[start:stop], k=k + 1, workers=-1)
        mean_dist[start:stop] = d[:, 1:].mean(axis=1)
        _report(progress, stop / n)

    mu, sigma = float(mean_dist.mean()), float(mean_dist.std())
    return mean_dist <= (mu + std_ratio * sigma)


def radius_outlier_removal(xyz: np.ndarray, radius: float = 0.5, min_neighbors: int = 5,
                           progress: ProgressCb = None,
                           chunk: int = 200_000,
                           cancel: CancelCb = None) -> np.ndarray:
    """半径离群点去除（ROR）：返回保留掩码。"""
    from scipy.spatial import cKDTree

    xyz = np.asarray(xyz, dtype=np.float32)
    n = len(xyz)
    if n == 0:
        return np.zeros(0, dtype=bool)

    tree = cKDTree(xyz)
    counts = np.empty(n, dtype=np.int32)
    for start in range(0, n, chunk):
        check_cancel(cancel)
        stop = min(start + chunk, n)
        counts[start:stop] = tree.query_ball_point(
            xyz[start:stop], radius, workers=-1, return_length=True)
        _report(progress, stop / n)
    return counts >= int(min_neighbors)


def crop_box(xyz: np.ndarray, mn, mx, progress: ProgressCb = None,
             cancel: CancelCb = None) -> np.ndarray:
    """盒裁切：返回盒内点的保留掩码。"""
    check_cancel(cancel)
    xyz = np.asarray(xyz, dtype=np.float32)
    mn = np.asarray(mn, dtype=np.float32)
    mx = np.asarray(mx, dtype=np.float32)
    mask = np.all((xyz >= mn) & (xyz <= mx), axis=1)
    _report(progress, 1.0)
    return mask
