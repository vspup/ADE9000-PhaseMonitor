#ifndef TYPES_H
#define TYPES_H

#include <Arduino.h>
#include "constants.h"

struct VoltageSnapshot
{
  uint32_t ts;

  float Uab;
  float Ubc;
  float Uca;

  float Uavg;
  float unb;
  float freq;

  SystemState state;

  bool signal_present;
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
