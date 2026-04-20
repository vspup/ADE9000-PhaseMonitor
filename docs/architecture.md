# System Architecture

## 1. Overview

The **ADE9000 Phase Monitor** is a hardware-software system for real-time three-phase voltage
monitoring. It supports delta (L-L) and wye (L-N) topologies and includes per-phase calibration.

---

## 2. System Components

### 2.1 Measurement Hardware

- ADE9000 evaluation board (EV-ADE9000SHIELDZ, voltage divider modified for 400V)
- Arduino Zero (SAMD21, 3.3V) — data acquisition and protocol controller

### 2.2 Embedded Firmware

Runs on Arduino Zero. Source layout:

```
ade9000_phase_monitor/
├── ade9000_phase_monitor.ino   — entry point (setup/loop only)
├── types.h / constants.h       — shared types and enums
└── src/
    ├── board/                  — config.h, pins.h
    ├── sensors/ade9000/        — SPI driver, RMS acquisition
    ├── app/                    — orchestration, state, mode, calibration, events, commands
    └── protocol/               — JSON Lines output
```

Responsibilities:
- ADE9000 SPI initialization and register configuration
- RMS voltage acquisition (AVRMS / BVRMS / CVRMS)
- Mode-aware measurement (delta or wye)
- Derived parameter calculation (Uavg, unbalance %)
- Frequency auto-detection (50 / 60 Hz) and ACCMODE update
- Event detection (dip, unbalance, frequency deviation, startup)
- System state machine
- Per-phase voltage gain calibration (NVM-persisted)
- JSON telemetry stream + command processing over UART

### 2.3 PC Application

Python / PySide6 desktop application. Source layout:

```
software/pc_monitor/
├── main.py
├── core/     — packet_parser, serial_reader, data_buffer, logger, measurement_mode
└── ui/       — main_window, plot_panel, control_panel, calibration_dialog, status_bar
```

Responsibilities:
- Serial port connection and command sending
- JSON packet parsing and mode detection
- Real-time plots (voltage, unbalance, frequency)
- Mode selector (Delta / Wye) with immediate firmware sync
- Guided calibration dialog
- CSV data logging

---

## 3. Measurement Modes

| Mode | ACCMODE VCONSEL | Voltages | Use case |
|---|---|---|---|
| `MEASURE_DELTA` | 001 (VB=VA−VC) | Uab, Ubc, Uca | 3-phase delta grid, default |
| `MEASURE_WYE` | 000 (direct) | Va, Vb, Vc | 3-phase wye grid |
| `CALIBRATION_LN` | 000 (direct) | Va, Vb, Vc | Per-phase gain calibration |

Mode is carried in every JSON packet (`"mode"` field). The PC app adapts its display accordingly.
`mode_manager.cpp` is the single authority for writing the ACCMODE register — it preserves the
SELFREQ bit (50/60 Hz) on every mode switch.

---

## 4. Data Flow

```
3-phase grid
↓
Voltage dividers (EV-ADE9000SHIELDZ, modified for 400V)
↓
ADE9000 (RMS registers: AVRMS / BVRMS / CVRMS)
↓  SPI
Arduino Zero — firmware (measurement, events, state, protocol)
↓  UART / USB-CDC  115200 baud  JSON Lines  5 Hz
PC Application (Python / PySide6)
↓
Real-time plots + CSV logging
```

---

## 5. Communication Interface

See `docs/protocols/firmware-pc.md` for full packet specification.

Transport: UART over USB-CDC, 115200 baud.
Protocol: JSON Lines — one JSON object per newline.
Direction: firmware → PC (telemetry), PC → firmware (commands).

---

## 6. System States

```
IDLE → MONITORING → ARMED → EVENT_DETECTED → RECORDING → COMPLETED
```

---

## 7. Design Principles

- Thin sketch: `.ino` only calls `appSetup()` / `appLoop()`
- Single register authority: all ACCMODE writes go through `modeSet()`
- Mode-aware pipeline: measurement, events, and protocol all branch on current mode
- Stateless stream: every packet is self-contained (carries `mode`, `ts`, `state`)
- No Qt in `core/`: PC core layer is testable without a display
- Thread boundary: serial reads on QThread, all UI updates on main thread

---

## 8. Future Extensions

- Current measurement (IARMs / IBRMs / ICRMs)
- Power and energy calculation
- Harmonic analysis
- Network interface (TCP/IP remote monitoring)
