"""Editable source-coordinate boxes and bounded, local geometric association.

Association uses deterministic reservoir sampling, multiple yaw/position starts,
and trimmed nearest-neighbour rigid alignment. Only translation and rotation
around source Z are fitted. A match is a suggestion with a score measuring
geometric agreement. Similar objects, occlusion, sparse returns and
uncompensated sensor motion can require manual correction.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import math
from numbers import Integral, Real

import numpy as np


def _number(value, name):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _vector(value, name):
    try:
        values = tuple(value)
    except TypeError as error:
        raise ValueError(f"{name} must contain three finite numbers") from error
    if len(values) != 3:
        raise ValueError(f"{name} must contain three finite numbers")
    return tuple(_number(item, name) for item in values)


def _frame_index(value):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral) or value < 0:
        raise ValueError("frame_index must be a nonnegative integer")
    return int(value)


@dataclass(frozen=True, slots=True)
class Box3D:
    center: tuple[float, float, float]
    size: tuple[float, float, float]
    yaw: float = 0.0
    roll: float = 0.0
    pitch: float = 0.0

    def __post_init__(self):
        object.__setattr__(self, "center", _vector(self.center, "center"))
        object.__setattr__(self, "size", _vector(self.size, "size"))
        if any(value <= 0 for value in self.size):
            raise ValueError("size must be strictly positive")
        object.__setattr__(self, "yaw", math.remainder(_number(self.yaw, "yaw"), 2 * math.pi))
        object.__setattr__(self, "roll", math.remainder(_number(self.roll, "roll"), 2 * math.pi))
        object.__setattr__(self, "pitch", math.remainder(_number(self.pitch, "pitch"), 2 * math.pi))

    def to_dict(self):
        return {"center": list(self.center), "size": list(self.size), "yaw": self.yaw,
                "roll": self.roll, "pitch": self.pitch}

    @classmethod
    def from_dict(cls, value):
        return cls(value["center"], value["size"], value.get("yaw", 0.0),
                   roll=value.get("roll", 0.0), pitch=value.get("pitch", 0.0))


@dataclass(frozen=True, slots=True)
class Observation:
    frame_index: int
    box: Box3D
    manual: bool = True
    score: float | None = None

    def __post_init__(self):
        object.__setattr__(self, "frame_index", _frame_index(self.frame_index))
        if not isinstance(self.box, Box3D):
            raise ValueError("box must be a Box3D")
        if not isinstance(self.manual, bool):
            raise ValueError("manual must be a boolean")
        if self.score is not None:
            score = _number(self.score, "score")
            if not 0 <= score <= 1:
                raise ValueError("score must lie between zero and one")
            object.__setattr__(self, "score", score)

    def to_dict(self):
        return {"frame_index": self.frame_index, "box": self.box.to_dict(),
                "manual": self.manual, "score": self.score}

    @classmethod
    def from_dict(cls, value):
        return cls(value["frame_index"], Box3D.from_dict(value["box"]),
                   value.get("manual", True), value.get("score"))


@dataclass(slots=True)
class ObjectTrack:
    id: str
    name: str
    observations: dict[int, Observation] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("id must be a nonempty string")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must be a nonempty string")
        if not isinstance(self.observations, Mapping):
            raise ValueError("observations must map frame indices to observations")
        for index, observation in self.observations.items():
            if (not isinstance(observation, Observation)
                    or _frame_index(index) != observation.frame_index):
                raise ValueError("observation key must equal its original frame_index")
        self.observations = dict(self.observations)

    def to_dict(self):
        return {"id": self.id, "name": self.name,
                "observations": [self.observations[index].to_dict()
                                 for index in sorted(self.observations)]}

    @classmethod
    def from_dict(cls, value):
        observations = {}
        for item in value.get("observations", []):
            observation = Observation.from_dict(item)
            if observation.frame_index in observations:
                raise ValueError("duplicate observation frame_index")
            observations[observation.frame_index] = observation
        return cls(value["id"], value["name"], observations)


@dataclass(frozen=True, slots=True)
class TrackMatch:
    box: Box3D | None
    score: float
    reason: str


def _xyz(value):
    points = np.asarray(value)
    if points.ndim != 2 or points.shape[1] != 3:
        raise ValueError("xyz must be an N by 3 array")
    if not np.issubdtype(points.dtype, np.number) or np.iscomplexobj(points):
        raise ValueError("xyz must contain real numbers")
    return points


def _rotation(yaw):
    cosine, sine = math.cos(yaw), math.sin(yaw)
    return np.array([[cosine, -sine, 0.0], [sine, cosine, 0.0], [0.0, 0.0, 1.0]])


def box_rotation(box):
    """Local-to-source rotation, using Rz(yaw) Ry(pitch) Rx(roll)."""
    cr, sr = math.cos(box.roll), math.sin(box.roll)
    cp, sp = math.cos(box.pitch), math.sin(box.pitch)
    rx = np.array([[1., 0., 0.], [0., cr, -sr], [0., sr, cr]])
    ry = np.array([[cp, 0., sp], [0., 1., 0.], [-sp, 0., cp]])
    return _rotation(box.yaw) @ ry @ rx


def fit_box(xyz, yaw=0.0, *, roll=0.0, pitch=0.0):
    """Fit extrema in the requested orientation, ignoring nonfinite rows.

    Flat dimensions receive one millimetre of thickness so the result remains
    editable.
    """
    orientation = Box3D((0, 0, 0), (1, 1, 1), yaw, roll=roll, pitch=pitch)
    points = _xyz(xyz)
    points = points[np.isfinite(points).all(axis=1)]
    if len(points) == 0:
        raise ValueError("cannot fit a box without finite points")
    rotation = box_rotation(orientation)
    local = points @ rotation
    lower, upper = local.min(axis=0), local.max(axis=0)
    return Box3D((0.5 * (lower + upper)) @ rotation.T,
                 np.maximum(upper - lower, 0.001), yaw, roll=roll, pitch=pitch)


def points_in_box(xyz, box, margin=0.0):
    """Return an input-length boolean mask, including finite boundary points."""
    points = _xyz(xyz)
    margin = _number(margin, "margin")
    if margin < 0:
        raise ValueError("margin must be nonnegative")
    finite = np.isfinite(points).all(axis=1)
    local = (points[finite] - box.center) @ box_rotation(box)
    result = np.zeros(len(points), dtype=bool)
    result[finite] = (np.abs(local) <= np.asarray(box.size) * 0.5 + margin + 1e-9).all(axis=1)
    return result


def box_corners(box):
    """Return four bottom corners followed by the corresponding top corners."""
    signs = np.array([[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
                      [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]])
    return signs * (np.asarray(box.size) * 0.5) @ box_rotation(box).T + box.center


def _region_sample(points, box, distance, limit, cancel):
    """Scan bounded chunks and retain a uniform fixed-size reservoir."""
    rng = np.random.default_rng(0)
    selected = np.empty((0, 3), dtype=float)
    priorities = np.empty(0)
    radius = np.linalg.norm(box.size) * 0.5 + distance
    for start in range(0, len(points), 32768):
        if cancel():
            return None
        chunk = np.asarray(points[start:start + 32768], dtype=float)
        if distance == 0:
            keep = points_in_box(chunk, box)
        else:
            chunk = chunk[np.isfinite(chunk).all(axis=1)]
            keep = np.linalg.norm(chunk - box.center, axis=1) <= radius
        chunk = chunk[keep]
        selected = np.concatenate([selected, chunk])
        priorities = np.concatenate([priorities, rng.random(len(chunk))])
        if len(selected) > limit:
            indices = np.argpartition(priorities, limit - 1)[:limit]
            selected, priorities = selected[indices], priorities[indices]
    return selected


def _alignment(template, tree, center, angle, tolerance, cancel):
    """Trim outlying correspondences and fit only XY rotation plus XYZ shift."""
    for _ in range(35):
        if cancel():
            return None
        moving = template @ _rotation(angle).T + center
        distances, indices = tree.query(moving, k=1)
        keep = distances <= max(tolerance, float(np.quantile(distances, 0.85)))
        source, target = template[keep], tree.data[indices[keep]]
        source_mean, target_mean = source.mean(axis=0), target.mean(axis=0)
        source_xy, target_xy = source[:, :2] - source_mean[:2], target[:, :2] - target_mean[:2]
        cross = np.sum(source_xy[:, 0] * target_xy[:, 1] - source_xy[:, 1] * target_xy[:, 0])
        dot = np.sum(source_xy * target_xy)
        new_angle = math.atan2(float(cross), float(dot))
        new_center = target_mean - source_mean @ _rotation(new_angle).T
        change = (np.linalg.norm(new_center - center)
                  + abs(math.remainder(new_angle - angle, 2 * math.pi)))
        center, angle = new_center, new_angle
        if change < 1e-5:
            break
    return center, angle


def _match_quality(template, current, tree, center, angle, box, tolerance):
    from scipy.spatial import cKDTree
    moving = template @ _rotation(angle).T + center
    distances = tree.query(moving, k=1)[0]
    coverage = float(np.mean(distances <= tolerance))
    candidate = Box3D(center, box.size, box.yaw + angle, roll=box.roll, pitch=box.pitch)
    inside = current[points_in_box(current, candidate, margin=tolerance * 0.2)]
    if len(inside) < 6:
        return 0.0, math.inf
    reverse = cKDTree(moving).query(inside, k=1)[0]
    reverse_coverage = float(np.mean(reverse <= tolerance))
    residual = float(np.mean(np.minimum(distances, tolerance * 2)))
    reverse_residual = float(np.mean(np.minimum(reverse, tolerance * 2)))
    # Symmetric coverage penalizes a template accidentally fitting a small patch.
    score = (min(coverage, reverse_coverage)
             * math.exp(-(residual + reverse_residual) / (4 * tolerance)))
    return score, (residual + reverse_residual) * 0.5


def track_step(previous_xyz, current_xyz, box, max_distance=2.0, cancel=None):
    """Suggest a same-sized neighbouring-frame box or return a failure reason.

    Alignment uses at most 512 source points; validation retains up to 4096
    source and 4096 local destination points so canopy sampling gaps are not
    confused with missing geometry. The source scans in 32768-point chunks,
    alignment has at most 18 starts and 35
    iterations per start, and cancellation is checked between chunks/iterations.
    max_distance bounds the Euclidean displacement of the box centre in metres.
    A failed match always has box=None. Scores lie in [0, 1], with success gated
    at 0.65. Reasons are matched, cancelled, insufficient_previous_points,
    insufficient_current_points, poor_match, or displacement_exceeded.
    """
    # Geometry, editing and startup do not need the matching engine. Import it
    # in the tracking job on first use, without delaying the initial workspace.
    from scipy.spatial import cKDTree

    max_distance = _number(max_distance, "max_distance")
    if max_distance < 0:
        raise ValueError("max_distance must be nonnegative")
    cancelled: Callable[[], bool] = cancel if cancel is not None else lambda: False
    if cancelled():
        return TrackMatch(None, 0.0, "cancelled")
    previous, current = _xyz(previous_xyz), _xyz(current_xyz)
    source = _region_sample(previous, box, 0, 4096, cancelled)
    if source is None:
        return TrackMatch(None, 0.0, "cancelled")
    if len(source) < 6 or np.linalg.norm(np.ptp(source, axis=0)) < 1e-6:
        return TrackMatch(None, 0.0, "insufficient_previous_points")
    destination = _region_sample(current, box, max_distance + 1e-9, 4096, cancelled)
    if destination is None:
        return TrackMatch(None, 0.0, "cancelled")
    if len(destination) < 6:
        return TrackMatch(None, 0.0, "insufficient_current_points")

    # Template uses source axes so the fitted angle is relative to the old box.
    support = source - box.center
    template = support[np.linspace(0, len(support) - 1, min(512, len(support)), dtype=int)]
    tree = cKDTree(destination)
    tolerance = max(0.035, min(0.2, float(np.linalg.norm(box.size)) * 0.07))
    cell_size = max(0.2, min(box.size) * 0.8)
    _, inverse, counts = np.unique(np.floor(destination / cell_size), axis=0,
                                  return_inverse=True, return_counts=True)
    seeds = [np.asarray(box.center), destination.mean(axis=0)]
    for index in np.argsort(-counts, kind="stable")[:16]:
        seeds.append(destination[inverse == index].mean(axis=0))

    starts = []
    for angle in np.arange(-math.pi, math.pi, math.pi / 6):
        if cancelled():
            return TrackMatch(None, 0.0, "cancelled")
        rotated_mean = template.mean(axis=0) @ _rotation(angle).T
        for seed in seeds:
            center = seed - rotated_mean
            distances = tree.query(template @ _rotation(angle).T + center, k=1)[0]
            cost = float(np.mean(np.minimum(distances, tolerance * 2)))
            starts.append((cost, center, angle))

    best_box, best_score, best_cost = None, 0.0, math.inf
    beyond_bound = False
    # A canopy's point centroid can be metres away from its box centre. Keep
    # the previous pose as a seed instead of recentering every candidate.
    candidates = [(0., np.asarray(box.center), 0.)]
    candidates.extend(sorted(starts, key=lambda item: item[0])[:17])
    for _, center, angle in candidates:
        aligned = _alignment(template, tree, center, angle, tolerance, cancelled)
        if aligned is None:
            return TrackMatch(None, 0.0, "cancelled")
        center, angle = aligned
        score, cost = _match_quality(support, destination, tree, center, angle, box, tolerance)
        if np.linalg.norm(center - box.center) > max_distance + 1e-6:
            beyond_bound |= score >= 0.65
            continue
        # Symmetric trunks/crowns can fit equally well after a half-turn. A
        # small continuity preference prevents sampling noise flipping heading;
        # the geometric acceptance gate itself remains unchanged.
        objective = cost + tolerance * .1 * abs(math.remainder(angle, 2 * math.pi)) / math.pi
        if score >= 0.65 and objective < best_cost:
            best_box = Box3D(center, box.size, box.yaw + angle, roll=box.roll, pitch=box.pitch)
            best_score, best_cost = score, objective
    if cancelled():
        return TrackMatch(None, 0.0, "cancelled")
    if best_box is None:
        reason = "displacement_exceeded" if beyond_bound else "poor_match"
        return TrackMatch(None, 0.0, reason)
    return TrackMatch(best_box, best_score, "matched")
