#include "app.h"
#include "config.h"
#include "types.h"
#include "ade9000_driver.h"
#include "measurements.h"
#include "calculations.h"
#include "events.h"
#include "protocol.h"
#include "state_machine.h"
#include "commands.h"
#include "calibration.h"
#include "mode_manager.h"

static uint32_t lastSendMs   = 0;
static bool     freqDetected = false;

// Accumulate frequency readings to filter out noise before locking the mode.
static float  freqAccum    = 0.0f;
static uint8_t freqSamples = 0;
static const uint8_t FREQ_DETECT_SAMPLES = 5;  // ~1 second at 200ms period

void appSetup()
{
  Serial.begin(UART_BAUDRATE);
  while (!Serial) {}

  ade9000DriverInit();
  modeManagerInit();     // default: MEASURE_DELTA
  calibrationInit();     // load saved gains and apply to ADE9000
  stateMachineInit();
  commandsInit();

  sendStatusOk("boot", FW_NAME, FW_VERSION);
}

void appLoop()
{
  commandsProcess();

  // During calibration the main data loop is suspended — commands still processed above.
  if (calibrationIsActive()) return;

  uint32_t now = millis();
  if (now - lastSendMs >= SEND_PERIOD_MS)
  {
    lastSendMs = now;

    VoltageSnapshot snap;
    if (readVoltageSnapshot(snap))
    {
      // Auto-detect grid frequency once, after signal is present
      if (!freqDetected && snap.signal_present && snap.freq > 40.0f && snap.freq < 70.0f)
      {
        freqAccum += snap.freq;
        freqSamples++;

        if (freqSamples >= FREQ_DETECT_SAMPLES)
        {
          float nominalFreq = freqAccum / (float)freqSamples;
          ade9000ApplyFreqMode(nominalFreq);
          eventsSetNominalFreq(nominalFreq);
          freqDetected = true;
          sendStatusOk("freq_locked");
        }
      }

      EventFlags flags = detectEvents(snap);
      stateMachineUpdate(snap, flags);
      snap.state = stateMachineGetState();
      sendVoltageJson(snap, flags);
    }
    else
    {
      sendStatusError("read_failed");
    }
  }
}
