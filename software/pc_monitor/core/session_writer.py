"""Session artifact writer — filesystem persistence for CaptureSession.

Writes three files to <output_dir>/<session_id>/ atomically
(temp dir → rename on same volume). Caller owns the CaptureSession object.

Usage:
    paths = write_session(session)
    print(paths.session_dir)   # Path to captures/2026-01-01T12-00-00/
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from core.orchestrator import CaptureSession

SCHEMA_VERSION = 2


@dataclass
class SessionPaths:
    session_dir:  Path
    arduino_csv:  Path
    dist_csv:     Path
    session_json: Path


def write_session(session: CaptureSession) -> SessionPaths:
    """Write arduino.csv, distribution.csv, session.json atomically.

    Uses a temp directory inside output_dir for all writes, then renames
    it to the final session directory in one OS call (same-volume move).
    On any error the temp directory is cleaned up and the exception propagates.
    """
    out_dir = Path(session.config.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    target = out_dir / session.session_id
    tmp    = Path(tempfile.mkdtemp(dir=out_dir, prefix=".tmp_"))
    try:
        _write_arduino_csv(tmp, session)
        _write_dist_csv(tmp, session)
        _write_session_json(tmp, session)
        tmp.rename(target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise

    return SessionPaths(
        session_dir  = target,
        arduino_csv  = target / "arduino.csv",
        dist_csv     = target / "distribution.csv",
        session_json = target / "session.json",
    )


def _write_arduino_csv(tmp: Path, session: CaptureSession) -> None:
    with (tmp / "arduino.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["i", "uab", "ubc", "uca", "ia", "ib", "ic"])
        for s in session.arduino_samples:
            w.writerow([s.i, s.uab, s.ubc, s.uca, s.ia, s.ib, s.ic])


def _write_dist_csv(tmp: Path, session: CaptureSession) -> None:
    ch_headers = [f"ch{i}_{sfx}" for i in range(8) for sfx in ("raw", "hex")]
    with (tmp / "distribution.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["idx"] + ch_headers)
        for idx, raw_ints, hex_strs in session.dist_samples:
            row: list = [idx]
            for raw, hex_v in zip(raw_ints, hex_strs):
                row += [raw, hex_v]
            w.writerow(row)


def _write_session_json(tmp: Path, session: CaptureSession) -> None:
    cfg     = session.config
    done    = session.arduino_done
    ds      = session.dist_status
    a_sync  = session.arduino_sync
    d_sync  = session.dist_sync

    doc = {
        "schema_version":   SCHEMA_VERSION,
        "session_id":       session.session_id,
        "started_at_pc_ns": session.started_at_ns,
        "arduino": {
            "port":             session.arduino_port,
            "trigger_mode":     cfg.trigger_mode,
            "pre":              done.pre,
            "post":             done.post,
            "trigger_tick_ms":  done.trigger_tick_ms,
            "sample_period_ms": done.sample_period_ms,
            "offset_ms":        a_sync.offset_ms,
            "rtt_ms_best":      a_sync.rtt_ms_best,
            "n_sync_samples":   a_sync.n_samples,
        },
        "distribution": {
            "port":             session.dist_port,
            "trigger_tick_ms":  ds.trigger_tick,
            "trigger_idx":      ds.trigger_idx,
            "sample_period_ms": ds.sample_period_ms,
            "channels":         ds.channels,
            "offset_ms":        d_sync.offset_ms,
            "rtt_ms_best":      d_sync.rtt_ms_best,
            "n_sync_samples":   d_sync.n_samples,
        },
        "offset_ad_ms": session.offset_ad_ms,
    }
    (tmp / "session.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
