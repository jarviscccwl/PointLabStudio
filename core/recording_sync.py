"""Integer-nanosecond matching for independently indexed recording streams."""

from bisect import bisect_left
from operator import index

from core.io.recording_types import FrameRef


class TimestampMatcher:
    """Match corrected stream times to another stream's requested timestamp.

    ``offset_ns`` is added to this stream's timestamps. Device mode excludes
    frames without device timestamps. Ties select the earlier corrected time,
    then the lowest ``FrameRef.index`` for duplicate times. The returned index
    is the source frame index, regardless of the order of ``refs``.
    """

    def __init__(self, refs: list[FrameRef], basis: str = "record", offset_ns: int = 0):
        if basis not in ("record", "device"):
            raise ValueError("Timestamp basis must be 'record' or 'device'.")
        offset_ns = index(offset_ns)
        entries = []
        for ref in refs:
            stamp = ref.record_time_ns if basis == "record" else ref.timestamp_ns
            if stamp is not None:
                entries.append((index(stamp) + offset_ns, index(ref.index)))
        entries.sort()
        # One entry per time also makes predecessor searches choose the first
        # original frame index when there are duplicate timestamps.
        self._timestamps = []
        self._indices = []
        for stamp, frame_index in entries:
            if not self._timestamps or stamp != self._timestamps[-1]:
                self._timestamps.append(stamp)
                self._indices.append(frame_index)

    def closest(self, timestamp_ns: int, tolerance_ns: int | None) -> tuple[int, int] | None:
        """Return (original frame index, matched time minus requested time).

        Tolerance is nonnegative and inclusive; None reports the nearest frame
        without a cutoff (for navigation and unmatched-frame diagnostics).
        All arithmetic uses integers,
        preserving single nanoseconds at epoch-sized timestamps.
        """
        timestamp_ns = index(timestamp_ns)
        if tolerance_ns is not None:
            tolerance_ns = index(tolerance_ns)
            if tolerance_ns < 0:
                raise ValueError("Timestamp tolerance must be nonnegative.")
        if not self._timestamps:
            return None
        insertion = bisect_left(self._timestamps, timestamp_ns)
        candidates = range(max(0, insertion - 1), min(len(self._timestamps), insertion + 1))
        best = min(candidates, key=lambda position: (abs(self._timestamps[position] - timestamp_ns),
                                                    self._timestamps[position]))
        delta = self._timestamps[best] - timestamp_ns
        if tolerance_ns is not None and abs(delta) > tolerance_ns:
            return None
        return self._indices[best], delta
