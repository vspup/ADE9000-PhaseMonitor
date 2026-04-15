# System Architecture

## 1. Overview

This document describes the high-level architecture of the **ADE9000 Phase Monitor** system.

The system is designed to measure and analyze three-phase line-to-line voltages in a 400V delta configuration during power supply startup.

---

## 2. System Components

The system consists of three main subsystems:

### 2.1 Measurement Hardware

* ADE9000-based measurement board
* Voltage divider network (adapted for 400V delta)
* Arduino Zero (data acquisition controller)

---

### 2.2 Embedded Firmware

Runs on Arduino Zero and is responsible for:

* ADE9000 initialization
* RMS voltage acquisition
* Derived parameter calculation
* Event detection
* Data transmission over UART

---

### 2.3 PC Application

Python-based desktop application responsible for:

* Serial communication
* Real-time visualization
* Data logging (CSV / JSONL)
* Event analysis
* User configuration

---

## 3. Data Flow

```text
3-phase grid (delta 400V)
↓
Voltage dividers
↓
ADE9000
↓
Arduino Zero (firmware)
↓ UART (USB CDC)
PC Application (Python)
↓
Visualization + Logging
```

---

## 4. Functional Decomposition

### 4.1 Firmware Responsibilities

* Acquire RMS voltages (Uab, Ubc, Uca)
* Compute:

  * average voltage (Uavg)
  * voltage unbalance
* Detect events:

  * voltage dip
  * imbalance
  * frequency deviation
* Maintain system state
* Stream JSON data via UART

---

### 4.2 PC Application Responsibilities

* Parse incoming JSON stream
* Display real-time graphs:

  * voltages
  * unbalance
  * system state
* Store measurement data
* Mark events in timeline
* Provide configuration interface

---

## 5. Communication Interface

### 5.1 Transport

* UART over USB (CDC)

### 5.2 Protocol

* JSON Lines (one JSON object per line)

Example:

```json
{
  "ts": 15230,
  "uab": 401.2,
  "ubc": 398.7,
  "uca": 403.1
}
```

---

## 6. System States

```text
IDLE → MONITORING → ARMED → EVENT → RECORDING → COMPLETED
```

---

## 7. Design Principles

* Simple and deterministic firmware
* Stateless data stream (PC reconstructs context)
* Separation of concerns (firmware vs UI)
* Human-readable protocol (JSON)
* Extensibility for future analytics

---

## 8. Future Extensions

* Current measurement
* Power calculation
* Harmonic analysis
* Network interface (TCP/IP)
* Remote monitoring

---
