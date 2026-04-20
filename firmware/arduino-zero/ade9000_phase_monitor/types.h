#ifndef TYPES_H
#define TYPES_H

#include <Arduino.h>
#include "constants.h"

struct VoltageSnapshot
{
  uint32_t        ts;
  MeasurementMode mode;

  // Phase-to-neutral (MODE_MEASURE_WYE, MODE_CALIBRATION_LN)
  float Va, Vb, Vc;
  float Vavg;        // mean(Va, Vb, Vc)

  // Line-to-line (MODE_MEASURE_DELTA)
  float Uab, Ubc, Uca;
  float Uavg;        // mean(Uab, Ubc, Uca)

  float unb;         // % unbalance — phase-based in WYE, L-L in DELTA
  float freq;

  // Phase currents (mode-independent: always Ia, Ib, Ic; via Talema AZ-0500 CTs)
  float Ia, Ib, Ic;
  float Iavg;
  float Iunb;        // % current unbalance

  SystemState state;
  bool        signal_present;
};

struct EventFlags
{
  bool dip;
  bool unbalance;
  bool startup;
  bool freq_err;
};

struct SystemStatus
{
  SystemState state;
  bool ade_ready;
  bool comm_ok;
};

#endif
