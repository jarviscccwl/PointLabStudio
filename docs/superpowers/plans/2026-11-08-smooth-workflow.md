# Smooth workflow implementation plan

> Execute inline with superpowers:executing-plans; preserve uncommitted work and record verification here. No commits.

**Goal:** Reduce repeated work during pointcloud display, improve playback pacing and make target review more convenient.
**Architecture:** Keep Qt and serialized readers. Limit bounds reuse to a viewport refresh; retain existing frame/cache/source contracts. Reorganize the tracking dialog without changing annotation persistence.
**Tech Stack:** Existing Python, PySide6, NumPy, pygfx environment.
**Spec:** ../specs/2026-11-08-smooth-workflow-design.md

## Global constraints

- Existing feature/recording-browser checkout, no commit/push/build, original recordings read-only.
- No parallel readers or unbounded prefetch; preserve manual anchors and stale-result guards.
- Do not retry previously rejected cleanup; list inspected safe-to-delete paths instead.
- Existing baseline: 435 passed, 16 skipped, 140 subtests, exit 0.

## Review focus

1. Bounds remain accurate after in-place edits, replacement, hiding and removing clouds.
2. Playback with slow reads, rapid pause/seek, final frame, rate changes and tracking cannot schedule stale reads or skip frames.
3. Shortcuts affect previews only; no conflict with text/number editing or ordinary scene visibility.
4. Target navigation/focus is disabled during reads, disconnected/changed sources and no-observation frames; does not mutate source data.
5. Small screen and theme changes leave controls reachable; cleanup excludes dependencies, Git and original data.

## Tasks

- [x] 1. Display hot path: tests/test_display_efficiency.py, ui/viewport/viewport.py, core/color/colormaps.py. Reproduce duplicate real bounds calls while retaining correct scene bounds; reuse LOD metrics bounds within each refresh. Verify in-place and replacement edits. Exercise known color interpolation values, clipping, NaNs, shape and dtype; compare color arrays with the prior formula and benchmark the unchanged workload.
- [x] 2. Playback/navigation: tests/test_playback_controls.py, ui/widgets/recording_browser.py, ui/widgets/recording_chrome.py, ui/widgets/data_view.py. Reproduce redundant current-frame presentation and additive waiting; use monotonic start time and remainder scheduling. Add preview-scoped Space/Home/End and first/last buttons. Verify pause, rate changes, seek, end, source changes and serialized reads.
- [x] 3. Tracking convenience: ui/widgets/object_tracking.py and tests/test_tracking_integration.py. Reorganize sections with scrollable content; add previous/next, frame label and target focus. Test actual adjacent navigation and camera focus without replacing source entities, including unavailable source and missing observation. Cosmetic layout is verified by native screenshots.
- [x] 4. Final review and verification: independent code review, focused checks, real-data before/after probe, native dark/light and small-window exercise, full suite. Update README, verification and cleanup guide with actual evidence and remaining paths.

## Progress and decisions

Ruling: continue the existing checkout and established UI. Authorization for the ongoing optimization is already present; routine implementation choices do not require another approval. Preserve previous changes. No new external dependencies.

Controlled measurement: native 114507 nine transitions (400/401/402 repeated), same 1280x800 window/cache/settings. Restoring only the two old display hot paths in the probe process yielded median presentation 92.170 ms and 18 bounds scans; current code yielded 50.784 ms and 9 scans. Median frame-ready was 113.917 ms versus 71.484 ms. Both probes release the GIL during waits. Early QTest.qWait probes distorted uncached reader timing and are superseded by the controlled reports. These are local transition measurements, not sustained playback/FPS benchmarks.

Focused verification: display/annotation/view tests 14 passed; recording playback/navigation/UI tests 42 passed; final tracking integration/tracking UI/playback group 53 passed. The main-window Space conflict was reproduced and fixed by restricting ordinary scene shortcuts to the scene tree and frame-browser splitter. Native layout checks caught and fixed the tracking scroll area's stale light-theme background. Final dark/light panel is 460x740 with no scroll; 440x480 remains scrollable.

Independent review found no actionable issues and verified slow-reader ordered playback plus rapid pause/seek behavior. Real-data native workflow and final layout probes exited 0; source files' sizes and modification times are unchanged. README, in-app shortcut help, documentation index and cleanup guide are updated.

Final full suite (Windows Qt, `python -B -m pytest -q -p no:cacheprovider --durations=5`): **447 passed, 16 skipped, 140 subtests passed, 5 existing warnings, 435.45 seconds, exit 0**. Only documentation and static shortcut-help wording changed while it ran; the help module passed an AST syntax check. Cleanup remains intentionally unexecuted after the prior approval rejection; `docs/cleanup-guide.md` lists each inspected path and preservation constraints. No commit, push or EXE build.
