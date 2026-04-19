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
**Decision:** Uavg (yellow dashed) added to voltage graph; Unbalance% + Frequency combined with secondary ViewBox (right Y-axis); voltage plot gets 2/3 height via stretch factor
**Consequences:**
- `plot_panel.py` uses `pg.ViewBox` for frequency right axis — geometry sync via `sigResized`
- 3 plots total instead of 4
