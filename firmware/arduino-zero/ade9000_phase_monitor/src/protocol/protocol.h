#ifndef PROTOCOL_H
#define PROTOCOL_H

#include "../../types.h"

void sendStatusOk(const char *event, const char *name = nullptr, const char *version = nullptr);
void sendStatusError(const char *reason);
void sendVoltageJson(const VoltageSnapshot &snapshot, const EventFlags &flags);

// Work-mode acknowledgement. `wmode` must be "monitor" or "capture".
void sendWorkModeOk(const char *wmode);

// Consolidated status snapshot (response to GET STATUS).
// Contains everything a client app needs after connect: operational mode,
// measurement mode, calibration flag, and whether telemetry is streaming.
void sendStatusSnapshot(const char *wmode, const char *mmode,
                        bool calibrating, bool streaming);

void sendCalibrationPhase(const char *phase);
void sendCalibrationRms(const char *phase, float vrms);
void sendCalibrationApplied(const char *phase, float gain, int32_t regVal);

#endif
