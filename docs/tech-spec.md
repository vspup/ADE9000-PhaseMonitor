# Three-Phase Voltage Monitoring System (ADE9000 + Arduino Zero)

## 1. Project Overview

### 1.1 Purpose

Develop a standalone hardware-software tool for monitoring three-phase voltage behavior. The tool is usable on its own (bench diagnostics, grid audits, long-term monitoring) and can be embedded as a subsystem in larger projects.

The system is intended for engineering diagnostics and analysis of:

- voltage dips
- phase imbalance (unbalance)
- transient behavior
- abnormal grid conditions

### 1.1.1 Scope of this document

This spec covers the **ADE9000 PhaseMonitor tool only** — Arduino Zero + ADE9000 firmware and its PySide6 PC application. It does NOT describe any larger system that may use this tool as a building block.

When this tool is embedded in the MPS2P startup-sequence analyzer, cross-device orchestration (coordinating it with the MPS2P Distribution Board) is specified separately:

- Umbrella system spec: `../../mps2p-tran/TECH_SPEC.md`
- PC-side orchestration contract: `docs/protocols/sequencer.md`

These external documents adapt to this one, not the other way around. Changes here must remain compatible with the wire protocol in `docs/protocols/firmware-pc.md`.

---

### 1.2 Key Objective

Measure and visualize **three-phase voltages (delta L-L or wye L-N, up to 400V)** in real time, with event detection, per-phase calibration, and logging.

---

## 2. System Architecture

### 2.1 Hardware

- Arduino Zero
- ADE9000-based board (EV-ADE9000SHIELDZ)
- Modified voltage dividers for ~400V delta system
- USB connection to PC

### 2.2 Software

- Embedded firmware (Arduino)
- Desktop application (PC, Python-based)
- Logging subsystem

---

### 2.3 Data Flow

```

3-phase grid (delta 400V)
↓
Voltage dividers
↓
ADE9000
↓
Arduino Zero
↓ UART (USB CDC)
PC Application
↓
Visualization + Logging

```

---

## 3. Measurement Model

### 3.1 Electrical Configuration

- System type: 3-phase, 3-wire (delta)
- Nominal voltage: 400V (line-to-line)

---

### 3.2 Measured Signals

| Parameter | Description | Unit |
|----------|------------|------|
| Uab | Line voltage A-B | V |
| Ubc | Line voltage B-C | V |
| Uca | Line voltage C-A | V |
| Freq | Grid frequency | Hz |

---

### 3.3 Derived Parameters

| Parameter | Formula | Unit |
|----------|--------|------|
| Uavg | (Uab + Ubc + Uca) / 3 | V |
| Unbalance | max(|Ui - Uavg|) / Uavg * 100% | % |

---

## 4. Functional Requirements

### 4.1 Measurement

System shall:
- read RMS voltages from ADE9000
- compute derived values
- update data in real-time

---

### 4.2 Event Detection

System shall detect:

- Voltage dip  
```

any(U < threshold)

```

- Voltage imbalance  
```

unbalance > threshold

````

- Frequency deviation

---

### 4.3 Logging

System shall:
- store measurement data
- store event markers
- support export (CSV / JSON)

---

### 4.4 Visualization

System shall provide real-time plots.

---

## 5. System States

```text
0 = IDLE
1 = MONITORING
2 = ARMED
3 = EVENT_DETECTED
4 = RECORDING
5 = COMPLETED
6 = FAULT
````

---

## 6. Data Interface (UART Protocol)

### 6.1 Format

JSON per line

### 6.2 Example

```json
{
  "ts": 15230,
  "uab": 401.2,
  "ubc": 398.7,
  "uca": 403.1,
  "uavg": 401.0,
  "unb": 0.86,
  "f": 60.01,
  "state": 1,
  "flags": ["dip"]
}
```

---

### 6.3 Field Definitions

| Field | Description     |
| ----- | --------------- |
| ts    | timestamp (ms)  |
| uab   | voltage AB      |
| ubc   | voltage BC      |
| uca   | voltage CA      |
| uavg  | average voltage |
| unb   | unbalance %     |
| f     | frequency       |
| state | system state    |
| flags | event flags     |

---

## 7. Event Flags

| Flag      | Meaning                 |
| --------- | ----------------------- |
| dip       | voltage below threshold |
| unbalance | excessive imbalance     |
| startup   | startup detected        |
| freq_err  | frequency out of range  |

---

## 8. Performance Requirements

| Parameter   | Requirement          |
| ----------- | -------------------- |
| Update rate | 5–10 Hz              |
| Latency     | < 500 ms             |
| Stability   | continuous operation |

---

## 9. PC Application Requirements

### 9.1 Core Features

* Serial connection
* Real-time plotting
* Data logging
* Event detection
* Parameter configuration

---

### 9.2 UI Layout

#### Top panel

* COM port selection
* Connect / Disconnect
* Start / Stop
* Arm trigger

#### Left panel

* Nominal voltage
* Dip threshold
* Unbalance threshold
* Recording settings

#### Main area

* Graphs

#### Bottom panel

* Live values
* System state

---

## 10. Required Graphs (MVP)

### Graph 1 — Voltages

* Uab
* Ubc
* Uca

---

### Graph 2 — Unbalance

* Unbalance %

---

### Graph 3 — Average Voltage

* Uavg

---

### Graph 4 — State Timeline

* system state over time

---

## 11. Logging Format

### 11.1 CSV

```
ts,uab,ubc,uca,uavg,unb,f,state
```

### 11.2 JSONL

```json
{...}
{...}
```

---

## 12. Operating Modes

Two orthogonal axes, both exposed over UART (see `docs/protocols/firmware-pc.md`):

### 12.1 Measurement mode (`SET MODE …`)

* `delta` — line-to-line Uab / Ubc / Uca (default)
* `wye` — line-to-neutral Va / Vb / Vc
* `cal_ln` — per-phase calibration

### 12.2 Work mode (`SET WMODE …`)

* `idle` — boot default, silent, command-only
* `monitor` — 5 Hz JSON telemetry for live plotting
* `capture` — telemetry suspended; capture pipeline active (pre/post ring buffer, `CAP ARM` / `CAP TRIGGER` / `CAP READ`)

Capture buffer capacity is 500 samples at ~10 ms period, with a runtime-configurable pre/post split via `CAP SET <pre> <post>` (`pre + post ≤ 500`). Default split is 100 / 200 → a 3 s window around the trigger. Triggers: `manual` (PC-initiated via `CAP TRIGGER`) or `dip <V>` (voltage threshold). See the protocol doc for the full FSM.

---

## 13. Firmware Requirements

Arduino firmware shall:

* initialize ADE9000
* read RMS values
* compute derived values
* detect events
* send data via UART
* handle configuration commands

---

## 14. Safety Requirements

* system must ensure safe handling of 400V inputs
* proper isolation and insulation required
* enclosure recommended
* no exposed high-voltage nodes

---

## 15. Limitations (MVP)

Not included in first version:

* current measurement
* power calculation
* harmonic analysis
* waveform streaming
* network connectivity

---

## 16. Risks

### Hardware

* incorrect divider scaling
* noise coupling
* overvoltage risk

### Software

* serial overflow
* GUI lag
* timing inconsistencies

---

## 17. Development Stages

### Stage 1

System architecture and specification

### Stage 2

Firmware development (basic telemetry)

### Stage 3

PC visualization tool

### Stage 4

Event detection and logging

### Stage 5

Advanced analytics

---

## 18. Acceptance Criteria

System is considered functional if:

* voltages are measured correctly
* data is displayed in real time
* events are detected
* logs are saved
* startup behavior is observable

---

## 19. Summary

Minimal viable system:

* measure Uab/Ubc/Uca
* compute Uavg + unbalance
* send data to PC
* display 4 graphs
* record startup events
