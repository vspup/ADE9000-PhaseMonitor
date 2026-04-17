#include "measurements.h"
#include "ade9000_driver.h"
#include "calculations.h"
#include "mode_manager.h"
#include "config.h"

// Minimum voltage (V) on any channel to consider the signal present.
static const float SIGNAL_MIN_V = 50.0f;

bool readVoltageSnapshot(VoltageSnapshot &snap)
{
  snap.ts   = millis();
  snap.mode = modeGet();

  float ch_a, ch_b, ch_c;
  if (!ade9000ReadVoltageRMS(ch_a, ch_b, ch_c))
    return false;

  switch (snap.mode)
  {
    case MODE_MEASURE_DELTA:
      snap.Uab  = ch_a;
      snap.Ubc  = ch_b;
      snap.Uca  = ch_c;
      snap.Uavg = calcAverage3(ch_a, ch_b, ch_c);
      snap.unb  = calcUnbalancePct(ch_a, ch_b, ch_c, snap.Uavg);
      snap.signal_present = isSignalPresent(ch_a, ch_b, ch_c, SIGNAL_MIN_V);
      break;

    case MODE_MEASURE_WYE:
      snap.Va   = ch_a;
      snap.Vb   = ch_b;
      snap.Vc   = ch_c;
      snap.Vavg = calcAverage3(ch_a, ch_b, ch_c);
      snap.unb  = calcUnbalancePct(ch_a, ch_b, ch_c, snap.Vavg);
      snap.signal_present = isSignalPresent(ch_a, ch_b, ch_c, SIGNAL_MIN_V);
      break;

    case MODE_CALIBRATION_LN:
      snap.Va   = ch_a;
      snap.Vb   = ch_b;
      snap.Vc   = ch_c;
      snap.signal_present = isSignalPresent(ch_a, ch_b, ch_c, SIGNAL_MIN_V);
      break;
  }

  snap.freq = 0.0f;
  if (snap.signal_present)
  {
    if (!ade9000ReadFrequency(snap.freq))
      return false;
  }

  return true;
}
