"""原生点云格式读写（PLY / PCD / 文本）。

目标：无损保留命名标量场（intensity、labels、自定义字段）与颜色，
避免 "导出时把伪彩色烘进 RGB、丢掉 intensity" 的数据损坏。

- PLY：binary_little_endian，任意 float 标量属性 + uchar RGB
- PCD：binary / ascii，保留 F/U/I 类型、精度与 COUNT 向量字段，rgb 用打包 float
- 文本：首行注释声明列名，后续 x y z + 标量列
"""

from __future__ import annotations

import os
import io
from typing import Optional

import numpy as np

from core.model.entities import CloudEntity, ScalarField

_PLY_TYPES = {
    "char": "i1", "int8": "i1",
    "uchar": "u1", "uint8": "u1",
    "short": "i2", "int16": "i2",
    "ushort": "u2", "uint16": "u2",
    "int": "i4", "int32": "i4",
    "uint": "u4", "uint32": "u4",
    "float": "f4", "float32": "f4",
    "double": "f8", "float64": "f8",
}
_PCD_TYPES = {"F": "f", "U": "u", "I": "i"}

_XYZ_NAMES = {"x", "y", "z"}
_COLOR_NAMES = {"red", "green", "blue", "r", "g", "b"}


# ---------------- 通用字段提取 ----------------

def _scalar_fields(entity: CloudEntity):
    """返回 [(name, (N,) float32)]，跳过颜色/法线等向量场。"""
    out = []
    for name, field in entity.fields.items():
        data = np.asarray(field.data)
        if field.kind == "color" or data.ndim != 1:
            continue
        out.append((name, data.astype(np.float32, copy=False)))
    return out


def _rgb01(entity: CloudEntity) -> Optional[np.ndarray]:
    field = entity.fields.get("rgb")
    if field is None:
        return None
    data = np.asarray(field.data, dtype=np.float32)
    if data.ndim != 2 or data.shape[1] < 3:
        return None
    return data[:, :3]


def _normals(entity: CloudEntity) -> Optional[np.ndarray]:
    field = entity.fields.get("normal")
    if field is None:
        return None
    data = np.asarray(field.data, dtype=np.float32)
    return data if data.ndim == 2 and data.shape[1] >= 3 else None


def _make_entity(name: str, path: str, xyz: np.ndarray, fields: dict, info: str) -> CloudEntity:
    return CloudEntity(name=name, path=path, xyz=np.asarray(xyz, dtype=np.float32),
                       fields=fields, dim_hint=3 + len(fields), info=info)


# ---------------- PLY ----------------

def write_ply(entity: CloudEntity, path: str) -> None:
    xyz = np.asarray(entity.xyz, dtype=np.float32)
    n = xyz.shape[0]
    scalars = _scalar_fields(entity)
    rgb = _rgb01(entity)
    normals = _normals(entity)

    props = [("x", "f4"), ("y", "f4"), ("z", "f4")]
    props += [(name, "f4") for name, _ in scalars]
    if normals is not None:
        props += [("nx", "f4"), ("ny", "f4"), ("nz", "f4")]
    if rgb is not None:
        props += [("red", "u1"), ("green", "u1"), ("blue", "u1")]

    with open(path, "wb") as f:
        f.write(b"ply\nformat binary_little_endian 1.0\n")
        f.write(f"element vertex {n}\n".encode())
        for name, dtype in props:
            kind = "uchar" if dtype == "u1" else "float"
            f.write(f"property {kind} {name}\n".encode())
        f.write(b"end_header\n")

        dtype = np.dtype([(name, "<" + dt) for name, dt in props])
        rows = np.empty(n, dtype=dtype)
        rows["x"], rows["y"], rows["z"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
        for name, data in scalars:
            rows[name] = data
        if normals is not None:
            rows["nx"], rows["ny"], rows["nz"] = normals[:, 0], normals[:, 1], normals[:, 2]
        if rgb is not None:
            cols = np.clip(rgb * 255.0 if rgb.max(initial=0.0) <= 1.5 else rgb, 0, 255).astype(np.uint8)
            rows["red"], rows["green"], rows["blue"] = cols[:, 0], cols[:, 1], cols[:, 2]
        f.write(rows.tobytes())


def read_ply(path: str) -> CloudEntity:
    with open(path, "rb") as f:
        if f.readline().strip() != b"ply":
            raise ValueError("不是 PLY 文件")
        fmt = "ascii"
        elements = []          # [name, count, [(prop, dtype)]]
        while True:
            line = f.readline()
            if not line:
                raise ValueError("PLY 头不完整")
            parts = line.decode("ascii", "ignore").split()
            if not parts:
                continue
            key = parts[0]
            if key == "format":
                fmt = parts[1]
            elif key == "element":
                elements.append([parts[1], int(parts[2]), []])
            elif key == "property" and elements:
                elements[-1][2].append((parts[-1], parts[1]))
            elif key == "end_header":
                break

        if not elements or elements[0][0] != "vertex":
            raise ValueError("PLY 缺少 vertex 元素")
        vname, count, props = elements[0]
        props = [(n, _PLY_TYPES.get(t, "f4")) for n, t in props]

        if fmt.startswith("binary"):
            endian = "<" if "little" in fmt else ">"
            dtype = np.dtype([(n, endian + t) for n, t in props])
            raw = f.read(dtype.itemsize * count)
            table = np.frombuffer(raw, dtype=dtype, count=count)
        else:
            rows = []
            for _ in range(count):
                line = f.readline()
                if not line:
                    break
                rows.append([float(v) for v in line.split()])
            if not rows:
                raise ValueError("PLY 顶点为空")
            table = np.asarray(rows, dtype=np.float64)

    if table.dtype.names:
        names = list(table.dtype.names)
        index = {name: i for i, name in enumerate(names)}

        def get(name: str):
            return np.asarray(table[name]) if name in index else None
    else:
        names = [p[0] for p in props]
        index = {name: i for i, name in enumerate(names)}

        def get(name: str):
            return table[:, index[name]] if name in index else None

    xyz = np.stack([get("x"), get("y"), get("z")], axis=1).astype(np.float32)
    fields = {}

    if get("red") is not None and get("green") is not None and get("blue") is not None:
        rgb = np.stack([get("red"), get("green"), get("blue")], axis=1).astype(np.float32)
        if rgb.max(initial=0.0) > 1.5:
            rgb /= 255.0
        fields["rgb"] = ScalarField("rgb", rgb, kind="color")

    for name in names:
        key = name.lower()
        if key in _XYZ_NAMES or key in _COLOR_NAMES or key.startswith("normal_") or key in {"nx", "ny", "nz"}:
            continue
        data = np.asarray(get(name), dtype=np.float32)
        fields[name] = ScalarField(name, data)

    normals = None
    if all(get(k) is not None for k in ("nx", "ny", "nz")):
        normals = np.stack([get("nx"), get("ny"), get("nz")], axis=1).astype(np.float32)
    elif all(get("normal_" + k) is not None for k in "xyz"):
        normals = np.stack([get("normal_" + k) for k in "xyz"], axis=1).astype(np.float32)
    if normals is not None:
        fields["normal"] = ScalarField("normal", normals, kind="normal")

    return _make_entity(os.path.basename(path), path, xyz, fields, f"ply {fmt}")


# ---------------- PCD ----------------

def write_pcd(entity: CloudEntity, path: str) -> None:
    xyz = np.asarray(entity.xyz, dtype=np.float32)
    n = xyz.shape[0]
    rgb_data, normal_data = entity.field("rgb"), entity.field("normal")
    rgb = (_rgb01(entity) if rgb_data is not None and np.shape(rgb_data) == (n, 3) else None)
    normals = (np.asarray(normal_data) if normal_data is not None and np.shape(normal_data) == (n, 3) else None)
    properties = [(axis, xyz[:, index]) for index, axis in enumerate(("x", "y", "z"))]
    for name, field in entity.fields.items():
        if name == "rgb" and rgb is not None or name == "normal" and normals is not None:
            continue
        properties.append((name, np.asarray(field.data)))
    if normals is not None:
        properties.extend((name, normals[:, index]) for index, name in enumerate(("normal_x", "normal_y", "normal_z")))
    if rgb is not None:
        cols = np.clip(rgb * 255.0 if rgb.max(initial=0.0) <= 1.5 else rgb, 0, 255).astype(np.uint32)
        packed = (cols[:, 0] << 16) | (cols[:, 1] << 8) | cols[:, 2]
        properties.append(("rgb", packed.astype(np.uint32).view(np.float32)))
    names, sizes, types, counts, descriptors = [], [], [], [], []
    for name, data in properties:
        if (not name or not name.isascii() or any(char.isspace() for char in name)
                or "\x00" in name or name in names):
            raise ValueError(f"PCD 字段名称无效或重复：{name!r}")
        if data.ndim not in (1, 2) or len(data) != n or data.dtype.kind not in "biuf":
            raise ValueError(f"PCD 字段必须是每点的数值或数值向量：{name}")
        count = 1 if data.ndim == 1 else data.shape[1]
        if count < 1:
            raise ValueError(f"PCD 字段向量不能为空：{name}")
        dtype = data.dtype.newbyteorder("<")
        if dtype.kind == "b":
            dtype = np.dtype("u1")
        if dtype.kind == "f" and dtype.itemsize < 4:
            dtype = np.dtype("<f4")
        if dtype.itemsize not in (1, 2, 4, 8):
            raise ValueError(f"PCD 不支持此字段精度：{name}")
        names.append(name)
        sizes.append(str(dtype.itemsize))
        types.append({"f": "F", "u": "U", "i": "I"}[dtype.kind])
        counts.append(str(count))
        descriptors.append((name, dtype) if count == 1 else (name, dtype, (count,)))
    rows = np.empty(n, dtype=descriptors)
    for name, data in properties:
        rows[name] = data.reshape(rows[name].shape)
    with open(path, "wb") as f:
        header = [
            "# .PCD v0.7 - Point Cloud Data file format",
            "VERSION 0.7",
            "FIELDS " + " ".join(names),
            "SIZE " + " ".join(sizes),
            "TYPE " + " ".join(types),
            "COUNT " + " ".join(counts),
            f"WIDTH {n}", "HEIGHT 1", "VIEWPOINT 0 0 0 1 0 0 0",
            f"POINTS {n}", "DATA binary",
        ]
        f.write(("\n".join(header) + "\n").encode())

        f.write(rows.tobytes())


def read_pcd(path: str) -> CloudEntity:
    header = {}
    with open(path, "rb") as f:
        while True:
            line = f.readline()
            if not line:
                raise ValueError("PCD 头不完整")
            text = line.decode("ascii", "ignore").strip()
            if not text or text.startswith("#"):
                continue
            key, _, value = text.partition(" ")
            header[key.upper()] = value.strip()
            if key.upper() == "DATA":
                fmt = value.strip().lower()
                break
        body = f.read()

    names = header["FIELDS"].split()
    sizes = [int(v) for v in header.get("SIZE", "4 " * len(names)).split()]
    types = header.get("TYPE", "F " * len(names)).split()
    counts = [int(v) for v in header.get("COUNT", "1 " * len(names)).split()]
    n = int(header.get("POINTS", "0"))
    if (not names or len(set(names)) != len(names) or n < 0
            or not len(names) == len(sizes) == len(types) == len(counts)):
        raise ValueError("PCD 字段定义不完整或重复")

    rows = []
    for name, size, kind, count in zip(names, sizes, types, counts):
        code = _PCD_TYPES.get(kind.upper())
        if code is None or count < 1 or size not in (1, 2, 4, 8) or code == "f" and size not in (4, 8):
            raise ValueError("PCD 字段类型、长度或 COUNT 无效")
        dtype = np.dtype(f"<{code}{size}")
        rows.append((name, dtype) if count == 1 else (name, dtype, (count,)))
    dtype = np.dtype(rows)

    if fmt == "ascii":
        table = np.loadtxt(io.BytesIO(body), dtype=dtype, ndmin=1)[:n or None]
    elif fmt == "binary":
        table = np.frombuffer(body[: dtype.itemsize * n], dtype=dtype, count=n)
    else:
        raise ValueError(f"PCD 原生读取暂不支持 DATA {fmt}")

    index = {name: i for i, name in enumerate(names)}

    def get(name: str):
        if name not in index:
            return None
        return np.asarray(table[name])

    xyz = np.stack([get("x"), get("y"), get("z")], axis=1).astype(np.float32)
    fields = {}
    packed_rgb = ("rgb" in index and counts[index["rgb"]] == 1 and sizes[index["rgb"]] == 4)
    if packed_rgb:
        raw = np.asarray(get("rgb"))
        if raw.dtype == np.float32:
            packed = raw.view(np.uint32)
        else:
            packed = raw.astype(np.uint32)
        rgb = np.stack([(packed >> 16) & 0xFF, (packed >> 8) & 0xFF, packed & 0xFF], axis=1)
        fields["rgb"] = ScalarField("rgb", rgb.astype(np.float32) / 255.0, kind="color")

    skip = {"x", "y", "z", "normal_x", "normal_y", "normal_z"}
    if packed_rgb:
        skip.add("rgb")
    for name in names:
        if name.lower() in skip:
            continue
        fields[name] = ScalarField(name, np.array(get(name), copy=True))

    if get("normal_x") is not None:
        normals = np.stack([get("normal_x"), get("normal_y"), get("normal_z")], axis=1)
        fields["normal"] = ScalarField("normal", normals.copy(), kind="normal")

    return _make_entity(os.path.basename(path), path, xyz, fields, f"pcd {fmt}")


# ---------------- 文本 ----------------

def write_text(entity: CloudEntity, path: str) -> None:
    xyz = np.asarray(entity.xyz, dtype=np.float32)
    scalars = _scalar_fields(entity)
    rgb = _rgb01(entity)
    color_scale = 255.0 if rgb is not None and rgb.max(initial=0.0) <= 1.5 else 1.0
    ext = os.path.splitext(path)[1].lower()
    sep = "," if ext == ".csv" else " "
    names = ["x", "y", "z"] + [name for name, _ in scalars] + (["r", "g", "b"] if rgb is not None else [])

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        if ext != ".csv":
            f.write("# columns:" + " " + " ".join(names) + "\n")
        for i in range(xyz.shape[0]):
            values = [f"{xyz[i, 0]:.6f}", f"{xyz[i, 1]:.6f}", f"{xyz[i, 2]:.6f}"]
            values += [f"{data[i]:.6f}" for _, data in scalars]
            if rgb is not None:
                cols = rgb[i] * color_scale
                values += [str(int(round(float(c)))) for c in np.clip(cols, 0, 255)]
            f.write(sep.join(values) + "\n")


def write_pts(entity: CloudEntity, path: str) -> None:
    """标准 PTS：首行点数，随后 x y z [intensity] r g b。"""
    xyz = np.asarray(entity.xyz, dtype=np.float32)
    rgb = _rgb01(entity)
    color_scale = 255.0 if rgb is not None and rgb.max(initial=0.0) <= 1.5 else 1.0
    intensity = entity.fields.get("intensity")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"{xyz.shape[0]}\n")
        for i in range(xyz.shape[0]):
            row = [f"{xyz[i, 0]:.6f}", f"{xyz[i, 1]:.6f}", f"{xyz[i, 2]:.6f}"]
            if intensity is not None:
                row.append(f"{float(intensity.data[i]):.6f}")
            if rgb is not None:
                cols = rgb[i] * color_scale
                row += [str(int(round(float(c)))) for c in np.clip(cols, 0, 255)]
            f.write(" ".join(row) + "\n")


def read_text(path: str, ext: str) -> CloudEntity:
    ext = ext.lower()
    delimiter = "," if ext == ".csv" else None
    columns = None
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        first = f.readline()
    skip_rows = 0
    if ext == ".pts":
        head = first.strip().split()
        if len(head) == 1 and head[0].isdigit():
            skip_rows = 1
    if first.startswith("#"):
        if "columns:" in first:
            columns = first.split("columns:", 1)[1].split()

    array = np.loadtxt(path, dtype=np.float32, delimiter=delimiter,
                       comments="#", ndmin=2, skiprows=skip_rows)
    if array.shape[1] < 3:
        raise ValueError(f"文本点云不是 Nx3+: shape={array.shape}")

    ncols = array.shape[1]
    if columns and len(columns) == ncols:
        index = {name.lower(): i for i, name in enumerate(columns)}
        xyz = np.stack([array[:, index["x"]], array[:, index["y"]], array[:, index["z"]]], axis=1)
        fields = {}
        for name in columns:
            key = name.lower()
            if key in _XYZ_NAMES:
                continue
            data = array[:, index[key]]
            if key in {"r", "g", "b"}:
                continue
            fields[name] = ScalarField(name, data)
        if all(k in index for k in ("r", "g", "b")):
            rgb = np.stack([array[:, index[k]] for k in ("r", "g", "b")], axis=1)
            fields["rgb"] = ScalarField("rgb", rgb / 255.0 if rgb.max() > 1.5 else rgb, kind="color")
        return _make_entity(os.path.basename(path), path, xyz, fields, f"text {ncols} cols")

    xyz = array[:, :3]
    fields = {}
    if ext == ".pts" and ncols >= 7:
        fields["intensity"] = ScalarField("intensity", array[:, 3])
        color = array[:, 4:7]
    else:
        if ncols >= 4:
            fields["intensity"] = ScalarField("intensity", array[:, 3])
        color = array[:, 3:6] if ncols >= 6 else None
    if color is not None:
        fields["rgb"] = ScalarField("rgb", color / 255.0 if color.max() > 1.5 else color, kind="color")
    return _make_entity(os.path.basename(path), path, xyz, fields, f"text {ncols} cols")
