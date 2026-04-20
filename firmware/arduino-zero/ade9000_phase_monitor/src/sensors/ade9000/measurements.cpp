#include "measurements.h"
#include "ade9000_driver.h"
#include "src/app/calculations.h"
#include "src/app/mode_manager.h"
#include "src/board/config.h"

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

  // Currents are mode-independent: always Ia/Ib/Ic.
  float ia = 0.0f, ib = 0.0f, ic = 0.0f;
  ade9000ReadCurrentRMS(ia, ib, ic);
  snap.Ia   = ia;
  snap.Ib   = ib;
  snap.Ic   = ic;
  snap.Iavg = calcAverage3(ia, ib, ic);
  snap.Iunb = calcUnbalancePct(ia, ib, ic, snap.Iavg);

  snap.freq = 0.0f;
  if (snap.signal_present)
  {
    if (!ade9000ReadFrequency(snap.freq))
      return false;
  }

  return true;
}
