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
- `firmware/arduino-zero/` — Arduino sketch and C++ modules
- `software/pc_monitor/` — PySide6 desktop application
- `docs/` — technical documentation and decisions
- `docs/protocols/` — firmware ↔ PC communication protocol
- `docs/standards/` — living development standards
- `reference/` — datasheets, schematics (PDFs)
- `data/` — measurement logs (not tracked)

## Conventions
- Firmware: one `.cpp/.h` pair per module (driver, measurements, calculations, events, state_machine, protocol)
- Protocol: JSON Lines over USB CDC, 5 Hz update rate — see `docs/protocols/firmware-pc.md`
- PC app: `core/` (no Qt) strictly separated from `ui/` (Qt only)
- Packet dataclass is the single source of truth for field names

## Important
- Before large changes — read `docs/decisions.md`
- Voltage scaling factor `5.376e-6` in `ade9000_driver.cpp` is board-specific — do not change without hardware recalibration
- `.venv` lives inside `software/pc_monitor/` — do not move it
