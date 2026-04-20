#ifndef CALIBRATION_H
#define CALIBRATION_H

#include <Arduino.h>

// Per-phase voltage gain multipliers (1.0 = no correction).
// Persisted in flash; applied to AVGAIN/BVGAIN/CVGAIN at every boot.
struct CalibrationData {
    float vgain_a;
    float vgain_b;
    float vgain_c;
};

enum CalPhase : uint8_t {
    CAL_PHASE_NONE = 0,
    CAL_PHASE_A    = 1,
    CAL_PHASE_B    = 2,
    CAL_PHASE_C    = 3,
};

// Load persisted gains from flash and apply to ADE9000 on boot.
void calibrationInit();

// True while calibration mode is active — suppresses normal data output.
bool calibrationIsActive();

// Enter L-N measurement mode; stop regular monitoring output.
void calibrationEnter();

// Restore delta ACCMODE and resume normal monitoring.
void calibrationExit();

// Select the active phase; resets its gain register to unity for raw measurement.
void calibrationSelectPhase(CalPhase phase);

// Average CAL_NUM_SAMPLES RMS readings (~1 s), then report result to host.
// Blocks for ~1 s — acceptable during interactive calibration.
void calibrationReadRms();

// Compute and write gain register for the active phase using voltmeter reference.
void calibrationApplyGain(float vReal);

// Save current CalibrationData (all three phases) to flash.
void calibrationSave();

#endif
