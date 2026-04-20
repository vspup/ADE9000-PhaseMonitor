import csv
from datetime import datetime
from pathlib import Path
from typing import IO, Optional

from core.packet_parser import Packet

_HEADER = ('ts', 'uab', 'ubc', 'uca', 'uavg', 'unb', 'f', 'state', 'flags')


class Logger:
    """Writes packets to a CSV file. Call start() → write() ... → stop()."""

    def __init__(self):
        self._file: Optional[IO[str]] = None
        self._writer = None
        self._active = False
        self._path   = ''

    def start(self, directory: str = '.') -> str:
        """Open a new session file. Returns the full path."""
        ts   = datetime.now().strftime('%Y%m%d_%H%M%S')
        path = Path(directory) / f'session_{ts}.csv'

        self._file   = open(path, 'w', newline='', encoding='utf-8')
        self._writer = csv.writer(self._file)
        self._writer.writerow(_HEADER)
        self._active = True
        self._path   = str(path)
        return self._path

    def write(self, packet: Packet) -> None:
        if not self._active:
            return
        flags_str = '|'.join(packet.flags) if packet.flags else ''
        self._writer.writerow((
            packet.ts, f'{packet.uab:.3f}', f'{packet.ubc:.3f}',
            f'{packet.uca:.3f}', f'{packet.uavg:.3f}', f'{packet.unb:.3f}',
            f'{packet.f:.3f}', packet.state, flags_str,
        ))

    def stop(self) -> None:
        self._active = False
        if self._file:
            self._file.flush()
            self._file.close()
        self._file   = None
        self._writer = None

    @property
    def active(self) -> bool:
        return self._active

    @property
    def path(self) -> str:
        return self._path
