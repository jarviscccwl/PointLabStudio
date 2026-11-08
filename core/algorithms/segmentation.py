"""分割算法：平面 RANSAC、地面提取、DBSCAN 聚类（numpy / scipy）。"""

from __future__ import annotations

from typing import Tuple

import numpy as np

from core.cancellation import CancelCb, check_cancel
from core.progress import ProgressCb, report_progress as _report


def plane_ransac(xyz: np.ndarray, distance: float = 0.1, max_iterations: int = 300,
                 seed: int = 0, sample_limit: int = 100_000,
                 progress: ProgressCb = None, cancel: CancelCb = None) -> Tuple[Tuple[np.ndarray, float], np.ndarray]:
    """随机采样一致性拟合最大平面。

    返回 ((normal(3,), d), 内点掩码)，平面方程 n·p + d = 0。
    在大数据上先用采样点迭代拟合，最后对全量点计算内点。
    """
    xyz = np.asarray(xyz, dtype=np.float32)
    n = len(xyz)
    if n < 3:
        raise ValueError("点数不足以拟合平面")

    rng = np.random.default_rng(seed)
    fit_points = xyz
    if n > sample_limit:
        fit_points = xyz[rng.choice(n, sample_limit, replace=False)]

    m = len(fit_points)
    best_model = None
    best_count = -1
    for it in range(max_iterations):
        if it % 8 == 0:
            check_cancel(cancel)
        idx = rng.choice(m, 3, replace=False)
        p0, p1, p2 = fit_points[idx]
        normal = np.cross(p1 - p0, p2 - p0)
        norm = float(np.linalg.norm(normal))
        if norm < 1e-9:
            continue
        normal = normal / norm
        d = -float(normal @ p0)
        dist = np.abs(fit_points @ normal + d)
        count = int((dist < distance).sum())
        if count > best_count:
            best_count, best_model = count, (normal.astype(np.float32), d)
        _report(progress, (it + 1) / max_iterations * 0.8)

    if best_model is None:
        raise ValueError("平面拟合失败")

    normal, d = best_model
    dist = np.abs(xyz @ normal + d)
    mask = dist < distance
    _report(progress, 1.0)
    return (normal, d), mask


def extract_ground(xyz: np.ndarray, distance: float = 0.15, max_iterations: int = 300,
                   progress: ProgressCb = None, cancel: CancelCb = None) -> Tuple[Tuple[np.ndarray, float], np.ndarray]:
    """地面提取：拟合最大平面，并要求法向接近竖直（|n_z| 较大）。"""
    xyz = np.asarray(xyz, dtype=np.float32)
    n = len(xyz)
    rng = np.random.default_rng(1)
    fit_points = xyz if n <= 100_000 else xyz[rng.choice(n, 100_000, replace=False)]
    m = len(fit_points)

    best_model = None
    best_score = -1
    for it in range(max_iterations):
        if it % 8 == 0:
            check_cancel(cancel)
        idx = rng.choice(m, 3, replace=False)
        p0, p1, p2 = fit_points[idx]
        normal = np.cross(p1 - p0, p2 - p0)
        norm = float(np.linalg.norm(normal))
        if norm < 1e-9:
            continue
        normal = normal / norm
        if abs(float(normal[2])) < 0.85:   # 只接受接近水平的面
            continue
        if normal[2] < 0:
            normal = -normal
        d = -float(normal @ p0)
        dist = np.abs(fit_points @ normal + d)
        score = int((dist < distance).sum())
        if score > best_score:
            best_score, best_model = score, (normal.astype(np.float32), d)
        _report(progress, (it + 1) / max_iterations * 0.8)

    if best_model is None:
        raise ValueError("未找到水平平面（地面），可尝试增大距离阈值")

    normal, d = best_model
    dist = np.abs(xyz @ normal + d)
    mask = dist < distance
    _report(progress, 1.0)
    return (normal, d), mask


def dbscan(xyz: np.ndarray, eps: float = 0.5, min_samples: int = 10,
           max_points: int = 150_000, progress: ProgressCb = None,
           cancel: CancelCb = None) -> np.ndarray:
    """欧式聚类（DBSCAN），返回每个点的簇标签（-1 为噪声）。

    点数超过 max_points 时先抽样聚类，再按最近邻把标签传播回全部点。
    """
    from scipy.spatial import cKDTree

    xyz = np.asarray(xyz, dtype=np.float32)
    n = len(xyz)
    if n == 0:
        return np.zeros(0, dtype=np.int32)

    rng = np.random.default_rng(2)
    if n > max_points:
        sample_idx = rng.choice(n, max_points, replace=False)
        sample_idx.sort()
        work = xyz[sample_idx]
    else:
        sample_idx = None
        work = xyz

    tree = cKDTree(work)
    m = len(work)
    labels = np.full(m, -1, dtype=np.int32)
    visited = np.zeros(m, dtype=bool)
    queued = np.zeros(m, dtype=bool)
    cluster_id = 0

    for i in range(m):
        if i % 64 == 0:
            check_cancel(cancel)
        if visited[i]:
            continue
        visited[i] = True
        neighbors = tree.query_ball_point(work[i], eps)
        if len(neighbors) < min_samples:
            continue
        cluster_id += 1
        labels[i] = cluster_id
        queue = list(neighbors)
        queued[neighbors] = True
        expanded = 0
        while queue:
            if expanded % 64 == 0:
                check_cancel(cancel)
            expanded += 1
            j = queue.pop()
            if not visited[j]:
                visited[j] = True
                n2 = tree.query_ball_point(work[j], eps)
                if len(n2) >= min_samples:
                    new = np.asarray(n2, dtype=np.int64)
                    new = new[~queued[new]]
                    queued[new] = True
                    queue.extend(new.tolist())
            if labels[j] == -1:
                labels[j] = cluster_id
        if progress is not None and i % 2000 == 0:
            _report(progress, 0.9 * i / m)

    check_cancel(cancel)
    if sample_idx is not None:
        # 最近邻把标签传播到全集
        full_labels = np.full(n, -1, dtype=np.int32)
        _, nn = tree.query(xyz, workers=-1)
        full_labels = labels[nn].astype(np.int32)
        labels = full_labels
    _report(progress, 1.0)
    return labels


def suggest_eps(xyz: np.ndarray, min_samples: int = 10, sample: int = 4000,
                cancel: CancelCb = None) -> float:
    """按 k 近邻平均距离估计 DBSCAN 邻域半径（比按包围盒比例更稳）。

    取 min_samples 个近邻（不含自身）的平均距离 × 1.5，作为 eps 的推荐值。
    """
    from scipy.spatial import cKDTree

    check_cancel(cancel)
    xyz = np.asarray(xyz, dtype=np.float32)
    n = len(xyz)
    if n < 3:
        return 0.1
    k = int(min(max(min_samples, 2), n - 1))
    if n > sample:
        idx = np.linspace(0, n - 1, sample).astype(np.int64)
        query_points = xyz[idx]
    else:
        query_points = xyz
    tree = cKDTree(xyz.astype(np.float64))
    check_cancel(cancel)
    dists, _ = tree.query(query_points.astype(np.float64), k=k + 1, workers=-1)
    if np.ndim(dists) == 1:
        dists = dists[:, None]
    kth = dists[:, -1]
    kth = kth[np.isfinite(kth)]
    if len(kth) == 0:
        return 0.1
    return float(max(np.mean(kth) * 1.5, 1e-4))
