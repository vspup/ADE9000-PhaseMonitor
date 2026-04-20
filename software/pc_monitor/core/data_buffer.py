from collections import deque
from typing import Optional

import numpy as np

from core.packet_parser import Packet

_FIELDS = ('ts', 'uab', 'ubc', 'uca', 'uavg', 'va', 'vb', 'vc', 'vavg',
           'unb', 'f', 'state', 'ia', 'ib', 'ic', 'iavg', 'iunb')


class DataBuffer:
    """Circular buffer of Packet objects. Thread-unsafe — write from main thread only."""

    def __init__(self, maxlen: int = 1200):
        self._packets: deque = deque(maxlen=maxlen)

    def append(self, packet: Packet) -> None:
        self._packets.append(packet)

    def get_arrays(self) -> dict:
        """Return dict of numpy arrays, one per field, in chronological order."""
        if not self._packets:
            return {f: np.array([], dtype=np.float64) for f in _FIELDS}
        pkts = list(self._packets)
        return {f: np.array([getattr(p, f) for p in pkts], dtype=np.float64)
                for f in _FIELDS}

    @property
    def latest(self) -> Optional[Packet]:
        return self._packets[-1] if self._packets else None

    def __len__(self) -> int:
        return len(self._packets)

    def clear(self) -> None:
        self._packets.clear()
