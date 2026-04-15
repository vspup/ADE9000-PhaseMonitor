#include "state_machine.h"
#include "constants.h"

// State transitions:
//   IDLE → MONITORING  : signal present
//   MONITORING → ARMED : stable, no events
//   ARMED → EVENT_DETECTED : any event flag set
//   EVENT_DETECTED → RECORDING : (reserved for future recording trigger)
//   RECORDING → COMPLETED : (reserved for future completion logic)
//   any → FAULT : ADE not responding (future)

static SystemState currentState = STATE_IDLE;

void stateMachineInit()
{
  currentState = STATE_IDLE;
}

void stateMachineUpdate(const VoltageSnapshot &snapshot, const EventFlags &flags)
{
  switch (currentState)
  {
    case STATE_IDLE:
      if (snapshot.signal_present)
        currentState = STATE_MONITORING;
      break;

    case STATE_MONITORING:
      if (!snapshot.signal_present)
        currentState = STATE_IDLE;
      else if (!flags.dip && !flags.unbalance && !flags.freq_err)
        currentState = STATE_ARMED;
      break;

    case STATE_ARMED:
      if (!snapshot.signal_present)
        currentState = STATE_IDLE;
      else if (flags.dip || flags.unbalance || flags.freq_err)
        currentState = STATE_EVENT_DETECTED;
      break;

    case STATE_EVENT_DETECTED:
      // Placeholder: transition to RECORDING when recording is implemented
      break;

    case STATE_RECORDING:
      // Placeholder: transition to COMPLETED when done
      break;

    case STATE_COMPLETED:
      currentState = STATE_MONITORING;
      break;

    case STATE_FAULT:
      // Stay in FAULT until explicit reset via command
      break;
  }
}

SystemState stateMachineGetState()
{
  return currentState;
}
