"""色彩映射表系统（迁移并升级 vis_point_cloud.py 的 scalar_to_rgb）。

设计：任意色彩映射 = 一组 (位置, 颜色) 断点 + 分段线性插值；
标量归一化沿用原脚本的 2–98 百分位鲁棒归一。
"""

from __future__ import annotations

from typing import Optional

import numpy as np

# 断点式色彩映射：[(位置 0..1, (r,g,b)), ...]
# turbo 为 Google Turbo 的 17 断点近似（0~1 线性插值）。
COLORMAPS: dict = {
    "default": [
        (0.00, (0.08, 0.20, 0.60)),
        (0.50, (0.10, 0.85, 0.75)),
        (1.00, (0.98, 0.92, 0.22)),
    ],
    "turbo": [
        (0.000, (0.190, 0.072, 0.232)),
        (0.063, (0.272, 0.375, 0.833)),
        (0.125, (0.275, 0.496, 0.969)),
        (0.188, (0.267, 0.629, 1.000)),
        (0.250, (0.288, 0.772, 1.000)),
        (0.313, (0.341, 0.871, 1.000)),
        (0.375, (0.418, 0.969, 1.000)),
        (0.438, (0.496, 1.000, 0.939)),
        (0.500, (0.585, 1.000, 0.846)),
        (0.563, (0.668, 1.000, 0.752)),
        (0.625, (0.744, 1.000, 0.659)),
        (0.688, (0.820, 1.000, 0.662)),
        (0.750, (0.872, 1.000, 0.749)),
        (0.813, (0.917, 1.000, 0.826)),
        (0.875, (0.952, 1.000, 0.893)),
        (0.938, (0.991, 1.000, 0.970)),
        (1.000, (1.000, 1.000, 0.999)),
    ],
    "viridis": [
        (0.000, (0.267, 0.005, 0.329)),
        (0.125, (0.283, 0.141, 0.458)),
        (0.250, (0.254, 0.265, 0.530)),
        (0.375, (0.207, 0.372, 0.553)),
        (0.500, (0.164, 0.471, 0.558)),
        (0.625, (0.128, 0.567, 0.551)),
        (0.750, (0.135, 0.659, 0.518)),
        (0.875, (0.267, 0.749, 0.441)),
        (1.000, (0.478, 0.821, 0.318)),
    ],
    "inferno": [
        (0.000, (0.001, 0.000, 0.014)),
        (0.125, (0.130, 0.029, 0.129)),
        (0.250, (0.317, 0.072, 0.251)),
        (0.375, (0.491, 0.121, 0.350)),
        (0.500, (0.650, 0.191, 0.389)),
        (0.625, (0.791, 0.292, 0.377)),
        (0.750, (0.909, 0.427, 0.320)),
        (0.875, (0.988, 0.644, 0.183)),
        (1.000, (0.988, 0.921, 0.278)),
    ],
    "jet": [
        (0.000, (0.00, 0.00, 0.50)),
        (0.125, (0.00, 0.00, 1.00)),
        (0.375, (0.00, 1.00, 1.00)),
        (0.625, (1.00, 1.00, 0.00)),
        (0.875, (1.00, 0.00, 0.00)),
        (1.000, (0.50, 0.00, 0.00)),
    ],
    "rainbow": [
        (0.000, (0.47, 0.00, 0.96)),
        (0.167, (0.00, 0.25, 1.00)),
        (0.333, (0.00, 0.85, 0.30)),
        (0.500, (1.00, 1.00, 0.00)),
        (0.667, (1.00, 0.55, 0.00)),
        (0.833, (1.00, 0.00, 0.00)),
        (1.000, (0.65, 0.00, 0.00)),
    ],
    "grayscale": [
        (0.0, (0.0, 0.0, 0.0)),
        (1.0, (1.0, 1.0, 1.0)),
    ],
}

COLOR_MODE_LABELS = {
    "auto": "自动",
    "height": "高度",
    "intensity": "强度",
    "rgb": "RGB",
    "label": "聚类标签",
    "field": "字段",
    "none": "无",
}


def label_to_rgb(labels: np.ndarray) -> np.ndarray:
    """聚类标签 → 伪彩色（按黄金比例分配色相，噪声点用灰色）。"""
    labels = np.asarray(labels).reshape(-1)
    n = len(labels)
    rgb = np.full((n, 3), 0.6, dtype=np.float32)  # 噪声 -1 → 灰
    valid = labels >= 0
    if np.any(valid):
        vals = labels[valid].astype(np.int64)
        hue = (vals * 0.618033988749895) % 1.0
        # HSV(H, S=0.65, V=0.95) → RGB
        h6 = hue * 6.0
        i = h6.astype(np.int32) % 6
        f = h6 - h6.astype(np.int32)
        s, v = 0.65, 0.95
        p = v * (1 - s)
        q = v * (1 - s * f)
        t = v * (1 - s * (1 - f))
        r = np.choose(i, [v, q, p, p, t, v])
        g = np.choose(i, [t, v, v, q, p, p])
        b = np.choose(i, [p, p, t, v, v, q])
        rgb[valid] = np.stack([r, g, b], axis=1).astype(np.float32)
    return rgb


def list_colormaps() -> list:
    return list(COLORMAPS.keys())


def robust_norm(values: np.ndarray, lo_p: float = 2.0, hi_p: float = 98.0, eps: float = 1e-12):
    """2–98 百分位鲁棒归一化到 [0,1]（迁移自原脚本）。"""
    values = np.asarray(values, dtype=np.float32)
    mask = np.isfinite(values)
    if not np.any(mask):
        return np.zeros_like(values, dtype=np.float32)

    lo, hi = np.percentile(values[mask], [lo_p, hi_p])
    if abs(hi - lo) < eps:
        return np.zeros_like(values, dtype=np.float32)

    normalized = np.clip((values - lo) / (hi - lo), 0.0, 1.0)
    return np.where(mask, normalized, 0.0).astype(np.float32)


def sample_colormap(name: str, t: np.ndarray) -> np.ndarray:
    """按色彩映射把 t(N,)∈[0,1] 采样为 (N,3) RGB。"""
    stops = COLORMAPS.get(name, COLORMAPS["default"])
    pos = np.array([s[0] for s in stops], dtype=np.float32)
    col = np.array([s[1] for s in stops], dtype=np.float32)

    t = np.clip(np.asarray(t, dtype=np.float32), 0.0, 1.0)
    flat = t.reshape(-1)
    # Interpolate channels directly instead of materializing two Nx3 endpoint
    # arrays and several Nx3 products for every displayed cloud.
    rgb = np.empty((len(flat), 3), dtype=np.float32)
    for channel in range(3):
        rgb[:, channel] = np.interp(flat, pos, col[:, channel])
    np.clip(rgb, 0.0, 1.0, out=rgb)
    return rgb.reshape(t.shape + (3,))


def scalar_to_rgb(values: np.ndarray, colormap: str = "default",
                  vmin: Optional[float] = None, vmax: Optional[float] = None) -> np.ndarray:
    """把 intensity / height / field 等标量转成伪彩色。

    vmin/vmax 为 None 时按 2–98 百分位拉伸（robust_norm），否则用固定范围。
    """
    values = np.asarray(values, dtype=np.float64).reshape(-1)
    if vmin is not None and vmax is not None and float(vmax) > float(vmin):
        t = np.clip((values - float(vmin)) / (float(vmax) - float(vmin)), 0.0, 1.0)
    else:
        t = robust_norm(values)
    return sample_colormap(colormap, t)


def array_to_rgb(array: np.ndarray):
    """支持 Nx1 标量、Nx3 RGB、Nx4 RGBA（迁移自原脚本）。"""
    array = np.asarray(array)
    if array.ndim == 1:
        array = array.reshape(-1, 1)

    if array.ndim != 2:
        raise ValueError(f"颜色数组必须是 Nx1/Nx3/Nx4，实际 shape={array.shape}")

    if array.shape[1] == 1:
        return scalar_to_rgb(array[:, 0])

    if array.shape[1] >= 3:
        rgb = array[:, :3].astype(np.float32)
        rgb = rgb / 255.0 if float(np.nanmax(rgb)) > 1.5 else rgb
        return np.clip(rgb, 0.0, 1.0)

    raise ValueError(f"颜色数组列数不足: shape={array.shape}")


def rgb_has_color(rgb, max_samples: int = 50000, min_chroma: float = 0.10) -> bool:
    """判断 RGB 是否含有有意义的颜色信息（按中位色度）。

    建图/重建导出的 PCD 常用灰白甚至 R=B 的近似单色（如 0xA0A0A0），
    RGB 显示会整片糊成灰块；此类数据自动着色应回退到高度/强度。
    """
    if rgb is None or len(rgb) == 0:
        return False
    step = max(1, len(rgb) // max_samples)
    sample = np.asarray(rgb[::step], dtype=np.float32)
    try:
        chroma = sample.max(axis=1) - sample.min(axis=1)
        return float(np.median(chroma)) >= min_chroma
    except Exception:
        return False


def compute_colors(entity, mode: Optional[str] = None) -> Optional[np.ndarray]:
    """按颜色模式为实体计算 (N,3) RGB；mode=None 时用实体自身设置。

    规则（沿用原脚本的 auto 语义，并做显示优化）：
      auto: 有真实颜色（非灰白）用 rgb → intensity 字段 → 高度着色
      rgb/intensity/height/none: 显式指定
    """
    mode = entity.color_mode if mode is None else mode
    if mode == "none":
        return None

    rgb = entity.field("rgb")
    intensity = entity.field("intensity")
    labels = entity.field("labels")
    vmin, vmax = entity.color_range if entity.color_range else (None, None)

    if mode == "label" and labels is not None:
        return label_to_rgb(labels)

    if mode == "field":
        field = entity.field(entity.color_field)
        if field is None:
            # 未指定/字段被删：取第一个非 RGB 的一维标量场
            for name, candidate in entity.fields.items():
                if name != "rgb" and candidate is not None and np.ndim(candidate.data) == 1:
                    field = candidate
                    entity.color_field = name
                    break
        if field is not None:
            return scalar_to_rgb(field.data, entity.colormap, vmin, vmax)

    if mode in ("auto", "rgb") and rgb is not None:
        if mode == "rgb" or rgb_has_color(rgb):
            return array_to_rgb(rgb)

    if mode in ("auto", "intensity") and intensity is not None:
        return scalar_to_rgb(intensity, entity.colormap, vmin, vmax)

    if mode == "auto" and entity.xyz is not None:
        return scalar_to_rgb(entity.xyz[:, 2], entity.colormap, vmin, vmax)

    if mode in ("height", "intensity", "field") and entity.xyz is not None:
        source = entity.xyz[:, 2] if mode != "intensity" else (intensity if intensity is not None else entity.xyz[:, 2])
        return scalar_to_rgb(source, entity.colormap, vmin, vmax)

    if entity.xyz is not None:
        return scalar_to_rgb(entity.xyz[:, 2], entity.colormap, vmin, vmax)
    return None
