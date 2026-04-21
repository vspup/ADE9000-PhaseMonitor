#ifndef PROTOCOL_H
#define PROTOCOL_H

#include "../../types.h"

void sendStatusOk(const char *event, const char *name = nullptr, const char *version = nullptr);
// When `got` is non-null, the offending token is echoed back as "got":"..."
// to aid debugging of typos via Serial Monitor.
void sendStatusError(const char *reason, const char *got = nullptr);
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

// Capture pipeline events.
void sendCaptureStatus(const char *state, uint16_t filled, uint16_t total);
void sendCaptureSample(int16_t i,
                       float uab, float ubc, float uca,
                       float ia,  float ib,  float ic);
void sendCaptureDone(uint16_t n);

#endif
