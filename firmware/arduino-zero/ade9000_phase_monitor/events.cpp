#include "events.h"
#include "config.h"

// Allowed deviation from nominal frequency (±1 Hz covers both 50Hz and 60Hz grids).
static const float FREQ_TOLERANCE_HZ = 1.0f;

// Nominal frequency is set once after auto-detection (see app.cpp / ade9000ApplyFreqMode).
// Default covers both grids: 0 means "not yet detected" → freq_err suppressed.
static float nominalFreqHz = 0.0f;

void eventsSetNominalFreq(float hz)
{
  nominalFreqHz = hz;
}

EventFlags detectEvents(const VoltageSnapshot &snapshot)
{
  EventFlags flags = {};

  // Dip: any line-to-line voltage below threshold
  flags.dip = (snapshot.Uab < DEFAULT_DIP_THRESHOLD_V) ||
              (snapshot.Ubc < DEFAULT_DIP_THRESHOLD_V) ||
              (snapshot.Uca < DEFAULT_DIP_THRESHOLD_V);

  // Unbalance: exceeds configured percent threshold
  flags.unbalance = (snapshot.unb > DEFAULT_UNBALANCE_THRESHOLD_PCT);

  // Startup window: signal appeared recently (signal_present but state still IDLE)
  flags.startup = snapshot.signal_present && (snapshot.state == STATE_IDLE);

  // Frequency deviation: only checked after nominal is established
  if (snapshot.signal_present && nominalFreqHz > 0.0f)
  {
    float dev = snapshot.freq - nominalFreqHz;
    if (dev < 0.0f) dev = -dev;
    flags.freq_err = (dev > FREQ_TOLERANCE_HZ);
  }

  return flags;
}
