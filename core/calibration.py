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

import numpy as np
import yaml


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


def calibration_from_dict(data: dict, base: CameraCalibration | None = None) -> CameraCalibration:
    """Read ROS CameraInfo/calibration fields or the simple workspace schema.

    Intrinsics accept K/k/camera_matrix (3x3 or flat nine, optionally a ROS
    rows/cols/data mapping), D/d/distortion_coefficients, width/image_width,
    height/image_height and distortion_model. Missing fields inherit ``base``.

    Extrinsics accept T_camera_lidar or inverse T_lidar_camera, each 4x4 or flat
    sixteen. Generic transform/T/extrinsics matrices require transform_direction
    equal to lidar_to_camera or camera_to_lidar. Named matrices already specify
    direction. Supply at most one transform. translation_units accepts m, cm or
    mm and defaults to m. Values are normalized to metres. A base allows loading
    an extrinsics-only file alongside CameraInfo intrinsics, without mutation.
    """
    if not isinstance(data, dict):
        raise ValueError(
            "Calibration must be an object containing camera intrinsics and/or an explicit transform."
        )
    defaults = calibration_to_dict(base) if base is not None else {}
    unit = data.get("translation_units", "m")
    factors = {"m": 1.0, "cm": 0.01, "mm": 0.001}
    if not isinstance(unit, str) or unit not in factors:
        raise ValueError("translation_units must be 'm', 'cm' or 'mm' (default 'm').")
    transform_names = [
        name for name in ("T_camera_lidar", "T_lidar_camera", "transform", "T", "extrinsics")
        if name in data and data[name] is not None
    ]
    if len(transform_names) > 1:
        raise ValueError("Supply one extrinsic matrix, T_camera_lidar or T_lidar_camera.")
    extrinsics = defaults.get("T_camera_lidar")
    if transform_names:
        name = transform_names[0]
        direction = {
            "T_camera_lidar": "lidar_to_camera", "T_lidar_camera": "camera_to_lidar"
        }.get(name)
        supplied_direction = data.get("transform_direction")
        if direction is None:
            direction = supplied_direction
        elif supplied_direction is not None and supplied_direction != direction:
            raise ValueError(
                f"transform_direction conflicts with the direction named by {name}."
            )
        if direction not in ("lidar_to_camera", "camera_to_lidar"):
            raise ValueError(
                "Generic transform requires transform_direction 'lidar_to_camera' or 'camera_to_lidar'."
            )
        extrinsics = _transform(data[name], name)
        extrinsics[:3, 3] *= factors[unit]
        if direction == "camera_to_lidar":
            extrinsics = np.linalg.inv(extrinsics)
    elif "T_camera_lidar" in data and data["T_camera_lidar"] is None:
        extrinsics = None
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


def load_calibration(path: str, base: CameraCalibration | None = None) -> CameraCalibration:
    """Safely load a UTF-8 JSON/YAML calibration using calibration_from_dict.

    YAML supports standard ROS camera_matrix/data, distortion_coefficients/data,
    image_width/image_height. JSON uses K, D, width, height and optionally an
    explicitly directed transform. Supply ``base`` for extrinsics-only files.
    """
    contents = Path(path).read_text(encoding="utf-8-sig")
    try:
        if Path(path).suffix.lower() == ".json":
            data = json.loads(contents)
        else:
            data = yaml.safe_load(contents)
    except (json.JSONDecodeError, yaml.YAMLError) as error:
        raise ValueError(f"Cannot parse calibration file {Path(path).name}: {error}") from error
    return calibration_from_dict(data, base=base)


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
                   image_size: tuple[int, int] | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Project lidar xyz in metres, returning pixel Nx2 and original row indices.

    Requires explicit T_camera_lidar. Filters nonfinite, nonpositive-depth and
    out-of-image results. image_size is (width, height), scaling calibration K
    for a resized image. Uses plumb_bob/rational_polynomial Brown-Conrady or
    equidistant fisheye distortion. Bounds include zero and exclude width/height.
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
    visible = (np.isfinite(pixels).all(axis=1) & (pixels[:, 0] >= 0) & (pixels[:, 0] < width)
               & (pixels[:, 1] >= 0) & (pixels[:, 1] < height))
    return pixels[visible], indices[visible]
