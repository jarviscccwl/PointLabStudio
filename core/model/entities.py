"""核心数据模型：CloudEntity / ScalarField。

设计要点：
- 源数据只读，处理产生派生实体（非破坏性编辑）
- 每片点云有任意多个命名标量场，颜色模式引用标量场名
- transform 为 4x4 矩阵，预留多片点云全局配准
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

# 自动分级（Auto-Ingest）层级
TIER_S = "S"  # < 100 万点
TIER_M = "M"  # 100 万 ~ 1 亿
TIER_L = "L"  # >= 1 亿

COLOR_MODES = ["height", "intensity", "rgb", "label", "field", "none"]


def identity4() -> np.ndarray:
    return np.eye(4, dtype=np.float32)


@dataclass
class ScalarField:
    """命名标量场：intensity / labels / 自定义标量等。"""

    name: str
    data: np.ndarray          # (N,) float 或 (N,3) 颜色
    kind: str = "scalar"      # 'scalar' | 'color'
    stats: Optional[dict] = None

    def __post_init__(self):
        if self.stats is None:
            self.stats = {}

    @property
    def count(self) -> int:
        return int(self.data.shape[0])


class _BoundsMixin:
    """点云与网格共用的包围盒中心计算。"""

    def bounds(self) -> Optional[tuple]:
        raise NotImplementedError

    def center(self) -> np.ndarray:
        b = self.bounds()
        if b is None:
            return np.zeros(3)
        return (b[0] + b[1]) / 2.0


@dataclass(eq=False)
class CloudEntity(_BoundsMixin):
    """一片点云（现有 CloudData 的升级版）。"""

    name: str
    path: Optional[str] = None
    xyz: Optional[np.ndarray] = None                 # (N,3) float32
    fields: dict = field(default_factory=dict)       # name -> ScalarField
    color_mode: str = "height"                       # height/intensity/rgb/label/field/none
    colormap: str = "default"
    color_field: str = ""                            # mode="field" 时着色的标量场名
    color_range: Optional[tuple] = None              # (vmin, vmax) 手动颜色范围；None=2–98 百分位
    dim_hint: Optional[int] = None                   # 原始数据维度（.bin 识别结果等）
    point_size: float = 2.0
    visible: bool = True
    transform: np.ndarray = field(default_factory=identity4)
    tier: str = TIER_S                               # Auto-Ingest 层级
    loaded: float = 1.0                              # 已加载比例 0..1（L 级流式加载）
    info: str = ""
    debug: str = ""
    load_state: str = "ready"                       # unloaded / loading / ready / error
    visibility_pinned: bool = False

    # ---------- 便捷属性 ----------
    @property
    def point_count(self) -> int:
        return 0 if self.xyz is None else int(self.xyz.shape[0])

    @property
    def is_preview(self) -> bool:
        return self.loaded < 1.0

    def bounds(self) -> Optional[tuple]:
        """返回 ((minx,miny,minz), (maxx,maxy,maxz))。"""
        if self.xyz is None or len(self.xyz) == 0:
            return None
        return (
            np.asarray(self.xyz.min(axis=0), dtype=np.float64),
            np.asarray(self.xyz.max(axis=0), dtype=np.float64),
        )

    def extent(self) -> float:
        b = self.bounds()
        if b is None:
            return 1.0
        return float(np.linalg.norm(b[1] - b[0]))

    def range_text(self) -> str:
        b = self.bounds()
        if b is None:
            return "空"
        mn, mx = b
        return f"x[{mn[0]:.1f},{mx[0]:.1f}] y[{mn[1]:.1f},{mx[1]:.1f}] z[{mn[2]:.1f},{mx[2]:.1f}]"

    def field(self, name: str) -> Optional[np.ndarray]:
        f = self.fields.get(name)
        return None if f is None else f.data

    def subset_fields(self, index) -> dict:
        """按索引裁切全部命名标量场，返回新的 fields 字典。"""
        return {name: ScalarField(name, f.data[index], f.kind)
                for name, f in self.fields.items()}

    # ---------- 状态复制（保持显示状态、替换数据） ----------
    def copy_display_from(self, other: "CloudEntity") -> None:
        self.color_mode = other.color_mode
        self.colormap = other.colormap
        self.point_size = other.point_size
        self.visible = other.visible


@dataclass(eq=False)
class MeshEntity(_BoundsMixin):
    """三角网格实体（Poisson 重建结果等），可与点云一起渲染并作为 C2M 参照。"""

    name: str
    vertices: np.ndarray                    # (M,3) float32
    triangles: np.ndarray                   # (T,3) int32
    path: Optional[str] = None
    color: tuple = (0.56, 0.66, 0.82)
    visible: bool = True
    opacity: float = 1.0
    info: str = ""

    @property
    def triangle_count(self) -> int:
        return 0 if self.triangles is None else int(np.asarray(self.triangles).shape[0])

    @property
    def vertex_count(self) -> int:
        return 0 if self.vertices is None else int(np.asarray(self.vertices).shape[0])

    def bounds(self) -> Optional[tuple]:
        if self.vertices is None or len(self.vertices) == 0:
            return None
        return (
            np.asarray(self.vertices.min(axis=0), dtype=np.float64),
            np.asarray(self.vertices.max(axis=0), dtype=np.float64),
        )
