"""Offset estimation between PC monotonic clock and device millis().

Pure logic only. Caller owns serial I/O and collects per-seq samples:
    send_ns  — perf_counter_ns() taken just before writing SYNC <seq>
    recv_ns  — perf_counter_ns() taken when the sync response line arrived
    tick_ms  — device `tick_ms` echoed back in the response

compute_offset() picks the best_k samples with lowest round-trip time
(cleanest channel conditions) and returns their median offset.

Offset convention:
    device_tick_ms_at_pc_ns(pc_ns) ≈ offset_ms + pc_ns / 1e6

equivalently:
    offset_ms = tick_ms − (send_ns + recv_ns) / 2 / 1e6
"""
from dataclasses import dataclass
from statistics import median
from typing import List


@dataclass
class SyncSample:
    seq:     int
    send_ns: int      # perf_counter_ns() before write
    recv_ns: int      # perf_counter_ns() after recv
    tick_ms: int      # device millis() in response

    @property
    def rtt_ms(self) -> float:
        return (self.recv_ns - self.send_ns) / 1e6

    @property
    def offset_ms(self) -> float:
        mid_ms = (self.send_ns + self.recv_ns) / 2.0 / 1e6
        return float(self.tick_ms) - mid_ms


@dataclass
class SyncResult:
    offset_ms:     float   # median over `n_used` cleanest samples
    rtt_ms_median: float
    rtt_ms_best:   float   # floor achievable on this link
    n_samples:     int     # total probed
    n_used:        int     # best_k actually used for offset


def compute_offset(samples: List[SyncSample], best_k: int = 8) -> SyncResult:
    if not samples:
        raise ValueError('compute_offset: no samples')

    by_rtt = sorted(samples, key=lambda s: s.rtt_ms)
    k = min(best_k, len(by_rtt))
    best = by_rtt[:k]

    return SyncResult(
        offset_ms     = median(s.offset_ms for s in best),
        rtt_ms_median = median(s.rtt_ms    for s in samples),
        rtt_ms_best   = by_rtt[0].rtt_ms,
        n_samples     = len(samples),
        n_used        = k,
    )
