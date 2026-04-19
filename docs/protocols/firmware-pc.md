# Firmware ↔ PC Protocol

**Transport:** USB CDC (virtual COM port), 115200 baud, 8N1
**Format:** JSON Lines — one JSON object per line, terminated with `\n`
**Update rate:** 5 Hz (every 200 ms)

---

## Packet format

```json
{
  "ts":    1023450,
  "uab":   228.4,
  "ubc":   229.1,
  "uca":   227.8,
  "uavg":  228.4,
  "unb":   0.57,
  "f":     50.02,
  "state": 1,
  "flags": ["DIP"]
}
```

## Fields

| Field   | Type     | Unit | Description                              |
|---------|----------|------|------------------------------------------|
| `ts`    | uint32   | ms   | Arduino `millis()` timestamp             |
| `uab`   | float    | V    | Line-to-line voltage Uab (RMS)           |
| `ubc`   | float    | V    | Line-to-line voltage Ubc (RMS)           |
| `uca`   | float    | V    | Line-to-line voltage Uca (RMS)           |
| `uavg`  | float    | V    | Average voltage (Uab+Ubc+Uca)/3          |
| `unb`   | float    | %    | Voltage unbalance: max(|Ui−Uavg|)/Uavg×100 |
| `f`     | float    | Hz   | Grid frequency (0 if not yet detected)   |
| `state` | uint8    | —    | State machine state (see below)          |
| `flags` | []string | —    | Active event flags (see below)           |

## State machine

| Value | Name             | Description                        |
|-------|------------------|------------------------------------|
| 0     | `IDLE`           | Waiting for grid signal            |
| 1     | `MONITORING`     | Normal measurement                 |
| 2     | `ARMED`          | Ready to capture event             |
| 3     | `EVENT_DETECTED` | Threshold crossed                  |
| 4     | `RECORDING`      | Capturing event data               |
| 5     | `COMPLETED`      | Recording done                     |
| 6     | `FAULT`          | Hardware or communication fault    |

## Event flags

| Flag    | Trigger condition                        |
|---------|------------------------------------------|
| `DIP`   | Any voltage < threshold (default 340 V)  |
| `UNB`   | Unbalance > threshold (default 10%)      |
| `FREQ`  | Frequency deviation > 1 Hz from nominal |
| `START` | Rapid voltage ramp-up detected           |

## PC-side parsing

`packet_parser.py` — `parse_packet(line: str) -> Optional[Packet]`

- Returns `None` on any JSON or field error (malformed lines are silently dropped)
- Minimum required field: `uab` (packets without it are treated as status messages)
- `flags` defaults to `[]` if absent
