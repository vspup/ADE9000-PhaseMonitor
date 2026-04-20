#include "ade9000_driver.h"
#include "src/board/pins.h"
#include "src/board/config.h"

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

// Tracks the last value written to ACCMODE so calibration can restore it on exit.
static uint16_t currentAccMode = ACCMODE_DELTA_60HZ;

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
  ade9000SetAccMode(ACCMODE_DELTA_60HZ);
}

// Called once from app after signal is detected and frequency is known.
// Switches SELFREQ bit if grid is 50Hz. Safe to call multiple times.
void ade9000SetAccMode(uint16_t accmode)
{
  currentAccMode = accmode;
  ade9000.SPI_Write_16(ADDR_ACCMODE, currentAccMode);
}

uint16_t ade9000GetCurrentAccMode()
{
  return currentAccMode;
}

void ade9000ApplyFreqMode(float measuredHz)
{
  // Update only the SELFREQ bit (bit 8); preserve the VCONSEL/ICONSEL bits
  // so the current measurement mode (delta vs L-N) is not disturbed.
  uint16_t base    = currentAccMode & ~static_cast<uint16_t>(0x0100);
  uint16_t selfreq = (measuredHz < 55.0f) ? 0x0100u : 0x0000u;
  ade9000SetAccMode(base | selfreq);
}

int32_t ade9000ReadRawRms(uint8_t phase)
{
  uint16_t addr;
  switch (phase) {
    case 0:  addr = ADDR_AVRMS; break;
    case 1:  addr = ADDR_BVRMS; break;
    case 2:  addr = ADDR_CVRMS; break;
    default: return 0;
  }
  return (int32_t)ade9000.SPI_Read_32(addr);
}

void ade9000WriteVGain(uint8_t phase, float gainMultiplier)
{
  // XVGAIN register format: two's-complement 32-bit.
  // Applied multiplier = 1 + register / 2^27.
  // Range: ±50% correction safely fits in 32-bit signed.
  int32_t regVal = (int32_t)((gainMultiplier - 1.0f) * 134217728.0f);  // 2^27

  uint16_t addr;
  switch (phase) {
    case 0:  addr = ADDR_AVGAIN; break;
    case 1:  addr = ADDR_BVGAIN; break;
    case 2:  addr = ADDR_CVGAIN; break;
    default: return;
  }
  ade9000.SPI_Write_32(addr, (uint32_t)regVal);
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
