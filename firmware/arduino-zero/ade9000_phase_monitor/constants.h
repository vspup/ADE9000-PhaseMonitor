#ifndef CONSTANTS_H
#define CONSTANTS_H

#include <Arduino.h>

enum SystemState : uint8_t
{
  STATE_IDLE           = 0,
  STATE_MONITORING     = 1,
  STATE_ARMED          = 2,
  STATE_EVENT_DETECTED = 3,
  STATE_RECORDING      = 4,
  STATE_COMPLETED      = 5,
  STATE_FAULT          = 6
};

#endif
