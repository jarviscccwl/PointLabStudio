"""内置处理命令：实现与参数元数据放在一处，pipeline / UI / CLI 共用。"""

from __future__ import annotations

import numpy as np

from core.algorithms import filters, segmentation
from core.cancellation import check_cancel
from core.color.colormaps import array_to_rgb
from core.commands.command import Command
from core.commands.spec import CommandSpec, ParamDef, get_spec, register
from core.geometry.ops import subsample_indices
from core.model.entities import CloudEntity, ScalarField


def _make_result(source: CloudEntity, xyz, fields: dict, info: str) -> CloudEntity:
    result = CloudEntity(name=source.name, path=None, xyz=xyz, fields=fields)
    result.color_mode = source.color_mode
    result.color_field = source.color_field
    result.color_range = source.color_range
    result.colormap = source.colormap
    result.point_size = source.point_size
    result.info = info
    if source.is_preview:
        result.loaded = source.loaded
        result.tier = source.tier
        result.info += f"；基于 {source.loaded:.0%} 预览"
    return result


def _apply_voxel(entity, params, progress, cancel):
    voxel = float(params["voxel"])
    colors = array_to_rgb(entity.field("rgb")) if entity.field("rgb") is not None else None
    intensity = entity.field("intensity")
    xyz, cols, inten = filters.voxel_downsample(
        entity.xyz, colors=colors, intensity=intensity, voxel=voxel,
        progress=progress, cancel=cancel)
    fields = {}
    if cols is not None:
        fields["rgb"] = ScalarField("rgb", cols, kind="color")
    if inten is not None:
        fields["intensity"] = ScalarField("intensity", inten)
    return _make_result(entity, xyz, fields, f"体素 {voxel:g}m（{len(xyz):,} 点）")


def _apply_random(entity, params, progress, cancel):
    count = int(params["count"])
    idx = subsample_indices(entity.point_count, count)
    if idx is None:
        return _make_result(entity, entity.xyz, entity.subset_fields(slice(None)),
                            f"随机采样保持全部 {entity.point_count:,} 点")
    return _make_result(entity, entity.xyz[idx], entity.subset_fields(idx),
                        f"随机 {len(idx):,} 点")


def _apply_sor(entity, params, progress, cancel):
    keep = filters.statistical_outlier_removal(
        entity.xyz, k=int(params["k"]), std_ratio=float(params["std"]),
        progress=progress, cancel=cancel)
    return _masked(entity, keep, f"SOR k={int(params['k'])} std={float(params['std']):g}")


def _apply_ror(entity, params, progress, cancel):
    keep = filters.radius_outlier_removal(
        entity.xyz, radius=float(params["radius"]),
        min_neighbors=int(params["min_pts"]), progress=progress, cancel=cancel)
    return _masked(entity, keep, f"ROR r={float(params['radius']):g}")


def _apply_crop(entity, params, progress, cancel):
    mn = [params["xmin"], params["ymin"], params["zmin"]]
    mx = [params["xmax"], params["ymax"], params["zmax"]]
    if any(lo > hi for lo, hi in zip(mn, mx)):
        raise ValueError("盒裁切：每个轴的 min 不能大于 max")
    keep = filters.crop_box(entity.xyz, mn, mx, progress=progress, cancel=cancel)
    return _masked(entity, keep, "盒裁切")


def _masked(entity, keep, info):
    keep_count = int(keep.sum())
    if keep_count <= 0:
        raise ValueError(f"{info}：没有保留任何点，请检查参数")
    return _make_result(entity, entity.xyz[keep], entity.subset_fields(keep),
                        f"{info}（{keep_count:,} 点）")


def _apply_plane(entity, params, progress, cancel):
    (normal, d), keep = segmentation.plane_ransac(
        entity.xyz, distance=float(params["dist"]),
        max_iterations=int(params["iters"]), progress=progress, cancel=cancel)
    info = (f"平面 n=({normal[0]:.3f},{normal[1]:.3f},{normal[2]:.3f}) d={d:.3f}｜"
            f"{normal[0]:.4f}x + {normal[1]:.4f}y + {normal[2]:.4f}z + {d:.4f} = 0")
    return _masked(entity, keep, info)


def _apply_ground(entity, params, progress, cancel):
    (normal, d), keep = segmentation.extract_ground(
        entity.xyz, distance=float(params["dist"]),
        max_iterations=int(params["iters"]), progress=progress, cancel=cancel)
    info = f"地面 z≈{-d / normal[2]:.3f}｜n=({normal[0]:.3f},{normal[1]:.3f},{normal[2]:.3f})"
    return _masked(entity, keep, info)


def _apply_dbscan(entity, params, progress, cancel):
    eps = float(params["eps"])
    labels = segmentation.dbscan(
        entity.xyz, eps=eps, min_samples=int(params["min_pts"]),
        progress=progress, cancel=cancel)
    clusters = int(labels.max()) + 1 if (labels >= 0).any() else 0
    if clusters == 0:
        raise ValueError("DBSCAN：没有找到簇，请增大邻域半径或降低最少点数")
    noise = int((labels < 0).sum())
    fields = entity.subset_fields(slice(None))
    fields["labels"] = ScalarField("labels", labels.astype(np.float32))
    result = _make_result(entity, entity.xyz, fields,
                          f"DBSCAN eps={eps:g} min={int(params['min_pts'])}｜"
                          f"{clusters} 簇，噪声 {noise:,} 点")
    result.color_mode = "label"
    return result


def _dist_def(default: float = 0.1) -> ParamDef:
    return ParamDef("dist", "距离阈值 (m)", default=default, minimum=1e-4, maximum=1e5, decimals=3)


def _iters_def() -> ParamDef:
    return ParamDef("iters", "RANSAC 迭代", kind="int", default=300, minimum=20, maximum=3000)


BUILTIN_SPECS = [
    CommandSpec("voxel", "体素降采样",
                (ParamDef("voxel", "体素大小 (m)", default=0.2, minimum=1e-4, maximum=1e5,
                          decimals=3),),
                _apply_voxel, "每格取质心，颜色/强度取均值"),
    CommandSpec("random", "随机采样",
                (ParamDef("count", "目标点数", kind="int", default=100_000,
                          minimum=1, maximum=2_000_000_000),),
                _apply_random, "均匀随机采样到指定点数"),
    CommandSpec("sor", "统计滤波 SOR",
                (ParamDef("k", "邻域点数 k", kind="int", default=20, minimum=3, maximum=200),
                 ParamDef("std", "标准差倍数", default=2.0, minimum=0.1, maximum=10.0,
                          decimals=2)),
                _apply_sor, "剔除离群点"),
    CommandSpec("ror", "半径滤波 ROR",
                (ParamDef("radius", "半径 (m)", default=0.5, minimum=1e-4, maximum=1e5,
                          decimals=3),
                 ParamDef("min_pts", "最少邻居数", kind="int", default=5, minimum=1,
                          maximum=200)),
                _apply_ror, "剔除孤立噪点"),
    CommandSpec("crop", "盒裁切",
                tuple(ParamDef(key, f"{axis} {side}", default=default, decimals=2)
                      for key, axis, side, default in (
                          ("xmin", "X", "min", -1e9), ("xmax", "X", "max", 1e9),
                          ("ymin", "Y", "min", -1e9), ("ymax", "Y", "max", 1e9),
                          ("zmin", "Z", "min", -1e9), ("zmax", "Z", "max", 1e9))),
                _apply_crop, "保留盒内的点"),
    CommandSpec("plane", "平面分割", (_dist_def(0.1), _iters_def()), _apply_plane,
                "RANSAC 拟合最大平面并保留平面内点"),
    CommandSpec("ground", "地面提取", (_dist_def(0.15), _iters_def()), _apply_ground,
                "拟合水平地面并保留地面点"),
    CommandSpec("dbscan", "DBSCAN 聚类",
                (ParamDef("eps", "邻域半径 (m)", default=0.5, minimum=1e-4, maximum=1e5,
                          decimals=3),
                 ParamDef("min_pts", "最少点数", kind="int", default=10, minimum=2,
                          maximum=1000)),
                _apply_dbscan, "按密度聚类，噪声点标签为 -1"),
]

for _spec in BUILTIN_SPECS:
    register(_spec)


def run_command(entity: CloudEntity, command, progress=None, cancel=None) -> CloudEntity:
    """执行一条命令（Command 或旧式 {'op','params'} 字典）。"""
    command = Command.from_step(command)
    spec = get_spec(command.op)
    check_cancel(cancel)
    return spec.apply(entity, spec.normalize(command.params), progress, cancel)
