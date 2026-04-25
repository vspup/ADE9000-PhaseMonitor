# Orchestrator — progress snapshot (2026-04-24)

## What was done this session

### Groundwork (committed before this session)
- `docs/protocols/sequencer.md` — PC-side cross-device orchestration contract
  (trigger master, asymmetric timing, CSV layout, error matrix, terminology mapping).
- `software/pc_monitor/db_tool.py` — standalone Tkinter diagnostic tool for the
  Distribution Board (PING/STATUS/ARM/START, CAP STATUS/READ, EVT, plot window).

### This session (commits 8562e06 → c801d80)

| Commit | What |
|--------|------|
| `8562e06` | `core/distribution_client.py` + `tests/test_distribution_client.py` |
| `b8346a2` | `core/ade9000_client.py` + `tests/test_ade9000_client.py` |
| `c801d80` | `core/orchestrator.py` + `tests/test_orchestrator.py` |

#### `core/distribution_client.py`
Qt-free blocking client for the Distribution Board RS-485 protocol.
- `DistributionProtocol` — pure parsers: STATUS, CAP STATUS, CAP READ
  samples/done, EVT lines.
- `DistributionClient` — blocking API: `ping`, `ping_probe`, `status`, `arm`,
  `start` (raises `VbusBlockError` / `StartAlreadyOnError`), `cap_status`,
  `cap_read`, `take_events`.
- `_Transport` — background reader thread, CRLF/LF/CR tolerant, ASCII.

#### `core/ade9000_client.py`
Qt-free blocking client for the ADE9000 JSON Lines protocol.
- `Ade9000Protocol` — command strings, JSON parsing, telemetry detection.
- `Ade9000Client` — blocking API: `set_wmode_capture` (connect handshake),
  `sync_probe` (N SYNC probes → `SyncResult`, recv_ns recorded before JSON
  parsing), `cap_set`, `cap_arm_manual`, `cap_arm_dip`, `cap_trigger`,
  `cap_abort`, `cap_status` (→ `CaptureStatus`), `cap_read`
  (→ `list[CaptureSample]` + `CaptureDone`).
- Telemetry packets skipped in `_recv_json`; sync replies matched by seq.

#### `core/orchestrator.py`
Cross-device sequencer. Implements `sequencer.md §4` exactly.
- `OrchestratorConfig` — ports, pre/post, trigger_mode (manual|dip),
  dip_threshold, output_dir.
- `CaptureSession` — raw data from both devices; no filesystem I/O.
- `Orchestrator.run()` — blocking, phases 0–5:
  CONNECT → SYNC → ARM → FIRE → DRAIN → READ.
- Error handling per `sequencer.md §7`:
  `vbus_error` → abort ADE9000, raise `OrchestratorError`;
  drain timeout (6 s ADE9000 / 15 s Distribution) → raise;
  any exception → `_abort_both()`, ports always closed.
- Progress callback `on_progress(phase, msg)` for UI integration.

## Test status

```
151 passed, 0 failed  (3.18 s)
python -m pytest tests/  from software/pc_monitor/
```

| File | Tests | Covers |
|------|-------|--------|
| `test_distribution_client.py` | 51 | Protocol parsers + client API |
| `test_ade9000_client.py` | 36 | Protocol helpers + client API |
| `test_orchestrator.py` | 23 | Happy path, sequencing, drain, §7 errors |
| `test_capture_parser.py` | 20 | ADE9000 capture event parser |
| `test_packet_parser.py` | 12 | ADE9000 telemetry parser |
| `test_sync_probe.py` | 9 | Clock offset estimation |

## What remains (orchestrator plan steps 3–5)

### Step 3 — `core/session_writer.py`
Write the three session artifacts to disk (all-or-nothing):
```
captures/<session_id>/
  arduino.csv        — i, uab, ubc, uca, ia, ib, ic
  distribution.csv   — idx, ch0_raw, ch0_hex, ..., ch7_raw, ch7_hex
  session.json       — offsets, trigger ticks, FW versions, sample periods
```
Input: `CaptureSession` (returned by `Orchestrator.run()`).
Output: `SessionPaths` dataclass with the three `Path` objects.

### Step 4 — `core/orchestrator_worker.py`
Thin `QThread` wrapper:
- Signals: `progress(str, str)`, `done(CaptureSession)`, `failed(str)`.
- `run()` calls `Orchestrator.run()` then `session_writer.write_session()`.

### Step 5 — `ui/orchestrator_window.py` + `orchestrator_tool.py`
Standalone PySide6 window:
- Port selectors for both devices (Arduino + Distribution).
- Pre/Post spinboxes, trigger mode radio (manual / dip).
- Run button → progress log → on done: session directory path + key metrics
  (offset_ad_ms, trigger ticks, sample counts).

## Known gaps / follow-ups (from sequencer.md §8)

- Distribution has no proper `SYNC` command — `dist_offset_ms` is `rtt/2`
  approximation. Needs `SYNC <seq>` in FW (separate Distribution PR).
- No `CAP ABORT` command on Distribution — error recovery re-ARMs to IDLE.
- `db_tool.py` still has its own copy of `DistributionProtocol`; should
  import from `distribution_client.py` (cleanup PR).
- `_Transport` duplicated between `ade9000_client.py` and
  `distribution_client.py`; extract to `core/serial_transport.py` once
  orchestrator layer stabilises.
