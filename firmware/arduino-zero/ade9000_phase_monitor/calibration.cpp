#include "calibration.h"
#include "ade9000_driver.h"
#include "config.h"
#include "protocol.h"

#include <FlashStorage.h>

// ---------------------------------------------------------------------------
// Flash storage
// ---------------------------------------------------------------------------

struct CalNvmData {
    uint32_t       magic;
    CalibrationData cal;
};

static const uint32_t CAL_MAGIC = 0xADE99000UL;

FlashStorage(calFlash, CalNvmData);

// ---------------------------------------------------------------------------
// Module state
// ---------------------------------------------------------------------------

static bool      active      = false;
static CalPhase  activePhase = CAL_PHASE_NONE;
static uint16_t  savedDeltaAccMode = 0x0090;   // restored on exit

static CalibrationData currentCal = { 1.0f, 1.0f, 1.0f };

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

static uint16_t lnAccMode()
{
    // Preserve SELFREQ bit from saved delta mode; clear VCONSEL/ICONSEL bits.
    // bits[7:4] = 0 → VCONSEL=000 (L-N, 3-phase 4-wire), ICONSEL=0.
    // bit 8 = SELFREQ (carry over from delta mode).
    return savedDeltaAccMode & 0x0100;   // keep only SELFREQ
}

// CalPhase enum is 1-based (A=1,B=2,C=3); driver functions expect 0-based index.
static inline uint8_t phaseIdx(CalPhase phase) { return (uint8_t)phase - 1; }

static float readAveragedRms(CalPhase phase)
{
    float sum = 0.0f;
    for (uint8_t i = 0; i < CAL_NUM_SAMPLES; i++) {
        sum += (float)ade9000ReadRawRms(phaseIdx(phase)) * ADE9000_VRMS_SCALE;
        delay(CAL_SAMPLE_INTERVAL_MS);
    }
    return sum / (float)CAL_NUM_SAMPLES;
}

static void applyGainToDriver(CalPhase phase, float multiplier)
{
    ade9000WriteVGain(phaseIdx(phase), multiplier);
}

static void applyAllGains()
{
    applyGainToDriver(CAL_PHASE_A, currentCal.vgain_a);
    applyGainToDriver(CAL_PHASE_B, currentCal.vgain_b);
    applyGainToDriver(CAL_PHASE_C, currentCal.vgain_c);
}

static float& gainRef(CalPhase phase)
{
    if (phase == CAL_PHASE_A) return currentCal.vgain_a;
    if (phase == CAL_PHASE_B) return currentCal.vgain_b;
    return currentCal.vgain_c;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

void calibrationInit()
{
    CalNvmData stored = calFlash.read();
    if (stored.magic == CAL_MAGIC) {
        currentCal = stored.cal;
    } else {
        currentCal = { 1.0f, 1.0f, 1.0f };
    }
    // Apply saved (or default) gains to ADE9000.
    // The driver must already be initialised before this is called.
    applyAllGains();
}

bool calibrationIsActive()
{
    return active;
}

void calibrationEnter()
{
    // Remember which delta mode was in use so we can restore it on exit.
    savedDeltaAccMode = ade9000GetCurrentAccMode();

    active      = true;
    activePhase = CAL_PHASE_NONE;

    ade9000.SPI_Write_16(ADDR_ACCMODE, lnAccMode());
    sendStatusOk("cal_started");
}

void calibrationExit()
{
    active      = false;
    activePhase = CAL_PHASE_NONE;

    ade9000.SPI_Write_16(ADDR_ACCMODE, savedDeltaAccMode);
    sendStatusOk("cal_exit");
}

void calibrationSelectPhase(CalPhase phase)
{
    if (!active) {
        sendStatusError("not_in_cal");
        return;
    }
    if (phase == CAL_PHASE_NONE || phase > CAL_PHASE_C) {
        sendStatusError("bad_phase");
        return;
    }
    activePhase = phase;

    // Reset gain register to unity so the next READ gives a raw measurement.
    applyGainToDriver(activePhase, 1.0f);

    // Wait two RMS accumulation cycles before measuring (~400 ms).
    delay(400);

    static const char *names[] = { "", "A", "B", "C" };
    sendCalibrationPhase(names[phase]);
}

void calibrationReadRms()
{
    if (!active) {
        sendStatusError("not_in_cal");
        return;
    }
    if (activePhase == CAL_PHASE_NONE) {
        sendStatusError("no_phase");
        return;
    }

    float vrms = readAveragedRms(activePhase);

    static const char *names[] = { "", "A", "B", "C" };
    sendCalibrationRms(names[activePhase], vrms);
}

void calibrationApplyGain(float vReal)
{
    if (!active) {
        sendStatusError("not_in_cal");
        return;
    }
    if (activePhase == CAL_PHASE_NONE) {
        sendStatusError("no_phase");
        return;
    }
    if (vReal <= 0.0f || vReal > 1000.0f) {
        sendStatusError("bad_vreal");
        return;
    }

    // Measure without any correction (gain was reset to 1.0 in calibrationSelectPhase).
    float vMeasured = readAveragedRms(activePhase);
    if (vMeasured < 1.0f) {
        sendStatusError("no_signal");
        return;
    }

    float newGain = vReal / vMeasured;

    // Reject only clearly invalid values (>4x or <0.25x correction).
    if (newGain < 0.25f || newGain > 4.0f) {
        sendStatusError("gain_out_of_range");
        return;
    }

    gainRef(activePhase) = newGain;
    applyGainToDriver(activePhase, newGain);

    // Convert to register value for reporting.
    int32_t regVal = (int32_t)((newGain - 1.0f) * 134217728.0f);  // (gain-1)*2^27

    static const char *names[] = { "", "A", "B", "C" };
    sendCalibrationApplied(names[activePhase], newGain, regVal);
}

void calibrationSave()
{
    CalNvmData d;
    d.magic = CAL_MAGIC;
    d.cal   = currentCal;
    calFlash.write(d);
    sendStatusOk("cal_saved");
}
