"""Session artifact reader — inverse of session_writer.write_session().

Reconstructs a CaptureSession from a session directory written previously,
so the capture viewer can be opened on past runs without re-running hardware.

Lossy on fields that session.json does not persist (DistCapStatus.state,
SyncResult.rtt_ms_median / n_used, CaptureDone.trigger_index): these are
filled with sensible defaults that keep the viewer happy.

Usage:
    sess = read_session(Path("captures/2026-04-28T18-17-52"))
    CaptureViewDialog(sess).exec()
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.capture_parser import CaptureDone, CaptureSample
from core.distribution_client import DistCapSample, DistCapStatus
from core.orchestrator import CaptureSession, OrchestratorConfig
from core.sync_probe import SyncResult


class SessionReadError(Exception):
    """Raised when a session directory is malformed or unreadable."""


@dataclass
class SessionEntry:
    """Lightweight directory listing — for the session-browser UI.

    Holds just enough to render a row without parsing CSVs.
    """
    session_dir: Path
    session_id:  str
    started_at_pc_ns: int
    arduino_samples: int   # count, not the data
    dist_samples:    int


def list_sessions(captures_dir: Path) -> list[SessionEntry]:
    """Return entries for every readable session directory under captures_dir.

    Sorted newest-first by session_id (which is a sortable timestamp string).
    Subdirectories that don't look like sessions (missing session.json) are
    skipped silently — that lets old `cap_*.csv` files coexist.
    """
    if not captures_dir.is_dir():
        return []

    entries: list[SessionEntry] = []
    for child in captures_dir.iterdir():
        if not child.is_dir():
            continue
        sj = child / "session.json"
        if not sj.is_file():
            continue
        try:
            doc = json.loads(sj.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        entries.append(SessionEntry(
            session_dir      = child,
            session_id       = str(doc.get("session_id", child.name)),
            started_at_pc_ns = int(doc.get("started_at_pc_ns", 0)),
            arduino_samples  = _count_csv_rows(child / "arduino.csv"),
            dist_samples     = _count_csv_rows(child / "distribution.csv"),
        ))

    entries.sort(key=lambda e: e.session_id, reverse=True)
    return entries


def read_session(session_dir: Path) -> CaptureSession:
    """Load a CaptureSession previously written by write_session()."""
    session_dir = Path(session_dir)
    sj = session_dir / "session.json"
    ac = session_dir / "arduino.csv"
    dc = session_dir / "distribution.csv"
    for path in (sj, ac, dc):
        if not path.is_file():
            raise SessionReadError(f"missing file: {path}")

    try:
        doc = json.loads(sj.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SessionReadError(f"cannot parse {sj}: {exc}") from exc

    arduino_samples = _read_arduino_csv(ac)
    dist_samples    = _read_dist_csv(dc)

    a = doc.get("arduino", {}) or {}
    d = doc.get("distribution", {}) or {}

    cfg = OrchestratorConfig(
        arduino_port = str(a.get("port", "")),
        dist_port    = str(d.get("port", "")),
        pre          = int(a.get("pre",  0)),
        post         = int(a.get("post", 0)),
        trigger_mode = str(a.get("trigger_mode", "manual")),
        # output_dir points at the parent so a re-write would land back next door
        output_dir   = session_dir.parent,
    )

    arduino_done = CaptureDone(
        n                = len(arduino_samples),
        trigger_tick_ms  = int(a.get("trigger_tick_ms",  0)),
        sample_period_ms = int(a.get("sample_period_ms", 0)),
        pre              = int(a.get("pre",  0)),
        post             = int(a.get("post", 0)),
        trigger_index    = 0,
    )

    rtt_best = float(a.get("rtt_ms_best", 0.0))
    arduino_sync = SyncResult(
        offset_ms     = float(a.get("offset_ms", 0.0)),
        rtt_ms_median = rtt_best,
        rtt_ms_best   = rtt_best,
        n_samples     = int(a.get("n_sync_samples", 0)),
        n_used        = int(a.get("n_sync_samples", 0)),
    )

    dist_status = DistCapStatus(
        state            = "READY",
        samples          = len(dist_samples),
        trigger_idx      = int(d.get("trigger_idx", 0)),
        sample_period_ms = int(d.get("sample_period_ms", 0)),
        channels         = int(d.get("channels", 8)),
        trigger_tick     = int(d.get("trigger_tick_ms", 0)),
    )

    return CaptureSession(
        config          = cfg,
        started_at_ns   = int(doc.get("started_at_pc_ns", 0)),
        session_id      = str(doc.get("session_id", session_dir.name)),
        arduino_samples = arduino_samples,
        arduino_done    = arduino_done,
        arduino_sync    = arduino_sync,
        arduino_port    = cfg.arduino_port,
        dist_samples    = dist_samples,
        dist_status     = dist_status,
        dist_rtt_ms     = float(d.get("rtt_ms", 0.0)),
        dist_port       = cfg.dist_port,
        offset_ad_ms    = float(doc.get("offset_ad_ms", 0.0)),
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _count_csv_rows(path: Path) -> int:
    """Return the number of data rows (not counting the header)."""
    if not path.is_file():
        return 0
    try:
        with path.open(newline="", encoding="utf-8") as f:
            return max(0, sum(1 for _ in csv.reader(f)) - 1)
    except OSError:
        return 0


def _read_arduino_csv(path: Path) -> list[CaptureSample]:
    samples: list[CaptureSample] = []
    with path.open(newline="", encoding="utf-8") as f:
        rd = csv.reader(f)
        try:
            next(rd)   # header
        except StopIteration:
            return samples
        for row in rd:
            if not row:
                continue
            try:
                samples.append(CaptureSample(
                    i   = int(row[0]),
                    uab = float(row[1]),
                    ubc = float(row[2]),
                    uca = float(row[3]),
                    ia  = float(row[4]),
                    ib  = float(row[5]),
                    ic  = float(row[6]),
                ))
            except (IndexError, ValueError) as exc:
                raise SessionReadError(f"bad row in {path}: {row} ({exc})") from exc
    return samples


def _read_dist_csv(path: Path) -> list[DistCapSample]:
    samples: list[DistCapSample] = []
    with path.open(newline="", encoding="utf-8") as f:
        rd = csv.reader(f)
        try:
            header = next(rd)
        except StopIteration:
            return samples
        # 1 idx column + 2 columns per channel (raw, hex)
        n_channels = max(0, (len(header) - 1) // 2)
        for row in rd:
            if not row:
                continue
            try:
                idx = int(row[0])
                raw_ints: list[int]  = []
                hex_strs: list[str]  = []
                for ch in range(n_channels):
                    raw_ints.append(int(row[1 + ch * 2]))
                    hex_strs.append(str(row[2 + ch * 2]))
                samples.append((idx, raw_ints, hex_strs))
            except (IndexError, ValueError) as exc:
                raise SessionReadError(f"bad row in {path}: {row} ({exc})") from exc
    return samples
