"""Colors for the already-visible subset of a lidar-to-image projection."""

import numpy as np

from core.calibration import CameraCalibration
from core.color.colormaps import sample_colormap


_PALETTE = np.rint(sample_colormap("default", np.linspace(0, 1, 16)) * 255).astype(np.uint8)
_SOLID = np.array([40, 230, 140], dtype=np.uint8)


def projection_colors(
    xyz: np.ndarray, calibration: CameraCalibration | None, mode: str = "depth"
) -> np.ndarray:
    """Return Nx3 uint8 RGB colors without filtering or reordering points.

    The caller supplies the visible projected lidar points in metres. Depth is
    optical-camera Z after the explicit ``T_camera_lidar`` transform; height is
    lidar Z. Scalar modes use 16 samples of the existing default colormap, with
    the visible subset's 2nd-to-98th percentile range. Constant values use the
    first palette color. Solid mode retains the existing green overlay color.
    Nonfinite points/transforms are rejected rather than silently recolored.
    """
    if mode not in ("depth", "height", "solid"):
        raise ValueError("Projection color mode must be depth, height or solid.")
    try:
        points = np.asarray(xyz, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise ValueError("Projection points must be finite numeric Nx3 values.") from error
    if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
        raise ValueError("Projection points must be a finite Nx3 array.")

    if mode == "depth":
        if calibration is None or calibration.T_camera_lidar is None:
            raise ValueError("Depth coloring requires explicit T_camera_lidar extrinsics.")
        transform = np.asarray(calibration.T_camera_lidar, dtype=np.float64)
        if transform.shape != (4, 4) or not np.isfinite(transform).all():
            raise ValueError("T_camera_lidar must be a finite 4x4 matrix.")
        with np.errstate(over="ignore", invalid="ignore"):
            values = points @ transform[2, :3] + transform[2, 3]
        if not np.isfinite(values).all():
            raise ValueError("Transformed camera depth must remain finite.")
    elif mode == "height":
        values = points[:, 2]
    else:
        return np.tile(_SOLID, (len(points), 1))

    if not len(points):
        return np.empty((0, 3), dtype=np.uint8)
    # Scaling first prevents overflow when subtracting large finite endpoints.
    scale = max(float(np.max(np.abs(values))), 1.0)
    scaled = values / scale
    low, high = np.percentile(scaled, [2, 98])
    normalized = np.clip((scaled - low) / (high - low), 0, 1) if high > low else np.zeros_like(values)
    indices = np.floor(normalized * 15 + 0.5).astype(np.intp)
    return _PALETTE[indices]
