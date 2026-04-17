#ifndef ADE9000_DRIVER_H
#define ADE9000_DRIVER_H

#include <Arduino.h>
#include "ADE9000API.h"
#include "ADE9000RegMap.h"

extern ADE9000Class ade9000;

void     ade9000DriverInit();
uint16_t ade9000ReadRunRegister();
bool     ade9000ReadVoltageRMS(float &uab, float &ubc, float &uca);
bool     ade9000ReadFrequency(float &freqHz);
void     ade9000ApplyFreqMode(float measuredHz);

// Returns the last ACCMODE value written (used by calibration to restore on exit).
uint16_t ade9000GetCurrentAccMode();

// Read raw signed 32-bit AVRMS/BVRMS/CVRMS for a single phase (0=A, 1=B, 2=C).
int32_t  ade9000ReadRawRms(uint8_t phase);

// Write AVGAIN/BVGAIN/CVGAIN for a single phase (0=A, 1=B, 2=C).
// gainMultiplier: 1.0 = unity; register = (gain-1)*2^27, two's-complement 32-bit.
void     ade9000WriteVGain(uint8_t phase, float gainMultiplier);

#endif
