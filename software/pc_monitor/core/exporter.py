"""Capture-viewer export helpers — pure logic, Qt-free, file-I/O-free.

Two slice computations: one per device, returning (header, rows) tuples
ready for csv.writer.writerows(). Time column is always relative to the
trigger sample (ms), matching the on-screen time axis in the viewer.

The viewer feeds these to QFileDialog → file write; tests cover the
slice math (boundaries, swap, channel filter) without touching Qt.
"""
from __future__ import annotations

from typing import Iterable

from core.capture_parser import CaptureSample
from core.distribution_client import CHANNEL_KEYS, DistCapSample


# Headers — kept in lockstep with session_writer.py's full-session CSVs,
# plus a leading t_ms column. Slice files therefore differ from full-
# session files by one column; downstream tooling treats t_ms as the
# canonical "where on the timeline" column and ignores `i` / `idx` if
# it doesn't need the sample-index notion.

_ARDUINO_HEADER = ["t_ms", "i", "uab", "ubc", "uca", "ia", "ib", "ic"]


def slice_arduino(
    samples:   Iterable[CaptureSample],
    period_ms: int,
    t1_ms:     float,
    t2_ms:     float,
) -> tuple[list[str], list[list]]:
    """ADE9000 samples whose t_ms lies in the inclusive [t1, t2] window.

    `t_ms = i * period_ms` — assumes the trigger lives at i=0 (the
    project-wide convention; see sequencer.md §3.3). Swaps t1/t2 if
    given in the wrong order so the dialog never has to validate.
    Returns (_ARDUINO_HEADER, rows). Rows preserve sample order.
    """
    lo, hi = (t1_ms, t2_ms) if t1_ms <= t2_ms else (t2_ms, t1_ms)
    rows: list[list] = []
    for s in samples:
        t = s.i * period_ms
        if lo <= t <= hi:
            rows.append([t, s.i, s.uab, s.ubc, s.uca, s.ia, s.ib, s.ic])
    return _ARDUINO_HEADER, rows


def slice_distribution(
    samples:        Iterable[DistCapSample],
    trigger_idx:    int,
    period_ms:      int,
    t1_ms:          float,
    t2_ms:          float,
    channel_filter: set[str] | None = None,
) -> tuple[list[str], list[list]]:
    """Distribution samples whose t_ms lies in the inclusive [t1, t2] window.

    `t_ms = (idx - trigger_idx) * period_ms` — same trigger=0 convention.
    `channel_filter` is the set of CHANNEL_KEYS to include (e.g. {"u17_ch0",
    "u17_ch1"}); when None or all 8 keys, every channel is written. The
    header tracks the filter so downstream parsers see only the columns
    that exist in the slice. Each included channel contributes a `<key>_raw`
    and `<key>_hex` pair, matching session_writer.py's full-session layout.
    """
    if channel_filter is None:
        keep_idx = list(range(len(CHANNEL_KEYS)))
    else:
        keep_idx = [i for i, k in enumerate(CHANNEL_KEYS) if k in channel_filter]

    header: list[str] = ["t_ms", "idx"]
    for i in keep_idx:
        header.append(f"{CHANNEL_KEYS[i]}_raw")
        header.append(f"{CHANNEL_KEYS[i]}_hex")

    lo, hi = (t1_ms, t2_ms) if t1_ms <= t2_ms else (t2_ms, t1_ms)
    rows: list[list] = []
    for idx, raw_ints, hex_strs in samples:
        t = (idx - trigger_idx) * period_ms
        if not (lo <= t <= hi):
            continue
        row: list = [t, idx]
        for ci in keep_idx:
            row.append(raw_ints[ci])
            row.append(hex_strs[ci])
        rows.append(row)
    return header, rows
