# Firmware ↔ PC Protocol

**Transport:** USB CDC (virtual COM port), 115200 baud, 8N1  
**Format:** JSON Lines — one JSON object per line, terminated with `\n`  
**Update rate:** 5 Hz (every 200 ms)

---

## Firmware → PC: Telemetry Packet

Fields vary by active measurement mode. The PC parser uses presence of `ts` to identify
telemetry packets (status/command responses never contain `ts`).

### Common fields (all modes)

| Field | Type | Unit | Description |
|---|---|---|---|
| `ts` | uint32 | ms | `millis()` timestamp |
| `mode` | string | — | `"delta"`, `"wye"`, or `"cal_ln"` |
| `f` | float | Hz | Grid frequency (0.0 if signal absent) |
| `state` | uint8 | — | State machine state (see below) |
| `flags` | []string | — | Active event flags (see below) |

### Phase-current fields (MEASURE_DELTA and MEASURE_WYE)

Emitted in both measurement modes — currents are mode-independent (always
Ia/Ib/Ic from the three CT channels; Talema AZ-0500 on this board).

| Field | Type | Unit | Description |
|---|---|---|---|
| `ia` | float | A | Phase A current (RMS) |
| `ib` | float | A | Phase B current (RMS) |
| `ic` | float | A | Phase C current (RMS) |
| `iavg` | float | A | Average: (Ia+Ib+Ic)/3 |
| `iunb` | float | % | Current unbalance |

### MEASURE_DELTA

```json
{"ts":15230,"mode":"delta","uab":401.20,"ubc":398.70,"uca":403.10,"uavg":401.00,"unb":0.86,"ia":1.234,"ib":1.251,"ic":1.220,"iavg":1.235,"iunb":1.29,"f":50.01,"state":1,"flags":[]}
```

| Field | Type | Unit | Description |
|---|---|---|---|
| `uab` | float | V | Line voltage A-B (RMS) |
| `ubc` | float | V | Line voltage B-C (RMS) |
| `uca` | float | V | Line voltage C-A (RMS) |
| `uavg` | float | V | Average: (Uab+Ubc+Uca)/3 |
| `unb` | float | % | Unbalance: max(\|Ui−Uavg\|)/Uavg×100 |

### MEASURE_WYE

```json
{"ts":15430,"mode":"wye","va":231.50,"vb":229.80,"vc":230.60,"vavg":230.63,"unb":0.37,"ia":1.234,"ib":1.251,"ic":1.220,"iavg":1.235,"iunb":1.29,"f":50.01,"state":1,"flags":[]}
```

| Field | Type | Unit | Description |
|---|---|---|---|
| `va` | float | V | Phase-to-neutral voltage A (RMS) |
| `vb` | float | V | Phase-to-neutral voltage B (RMS) |
| `vc` | float | V | Phase-to-neutral voltage C (RMS) |
| `vavg` | float | V | Average: (Va+Vb+Vc)/3 |
| `unb` | float | % | Unbalance |

### CALIBRATION_LN

```json
{"ts":16000,"mode":"cal_ln","va":62.40,"vb":0.00,"vc":0.00,"f":50.01,"state":1,"flags":[]}
```

| Field | Type | Unit | Description |
|---|---|---|---|
| `va/vb/vc` | float | V | Raw pre-correction RMS per phase |

No `vavg`, no `unb` — calibration mode only.

---

## Firmware → PC: Status Responses

No `ts` field — PC parser uses this to skip them as non-telemetry.

```json
{"status":"ok","event":"boot","fw":"ADE9000 Phase Monitor","ver":"1.0"}
{"status":"ok","event":"cal_loaded"}     // gains restored from NVM
{"status":"ok","event":"cal_defaulted"}  // NVM empty or magic mismatch — using {1,1,1}
{"status":"ok","event":"freq_locked"}
{"status":"ok","event":"mode_set"}
{"status":"ok","event":"wmode","wmode":"monitor"}   // or "capture"
{"status":"ok","event":"status","wmode":"monitor","mmode":"delta","cal":false,"streaming":true}
{"status":"ok","event":"pong"}
{"status":"ok","event":"cal_started"}
{"status":"ok","event":"cal_phase","phase":"A"}
{"status":"ok","event":"cal_rms","phase":"A","vrms":62.394}
{"status":"ok","event":"cal_applied","phase":"A","gain":1.698912,"reg":93847231}
{"status":"ok","event":"cal_saved"}
{"status":"ok","event":"cal_exit"}
{"status":"error","reason":"<reason>"}
{"status":"error","reason":"bad_wmode","got":"Capture"}   // offending token echoed
```

Error reasons: `unknown_cmd`, `cmd_overflow`, `not_in_cal`, `no_phase`, `no_signal`,
`gain_out_of_range`, `bad_vreal`, `bad_phase`, `bad_mode`, `bad_wmode`, `read_failed`, `save_failed`,
`not_in_capture_mode`, `cap_busy`, `not_armed`, `not_ready`, `bad_trigger`, `missing_threshold`, `bad_split`, `unknown_cap_cmd`.

---

## PC → Firmware: Commands

ASCII text, newline-terminated (`\n`).

| Command | Description |
|---|---|
| `PING` | Connectivity check → `pong` |
| `SET MODE delta` | Switch to MEASURE_DELTA |
| `SET MODE wye` | Switch to MEASURE_WYE |
| `SET WMODE monitor` | Enter live monitoring work mode (→ `wmode` ack) |
| `SET WMODE capture` | Enter capture work mode — live stream suspended (→ `wmode` ack) |
| `GET WMODE` | Report current work mode (→ `wmode` ack) |
| `GET STATUS` | Consolidated snapshot: wmode, mmode, cal, streaming (→ `status` event) |
| `CAP SET <pre> <post>` | Configure pre/post sample split (IDLE state only, `pre+post ≤ 500`) |
| `CAP ARM manual` | Arm capture buffer, wait for manual trigger (CAPTURE mode only) |
| `CAP ARM dip <V>` | Arm capture, auto-trigger when min(V_L-L) < threshold volts |
| `CAP TRIGGER` | Manual trigger (only valid after `CAP ARM manual`) |
| `CAP STATUS` | Capture FSM snapshot (→ `cap_status`) |
| `CAP READ` | Stream captured samples: `cap_sample` × N + `cap_done` |
| `CAP ABORT` | Abort capture, return FSM to IDLE |
| `CAL START` | Enter calibration (suspends telemetry loop) |
| `CAL PHASE A\|B\|C` | Select phase, reset its gain to 1.0 |
| `CAL READ` | Read averaged raw RMS for selected phase |
| `CAL APPLY <v>` | Compute and apply gain: `v / measured` |
| `CAL SAVE` | Persist gains to NVM (FlashStorage) |
| `CAL EXIT` | Exit calibration, restore previous mode |

### Calibration sequence

```
→ CAL START
← {"status":"ok","event":"cal_started"}
→ CAL PHASE A
← {"status":"ok","event":"cal_phase","phase":"A"}
→ CAL READ
← {"status":"ok","event":"cal_rms","phase":"A","vrms":62.394}
→ CAL APPLY 106.0
← {"status":"ok","event":"cal_applied","phase":"A","gain":1.698912,"reg":93847231}
→ CAL SAVE
← {"status":"ok","event":"cal_saved"}
→ CAL EXIT
← {"status":"ok","event":"cal_exit"}
```

---

## Work mode (orthogonal to measurement mode)

Two operational modes, tracked independently of `SET MODE delta|wye`:

| Mode | Telemetry stream | Purpose |
|---|---|---|
| `idle` | None | Boot default — firmware only answers commands |
| `monitor` | 5 Hz packets as specified above | Live monitoring (MONITOR GUI) |
| `capture` | Suspended — capture pipeline active | Startup-capture app |

**Default at boot:** `idle`. Nothing streams until the client explicitly
sends `SET WMODE monitor` or `SET WMODE capture`. This keeps Serial Monitor
quiet during manual debugging; PC apps unchanged since they always send
an explicit `SET WMODE` on connect.

### Connect handshake (MONITOR app)

```
→ SET WMODE monitor
← {"status":"ok","event":"wmode","wmode":"monitor"}
→ SET MODE delta          (or wye)
← {"status":"ok","event":"mode_set"}
```

The GUI MUST:
- send `SET WMODE monitor` immediately after opening the port;
- **ignore all telemetry packets** until the `wmode` ack arrives;
- abort the connection with a visible error if no ack within 2 s,
  or if the ack reports a different mode.

Future capture app will mirror this handshake with `SET WMODE capture`.

---

## Capture pipeline (WORK_MODE_CAPTURE only)

Fast-RMS ring buffer for post-mortem waveform inspection around events.
All `CAP …` commands return `{"status":"error","reason":"not_in_capture_mode"}`
when issued in MONITOR mode.

**Sampling:** 10 ms period via ADE9000 half-cycle RMS registers (`xVRMSONE`,
`xIRMSONE`). **Buffer:** 500 samples total, with a runtime-configurable
split between pre-trigger and post-trigger (`CAP SET`). Default is
100 / 200 (1.0 s before, 2.0 s after). Sum must satisfy `pre + post ≤ 500`.

### FSM

| State | Meaning |
|---|---|
| `IDLE` | No capture in progress |
| `ARMED` | Ring buffer filling, waiting for trigger condition |
| `TRIGGERED` | Trigger fired, still collecting post-trigger samples |
| `READY` | Capture complete, awaiting `CAP READ` or `CAP ABORT` |

Transitions: `IDLE → ARMED` (via `CAP ARM …`) → `TRIGGERED` (trigger fires
and ≥100 pre-roll samples accumulated) → `READY` (after 150 post-trigger
samples) → `IDLE` (after `CAP READ` or `CAP ABORT`).

### Triggers

- `manual` — fires on `CAP TRIGGER` command.
- `dip <V>` — fires when `min(uab, ubc, uca) < V` (or the wye equivalent,
  depending on current measurement mode). Threshold in volts.

### Responses

```json
{"status":"ok","event":"cap_status","state":"ARMED","filled":47,"pre":100,"post":200,"total":500}
{"status":"ok","event":"cap_triggered"}
{"status":"ok","event":"cap_aborted"}
{"event":"cap_sample","i":-100,"uab":401.2,"ubc":398.7,"uca":403.1,"ia":1.234,"ib":1.251,"ic":1.220}
{"status":"ok","event":"cap_done","n":300}
```

`cap_sample` has no `status` field and no `ts` — it's a streaming data row,
keyed by `event` and `i` (sample index: `-100..199`, `0` = trigger moment).

Error reasons specific to capture: `not_in_capture_mode`, `cap_busy`,
`not_armed`, `not_ready`, `bad_trigger`, `missing_threshold`,
`bad_split`, `unknown_cap_cmd`.

### Example flow

```
→ SET WMODE capture
← {"status":"ok","event":"wmode","wmode":"capture"}
→ CAP ARM dip 340
← {"status":"ok","event":"cap_status","state":"ARMED","filled":0,"total":500}
   (wait for dip, or poll CAP STATUS)
→ CAP STATUS
← {"status":"ok","event":"cap_status","state":"READY","filled":300,"total":500}
→ CAP READ
← {"event":"cap_sample","i":-100,…}
  …300 rows…
← {"status":"ok","event":"cap_done","n":300}
```

---

## State machine

| Value | Name | Description |
|---|---|---|
| 0 | IDLE | No signal |
| 1 | MONITORING | Normal measurement |
| 2 | ARMED | Ready to capture event |
| 3 | EVENT_DETECTED | Threshold crossed |
| 4 | RECORDING | Capturing post-event data |
| 5 | COMPLETED | Recording done |

## Event flags

| Flag | Trigger |
|---|---|
| `dip` | Any voltage < threshold (default 340 V delta / ~196 V wye) |
| `unb` | Unbalance > threshold (default 10%) |
| `freq_err` | Frequency deviation > 1 Hz from detected nominal |
| `startup` | Signal appears while state = IDLE |
