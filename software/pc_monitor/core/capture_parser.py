"""Parser for CAPTURE-mode events from firmware.

Handles the `cap_*` JSON Lines emitted in WORK_MODE_CAPTURE:
  - cap_status  — FSM + fill level (response to CAP STATUS / CAP ARM)
  - cap_sample  — one captured sample (streamed during CAP READ)
  - cap_done    — end-of-stream marker after CAP READ

Status-only markers (`cap_triggered`, `cap_aborted`, `cap_busy` etc.) are
not parsed here — callers can read `event` directly off the raw JSON.
"""
import json
from dataclasses import dataclass
from typing import Optional, Union


@dataclass
class CaptureStatus:
    state:  str   # "IDLE" | "ARMED" | "TRIGGERED" | "READY"
    filled: int
    total:  int


@dataclass
class CaptureSample:
    i:   int      # sample index: -100..-1 pre, 0 trigger, 1..199 post
    uab: float
    ubc: float
    uca: float
    ia:  float
    ib:  float
    ic:  float


@dataclass
class CaptureDone:
    n: int        # number of samples streamed


CaptureEvent = Union[CaptureStatus, CaptureSample, CaptureDone]


def parse_capture_event(line: str) -> Optional[CaptureEvent]:
    """Return a typed capture event, or None for any non-capture / malformed line."""
    try:
        d = json.loads(line.strip())
        ev = d.get('event')
        if ev == 'cap_sample':
            return CaptureSample(
                i   = int(d['i']),
                uab = float(d.get('uab', 0.0)),
                ubc = float(d.get('ubc', 0.0)),
                uca = float(d.get('uca', 0.0)),
                ia  = float(d.get('ia',  0.0)),
                ib  = float(d.get('ib',  0.0)),
                ic  = float(d.get('ic',  0.0)),
            )
        if ev == 'cap_status':
            return CaptureStatus(
                state  = str(d['state']),
                filled = int(d['filled']),
                total  = int(d['total']),
            )
        if ev == 'cap_done':
            return CaptureDone(n=int(d['n']))
        return None
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None
