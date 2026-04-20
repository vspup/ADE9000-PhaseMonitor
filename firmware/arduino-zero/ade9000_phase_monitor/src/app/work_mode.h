#ifndef WORK_MODE_H
#define WORK_MODE_H

#include "../../constants.h"

// Default at boot: WORK_MODE_MONITOR. PC application is expected to
// confirm the mode explicitly on connect.
void        workModeInit();

WorkMode    workModeGet();
void        workModeSet(WorkMode mode);

// Human-readable name for protocol responses ("monitor" / "capture").
const char* workModeGetName(WorkMode mode);

#endif
