#include "mode_manager.h"
#include "ade9000_driver.h"

// ACCMODE base values (without SELFREQ bit 8).
// Delta: VCONSEL=001 (VB = VA−VC), ICONSEL=1 (IB = −IA−IC).
// LN:    VCONSEL=000 (VA, VB, VC direct). Used for WYE and CALIBRATION_LN.
static const uint16_t ACCMODE_BASE_DELTA = 0x0090;
static const uint16_t ACCMODE_BASE_LN    = 0x0000;
static const uint16_t SELFREQ_BIT        = 0x0100;

static MeasurementMode currentMode  = MODE_MEASURE_DELTA;
static MeasurementMode previousMode = MODE_MEASURE_DELTA;

void modeManagerInit()
{
    currentMode  = MODE_MEASURE_DELTA;
    previousMode = MODE_MEASURE_DELTA;
    // ACCMODE already set correctly by ade9000DriverInit() for delta.
}

MeasurementMode modeGet()      { return currentMode; }
MeasurementMode modePrevious() { return previousMode; }

void modeSet(MeasurementMode mode)
{
    if (mode == currentMode) return;
    previousMode = currentMode;
    currentMode  = mode;

    // Preserve the SELFREQ bit so 50/60 Hz auto-detection is not lost.
    uint16_t selfreq = ade9000GetCurrentAccMode() & SELFREQ_BIT;
    uint16_t base    = (mode == MODE_MEASURE_DELTA) ? ACCMODE_BASE_DELTA
                                                     : ACCMODE_BASE_LN;
    ade9000SetAccMode(base | selfreq);
}

const char* modeGetName(MeasurementMode mode)
{
    switch (mode) {
        case MODE_CALIBRATION_LN: return "cal_ln";
        case MODE_MEASURE_DELTA:  return "delta";
        case MODE_MEASURE_WYE:    return "wye";
        default:                  return "unknown";
    }
}
