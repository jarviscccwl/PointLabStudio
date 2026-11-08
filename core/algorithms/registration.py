"""配准算法：点对点/点到面 ICP、FPFH+RANSAC 全局配准。"""

from __future__ import annotations

from typing import Tuple

import numpy as np

from core.cancellation import CancelCb, check_cancel
from core.progress import ProgressCb


def apply_transform(xyz: np.ndarray, transform: np.ndarray) -> np.ndarray:
    """应用 4x4 变换到 (N,3) 点。"""
    xyz = np.asarray(xyz, dtype=np.float32)
    t = np.asarray(transform, dtype=np.float64)
    out = xyz @ t[:3, :3].T.astype(np.float32) + t[:3, 3].astype(np.float32)
    return out.astype(np.float32)


def _umeyama(src: np.ndarray, dst: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """最小二乘刚体变换（无缩放）：返回 R, t，使 dst ≈ R·src + t（Kabsch）。"""
    src_mean = src.mean(axis=0)
    dst_mean = dst.mean(axis=0)
    src_c = src - src_mean
    dst_c = dst - dst_mean
    cov = src_c.T @ dst_c / len(src)
    u, s, vt = np.linalg.svd(cov)
    d = np.sign(np.linalg.det(u @ vt))
    dmat = np.diag([1.0, 1.0, d])
    rotation = vt.T @ dmat @ u.T
    translation = dst_mean - rotation @ src_mean
    return rotation, translation


def icp(source: np.ndarray, target: np.ndarray, max_iterations: int = 30,
        tolerance: float = 1e-5, sample_limit: int = 80_000,
        trim_ratio: float = 0.9, seed: int = 0, progress: ProgressCb = None,
        cancel: CancelCb = None) -> Tuple[np.ndarray, float]:
    """点对点 ICP：把 source 对齐到 target。

    采用截尾对应点（默认丢弃最差 10% 的匹配）降低离群/遮挡影响；
    大点云会自动抽样做迭代。返回 (4x4 变换, 最终 RMSE)。
    """
    from scipy.spatial import cKDTree

    source = np.asarray(source, dtype=np.float32)
    target = np.asarray(target, dtype=np.float32)
    if len(source) < 10 or len(target) < 10:
        raise ValueError("点云点数太少，无法配准")

    rng = np.random.default_rng(seed)
    src = source if len(source) <= sample_limit else source[
        rng.choice(len(source), sample_limit, replace=False)]
    tgt = target if len(target) <= sample_limit else target[
        rng.choice(len(target), sample_limit, replace=False)]

    tree = cKDTree(tgt)
    transform = np.eye(4, dtype=np.float64)
    # 质心预对齐：先消除整体平移，ICP 只需收敛旋转与残差平移
    centroid_init = np.eye(4, dtype=np.float64)
    centroid_init[:3, 3] = tgt.mean(axis=0) - src.mean(axis=0)
    transform = centroid_init
    rmse = float("inf")
    prev_rmse = float("inf")

    for it in range(max_iterations):
        check_cancel(cancel)
        moved = apply_transform(src, transform).astype(np.float64)
        dist, idx = tree.query(moved, workers=-1)
        keep = np.ones(len(dist), dtype=bool)
        if trim_ratio < 1.0:
            threshold = float(np.quantile(dist, trim_ratio))
            keep = dist <= threshold
            if keep.sum() < 10:
                keep = np.ones(len(dist), dtype=bool)
        rotation, translation = _umeyama(moved[keep], tgt[idx][keep])
        step = np.eye(4, dtype=np.float64)
        step[:3, :3] = rotation
        step[:3, 3] = translation
        transform = step @ transform
        rmse = float(np.sqrt(np.mean(dist[keep] ** 2)))
        if progress is not None:
            progress((it + 1) / max_iterations)
        if it > 2 and abs(prev_rmse - rmse) < 1e-7:
            break
        prev_rmse = rmse
        if (np.linalg.norm(translation) < tolerance
                and np.linalg.norm(rotation - np.eye(3)) < tolerance and it > 3):
            break

    dist, _ = tree.query(apply_transform(src, transform), workers=-1)
    keep = dist <= np.quantile(dist, trim_ratio) if trim_ratio < 1.0 else np.ones(len(dist), bool)
    rmse = float(np.sqrt(np.mean(dist[keep] ** 2)))
    return transform, rmse


def estimate_normals(xyz: np.ndarray, k: int = 20,
                     progress: ProgressCb = None, cancel: CancelCb = None) -> np.ndarray:
    """用 k 近邻 PCA 估计法线，按“背离点云质心”统一朝向（适合单视角扫描）。"""
    from scipy.spatial import cKDTree

    xyz = np.asarray(xyz, dtype=np.float64)
    if len(xyz) < 3:
        raise ValueError("点云点数太少，无法估计法线")
    k = int(min(max(3, k), len(xyz)))
    tree = cKDTree(xyz)
    normals = np.empty_like(xyz, dtype=np.float32)
    center = xyz.mean(axis=0)
    for start in range(0, len(xyz), 20_000):
        check_cancel(cancel)
        points = xyz[start:start + 20_000]
        _, idx = tree.query(points, k=k, workers=-1)
        neighbors = xyz[idx]
        centered = neighbors - neighbors.mean(axis=1, keepdims=True)
        cov = np.einsum("nki,nkj->nij", centered, centered) / k
        _, vectors = np.linalg.eigh(cov)
        normal = vectors[:, :, 0]
        flip = np.einsum("ij,ij->i", normal, points - center) < 0
        normal[flip] *= -1.0
        normals[start:start + len(points)] = normal
        if progress is not None:
            progress((start + len(points)) / len(xyz))
    return normals


def icp_point_to_plane(source: np.ndarray, target: np.ndarray,
                       target_normals: np.ndarray | None = None,
                       max_iterations: int = 30, tolerance: float = 1e-6,
                       max_corr_distance: float = 1.0, sample_limit: int = 80_000,
                       trim_ratio: float = 0.9, seed: int = 0,
                       progress: ProgressCb = None,
                       cancel: CancelCb = None,
                       initial_transform: np.ndarray | None = None) -> Tuple[np.ndarray, float]:
    """点到面 ICP：最小化 n·(R·p + t - q) 的平方和。

    比点对点 ICP 收敛更快、对平面/墙面场景更稳。target_normals 为空时自动估计。
    """
    from scipy.spatial import cKDTree

    source = np.asarray(source, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64)
    if len(source) < 10 or len(target) < 10:
        raise ValueError("点云点数太少，无法配准")

    rng = np.random.default_rng(seed)
    src = source if len(source) <= sample_limit else source[
        rng.choice(len(source), sample_limit, replace=False)]
    target_indices = np.arange(len(target)) if len(target) <= sample_limit else rng.choice(
        len(target), sample_limit, replace=False)
    tgt = target[target_indices]
    if target_normals is None:
        tgt_n = estimate_normals(tgt, cancel=cancel)
    else:
        target_normals = np.asarray(target_normals, dtype=np.float64)
        if target_normals.shape != target.shape:
            raise ValueError("目标法线必须与目标点云逐点对应")
        tgt_n = target_normals[target_indices]

    tree = cKDTree(tgt)
    transform = np.eye(4, dtype=np.float64)
    if initial_transform is None:
        transform[:3, 3] = tgt.mean(axis=0) - src.mean(axis=0)
    else:
        transform = np.array(initial_transform, dtype=np.float64, copy=True)
        if transform.shape != (4, 4) or not np.isfinite(transform).all():
            raise ValueError("初始变换必须是有限的 4×4 矩阵")
    rmse = float("inf")

    for it in range(max_iterations):
        check_cancel(cancel)
        moved = apply_transform(src, transform).astype(np.float64)
        dist, idx = tree.query(moved, workers=-1)
        keep = dist <= max_corr_distance
        if trim_ratio < 1.0:
            keep &= dist <= float(np.quantile(dist[keep], trim_ratio)) if keep.any() else keep
        if keep.sum() < 6:
            break

        p = moved[keep]
        q = tgt[idx[keep]]
        n = tgt_n[idx[keep]]
        r = np.einsum("ij,ij->i", n, p - q)
        cross = np.cross(p, n)
        jac = np.hstack([cross, n])            # (m, 6)
        hess = jac.T @ jac
        grad = jac.T @ r
        try:
            delta = np.linalg.solve(hess + np.eye(6) * 1e-9, -grad)
        except np.linalg.LinAlgError:
            break

        # 旋转向量用 Rodrigues 公式精确转成旋转矩阵（比欧拉小角近似收敛快）
        rot_vec = delta[:3]
        theta = float(np.linalg.norm(rot_vec))
        if theta > 1e-12:
            k = rot_vec / theta
            kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
            rot = np.eye(3) + np.sin(theta) * kx + (1.0 - np.cos(theta)) * (kx @ kx)
        else:
            rot = np.eye(3)
        step = np.eye(4)
        step[:3, :3] = rot
        step[:3, 3] = delta[3:]
        transform = step @ transform

        rmse = float(np.sqrt(np.mean(r ** 2)))
        if progress is not None:
            progress((it + 1) / max_iterations)
        if np.linalg.norm(delta[:3]) < tolerance * 10 and np.linalg.norm(delta[3:]) < tolerance:
            break
    moved = apply_transform(src, transform).astype(np.float64)
    dist, idx = tree.query(moved, workers=-1)
    keep = dist <= max_corr_distance
    if trim_ratio < 1.0 and keep.any():
        keep &= dist <= np.quantile(dist[keep], trim_ratio)
    if keep.sum() < 6:
        raise ValueError("有效对应点不足；请增大对应距离或先执行全局配准")
    residual = np.einsum("ij,ij->i", tgt_n[idx[keep]], moved[keep] - tgt[idx[keep]])
    rmse = float(np.sqrt(np.mean(residual ** 2)))
    return transform, rmse


def global_registration(source: np.ndarray, target: np.ndarray,
                        voxel_size: float | None = None,
                        max_iterations: int = 400_000,
                        progress: ProgressCb = None,
                        cancel: CancelCb = None) -> Tuple[np.ndarray, float, float]:
    """FPFH + RANSAC 粗配准（Open3D），返回 (4x4 变换, fitness, inlier_rmse)。

    适用于两组点云没有初始位姿的情况；结果通常还需点到面 ICP 精修。
    """
    import open3d as o3d

    check_cancel(cancel)
    source = np.asarray(source, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64)
    if len(source) < 100 or len(target) < 100:
        raise ValueError("全局配准需要至少 100 个点")
    if voxel_size is None:
        diag = float(np.linalg.norm(target.max(axis=0) - target.min(axis=0)))
        voxel_size = max(diag / 200.0, 1e-3)
    radius = voxel_size * 5.0

    def _prep(points):
        pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points))
        down = pc.voxel_down_sample(voxel_size)
        down.estimate_normals(
            o3d.geometry.KDTreeSearchParamHybrid(radius=radius, max_nn=30))
        feat = o3d.pipelines.registration.compute_fpfh_feature(
            down, o3d.geometry.KDTreeSearchParamHybrid(radius=radius, max_nn=100))
        return down, feat

    src_down, src_feat = _prep(source)
    check_cancel(cancel)
    tgt_down, tgt_feat = _prep(target)
    check_cancel(cancel)
    distance = voxel_size * 1.5
    result = o3d.pipelines.registration.registration_ransac_based_on_feature_matching(
        src_down, tgt_down, src_feat, tgt_feat, True, distance,
        o3d.pipelines.registration.TransformationEstimationPointToPoint(False), 3,
        [o3d.pipelines.registration.CorrespondenceCheckerBasedOnEdgeLength(0.9),
         o3d.pipelines.registration.CorrespondenceCheckerBasedOnDistance(distance)],
        o3d.pipelines.registration.RANSACConvergenceCriteria(max_iterations, 0.999))
    if progress is not None:
        progress(1.0)
    return (np.asarray(result.transformation, dtype=np.float64),
            float(result.fitness), float(result.inlier_rmse))
