"""Validated camera intrinsics and explicit lidar-to-camera projection.

Coordinates use metres and the optical-camera convention (x right, y down,
z forward). ``T_camera_lidar`` maps lidar coordinates into camera coordinates.
CameraInfo provides intrinsics only. An explicit extrinsic transform is required
before projecting lidar points.
"""

from dataclasses import dataclass
import json
from operator import index
from pathlib import Path
import re
import tomllib

import numpy as np
import yaml


INTRINSIC_FIELDS = ("K", "k", "camera_matrix", "D", "d", "distortion_coefficients",
                    "width", "image_width", "height", "image_height", "distortion_model")
TRANSFORM_DIRECTIONS = {
    "T_camera_lidar": "lidar_to_camera", "T_C_L": "lidar_to_camera",
    "T_lidar_camera": "camera_to_lidar", "T_L_C": "camera_to_lidar",
    "transform": None, "T": None, "extrinsics": None, "extrinsic_matrix": None,
}
EXTRINSIC_FIELDS = (*TRANSFORM_DIRECTIONS, "transform_direction", "transform_convention",
                    "translation_units", "translation_unit", "length_unit")


def _matrix(value, shape: tuple[int, int], name: str) -> np.ndarray:
    if isinstance(value, dict):
        if "rows" in value and value["rows"] != shape[0]:
            raise ValueError(f"{name} must have {shape[0]} rows.")
        if "cols" in value and value["cols"] != shape[1]:
            raise ValueError(f"{name} must have {shape[1]} columns.")
        value = value.get("data")
    try:
        result = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must contain numeric matrix values.") from error
    if result.shape == (shape[0] * shape[1],):
        result = result.reshape(shape)
    if result.shape != shape or not np.isfinite(result).all():
        raise ValueError(f"{name} must be a finite {shape[0]}x{shape[1]} matrix.")
    return result.copy()


def _dimension(value, name: str) -> int:
    try:
        result = index(value)
    except TypeError as error:
        raise ValueError(f"{name} must be a positive integer.") from error
    if isinstance(value, (bool, np.bool_)) or result <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return result


def _nonnegative_integer(value, name: str) -> int:
    try:
        result = index(value)
    except TypeError as error:
        raise ValueError(f"{name} must be a nonnegative integer.") from error
    if isinstance(value, (bool, np.bool_)) or result < 0:
        raise ValueError(f"{name} must be a nonnegative integer.")
    return result


def _transform(value, name: str = "T_camera_lidar") -> np.ndarray:
    matrix = _matrix(value, (4, 4), name)
    if not np.allclose(matrix[3], [0, 0, 0, 1], rtol=0, atol=1e-8):
        raise ValueError(f"{name} must have homogeneous last row [0, 0, 0, 1].")
    rotation = matrix[:3, :3]
    if (not np.allclose(rotation.T @ rotation, np.eye(3), rtol=0, atol=1e-5)
            or not np.isclose(np.linalg.det(rotation), 1, rtol=0, atol=1e-5)):
        raise ValueError(f"{name} rotation must be orthonormal with determinant +1.")
    return matrix


def extrinsics_from_pose(translation_m, rotation_deg) -> np.ndarray:
    """Build lidar → optical-camera extrinsics from metres and XYZ degrees.

    Right-handed active rotations about fixed X, Y, Z axes, applied in that
    order to column vectors: R = Rz(yaw) @ Ry(pitch) @ Rx(roll). Translation
    is expressed in camera axes: p_camera = R @ p_lidar + t.
    """
    try:
        translation = np.asarray(translation_m, dtype=np.float64)
        angles = np.asarray(rotation_deg, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("平移与旋转角必须各包含 3 个有限数值。") from error
    if (translation.shape != (3,) or angles.shape != (3,)
            or not np.isfinite(translation).all() or not np.isfinite(angles).all()):
        raise ValueError("平移与旋转角必须各包含 3 个有限数值。")
    # Modulo first also bounds finite but extremely large user input.
    roll, pitch, yaw = np.deg2rad(np.remainder(angles, 360.0))
    cr, cp, cy = np.cos([roll, pitch, yaw])
    sr, sp, sy = np.sin([roll, pitch, yaw])
    matrix = np.eye(4)
    matrix[:3, :3] = [[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                     [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                     [-sp, cp * sr, cp * cr]]
    matrix[:3, 3] = translation
    return _transform(matrix)


def pose_from_extrinsics(transform) -> tuple[np.ndarray, np.ndarray]:
    """Return metre translation and fixed-axis XYZ angles in degrees.

    Euler angles are not unique. At pitch ±90°, choose yaw = 0. Callers which
    only display/edit a transform should retain the original matrix until an
    angle is edited, avoiding roundoff from merely opening a dialog.
    """
    matrix = _transform(transform)
    rotation = matrix[:3, :3]
    cos_pitch = np.hypot(rotation[0, 0], rotation[1, 0])
    pitch = np.arctan2(-rotation[2, 0], cos_pitch)
    if cos_pitch > 1e-10:
        roll = np.arctan2(rotation[2, 1], rotation[2, 2])
        yaw = np.arctan2(rotation[1, 0], rotation[0, 0])
    else:
        roll = np.arctan2(-rotation[1, 2], rotation[1, 1])
        yaw = 0.0
    return matrix[:3, 3].copy(), np.rad2deg([roll, pitch, yaw])


@dataclass
class CameraCalibration:
    """Validated optical-camera intrinsics and optional explicit extrinsics.

    ``D`` accepts 0/4/5 plumb_bob coefficients, 8 rational_polynomial
    coefficients, or 4 equidistant coefficients. Empty plumb_bob means pinhole.
    ``K`` is upper triangular with positive focal lengths and last row [0,0,1].
    Translation and projected point coordinates are in metres.
    """

    K: np.ndarray
    D: np.ndarray
    width: int
    height: int
    distortion_model: str = "plumb_bob"
    T_camera_lidar: np.ndarray | None = None

    def __post_init__(self):
        self.K = _matrix(self.K, (3, 3), "K")
        if (self.K[0, 0] <= 0 or self.K[1, 1] <= 0
                or not np.allclose(self.K[2], [0, 0, 1], rtol=0, atol=1e-10)
                or not np.isclose(self.K[1, 0], 0, rtol=0, atol=1e-10)):
            raise ValueError(
                "K requires positive focal lengths, upper-triangular form and last row [0, 0, 1]."
            )
        self.width = _dimension(self.width, "width")
        self.height = _dimension(self.height, "height")
        try:
            coefficients = np.asarray(self.D, dtype=np.float64)
        except (TypeError, ValueError) as error:
            raise ValueError("D must be a finite vector of distortion coefficients.") from error
        if coefficients.ndim == 2 and 1 in coefficients.shape:
            coefficients = coefficients.reshape(-1)
        if coefficients.ndim != 1 or not np.isfinite(coefficients).all():
            raise ValueError("D must be a finite vector of distortion coefficients.")
        lengths = {"plumb_bob": (0, 4, 5), "rational_polynomial": (8,), "equidistant": (4,)}
        if self.distortion_model == "radtan5" and coefficients.size != 5:
            raise ValueError("radtan5 requires all 5 distortion coefficients.")
        if isinstance(self.distortion_model, str):
            self.distortion_model = {
                "radtan5": "plumb_bob", "radtan": "plumb_bob", "fisheye": "equidistant",
            }.get(self.distortion_model, self.distortion_model)
        if not isinstance(self.distortion_model, str) or self.distortion_model not in lengths:
            raise ValueError(
                "distortion_model must be plumb_bob, rational_polynomial or equidistant."
            )
        if coefficients.size not in lengths[self.distortion_model]:
            raise ValueError(
                f"{self.distortion_model} requires D lengths {lengths[self.distortion_model]}."
            )
        self.D = coefficients.copy()
        if self.T_camera_lidar is not None:
            self.T_camera_lidar = _transform(self.T_camera_lidar)


def _field(data: dict, names: tuple[str, ...], default=None):
    for name in names:
        if name in data:
            value = data[name]
            if isinstance(value, dict) and name in ("D", "d", "distortion_coefficients"):
                return value.get("data")
            return value
    return default


def _read_extrinsics(data, default=None):
    factors = {"m": 1.0, "metre": 1.0, "meter": 1.0, "cm": .01, "mm": .001}
    units = [data[name] for name in ("translation_units", "translation_unit", "length_unit")
             if name in data] or ["m"]
    if any(not isinstance(unit, str) or unit not in factors for unit in units):
        raise ValueError("translation_units must be 'm', 'cm' or 'mm' (default 'm').")
    if len({factors[unit] for unit in units}) > 1:
        raise ValueError("Conflicting translation_units / translation_unit / length_unit.")
    names = [name for name in TRANSFORM_DIRECTIONS if data.get(name) is not None]
    supplied_direction = data.get("transform_direction")
    if supplied_direction is not None and supplied_direction not in ("lidar_to_camera", "camera_to_lidar"):
        raise ValueError("transform_direction must be 'lidar_to_camera' or 'camera_to_lidar'.")
    if supplied_direction is None:
        convention = re.sub(r"\s+", "", str(data.get("transform_convention", "")))
        if convention == "p_camera_optical=R_camera_lidar*p_lidar+t_camera_lidar":
            supplied_direction = "lidar_to_camera"
    result = None
    for name in names:
        direction = TRANSFORM_DIRECTIONS[name]
        if direction is None:
            direction = supplied_direction
        elif (len(names) == 1 and supplied_direction is not None
              and supplied_direction != direction):
            raise ValueError(f"transform_direction conflicts with the direction named by {name}.")
        if direction not in ("lidar_to_camera", "camera_to_lidar"):
            raise ValueError("Generic transform requires transform_direction 'lidar_to_camera' "
                             "or 'camera_to_lidar'.")
        matrix = _transform(data[name], name)
        matrix[:3, 3] *= factors[units[0]]
        if direction == "camera_to_lidar":
            matrix = np.linalg.inv(matrix)
        if result is not None and not np.allclose(matrix, result, rtol=0, atol=3e-6):
            raise ValueError(f"Extrinsic matrices conflict: {name} is inconsistent with {names[0]}.")
        if result is None:
            result = matrix
    if result is not None:
        return result
    return None if "T_camera_lidar" in data and data["T_camera_lidar"] is None else default


def calibration_from_dict(data: dict, base: CameraCalibration | None = None) -> CameraCalibration:
    """Read ROS CameraInfo/calibration fields or the simple workspace schema.

    Intrinsics accept K/k/camera_matrix (3x3 or flat nine, optionally a ROS
    rows/cols/data mapping), D/d/distortion_coefficients, width/image_width,
    height/image_height and distortion_model. Missing fields inherit ``base``.

    Extrinsics accept T_camera_lidar or inverse T_lidar_camera, each 4x4 or flat
    sixteen. Generic transform/T/extrinsics matrices require transform_direction
    equal to lidar_to_camera or camera_to_lidar. Named matrices already specify
    direction. Redundant named transforms must agree after inversion. Short
    names T_C_L/T_L_C are supported. translation_units (or translation_unit /
    length_unit) accepts m, metre, meter, cm or mm and defaults to m. A base allows loading
    an extrinsics-only file alongside CameraInfo intrinsics, without mutation.
    """
    if not isinstance(data, dict):
        raise ValueError(
            "Calibration must be an object containing camera intrinsics and/or an explicit transform."
        )
    if not any(name in data for name in (*INTRINSIC_FIELDS, *TRANSFORM_DIRECTIONS)):
        raise ValueError("No recognized camera calibration fields in this document.")
    defaults = calibration_to_dict(base) if base is not None else {}
    extrinsics = _read_extrinsics(data, defaults.get("T_camera_lidar"))
    intrinsic = _field(data, ("K", "k", "camera_matrix"), defaults.get("K"))
    if intrinsic is None:
        raise ValueError(
            "Calibration requires K/camera_matrix intrinsics, "
            "or a CameraInfo base for an extrinsics-only file."
        )
    return CameraCalibration(
        K=intrinsic,
        D=_field(data, ("D", "d", "distortion_coefficients"), defaults.get("D", [])),
        width=_field(data, ("width", "image_width"), defaults.get("width")),
        height=_field(data, ("height", "image_height"), defaults.get("height")),
        distortion_model=data.get(
            "distortion_model", defaults.get("distortion_model", "plumb_bob")
        ),
        T_camera_lidar=extrinsics,
    )


def from_camera_info(data: dict) -> CameraCalibration:
    """Normalize ROS1/ROS2 CameraInfo for the actual raw optical image space.

    K/D (or k/d) describe full-resolution distorted pixels. Subtract the ROI
    offsets in unbinned pixels, then divide the intrinsic pixel rows by binning.
    Returned width/height describe the resulting cropped, binned raw image.
    ROS binning 0 means 1, and an all-zero ROI means the complete calibrated
    image. Incomplete binning blocks follow ROS integer dimension truncation.

    ROI values must be nonnegative integers, with either all four spatial
    values zero or positive width/height entirely inside the calibrated image.
    Binning must be nonnegative integral and leave a nonempty output image.

    R/P and roi.do_rectify describe rectification and do not replace raw K/D
    here. Already-rectified images require a calibration expressed in their
    rectified image/optical space. CameraInfo alone supplies no lidar extrinsics,
    so T_camera_lidar stays absent unless an explicit transform is supplied.
    """
    calibration = calibration_from_dict(data)
    binning_x = max(_nonnegative_integer(data.get("binning_x", 0), "binning_x"), 1)
    binning_y = max(_nonnegative_integer(data.get("binning_y", 0), "binning_y"), 1)
    roi = data.get("roi", {})
    if not isinstance(roi, dict):
        raise ValueError("CameraInfo roi must be an object with integer spatial values.")
    x_offset = _nonnegative_integer(roi.get("x_offset", 0), "roi.x_offset")
    y_offset = _nonnegative_integer(roi.get("y_offset", 0), "roi.y_offset")
    roi_width = _nonnegative_integer(roi.get("width", 0), "roi.width")
    roi_height = _nonnegative_integer(roi.get("height", 0), "roi.height")
    if (x_offset, y_offset, roi_width, roi_height) == (0, 0, 0, 0):
        roi_width, roi_height = calibration.width, calibration.height
    elif (roi_width == 0 or roi_height == 0
          or x_offset + roi_width > calibration.width
          or y_offset + roi_height > calibration.height):
        raise ValueError("CameraInfo ROI must have positive dimensions inside the calibrated image.")
    width, height = roi_width // binning_x, roi_height // binning_y
    if width == 0 or height == 0:
        raise ValueError("CameraInfo binning must leave a nonempty image inside the ROI.")
    matrix = calibration.K.copy()
    matrix[0, 2] -= x_offset
    matrix[1, 2] -= y_offset
    matrix[0] /= binning_x
    matrix[1] /= binning_y
    return CameraCalibration(matrix, calibration.D, width, height,
                             calibration.distortion_model, calibration.T_camera_lidar)


class _OpenCVSafeLoader(yaml.SafeLoader):
    """Only extend safe YAML with OpenCV's inert matrix mapping tag."""


_OpenCVSafeLoader.add_constructor(
    "tag:yaml.org,2002:opencv-matrix",
    lambda loader, node: loader.construct_mapping(node, deep=True),
)


def parse_calibration_text(contents: str, filename: str) -> dict:
    contents = contents.lstrip("\ufeff")
    # OpenCV writes a non-standard %YAML:1.0 directive; remove only that line.
    contents = re.sub(r"^%YAML:1\.0\s*\r?\n", "", contents, count=1)
    try:
        suffix = Path(filename).suffix.lower()
        data = (json.loads(contents) if suffix == ".json" else tomllib.loads(contents)
                if suffix == ".toml" else yaml.load(contents, Loader=_OpenCVSafeLoader))
    except (json.JSONDecodeError, yaml.YAMLError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"Cannot parse calibration file {Path(filename).name}: {error}") from error
    if not isinstance(data, dict):
        raise ValueError("Calibration must contain a mapping of parameter names and values.")
    return data


def load_calibration(path: str, base: CameraCalibration | None = None) -> CameraCalibration:
    """Safely load one explicitly described calibration in a supported format.

    Multiple candidates and untyped matrices require choices in the import
    dialog. Supply ``base`` for an extrinsics-only file. Import locally to keep
    the data model independent of the bundle reader during module loading.
    """
    from core.calibration_files import read_calibration_bundle

    bundle = read_calibration_bundle(path)
    if len(bundle.documents) != 1:
        raise ValueError("文件包含多组标定，请在导入窗口选择需要的内参和外参。")
    document = bundle.documents[0]
    if document.requires_interpretation:
        raise ValueError("矩阵说明不完整，请在导入窗口选择类型、原始方向和单位。")
    return calibration_from_dict(document.data, base=base)


def calibration_to_dict(calibration: CameraCalibration) -> dict:
    """Return JSON-safe workspace fields with explicitly named metre extrinsics."""
    return {
        "K": calibration.K.tolist(),
        "D": calibration.D.tolist(),
        "width": calibration.width,
        "height": calibration.height,
        "distortion_model": calibration.distortion_model,
        "T_camera_lidar": (
            None if calibration.T_camera_lidar is None else calibration.T_camera_lidar.tolist()
        ),
        "translation_units": "m",
    }


def project_points(xyz: np.ndarray, calibration: CameraCalibration,
                   image_size: tuple[int, int] | None = None, *,
                   clip_to_image: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Project lidar xyz in metres, returning pixel Nx2 and original row indices.

    Requires explicit T_camera_lidar. Filters nonfinite, nonpositive-depth and
    out-of-image results. image_size is (width, height), scaling calibration K
    for a resized image. Uses plumb_bob/rational_polynomial Brown-Conrady or
    equidistant fisheye distortion. Bounds include zero and exclude width/height.
    ``clip_to_image=False`` retains finite offscreen pixels for clipping lines;
    nonfinite and nonpositive-depth points are still excluded.
    """
    if calibration.T_camera_lidar is None:
        raise ValueError(
            "Projection requires explicit T_camera_lidar extrinsics. "
            "Import a lidar-to-camera calibration."
        )
    transform = _transform(calibration.T_camera_lidar)
    points = np.asarray(xyz, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3:
        raise ValueError("xyz must be an Nx3 array of lidar coordinates in metres.")
    width, height = calibration.width, calibration.height
    if image_size is not None:
        if len(image_size) != 2:
            raise ValueError("image_size must be (width, height).")
        width = _dimension(image_size[0], "image width")
        height = _dimension(image_size[1], "image height")
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        camera = points @ transform[:3, :3].T + transform[:3, 3]
        indices = np.flatnonzero(np.isfinite(camera).all(axis=1) & (camera[:, 2] > 0))
        normalized = camera[indices, :2] / camera[indices, 2, None]
        x, y = normalized.T
        radius_squared = x * x + y * y
        if calibration.distortion_model == "equidistant":
            radius = np.sqrt(radius_squared)
            theta = np.arctan(radius)
            theta_squared = theta * theta
            k1, k2, k3, k4 = calibration.D
            angular_polynomial = k1 + theta_squared * (
                k2 + theta_squared * (k3 + theta_squared * k4)
            )
            theta_distorted = theta * (1 + theta_squared * angular_polynomial)
            scale = np.divide(theta_distorted, radius, out=np.ones_like(radius), where=radius != 0)
            distorted_x, distorted_y = x * scale, y * scale
        else:
            coefficients = np.zeros(8)
            coefficients[:calibration.D.size] = calibration.D
            k1, k2, p1, p2, k3, k4, k5, k6 = coefficients
            radial = ((1 + radius_squared * (k1 + radius_squared * (k2 + radius_squared * k3)))
                      / (1 + radius_squared * (k4 + radius_squared * (k5 + radius_squared * k6))))
            distorted_x = x * radial + 2 * p1 * x * y + p2 * (radius_squared + 2 * x * x)
            distorted_y = y * radial + p1 * (radius_squared + 2 * y * y) + 2 * p2 * x * y
        pixels = np.column_stack((distorted_x, distorted_y, np.ones_like(x))) @ calibration.K.T
        pixels = pixels[:, :2]
        pixels *= [width / calibration.width, height / calibration.height]
    visible = np.isfinite(pixels).all(axis=1)
    if clip_to_image:
        visible &= ((pixels[:, 0] >= 0) & (pixels[:, 0] < width)
                    & (pixels[:, 1] >= 0) & (pixels[:, 1] < height))
    return pixels[visible], indices[visible]
