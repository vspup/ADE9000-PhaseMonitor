#include "capture.h"
#include "../sensors/ade9000/ade9000_driver.h"
#include "../protocol/protocol.h"

static const uint16_t CAP_TOTAL       = 300;
static const uint16_t CAP_PRE         = 100;   // samples before trigger
static const uint16_t CAP_POST        = 200;   // samples including trigger
static const uint32_t CAP_PERIOD_MS   = 10;

struct FastSample
{
  float Uab, Ubc, Uca;   // or Va, Vb, Vc in WYE mode — channel-wise RMSONE
  float Ia,  Ib,  Ic;
};

static FastSample         buf[CAP_TOTAL];
static CaptureState       state          = CAP_IDLE;
static CaptureTriggerType trigType       = CAP_TRIG_NONE;
static float              dipThreshold   = 0.0f;
static bool               manualRequest  = false;

static uint16_t writeIdx   = 0;
static uint16_t triggerIdx = 0;
static uint16_t armedCount = 0;   // samples captured since ARM (caps at CAP_PRE)
static uint16_t postCount  = 0;   // samples captured since trigger (incl. trigger)
static uint32_t lastTickMs = 0;

void captureInit()
{
  state         = CAP_IDLE;
  trigType      = CAP_TRIG_NONE;
  manualRequest = false;
  writeIdx = triggerIdx = armedCount = postCount = 0;
  lastTickMs = 0;
}

static void resetBuffers(uint32_t now)
{
  writeIdx = triggerIdx = armedCount = postCount = 0;
  manualRequest = false;
  lastTickMs = now;
}

bool captureArm(CaptureTriggerType t, float dipV)
{
  if (state != CAP_IDLE) return false;
  trigType     = t;
  dipThreshold = dipV;
  resetBuffers(millis());
  state = CAP_ARMED;
  return true;
}

bool captureManualTrigger()
{
  if (state != CAP_ARMED || trigType != CAP_TRIG_MANUAL) return false;
  manualRequest = true;
  return true;
}

bool captureAbort()
{
  state         = CAP_IDLE;
  trigType      = CAP_TRIG_NONE;
  manualRequest = false;
  return true;
}

CaptureState captureGetState() { return state; }

const char *captureStateName(CaptureState s)
{
  switch (s) {
    case CAP_IDLE:      return "IDLE";
    case CAP_ARMED:     return "ARMED";
    case CAP_TRIGGERED: return "TRIGGERED";
    case CAP_READY:     return "READY";
  }
  return "?";
}

static bool readFastSample(FastSample &s)
{
  float ua, ub, uc, ia, ib, ic;
  if (!ade9000ReadFastRms(ua, ub, uc, ia, ib, ic)) return false;
  s.Uab = ua; s.Ubc = ub; s.Uca = uc;
  s.Ia  = ia; s.Ib  = ib; s.Ic  = ic;
  return true;
}

static bool triggerFires(const FastSample &s)
{
  switch (trigType) {
    case CAP_TRIG_MANUAL: return manualRequest;
    case CAP_TRIG_DIP: {
      float mn = s.Uab;
      if (s.Ubc < mn) mn = s.Ubc;
      if (s.Uca < mn) mn = s.Uca;
      return mn < dipThreshold;
    }
    default: return false;
  }
}

void captureTick(uint32_t now_ms)
{
  if (state != CAP_ARMED && state != CAP_TRIGGERED) return;
  if (now_ms - lastTickMs < CAP_PERIOD_MS) return;
  lastTickMs = now_ms;

  FastSample s;
  if (!readFastSample(s)) return;

  buf[writeIdx] = s;

  if (state == CAP_ARMED)
  {
    if (armedCount < CAP_PRE) armedCount++;

    // Only arm trigger once we have a full pre-roll of samples.
    if (armedCount >= CAP_PRE && triggerFires(s))
    {
      triggerIdx = writeIdx;
      state      = CAP_TRIGGERED;
      postCount  = 1;   // current sample counts as i=0
    }
  }
  else // CAP_TRIGGERED
  {
    postCount++;
    if (postCount >= CAP_POST) state = CAP_READY;
  }

  writeIdx = (writeIdx + 1) % CAP_TOTAL;
}

void captureSendStatus()
{
  uint16_t filled = (state == CAP_ARMED)     ? armedCount
                  : (state == CAP_TRIGGERED) ? (uint16_t)(CAP_PRE + postCount)
                  : (state == CAP_READY)     ? (uint16_t)(CAP_PRE + CAP_POST)
                                             : (uint16_t)0;
  sendCaptureStatus(captureStateName(state), filled, CAP_TOTAL);
}

bool captureStreamRead()
{
  if (state != CAP_READY) {
    sendStatusError("not_ready");
    return false;
  }

  // Samples to emit: i = -CAP_PRE .. -1 (pre), 0 (trigger), 1 .. CAP_POST-1 (post).
  // Position in ring: (triggerIdx + i + CAP_TOTAL) % CAP_TOTAL.
  const int16_t iStart = -(int16_t)CAP_PRE;
  const int16_t iEnd   =  (int16_t)CAP_POST;   // exclusive

  for (int16_t i = iStart; i < iEnd; i++)
  {
    uint16_t pos = (uint16_t)(((int32_t)triggerIdx + i + CAP_TOTAL) % CAP_TOTAL);
    const FastSample &s = buf[pos];
    sendCaptureSample(i,
                      s.Uab, s.Ubc, s.Uca,
                      s.Ia,  s.Ib,  s.Ic);
  }

  sendCaptureDone((uint16_t)(CAP_PRE + CAP_POST));
  state         = CAP_IDLE;
  trigType      = CAP_TRIG_NONE;
  manualRequest = false;
  return true;
}
