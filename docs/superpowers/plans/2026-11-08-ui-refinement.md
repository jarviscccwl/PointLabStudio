# Recording interface refinement implementation plan

**Goal:** Make opening and browsing sensor recordings clear at ordinary desktop sizes, and complete the everyday navigation and source-management actions.

**Design:** Retain the existing Qt application and dark/light themes. Arrange the recording workspace as a compact stream selector, the main preview, and a bottom transport bar. Keep synchronization and calibration in a separate nonmodal settings dialog, as adopted by the latest upstream. Give package selection a searchable inventory and a bounded details panel. These are implementation choices within the user's authorized interface refinement, not a new application design.

**Constraints:** Preserve prior uncommitted work on feature/recording-browser. Do not package, commit, push, or modify input recordings. Keep missing-clock and calibration provenance. Keep source-reader work serialized. Clean task artifacts only after inspecting exact paths; exclude Git internals, the environment, original data and the existing release.

## Tasks

- [x] Package overview: search and type filtering, explicit select-visible actions, selected counts, readable source details, optional range controls. Preserve selections through filtering/scanning. Expose set_selection(selection), including the original time origin on reconfiguration.
- [x] Scene inventory: searchable file/topic tree with accurate visible counts and source overview/reload actions. Preserve directory selection, import, drop and source-file behavior.
- [x] Recording workspace: separate advanced controls, contextual actions and summaries, bottom transport, time jump, default meaningful comparison stream, secondary export and clear metadata display. Preserve stale-result protection and bounded caches.
- [x] Connect source overview/reload to the existing serialized import queue and restore the current stream/position/settings when possible. Preserve project dirty state and update documentation.
- [x] Focused regression, independent review and native dark/light visual verification using synthetic recordings. The review's removed-primary comparison finding is fixed and covered by a real MCAP workflow test.
- [x] Repeat visual checks on D:/2026_10_14 after the drive was connected. Indexed 37 sources across five captures; sampled first/middle/last frames and four cloud/image pairs. Verified the 115220 capture through the native overview, synchronized preview, reconfiguration/reload, export, calibration, IMU/text and workspace restore workflows. Original input sizes and modification times unchanged; real extrinsic projection and long-duration performance remain unverified.
- [x] Reproduce and fix late status-timer and GPU-presentation callbacks after window deletion, including zero-size/collapsed viewports. Keep rendercanvas pinned consistently because pending-download cancellation uses its version-specific interface.
- [ ] Process-file cleanup: an exact-file cleanup after path validation was rejected by automatic approval review (blocked by policy). No deletion succeeded; preserve the files rather than retry through another mechanism.

## Verification

Use .venv/Scripts/python.exe -B with QT_QPA_PLATFORM=offscreen for focused tests. Native Qt visual probes use isolated settings and read-only data from D:/2026_10_14. Compare before/after screenshots at 1280×800 and 1440×900; prevent long topics or metadata from widening the window or shrinking the preview. No new tests for cosmetic-only style values.
