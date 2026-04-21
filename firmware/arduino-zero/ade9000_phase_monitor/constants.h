#ifndef CONSTANTS_H
#define CONSTANTS_H

#include <Arduino.h>

enum SystemState : uint8_t
{
  STATE_IDLE           = 0,
  STATE_MONITORING     = 1,
  STATE_ARMED          = 2,
  STATE_EVENT_DETECTED = 3,
  STATE_RECORDING      = 4,
  STATE_COMPLETED      = 5,
  STATE_FAULT          = 6,
  STATE_CALIBRATION    = 7
};

enum MeasurementMode : uint8_t
{
  MODE_CALIBRATION_LN = 0,   // L-N calibration; ACCMODE VCONSEL=000
  MODE_MEASURE_DELTA  = 1,   // 3-wire delta; ACCMODE VCONSEL=001, VB reconstructed
  MODE_MEASURE_WYE    = 2    // 4-wire star; ACCMODE VCONSEL=000, direct Va/Vb/Vc
};

// Operational (work) mode — orthogonal to MeasurementMode.
// IDLE:    boot default. No telemetry, no capture — firmware only answers
//          commands. Client must explicitly SET WMODE to start work.
// MONITOR: 5 Hz live telemetry stream.
// CAPTURE: fast-RMS ring-buffer capture pipeline.
enum WorkMode : uint8_t
{
  WORK_MODE_IDLE    = 0,
  WORK_MODE_MONITOR = 1,
  WORK_MODE_CAPTURE = 2
};

#endif
