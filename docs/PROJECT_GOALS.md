# 📄 PROJECT_GOALS.md

## ADE9000 Phase Monitor — Final Target Definition

---

## 1. Purpose

Develop a **hardware-software system** for real-time monitoring and analysis of **three-phase line-to-line voltages** in a **delta (3P3W) system** during power supply startup and operation.

The system is intended for **engineering diagnostics**, including:

* voltage dips
* phase imbalance (unbalance)
* transient behavior during startup
* abnormal grid conditions

---

## 2. Core Objective

The system shall:

👉 Measure, process, and visualize **line-to-line voltages**:

* `Uab`
* `Ubc`
* `Uca`

in real time, with:

* derived parameter calculation
* event detection
* logging and visualization on PC

---

## 3. Electrical Model

### 3.1 System Type

* 3-phase
* 3-wire (delta)
* no neutral

---

### 3.2 Nominal Voltage

* Typical: **400 V (line-to-line)**

---

### 3.3 Frequency Support

The system **must support both grid standards**:

* **50 Hz**
* **60 Hz**

👉 Requirements:

* automatic handling of both frequencies
* no manual reconfiguration required
* correct RMS and event detection in both modes

---

## 4. Measurement Requirements

The firmware shall continuously acquire:

| Parameter | Description                   |
| --------- | ----------------------------- |
| Uab       | Voltage between phase A and B |
| Ubc       | Voltage between phase B and C |
| Uca       | Voltage between phase C and A |

---

## 5. Derived Parameters

The system shall compute:

### 5.1 Average Voltage

```text
Uavg = (Uab + Ubc + Uca) / 3
```

---

### 5.2 Voltage Unbalance

```text
Unbalance = max(|Ui - Uavg|) / Uavg * 100%
```

---

### 5.3 Frequency

* measured from ADE9000
* must work correctly for both:

  * 50 Hz
  * 60 Hz

---

## 6. Event Detection

The system shall detect the following events:

### 6.1 Voltage Dip

Condition:

```text
any(U < threshold)
```

---

### 6.2 Voltage Unbalance

Condition:

```text
Unbalance > threshold
```

---

### 6.3 Frequency Deviation

Condition:

```text
|f - nominal| > threshold
```

Where nominal is automatically determined (50 or 60 Hz).

---

### 6.4 Startup Detection

System shall detect:

* rapid voltage ramp-up
* transient imbalance during startup

---

## 7. Firmware Responsibilities

Firmware (Arduino Zero) shall:

* initialize ADE9000
* acquire RMS voltage values
* compute derived parameters
* detect events
* maintain system state
* transmit telemetry over UART (USB CDC)

---

## 8. Data Interface (UART Protocol)

### 8.1 Transport

* UART over USB (CDC)

---

### 8.2 Format

* JSON Lines (one JSON object per line)

---

### 8.3 Final Data Packet Format

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

### 8.4 Field Definitions

| Field | Description     |
| ----- | --------------- |
| ts    | timestamp (ms)  |
| uab   | line voltage AB |
| ubc   | line voltage BC |
| uca   | line voltage CA |
| uavg  | average voltage |
| unb   | unbalance (%)   |
| f     | frequency (Hz)  |
| state | system state    |
| flags | detected events |

---

## 9. System States

```text
IDLE → MONITORING → ARMED → EVENT_DETECTED → RECORDING → COMPLETED
```

---

## 10. PC Application Responsibilities

The PC application shall:

* connect via serial port
* parse JSON stream
* display real-time graphs
* log data (CSV / JSONL)
* visualize events on timeline
* allow configuration (thresholds, modes)

---

## 11. Required Graphs (MVP)

### Graph 1 — Voltages

* Uab
* Ubc
* Uca

### Graph 2 — Unbalance

* Unbalance %

### Graph 3 — Average Voltage

* Uavg

### Graph 4 — Frequency

* frequency (Hz)

### Graph 5 — State Timeline

* system state over time

---

## 12. Performance Requirements

| Parameter   | Requirement |
| ----------- | ----------- |
| Update rate | 5–10 Hz     |
| Latency     | < 500 ms    |
| Operation   | continuous  |

---

## 13. Logging Requirements

### CSV

```text
ts,uab,ubc,uca,uavg,unb,f,state
```

### JSONL

```json
{...}
{...}
```

---

## 14. Safety Requirements

* measurement front-end must be rated for 400V
* proper isolation required
* no exposed high-voltage nodes
* safe enclosure required

---

## 15. Acceptance Criteria

The system is considered complete if:

* voltages Uab/Ubc/Uca are measured correctly
* data is streamed in real time
* system works for both 50 Hz and 60 Hz grids
* unbalance is computed correctly
* frequency is measured correctly
* events are detected reliably
* PC application displays and logs data
* startup behavior is clearly observable

---

## 16. Final System Definition

The final system is:

👉 A real-time three-phase delta voltage monitoring instrument
capable of analyzing startup behavior and grid anomalies
with full support for **both 50 Hz and 60 Hz networks**

---

## 17. Hardware Modification (Voltage Divider Adaptation)

### 17.1 Background

The standard ADE9000 Evaluation Board (EV-ADE9000SHIELDZ) is designed for lower voltage measurement ranges and **is not directly suitable for 400V line-to-line delta systems**.

### 17.2 Modification Strategy

Replace high-side resistors only. Lower resistor network unchanged.

### 17.3 Target Configuration

Replace each 200 kΩ high-side resistor with **332 kΩ, 2010, thick film, 0.5%, 100 ppm/°C**.

### 17.4 Electrical Impact

* Increases total divider resistance
* Allows safe measurement of ~400V line-to-line
* Does not affect lower resistor or ADE9000 input scaling architecture

### 17.5 Calibration Requirement

After modification: apply known reference voltage, measure raw RMS, compute:

```text
K = U_real / U_measured
```

Apply correction in firmware (`ADE9000_VRMS_SCALE`).

### 17.6 Status

Hardware modification deferred. Current testing uses original EV-ADE9000SHIELDZ without phase connections.
