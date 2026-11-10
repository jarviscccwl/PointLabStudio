"""Read calibration selections without extracting or modifying user files."""
from dataclasses import dataclass, field
from io import BytesIO
import json
import math
from pathlib import Path, PurePosixPath
import re
import tomllib
from zipfile import BadZipFile, ZipFile

import numpy as np

from core.calibration import (
    CameraCalibration, EXTRINSIC_FIELDS, INTRINSIC_FIELDS, TRANSFORM_DIRECTIONS,
    calibration_from_dict, parse_calibration_text,
)

MAX_FILE_BYTES = 1024**2
MAX_ARCHIVE_BYTES = 8 * 1024**2
CALIBRATION_EXTENSIONS = (".json", ".yaml", ".yml", ".txt", ".csv", ".toml", ".npy", ".npz")
CALIBRATION_FILE_FILTER = (
    "标定文件 (*.json *.yaml *.yml *.txt *.csv *.toml *.npy *.npz *.zip);;所有文件 (*)"
)


@dataclass
class CalibrationDocument:
    name: str
    data: dict
    raw_matrix: np.ndarray | None = None

    @property
    def has_intrinsics(self):
        return any(name in self.data for name in INTRINSIC_FIELDS)

    @property
    def has_extrinsics(self):
        return any(self.data.get(name) is not None for name in TRANSFORM_DIRECTIONS)

    @property
    def requires_interpretation(self):
        if self.raw_matrix is not None:
            return True
        generic = any(self.data.get(name) is not None for name, direction
                      in TRANSFORM_DIRECTIONS.items() if direction is None)
        return generic and not (self.data.get("transform_direction")
                                or self.data.get("transform_convention"))

    @property
    def status(self):
        labels = (("status", "状态"), ("validation_status", "验证"),
                  ("diagnostic_outcome", "诊断"), ("recommended_for_deployment", "建议部署"))
        values = [f"{label}: {self.data[key]}" for key, label in labels if key in self.data]
        if self.data.get("calibration_source"):
            values.append(f"来源说明: {self.data['calibration_source']}")
        return " · ".join(values) or "文件未提供验收状态"


@dataclass
class CalibrationBundle:
    documents: list[CalibrationDocument] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)


def parse_numeric_matrix(contents: str) -> np.ndarray:
    """Parse only finite numbers, delimiters and # comments; never execute text."""
    rows = []
    for line in contents.splitlines():
        line = line.split("#", 1)[0].strip()
        for row in line.split(";"):
            tokens = row.translate(str.maketrans({c: " " for c in "[],()"})).split()
            if tokens:
                try:
                    rows.append([float(token) for token in tokens])
                except ValueError as error:
                    raise ValueError("矩阵仅支持数字、空格或逗号分隔；请移除说明文字或使用带字段名的标定文件。") from error
    try:
        matrix = np.asarray(rows, dtype=np.float64)
    except ValueError as error:
        raise ValueError("矩阵各行的数值数量必须一致。") from error
    if matrix.ndim == 2 and matrix.shape[0] == 1 and matrix.size in (9, 12, 16):
        matrix = matrix.reshape({9: (3, 3), 12: (3, 4), 16: (4, 4)}[matrix.size])
    return _raw_matrix(matrix)


def _raw_matrix(value):
    matrix = np.asarray(value)
    if matrix.dtype.kind not in "iuf":
        raise ValueError("矩阵必须是数值数组，不支持对象、字符串或复数。")
    if matrix.ndim == 1 and matrix.size in (9, 12, 16):
        matrix = matrix.reshape({9: (3, 3), 12: (3, 4), 16: (4, 4)}[matrix.size])
    if matrix.shape not in ((3, 3), (3, 4), (4, 4)) or not np.isfinite(matrix).all():
        raise ValueError("矩阵须为有限数值的 3×3、3×4 或 4×4。")
    return matrix.astype(np.float64, copy=True)


def interpret_matrix(document: CalibrationDocument, matrix_type: str | None,
                     direction=None, units=None, image_size=None) -> CalibrationDocument:
    """Resolve an untyped matrix only from explicit user choices.

    No shape or filename is used to infer its purpose or transform direction.
    Intrinsic matrices need the calibrated image dimensions. Extrinsic 3x4
    matrices acquire the homogeneous row only after explicit extrinsic choice.
    """
    if matrix_type not in ("intrinsics", "extrinsics"):
        raise ValueError("请选择矩阵类型：相机内参或雷视外参。")
    if document.raw_matrix is None:
        if matrix_type != "extrinsics" or not document.has_extrinsics:
            raise ValueError("此文件已使用外参字段名，不能作为相机内参矩阵。")
        data = dict(document.data)
    else:
        matrix = _raw_matrix(document.raw_matrix)
        data = {}
        if matrix_type == "intrinsics":
            if matrix.shape != (3, 3):
                raise ValueError("相机内参 K 必须是 3×3 矩阵。")
            if image_size is None or len(image_size) != 2:
                raise ValueError("请填写该内参对应的标定图像宽度和高度（像素）。")
            data.update(K=matrix, width=image_size[0], height=image_size[1])
            # Validate dimensions/K now, while leaving existing distortion alone.
            CameraCalibration(matrix, [], *image_size)
            return CalibrationDocument(document.name, data)
        if matrix.shape == (3, 4):
            matrix = np.vstack((matrix, [0, 0, 0, 1]))
        if matrix.shape != (4, 4):
            raise ValueError("雷视外参须为 3×4 或 4×4 齐次矩阵。")
        data["transform"] = matrix
    if direction not in ("lidar_to_camera", "camera_to_lidar"):
        raise ValueError("请选择原始外参方向：雷达 → 相机或相机 → 雷达。")
    if units not in ("m", "cm", "mm"):
        raise ValueError("请选择原始外参平移单位：米、厘米或毫米。")
    data["transform_direction"] = direction
    data["translation_units"] = units
    return CalibrationDocument(document.name, data)


def _camera_section(data):
    if not isinstance(data, dict) or "matrix" not in data or "size" not in data:
        raise ValueError("相机 TOML 段须包含 matrix、size 和可选 distortions。")
    size = data["size"]
    if not isinstance(size, (list, tuple)) or len(size) != 2:
        raise ValueError("相机 TOML 的 size 须为 [宽度, 高度]。")
    return {"K": data["matrix"], "D": data.get("distortions", []),
            "width": size[0], "height": size[1],
            "distortion_model": "equidistant" if data.get("fisheye") is True else "plumb_bob"}


def _mapping_documents(name, data):
    if "cameras" in data and isinstance(data["cameras"], dict):
        return [CalibrationDocument(f"{name} · cameras.{key}", _camera_section(camera))
                for key, camera in data["cameras"].items()]
    return [CalibrationDocument(name, data)]


def _mixed_initial_document(name, text):
    """Read the explicitly documented TOML + matrix diagnostic export."""
    direction = re.search(
        r"(?m)^\s*#\s*T_camera_lidar\s*:\s*p_camera\s*=\s*R\s*\*\s*p_lidar\s*\+\s*t\s*$", text
    )
    if direction is None:
        raise ValueError("混合 TOML/矩阵文本缺少明确的外参方向；请单独保存矩阵，再在导入窗口指定方向和单位。")
    start = re.search(r"(?m)^\s*(?:rmse_m|rotation_matrix|translation_m|quaternion_xyzw|transform_4x4)\s*:", text)
    if start is None:
        raise ValueError("混合文本未找到外参段。请另存为标准 TOML、JSON 或纯矩阵文本。")
    try:
        cameras = tomllib.loads(text[:start.start()])
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"混合文本的相机 TOML 段无法读取：{error}") from error
    entries = _mapping_documents(name, cameras)
    if len(entries) != 1:
        raise ValueError("混合文本含多台相机，请将需要的相机内参另存为独立 TOML 文件。")
    after = text[start.start():]
    match = re.search(r"(?m)^\s*transform_4x4\s*:\s*$", after)
    if match is None:
        raise ValueError("混合文本须包含独立的 transform_4x4: 和随后四行矩阵。")
    matrix = parse_numeric_matrix(after[match.end():])
    if matrix.shape != (4, 4):
        raise ValueError("transform_4x4 必须是 4×4 矩阵。")
    components = {}
    for field_name, size in (("rotation_matrix", 9), ("translation_m", 3)):
        values = re.findall(rf"(?m)^\s*{field_name}\s*:\s*(\[[^\n]*\])\s*$", after)
        if len(values) != 1:
            raise ValueError(f"混合文本须包含一组明确的 {field_name}，以核对矩阵和米单位。")
        try:
            component = np.asarray(json.loads(values[0]), dtype=np.float64)
        except (ValueError, TypeError) as error:
            raise ValueError(f"{field_name} 不是有效数值数组。") from error
        if component.size != size or not np.isfinite(component).all():
            raise ValueError(f"{field_name} 的数值数量不正确或含非有限数值。")
        components[field_name] = component
    if (not np.allclose(matrix[:3, :3], components["rotation_matrix"].reshape(3, 3), atol=3e-6, rtol=0)
            or not np.allclose(matrix[:3, 3], components["translation_m"].reshape(3), atol=3e-6, rtol=0)):
        raise ValueError("混合文本中的旋转、平移与 4×4 外参不一致；请核对原始文件。")
    data = dict(entries[0].data)
    data.update(T_camera_lidar=matrix, translation_units="m",
                calibration_source="混合文本初始参数；请核对是否为待使用版本")
    return [CalibrationDocument(name, data)]


def _read_numpy_array(content):
    source = BytesIO(content)
    try:
        version = np.lib.format.read_magic(source)
        if version not in ((1, 0), (2, 0), (3, 0)):
            raise ValueError("不支持的 NumPy 文件版本。")
        header_reader = (np.lib.format.read_array_header_1_0 if version == (1, 0)
                         else np.lib.format.read_array_header_2_0)
        shape, _, dtype = header_reader(source)
        if dtype.hasobject:
            raise ValueError("不读取 NumPy 对象数组或 pickle 内容；请导出纯数值数组。")
        if math.prod(shape) * dtype.itemsize > MAX_FILE_BYTES:
            raise ValueError("NumPy 数组展开后超过 1 MB 限制。")
        return np.load(BytesIO(content), allow_pickle=False)
    except (EOFError, OSError, TypeError) as error:
        raise ValueError("NumPy 数组无法安全读取。") from error


def _numpy_documents(name, content):
    if PurePosixPath(name).suffix.lower() == ".npy":
        return [CalibrationDocument(name, {}, _raw_matrix(_read_numpy_array(content)))]
    arrays = {}
    with ZipFile(BytesIO(content)) as archive:
        members = archive.infolist()
        if len(members) > 128 or sum(member.file_size for member in members) > MAX_FILE_BYTES:
            raise ValueError("NPZ 数组数量或展开大小超过限制（128 项 / 1 MB）。")
        for member in members:
            if member.is_dir() or not member.filename.endswith(".npy"):
                raise ValueError("NPZ 只能包含 NumPy 数组。")
            key = member.filename[:-4]
            if key in arrays:
                raise ValueError("NPZ 中存在同名数组，请使用唯一字段名。")
            arrays[key] = _read_numpy_array(archive.read(member))
    recognized = set(INTRINSIC_FIELDS) | set(EXTRINSIC_FIELDS)
    data = {key: (value.item() if value.ndim == 0 else value.tolist())
            for key, value in arrays.items() if key in recognized}
    documents = [CalibrationDocument(name, data)] if data else []
    for key, value in arrays.items():
        if key not in recognized:
            documents.append(CalibrationDocument(f"{name} · {key}", {}, _raw_matrix(value)))
    return documents


def _parse_documents(name, content):
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in (".npy", ".npz"):
        return _numpy_documents(name, content)
    text = content.decode("utf-8-sig")
    if suffix in (".txt", ".csv"):
        if re.search(r"(?m)^\s*transform_4x4\s*:", text) and "[cameras." in text:
            return _mixed_initial_document(name, text)
        try:
            matrix = parse_numeric_matrix(text)
        except ValueError:
            # A text file may use a standard named YAML or TOML schema.
            try:
                return _mapping_documents(name, parse_calibration_text(text, name))
            except ValueError:
                try:
                    return _mapping_documents(name, tomllib.loads(text))
                except tomllib.TOMLDecodeError as error:
                    raise ValueError("文本未能识别为纯数值矩阵、标准 TOML 或带字段名的 YAML；请核对格式。") from error
        return [CalibrationDocument(name, {}, matrix)]
    return _mapping_documents(name, parse_calibration_text(text, name))


def read_calibration_bundle(path: str) -> CalibrationBundle:
    """Read bounded safe formats without extracting archives or loading pickle."""
    bundle = CalibrationBundle()

    def add(name, content):
        try:
            for document in _parse_documents(name, content):
                if not (document.has_intrinsics or document.has_extrinsics or document.raw_matrix is not None):
                    bundle.ignored.append(f"{document.name}：不含相机内参或雷视外参")
                else:
                    bundle.documents.append(document)
        except (ValueError, UnicodeError, BadZipFile) as error:
            bundle.ignored.append(f"{name}：{error}")

    if Path(path).suffix.lower() == ".zip":
        with ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > 128:
                raise ValueError("标定压缩包文件数量超过 128 个限制。")
            total = 0
            for member in members:
                name = member.filename.replace("\\", "/")
                if member.is_dir():
                    continue
                if (PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts
                        or ":" in name):
                    bundle.ignored.append(f"{name}：无效文件路径")
                    continue
                if PurePosixPath(name).suffix.lower() not in CALIBRATION_EXTENSIONS:
                    bundle.ignored.append(f"{name}：非支持的标定文件类型")
                    continue
                total += member.file_size
                if member.file_size > MAX_FILE_BYTES or total > MAX_ARCHIVE_BYTES:
                    raise ValueError("标定文件过大：单文件上限 1 MB，包内标定总量上限 8 MB。")
                with archive.open(member) as source:
                    content = source.read(MAX_FILE_BYTES + 1)
                if len(content) > MAX_FILE_BYTES:
                    raise ValueError("标定文件超过 1 MB 大小限制。")
                add(name, content)
    else:
        with open(path, "rb") as source:
            content = source.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES:
            raise ValueError("标定文件超过 1 MB 大小限制。")
        add(Path(path).name, content)
    if not bundle.documents:
        raise ValueError("未找到可导入的相机内参或雷视外参。\n" + "\n".join(bundle.ignored[:8]))
    return bundle


def combine_calibration(intrinsics: CalibrationDocument | None,
                        extrinsics: CalibrationDocument | None,
                        base: CameraCalibration | None = None) -> CameraCalibration:
    """Replace only the selected component; never merge embedded K from an extrinsic."""
    data = {}
    if ((intrinsics is not None and intrinsics.raw_matrix is not None)
            or (extrinsics is not None and extrinsics.requires_interpretation)):
        raise ValueError("矩阵说明不完整：请在导入窗口选择类型、原始方向和单位。")
    if intrinsics is not None:
        data.update({key: value for key, value in intrinsics.data.items() if key in INTRINSIC_FIELDS})
    if extrinsics is not None:
        data.update({key: value for key, value in extrinsics.data.items() if key in EXTRINSIC_FIELDS})
    if not data:
        raise ValueError("请选择要导入的内参或外参。")
    if base is None and intrinsics is None:
        raise ValueError("请先导入或手动填写相机内参（焦距、主点和图像尺寸），再合并外参。")
    return calibration_from_dict(data, base)
