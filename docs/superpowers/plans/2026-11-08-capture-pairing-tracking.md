# Capture pairing and object tracking implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement the assigned tasks. Preserve local changes; do not commit.

**Goal:** Open a recording folder as synchronized cloud/image views and associate editable object boxes across adjacent cloud frames.

**Architecture:** Keep import/read serialization. Add a standalone geometry/model module, a viewport overlay API, and a focused annotation controller/dialog integrated with the recording browser. Save tracks under each recording source so existing path portability applies.

**Tech Stack:** Python, PySide6, NumPy, SciPy, pygfx; existing environment only.

**Spec:** ../specs/2026-11-08-capture-pairing-tracking-design.md

## Global constraints

- Work in the existing feature/recording-browser checkout; no commit, push or EXE build.
- Original D:/ recordings are read-only. Preserve existing uncommitted improvements.
- Default clock remains record/capture; do not guess offsets from filenames or sidecars.
- Track observations use original frame indices; manual observations cannot be overwritten automatically.
- Keep source reads serialized, cancellation effective and bounded frame memory.
- Previous process-file deletion was rejected; do not retry cleanup through another mechanism.

## Review focus

1. Multiple captures/cameras: never silently pair separate capture directories.
2. Reconfiguration, reload and save/restore: retain positions and annotations by original frame, invalidate changed-source annotations.
3. Failed geometry or missing clocks: no fabricated match/box; clear status and retain valid manual work.
4. Cancellation, importing and close: no concurrent source readers, stale writes or live-thread destruction.
5. Small windows, different themes and missing pointcloud: controls remain usable; annotation editing stays out of the preview area.

## Tasks

- [x] 1. Core model and geometric association. Create core/object_tracking.py and tests/test_object_tracking.py. API: Box3D(center, size, yaw), Observation(frame_index, box, manual=True, score=None), ObjectTrack(id, name, observations); all support to_dict/from_dict. Helpers fit_box(xyz, yaw=0), points_in_box(xyz, box, margin=0), box_corners(box), track_step(previous_xyz, current_xyz, box, max_distance=2.0, cancel=None) returning TrackMatch(box or None, score, reason). Test invalid inputs/serialization, translation/yaw, disappearing target, bounded displacement and cancellation. First failing tests, minimal implementation, focused checks.
- [x] 2. Viewport annotations. Add set_annotation_boxes(boxes) to PointCloudViewport accepting dictionaries {id, name, center, size, yaw, selected, manual}; draw wire boxes, ID labels and selected-box point highlighting without changing CloudEntity or selection semantics. Clear overlays when set_entities changes until the controller reapplies current observations. Own ui/viewport/annotation_overlay.py and the viewport integration, tests/test_annotation_viewport.py. Normal theme changes/stop must remain safe.
- [x] 3. Folder auto-pairing. Add a pure pairing helper and focused tests, integrate new imports through ui/recording_session.py and RecordingBrowser. Prefer same-group pointcloud/image; select first valid pair within current tolerance; preserve refresh/restore and single-mode behavior. Real-window folder-open regression must exercise the normal path, not select streams manually.
- [x] 4. Annotation UI/controller and persistence. Create ui/widgets/object_tracking.py for panel and controller, wire RecordingBrowser/DataView chrome. Connect selection once per viewport, attach observations on frame change, serialize tracks per source in recording_session. Background tracking locks conflicting reads; cancellation/close invalidates generation. Validate manual anchors, stale-frame reframe, frame-range persistence and save/reopen with focused meaningful tests.
- [x] 5. Review and real verification. Review integrated diff; run affected tests and full suite once stable. Use the 114507 folder, capture native dark/light preview, verify normal open -> synchronized pair -> select -> track -> manual correction -> save/reopen. Update README/verification records; keep final artifacts outside the source tree and disclose any unverified tracking precision.

## Progress and rulings

Pre-flight: tasks 1 and 2 meet task 4 via the APIs above; neither edits task 4 files. Task 3 and task 4 share browser/session files and are owned serially by the main implementer. Geometry and rendering tasks have independent ownership. All tasks agree with no-commit/source-read-only constraints. Existing full baseline: 351 passed, 16 skipped, 140 subtests, exit 0.

Ruling: continue in the existing checkout, as the user requested folder merge and local ongoing optimization; do not create another worktree or relocate their changes.
Ruling: user confirmed object tracking with manual correction; local geometric candidate association is an implementation choice. No external model installation or fully automatic semantic claims.

Completed: same-capture pairing, first-match navigation, object model/geometry, viewport overlays, focused tracking panel, serialized cancellation and source-bound persistence. Review findings for source changes, cross-capture grouping and offline resave were fixed and regression-tested; final persistence review found no further actionable issue.

Verification: the latest six MCAP integration tests passed. Windows native 114507 workflow passed normal folder open, real rectangle gesture, three-frame target association, manual anchor preservation, JSON export, project save/reopen and closing during an active tracking job. Dark/light screenshots were inspected; original input sizes/mtimes were unchanged. Full suite: 435 passed, 16 skipped, 140 subtests passed, 5 existing warnings, exit 0, 430.24 seconds. No implementation changes followed that run.

Delivery: README and docs/capture-tracking-2026-11-08.md explain usage, evidence and geometric limits. Final evidence is outside the source tree. Keep this checkout and local changes; no commit, push or packaging. Previously rejected .tmp cleanup remains unperformed. Final source/test cache cleanup was also rejected by automatic review (blocked by policy) and was not retried.
