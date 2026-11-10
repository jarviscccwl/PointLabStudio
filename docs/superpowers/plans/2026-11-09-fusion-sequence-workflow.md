# Fusion sequence workflow implementation plan

**Goal:** Complete editable calibration, reusable fusion exports and smooth frame-range browsing.
**Architecture:** Extend current Qt controls; bounded serial preparation cache; GUI captures the actual views and an independent writer encodes frame sequences. Reuse existing source/index/calibration/project contracts.
**Tech Stack:** Python 3.12, PySide6, NumPy, pygfx, OpenCV, Pillow.
**Spec:** ../specs/2026-11-09-fusion-sequence-workflow.md

## Global constraints

- Preserve all previous uncommitted calibration work and user data. No package, remote push, or historical cleanup retry.
- Default animation: synchronized cloud/image MP4; support GIF, image/cloud views, PNG/PLY sequences.
- Source reads serial; byte-bounded caches; cancellation and versioned requests; no Qt widget use in writer threads.
- Native UI/GPU tests run serially. Pure CPU checks can run in parallel.

## Tasks

- [x] Calibration: extend core/calibration_files.py and ui/widgets/calibration_editor.py plus panel. Provide ExtrinsicsDialog.calibration; add panel.edit_extrinsics_button for browser wiring. Add missing format/direction/unit/precision/cancel tests before implementation.
- [x] Export encoding: new core/sequence_export.py. SequenceWriter(path, fps, size) with append(rgb), close(commit=True), abort(); compose_views(cloud, image, layout, size). MP4/GIF bounded streaming, atomic finalization and errors. Independent decode tests.
- [x] Import UX: ui/recording_session.py, ui/widgets/source_overview.py, discovery cancellation. Improve large-folder discovery/progress and selection hierarchy without changing source selections silently. Tests for cancel and reuse.
- [x] Prepared-frame navigation: ui/widgets/recording_browser.py and focused cache module if useful. Cache by source identities, position, sync/calibration/display parameters; retain bounded nearby frames. Foreground navigation cancels/preempts background reads. Test rapid reversal, invalidation, source changes, close, calibration changes, memory limits.
- [x] Range/export UI: range timeline widget, ui/widgets/recording_export.py controller/dialog, browser/chrome wiring and viewport/image snapshot APIs. Range endpoint keyboard/mouse edits, exact inclusive selection, MP4/GIF/PNG/point-cloud exports, progress and cancellation. Preserve prior state/view after export. Add export lifecycle to window shutdown checks.
- [x] Integration: related regression, real 114507 and calibration ZIP, independent exports readback, native deep/light/small-window inspection, performance before/after, documentation and cleanup guide.

## Review focus

Wrong transform direction; stale frames after parameter changes; unmatched image within a sequence; cancelled output reported as completed; large-folder UI freezes; worker lifetime on close; inclusive endpoints and original frame numbers; GIF/MP4 frame counts and timestamp provenance.

## Verification outcome

See ../../fusion-sequence-2026-11-10.md for the 351-test regression, 19-test integration run, real recording measurements, native export readback and cleanup limitation. Historical and this-turn deletion denials were not retried. No packaging, commit or remote upload.
