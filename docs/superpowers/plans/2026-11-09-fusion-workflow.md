# Fusion workflow implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development for independent bounded tasks and review. Preserve all pre-existing uncommitted work. No commits or pushes.

**Goal:** Reliable cloud/image coloring, calibrated projection and responsive annotation in a simpler desktop workspace.

**Architecture:** Keep the existing recording reader, calibration model and two DataViews. Add pure projection helpers and a lightweight image annotation layer; keep display preferences in RecordingBrowser. Tracking and startup can be refined independently without modifying fusion ownership files.

**Tech Stack:** Python, NumPy, PySide6, pygfx, existing OpenCV.

**Spec:** docs/superpowers/specs/2026-11-09-fusion-workflow-design.md

## Global Constraints

- Work in the user's existing feature/recording-browser directory; all baseline changes must remain.
- No commit, push, new dependencies or raw-data mutation. No parallel GUI/GPU tests.
- Do not retry previously policy-blocked cleanup paths (docs/cleanup-guide.md).
- Keep the original decoded point fields intact when producing image-colored previews.

## Review Focus

- Cloud displayed as secondary view and later swapped: preferences still work.
- Missing/mismatched images and calibrations: no stale projection or RGB retained.
- A box crossing the camera plane or image bounds: finite correctly clipped edges.
- Changing display settings while a frame is being read: only current settings reach the canvas.
- Cancelled association and manual anchors: no overwritten manual boxes or stale operations.

## Tasks and progress

- [x] 1. Repair recording color controls. Files: ui/main_window.py, ui/recording_session.py, ui/widgets/recording_browser.py; new ui/widgets/cloud_display_controls.py. Tests: tests/test_recording_fusion.py. Reproduce disabled colormap and missing per-frame preferences, add browser API set_colormap / set_color_field / set_color_range and shared display application. Verify swap, reload and restore.
- [x] 2. Implement geometry in core/calibration.py and core/calibration_display.py (or focused core/fusion.py). Tests: tests/test_fusion.py. Add sample_image_colors returning RGB/visible indices with nearest-surface filtering; project_box_segments returning finite clipped image segments. Verify known geometry, resized image, distortion, negative depth, occlusion and no input mutation.
- [x] 3. Integrate fusion using ui/widgets/calibration_controls.py, data_view.py, recording_browser.py and recording_chrome.py. Add RGB preview without altering decoded cache; independent image box layer updated from tracking refresh. Preserve preferences in workspace. Test frame swaps, missing matches, live edit/delete, undistortion and reload.
- [x] 4. Optimize association and editing in ui/widgets/object_tracking.py, box_panel.py, box_editor.py and related tests. Keep manual anchors, cancellation and source checks. Provide a boxes_changed signal carrying the exact same visible box records as the viewport for fusion. Avoid recomputing unchanged lists/point transforms during edits. Owner must coordinate GUI test execution.
- [x] 5. Make startup movement continuous in ui/startup.py, apps/desktop.py, ui/widgets/smooth_progress.py and their tests. Verify unknown progress remains activity-only, sparse real progress animates, close and failure clean up child processes, and frozen entry points remain supported.
- [x] 6. Native UI acceptance using real D: capture and known calibration sample. Run appropriate full regression, record results and limitations in docs; tidy only this round's permissible process files.

## Decisions / verification ledger

- 2026-11-09: Extend the incumbent desktop UI; no platform replacement. User's requested direct implementation supersedes repeated design approval.
- 2026-11-09: Real camera intrinsics found, lidar-camera extrinsics not found. Asked user for path asynchronously. Continue with deterministic synthetic calibration tests.
- 2026-11-09: Baseline exact-head is 9c8686f with extensive prior local work, already on a feature branch. Keep current workspace rather than losing those changes in a clean checkout.

- 2026-11-09: Tasks 1–5 implemented. Geometry 33 passed + 44 subtests; focused recording/tracking/editor checks 128 passed; startup/progress 29 passed + 2 subtests. Independent review findings resolved.
- 2026-11-09: Native acceptance confirms calibrated sample RGB + point/box overlay and live mouse edit; real 114507 capture plays displayed frame 401–671 with changing point cloud/image, preserves intensity on topic reload. Added source-key fit reset after reproducing old-view reuse.
- 2026-11-09: Full Windows suite running; final narrow/small-window acceptance and cleanup remain. No external calibration supplied, real alignment remains unverified.
- 2026-11-09: User confirmed there is currently no extrinsic calibration; implement import and validate using known-calibration sample. This is the accepted validation boundary for this round.
- 2026-11-09: Full native Windows regression finished: 557 passed, 16 skipped, 2 failed, 146 subtests. Progress review found native purple palette vs app accent mismatch; one QProgressBar palette rule fixes real theme consistency. Animation assertions now isolate timer delivery rather than assuming setter calls cannot service events. Affected tests: 37 passed + 14 subtests. Final header/tool-restoration test: 1 passed.
- 2026-11-09: Native 1024x670 acceptance exposed the three-view editor clipped by a fixed preview minimum. Assigned bounded layout correction to tracking_flow; final task remains open until layout and native recheck pass.
- 2026-11-09: Compact layout correction complete and parent-reviewed: core views remain outside the optional scroll, compact heights retain all handles, responsive settings use the same controls, QObject-bound queued callbacks and normal splitter preferences are preserved. Focused tests: 76 passed + 4 size subtests; additional progress/cancel layout check passed the same four sizes.
- 2026-11-09: Task 6 complete. Native 1024x670 sample editor/parameter window and real frame 671 intensity view accepted; programs exited normally. Screenshot helper timed out on the open tool window, then accessibility information and shutdown confirmed the state; final real-data screenshot succeeded. All 94 product Python files parsed and diff whitespace check passed. Raw D: metadata unchanged for all 9 files, 3,760,696,004 bytes.
- 2026-11-09: Removed this round's collected-tests.txt. Deleting this round's ui-settings directory was automatically rejected with blocked by policy; not retried. Preserved final-ui.log separately and documented the exact manual-cleanup path. No build, commit or push performed.
