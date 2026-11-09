"""Offset index and clock regressions using independently specified EM4 wire bytes."""
import json
import os
from pathlib import Path
import struct
from unittest.mock import patch

import pytest

from core.cancellation import OperationCancelled
from core.io import pcap_reader, rs_em4
from test_lidar_import import eth_udp, msop, difop2, write_pcap, _pcapng_block


def test_em4_geometry_matches_independent_polar_formula_for_all_surfaces():
    import numpy as np

    calibration = rs_em4.Em4Calibration(np.linspace(-30, 30, 520),
                                      np.linspace(-4, 4, 13), np.array([-2., 1., 3., -1.]))
    assembler = rs_em4.Em4FrameAssembler(calibration)
    expected = []
    for column, yaw in enumerate((-179., -31.23, 0., 132.51)):
        for part in range(2):
            assembler.add(msop(column * 2 + part + 1, yaw, surface=column + 1,
                               radius=60000 - column * 1000))
        radius = (60000 - column * 1000) * .005
        rows = np.arange(520)
        heading = np.deg2rad(yaw + calibration.yaw_offsets[(rows // 20) % 13])
        pitch = np.deg2rad(calibration.pixel_pitch + calibration.surface_pitch[column])
        expected.append(np.column_stack((radius * np.cos(pitch) * np.cos(heading),
                                         radius * np.cos(pitch) * np.sin(heading),
                                         radius * np.sin(pitch))))
    frame = assembler.flush()
    np.testing.assert_allclose(frame.xyz, np.concatenate(expected).astype(np.float32), atol=2e-5, rtol=1e-7)
    assert np.all(frame.intensity == 7)


def source_type():
    from core.io.pcap_source import PcapSource
    return PcapSource


def recording(tmp_path, packets=None):
    path = tmp_path / 'sensor.pcap'
    if packets is None:
        packets = [(10 + frame + part * .01, eth_udp(msop(part + 1, 0, radius=1000 + frame)))
                   for frame in range(3) for part in range(2)]
    write_pcap(str(path), packets)
    return path


def test_offset_index_and_seek_without_prefix(tmp_path):
    path = recording(tmp_path)
    source = source_type()(str(path), cache_dir=tmp_path / 'cache')
    info = source.inspect()
    assert info.streams[0].count is None
    assert info.size_bytes == path.stat().st_size
    progress = []
    index = source.build_index(progress=progress.append)
    stream = info.streams[0].id
    assert len(index[stream]) == 3
    assert progress[0] == 0 and progress[-1] == 1
    # Reject any sequential prefix scan. The last frame must decode from its own range.
    original_reader = pcap_reader.iter_udp_datagrams
    def seek_reader(*args, **kwargs):
        assert kwargs['start_offset'] == index[stream][2].locator['start_offset']
        return original_reader(*args, **kwargs)
    with patch.object(pcap_reader, 'iter_udp_datagrams', side_effect=seek_reader):
        data = source.read_frame(stream, 2)
    assert data.value.point_count == 520
    assert data.record_time_ns == 12_000_000_000
    assert data.timestamp_ns == 1_700_000_000_000_000_000
    assert data.metadata['partial']
    assert data.metadata['missing_packets'] == 2398
    assert data.metadata['clock_domain'] == 'device'


def test_cache_reuse_and_stale_rejection(tmp_path):
    path = recording(tmp_path)
    cache = tmp_path / 'cache'
    first = source_type()(str(path), cache_dir=cache)
    first.build_index()
    assert list(cache.glob('*.json'))
    second = source_type()(str(path), cache_dir=cache)
    with patch.object(pcap_reader, 'iter_udp_datagrams', side_effect=AssertionError('rescanned')):
        assert second.build_index() == first.index
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
    third = source_type()(str(path), cache_dir=cache)
    with patch.object(pcap_reader, 'iter_udp_datagrams', side_effect=RuntimeError('stale rescanned')):
        with pytest.raises(RuntimeError, match='stale rescanned'):
            third.build_index()


def test_cancellation_does_not_publish_partial_index(tmp_path):
    source = source_type()(str(recording(tmp_path)), cache_dir=tmp_path / 'cache')
    with pytest.raises(OperationCancelled):
        source.build_index(cancel=lambda: True)
    assert source.index == {}
    assert not list((tmp_path / 'cache').glob('*.json'))


def test_late_calibration_applied_to_earlier_frames(tmp_path):
    packets = [(1, eth_udp(msop(1, 0))), (1.1, eth_udp(msop(2, 0))),
               (2, eth_udp(difop2(surface_offset=100), sport=7766, dport=7766))]
    source = source_type()(str(recording(tmp_path, packets)), cache_dir=tmp_path / 'cache')
    indices = source.build_index()
    data = source.read_frame(next(iter(indices)), 0)
    assert data.metadata['calibration'] == 'DIFOP2'
    assert abs(float(data.value.xyz[0, 2]) - 5 * __import__('math').sin(__import__('math').radians(-11.98))) < .001


def test_missing_packet_and_beginning_are_kept(tmp_path):
    packets = [(1, eth_udp(msop(4, 0))), (2, eth_udp(msop(2, 0))), (3, eth_udp(msop(3, 0)))]
    source = source_type()(str(recording(tmp_path, packets)), cache_dir=tmp_path / 'cache')
    indices = source.build_index()
    stream = next(iter(indices))
    assert len(indices[stream]) == 2
    first = source.read_frame(stream, 0)
    assert first.value.point_count == 260
    assert first.metadata['missing_packets'] == 2399


def test_pcapng_interface_timestamp_resolution_and_offset(tmp_path):
    path = tmp_path / 'nano.pcapng'
    shb = _pcapng_block(0x0A0D0D0A, struct.pack('<IHHq', 0x1A2B3C4D, 1, 0, -1))
    # if_tsresol=9 (decimal nanoseconds), if_tsoffset=-2 seconds
    opts = struct.pack('<HHB3xHHqHH', 9, 1, 9, 14, 8, -2, 0, 0)
    idb = _pcapng_block(1, struct.pack('<HHI', 1, 0, 65535) + opts)
    packet = eth_udp(msop(1, 0))
    stamp = 5_123_456_789
    epb = _pcapng_block(6, struct.pack('<IIIII', 0, stamp >> 32, stamp & 0xffffffff, len(packet), len(packet)) + packet)
    path.write_bytes(shb + idb + epb)
    datagrams = list(pcap_reader.iter_udp_datagrams(str(path)))
    assert datagrams[0].timestamp_ns == 3_123_456_789
    source = source_type()(str(path), cache_dir=tmp_path / 'cache')
    stream = next(iter(source.build_index()))
    assert source.read_frame(stream, 0).record_time_ns == 3_123_456_789


def test_import_manifest_has_original_clocks(tmp_path):
    from core.io.lidar_import import import_frames
    result = import_frames(str(recording(tmp_path)), str(tmp_path / 'out'), count=1)
    manifest = json.loads((tmp_path / 'out' / 'frames.json').read_text(encoding='utf-8'))
    assert manifest['frame_metadata'][0]['record_time_ns'] == 10_000_000_000
    assert manifest['frame_metadata'][0]['timestamp_ns'] == 1_700_000_000_000_000_000
    assert 'nominal' in ' '.join(result.warnings).lower()


def test_changed_source_rejected_before_random_read(tmp_path):
    path = recording(tmp_path)
    source = source_type()(str(path), cache_dir=tmp_path / 'cache')
    stream = next(iter(source.build_index()))
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
    with pytest.raises(pcap_reader.PcapError, match='changed'):
        source.read_frame(stream, 0)


def test_mid_scan_cancellation_leaves_no_cache(tmp_path):
    source = source_type()(str(recording(tmp_path)), cache_dir=tmp_path / 'cache')
    calls = [0]
    def cancel():
        calls[0] += 1
        return calls[0] > 4
    with pytest.raises(OperationCancelled):
        source.build_index(cancel=cancel)
    assert source.index == {}
    assert not list((tmp_path / 'cache').glob('*.json'))


def test_packet_loss_preserves_available_rows():
    assembler = rs_em4.Em4FrameAssembler()
    assembler.add(msop(2, 0))
    frame = assembler.flush()
    assert frame.point_count == 260
    assert frame.partial and frame.missing_packets == 2399


@pytest.mark.parametrize('endian', ['<', '>'])
def test_pcapng_sections_reset_interfaces_and_endian(tmp_path, endian):
    path = tmp_path / 'sections.pcapng'
    def block(kind, body, byte_order):
        body += bytes(-len(body) % 4)
        length = len(body) + 12
        return struct.pack(byte_order + 'II', kind, length) + body + struct.pack(byte_order + 'I', length)
    content = b''
    for order in [endian, '>' if endian == '<' else '<']:
        content += block(0x0a0d0d0a, struct.pack(order + 'IHHq', 0x1a2b3c4d, 1, 0, -1), order)
        # Binary clock units of 2^-10 seconds.
        option = struct.pack(order + 'HHB3xHH', 9, 1, 0x80 | 10, 0, 0)
        content += block(1, struct.pack(order + 'HHI', 1, 0, 65535) + option, order)
        payload = eth_udp(msop(1, 0))
        content += block(6, struct.pack(order + 'IIIII', 0, 0, 1536, len(payload), len(payload)) + payload, order)
    path.write_bytes(content)
    assert [d.timestamp_ns for d in pcap_reader.iter_udp_datagrams(str(path))] == [1_500_000_000] * 2


def test_corrupt_cache_rebuilt(tmp_path):
    path = recording(tmp_path)
    cache = tmp_path / 'cache'
    source = source_type()(str(path), cache_dir=cache)
    source.build_index()
    next(cache.glob('*.json')).write_text('{broken', encoding='utf-8')
    other = source_type()(str(path), cache_dir=cache)
    assert other.build_index() == source.index


def test_multiple_sensor_streams(tmp_path):
    packet1 = eth_udp(msop(1, 0))
    packet2 = bytearray(eth_udp(msop(1, 0)))
    packet2[26:30] = bytes([192, 168, 1, 201])
    source = source_type()(str(recording(tmp_path, [(1, packet1), (2, bytes(packet2))])), cache_dir=tmp_path / 'cache')
    index = source.build_index()
    assert len(index) == 2
    assert [source.read_frame(stream, 0).value.point_count for stream in index] == [260, 260]


def test_compression_rejected_with_description(tmp_path):
    source = source_type()(str(recording(tmp_path, [(1, eth_udp(msop(1, 0, compressed=True)))])), cache_dir=tmp_path / 'cache')
    assert source.inspect().warnings
    with pytest.raises(rs_em4.Em4CompressedError, match='roi_compression_mode'):
        source.build_index()


def test_cache_parser_version_invalidated(tmp_path):
    from core.io import pcap_source
    path = recording(tmp_path)
    source = source_type()(str(path), cache_dir=tmp_path / 'cache')
    source.build_index()
    other = source_type()(str(path), cache_dir=tmp_path / 'cache')
    with patch.object(pcap_source, 'PARSER_VERSION', pcap_source.PARSER_VERSION + 1):
        with patch.object(pcap_reader, 'iter_udp_datagrams', side_effect=RuntimeError('new parser rescanned')):
            with pytest.raises(RuntimeError, match='new parser rescanned'):
                other.build_index()


def test_cache_write_failure_keeps_reader_usable(tmp_path):
    path = recording(tmp_path)
    source = source_type()(str(path), cache_dir=tmp_path / 'cache')
    with patch('core.io.pcap_source.os.replace', side_effect=OSError('cache unavailable')):
        stream = next(iter(source.build_index()))
    assert source.read_frame(stream, 0).value.point_count == 520
    assert not list((tmp_path / 'cache').glob('*.tmp'))
    assert any('cache unavailable' in warning for warning in source.info.warnings)


@pytest.mark.parametrize('offset,value', [(26, 2), (27, 0), (30, 0x71)])
def test_unsupported_em4_header_rejected(offset, value):
    packet = bytearray(msop(1, 0))
    packet[offset] = value
    with pytest.raises(rs_em4.Em4FormatError):
        rs_em4.parse_msop(bytes(packet))


def test_inspection_after_index_keeps_exact_count(tmp_path):
    source = source_type()(str(recording(tmp_path)), cache_dir=tmp_path / 'cache')
    source.build_index()
    assert source.inspect().streams[0].count == 3


def test_ipv6_fragments_are_not_decoded_as_complete_udp():
    udp = struct.pack('>HHHH', 6699, 6699, 11, 0) + b'abc'
    fragment = struct.pack('>BBHI', 17, 0, 1, 7) + udp
    ip = struct.pack('>IHBB16s16s', 6 << 28, len(fragment), 44, 64, bytes(16), bytes(16)) + fragment
    assert pcap_reader._udp_from_ipv6(ip, 0, 1.0) is None


@pytest.mark.parametrize('container', ['pcap', 'pcapng'])
def test_low_level_range_never_reads_container_prefix(tmp_path, container):
    from test_lidar_import import write_pcapng
    path = tmp_path / ('seek.' + container)
    packets = [(1.0, eth_udp(b'prefix')), (2.0, eth_udp(b'target'))]
    writer = write_pcapng if container == 'pcapng' else write_pcap
    writer(str(path), packets)
    target = list(pcap_reader.iter_udp_datagrams(str(path)))[1]
    with path.open('r+b') as handle:
        handle.write(bytes(target.offset))
    datagrams = list(pcap_reader.iter_udp_datagrams(str(path), start_offset=target.offset,
                     stop_offset=target.next_offset, context=target.context))
    assert [datagram.payload for datagram in datagrams] == [b'target']
    assert datagrams[0].timestamp_ns == 2_000_000_000


def test_nonzero_yaw_offsets_disclose_manual_mapping_uncertainty(tmp_path):
    packets = [(1, eth_udp(msop(1, 0))),
               (2, eth_udp(difop2(yaw_offset_entry1=3), sport=7766, dport=7766))]
    source = source_type()(str(recording(tmp_path, packets)), cache_dir=tmp_path / 'cache')
    source.build_index()
    assert any('yaw-offset' in warning for warning in source.info.warnings)


@pytest.mark.parametrize('captured_time', [None, 0])
def test_pcapng_missing_capture_clock_is_distinct_from_zero(tmp_path, captured_time):
    path = tmp_path / 'clock.pcapng'
    packet = eth_udp(msop(1, 0))
    content = _pcapng_block(0x0a0d0d0a, struct.pack('<IHHq', 0x1a2b3c4d, 1, 0, -1))
    content += _pcapng_block(1, struct.pack('<HHI', 1, 0, 65535))
    if captured_time is None:
        content += _pcapng_block(3, struct.pack('<I', len(packet)) + packet)
    else:
        content += _pcapng_block(6, struct.pack('<IIIII', 0, 0, 0, len(packet), len(packet)) + packet)
    path.write_bytes(content)
    source = source_type()(str(path), cache_dir=tmp_path / 'cache')
    stream = next(iter(source.build_index()))
    assert source.index[stream][0].record_time_ns == captured_time
    assert source.read_frame(stream, 0).record_time_ns == captured_time
    assert source.info.start_ns == source.info.end_ns == captured_time
    assert source.info.streams[0].start_ns == source.info.streams[0].end_ns == captured_time
    cached = source_type()(str(path), cache_dir=tmp_path / 'cache')
    assert cached.build_index()[stream][0].record_time_ns == captured_time
    from core.io.lidar_import import import_frames
    import_frames(str(path), str(tmp_path / 'export'))
    manifest = json.loads((tmp_path / 'export' / 'frames.json').read_text(encoding='utf-8'))
    assert manifest['frame_metadata'][0]['record_time_ns'] == captured_time


def test_pcapng_span_ignores_absent_capture_time(tmp_path):
    path = tmp_path / 'mixed-clock.pcapng'
    content = _pcapng_block(0x0a0d0d0a, struct.pack('<IHHq', 0x1a2b3c4d, 1, 0, -1))
    content += _pcapng_block(1, struct.pack('<HHI', 1, 0, 65535))
    first = eth_udp(msop(2, 0))
    second = eth_udp(msop(1, 0))
    content += _pcapng_block(3, struct.pack('<I', len(first)) + first)
    content += _pcapng_block(6, struct.pack('<IIIII', 0, 0, 5_000_000, len(second), len(second)) + second)
    path.write_bytes(content)
    source = source_type()(str(path), cache_dir=tmp_path / 'cache')
    stream = next(iter(source.build_index()))
    assert [ref.record_time_ns for ref in source.index[stream]] == [None, 5_000_000_000]
    assert source.info.start_ns == source.info.end_ns == 5_000_000_000
