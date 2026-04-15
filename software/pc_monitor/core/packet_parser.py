import json
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Packet:
    ts:    int
    uab:   float
    ubc:   float
    uca:   float
    uavg:  float
    unb:   float
    f:     float
    state: int
    flags: List[str] = field(default_factory=list)


def parse_packet(line: str) -> Optional[Packet]:
    """Parse a JSON line from firmware. Returns None on any error."""
    try:
        d = json.loads(line.strip())
        # Require at minimum the voltage fields; drop status packets
        if 'uab' not in d:
            return None
        return Packet(
            ts=int(d.get('ts', 0)),
            uab=float(d['uab']),
            ubc=float(d['ubc']),
            uca=float(d['uca']),
            uavg=float(d.get('uavg', 0.0)),
            unb=float(d.get('unb', 0.0)),
            f=float(d.get('f', 0.0)),
            state=int(d.get('state', 0)),
            flags=list(d.get('flags', [])),
        )
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None
