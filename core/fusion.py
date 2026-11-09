"""Calibrated image sampling and clipped, distortion-aware 3D box wireframes.

All geometry is in source lidar coordinates/metres. RGB sampling colors existing
points only; it does not infer depth or modify the input cloud/image arrays.
"""
from dataclasses import replace

import numpy as np
from numpy.polynomial import Polynomial

from core.calibration import CameraCalibration, project_points
from core.object_tracking import Box3D, box_corners


_EDGES = ((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6),
          (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7))


def sample_image_colors(xyz: np.ndarray, image: np.ndarray,
                        calibration: CameraCalibration) -> tuple[np.ndarray, np.ndarray]:
    """Return float32 RGB [0,1] and original indices of camera-visible points.

    Samples the pixel containing each projection. A per-pixel depth buffer keeps
    the nearest surface (tolerance max(5 cm, 1% depth)); this cannot detect
    occlusion not sampled by lidar. Gray/16-bit images use the viewer's display
    normalization. Fully transparent RGBA pixels do not color points.
    """
    image = np.asarray(image)
    if (image.ndim not in (2, 3) or not image.shape[0] or not image.shape[1]
            or (image.ndim == 3 and image.shape[2] not in (3, 4))):
        raise ValueError("Image must be a nonempty grayscale, RGB or RGBA array.")
    pixels, indices = project_points(xyz, calibration, (image.shape[1], image.shape[0]))
    if not len(indices):
        return np.empty((0, 3), np.float32), indices
    pixel_indices = pixels.astype(np.intp)
    u, v = pixel_indices.T
    transform = calibration.T_camera_lidar
    depth = np.asarray(xyz)[indices] @ transform[2, :3] + transform[2, 3]
    _, bins = np.unique(v * image.shape[1] + u, return_inverse=True)
    nearest = np.full(int(bins.max()) + 1, np.inf)
    np.minimum.at(nearest, bins, depth)
    keep = depth <= nearest[bins] + np.maximum(.05, nearest[bins] * .01)
    if image.ndim == 3 and image.shape[2] == 4:
        keep &= image[v, u, 3] > 0
    indices, u, v = indices[keep], u[keep], v[keep]
    if image.dtype == np.uint8:
        samples = image[v, u].astype(np.float32) / 255
    else:
        finite = np.isfinite(image)
        low, high = np.percentile(image[finite], [1, 99]) if finite.any() else (0, 1)
        samples = image[v, u].astype(np.float32)
        samples = np.nan_to_num(np.clip((samples - low) / max(high - low, 1e-12), 0, 1), nan=0)
    if samples.ndim == 1:
        samples = np.repeat(samples[:, None], 3, axis=1)
    return np.ascontiguousarray(samples[:, :3], dtype=np.float32), indices


def _clip_segment(a, b, width, height):
    """Liang–Barsky clipping, retaining lines whose endpoints are both outside."""
    delta = b - a
    lower, upper = 0., 1.
    for p, q in ((-delta[0], a[0]), (delta[0], width - 1 - a[0]),
                 (-delta[1], a[1]), (delta[1], height - 1 - a[1])):
        if p == 0:
            if q < 0:
                return None
        elif p < 0:
            lower = max(lower, q / p)
        else:
            upper = min(upper, q / p)
        if lower > upper:
            return None
    segment = np.array([a + lower * delta, a + upper * delta])
    # Roundoff at a boundary must not enlarge the scene/scrollbars.
    return np.clip(segment, [0, 0], [width - 1, height - 1])


def _distortion_poles(a, b, calibration):
    """Find rational denominator zeros along an optical-space line segment.

    Multiplying by Z**6 produces a polynomial in line parameter t. Testing roots
    also catches two poles inside an interval whose endpoint signs are equal.
    """
    if calibration.distortion_model != "rational_polynomial" or not np.any(calibration.D[5:]):
        return np.empty(0)
    scale = max(1., np.abs([a, b]).max())
    a, b = a / scale, b / scale
    x, y, z = [Polynomial([start, end - start]) for start, end in zip(a, b)]
    radius = x * x + y * y
    k4, k5, k6 = calibration.D[5:]
    denominator = z**6 + k4 * radius * z**4 + k5 * radius**2 * z**2 + k6 * radius**3
    if not np.any(denominator.coef):
        return np.array([0., 1.])
    roots = denominator.roots()
    return roots.real[(np.abs(roots.imag) < 1e-7) & (roots.real >= 0) & (roots.real <= 1)]


def _refine_edges(edges, calibration, image_size):
    """Batch adaptive quarter/midpoint checks to a 0.35 px chord tolerance.

    Perspective compresses samples near a camera-plane crossing. Refinement in
    projected space handles that without paying a dense sampling cost on normal
    distant boxes. Work is bounded even for pathological distortion parameters.
    """
    pending = np.asarray(edges, dtype=float).reshape(-1, 2, 3)
    accepted = []
    fractions = np.linspace(0, 1, 5)
    for depth in range(15):
        if not len(pending):
            break
        samples = (pending[:, 0, None] * (1 - fractions[None, :, None])
                   + pending[:, 1, None] * fractions[None, :, None])
        pixels, indices = project_points(samples.reshape(-1, 3), calibration, image_size,
                                         clip_to_image=False)
        projected = np.full((len(samples) * 5, 2), np.nan)
        projected[indices] = pixels
        projected = projected.reshape(-1, 5, 2)
        finite = np.isfinite(projected).all(axis=(1, 2))
        delta = projected[:, -1] - projected[:, 0]
        with np.errstate(invalid="ignore", over="ignore"):
            relative = projected[:, 1:-1] - projected[:, :1]
            position = np.clip(np.sum(relative * delta[:, None], axis=2)
                               / np.maximum(np.sum(delta * delta, axis=1)[:, None], 1e-24), 0, 1)
            error = np.linalg.norm(relative - position[:, :, None] * delta[:, None], axis=2).max(axis=1)
        good = finite & (error <= .35)
        accepted.extend(projected[good][:, [0, -1]])
        unresolved = projected[~good]
        if depth == 14 or len(pending) * 2 + len(accepted) > 8192:
            # Keep only finite adjacent samples at the work bound. Pole-bearing
            # intervals were removed before refinement and cannot be bridged.
            for line in unresolved:
                for a, b in zip(line, line[1:]):
                    if np.isfinite([a, b]).all():
                        accepted.append(np.array([a, b]))
            break
        splitting = pending[~good]
        midpoint = (splitting[:, 0] + splitting[:, 1]) * .5
        pending = np.concatenate((np.stack((splitting[:, 0], midpoint), axis=1),
                                  np.stack((midpoint, splitting[:, 1]), axis=1)))
    return accepted


def project_box_segments(box: Box3D, calibration: CameraCalibration,
                         image_size: tuple[int, int]) -> np.ndarray:
    """Return Mx2x2 finite pixel line segments, clipped to the current image.

    Clip source edges against camera Z=1 mm before perspective division. Refine
    projected curves adaptively; never bridge through distortion poles.
    """
    if calibration.T_camera_lidar is None:
        raise ValueError("Box projection requires explicit extrinsics.")
    width, height = image_size
    if width < 1 or height < 1:
        raise ValueError("Image dimensions must be positive.")
    transform = calibration.T_camera_lidar
    corners = box_corners(box) @ transform[:3, :3].T + transform[:3, 3]
    optical = replace(calibration, T_camera_lidar=np.eye(4))
    near = .001
    edges = []
    for first, last in _EDGES:
        a, b = corners[first].copy(), corners[last].copy()
        if a[2] < near and b[2] < near:
            continue
        if a[2] < near:
            a += (b - a) * ((near - a[2]) / (b[2] - a[2]))
            a[2] = near
        elif b[2] < near:
            b += (a - b) * ((near - b[2]) / (a[2] - b[2]))
            b[2] = near
        samples = np.linspace(a, b, 17)
        poles = _distortion_poles(a, b, calibration)
        for i in range(16):
            if np.any((poles >= i / 16 - 1e-8) & (poles <= (i + 1) / 16 + 1e-8)):
                continue
            edges.append([samples[i], samples[i + 1]])
    segments = []
    for a, b in _refine_edges(edges, optical, image_size):
        clipped = _clip_segment(a, b, width, height)
        if clipped is not None:
            segments.append(clipped)
    return np.asarray(segments, dtype=np.float64).reshape(-1, 2, 2)
