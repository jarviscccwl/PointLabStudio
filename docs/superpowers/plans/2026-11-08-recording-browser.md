# Sensor recording browser implementation plan

> Agentic workers use the agreed interfaces below and verify each deliverable. Independent parser tasks own disjoint files. Integration and final review run in the current chat.

**Goal**

Open sensor recordings and ordinary files through one user workflow, inspect their contents before selection, browse indexed point clouds, images and messages, and compare synchronized data with optional camera calibration.

**Architecture**

Keep the current point-cloud tools and add a recording browser using shared SourceInfo, StreamInfo, FrameRef and FrameData contracts in core/io/recording_types.py. Sources expose inspect, build_index and read_frame. A background GUI controller owns source instances and reads bounded payloads. The scene lists streams rather than every message. Existing workspaces remain readable.

**Tech stack**

Python 3.12, PySide6, existing pygfx viewport, NumPy, MCAP ROS1/ROS2 decoders, rosbags, Pillow and PyYAML.

**Spec**

The user's approved three-phase proposal in this chat plus the latest instructions to support raw and compressed image formats, camera information, IMU and other messages, remove automatic point coloring, clean process artifacts, and leave packaging and remote publication for later confirmation.

## Global constraints

- Work in the supplied checkout on a local feature branch. No commits or remote publication are needed for this delivery.
- Input recordings under D:/2026_10_14 are read-only.
- No EXE build. Existing downloaded release stays available.
- Opening packages, directories, images and text uses the same entry points, including scene drops.
- Preserve nanosecond timestamp provenance and original frame identifiers. Frame counts distinguish unknown, indexed and declared counts.
- Plain point clouds and legacy .pls workspaces continue to work.
- Long scans and decoding run off the UI thread, allow cancellation, and keep memory bounded.
- Unsupported sensor protocols and image encodings report their exact type and retain metadata access.

## Review focus

- Real EM4 network byte order, incomplete frames, changing calibration and raw device versus capture time.
- MCAP containing only images/IMU, ROS1 bag, ROS2 SQLite, raw image row padding and PointCloud2 row padding.
- Out-of-order and duplicate timestamps, unmatched streams, fast navigation while reads are pending.
- Mixed directories and repeated imports, malformed sources, source files moved after workspace save.
- GUI shutdown during scanning, current-frame ownership, and existing point-cloud commands.

## Task 1. PCAP correctness and indexing

Own core/io/rs_em4.py, core/io/pcap_reader.py, core/io/pcap_source.py, legacy lidar timestamp metadata and focused parser tests.

- [ ] Reproduce actual packet header errors with independent network-order fixtures.
- [ ] Verify the sensor protocol against authoritative references, repair EM4 fields and frame boundaries, and preserve capture/device times.
- [ ] Implement PcapSource(RecordingSource) with metadata probing, cancellable offset-based indexing and random frame reads.
- [ ] Verify real small and large recordings without extracting all frames.

## Task 2. General recording and file sources

Own core/io/recording.py, core/io/recording_messages.py, core/io/mcap_reader.py and focused recording/codec tests. Consume recording_types.py and PcapSource from task 1 without editing them.

- [ ] Test mixed MCAP topics, padded raw images, compressed images, padded PointCloud2, ROS1 BAG and ROS2 DB3.
- [ ] Implement open_source(path), is_data_source(path), discover_sources(paths, cancel=None) and SOURCE_EXTENSIONS.
- [ ] Implement inspect/build_index/read_frame for MCAP, bags and image/text/table files with bounded decoded data.
- [ ] Preserve arbitrary message metadata, support Image/CompressedImage/PointCloud2/CameraInfo/Imu by schema, and produce useful unsupported-codec messages.
- [ ] Verify sample MCAP topic counts against their summaries and inspect real camera/IMU payloads.

## Task 3. Time association and calibration

Own core/recording_sync.py, core/calibration.py and their focused tests.

- [ ] Match frames by selected device/record time, offset and tolerance, including duplicate and unsorted times.
- [ ] Read and validate CameraInfo and YAML/JSON calibration with explicit transform direction.
- [ ] Project finite positive-depth points into images using intrinsics, distortion and lidar-to-camera extrinsics.

## Task 4. User-facing browser and normal entry points

Own ui/widgets/recording_browser.py, ui/widgets/source_overview.py, ui/recording_session.py, integration changes in main_window.py and scene_panel.py, dependencies and GUI regression tests.

- [ ] Build a background overview showing all streams before selection, counts, recording times, format information and warnings.
- [ ] Add image/table/message and cloud/image comparison views with frame controls, time matching, calibration import and current-frame export.
- [ ] Connect file dialogs, directory discovery and drops. Save source selections, ranges and browser/calibration state in workspaces.
- [ ] Remove auto from visible color options and shortcuts. Use 1 height, 2 intensity, 3 RGB, 4 labels, 5 scalar, 6 solid and normalize legacy auto to height on restoration.
- [ ] Verify real data through GUI entry points, update concise usage documentation and remove task scratch artifacts.

## Execution record

Initial repository is clean at 582a323. Python 3.12 environment is being provisioned locally. Parser and calibration tasks have disjoint ownership and can run concurrently while the main agent implements the GUI integration. Shared data contracts are defined before dispatch. Packet count is not treated as frame count. Default time association uses recording time until the user selects device time and any required offset.
