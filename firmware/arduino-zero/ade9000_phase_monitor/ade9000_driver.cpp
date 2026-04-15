#include "ade9000_driver.h"
#include "pins.h"
#include "config.h"

// Channel mapping (EV-ADE9000SHIELDZ, 3P3W delta, Phase B as reference):
//   ADE9000 channel A (AVRMS) → uab  (VA input = Phase A vs. Phase B ref)
//   ADE9000 channel B (BVRMS) → ubc  (VB reconstructed: VA − VC, see ACCMODE)
//   ADE9000 channel C (CVRMS) → uca  (VC input = Phase C vs. Phase B ref)
//
// ACCMODE delta base = 0x0090:
//   bits[7:4] = 0x9 → VCONSEL=001 (VB=VA−VC), ICONSEL=1 (IB=−IA−IC)
//   bit 8 (SELFREQ): set to 1 for 50Hz, leave 0 for 60Hz.
//   Auto-selected after first frequency measurement (ade9000ApplyFreqMode).

ADE9000Class ade9000;

// Applied at init: 60Hz delta. Re-applied to 50Hz if detected (see ade9000ApplyFreqMode).
static const uint16_t ACCMODE_DELTA_60HZ = 0x0090;
static const uint16_t ACCMODE_DELTA_50HZ = 0x0190;

void ade9000DriverInit()
{
  // PSM0 mode: full-power operation
  pinMode(PIN_PM_1, OUTPUT);
  digitalWrite(PIN_PM_1, LOW);

  // Hardware reset
  pinMode(PIN_ADE9000_RESET, OUTPUT);
  digitalWrite(PIN_ADE9000_RESET, HIGH);
  digitalWrite(PIN_ADE9000_RESET, LOW);
  delay(50);
  digitalWrite(PIN_ADE9000_RESET, HIGH);
  delay(1000);

  ade9000.SPI_Init(ADE9000_SPI_SPEED, PIN_ADE9000_CS);
  ade9000.SetupADE9000();

  // Override for 3P3W delta. Start with 60Hz until frequency is measured.
  ade9000.SPI_Write_16(ADDR_ACCMODE, ACCMODE_DELTA_60HZ);
}

// Called once from app after signal is detected and frequency is known.
// Switches SELFREQ bit if grid is 50Hz. Safe to call multiple times.
void ade9000ApplyFreqMode(float measuredHz)
{
  uint16_t mode = (measuredHz < 55.0f) ? ACCMODE_DELTA_50HZ : ACCMODE_DELTA_60HZ;
  ade9000.SPI_Write_16(ADDR_ACCMODE, mode);
}

uint16_t ade9000ReadRunRegister()
{
  return ade9000.SPI_Read_16(ADDR_RUN);
}

bool ade9000ReadVoltageRMS(float &uab, float &ubc, float &uca)
{
  VoltageRMSRegs data;
  ade9000.ReadVoltageRMSRegs(&data);

  uab = (float)data.VoltageRMSReg_A * ADE9000_VRMS_SCALE;
  ubc = (float)data.VoltageRMSReg_B * ADE9000_VRMS_SCALE;
  uca = (float)data.VoltageRMSReg_C * ADE9000_VRMS_SCALE;

  return true;
}

bool ade9000ReadFrequency(float &freqHz)
{
  PeriodRegs data;
  ade9000.ReadPeriodRegsnValues(&data);

  // Use channel A period as the frequency reference
  freqHz = data.FrequencyValue_A;
  return true;
}
