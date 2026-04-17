import json
from dataclasses import dataclass, field
from typing import List, Optional

from core.measurement_mode import MeasurementMode


@dataclass
class Packet:
    ts:   int
    mode: MeasurementMode

    # ── Line-to-line (MEASURE_DELTA) ──────────────────────────────────
    uab:  float = 0.0
    ubc:  float = 0.0
    uca:  float = 0.0
    uavg: float = 0.0

    # ── Phase-to-neutral (MEASURE_WYE, CALIBRATION_LN) ───────────────
    va:   float = 0.0
    vb:   float = 0.0
    vc:   float = 0.0
    vavg: float = 0.0

    # ── Shared ────────────────────────────────────────────────────────
    unb:   float = 0.0
    f:     float = 0.0
    state: int   = 0
    flags: List[str] = field(default_factory=list)


def parse_packet(line: str) -> Optional[Packet]:
    """Parse one JSON line from firmware. Returns None for non-data lines."""
    try:
        d = json.loads(line.strip())

        # Must have a timestamp to be a data packet (not a status/cal response).
        if 'ts' not in d:
            return None

        mode = MeasurementMode.from_str(d.get('mode', 'delta'))

        return Packet(
            ts   = int(d['ts']),
            mode = mode,
            # Delta fields
            uab  = float(d.get('uab',  0.0)),
            ubc  = float(d.get('ubc',  0.0)),
            uca  = float(d.get('uca',  0.0)),
            uavg = float(d.get('uavg', 0.0)),
            # Phase fields
            va   = float(d.get('va',   0.0)),
            vb   = float(d.get('vb',   0.0)),
            vc   = float(d.get('vc',   0.0)),
            vavg = float(d.get('vavg', 0.0)),
            # Shared
            unb  = float(d.get('unb', 0.0)),
            f    = float(d.get('f',   0.0)),
            state= int(d.get('state', 0)),
            flags= list(d.get('flags', [])),
        )
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None
