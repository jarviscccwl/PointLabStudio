"""格式读取器：返回 CloudEntity（迁移并升级自 vis_point_cloud.py 的 load_* 系列）。

新增能力：
- .bin 分块读取（带进度回调），支持超大文件
- .bin 预览读取（首块 + 全文件点数估算），支撑 Auto-Ingest 先粗后精
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np

# 注意：open3d 必须在这里（主线程）提前导入。
# 若首次导入发生在后台加载线程中，会与主线程的 wgpu/D3D12 渲染线程死锁（实测）。
try:
    import open3d as o3d
except Exception:  # noqa: BLE001
    o3d = None

from core.cancellation import CancelCb, check_cancel
from core.color.colormaps import array_to_rgb
from core.geometry.ops import sanitize_cloud
from core.io.bin_reader import infer_bin_dim, parse_bin_dim
from core.model.entities import CloudEntity, ScalarField, TIER_L, TIER_M, TIER_S
from core.progress import ProgressCb

# lcm(3,4,5,6,7,8)：探测块长度取该倍数，保证任意候选维度都能整除
_LCM = 840


def _probe_floats(path: str, dtype: str, count: int) -> np.ndarray:
    """读取前 count 个 float 作维度探测，长度对齐到所有候选维度的公倍数。"""
    itemsize = np.dtype(dtype).itemsize
    total = os.path.getsize(path) // itemsize
    count = min(count, total)
    if count < total:
        count -= count % _LCM
    if count < 3:
        raise ValueError("bin 文件太小")
    with open(path, "rb") as f:
        return np.fromfile(f, dtype=dtype, count=count)


@dataclass
class ReadOptions:
    """读取参数（对应原脚本的 CLI 参数）。"""

    bin_dim: str = "auto"
    dtype: str = "float32"
    npy_key: Optional[str] = None
    color_file: Optional[str] = None
    color_key: Optional[str] = None
    color_bin_dim: Optional[int] = None
    max_points: int = 0
    extra: dict = field(default_factory=dict)


# ---------------- 低层读取 ----------------

def _read_bin_chunked(path: str, dim: int, dtype: str = "float32",
                      progress: Optional[ProgressCb] = None,
                      cancel: CancelCb = None) -> np.ndarray:
    """分块读取 .bin 为 (N, dim)，带进度回调。"""
    itemsize = np.dtype(dtype).itemsize
    total = os.path.getsize(path) // itemsize
    if total < 3:
        raise ValueError("bin 文件太小")

    if total % dim != 0:
        total = (total // dim) * dim
    n_rows = total // dim

    out = np.empty((n_rows, dim), dtype=np.dtype(dtype))
    chunk_rows = max(1, 4_000_000 // dim)
    filled = 0
    with open(path, "rb") as f:
        while filled < n_rows:
            check_cancel(cancel)
            k = min(chunk_rows, n_rows - filled)
            buf = np.fromfile(f, dtype=dtype, count=k * dim)
            if buf.size < k * dim:
                buf = buf[: (buf.size // dim) * dim]
                out = out[: filled + buf.size // dim]
                k = buf.size // dim
                if k == 0:
                    break
            out[filled: filled + k] = buf.reshape(k, dim)
            filled += k
            if progress is not None:
                progress(filled / n_rows)
    return out


def _read_bin_preview(path: str, dim: int, dtype: str = "float32",
                      max_rows: int = 600_000) -> Tuple[np.ndarray, int]:
    """读取 .bin 前 max_rows 行作预览，并返回全文件估算点数。"""
    itemsize = np.dtype(dtype).itemsize
    total = os.path.getsize(path) // itemsize
    n_rows = (total // dim) if dim else 0
    if n_rows == 0:
        raise ValueError("bin 文件太小")
    mapped = np.memmap(path, dtype=dtype, mode="r", shape=(n_rows, dim))
    indices = np.linspace(0, n_rows - 1, min(max_rows, n_rows), dtype=np.int64)
    return np.array(mapped[indices]), n_rows


def load_array_file(path: str, key: Optional[str] = None):
    """npy/npz/txt/csv/bin 数组读取（迁移自原脚本）。"""
    ext = os.path.splitext(path)[1].lower()

    if ext == ".npy":
        obj = np.load(path, allow_pickle=True)
        if isinstance(obj, np.ndarray) and obj.dtype == object:
            try:
                obj = obj.item()
            except Exception:
                pass
        if isinstance(obj, dict):
            keys = [key] if key else ["points", "pointcloud", "point_cloud", "pc", "xyz", "lidar", "data",
                                      "arr_0", "colors", "color", "rgb", "rgba", "labels", "label"]
            for k in keys:
                if k in obj:
                    return np.asarray(obj[k]), k
            raise KeyError(f"找不到 key，已有 keys={list(obj.keys())}")
        return np.asarray(obj), None

    if ext == ".npz":
        with np.load(path, allow_pickle=True) as obj:
            keys = [key] if key else ["points", "pointcloud", "point_cloud", "pc", "xyz", "lidar", "data",
                                      "arr_0", "colors", "color", "rgb", "rgba", "labels", "label"]
            for k in keys:
                if k in obj.files:
                    return np.asarray(obj[k]), k
            if key or not obj.files:
                raise KeyError(f"找不到 key，已有 keys={obj.files}")
            return np.asarray(obj[obj.files[0]]), obj.files[0]

    if ext in [".txt", ".csv"]:
        return np.loadtxt(path, dtype=np.float32, delimiter="," if ext == ".csv" else None), None

    if ext == ".bin":
        return np.fromfile(path, dtype=np.float32), None

    raise ValueError(f"不支持的数组文件类型: {ext}")


# ---------------- 实体构建 ----------------

def _build_entity(name: str, path: str, xyz: np.ndarray, dim: int,
                  info: str = "", debug: str = "",
                  intensity: Optional[np.ndarray] = None,
                  rgb: Optional[np.ndarray] = None,
                  tier: str = TIER_S, loaded: float = 1.0) -> CloudEntity:
    xyz, intensity, rgb = sanitize_cloud(xyz, intensity, rgb)
    if xyz.shape[0] == 0:
        raise ValueError("点云为空（无非有限点被保留）")

    fields = {}
    if intensity is not None:
        fields["intensity"] = ScalarField("intensity", np.asarray(intensity, dtype=np.float32).reshape(-1))
    if rgb is not None:
        fields["rgb"] = ScalarField("rgb", np.asarray(rgb, dtype=np.float32).reshape(-1, 3), kind="color")

    return CloudEntity(
        name=name, path=path, xyz=xyz, fields=fields,
        dim_hint=dim, info=info, debug=debug, tier=tier, loaded=loaded,
    )


# ---------------- 各格式读取 ----------------

def read_bin(path: str, options: ReadOptions, progress: Optional[ProgressCb] = None,
             cancel: CancelCb = None) -> CloudEntity:
    dim, debug = None, ""
    dtype = options.dtype

    itemsize = np.dtype(dtype).itemsize
    if os.path.getsize(path) % itemsize:
        raise ValueError("bin 文件字节数不是 dtype 大小的整数倍")
    total = os.path.getsize(path) // itemsize
    user_dim = parse_bin_dim(options.bin_dim)

    if user_dim is not None:
        dim = user_dim
        if dim < 3 or total % dim != 0:
            raise ValueError(f"无法按 bin_dim={user_dim} reshape，float 数量={total}")
    else:
        # 用探测块做维度推断（不整读文件）
        probe = _probe_floats(path, dtype, 200_000)
        dim, debug = infer_bin_dim(path, probe, None, total_values=total)

    points = _read_bin_chunked(path, dim, dtype, progress, cancel)
    xyz = points[:, :3]
    intensity = points[:, 3] if dim >= 4 else None
    rgb = array_to_rgb(points[:, 3:6]) if dim >= 6 else None
    return _build_entity(os.path.basename(path), path, xyz, dim,
                         info=f"bin dim={dim}", debug=debug, intensity=intensity, rgb=rgb)


def read_bin_preview(path: str, options: ReadOptions, max_rows: int = 600_000) -> Tuple[CloudEntity, int]:
    """返回 (预览实体, 全文件估算点数)。"""
    dtype = options.dtype
    user_dim = parse_bin_dim(options.bin_dim)
    total = os.path.getsize(path) // np.dtype(dtype).itemsize
    if os.path.getsize(path) % np.dtype(dtype).itemsize:
        raise ValueError("bin 文件字节数不是 dtype 大小的整数倍")
    if user_dim is not None:
        if user_dim < 3 or total % user_dim:
            raise ValueError(f"无法按 bin_dim={user_dim} reshape，float 数量={total}")
        dim, debug = user_dim, f"dim={user_dim}"
    else:
        probe = _probe_floats(path, dtype, 200_000)
        dim, debug = infer_bin_dim(path, probe, None, total_values=total)

    preview, n_total = _read_bin_preview(path, dim, dtype, max_rows=max_rows)
    xyz = preview[:, :3]
    intensity = preview[:, 3] if dim >= 4 else None
    rgb = array_to_rgb(preview[:, 3:6]) if dim >= 6 else None

    entity = _build_entity(os.path.basename(path), path, xyz, dim,
                           info=f"bin dim={dim}（预览）", debug=debug,
                           intensity=intensity, rgb=rgb,
                           tier=TIER_M, loaded=max(0.01, min(1.0, preview.shape[0] / max(n_total, 1))))
    return entity, n_total


def read_stream_bin(path: str, options: ReadOptions, budget: int = 2_000_000,
                    progress: Optional[ProgressCb] = None,
                    cancel: Optional[CancelCb] = None) -> CloudEntity:
    """超大 .bin：八叉树流式采样，不把全量点读入内存。"""
    from core.io.octree import build_octree

    dtype = options.dtype
    size = os.path.getsize(path)
    itemsize = np.dtype(dtype).itemsize
    if size % itemsize:
        raise ValueError("bin 文件字节数不是 dtype 大小的整数倍")
    total_values = size // itemsize
    user_dim = parse_bin_dim(options.bin_dim)
    if user_dim is not None:
        if user_dim < 3 or total_values % user_dim:
            raise ValueError(f"无法按 bin_dim={user_dim} reshape，float 数量={total_values}")
        dim, debug = user_dim, f"dim={user_dim}"
    else:
        probe = _probe_floats(path, dtype, 200_000)
        dim, debug = infer_bin_dim(path, probe, None, total_values=total_values)

    index = build_octree(path, dim=dim, dtype=dtype, depth=5, per_cell=200,
                         progress=progress, cancel=cancel)
    rows = index.sample(budget)
    xyz = rows[:, :3]
    intensity = rows[:, 3] if dim >= 4 else None
    rgb = array_to_rgb(rows[:, 3:6]) if dim >= 6 else None
    loaded = float(len(xyz)) / max(index.total, 1)
    entity = _build_entity(
        os.path.basename(path), path, xyz, dim,
        info=f"八叉树流式 · {index.cell_count:,} 单元 · {index.total:,} 点 → {len(xyz):,} 采样",
        debug=debug, tier=TIER_L, loaded=loaded, intensity=intensity, rgb=rgb)
    entity.debug = f"{debug}; octree={index.stats()}"
    return entity


def read_npy_npz(path: str, options: ReadOptions, progress: Optional[ProgressCb] = None) -> CloudEntity:
    array, used_key = load_array_file(path, options.npy_key)
    array = np.asarray(array)

    if array.ndim == 1:
        dim, debug = infer_bin_dim(path, array.astype(np.float32), parse_bin_dim(options.bin_dim))
        array = array.reshape(-1, dim)
    else:
        if array.ndim != 2 or array.shape[1] < 3:
            raise ValueError(f"npy/npz 不是 Nx3+: shape={array.shape}")
        dim, debug = array.shape[1], f"key={used_key}"

    xyz = array[:, :3]
    intensity = array[:, 3] if dim >= 4 else None
    rgb = array_to_rgb(array[:, 3:6]) if dim >= 6 else None
    return _build_entity(os.path.basename(path), path, xyz, dim,
                         info=f"array dim={dim}", debug=debug, intensity=intensity, rgb=rgb)


def read_ascii(path: str, options: ReadOptions, progress: Optional[ProgressCb] = None) -> CloudEntity:
    ext = os.path.splitext(path)[1].lower()
    from core.io import native_io
    return native_io.read_text(path, ext)


def read_native(path: str) -> CloudEntity:
    """原生解析 PLY / PCD / 文本，保留 intensity、labels、法线与颜色。"""
    from core.io import native_io

    ext = os.path.splitext(path)[1].lower()
    if ext == ".ply":
        return native_io.read_ply(path)
    if ext == ".pcd":
        return native_io.read_pcd(path)
    return native_io.read_text(path, ext)


def read_open3d_cloud(path: str, options: ReadOptions, progress: Optional[ProgressCb] = None) -> CloudEntity:
    if o3d is None:
        raise RuntimeError("open3d 未安装或导入失败，无法读取该格式")

    point_cloud = o3d.io.read_point_cloud(path)
    if point_cloud.is_empty():
        raise ValueError("Open3D 读取为空")

    xyz = np.asarray(point_cloud.points, dtype=np.float32)
    colors = np.asarray(point_cloud.colors, dtype=np.float32)
    rgb = colors if colors.size else None
    return _build_entity(os.path.basename(path), path, xyz, 3, info="open3d", rgb=rgb)


def load_color_file(path: str, n_points: int, key=None, color_bin_dim=None):
    """额外颜色文件（迁移自原脚本）。"""
    array, _ = load_array_file(path, key)

    if array.ndim == 1 and array.size != n_points:
        if array.size == n_points * 3:
            array = array.reshape(n_points, 3)
        elif array.size == n_points * 4:
            array = array.reshape(n_points, 4)
        elif color_bin_dim and array.size % color_bin_dim == 0:
            array = array.reshape(-1, color_bin_dim)
        else:
            raise ValueError(f"颜色数量不匹配: color_size={array.size}, points={n_points}")

    if array.ndim == 2 and array.shape[0] != n_points:
        raise ValueError(f"颜色行数不匹配: color_shape={array.shape}, points={n_points}")

    return array_to_rgb(array)


# ---------------- 统一入口 ----------------

def read_cloud(path: str, options: ReadOptions, progress: Optional[ProgressCb] = None,
               cancel: CancelCb = None) -> CloudEntity:
    """全量读取一个点云文件（S 级同步 / M·L 级后台线程调用）。"""
    ext = os.path.splitext(path)[1].lower()

    if ext == ".bin":
        cloud = read_bin(path, options, progress, cancel)
    elif ext in [".npy", ".npz"]:
        cloud = read_npy_npz(path, options, progress)
    elif ext in [".txt", ".csv"]:
        cloud = read_ascii(path, options, progress)
    elif ext == ".las":
        from core.io.las_io import read_las
        cloud = read_las(path)
    elif ext == ".laz":
        from core.io.las_io import read_laz
        cloud = read_laz(path)
    elif ext == ".e57":
        from core.io.las_io import read_e57
        cloud = read_e57(path)
    elif ext in [".pcd", ".ply", ".xyz", ".xyzn", ".xyzrgb", ".pts"]:
        try:
            cloud = read_native(path)
        except Exception:  # noqa: BLE001 - 非标准变体交给 Open3D 兜底
            cloud = read_open3d_cloud(path, options, progress)
    else:
        raise ValueError(f"不支持的文件类型: {ext}")

    if options.color_file:
        cloud.fields["rgb"] = ScalarField(
            "rgb", load_color_file(options.color_file, cloud.xyz.shape[0],
                                   options.color_key, options.color_bin_dim), kind="color")
        cloud.info += " + color-file"

    if options.max_points and options.max_points > 0 and cloud.xyz.shape[0] > options.max_points:
        from core.geometry.ops import sample_cloud
        index = sample_cloud(cloud.xyz, options.max_points)[1]
        if index is not None:
            cloud.xyz = cloud.xyz[index]
            for f in cloud.fields.values():
                f.data = f.data[index]
            cloud.info += f"（采样至 {options.max_points}）"

    return cloud


def read_preview(path: str, options: ReadOptions) -> Optional[Tuple[CloudEntity, int]]:
    """快速预览：返回 (预览实体, 估算总点数)；不支持预览的格式返回 None。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".bin":
        return read_bin_preview(path, options)
    return None
