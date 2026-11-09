"""Canopy coverage must not fail simply because fitting uses fewer samples."""
import numpy as np
from core.object_tracking import Box3D, track_step


def tree_points(seed=12):
    rng = np.random.default_rng(seed)
    theta = rng.uniform(-np.pi, np.pi, 24000)
    height = rng.uniform(2., 8., len(theta))
    radius = np.sqrt(np.clip(1 - ((height - 5.) / 3.) ** 2, 0, 1))
    canopy = np.column_stack((3 * radius * np.cos(theta), 2 * radius * np.sin(theta), height))
    canopy += rng.normal(0, .015, canopy.shape)
    theta = rng.uniform(-np.pi, np.pi, 6000)
    trunk = np.column_stack((.2 * np.cos(theta), .2 * np.sin(theta), rng.uniform(0, 3, len(theta))))
    return np.concatenate((canopy, trunk))


def test_canopy_and_trunk_do_not_fail_when_identical_clouds_are_compared():
    points = tree_points()
    box = Box3D((0, 0, 4), (6.2, 4.2, 8.2))
    match = track_step(points, points.copy(), box)
    assert match.box is not None, match.reason
    np.testing.assert_allclose(match.box.center, box.center, atol=.12)
    assert abs(match.box.yaw) < .08


def test_resampled_tree_follows_small_motion_without_losing_canopy():
    previous = tree_points()
    current = tree_points(23) + (.18, -.08, .02)
    box = Box3D((0, 0, 4), (6.2, 4.2, 8.2))
    match = track_step(previous, current, box)
    assert match.box is not None, match.reason
    np.testing.assert_allclose(match.box.center, (.18, -.08, 4.02), atol=.14)
    assert abs(match.box.yaw) < .1


def test_skewed_canopy_keeps_previous_pose_with_nearby_background():
    # An uneven scan contains many crown points and few trunk points. Nearby
    # geometry lies outside the box but inside the search region. Recentring
    # every seed by the point centroid loses the original, correct pose.
    rng = np.random.default_rng(113)
    height = rng.uniform(4.5, 7.8, 600)
    theta = rng.uniform(-np.pi, np.pi, 600)
    radius = np.sqrt(np.clip(1 - ((height - 6.15) / 1.8) ** 2, 0, 1))
    canopy = np.column_stack((1.2 * radius * np.cos(theta), 1.6 * radius * np.sin(theta), height))
    canopy += rng.normal(0, .008, canopy.shape)
    theta = rng.uniform(-np.pi, np.pi, 10)
    trunk = np.column_stack((.15 * np.cos(theta), .15 * np.sin(theta), rng.uniform(.1, 5, 10)))
    background = np.column_stack((rng.uniform(3.2, 6, 400), rng.uniform(-3, 3, 400),
                                  rng.uniform(3, 8, 400)))
    points = np.concatenate((canopy, trunk, background))
    box = Box3D((0, 0, 4), (6.2, 4.2, 8.2))
    match = track_step(points, points.copy(), box)
    assert match.box is not None, match.reason
    np.testing.assert_allclose(match.box.center, (0, 0, 4), atol=.03)
    assert abs(match.box.yaw) < .03
