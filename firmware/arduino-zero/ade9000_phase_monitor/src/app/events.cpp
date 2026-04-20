#include "events.h"
#include "src/board/config.h"

static const float FREQ_TOLERANCE_HZ = 1.0f;

static float nominalFreqHz = 0.0f;

void eventsSetNominalFreq(float hz)
{
  nominalFreqHz = hz;
}

EventFlags detectEvents(const VoltageSnapshot &snap)
{
  EventFlags flags = {};

  // Dip detection: use the appropriate voltage set for the current mode.
  if (snap.mode == MODE_MEASURE_DELTA)
  {
    flags.dip = (snap.Uab < DEFAULT_DIP_THRESHOLD_V) ||
                (snap.Ubc < DEFAULT_DIP_THRESHOLD_V) ||
                (snap.Uca < DEFAULT_DIP_THRESHOLD_V);
  }
  else if (snap.mode == MODE_MEASURE_WYE)
  {
    // Phase-to-neutral dip threshold is ~57.7% of L-L (400V/√3 ≈ 231V → threshold ≈ 196V).
    // Reuse DEFAULT_DIP_THRESHOLD_V / √3 as a reasonable default.
    const float wyeDipV = DEFAULT_DIP_THRESHOLD_V * 0.5774f;
    flags.dip = (snap.Va < wyeDipV) ||
                (snap.Vb < wyeDipV) ||
                (snap.Vc < wyeDipV);
  }
  // CALIBRATION_LN: no dip detection.

  flags.unbalance = (snap.unb > DEFAULT_UNBALANCE_THRESHOLD_PCT) &&
                    (snap.mode != MODE_CALIBRATION_LN);

  flags.startup = snap.signal_present && (snap.state == STATE_IDLE);

  if (snap.signal_present && nominalFreqHz > 0.0f)
  {
    float dev = snap.freq - nominalFreqHz;
    if (dev < 0.0f) dev = -dev;
    flags.freq_err = (dev > FREQ_TOLERANCE_HZ);
  }

  return flags;
}
