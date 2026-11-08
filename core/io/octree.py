"""八叉树流式采样：超大点云不整体读入内存，也能按空间均匀取样。

思路：
1. 第一遍分块扫描文件，只统计坐标包围盒（不保留点）。
2. 第二遍分块扫描，把每点编码到深度 ``depth`` 的八叉树单元（每轴 2**depth 格），
   每个单元最多保留 ``per_cell`` 个样本（按块内均匀间隔抽取），并累计真实点数。
3. 渲染时按单元点数比例分配预算，合成为一次采样结果。

每个单元保存的是完整行（xyz + intensity/rgb 等），因此流式采样同样保留标量场。

内存上界约为 ``占用单元数 × per_cell × dim × 4 字节``，与文件总点数无关；
100M 点（float32 dim4，约 1.6 GB）可以在不加载全量的前提下得到空间均匀的 2M 采样。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from core.cancellation import CancelCb, check_cancel
from core.progress import ProgressCb


@dataclass
class OctreeIndex:
    """分块构建出的稀疏八叉树采样索引。"""

    path: str
    dim: int
    dtype: str
    total: int
    depth: int
    mn: np.ndarray
    mx: np.ndarray
    cells: dict[int, np.ndarray]      # code -> (n,dim) float32 样本（完整行）
    counts: dict[int, int]            # code -> 原始点数

    @property
    def cell_count(self) -> int:
        return len(self.cells)

    @property
    def sample_count(self) -> int:
        return int(sum(len(v) for v in self.cells.values()))

    def sample(self, budget: int = 2_000_000) -> np.ndarray:
        """按单元真实点数比例取样的完整行（确定性，不闪烁）。"""
        if self.total <= 0 or not self.cells:
            return np.zeros((0, self.dim), dtype=np.float32)
        budget = max(1, min(int(budget), self.sample_count))
        codes = sorted(self.cells)
        counts = np.array([self.counts[c] for c in codes], dtype=np.float64)
        share = counts / counts.sum() * budget
        take = np.floor(share).astype(np.int64)
        take = np.minimum(take, np.array([len(self.cells[c]) for c in codes]))
        # 余量按小数部分补齐，避免总数为 0 或远超预算
        rest = budget - int(take.sum())
        if rest > 0:
            frac = share - np.floor(share)
            order = np.argsort(-frac)
            i = 0
            while rest > 0 and i < len(order) * 4:
                c = order[i % len(order)]
                if take[c] < len(self.cells[codes[c]]):
                    take[c] += 1
                    rest -= 1
                i += 1
        parts = []
        for c, k in zip(codes, take):
            if k > 0:
                arranged = self.cells[c]
                if k >= len(arranged):
                    parts.append(arranged)
                else:
                    idx = np.linspace(0, len(arranged) - 1, k).astype(np.int64)
                    parts.append(arranged[idx])
        if not parts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.concatenate(parts, axis=0)

    def stats(self) -> dict:
        return {
            "total": self.total,
            "cells": self.cell_count,
            "kept": self.sample_count,
            "depth": self.depth,
        }


def build_octree(path: str, dim: int = 3, dtype: str = "float32", depth: int = 5,
                 per_cell: int = 400, chunk_points: int = 2_000_000,
                 progress: ProgressCb = None, cancel: CancelCb = None) -> OctreeIndex:
    """分块扫描 ``.bin`` 文件构建八叉树采样索引。"""
    if dim < 3:
        raise ValueError("八叉树需要至少 3 维坐标")
    arr = np.memmap(path, dtype=dtype, mode="r")
    if arr.size % dim != 0:
        raise ValueError(f"文件大小与维度不匹配: {arr.size} 个值 / {dim} 维")
    total = int(arr.size // dim)
    if total <= 0:
        raise ValueError("空文件")

    mn = np.full(3, np.inf, dtype=np.float64)
    mx = np.full(3, -np.inf, dtype=np.float64)
    for start in range(0, total, chunk_points):
        check_cancel(cancel)
        end = min(start + chunk_points, total)
        block = np.asarray(arr[start * dim:end * dim]).reshape(-1, dim)
        xyz = block[:, :3].astype(np.float64)
        xyz = xyz[np.isfinite(xyz).all(axis=1)]
        if len(xyz):
            mn = np.minimum(mn, xyz.min(axis=0))
            mx = np.maximum(mx, xyz.max(axis=0))
        if progress is not None:
            progress(0.5 * (end / total))
    if not np.isfinite(mn).all() or not np.isfinite(mx).all():
        raise ValueError("点云坐标全部无效")
    extent = np.maximum(mx - mn, 1e-9)

    mask = (1 << depth) - 1
    cells: dict[int, list[np.ndarray]] = {}
    counts: dict[int, int] = {}

    for start in range(0, total, chunk_points):
        check_cancel(cancel)
        end = min(start + chunk_points, total)
        block = np.asarray(arr[start * dim:end * dim]).reshape(-1, dim)
        xyz = block[:, :3].astype(np.float64)
        finite = np.isfinite(xyz).all(axis=1)
        if not finite.any():
            continue
        xyz = xyz[finite]
        q = np.floor((xyz - mn) / extent * (1 << depth)).astype(np.int64)
        np.clip(q, 0, mask, out=q)
        codes = (q[:, 0] << (2 * depth)) | (q[:, 1] << depth) | q[:, 2]
        order = np.argsort(codes, kind="stable")
        codes_sorted = codes[order]
        rows_sorted = block[finite][order].astype(np.float32)
        uniq, starts = np.unique(codes_sorted, return_index=True)
        ends = np.append(starts[1:], len(codes_sorted))
        for code, lo, hi in zip(uniq.tolist(), starts.tolist(), ends.tolist()):
            counts[code] = counts.get(code, 0) + (hi - lo)
            bucket = cells.get(code)
            room = per_cell - (0 if bucket is None else sum(len(b) for b in bucket))
            if room <= 0:
                continue
            segment = rows_sorted[lo:hi]
            if len(segment) > room:
                idx = np.linspace(0, len(segment) - 1, room).astype(np.int64)
                segment = segment[idx]
            if bucket is None:
                cells[code] = [segment]
            else:
                bucket.append(segment)
        if progress is not None:
            progress(0.5 + 0.5 * (end / total))

    packed = {code: np.concatenate(parts, axis=0) for code, parts in cells.items()}
    return OctreeIndex(path=path, dim=dim, dtype=dtype, total=total, depth=depth,
                       mn=mn.astype(np.float32), mx=mx.astype(np.float32),
                       cells=packed, counts=counts)


def estimate_stream_points(point_count: int, budget: int = 2_000_000) -> int:
    """给定总点数，返回流式采样后实际保留的点数（用于进度与提示文案）。"""
    if point_count <= budget:
        return int(point_count)
    return int(min(budget, max(1, math.ceil(point_count ** (2.0 / 3.0)))))
