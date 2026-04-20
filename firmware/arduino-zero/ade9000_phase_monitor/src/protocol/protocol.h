#ifndef PROTOCOL_H
#define PROTOCOL_H

#include "types.h"

void sendStatusOk(const char *event, const char *name = nullptr, const char *version = nullptr);
void sendStatusError(const char *reason);
void sendVoltageJson(const VoltageSnapshot &snapshot, const EventFlags &flags);

void sendCalibrationPhase(const char *phase);
void sendCalibrationRms(const char *phase, float vrms);
void sendCalibrationApplied(const char *phase, float gain, int32_t regVal);

#endif
