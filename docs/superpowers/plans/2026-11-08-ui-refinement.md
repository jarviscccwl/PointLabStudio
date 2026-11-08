# Recording interface refinement implementation plan

**Goal:** Make opening and browsing sensor recordings clear at ordinary desktop sizes, and complete the everyday navigation and source-management actions.

**Design:** Retain the existing Qt application and dark/light themes. Arrange the recording workspace as a compact stream selector, the main preview, and a bottom transport bar. Keep synchronization and calibration in a collapsible settings area. Give package selection a searchable inventory and a bounded details panel. These are implementation choices within the user's authorized interface refinement, not a new application design.

**Constraints:** Preserve prior uncommitted work on feature/recording-browser. Do not package, commit, push, or modify input recordings. Keep missing-clock and calibration provenance. Keep source-reader work serialized. Clean task artifacts only after inspecting exact paths; exclude Git internals, the environment, original data and the existing release.

## Tasks

- [ ] Package overview: search and type filtering, explicit select-visible actions, selected counts, readable source details, optional range controls. Preserve selections through filtering/scanning. Own source_overview.py and test_source_overview.py. Expose set_selection(selection) using the existing selection() dictionary.
- [ ] Scene inventory: searchable file/topic tree with accurate visible counts and source overview/reload actions. Preserve directory selection, import, drop and source-file behavior. Own scene_panel.py and focused scene tests.
- [ ] Recording workspace: collapse advanced controls, contextual actions and summaries, bottom transport, time jump, default meaningful comparison stream, secondary export and clear metadata display. Preserve stale-result protection and bounded caches. Own recording_browser.py, data_view.py and focused UI tests.
- [ ] Connect source overview/reload to the existing serialized import queue and restore the current stream/position/settings when possible. Update main-window state and documentation.
- [ ] Verify package filtering, hidden selections, no-clock data, duplicate source names, empty ranges, rapid navigation, reconfiguration and cancellation. Inspect native dark/light layouts using the real sample data. Run appropriate regression suite, independent review, and exact-path cleanup.

## Verification

Use .venv/Scripts/python.exe -B with QT_QPA_PLATFORM=offscreen for focused tests. Native Qt visual probes use isolated settings and read-only data from D:/2026_10_14. Compare before/after screenshots at 1280×800 and 1440×900; prevent long topics or metadata from widening the window or shrinking the preview. No new tests for cosmetic-only style values.
