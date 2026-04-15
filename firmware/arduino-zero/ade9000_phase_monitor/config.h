#ifndef CONFIG_H
#define CONFIG_H

#include <Arduino.h>

#define FW_NAME    "ADE9000 Phase Monitor"
#define FW_VERSION "v0.2.1"

static const uint32_t UART_BAUDRATE    = 115200;
static const uint32_t SEND_PERIOD_MS   = 200;
static const uint32_t ADE9000_SPI_SPEED = 5000000;

// Voltage scaling: raw ADE9000 RMS code → volts (line-to-line).
// EV-ADE9000SHIELDZ resistor divider attenuation = 801 (UG-1170, p.3).
// ADE9000 full-scale input = 0.5V peak = 0.3536 Vrms.
// Full-scale code = 52702092.
// Scale = (0.3536 * 801) / 52702092 = 5.376e-6 V/count.
// At 230V rms input: raw code ≈ 42 800 000 → 42800000 * 5.376e-6 = 230.1 V ✓
static const float ADE9000_VRMS_SCALE = 5.376e-6f;

// DIP threshold in volts (line-to-line, delta system).
// 3P3W 400V nominal: dip if any L-L voltage drops below 340V.
static const float DEFAULT_DIP_THRESHOLD_V        = 340.0f;
static const float DEFAULT_UNBALANCE_THRESHOLD_PCT = 10.0f;

static const bool ENABLE_DEBUG_OUTPUT = true;

#endif
