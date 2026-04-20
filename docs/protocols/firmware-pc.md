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
{"status":"ok","event":"pong"}
{"status":"ok","event":"cal_started"}
{"status":"ok","event":"cal_phase","phase":"A"}
{"status":"ok","event":"cal_rms","phase":"A","vrms":62.394}
{"status":"ok","event":"cal_applied","phase":"A","gain":1.698912,"reg":93847231}
{"status":"ok","event":"cal_saved"}
{"status":"ok","event":"cal_exit"}
{"status":"error","reason":"<reason>"}
```

Error reasons: `unknown_cmd`, `cmd_overflow`, `not_in_cal`, `no_phase`, `no_signal`,
`gain_out_of_range`, `bad_vreal`, `bad_phase`, `bad_mode`, `bad_wmode`, `read_failed`, `save_failed`.

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
| `monitor` | 5 Hz packets as specified above | Live monitoring (current PC app) |
| `capture` | Suspended — commands still processed | Reserved for startup-capture app |

**Default at boot:** `monitor`. PC apps must still send an explicit
`SET WMODE` on connect rather than relying on the default.

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
