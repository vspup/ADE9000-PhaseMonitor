# Architecture Decisions

Chronological log of important project decisions.
Each non-trivial decision = one entry.

---

## 2026-04-17: Project bootstrap

**Context:** new project start
**Decision:** bootstrapped using project-bootstrap v1.3
**Source:** https://github.com/vspup/claude-templates
**Consequences:**
- Structure: Extended type (firmware + software + reference)
- `.claude/`, `docs/decisions.md`, `CLAUDE.md` added
- Language rule: Discussion — Russian, Code/docs — English
- Living standards go to `docs/standards/` as they emerge

---

## 2026-04-17: ADE9000 + Arduino Zero as hardware platform

**Context:** need a metering IC capable of accurate RMS voltage measurement on 3-phase delta 400V grid
**Decision:** ADE9000 via EV-ADE9000SHIELDZ eval board + Arduino Zero (SAMD21, 3.3V SPI)
**Alternatives:** software RMS via ADC samples (insufficient accuracy), INA series (current-focused)
**Consequences:**
- SPI driver required (`ade9000_driver.cpp`)
- Voltage scaling factor `5.376e-6` V/count — board-specific, must be recalibrated after hardware mod
- Frequency auto-detection (50/60 Hz) built into firmware

---

## 2026-04-17: JSON Lines over USB CDC as firmware↔PC protocol

**Context:** need simple, debuggable, cross-platform data transport
**Decision:** JSON Lines at 115200 baud over USB CDC (Arduino as virtual COM port)
**Alternatives:** binary packed structs (faster but opaque), Modbus (overkill), CSV (no schema)
**Consequences:**
- `protocol.cpp` serializes each measurement cycle to one JSON line
- `packet_parser.py` on PC side, graceful fallback on malformed lines
- See `docs/protocols/firmware-pc.md` for packet specification

---

## 2026-04-17: PySide6 + PyQtGraph for PC application

**Context:** need real-time plotting at 5–10 Hz, cross-platform, Python
**Decision:** PySide6 (Qt6 bindings) + PyQtGraph for plots, QThread for serial I/O
**Alternatives:** tkinter (no real-time plot), Dear PyGui (less mature), Electron (heavy)
**Consequences:**
- Requires PySide6 >= 6.5.0
- `SerialReader` runs in QThread, emits signals to main thread
- `core/` strictly contains no Qt imports — testable without display

---

## 2026-04-19: UI layout — Uavg merged into voltage plot, dual Y-axis for bottom panel

**Context:** 4 separate plots wasted vertical space; Uavg scale matches Uab/Ubc/Uca
**Decision:** Uavg (yellow dashed) added to voltage graph; Unbalance% + Frequency on separate bottom panels; voltage plot gets 2/3 height via stretch factor
**Consequences:**
- 3 plots total instead of 4
- Bottom row: unbalance (left) + frequency (right), both linked to voltage X-axis

---

## 2026-04-20: Measurement mode architecture (CALIBRATION_LN / MEASURE_DELTA / MEASURE_WYE)

**Context:** project started as delta-only; wye support and per-phase calibration were added
**Decision:** explicit `MeasurementMode` enum shared across firmware and Python; mode carried in every JSON packet
**Why:** each mode has different physically meaningful fields — sending wrong fields is misleading
**Consequences:**
- Firmware: `mode_manager.cpp` owns ACCMODE register; `measurements`, `protocol`, `events` are mode-aware
- Python: `Packet` carries `mode`; UI (`control_panel`, `plot_panel`) switches field sets on mode change
- `CALIBRATION_LN` is a mode — ensures ACCMODE is set correctly during calibration

---

## 2026-04-20: serial_reader.py placed in core/ not io/

**Context:** python-profile recommends `io/` layer for external I/O, separate from `core/`
**Decision:** keep `serial_reader.py` in `core/` alongside the rest of the data pipeline
**Why:** the PC app is small (one component); an `io/` layer would add indirection without benefit
**How to apply:** if the app grows a second transport (TCP, file replay), extract `io/` then

---

## 2026-04-20: FlashStorage for calibration gain persistence

**Context:** per-phase voltage gains must survive power cycles
**Decision:** `FlashStorage` library (SAMD21 NVM emulation) with magic word `0xADE99000`
**Alternatives:** EEPROM emulation (not available on SAMD21 natively)
**Consequences:**
- Gains loaded and applied at `calibrationInit()` before first measurement
- Magic mismatch → defaults `{1.0, 1.0, 1.0}` applied silently
- NVM write wear: only on explicit `CAL SAVE` command, not on every measurement
