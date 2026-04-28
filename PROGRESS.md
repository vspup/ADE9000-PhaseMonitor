# Orchestrator — progress snapshot (2026-04-28, evening)

## What is working (end-to-end verified on hardware)

Full startup-capture session runs reliably:

```
CONNECT → SYNC → ARM → FIRE → DRAIN → READ → WRITE → DONE
```

ADE9000 500 samples @ 10 ms/sample (5 s window)
Distribution 254 samples @ 25 ms/sample (6.35 s window)
Session artifacts written atomically to `captures/<session_id>/`

After **DONE** the user can click **View Plots**, the main window steps
aside and a maximized analysis viewer opens with all three signal rows
(V / I / ADC), per-plot Y controls, global X / Trigger controls,
focus modes, ADC channel filter, and click-to-place comparison markers.

## Commits this session — earlier window

| Commit | What |
|--------|------|
| `313ba1c` | `core/session_writer.py` + tests |
| `b944df7` | `core/orchestrator_worker.py` + tests |
| `aad331f` | `ui/orchestrator_window.py` + `orchestrator_tool.py` |
| `75896ab` | fix: MODE CMD before PING to Distribution |
| `1c47b05` | fix: ping() scans for PONG, skips RS-485 echo garbage |
| `4e05626` | fix: trigger_tick optional in CAP STATUS parser (FW buf 96→128) |
| `7794009` | feat: port_scanner + Scan button in UI |
| `bbe045f` | fix: listen-first probe in _probe_ade9000 |
| `ee9cb5d` | feat: UI redesign — per-device dropdowns, indicators, tooltips |
| `e64bb2e` | fix: restore ADE9000 monitor mode after session |

## Commits this session — post-04-28 PROGRESS snapshot

| Commit | What |
|--------|------|
| `ec6bd87` | fix(scan,rs485): harden port scan + tolerate RS-485 echo/garble |
| `0bb2243` | feat(ui): post-session capture viewer dialog (initial) |
| `45ef7b7` | fix(scan): 50 ms settle before first TX in _probe_dist |
| `aa0ec54` | feat(viewer): large dialog + dual cursor markers per plot group |
| `cc74817` | feat(ui): 2 s heartbeat + structured error surface + marker snap-to-sample |
| `1244a14` | fix(drain): tolerate isolated CAP STATUS failures from Distribution |
| `950ba54` | feat(viewer): full plotting overhaul — focus, legend toggle, X/Y scale controls, ADC filter |
| `6f0a15a` | refactor(viewer): pro-oscilloscope UI polish — section titles, split bars, sync cursor, point markers |
| `c6d1fb8` | feat(viewer): take-over flow — main hides, viewer maximized + [Back] [Fullscreen] [Reset] header |
| `133e1ba` | refactor(viewer): drop mouse zoom/pan/hover — view changes only via the X/Y spinboxes |
| `58b4f00` | fix(viewer): marker pane — invisible on dark theme + plots jumped on marker place |

## Commits this session (dev, mps2p-FW-db-v3)

| Commit | What |
|--------|------|
| `37c8fa5` | fix: s_cap_tx 96→128 B (CAP STATUS truncated trigger_tick) |
| `76a3513` | feat: Phase 5 EVT wiring — vbus_block + RS485_U1_EmitEvent docs |

## Test status

```
234 passed   (≈10 s)
python -m pytest tests/  from software/pc_monitor/
```

Tests by file (collected count, latest run):

| File | Tests |
|------|-------|
| `test_distribution_client.py` | 52 |
| `test_ade9000_client.py` | 36 |
| `test_orchestrator.py` | 26 |
| `test_session_writer.py` | 33 |
| `test_orchestrator_worker.py` | 8 |
| `test_port_scanner.py` | 35 |
| `test_capture_parser.py` | 20 |
| `test_packet_parser.py` | 12 |
| `test_sync_probe.py` | 9 |
| (others)                      | 3 |

Net delta vs 04-28 snapshot: +15 tests (drain retry coverage,
worker structured-error tests, capture-parser coverage, viewer
sanity).

## Capture viewer — feature inventory

Header bar
- `← Back` (Esc) returns to the main window
- `Fullscreen` toggles between maximized and full-screen (F11)
- Title `Capture — <session_id> · Analysis Mode`
- `Reset View` autoscales X and Y on every plot

Top toolbar (two rows)
- Focus mode `All / V / I / ADC` (hides non-relevant rows)
- `Point markers` toggle — round dots at every sample on every line
- ADC channel filter (8 checkboxes + presets `All` / `u17` / `u18` / `None`)

Three plot rows, each with its own Y strip
- Section header (`Voltage scale`, `Current scale`, `ADC scale`)
- `max` / `min` spinboxes with unit suffix
- `Apply` / `Auto` buttons

Per-plot trigger overlay
- 2 px red dashed line at t = 0 with translucent ±5 ms band
- `TRIGGER  t = 0.0 ms` label on the band
- `view: ±W ms (span)` corner label that updates with every X change

X bar (two rows)
- Time range — X min / X max + `Apply X` / `Auto X`
- Trigger — ± half-window + `Centre on trigger`

Markers (mouse, structured monospace readout below the X bar)
- Left-click on V or I row → ADE9000 group marker on both
- Left-click on ADC row → Distribution group marker on that row
- Right-click → clears markers in the clicked group
- Two markers per group with M1 / M2 / Δt readout
- Pane has fixed reserved height — plots do not shift on placement

Legend
- Outside the axes (top-right of canvas)
- Click an entry to toggle the matching series; entry dims to alpha 0.35

Mouse-driven view changes (zoom / pan / hover) — explicitly **removed**;
all view edits go through the spinboxes.

## Robustness

- 2 s idle heartbeat between sessions; cable-yank shows a red dot
  on the affected port indicator within 2 s.
- Errors from `Orchestrator` carry `phase` / `device` / `command`;
  the failure panel shows e.g. *"Distribution failed on START"*
  with the underlying message and stays up until the next run.
- DRAIN tolerates up to 2 consecutive `CAP STATUS` timeouts (3 s each)
  before failing — handles isolated RS-485 garble without aborting
  a long capture.
- Marker pane styling neutralised — readable on light or dark Qt themes.

## Known issues / to fix next session

| Priority | Issue |
|----------|-------|
| High | ADE9000 RTT_best 96–110 ms (Windows USB latency) → ~50 ms jitter in `offset_ad_ms`. Investigate USB CDC latency or increase `best_k`. |
| Medium | `dist_offset_ms = rtt/2` approximation — needs `SYNC <seq>` in Distribution FW. |
| Medium | `_Transport` duplicated in `ade9000_client.py` and `distribution_client.py` — extract to `core/serial_transport.py`. |
| Medium | Viewer marker pane is fixed at 150 px — comfortable for typical use but clips two-marker pairs in both groups simultaneously (rare). Switch to `QScrollArea` if the case becomes common. |
| Low | `db_tool.py` has own copy of `DistributionProtocol` — replace with import. |
| Low | No `CAP ABORT` in Distribution FW — FSM stuck on error until reconnect. |

## Not yet implemented

- **Session browser**: no way to re-open previous sessions written
  to `captures/`. Today the viewer is reachable only after a fresh
  successful run.
- **Re-run without restart**: after error on FIRE / DRAIN, the
  Distribution board stays in ARM. Need a reset / abort flow in UI.
- **Distribution SYNC command**: proper clock offset (currently `rtt/2`).
- **Export from viewer**: PNG snapshot / CSV slice between markers.

## Architecture

```
orchestrator_tool.py
  └── OrchestratorWindow (PySide6)
        ├── port_scanner.scan_ports()         ← auto COM detection
        ├── 2 s heartbeat → port liveness indicators
        ├── OrchestratorWorker (QThread)
        │     ├── Orchestrator.run()
        │     │     ├── Ade9000Client (115200, JSON Lines)
        │     │     └── DistributionClient (57600, text RS-485)
        │     └── session_writer.write_session()
        │           └── captures/<session_id>/
        │                 ├── arduino.csv
        │                 ├── distribution.csv
        │                 └── session.json
        └── CaptureViewDialog                ← View Plots after DONE
              · 3 _PlotRow widgets (own Figure, Canvas, Y controls)
              · X bar (Time range + Trigger)
              · Marker pane (ADE9000 / Distribution groups)
```

## Next actions (priority order)

1. **Session browser** — open the last N sessions from `captures/`
   directly into `CaptureViewDialog`. Smallest path to making the
   viewer reusable beyond a single run.
2. **Reset / abort flow** in the orchestrator UI — explicit recovery
   from FIRE/DRAIN failures so the Distribution board can be
   re-armed without a manual reconnect.
3. **Decide marker pane fate** — confirm the 150 px reserved height
   feels right on a real-data run; switch to `QScrollArea` only if
   the user actually hits the clip case.
4. **Extract `_Transport`** into `core/serial_transport.py` so the
   two device clients stop carrying duplicate code.
5. **Distribution `SYNC <seq>`** — replace the `rtt/2` offset with a
   real probe; coordinate with `mps2p-FW-db-v3/`.
6. **Export from viewer** — PNG snapshot of the current view, plus a
   CSV slice between M1/M2 if both are placed.
