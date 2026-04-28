# Orchestrator — progress snapshot (2026-04-28)

## What is working (end-to-end verified on hardware)

Full startup-capture session runs reliably:

```
CONNECT → SYNC → ARM → FIRE → DRAIN → READ → WRITE → DONE
```

ADE9000 500 samples @ 10 ms/sample (5 s window)  
Distribution 254 samples @ 25 ms/sample (6.35 s window)  
Session artifacts written atomically to `captures/<session_id>/`

## Commits this session (dev, ADE9000-PhaseMonitor)

| Commit | What |
|--------|------|
| `313ba1c` | `core/session_writer.py` + tests (33) |
| `b944df7` | `core/orchestrator_worker.py` + tests |
| `aad331f` | `ui/orchestrator_window.py` + `orchestrator_tool.py` |
| `75896ab` | fix: MODE CMD before PING to Distribution |
| `1c47b05` | fix: ping() scans for PONG, skips RS-485 echo garbage |
| `4e05626` | fix: trigger_tick optional in CAP STATUS parser (FW buf 96→128) |
| `7794009` | feat: port_scanner + Scan button in UI |
| `bbe045f` | fix: listen-first probe in _probe_ade9000 (no TX on alien ports) |
| `ee9cb5d` | feat: UI redesign — per-device dropdowns, indicators, tooltips |
| `e64bb2e` | fix: restore ADE9000 monitor mode after session; substring STATUS match |

## Commits this session (dev, mps2p-FW-db-v3)

| Commit | What |
|--------|------|
| `37c8fa5` | fix: s_cap_tx 96→128 B (CAP STATUS truncated trigger_tick) |
| `76a3513` | feat: Phase 5 EVT wiring — vbus_block + RS485_U1_EmitEvent docs |

## Test status

```
219 passed, 1 skipped  (5.67 s)
python -m pytest tests/  from software/pc_monitor/
```

| File | Tests |
|------|-------|
| `test_distribution_client.py` | 52 |
| `test_ade9000_client.py` | 36 |
| `test_orchestrator.py` | 23 |
| `test_session_writer.py` | 33 |
| `test_orchestrator_worker.py` | 1 skipped (no PySide6 in test env) |
| `test_port_scanner.py` | 35 |
| `test_capture_parser.py` | 20 |
| `test_packet_parser.py` | 12 |
| `test_sync_probe.py` | 9 |

## Known issues / to fix next session

| Priority | Issue |
|----------|-------|
| High | ADE9000 RTT_best 96–110 ms (Windows USB latency) → ~50 ms jitter in `offset_ad_ms`. Investigate USB CDC latency or increase `best_k`. |
| Medium | `dist_offset_ms = rtt/2` approximation — needs `SYNC <seq>` in Distribution FW. |
| Medium | `_Transport` duplicated in `ade9000_client.py` and `distribution_client.py` — extract to `core/serial_transport.py`. |
| Low | `db_tool.py` has own copy of `DistributionProtocol` — replace with import. |
| Low | No `CAP ABORT` in Distribution FW — FSM stuck on error until reconnect. |

## Not yet implemented

- **Data viewer**: after DONE, no plots in UI. Only session path shown.
- **Session browser**: no way to re-open previous sessions.
- **Re-run without restart**: after error on FIRE/DRAIN, board stays in ARM. Need reset flow in UI.
- **Distribution SYNC command**: proper clock offset (currently rtt/2).

## Architecture

```
orchestrator_tool.py
  └── OrchestratorWindow (PySide6)
        ├── port_scanner.scan_ports()         ← auto COM detection
        └── OrchestratorWorker (QThread)
              ├── Orchestrator.run()
              │     ├── Ade9000Client (115200, JSON Lines)
              │     └── DistributionClient (57600, text RS-485)
              └── session_writer.write_session()
                    └── captures/<session_id>/
                          ├── arduino.csv
                          ├── distribution.csv
                          └── session.json
```
