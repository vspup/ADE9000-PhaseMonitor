#ifndef MODE_MANAGER_H
#define MODE_MANAGER_H

#include <Arduino.h>
#include "constants.h"

// Initialise with default mode (MEASURE_DELTA). Call after ade9000DriverInit().
void            modeManagerInit();

MeasurementMode modeGet();
MeasurementMode modePrevious();   // mode before the last modeSet() call

// Switch to a new mode: updates ACCMODE register, preserves SELFREQ (50/60 Hz) bit.
void            modeSet(MeasurementMode mode);

// Human-readable name for JSON "mode" field.
const char*     modeGetName(MeasurementMode mode);

#endif
