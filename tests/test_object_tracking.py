import json
import math
from unittest.mock import patch

import numpy as np
import pytest
from scipy.spatial import cKDTree

from core.object_tracking import (
    Box3D,
    ObjectTrack,
    Observation,
    box_corners,
    fit_box,
    points_in_box,
    track_step,
)


def object_points(seed=8, count=480):
    """Asymmetric, non-uniform solid with a protruding upper corner."""
    rng = np.random.default_rng(seed)
    base = rng.uniform([-0.95, -0.4, -0.3], [0.95, 0.4, 0.15], (count, 3))
    upper = rng.uniform([0.3, 0.05, 0.15], [0.95, 0.4, 0.65], (count // 3, 3))
    return np.concatenate([base, upper])


def move_points(points, translation, yaw):
    cosine, sine = math.cos(yaw), math.sin(yaw)
    rotation = np.array([[cosine, -sine, 0], [sine, cosine, 0], [0, 0, 1]])
    return points @ rotation.T + translation


def test_models_round_trip_original_frame_indices_and_manual_status():
    box = Box3D([1, 2, 3], [4, 2, 1], 0.4)
    track = ObjectTrack("target-7", "Target seven", {
        105: Observation(105, box),
        109: Observation(109, box, manual=False, score=0.82),
    })
    payload = json.loads(json.dumps(track.to_dict(), allow_nan=False))
    assert [item["frame_index"] for item in payload["observations"]] == [105, 109]
    restored = ObjectTrack.from_dict(payload)
    assert restored == track
    assert restored.observations[105].manual is True
    assert restored.observations[109].manual is False
    assert restored.observations[109].score == 0.82
    assert Box3D.from_dict(box.to_dict()) == box
    assert Observation.from_dict(track.observations[105].to_dict()).box == box


@pytest.mark.parametrize("center,size,yaw", [
    ([0, 0], [1, 1, 1], 0),
    ([0, 0, float("nan")], [1, 1, 1], 0),
    ([0, 0, 0], [1, 0, 1], 0),
    ([0, 0, 0], [1, -1, 1], 0),
    ([0, 0, 0], [1, 1, float("inf")], 0),
    ([0, 0, 0], [1, 1, 1], float("nan")),
])
def test_box_rejects_invalid_geometry(center, size, yaw):
    with pytest.raises((ValueError, TypeError)):
        Box3D(center, size, yaw)


def test_models_reject_invalid_indices_scores_and_conflicting_observations():
    box = Box3D([0, 0, 0], [1, 1, 1], 0)
    for index in [-1, 0.5, True]:
        with pytest.raises((ValueError, TypeError)):
            Observation(index, box)
    for score in [-0.1, 1.1, float("nan")]:
        with pytest.raises((ValueError, TypeError)):
            Observation(1, box, score=score)
    with pytest.raises((ValueError, TypeError)):
        Observation(1, box, manual="false")
    with pytest.raises((ValueError, TypeError)):
        ObjectTrack("", "Target", {})
    with pytest.raises((ValueError, TypeError)):
        ObjectTrack("one", "Target", {2: Observation(1, box)})
    duplicate = Observation(1, box).to_dict()
    with pytest.raises((ValueError, TypeError)):
        ObjectTrack.from_dict({"id": "one", "name": "Target",
                               "observations": [duplicate, duplicate]})


def test_fit_box_and_membership_use_the_yaw_rotated_coordinate_system():
    points = np.array([[2, 3, 4], [2, 7, 4], [0, 3, 6], [0, 7, 6], [np.nan, 0, 0]])
    box = fit_box(points, yaw=math.pi / 2)
    np.testing.assert_allclose(box.center, [1, 5, 5], atol=1e-12)
    np.testing.assert_allclose(box.size, [4, 2, 2], atol=1e-12)
    np.testing.assert_array_equal(points_in_box(points, box), [True, True, True, True, False])
    candidates = np.array([[2.1, 5, 5], [1, 7.1, 5], [1, 5, 6.1], [1, 5, 5]])
    np.testing.assert_array_equal(points_in_box(candidates, box), [False, False, False, True])
    assert points_in_box(candidates, box, margin=0.11).all()
    corners = box_corners(box)
    assert corners.shape == (8, 3)
    np.testing.assert_allclose(corners.min(axis=0), [0, 3, 4], atol=1e-12)
    np.testing.assert_allclose(corners.max(axis=0), [2, 7, 6], atol=1e-12)
    assert points_in_box(corners, box).all()


def test_fit_box_gives_flat_clouds_positive_thickness_and_rejects_empty_input():
    assert all(value > 0 for value in fit_box([[1, 2, 3]]).size)
    for points in [np.empty((0, 3)), [[np.inf, 0, 0]], [[1, 2]]]:
        with pytest.raises(ValueError):
            fit_box(points)
    with pytest.raises(ValueError):
        points_in_box([[0, 0, 0]], Box3D([0, 0, 0], [1, 1, 1], 0), margin=-1)


@pytest.mark.parametrize("translation,yaw", [
    ([0.75, -0.6, 0.2], 0),
    ([0.8, 0.45, -0.1], 0.43),
    ([-1.1, 0.6, 0.15], -0.62),
])
def test_tracking_follows_translation_and_yaw_and_preserves_box_size(translation, yaw):
    previous = object_points()
    box = Box3D([0, 0, 0.175], [2, 0.9, 1.05], 0)
    current = move_points(previous, translation, yaw)
    clutter = np.random.default_rng(9).uniform([-4, -4, -2], [4, 4, 2], (250, 3))
    result = track_step(np.concatenate([previous, clutter]),
                        np.concatenate([current, clutter]), box)
    assert result.box is not None, result.reason
    expected_center = move_points(np.array([box.center]), translation, yaw)[0]
    np.testing.assert_allclose(result.box.center, expected_center, atol=0.07)
    assert abs(math.remainder(result.box.yaw - yaw, 2 * math.pi)) < 0.08
    assert result.box.size == box.size
    assert 0.65 <= result.score <= 1
    assert result.reason == "matched"


def test_tracking_accepts_resampled_geometry_and_small_sensor_noise():
    previous = object_points(seed=17, count=800)
    current = move_points(object_points(seed=18, count=800), [0.45, 0.25, 0.08], 0.21)
    current += np.random.default_rng(19).normal(0, 0.005, current.shape)
    box = Box3D([0, 0, 0.175], [2, 0.9, 1.05], 0)
    result = track_step(previous, current, box)
    assert result.box is not None, result.reason
    np.testing.assert_allclose(result.box.center, [0.45, 0.25, 0.255], atol=0.11)
    assert abs(result.box.yaw - 0.21) < 0.12


def test_tracking_stops_for_vanished_and_incompatible_targets():
    previous = object_points()
    box = Box3D([0, 0, 0.175], [2, 0.9, 1.05], 0)
    for current in [np.empty((0, 3)), previous + [10, 0, 0], np.zeros((500, 3))]:
        result = track_step(previous, current, box)
        assert result.box is None
        assert result.reason != "matched"
        assert 0 <= result.score <= 1


def test_tracking_rejects_a_match_beyond_the_displacement_limit():
    previous = object_points()
    box = Box3D([0, 0, 0.175], [2, 0.9, 1.05], 0)
    result = track_step(previous, previous + [1.5, 0, 0], box, max_distance=0.5)
    assert result.box is None
    with pytest.raises(ValueError):
        track_step(previous, previous, box, max_distance=float("nan"))
    with pytest.raises(ValueError):
        track_step(previous, previous, box, max_distance=-1)


def test_tracking_handles_missing_source_support_and_nonfinite_rows():
    points = object_points()
    box = Box3D([0, 0, 0.175], [2, 0.9, 1.05], 0)
    assert track_step(np.zeros((0, 3)), points, box).box is None
    invalid = np.array([[np.nan, 0, 0], [0, np.inf, 0]])
    result = track_step(np.concatenate([points, invalid]), np.concatenate([points, invalid]), box)
    assert result.box is not None
    np.testing.assert_allclose(result.box.center, box.center, atol=0.03)


def test_tracking_honors_cancellation_before_and_during_alignment():
    points = object_points()
    box = Box3D([0, 0, 0.175], [2, 0.9, 1.05], 0)
    assert track_step(points, points, box, cancel=lambda: True).reason == "cancelled"
    checks = []

    def cancel_during_work():
        checks.append(True)
        return len(checks) >= 5

    result = track_step(points, points + [0.4, 0.2, 0], box, cancel=cancel_during_work)
    assert result.box is None
    assert result.reason == "cancelled"
    assert len(checks) == 5


def test_nonfinite_membership_rows_are_ignored_without_numeric_warnings():
    box = Box3D([0, 0, 0], [1, 1, 1], 0.3)
    with np.errstate(invalid="raise"):
        mask = points_in_box([[np.inf, 0, 0], [0, np.nan, 0], [0, 0, 0]], box)
    np.testing.assert_array_equal(mask, [False, False, True])


def test_tracking_respects_an_existing_heading_and_off_origin_rotation():
    previous = move_points(object_points(), [4, -2, 1], 0.58)
    box = Box3D([4, -2, 1.175], [2, 0.9, 1.05], 0.58)
    current = move_points(previous - [4, -2, 1], [4.4, -1.7, 1.1], -0.3)
    result = track_step(previous, current, box)
    assert result.box is not None, result.reason
    np.testing.assert_allclose(result.box.center, [4.4, -1.7, 1.275], atol=0.08)
    assert abs(result.box.yaw - 0.28) < 0.08


def test_tracking_rejects_ground_when_a_volumetric_target_disappears():
    previous = object_points()
    rng = np.random.default_rng(22)
    ground = np.column_stack([rng.uniform(-2, 2, (1600, 2)), np.zeros(1600)])
    result = track_step(previous, ground, Box3D([0, 0, 0.175], [2, 0.9, 1.05], 0))
    assert result.box is None


def test_dense_cloud_alignment_bounds_search_points_and_retains_local_object():
    previous = object_points(count=16000)
    current = move_points(previous, [0.35, 0.2, 0.05], 0.16)
    background = np.random.default_rng(23).uniform(20, 100, (80000, 3))
    tree_sizes = []

    def real_tree_with_size_record(points):
        tree_sizes.append(len(points))
        return cKDTree(points)

    with patch("core.object_tracking.cKDTree", side_effect=real_tree_with_size_record):
        result = track_step(np.concatenate([previous, background]),
                            np.concatenate([current, background]),
                            Box3D([0, 0, 0.175], [2, 0.9, 1.05], 0))
    assert result.box is not None, result.reason
    np.testing.assert_allclose(result.box.center, [0.35, 0.2, 0.225], atol=0.08)
    assert tree_sizes and max(tree_sizes) <= 4096


def test_stationary_target_can_be_associated_with_zero_displacement_limit():
    points = object_points()
    box = Box3D([0, 0, 0.175], [2, 0.9, 1.05], 0)
    result = track_step(points, points, box, max_distance=0)
    assert result.box is not None, result.reason
    np.testing.assert_allclose(result.box.center, box.center, atol=1e-6)


def test_full_orientation_roundtrip_corners_and_fitting():
    from core.object_tracking import box_rotation

    box = Box3D([3, -2, 5], [4, 2, 1], 0.4, roll=0.3, pitch=-0.5)
    assert Box3D.from_dict(box.to_dict()) == box
    assert Box3D.from_dict({"center": [0, 0, 0], "size": [1, 1, 1], "yaw": 0.2}).roll == 0
    rotation = box_rotation(box)
    np.testing.assert_allclose(rotation.T @ rotation, np.eye(3), atol=1e-12)
    corners = box_corners(box)
    local = (corners - box.center) @ rotation
    np.testing.assert_allclose(np.abs(local), np.tile(np.asarray(box.size) / 2, (8, 1)))
    assert points_in_box(corners, box).all()
    outside = np.asarray(box.center) + np.array([2.1, 0, 0]) @ rotation.T
    assert not points_in_box([outside], box)[0]
    fitted = fit_box(corners, box.yaw, roll=box.roll, pitch=box.pitch)
    np.testing.assert_allclose(fitted.center, box.center)
    np.testing.assert_allclose(fitted.size, box.size)
    assert (fitted.roll, fitted.pitch) == (box.roll, box.pitch)


def test_tracking_preserves_manual_pitch_roll_under_global_z_rotation():
    from core.object_tracking import box_rotation

    box = Box3D([0, 0, 0], [2, 0.9, 1.05], 0.32, roll=0.2, pitch=-0.35)
    previous = (object_points() - [0, 0, 0.175]) @ box_rotation(box).T
    current = move_points(previous, [0.25, -0.2, 0.08], 0.18)
    result = track_step(previous, current, box)
    assert result.box is not None, result.reason
    assert result.box.roll == box.roll
    assert result.box.pitch == box.pitch
    assert abs(result.box.yaw - 0.5) < 0.08


@pytest.mark.parametrize("angle", [float("nan"), float("inf"), True])
def test_box_rejects_invalid_pitch_roll(angle):
    with pytest.raises(ValueError):
        Box3D([0, 0, 0], [1, 1, 1], pitch=angle)
    with pytest.raises(ValueError):
        Box3D([0, 0, 0], [1, 1, 1], roll=angle)
