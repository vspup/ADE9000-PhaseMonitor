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
void     ade9000ApplyFreqMode(float measuredHz);  // call once after signal detected

#endif
