#include "capture.h"
#include "../sensors/ade9000/ade9000_driver.h"
#include "../protocol/protocol.h"

static const uint16_t CAP_TOTAL        = 500;
static const uint16_t CAP_PRE_DEFAULT  = 100;
static const uint16_t CAP_POST_DEFAULT = 200;
static const uint32_t CAP_PERIOD_MS    = 10;

static uint16_t capPre  = CAP_PRE_DEFAULT;
static uint16_t capPost = CAP_POST_DEFAULT;

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

static uint16_t writeIdx       = 0;
static uint16_t triggerIdx     = 0;
static uint16_t armedCount     = 0;   // samples captured since ARM (caps at capPre)
static uint16_t postCount      = 0;   // samples captured since trigger (incl. trigger)
static uint32_t lastTickMs     = 0;
static uint32_t triggerTickMs  = 0;   // millis() at moment of trigger firing

void captureInit()
{
  state         = CAP_IDLE;
  trigType      = CAP_TRIG_NONE;
  manualRequest = false;
  writeIdx = triggerIdx = armedCount = postCount = 0;
  lastTickMs = 0;
  triggerTickMs = 0;
}

static void resetBuffers(uint32_t now)
{
  writeIdx = triggerIdx = armedCount = postCount = 0;
  manualRequest = false;
  lastTickMs = now;
  triggerTickMs = 0;
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

bool captureConfigure(uint16_t pre, uint16_t post)
{
  if (state != CAP_IDLE)              return false;
  if (pre == 0 || post == 0)          return false;
  if ((uint32_t)pre + post > CAP_TOTAL) return false;
  capPre  = pre;
  capPost = post;
  return true;
}

uint16_t captureGetPre()          { return capPre; }
uint16_t captureGetPost()         { return capPost; }
uint16_t captureGetTotal()        { return CAP_TOTAL; }
uint16_t captureGetPeriodMs()     { return (uint16_t)CAP_PERIOD_MS; }
uint32_t captureGetTriggerTick()  { return triggerTickMs; }

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
    if (armedCount < capPre) armedCount++;

    // Only arm trigger once we have a full pre-roll of samples.
    if (armedCount >= capPre && triggerFires(s))
    {
      triggerIdx    = writeIdx;
      triggerTickMs = now_ms;
      state         = CAP_TRIGGERED;
      postCount     = 1;   // current sample counts as i=0
    }
  }
  else // CAP_TRIGGERED
  {
    postCount++;
    if (postCount >= capPost) state = CAP_READY;
  }

  writeIdx = (writeIdx + 1) % CAP_TOTAL;
}

void captureSendStatus()
{
  uint16_t filled = (state == CAP_ARMED)     ? armedCount
                  : (state == CAP_TRIGGERED) ? (uint16_t)(capPre + postCount)
                  : (state == CAP_READY)     ? (uint16_t)(capPre + capPost)
                                             : (uint16_t)0;
  sendCaptureStatus(captureStateName(state), filled, capPre, capPost, CAP_TOTAL, millis());
}

bool captureStreamRead()
{
  if (state != CAP_READY) {
    sendStatusError("not_ready");
    return false;
  }

  // Samples to emit: i = -capPre .. -1 (pre), 0 (trigger), 1 .. capPost-1 (post).
  // Position in ring: (triggerIdx + i + CAP_TOTAL) % CAP_TOTAL.
  const int16_t iStart = -(int16_t)capPre;
  const int16_t iEnd   =  (int16_t)capPost;   // exclusive

  for (int16_t i = iStart; i < iEnd; i++)
  {
    uint16_t pos = (uint16_t)(((int32_t)triggerIdx + i + CAP_TOTAL) % CAP_TOTAL);
    const FastSample &s = buf[pos];
    sendCaptureSample(i,
                      s.Uab, s.Ubc, s.Uca,
                      s.Ia,  s.Ib,  s.Ic);
  }

  sendCaptureDone((uint16_t)(capPre + capPost),
                  triggerTickMs, (uint16_t)CAP_PERIOD_MS,
                  capPre, capPost, (int16_t)0);
  state         = CAP_IDLE;
  trigType      = CAP_TRIG_NONE;
  manualRequest = false;
  return true;
}
