#include "measurements.h"
#include "ade9000_driver.h"
#include "calculations.h"
#include "config.h"

// Minimum L-L voltage (V) to consider signal present.
// 50V is ~12.5% of 400V nominal — filters noise, passes any live signal.
static const float SIGNAL_MIN_V = 50.0f;

bool readVoltageSnapshot(VoltageSnapshot &snapshot)
{
  snapshot.ts = millis();

  if (!ade9000ReadVoltageRMS(snapshot.Uab, snapshot.Ubc, snapshot.Uca))
    return false;

  snapshot.Uavg = calcAverage3(snapshot.Uab, snapshot.Ubc, snapshot.Uca);
  snapshot.unb  = calcUnbalancePct(snapshot.Uab, snapshot.Ubc, snapshot.Uca, snapshot.Uavg);
  snapshot.signal_present = isSignalPresent(snapshot.Uab, snapshot.Ubc, snapshot.Uca, SIGNAL_MIN_V);

  // Read frequency only when signal is present; otherwise report 0
  if (snapshot.signal_present)
  {
    if (!ade9000ReadFrequency(snapshot.freq))
      return false;
  }
  else
  {
    snapshot.freq = 0.0f;
  }

  return true;
}
