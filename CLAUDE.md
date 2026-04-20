# ADE9000-PhaseMonitor

Real-time three-phase (delta, 400V) voltage monitoring system.
Arduino Zero + ADE9000 metering IC → USB/UART → PySide6 PC application.

## Language rule
- Discussion with AI: Russian
- Code, docs, comments, commits, interfaces: English

## Stack
- **Firmware:** C++17, Arduino Zero, ADE9000 (SPI), UART 115200 baud
- **Software:** Python 3.11+, PySide6 6.5+, PyQtGraph, PySerial, NumPy

## Commands
- Run PC app: `cd software/pc_monitor && python main.py`
- Lint: `cd software/pc_monitor && ruff check .`

## Structure
- `firmware/arduino-zero/ade9000_phase_monitor/` — Arduino sketch + all C++ modules (flat, Arduino IDE requirement)
- `software/pc_monitor/` — PySide6 desktop application
  - `core/` — packet parsing, serial I/O, data buffer, logger, measurement mode
  - `ui/` — main window, plots, control panel, calibration dialog
- `docs/` — technical documentation and decisions
- `docs/protocols/` — firmware ↔ PC communication protocol
- `docs/standards/` — living development standards
- `reference/` — datasheets, schematics (PDFs, in .claudeignore)
- `data/` — measurement logs (not tracked)

## Measurement modes
- `MEASURE_DELTA` — line-to-line Uab/Ubc/Uca (default, ACCMODE VCONSEL=001)
- `MEASURE_WYE` — phase-to-neutral Va/Vb/Vc (ACCMODE VCONSEL=000)
- `CALIBRATION_LN` — per-phase gain calibration, entered via Calibrate button in UI

## Conventions
- Firmware: one `.cpp/.h` pair per module; thin `.ino` — only `appSetup()`/`appLoop()`
- Protocol: JSON Lines over USB CDC, 5 Hz update rate — see `docs/protocols/firmware-pc.md`
- PC app: `core/` (no Qt) strictly separated from `ui/` (Qt only)
- Packet dataclass is the single source of truth for field names
- `serial_reader.py` lives in `core/` — deliberate (see `docs/decisions.md`)
- Mode change flow: incoming packet → `main_window` detects → `ctrl.set_mode()` + `plots.set_mode()`

## Important
- Before large changes — read `docs/decisions.md`
- Changing ACCMODE: always via `modeSet()` in `mode_manager.cpp` — must preserve SELFREQ bit
- Voltage scaling factor `5.376e-6` in `ade9000_driver.cpp` is board-specific — do not change without hardware recalibration
- `.venv` lives inside `software/pc_monitor/` — do not move it
