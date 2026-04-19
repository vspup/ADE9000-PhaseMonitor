# ADE9000-PhaseMonitor

Real-time three-phase (delta) voltage monitoring system based on ADE9000 and Arduino Zero.
Measures Uab, Ubc, Uca, detects voltage dips and unbalance, visualizes data on PC in real time.

## Hardware

- Arduino Zero (SAMD21)
- EV-ADE9000SHIELDZ evaluation board
- Voltage dividers for 400V delta (hardware modification required for full voltage range)

## Installation

**Firmware:** open `firmware/arduino-zero/ade9000_phase_monitor/` in Arduino IDE and upload to Arduino Zero.

**PC application:**
```bash
cd software/pc_monitor
python -m venv .venv

# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Windows cmd:
.venv\Scripts\activate.bat
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

## Usage

```bash
cd software/pc_monitor
python main.py
```

1. Select COM port in the toolbar
2. Click **Connect**
3. Real-time plots appear: voltages (Uab/Ubc/Uca/Uavg), unbalance %, frequency
4. Use **Start Logging** to save session to CSV

## Development

- `CLAUDE.md` — AI assistant context
- `docs/decisions.md` — architecture decisions log
- `docs/protocols/firmware-pc.md` — JSON packet specification
- `docs/standards/` — development standards

## Language policy

Discussion in chat/issues: Russian.
Code, documentation, commits, interfaces: English.
