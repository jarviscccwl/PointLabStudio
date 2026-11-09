# Tree association repair

User reports that selecting a tree and associating adjacent frames gives no visible result. Existing application logs confirm actual runs at 09:21–09:22 returning zero suggestions due to poor_match. Frame number is unknown; use the same 114507 recording around displayed frames 353 and 400 and explicit tree/trunk regions. Preserve current checkout, uncommitted work and original data; no build/commit/push. Do not retry previously rejected deletions.

## Evidence and repair direction

The prior 500-test suite covered small volumetric synthetic objects and a tight real trunk. It did not cover a larger canopy. A 6×6×8 m region in the real recording fails even when compared with itself. Matching uses only 512 source points but evaluates coverage against up to 4096 destination points; this mistakes sampling gaps for lost geometry. Initial candidates all recenter the template centroid, which can move the box far from the previous pose for a density-skewed tree. Controlled probes isolate both causes.

Keep bounded local geometric association and the 0.65 acceptance gate. Separate the small alignment sample from denser validation support, explicitly test a continuous pose seed, and preserve manual anchors. Do not add unconditional box copying or silently lower the quality threshold. A failed match remains a failure with an actionable explanation.

The current top-level association button only expands settings; progress is not connected and result text lives at the bottom of a scroll area. Keep the three-view layout, make the start action explicit and visible, default to both directions, show persistent progress/status, and provide direct navigation through actual associated frames. On a frame without a box, explain how to return to the nearest existing box. These are fixes to the authorized workflow, not a new layout or new backend.

## Work

- [x] Add failing canopy identity/motion tests, repair sampling/continuity, verify old disappearance/displacement/anchor safeguards.
- [x] Add UI behavior tests for actual click-to-start, visible progress/errors, results navigation and no-box recovery; implement compact controls with correct worker lifetimes.
- [x] Verify real PCAP tree regions in both directions through actual buttons and frame changes, inspect dark/light/small layouts, review changes independently, run relevant regression suites, update usage and cleanup documentation.

## Verification ledger

- Native PCAP tree at original index 399, 10,581 selected points: actual button produced 10 automatic observations across indices 394–404 and retained the manual anchor. Every result was displayed; correcting index 401 and rerunning preserved the correction. No-box recovery, workspace save/restore, and unchanged input size/mtime checks passed.
- Full Windows Qt suite: 506 passed, 16 skipped, 140 subtests, 5 existing deprecation warnings; exit 0. Added the reviewer-recommended skewed canopy/background regression and verified the missing-pose-seed failure in an in-memory ablation. Final focused algorithm/controller/editor suite: 94 passed, exit 0 (overlaps full suite).
- Corrected small-window scroll/footer overlap and dark empty-editor text. Repeated the native workflow with geometry assertions in both compact and expanded states at 1024 × 700; final screenshots inspected.
- Independent read-only review found no blocking implementation issues. Its missing-seed test coverage finding was addressed; native UI/data and unrelated earlier changes remained the main agent's responsibility.
- README and repair report updated. No build, commit, push, or original-data edits. Deleting the new one-off external diagnostic script was rejected with `blocked by policy`; no retry, and the manual cleanup list now includes it and verification-only settings.
