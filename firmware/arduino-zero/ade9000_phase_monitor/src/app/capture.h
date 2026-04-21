#ifndef CAPTURE_H
#define CAPTURE_H

#include <Arduino.h>

// Fast-RMS capture pipeline. Active only in WORK_MODE_CAPTURE.
// Samples three voltages + three currents every ~10 ms from ADE9000
// half-cycle RMS registers into a 300-slot ring buffer
// (100 pre-trigger + 200 including trigger + post).
//
// FSM: IDLE → ARMED → TRIGGERED → READY → IDLE
// Triggers: manual (CAP TRIGGER) or voltage dip (min(V) < threshold).

enum CaptureState : uint8_t
{
  CAP_IDLE      = 0,
  CAP_ARMED     = 1,
  CAP_TRIGGERED = 2,
  CAP_READY     = 3
};

enum CaptureTriggerType : uint8_t
{
  CAP_TRIG_NONE   = 0,
  CAP_TRIG_MANUAL = 1,
  CAP_TRIG_DIP    = 2
};

void         captureInit();
void         captureTick(uint32_t now_ms);

// Returns false if state doesn't permit the transition.
bool         captureArm(CaptureTriggerType t, float dipThresholdV);
bool         captureManualTrigger();
bool         captureAbort();

CaptureState captureGetState();
const char  *captureStateName(CaptureState s);

// Emits cap_status event with current state and fill level.
void         captureSendStatus();

// Emits cap_sample * N + cap_done. Only valid in READY state;
// returns false and emits error otherwise. On success → IDLE.
bool         captureStreamRead();

#endif
