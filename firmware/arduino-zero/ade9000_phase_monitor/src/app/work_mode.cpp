#include "work_mode.h"

static WorkMode currentWorkMode = WORK_MODE_IDLE;

void workModeInit()
{
  currentWorkMode = WORK_MODE_IDLE;
}

WorkMode workModeGet()
{
  return currentWorkMode;
}

void workModeSet(WorkMode mode)
{
  currentWorkMode = mode;
}

const char* workModeGetName(WorkMode mode)
{
  switch (mode) {
    case WORK_MODE_IDLE:    return "idle";
    case WORK_MODE_MONITOR: return "monitor";
    case WORK_MODE_CAPTURE: return "capture";
  }
  return "?";
}
