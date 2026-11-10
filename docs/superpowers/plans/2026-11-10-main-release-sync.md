# v2.1.0 main and release synchronization

> Execute in this session using executing-plans. The user requested direct publication from the local folder, based on latest main, without merging preview.

**Goal:** Deliver a clean main checkout and one v2.1.0 Release containing Windows x64, macOS ARM64, Ubuntu amd64 and Ubuntu arm64 installers, with matching local dist files and current README links.

**Architecture:** Fast-forward to main `9ce844b`, reapply only the uncommitted Linux packaging work, and rebuild Ubuntu packages from this source. Reuse the Windows and macOS artifacts explicitly requested from existing releases. Publish source as a main descendant, and consolidate the macOS duplicate only after verifying its replacement.

**Constraints:** Preserve historical releases/tags and preview. Do not merge preview or upload dist into Git history. Preserve original data and user settings. Ubuntu baseline stays 22.04 with 24.04 verification. Existing macOS artifact has its own original build provenance; disclose it rather than claim a new build. Keep a recovery copy before synchronizing. Previously blocked cleanup targets are not silently retried.

## Review focus

Source ancestry; installation files genuinely matching their stated architecture; correct download hashes; no accidental replacement of latest main changes; no deletion of the macOS release until the final release has its verified asset; no disclosure of local credentials or private capture data.

## Tasks

- [x] Fetch main and Release metadata, preserve local changes, fast-forward the folder to main, and apply only local Linux packaging changes.
- [x] Download and hash-check the requested Windows EXE and macOS DMG into dist.
- [x] Rebuild both Ubuntu DEBs from the synchronized source and verify installation, actual point rendering and uninstall on Ubuntu 22.04 and 24.04. Run affected and full regressions with separate Qt test processes.
- [x] Update README, usage, release and cleanup records; verify local links and artifact manifests. Review the final source changes.
- [ ] Commit and push the source directly to main without merging preview. Align the v2.1.0 source tag with the published source using a checked expected ref, preserving the original reference in the local release record.
- [ ] Publish the four installers and checksums to v2.1.0, verify remote hashes, then remove only the duplicate v2.1.0-macos prerelease and tag. Preserve all historical versions and preview.
- [ ] Clean newly generated process files where allowed and report exact remaining manual cleanup paths.

## Progress

- Original local HEAD: `e7a32f9` on preview. Local changes saved to stash `57523cc9f6a38d20d433cfc3a7acf018e2e4e6d5` and an external recovery copy.
- Remote main and preview both pointed to `9ce844b` at inspection. Only main was fetched and fast-forwarded into the folder; preview was not merged or updated.
- User confirmed preserving historical releases and preview; only the duplicate v2.1.0 macOS prerelease is to be cleaned after consolidation.
- Main already contains a deterministic directory-stat test fixture. Kept that newer test instead of reapplying the earlier local timestamp workaround; packaging changes applied cleanly and no application source from preview was merged.
- Authenticated service and local Git credentials both use HOOK290 with repository push permission. Release upload uses the official API because the connector does not expose binary upload or release deletion.
- Fresh read-only review found no Critical/Important source blockers. Corrected the minor WHEELHOUSE documentation ambiguity: a URL accessible to the build container or a prepopulated image path is required; ordinary host paths are not automatically mounted. This documentation clarification changes no runtime behavior.
- Windows/Mac downloads match the original GitHub SHA-256 values; Windows file/product versions are 2.1.0. Large downloads used verified byte ranges after whole-file transfers stalled.
- Regressions: Windows 733 passed, 235 subtests, 18 skipped; Linux amd64 745 passed, 237 subtests, 6 skipped; ARM64 focused 154 passed, 111 subtests. Both full suites have 48 successful modules. All four architecture/Ubuntu combinations passed installation, real 9,600-point rendering, rotation, normal close, uninstall and personal-data preservation. Screenshots were visually inspected.
- The four installers and SHA256SUMS.txt are uploaded to release 440104293; each remote size and digest matches dist. The original Windows executable was preserved in place.
- Cleaned the 2.66 GiB WSL build directory, all ten task build/QA images and their unused intermediate layers; stopped test containers and the temporary wheel server. Download-cache deletion was rejected with `blocked by policy`, so it was not retried; cleanup-guide.md lists this and previous manual cleanup paths.
