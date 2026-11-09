# Unified box workflow implementation plan

> Execute with subagent-driven-development for isolated components and inline integration. Preserve this checkout and all prior uncommitted work. No commits, pushes or builds.

**Goal:** One usable 3D box workflow with orthogonal editing, resilient cloud playback and professional startup/loading feedback.
**Architecture:** Extend Box3D compatibly; share a CPU orthogonal editor between recordings and ordinary scene selection. Keep serialized source reads, recover render scheduling, and retain real progress semantics.
**Tech Stack:** Existing Python, Qt, NumPy/SciPy, pygfx; generated artwork in resources. No new dependencies.
**Spec:** ../specs/2026-11-09-unified-box-workflow-design.md

## Global constraints

- Existing feature/recording-browser checkout; no commits/push/EXE build; D data read-only.
- Source signatures, original indices, manual anchors, cancellation and bounded caches remain binding.
- No concurrent native/GPU test processes. CPU/widget-only focused tests may run separately.
- Preserve old Box3D(center, size, yaw) callers and older .pls payloads.
- Prior rejected deletions must not be retried through another mechanism; list remaining paths.

## Review focus

1. Nonzero pitch/roll must agree among containment, corners, display, fitting, tracking and persistence.
2. Dragging a face keeps the opposite face fixed; dimensions remain positive, and reset/undo do not cross frames.
3. Old delayed frame/error results cannot undo a topic reload; worker ownership remains serialized.
4. Renderer exceptions, collapsed views and hidden-to-visible transitions cannot silently stop drawing forever.
5. Progress animations cannot imply completion, create widget work off the GUI thread or continue after close.

## Tasks and ownership

- [x] **1. Geometry and reusable orthographic editor.** Own core/object_tracking.py, ui/widgets/box_editor.py (new), ui/viewport/annotation_overlay.py geometry helpers, tests/test_box_editor.py and geometry test additions. Add roll/pitch default zero, box_rotation(box), compatible fitting/membership/corners/serialization. Implement BoxEditor with `box_edited = Signal(object)`, `set_data(points, box, *, reset=False)`, `set_theme(theme)`, `clear()`, and `views` mapping top/front/side. Axes top=(X,Y), front=(Y,Z), side=(X,Z); drag handles/translation/rotation, WASD/QE, Shift, double-click fit, undo/reset. Bound visual point sampling; edits use original finite local points for fitting. Tests fail first for orientation roundtrip, opposite face, keyboard transform, fitting and data reset.
- [x] **2. Render recovery and topic reload.** Own viewport render loop changes, recording browser/chrome and new recovery tests. Reproduce stopped redraw following callback exceptions before fixing; finally reschedule, retain dirty on failed render, log/publish useful error. Investigate hidden scheduling. Add selected topic reload, cancel safely, clear only matching cached frames, preserve original frame/sync/annotation/camera; allow fresh rendering resource recovery. Tests cover cloud changing while images/indices advance, stale errors, reload during read, disconnected source and preserved other topics.
- [x] **3. Unified box integration.** Own ui/widgets/object_tracking.py, new ui/widgets/scene_box.py, main_window.py, viewport interaction hooks and integration tests. One top-level 3D box action routes to current workspace. Embed editor for recording frames and static point selection. Replace separate tracking entry with association section. Wire live manual edits, identity guards, selected-box clicks, Ctrl-drag, old workspaces and ordinary scene extraction. Validate numerical controls, three views and source/frame switching.
- [x] **4. Startup art and progress.** Own ui/startup.py, apps/desktop.py, new ui/widgets/smooth_progress.py and progress adoption in recording_session/source_overview plus tests. Root generates artwork first; implementation consumes resources/startup-art.png. Animate scan/light subtly, retain app logo and native text; make heavy import animation responsive without moving QWidget construction off GUI thread. Smooth known progress, animated unknown progress, terminal/cancel lifecycle. Scope-specific tests and native startup verification.
- [x] **5. Verify and deliver.** Independent review, targeted fixes, real-data continuous playback and box editing, theme/small-window screenshots, full Windows Qt suite. Update README, docs index, verification and cleanup guide. No hidden process files in source tree, no deletion retries.

## Ledger

- Baseline before this request: 447 passed, 16 skipped, 140 subtests; HEAD 9c8686f. Prior work remains uncommitted.
- User clarified freeze: frame number or camera image continues, point cloud does not. Prioritize actual render/presentation lifecycle, not just reader timing.
- Ruling: continue authorized local implementation with existing architecture; do not add a redundant design approval stop. Keep implementation choices reviewable in this plan.

- Tasks 1–4 implemented and independently reviewed. Task 1 had two corrected geometry/editor P2 findings; tasks 2/3 had two corrected workspace/topic-routing P2 findings; startup/progress review reported no Important/Critical findings.
- Native acceptance: 114507 PCAP/MCAP, original indices 401–460 continuous; 57 distinct presented cloud bitmaps despite one injected presentation error. Reload retains pose/annotations, 1024×700 expanded controls remain usable, original input sizes/mtimes unchanged.
- Native ordinary scene gestures: Ctrl-drag creates box, orthographic drag/local undo works, volume selection is 56/500 points, source unchanged. Repeated drag no longer shifts the viewport.
- Native desktop startup: child splash survives main-thread imports, exits normally at readiness, animation changes during fixed progress and stops on close. Frozen fallback limitation recorded.
- Documentation and cleanup inventory updated. Old project .tmp is now absent; remaining source caches ~2.69 MiB. New external test-settings deletion was also auto-review rejected (blocked by policy); no retry. All exact paths recorded in cleanup-guide.md.
- Final Windows Qt suite: 500 passed, 16 skipped, 140 subtests, 5 existing deprecation warnings; 482.52 seconds, exit 0. One old menu-label assertion found in the initial full run was updated to the renamed action, with explicit topic-routing coverage, before this complete rerun. git diff --check passes. No commits, pushes or builds.
